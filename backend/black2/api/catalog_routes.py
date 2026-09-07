"""Compatibility catalog surface over the offline Black 2 Dex store."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..dex.store import DexDataError, ENTITY_KINDS, ENTITY_SINGULAR, dex_store


router = APIRouter(prefix="/api/v1/catalog", tags=["black2-catalog-v1"])
Search = Annotated[str | None, Query(max_length=120)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def _unavailable(exc: DexDataError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "DEX_DATA_UNAVAILABLE", "message": str(exc)},
    )


def _check_kind(kind: str) -> None:
    if kind not in ENTITY_KINDS:
        raise HTTPException(
            status_code=404,
            detail={"code": "CATALOG_ENTITY_NOT_FOUND", "entity": kind},
        )


def _catalog_detail(kind: str, entity_id: int) -> dict:
    _check_kind(kind)
    try:
        entity = dex_store.get(kind, entity_id)
        dataset = dex_store.dataset_ref()
    except DexDataError as exc:
        raise _unavailable(exc) from exc
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEX_ENTRY_NOT_FOUND", "entity": kind, "id": entity_id},
        )
    singular = ENTITY_SINGULAR[kind]
    payload = {
        "format": f"black2-catalog-{singular}/v1",
        "dataset": dataset,
        singular: entity,
    }
    payload.update(entity)
    payload["format"] = f"black2-catalog-{singular}/v1"
    return payload


@router.get("/capabilities")
def catalog_capabilities() -> dict:
    try:
        summary = dex_store.summary()
    except DexDataError as exc:
        raise _unavailable(exc) from exc
    counts = summary.get("counts", {})
    dataset = summary["dataset"]
    entities = {
        kind: {
            "entity": ENTITY_SINGULAR[kind],
            "count": counts.get(kind, 0),
            "list": f"/api/v1/catalog/{kind}",
            "detail": f"/api/v1/catalog/{kind}/{{id}}",
            "rom_lookup": f"/api/v1/catalog/rom/{kind}/{{rom_id}}",
            "id_space": dataset.get("id_spaces", {}).get(
                f"{kind}.id"
            ),
            "rom_id_space": dataset.get("rom_id_spaces", {}).get(kind),
        }
        for kind in ENTITY_KINDS
    }
    return {
        "format": "black2-catalog-capabilities/v1",
        "dataset": dataset,
        "offline": True,
        "read_only": True,
        "entities": entities,
        "search": "/api/v1/catalog/search",
        "pagination": {"query": "q", "limit_default": 50, "limit_maximum": 1000},
        "rom_provenance": dataset.get("rom_provenance"),
    }


@router.get("/search")
def catalog_search(
    q: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Limit = 50,
    offset: Offset = 0,
    entity: Annotated[str | None, Query(alias="entity", max_length=24)] = None,
) -> dict:
    kinds = None
    if entity:
        kinds = tuple(part.strip() for part in entity.split(",") if part.strip())
        invalid = [kind for kind in kinds if kind not in ENTITY_KINDS]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail={"code": "CATALOG_INVALID_ENTITY", "entities": invalid},
            )
    try:
        return dex_store.search_all(q, limit=limit, offset=offset, kinds=kinds)
    except DexDataError as exc:
        raise _unavailable(exc) from exc


@router.get("/rom/{kind}/{rom_id}")
def catalog_rom_lookup(kind: str, rom_id: int) -> dict:
    """Resolve a Gen V/ROM-facing id while preserving ambiguous candidates."""
    _check_kind(kind)
    if rom_id < 0 or rom_id > 65535:
        raise HTTPException(
            status_code=422,
            detail={"code": "CATALOG_INVALID_ROM_ID", "message": "rom_id must be between 0 and 65535"},
        )
    try:
        entities = dex_store.entities_by_rom_id(kind, rom_id)
        dataset = dex_store.dataset_ref()
    except DexDataError as exc:
        raise _unavailable(exc) from exc
    return {
        "format": "black2-catalog-rom-lookup/v1",
        "dataset": dataset,
        "entity": kind,
        "entity_type": ENTITY_SINGULAR[kind],
        "rom_id": rom_id,
        "matched": len(entities),
        "ambiguous": len(entities) > 1,
        "items": entities,
    }


@router.get("/{kind}")
def catalog_list(
    kind: str,
    q: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
    game_index: Annotated[int | None, Query(ge=0, le=65535)] = None,
    rom_id: Annotated[int | None, Query(ge=0, le=65535)] = None,
) -> dict:
    _check_kind(kind)
    if game_index is not None and kind != "items":
        raise HTTPException(
            status_code=422,
            detail={"code": "CATALOG_INVALID_FILTER", "message": "game_index is only valid for items"},
        )
    if game_index is not None and rom_id is not None:
        raise HTTPException(
            status_code=422,
            detail={"code": "CATALOG_INVALID_FILTER", "message": "game_index and rom_id are mutually exclusive"},
        )
    try:
        result = dex_store.list(
            kind,
            q=q,
            limit=limit,
            offset=offset,
            game_index=game_index,
            rom_id=rom_id,
        )
    except DexDataError as exc:
        raise _unavailable(exc) from exc
    result["format"] = "black2-catalog-list/v1"
    result["catalog_entity"] = ENTITY_SINGULAR[kind]
    return result


@router.get("/{kind}/{entity_id}")
def catalog_detail(kind: str, entity_id: int) -> dict:
    return _catalog_detail(kind, entity_id)


__all__ = ["router"]
