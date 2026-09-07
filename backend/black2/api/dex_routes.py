"""Offline Black 2 Pokedex, move, item, ability and learnset API."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..dex.store import DexDataError, dex_store


router = APIRouter(prefix="/api/v1/dex", tags=["black2-dex-v1"])
Search = Annotated[str | None, Query(max_length=120)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def _list(kind: str, q: str | None, limit: int, offset: int, game_index: int | None = None):
    try:
        return dex_store.list(
            kind,
            q=q,
            limit=limit,
            offset=offset,
            game_index=game_index,
        )
    except DexDataError as exc:
        raise HTTPException(status_code=503, detail={"code": "DEX_DATA_UNAVAILABLE", "message": str(exc)}) from exc


def _detail(kind: str, entity_id: int):
    try:
        entity = dex_store.get(kind, entity_id)
    except DexDataError as exc:
        raise HTTPException(status_code=503, detail={"code": "DEX_DATA_UNAVAILABLE", "message": str(exc)}) from exc
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEX_ENTRY_NOT_FOUND", "entity": kind, "id": entity_id},
        )
    singular = {
        "pokemon": "pokemon",
        "moves": "move",
        "items": "item",
        "abilities": "ability",
        "types": "type",
    }[kind]
    payload = {
        "format": f"black2-dex-{singular}/v1",
        "dataset": dex_store.dataset_ref(),
        singular: entity,
    }
    # Keep the wrapped form for stable metadata while exposing the entity's
    # fields at the top level for lightweight clients.
    payload.update(entity)
    payload["format"] = f"black2-dex-{singular}/v1"
    return payload


@router.get("/summary")
def dex_summary():
    try:
        return dex_store.summary()
    except DexDataError as exc:
        raise HTTPException(status_code=503, detail={"code": "DEX_DATA_UNAVAILABLE", "message": str(exc)}) from exc


@router.get("/pokemon")
def list_pokemon(q: Search = None, limit: Limit = 50, offset: Offset = 0):
    return _list("pokemon", q, limit, offset)


@router.get("/pokemon/{pokemon_id}")
def get_pokemon(pokemon_id: int):
    return _detail("pokemon", pokemon_id)


@router.get("/moves")
def list_moves(q: Search = None, limit: Limit = 50, offset: Offset = 0):
    return _list("moves", q, limit, offset)


@router.get("/moves/{move_id}")
def get_move(move_id: int):
    return _detail("moves", move_id)


@router.get("/items/by-game-index/{game_index}")
def get_items_by_game_index(game_index: int):
    if game_index < 0 or game_index > 65535:
        raise HTTPException(status_code=422, detail="game_index must be between 0 and 65535")
    try:
        items = dex_store.items_by_game_index(game_index)
        dataset = dex_store.dataset_ref()
    except DexDataError as exc:
        raise HTTPException(status_code=503, detail={"code": "DEX_DATA_UNAVAILABLE", "message": str(exc)}) from exc
    return {
        "format": "black2-dex-item-game-index/v1",
        "dataset": dataset,
        "game_index": game_index,
        "matched": len(items),
        "ambiguous": len(items) > 1,
        "items": items,
    }


@router.get("/items")
def list_items(
    q: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
    game_index: Annotated[int | None, Query(ge=0, le=65535)] = None,
):
    return _list("items", q, limit, offset, game_index)


@router.get("/items/{item_id}")
def get_item(item_id: int):
    return _detail("items", item_id)


@router.get("/abilities")
def list_abilities(q: Search = None, limit: Limit = 50, offset: Offset = 0):
    return _list("abilities", q, limit, offset)


@router.get("/abilities/{ability_id}")
def get_ability(ability_id: int):
    return _detail("abilities", ability_id)


@router.get("/types")
def list_types(q: Search = None, limit: Limit = 50, offset: Offset = 0):
    return _list("types", q, limit, offset)


@router.get("/types/{type_id}")
def get_type(type_id: int):
    return _detail("types", type_id)


__all__ = ["router"]
