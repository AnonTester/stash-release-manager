"""Export / import of the user's data (v0.173.0, see data_transfer.py)."""
import json
import os

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

import data_transfer

router = APIRouter(prefix="/transfer", tags=["transfer"])

_db = None
_data_dir = "/data"


def init_transfer_router(db, data_dir):
    global _db, _data_dir
    _db, _data_dir = db, data_dir


@router.get("/groups")
def groups():
    """What can be exported (live row counts) and which groups are ticked by default."""
    return {"groups": data_transfer.group_counts(_db), "files": data_transfer.list_files(_data_dir)}


@router.post("/export")
def export(body: dict):
    return data_transfer.export(_db, _data_dir, body.get("groups") or [])


@router.get("/files")
def files():
    return {"files": data_transfer.list_files(_data_dir)}


@router.get("/download")
def download(name: str):
    """The export file itself (a link in the browser; the file also lies in data/exports/)."""
    try:
        path = data_transfer.resolve(_data_dir, name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return FileResponse(path, media_type="application/zip", filename=os.path.basename(path))


@router.post("/import/check")
def import_check(body: dict):
    """Dry run: counts what a file would bring in and what blocks it. Changes nothing."""
    return data_transfer.check(_db, _data_dir, body.get("name"), body.get("groups"))


@router.post("/import/apply")
def import_apply(body: dict):
    return data_transfer.apply(_db, _data_dir, body.get("name"), body.get("groups"))


@router.websocket("/upload")
async def upload(ws: WebSocket):
    """Upload of an export ZIP from the browser into data/imports/ (v0.180.0). A WebSocket because Stash's content security policy lets the
    page talk to other hosts only that way. Protocol: the client sends {"name", "size"}; the server answers {"ready"}; then the client sends
    binary chunks (each answered by {"got": bytes so far}) and finally the text "end"; the server answers {"ok", "name"} or {"error"}."""
    await ws.accept()
    tmp = None
    try:
        head = json.loads(await ws.receive_text())
        size = int(head.get("size") or 0)
        if size <= 0 or size > data_transfer.MAX_UPLOAD_BYTES:
            await ws.send_json({"error": "the file is empty or larger than 2 GB"})
            return
        try:
            tmp, final, shown = data_transfer.upload_names(_data_dir, head.get("name"))
        except ValueError as e:
            await ws.send_json({"error": str(e)})
            return
        await ws.send_json({"ready": True})
        got = 0
        with open(tmp, "wb") as fh:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
                if msg.get("bytes") is not None:
                    got += len(msg["bytes"])
                    if got > size:
                        await ws.send_json({"error": "more data than announced"})
                        return
                    fh.write(msg["bytes"])
                    await ws.send_json({"got": got})
                elif msg.get("text") == "end":
                    break
        try:
            data_transfer.finish_upload(tmp, final, size)
        except ValueError as e:
            await ws.send_json({"error": str(e)})
            return
        tmp = None
        await ws.send_json({"ok": True, "name": shown})
    except WebSocketDisconnect:
        pass
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
