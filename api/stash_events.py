"""Stash job events (2026-09-30): what happens when Stash's job queue is long.

Stash has no plugin hook for "job finished" (its hooks are object events like Scene.Create.Post, which the
sidecar already uses), but it PUSHES job changes over a GraphQL subscription (`jobsSubscribe`: ADD / UPDATE /
REMOVE with the job's status). This watcher keeps one such websocket open, mirrors the queue in memory and calls
`on_scan_finished` as soon as a scan job is done -- so pending import verifications continue within seconds
instead of waiting for the next 15-minute poll, however long Stash's queue was. The polling loops stay as the
safety net (websocket down, Stash restarted).

Protocol seen live against this Stash: sub-protocol "graphql-ws" (start / data / ka).
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

_SUBSCRIPTION = "subscription { jobsSubscribe { type job { id status description progress } } }"
_DONE = ("FINISHED", "CANCELLED", "FAILED")


class StashJobWatcher:
    def __init__(self, get_connection, on_scan_finished, tracked_jobs=None):
        """get_connection() -> (stash_url, api_key); on_scan_finished() is called (debounced) when a scan
        job finished. tracked_jobs() -> set of job ids (str) the sidecar itself waits for (the phash regeneration of
        verification.py): their end calls the same callback (v0.106.3)."""
        self._get_connection = get_connection
        self._on_scan_finished = on_scan_finished
        self._tracked_jobs = tracked_jobs or (lambda: set())
        self.jobs = {}
        self.connected = False
        self.last_event_at = None

    # ---------- state, readable from any thread ----------

    def snapshot(self):
        """{"connected", "total", "running": [descriptions], "generating": bool, "jobs": n}."""
        jobs = list(self.jobs.values())
        running = [j for j in jobs if j.get("status") == "RUNNING"]
        return {"connected": self.connected, "total": len(jobs),
                "running": [f"{j.get('description')} ({round((j.get('progress') or 0) * 100)}%)" for j in running],
                "generating": any("enerat" in (j.get("description") or "") for j in jobs if j.get("status") in ("RUNNING", "READY"))}

    # ---------- event handling ----------

    def _handle(self, update):
        kind, job = update.get("type"), update.get("job") or {}
        job_id = str(job.get("id"))
        previous = self.jobs.get(job_id, {})
        if kind == "REMOVE" or job.get("status") in _DONE:
            self.jobs.pop(job_id, None)
        else:
            self.jobs[job_id] = {**previous, **{k: v for k, v in job.items() if v is not None}}
        desc = job.get("description") or previous.get("description") or ""
        ended = kind == "REMOVE" or job.get("status") in _DONE
        tracked = ended and not desc.startswith("Scanning") and job_id in self._tracked_jobs()
        if ended and (desc.startswith("Scanning") or tracked):
            logger.info("stash events: %s job %s finished -- continuing pending imports",
                        "scan" if not tracked else "phash regeneration", job_id)
            try:
                self._on_scan_finished()
            except Exception:
                logger.exception("stash events: scan-finished callback failed")

    async def _load_queue(self, stash_url, api_key):
        """The queue as it is right now (the subscription only reports changes)."""
        import urllib.request

        def fetch():
            req = urllib.request.Request(
                stash_url.rstrip("/") + "/graphql",
                data=json.dumps({"query": "{ jobQueue { id status description progress } }"}).encode(),
                headers={"ApiKey": api_key, "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=30))

        data = await asyncio.to_thread(fetch)
        self.jobs = {str(j["id"]): j for j in (data.get("data", {}).get("jobQueue") or [])
                     if j.get("status") not in _DONE}

    async def run(self):
        import websockets
        delay = 5
        while True:
            try:
                url, key = self._get_connection()
                if not url:  # a fresh installation: Stash is not entered yet (Settings > Connections) -- wait quietly
                    self.connected = False
                    await asyncio.sleep(30)
                    continue
                ws_url = url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/graphql"
                async with websockets.connect(ws_url, subprotocols=["graphql-transport-ws", "graphql-ws"],
                                              additional_headers={"ApiKey": key}, open_timeout=20,
                                              ping_interval=None) as ws:
                    new_protocol = ws.subprotocol == "graphql-transport-ws"
                    await ws.send(json.dumps({"type": "connection_init", "payload": {}}))
                    ack = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    if ack.get("type") != "connection_ack":
                        raise RuntimeError(f"unexpected answer to connection_init: {ack}")
                    await ws.send(json.dumps({"id": "1", "type": "subscribe" if new_protocol else "start",
                                              "payload": {"query": _SUBSCRIPTION}}))
                    await self._load_queue(url, key)
                    self.connected, delay = True, 5
                    logger.info("stash events: connected (%d job(s) in the queue)", len(self.jobs))
                    while True:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), 120))  # Stash sends keep-alives
                        self.last_event_at = time.time()
                        if msg.get("type") in ("data", "next"):
                            update = (((msg.get("payload") or {}).get("data") or {}).get("jobsSubscribe"))
                            if update:
                                self._handle(update)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as e:
                self.connected = False
                logger.warning("stash events: connection lost (%s: %s) -- retrying in %ds", type(e).__name__, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)
