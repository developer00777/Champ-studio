"""Brand kit API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import brands, db
from ..deps import actor, guarded, require_role

router = APIRouter(dependencies=[guarded])


@router.get("/api/brands")
async def list_brands():
    return {"brands": brands.list_kits()}


@router.get("/api/brands/{kit_id}")
async def get_brand(kit_id: str):
    kit = brands.get(kit_id)
    if kit.get("id") != kit_id:
        raise HTTPException(404, "brand kit not found")
    return kit


@router.post("/api/brands", dependencies=[require_role("editor")])
async def save_brand(kit: dict, request: Request):
    if not kit.get("name"):
        raise HTTPException(422, "name required")
    saved = brands.save(kit)
    db.audit("brand.save", saved["id"], {"name": saved["name"]}, actor=actor(request)["id"])
    return saved


@router.delete("/api/brands/{kit_id}", dependencies=[require_role("admin")])
async def delete_brand(kit_id: str, request: Request):
    if not brands.delete(kit_id):
        raise HTTPException(404, "brand kit not found")
    db.audit("brand.delete", kit_id, {}, actor=actor(request)["id"])
    return {"deleted": kit_id}
