"""CRUD endpoints for configured download clients (Milestone 3,
2026-09-19) -- see download_client_repo.py."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import download_client_repo
import download_history_cleanup
from qbittorrent_client import QbittorrentClient
from sabnzbd_client import SabnzbdClient

router = APIRouter(prefix="/download-clients", tags=["download-clients"])

_db = None


_get_prowlarr_client = None


def init_download_clients_router(db, get_prowlarr_client=None):
    global _db, _get_prowlarr_client
    _db = db
    _get_prowlarr_client = get_prowlarr_client


# Prowlarr implementation name -> this program's client type (the only two clients it can talk to)
_PROWLARR_TYPES = {"sabnzbd": "sabnzbd", "qbittorrent": "qbittorrent"}


def prowlarr_clients_from_fields(clients):
    """Prowlarr's download clients as prefill suggestions: type, name, URL (host/port/ssl/base), username, enabled. Never a key or password
    (Prowlarr masks them). Clients of a kind this program cannot use are left out."""
    out = []
    for c in clients:
        kind = _PROWLARR_TYPES.get(str(c.get("implementation") or "").lower())
        if not kind:
            continue
        f = {x.get("name"): x.get("value") for x in c.get("fields", [])}
        host = str(f.get("host") or "").strip()
        if not host:
            continue
        base = str(f.get("urlBase") or "").strip().strip("/")
        url = f"{'https' if f.get('useSsl') else 'http'}://{host}" + (f":{f['port']}" if f.get("port") else "") + (f"/{base}" if base else "")
        out.append({"name": c.get("name") or kind, "type": kind, "url": url, "username": str(f.get("username") or ""),
                    "enabled": bool(c.get("enable")), "implementation": c.get("implementation")})
    return out


@router.get("/from-prowlarr")
def from_prowlarr():
    """The download clients configured in Prowlarr, as prefill for the add form (the API key has to be typed in: Prowlarr hides it)."""
    client = _get_prowlarr_client() if _get_prowlarr_client else None
    if client is None:
        raise HTTPException(400, "Prowlarr is not configured")
    try:
        return prowlarr_clients_from_fields(client.get_download_clients())
    except Exception as e:
        raise HTTPException(502, f"Prowlarr: {e}")


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


def _ping(client_type, url, api_key, username, password):
    """Cheapest authenticated call available for a client's type. Raises on any failure."""
    if client_type == "qbittorrent":
        return {"ok": True, "version": QbittorrentClient(url, username, password).ping()}
    if client_type != "sabnzbd":
        raise ValueError(f"connection check not implemented for type {client_type!r} yet")
    SabnzbdClient(url, api_key).ping()
    return {"ok": True}


class DownloadClientCheckBody(BaseModel):
    type: str = "sabnzbd"
    url: str = ""
    api_key: str = ""
    username: str = ""
    password: str = ""


@router.post("/check")
def check_entered(body: DownloadClientCheckBody):
    """Checks the details as they are typed into the add / edit form -- nothing is saved or read from the saved clients (v0.186.1).
    A failure comes back as {"ok": false, "error": ...} (HTTP 200) so the form can show the plain message."""
    if not body.url.strip():
        return {"ok": False, "error": "enter the URL first"}
    try:
        return _ping(body.type, body.url.strip(), body.api_key, body.username, body.password)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/{client_id}/clean-history")
def clean_history(client_id: int, dry_run: bool = True):
    """Removes the finished, fully imported downloads of this program from the client's history (v0.215.0, like the *arr "Remove Completed").
    Dry run (default): only counts what would go. Never touches an entry that does not belong to an imported release of ours."""
    client_row = download_client_repo.get_one(_db, client_id)
    if client_row is None:
        raise HTTPException(404, "download client not found")
    if client_row["type"] != "sabnzbd":
        raise HTTPException(400, "only SABnzbd clients keep a history to clean up")
    try:
        return download_history_cleanup.clean_client(_db, client_row, dry_run=dry_run)
    except Exception as e:
        return {"error": str(e)}


@router.post("/{client_id}/purge-archive")
def purge_archive(client_id: int, dry_run: bool = True, older_than_days: int = 30):
    """Removes the entries of the client's SABnzbd Archive that are older than `older_than_days` (default 30; only its category when one is set).
    Dry run (default): only counts. The same purge runs by itself every day (setting sab_archive_purge_days, 0 = off)."""
    client_row = download_client_repo.get_one(_db, client_id)
    if client_row is None:
        raise HTTPException(404, "download client not found")
    if client_row["type"] != "sabnzbd":
        raise HTTPException(400, "only SABnzbd clients have an Archive")
    try:
        return download_history_cleanup.purge_archive(client_row, older_than_days, dry_run=dry_run)
    except Exception as e:
        return {"error": str(e)}


@router.post("/{client_id}/check")
def check_client(client_id: int):
    """Cheapest authenticated call available for this saved client's type, same role as the other connections' "Check connections"
    button."""
    client_row = download_client_repo.get_one(_db, client_id)
    if client_row is None:
        raise HTTPException(404, "download client not found")
    try:
        return _ping(client_row["type"], client_row["url"], client_row["api_key"], client_row["username"], client_row["password"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))
