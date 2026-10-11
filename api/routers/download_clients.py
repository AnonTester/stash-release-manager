"""CRUD endpoints for configured download clients (Milestone 3,
2026-09-19) -- see download_client_repo.py."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import download_client_repo
from qbittorrent_client import QbittorrentClient
from sabnzbd_client import SabnzbdClient

router = APIRouter(prefix="/download-clients", tags=["download-clients"])

_db = None


def init_download_clients_router(db):
    global _db
    _db = db


class DownloadClientBody(BaseModel):
    name: str
    type: str = "sabnzbd"
    protocol: str = "usenet"
    priority: int = 25
    enabled: bool = True
    url: str = ""
    api_key: str = ""
    username: str = ""
    password: str = ""
    category: str = ""
    options: dict = {}


def _validate(body):
    if body.type not in download_client_repo.TYPE_PROTOCOL:
        raise HTTPException(400, f"type must be one of {', '.join(download_client_repo.TYPE_PROTOCOL)}")
    if not body.name.strip() or not body.url.strip():
        raise HTTPException(400, "name and url are required")
    body.protocol = download_client_repo.TYPE_PROTOCOL[body.type]  # the protocol follows the type


@router.get("")
def list_clients():
    return download_client_repo.list_all(_db)


@router.post("")
def create_client(body: DownloadClientBody):
    _validate(body)
    client_id = download_client_repo.create(
        _db, body.name, body.type, body.protocol, body.priority, body.enabled,
        body.url, body.api_key, body.username, body.password, body.category, body.options,
    )
    return download_client_repo.get_one(_db, client_id)


@router.put("/{client_id}")
def update_client(client_id: int, body: DownloadClientBody):
    if download_client_repo.get_one(_db, client_id) is None:
        raise HTTPException(404, "download client not found")
    _validate(body)
    download_client_repo.update(
        _db, client_id, name=body.name, type=body.type, protocol=body.protocol,
        priority=body.priority, enabled=int(body.enabled), url=body.url, api_key=body.api_key,
        username=body.username, password=body.password, category=body.category, options=body.options,
    )
    return download_client_repo.get_one(_db, client_id)


@router.delete("/{client_id}")
def delete_client(client_id: int):
    download_client_repo.delete(_db, client_id)
    return {"ok": True}


@router.post("/{client_id}/check")
def check_client(client_id: int):
    """Cheapest authenticated call available for this client's type,
    same role as the other connections' "Check connections" button --
    only 'sabnzbd' is implemented today (see the extensibility note in
    indexer_search.py)."""
    client_row = download_client_repo.get_one(_db, client_id)
    if client_row is None:
        raise HTTPException(404, "download client not found")
    try:
        if client_row["type"] == "qbittorrent":
            return {"ok": True, "version": QbittorrentClient(client_row["url"], client_row["username"], client_row["password"]).ping()}
        if client_row["type"] != "sabnzbd":
            raise HTTPException(400, f"connection check not implemented for type {client_row['type']!r} yet")
        SabnzbdClient(client_row["url"], client_row["api_key"]).ping()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e))
