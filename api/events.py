"""Live push to the browser (2026-09-26, v0.78.0).

The plugin's pages used to poll the sidecar through Stash's
runPluginOperation bridge (one Python process spawned on the NAS per
call, every 5-10 s, plus a SABnzbd queue fetch each time). Now the
browser opens ONE WebSocket straight to this sidecar (`/events/ws`).

WebSocket, not Server-Sent Events, because of Stash's own
Content-Security-Policy (checked live 2026-09-26): `connect-src 'self'
ws: wss:` -- fetch/EventSource to any other host is blocked in Stash's
pages, WebSockets to any host are allowed. The same stream is also
served as SSE at `GET /events` (handy for `curl -N`). Needs the plugin's
sidecarUrl to be reachable from the browser; an HTTPS Stash needs wss://
(a TLS proxy in front of the sidecar), otherwise the pages fall back to
the old polling on their own.

Events (JSON, `type` inside):
- `hello`            -- sent on every (re)connect; the page resyncs.
- `release_changed`  -- {provider, scene_id, change}: a release's status,
                        download job, import error or pending import path
                        changed (release_repo publishes after commit).
- `progress`         -- {data}: the same map GET /releases/download-progress
                        returns (download % + import phases), sent only
                        when it changed.
- `ping`             -- every 20 s; keeps proxies from dropping the idle
                        connection and lets a page notice it's gone.

The download/import progress is computed by main.py's
_live_progress_loop(): only while at least one browser is connected,
every few seconds while something is downloading/importing, otherwise
it sleeps until woken (a release change, an import phase change, a new
subscriber). No viewers -> no SABnzbd queries at all.

Thread-safe: publish()/wake() are called from the import/search threads
(asyncio.to_thread) as well as from the event loop.
"""
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

PING_SECONDS = 20
QUEUE_SIZE = 500

_loop = None
_wake_event = None
_subscribers = set()
_snapshot_wanted = False
_OVERFLOW = object()


def attach_loop(loop):
    """Called once from main.py's lifespan, before any subscriber exists."""
    global _loop, _wake_event
    _loop = loop
    _wake_event = asyncio.Event()


def subscriber_count():
    return len(_subscribers)


def _call_in_loop(fn, *args):
    if _loop is None or _loop.is_closed():
        return  # scripts / tests without a running sidecar: nothing to push to
    try:
        _loop.call_soon_threadsafe(fn, *args)
    except RuntimeError:
        pass  # loop shutting down


def publish(event_type, **data):
    """Queue an event for every connected browser. No-op without viewers."""
    if not _subscribers:
        return
    _call_in_loop(_dispatch, json.dumps({"type": event_type, **data}), event_type != "progress")


def wake():
    """Something the progress map depends on changed -- recompute soon."""
    _call_in_loop(_set_wake)


def _set_wake():
    if _wake_event is not None:
        _wake_event.set()


def _dispatch(message, also_wake):
    for queue in list(_subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # A stalled browser tab: drop its backlog and make it resync.
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(_OVERFLOW)
    if also_wake:
        _set_wake()


async def wait_for_work(timeout):
    """Sleeps until woken or `timeout` seconds (None = until woken)."""
    try:
        await asyncio.wait_for(_wake_event.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    _wake_event.clear()


def take_snapshot_request():
    """True once after a new subscriber connected (it needs the current
    progress map even if nothing changed since the last event)."""
    global _snapshot_wanted
    wanted, _snapshot_wanted = _snapshot_wanted, False
    return wanted


_HELLO = json.dumps({"type": "hello"})
_PING = json.dumps({"type": "ping"})


def _subscribe():
    global _snapshot_wanted
    queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(queue)
    _snapshot_wanted = True
    _set_wake()
    logger.info("live events: browser connected (%d open)", len(_subscribers))
    return queue


def _unsubscribe(queue):
    _subscribers.discard(queue)
    logger.info("live events: browser disconnected (%d open)", len(_subscribers))


async def _next_message(queue):
    """The next JSON text to send: an event, a ping after PING_SECONDS of
    silence, or a fresh hello (= "resync") after a queue overflow."""
    try:
        message = await asyncio.wait_for(queue.get(), PING_SECONDS)
    except asyncio.TimeoutError:
        return _PING
    return _HELLO if message is _OVERFLOW else message


async def serve_websocket(websocket):
    """Main loop of the /events/ws WebSocket (already accepted). A closed
    browser tab shows up as a failing send, at the latest with the next
    ping."""
    queue = _subscribe()
    try:
        await websocket.send_text(_HELLO)
        while True:
            await websocket.send_text(await _next_message(queue))
    except Exception:
        pass  # disconnected
    finally:
        _unsubscribe(queue)


async def stream(request):
    """Async generator for the StreamingResponse of GET /events (SSE)."""
    queue = _subscribe()
    try:
        yield "retry: 5000\n\n"
        yield f"data: {_HELLO}\n\n"
        while not await request.is_disconnected():
            yield f"data: {await _next_message(queue)}\n\n"
    finally:
        _unsubscribe(queue)
