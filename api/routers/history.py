"""History/Events log endpoints (2026-09-28, v0.79.0, see history.py)."""
from fastapi import APIRouter, HTTPException

import history

router = APIRouter(prefix="/history", tags=["history"])


def _split(value):
    return [v for v in (value or "").split(",") if v]


@router.get("")
def list_history(q: str = "", levels: str = "", kinds: str = "", categories: str = "", provider: str = "",
                 scene_id: str = "", date_from: str = "", date_until: str = "", page: int = 1, per_page: int = 50):
    return history.query(q=q, levels=_split(levels), kinds=_split(kinds), categories=_split(categories),
                         provider=provider or None, scene_id=scene_id or None,
                         date_from=date_from or None, date_until=date_until or None, page=page, per_page=per_page)


@router.post("")
def add_entry(body: dict):
    """A pop-up from the browser worth keeping (warnings/errors, see
    core.js's notify()) -- always an 'event' in category 'ui'."""
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    level = body.get("level") if body.get("level") in history.LEVELS else "info"
    title = str(body.get("title") or "").strip()
    history.event(level, "ui", f"{title}: {message}" if title else message, details=body.get("details"),
                  dedupe_minutes=1)
    return {"ok": True}
