"""CRUD endpoints for this project's own quality-profile concept
(Milestone 3, 2026-09-19) -- see quality_profile_repo.py."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import quality_profile_repo

router = APIRouter(prefix="/quality-profiles", tags=["quality-profiles"])

_db = None


def init_quality_profiles_router(db):
    global _db
    _db = db


class QualityProfileBody(BaseModel):
    name: str
    resolutions: list[int]
    cutoff_resolution: int | None = None


@router.get("")
def list_profiles():
    return quality_profile_repo.list_all(_db)


@router.post("")
def create_profile(body: QualityProfileBody):
    profile_id = quality_profile_repo.create(_db, body.name, body.resolutions, body.cutoff_resolution)
    return quality_profile_repo.get_one(_db, profile_id)


@router.put("/{profile_id}")
def update_profile(profile_id: int, body: QualityProfileBody):
    if quality_profile_repo.get_one(_db, profile_id) is None:
        raise HTTPException(404, "quality profile not found")
    quality_profile_repo.update(_db, profile_id, name=body.name, resolutions=body.resolutions,
                                 cutoff_resolution=body.cutoff_resolution)
    return quality_profile_repo.get_one(_db, profile_id)


@router.delete("/{profile_id}")
def delete_profile(profile_id: int):
    quality_profile_repo.delete(_db, profile_id)
    return {"ok": True}
