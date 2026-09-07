"""Read-only, evidence-backed navigation planning.

This service deliberately has no input-engine dependency.  A plan can be
inspected or drawn by a client, but creating one can never move the player.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any, Callable, Iterable
from uuid import uuid4

from .observed_navigation import NavNode, ObservedNavigationGraph
from .player_coordinates import canonical_grid_player
from .navigation_audit import navigation_audit_log


@dataclass(frozen=True)
class NavigationPlanningError(Exception):
    code: str
    message: str
    status_code: int = 409
    retryable: bool = False
    details: dict[str, Any] | None = None


def _integer(value: Any) -> int | None:
    """Return an integer coordinate without silently truncating a float."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number != math.trunc(number):
        return None
    return int(number)


def _path_action_segments(path: list[dict[str, int]]) -> list[dict[str, Any]]:
    """Compress cardinal nodes into the continuous input actions we execute."""
    names = {(0, -1): "North", (1, 0): "East", (0, 1): "South", (-1, 0): "West"}
    actions: list[dict[str, Any]] = []
    index = 1
    while index < len(path):
        delta = (path[index]["x"] - path[index - 1]["x"], path[index]["z"] - path[index - 1]["z"])
        direction = names.get(delta, "Invalid")
        count = 1
        while index + count < len(path):
            next_delta = (
                path[index + count]["x"] - path[index + count - 1]["x"],
                path[index + count]["z"] - path[index + count - 1]["z"],
            )
            if names.get(next_delta, "Invalid") != direction:
                break
            count += 1
        actions.append({
            "direction": direction,
            "steps": count,
            "from": path[index - 1],
            "to": path[index + count - 1],
        })
        index += count
    return actions


def normalize_occupancy_point(
    value: Any, *, default_zone: int | None = None, default_y: int | None = None,
) -> dict[str, Any] | None:
    """Normalize the coordinate shapes emitted by the renderer and RAM API.

    The live actor contract currently exposes ``actor.grid``.  Older clients
    and a few browser-side adapters have also emitted ``position.grid``, flat
    ``x/y/z`` or a world position.  Navigation must treat all of those as the
    same transient obstacle, otherwise a stale/shape-mismatched snapshot can
    produce a route through an NPC.
    """
    if isinstance(value, NavNode):
        return {
            "zone_id": int(value.zone_id),
            "grid": {"x": int(value.x), "y": int(value.y), "z": int(value.z)},
        }
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        x, z = _integer(value[0]), _integer(value[1])
        y = _integer(value[2]) if len(value) >= 3 else default_y
        if x is None or z is None:
            return None
        return {
            "zone_id": default_zone,
            "grid": {"x": x, "y": y, "z": z},
        }
    if not isinstance(value, dict):
        return None

    nested_position = value.get("position") if isinstance(value.get("position"), dict) else None
    nested_grid = value.get("grid") if isinstance(value.get("grid"), dict) else None
    position_grid = (
        nested_position.get("grid")
        if nested_position and isinstance(nested_position.get("grid"), dict)
        else None
    )

    # Prefer an explicit grid object, then position.grid, then flat fields.
    coordinate = nested_grid or position_grid or value
    x = _integer(coordinate.get("x"))
    z = _integer(coordinate.get("z"))
    y = _integer(coordinate.get("y"))

    # A flat position is normally a world coordinate.  Only interpret it as a
    # grid when the caller explicitly labels the space; otherwise convert the
    # world X/Z using the Gen-5 16-unit tile contract.
    if (x is None or z is None) and nested_position:
        position_space = str(
            nested_position.get("space")
            or nested_position.get("coordinate_space")
            or value.get("space")
            or value.get("coordinate_space")
            or ""
        ).lower()
        px, pz = nested_position.get("x"), nested_position.get("z")
        if "grid" in position_space or "gpos" in position_space:
            x, z = _integer(px), _integer(pz)
            y = _integer(nested_position.get("y"))
        elif px is not None and pz is not None:
            try:
                wx, wz = float(px), float(pz)
                if math.isfinite(wx) and math.isfinite(wz):
                    x, z = math.floor(wx / 16.0), math.floor(wz / 16.0)
                    y = _integer(nested_position.get("grid_y"))
            except (TypeError, ValueError):
                pass

    # Finally accept an explicitly supplied world object.
    if (x is None or z is None) and isinstance(value.get("world"), dict):
        world = value["world"]
        try:
            wx, wz = float(world.get("x")), float(world.get("z"))
            if math.isfinite(wx) and math.isfinite(wz):
                x, z = math.floor(wx / 16.0), math.floor(wz / 16.0)
        except (TypeError, ValueError):
            pass

    if x is None or z is None:
        return None
    if y is None:
        y = default_y

    zone_value = value.get("zone_id")
    if zone_value is None:
        zone_value = value.get("effective_zone_id_candidate")
    if zone_value is None and nested_position:
        zone_value = nested_position.get("zone_id")
    if zone_value is None and nested_grid:
        zone_value = nested_grid.get("zone_id")
    zone = _integer(zone_value if zone_value is not None else default_zone)
    return {"zone_id": zone, "grid": {"x": x, "y": y, "z": z}}


def normalize_occupancy(
    values: Iterable[Any] = (), *, default_zone: int | None = None, default_y: int | None = None,
) -> list[dict[str, Any]]:
    """Return deduplicated canonical transient actor tiles."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int, int | None, int]] = set()
    for value in values or ():
        point = normalize_occupancy_point(value, default_zone=default_zone, default_y=default_y)
        if not point:
            continue
        grid = point["grid"]
        key = (point.get("zone_id"), grid["x"], grid.get("y"), grid["z"])
        if key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result


class NavigationPlanService:
    """Build a safe same-Zone plan from observed layered movement edges."""

    def __init__(
        self,
        graph: ObservedNavigationGraph,
        player_sample: Callable[[], dict[str, Any] | None],
        static_provider: Any | Callable[[], Any] | None = None,
    ) -> None:
        self.graph = graph
        self.player_sample = player_sample
        self.static_provider = static_provider

    def _resolve_static_provider(self) -> Any | None:
        """Resolve an optional lazy ROM provider without affecting fixtures."""
        provider = self.static_provider
        if provider is None:
            return None
        if callable(provider) and not hasattr(provider, "find_path"):
            try:
                return provider()
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                return None
        return provider

    def static_status(self) -> dict[str, Any]:
        provider = self._resolve_static_provider()
        if provider is None:
            return {
                "available": False,
                "source": "ROM terrain records",
                "reason": "static navigation provider is unavailable",
            }
        status = getattr(provider, "status", None)
        return status() if callable(status) else {
            "available": True,
            "source": "ROM terrain records",
            "revision": getattr(provider, "revision", None),
        }

    @staticmethod
    def _occupied_nodes(
        occupied: Iterable[Any], *, zone_id: int, y: int,
    ) -> set[tuple[int, int]]:
        """Normalize caller-supplied actor occupancy for route filtering."""
        result: set[tuple[int, int]] = set()
        for point in normalize_occupancy(occupied, default_zone=zone_id, default_y=y):
            item_zone = point.get("zone_id")
            grid = point.get("grid") or {}
            item_y = grid.get("y")
            if item_zone is not None and int(item_zone) != zone_id:
                continue
            if item_y is not None and int(item_y) != y:
                continue
            result.add((int(grid["x"]), int(grid["z"])))
        return result

    def has_static_edge(
        self, start: NavNode, goal: NavNode, *, occupied: Iterable[Any] = (),
    ) -> bool:
        provider = self._resolve_static_provider()
        checker = getattr(provider, "has_candidate_edge", None) if provider is not None else None
        if not callable(checker):
            return False
        try:
            return bool(checker(
                start, goal, player_sample=self.player_sample(), occupied=occupied,
            ))
        except TypeError:
            # Keep third-party/test providers written against the original
            # two-argument contract usable.
            try:
                return bool(checker(start, goal, player_sample=self.player_sample()))
            except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError):
                return False
        except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError):
            return False

    def same_spatial_node(self, a: NavNode | None, b: NavNode | None) -> bool:
        """Compare canonical positions while treating same-Matrix Zone as metadata."""
        if a is None or b is None:
            return a is b
        if (a.x, a.y, a.z) != (b.x, b.y, b.z):
            return False
        if a.zone_id == b.zone_id:
            return True
        return self._matrix_id_for_zone(a.zone_id) == self._matrix_id_for_zone(b.zone_id) not in (None,)

    def capabilities(self) -> dict[str, Any]:
        graph = self.graph.status()
        return {
            "format": "black2-navigation-capabilities/v1",
            "coordinate_spaces": ["gen5-field-grid-v1", "gen5-matrix-grid-v1"],
            "destination_types": ["grid", "global_grid"],
            "navigation_intents": ["walk_to_tile", "interact", "route"],
            "navigation_intent_semantics": {
                "walk_to_tile": "pure movement to an exact walkable tile; never auto-interacts",
                "route": "legacy pure-movement alias; never auto-interacts",
                "interact": "explicit NPC approach/face/interact semantics only",
            },
            "planning": {
                "available": True,
                "read_only": True,
                "same_zone": True,
                "cross_zone": True,
                "cross_zone_scope": "same Matrix with ROM Zone ownership; Zone is resolved metadata, not an address boundary",
                "cross_matrix": False,
                "start_types": ["player_runtime", "explicit_grid"],
                "exact_elevation_required": True,
                "evidence": "observed_layered_edges_then_rom_static_candidates",
                "route_sources": ["verified_observed", "candidate_static"],
                "static_candidate_fallback": self.static_status(),
            },
            "execution": {"available": False},
            "movement": {
                "modes": ["auto", "walk", "run", "bike", "surf"],
                "selection": "auto chooses the fastest already-active verified transport: bike/surf, then run, then walk",
                "continuous_input": True,
                "optimization": "shortest path; static fallback breaks equal-length ties by fewer turns",
            },
            "graph": {
                "node_count": graph["node_count"],
                "directed_edge_count": graph["directed_edge_count"],
                "zones": graph["zones"],
                "confidence": graph["confidence"],
            },
        }

    @staticmethod
    def _grid_destination(node: NavNode) -> dict[str, Any]:
        """Return the public API coordinate, including its schema identity."""
        return {
            "type": "grid",
            "space": "gen5-field-grid-v1",
            **node.public(),
        }

    def movement_capabilities(
        self, zone_id: int, *, requested: str = "auto", player: dict[str, Any] | None = None,
        path: Iterable[NavNode] = (),
    ) -> dict[str, Any]:
        """Resolve movement mode from ROM ZoneHeader evidence when available.

        Running is not equated with "outside": some interior maps (for
        example battle facilities/gyms) explicitly allow it.  Cycling is
        additionally gated by the ZoneHeader and by the runtime transport
        state because mounting/registered-item input is not yet a verified
        bridge primitive.
        """
        mode = str(requested or "auto").lower()
        if mode not in {"auto", "walk", "run", "bike", "surf"}:
            raise NavigationPlanningError(
                "NAV_INVALID_MOVEMENT_MODE", "movement_mode must be auto, walk, run, bike, or surf.", status_code=422,
            )
        provider = self._resolve_static_provider()
        evidence: dict[str, Any] = {"source": "unresolved", "zone_id": int(zone_id)}
        allow_running = None
        allow_cycling = None
        environment = None
        if provider is not None:
            try:
                header = provider.rom.zone(int(zone_id))
                area = provider.rom.area(int(header.area_id))
                allow_running = bool(header.enable_running)
                allow_cycling = bool(header.enable_cycling)
                environment = "exterior" if bool(area.is_exterior) else "interior"
                evidence = {
                    "source": "ROM ZoneHeader + AreaHeader",
                    "zone_id": int(zone_id),
                    "environment": environment,
                    "enable_running": allow_running,
                    "enable_cycling": allow_cycling,
                    "area_id": int(header.area_id),
                }
                rules_fn = getattr(provider, "movement_rules", None)
                if callable(rules_fn):
                    route_rules = rules_fn(int(zone_id), path=path, player_sample=player)
                    evidence["route_rules"] = route_rules
            except (AttributeError, FileNotFoundError, IndexError, OSError, RuntimeError, TypeError, ValueError):
                pass

        transport = ((player or {}).get("locomotion") or {}).get("transport_mode")
        route_rules = evidence.get("route_rules") or {}
        bike_blockers = route_rules.get("bike_blockers") or []
        # The executor does not currently own a verified mount/dismount or
        # Surf-start primitive.  Movement modes therefore describe the
        # transport that is already active in PlayerRuntime, except that
        # OnFoot can freely choose walk/run when ZoneHeader allows running.
        on_foot = transport in (None, "", "OnFoot")
        available = {
            "walk": on_foot,
            "run": on_foot and allow_running is True,
            "bike": allow_cycling is True and not bike_blockers and transport == "Cycling",
            "surf": transport == "Surf",
        }
        reasons = {
            "walk": "PlayerRuntime is on foot" if on_foot else f"current transport is {transport!r}; no verified dismount primitive is owned by navigation",
            "run": "PlayerRuntime is on foot and ZoneHeader explicitly enables running" if available["run"] else "running requires OnFoot transport and ZoneHeader running permission",
            "bike": "ZoneHeader enables cycling, the runtime is Cycling, and every route tile permits cycling" if available["bike"] else "cycling requires ZoneHeader permission, a verified mounted bicycle, and a route without bike-blocking terrain",
            "surf": "PlayerRuntime is already in Surf transport" if available["surf"] else "surf requires a verified active Surf transport; navigation does not auto-start Surf yet",
        }
        if mode == "auto":
            # Prefer the fastest mode that is already safe to execute.  This
            # removes the previous artificial walk-only default while avoiding
            # speculative item/menu actions to mount a bike or start Surf.
            selected = next((candidate for candidate in ("bike", "surf", "run", "walk") if available[candidate]), None)
            if selected is None:
                raise NavigationPlanningError(
                    "NAV_MOVEMENT_UNAVAILABLE",
                    "No currently active verified movement mode can execute this route.",
                    status_code=409,
                    details={"requested_mode": mode, "available": available, "reasons": reasons, "evidence": evidence, "transport_mode": transport},
                )
        else:
            selected = mode
        if not available.get(selected, False):
            raise NavigationPlanningError(
                "NAV_MOVEMENT_UNAVAILABLE",
                f"Movement mode '{mode}' is not executable in this Zone.",
                status_code=409,
                details={"requested_mode": mode, "reason": reasons.get(mode), "evidence": evidence, "transport_mode": transport},
            )
        return {
            "requested": mode,
            "selected": selected,
            "available": available,
            "reasons": reasons,
            "evidence": evidence,
            "runtime_transport": transport,
        }

    @staticmethod
    def _normalize_interaction(
        interaction: dict[str, Any] | None, *, start: NavNode, goal: NavNode,
        navigation_intent: str = "walk_to_tile",
    ) -> dict[str, Any] | None:
        """Validate interaction metadata without changing the walk goal."""
        if interaction is None:
            return None
        if not isinstance(interaction, dict) or interaction.get("kind") != "npc":
            raise NavigationPlanningError(
                "NAV_INVALID_INTERACTION",
                "Only NPC interaction goals are supported.",
                status_code=422,
            )

        def node_from(value: Any) -> NavNode | None:
            if not isinstance(value, dict):
                return None
            try:
                if value.get("type") != "grid" or value.get("space") != "gen5-field-grid-v1":
                    return None
                return NavNode(
                    int(value["zone_id"]), int(value["x"]), int(value["y"]), int(value["z"]),
                )
            except (KeyError, TypeError, ValueError):
                return None

        target = node_from(interaction.get("target"))
        stand = node_from(interaction.get("stand_tile"))
        facing = interaction.get("facing")
        if target is None or stand is None or facing not in {"North", "East", "South", "West"}:
            raise NavigationPlanningError(
                "NAV_INVALID_INTERACTION",
                "NPC interaction must include target, adjacent stand_tile and cardinal facing.",
                status_code=422,
            )
        if stand != goal:
            raise NavigationPlanningError(
                "NAV_INTERACTION_GOAL_MISMATCH",
                "The navigation destination must equal the NPC interaction stand_tile.",
                status_code=422,
                details={"destination": goal.public(), "stand_tile": stand.public()},
            )
        if target.zone_id != start.zone_id or target.y != start.y or stand.zone_id != target.zone_id or stand.y != target.y:
            raise NavigationPlanningError(
                "NAV_INVALID_INTERACTION",
                "NPC interaction target and stand_tile must be on the current Zone/elevation layer.",
                status_code=422,
            )
        dx, dz = target.x - stand.x, target.z - stand.z
        expected_facing = {
            (0, -1): "North", (1, 0): "East", (0, 1): "South", (-1, 0): "West",
        }.get((dx, dz))
        if expected_facing != facing:
            raise NavigationPlanningError(
                "NAV_INVALID_INTERACTION",
                "NPC facing must point from stand_tile toward the target tile.",
                status_code=422,
                details={"expected_facing": expected_facing, "facing": facing},
            )
        normalized = {
            "kind": "npc",
            "target": NavigationPlanService._grid_destination(target),
            "stand_tile": NavigationPlanService._grid_destination(stand),
            "facing": facing,
            "turn_only": start == stand,
            # Older callers only requested the safe standing/facing plan.  A
            # UI/API caller using the explicit interact intent opts into the
            # final A-button action; preserving the default keeps old clients
            # read-compatible while making interaction execution explicit.
            "execute": bool(interaction.get("execute", False) or navigation_intent == "interact"),
        }
        if interaction.get("actor_id") is not None:
            normalized["actor_id"] = str(interaction["actor_id"])
        return normalized

    def _global_provider(self) -> Any | None:
        provider = self._resolve_static_provider()
        return provider if callable(getattr(provider, "resolve_zone_for_global", None)) else None

    def _matrix_id_for_zone(self, zone_id: int) -> int | None:
        provider = self._global_provider()
        rom = getattr(provider, "rom", None) if provider is not None else None
        try:
            return int(rom.zone(int(zone_id)).matrix_id)
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _resolve_destination_node(self, destination: dict[str, Any], start: NavNode) -> tuple[NavNode, dict[str, Any]]:
        dtype = str(destination.get("type") or "grid")
        space = str(destination.get("space") or "")
        if dtype == "grid":
            if space != "gen5-field-grid-v1":
                raise NavigationPlanningError("NAV_INVALID_DESTINATION", "grid destination must use gen5-field-grid-v1.", status_code=422)
            try:
                node = NavNode(int(destination["zone_id"]), int(destination["x"]), int(destination["y"]), int(destination["z"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise NavigationPlanningError("NAV_INVALID_DESTINATION", "Destination must be a complete gen5-field-grid-v1 coordinate.", status_code=422, details={"reason": str(exc)}) from exc
            return node, {"input_type": "grid", "matrix_id": self._matrix_id_for_zone(node.zone_id), "zone_resolved": False}
        if dtype != "global_grid" or space != "gen5-matrix-grid-v1":
            raise NavigationPlanningError("NAV_INVALID_DESTINATION", "Destination must be grid/gen5-field-grid-v1 or global_grid/gen5-matrix-grid-v1.", status_code=422)
        provider = self._global_provider()
        if provider is None:
            raise NavigationPlanningError("NAV_GLOBAL_UNAVAILABLE", "Matrix-global navigation requires the ROM static navigation provider.", status_code=503)
        matrix_id = destination.get("matrix_id")
        if matrix_id is None:
            matrix_id = self._matrix_id_for_zone(start.zone_id)
        try:
            matrix_id = int(matrix_id)
            x, y, z = int(destination["x"]), int(destination["y"]), int(destination["z"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NavigationPlanningError("NAV_INVALID_DESTINATION", "global_grid requires x/y/z and an inferable Matrix.", status_code=422, details={"reason": str(exc)}) from exc
        start_matrix = self._matrix_id_for_zone(start.zone_id)
        if start_matrix != matrix_id:
            raise NavigationPlanningError(
                "NAV_MATRIX_TRANSITION_UNVERIFIED",
                "The destination is in another Matrix. A verified connector transition is required.",
                details={"start_matrix_id": start_matrix, "goal_matrix_id": matrix_id},
            )
        try:
            zone_id = provider.resolve_zone_for_global(matrix_id, x, z, preferred_zone=start.zone_id)
        except TypeError:
            zone_id = provider.resolve_zone_for_global(matrix_id, x, z)
        if zone_id is None:
            raise NavigationPlanningError(
                "NAV_GLOBAL_DESTINATION_UNOWNED",
                "The Matrix-global destination does not resolve to an owned ROM Zone cell.",
                status_code=422,
                details={"matrix_id": matrix_id, "x": x, "y": y, "z": z},
            )
        return NavNode(int(zone_id), x, y, z), {"input_type": "global_grid", "matrix_id": matrix_id, "zone_resolved": True}

    def _resolve_start_node(self, start_position: dict[str, Any], player: dict[str, Any] | None = None) -> tuple[NavNode, dict[str, Any]]:
        dtype = str(start_position.get("type") or "grid")
        space = str(start_position.get("space") or "")
        if dtype == "grid" and space == "gen5-field-grid-v1":
            try:
                return NavNode(int(start_position["zone_id"]), int(start_position["x"]), int(start_position["y"]), int(start_position["z"])), {"input_type": "grid"}
            except (KeyError, TypeError, ValueError) as exc:
                raise NavigationPlanningError("NAV_INVALID_START", "Explicit start must be a complete gen5-field-grid-v1 coordinate.", status_code=422, details={"reason": str(exc)}) from exc
        if dtype == "global_grid" and space == "gen5-matrix-grid-v1":
            provider = self._global_provider()
            if provider is None:
                raise NavigationPlanningError("NAV_GLOBAL_UNAVAILABLE", "Matrix-global navigation requires the ROM static navigation provider.", status_code=503)
            matrix_id = start_position.get("matrix_id")
            if matrix_id is None and isinstance(player, dict) and isinstance(player.get("zone_id"), int):
                matrix_id = self._matrix_id_for_zone(int(player["zone_id"]))
            try:
                matrix_id = int(matrix_id)
                x, y, z = int(start_position["x"]), int(start_position["y"]), int(start_position["z"])
            except (KeyError, TypeError, ValueError) as exc:
                raise NavigationPlanningError("NAV_INVALID_START", "global_grid start requires x/y/z and matrix_id.", status_code=422, details={"reason": str(exc)}) from exc
            zone_id = provider.resolve_zone_for_global(matrix_id, x, z)
            if zone_id is None:
                raise NavigationPlanningError("NAV_GLOBAL_START_UNOWNED", "The Matrix-global start does not resolve to an owned ROM Zone cell.", status_code=422)
            return NavNode(int(zone_id), x, y, z), {"input_type": "global_grid", "matrix_id": matrix_id}
        raise NavigationPlanningError("NAV_INVALID_START", "Explicit start must be grid or global_grid.", status_code=422)

    def create_plan(
        self,
        destination: dict[str, Any],
        start_position: dict[str, Any] | None = None,
        *,
        occupied: Iterable[Any] = (),
        interaction: dict[str, Any] | None = None,
        movement_mode: str = "auto",
        navigation_intent: str = "walk_to_tile",
        allowed_nodes: Iterable[Any] = (),
    ) -> dict[str, Any]:
        start_source = "player_runtime"
        if start_position is None:
            player = canonical_grid_player(self.player_sample(), require_resolved=True)
            start = NavNode.from_player(player)
            if start is None:
                raise NavigationPlanningError(
                    "NAV_PLAYER_UNRESOLVED",
                    "PlayerRuntime does not contain a resolved Zone and GPos.",
                    details={"player_status": player.get("status"), "reason": player.get("reason")},
                )
        else:
            # Explicit starts are for offline/static planning only.
            player = {"status": "candidate", "confidence": "candidate", "frame": None}
            start, start_meta = self._resolve_start_node(start_position, player)
            start_source = "explicit_global_grid" if start_meta.get("input_type") == "global_grid" else "explicit_grid"

        goal, goal_meta = self._resolve_destination_node(destination, start)
        start_matrix_id = self._matrix_id_for_zone(start.zone_id)
        goal_matrix_id = self._matrix_id_for_zone(goal.zone_id)
        if goal.zone_id != start.zone_id and (start_matrix_id is None or goal_matrix_id is None or start_matrix_id != goal_matrix_id):
            code = "NAV_MATRIX_TRANSITION_UNVERIFIED" if start_matrix_id is not None and goal_matrix_id is not None else "NAV_TRANSITION_UNVERIFIED"
            raise NavigationPlanningError(
                code,
                "Cross-Matrix planning requires a verified connector; Zone boundaries inside one proven Matrix are not navigation barriers.",
                details={"start_zone_id": start.zone_id, "goal_zone_id": goal.zone_id, "start_matrix_id": start_matrix_id, "goal_matrix_id": goal_matrix_id},
            )
        if navigation_intent not in {"route", "walk_to_tile", "interact"}:
            raise NavigationPlanningError(
                "NAV_INVALID_INTENT",
                "navigation_intent must be route, walk_to_tile, or interact.",
                status_code=422,
            )
        if navigation_intent in {"route", "walk_to_tile"} and interaction is not None:
            raise NavigationPlanningError(
                "NAV_INTENT_CONFLICT",
                "Pure movement intents cannot include NPC interaction metadata; use navigation_intent='interact'.",
                status_code=422,
            )
        if navigation_intent == "interact" and interaction is None:
            raise NavigationPlanningError(
                "NAV_INTERACTION_TARGET_REQUIRED",
                "interact requires an NPC target and adjacent standing tile.",
                status_code=422,
            )
        normalized_interaction = self._normalize_interaction(
            interaction, start=start, goal=goal, navigation_intent=navigation_intent,
        )

        # An observed route is authoritative for geometry, but a caller's
        # current actor occupancy still makes individual tiles unavailable.
        # Treat such a route as temporarily unusable so the ROM candidate
        # planner can choose a detour around the actor.
        occupied_nodes = self._occupied_nodes(occupied, zone_id=start.zone_id, y=start.y)
        occupied_nodes.discard((start.x, start.z))
        normalized_occupied = normalize_occupancy(
            occupied, default_zone=start.zone_id, default_y=start.y,
        )
        normalized_allowed = normalize_occupancy(
            allowed_nodes, default_zone=start.zone_id, default_y=start.y,
        )
        allowed_xy = self._occupied_nodes(
            normalized_allowed, zone_id=start.zone_id, y=start.y,
        ) if normalized_allowed else None
        if allowed_xy is not None and ((start.x, start.z) not in allowed_xy or (goal.x, goal.z) not in allowed_xy):
            raise NavigationPlanningError(
                "NAV_ALLOWED_SUBGRAPH_MISMATCH",
                "Start and goal must both belong to the allowed navigation subgraph.",
                status_code=409,
                details={"start": start.public(), "goal": goal.public(), "allowed_tile_count": len(allowed_xy)},
            )
        if navigation_intent in {"route", "walk_to_tile"} and (goal.x, goal.z) in occupied_nodes:
            raise NavigationPlanningError(
                "NAV_DESTINATION_OCCUPIED",
                "The fixed destination tile is currently occupied by a runtime actor.",
                details={"destination": goal.public(), "occupied": sorted(occupied_nodes)},
            )
        result = (
            self.graph.find_path(start, goal, require_direct_observation=True)
            if start.zone_id == goal.zone_id
            else {"reachable": False, "reason": "cross-Zone same-Matrix route requires Matrix-global static graph", "path": []}
        )
        if result.get("reachable"):
            observed_path = result.get("path") or []
            if occupied_nodes and any(
                (int(point.get("x")), int(point.get("z"))) in occupied_nodes
                for point in observed_path
                if isinstance(point, dict)
            ):
                result = {
                    "reachable": False,
                    "reason": "observed route intersects current runtime actor occupancy",
                    "path": [],
                }
            elif allowed_xy is not None and any(
                (int(point.get("x")), int(point.get("z"))) not in allowed_xy
                for point in observed_path
                if isinstance(point, dict)
            ):
                result = {
                    "reachable": False,
                    "reason": "observed route leaves the allowed navigation subgraph",
                    "path": [],
                }
        route_source = "verified_observed"
        route_confidence = "verified_observed"
        warnings: list[Any] = []
        if not result.get("reachable"):
            # The ROM graph is intentionally a fallback.  It fills the gap
            # between a newly loaded room and the observation trace, while
            # retaining an explicit candidate label for the executor/UI.
            provider = self._resolve_static_provider()
            finder = getattr(provider, "find_path", None) if provider is not None else None
            if callable(finder):
                try:
                    try:
                        static_result = finder(
                            start, goal, player_sample=self.player_sample(), occupied=normalized_occupied,
                            allowed=normalized_allowed,
                        )
                    except TypeError:
                        # Compatibility for providers written before the
                        # allowed-subgraph contract. Preserve dynamic actor
                        # occupancy first; only drop the new constraint. The
                        # final path invariant below still rejects any escape
                        # from allowed_nodes.
                        try:
                            static_result = finder(
                                start, goal, player_sample=self.player_sample(), occupied=normalized_occupied,
                            )
                        except TypeError:
                            static_result = finder(start, goal, player_sample=self.player_sample())
                except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
                    static_result = {
                        "reachable": False,
                        "reason": f"static provider unavailable: {type(exc).__name__}",
                        "path": [],
                    }
                if static_result.get("reachable"):
                    result = static_result
                    route_source = "candidate_static"
                    route_confidence = "candidate_static"
                    warnings.append({
                        "code": "NAV_STATIC_CANDIDATE",
                        "message": "Route is compiled from ROM static collision candidates; every step requires live PlayerRuntime verification.",
                    })
        if not result.get("reachable"):
            raise NavigationPlanningError(
                "NAV_NO_ROUTE",
                "No connected route exists in the observed layered graph.",
                details={
                    "start": start.public(),
                    "goal": goal.public(),
                    "reason": result.get("reason"),
                    "static_candidate": self.static_status(),
                },
            )

        # Do not expose graph persistence metadata in the public drawing path.
        path = [
            {"zone_id": int(p["zone_id"]), "x": int(p["x"]), "y": int(p["y"]), "z": int(p["z"])}
            for p in result.get("path", [])
        ]
        occupied_path = [
            point for point in path[1:]
            if (point["x"], point["z"]) in occupied_nodes
        ]
        outside_allowed = [
            point for point in path
            if allowed_xy is not None and (point["x"], point["z"]) not in allowed_xy
        ]
        if occupied_path:
            # This is an invariant check in addition to the graph/provider
            # filters.  It prevents a third-party provider or a malformed
            # occupancy shape from ever exposing an unsafe drawable route.
            raise NavigationPlanningError(
                "NAV_OCCUPANCY_CONFLICT",
                "The planned route intersects a current runtime actor occupancy.",
                details={"occupied": occupied_path},
            )
        if outside_allowed:
            raise NavigationPlanningError(
                "NAV_ALLOWED_SUBGRAPH_ESCAPE",
                "The planned route leaves the caller-supplied allowed navigation subgraph.",
                details={"outside": outside_allowed, "allowed_tile_count": len(allowed_xy or ())},
            )
        if any(
            abs(path[index]["x"] - path[index - 1]["x"])
            + abs(path[index]["z"] - path[index - 1]["z"])
            != 1
            for index in range(1, len(path))
        ):
            raise NavigationPlanningError(
                "NAV_EDGE_UNEXECUTABLE",
                "The observed route contains an edge without a cardinal input mapping.",
            )

        movement = self.movement_capabilities(
            start.zone_id, requested=movement_mode,
            player=self.player_sample() if start_source == "player_runtime" else None,
            path=[NavNode(p["zone_id"], p["x"], p["y"], p["z"]) for p in path],
        )
        revision_source = {
            "status": self.graph.status(),
            "path": path,
            "route_source": route_source,
            "static_revision": result.get("world_revision") if route_source == "candidate_static" else None,
        }
        revision = sha256(
            json.dumps(revision_source, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:16]
        steps = max(0, len(path) - 1)
        action_segments = _path_action_segments(path)
        turns = int(result.get("turns")) if isinstance(result.get("turns"), int) else max(0, len(action_segments) - 1)
        optimization = result.get("optimization") or "shortest_verified_path_with_continuous_direction_segments"
        response = {
            "format": "black2-navigation-plan/v1",
            "plan_id": f"plan_{uuid4().hex}",
            "status": "ready",
            "world_revision": (
                f"static:{result.get('world_revision') or 'rom'}:{revision}"
                if route_source == "candidate_static" else f"observed:{revision}"
            ),
            "route_source": route_source,
            "confidence": route_confidence,
            "navigation_intent": navigation_intent,
            "resolved_start": {
                "zone_id": start.zone_id,
                **({"global": {"type": "global_grid", "space": "gen5-matrix-grid-v1", "matrix_id": start_matrix_id, "x": start.x, "y": start.y, "z": start.z}} if start_matrix_id is not None else {}),
                "position": {"x": start.x, "y": start.y, "z": start.z},
                "frame": player.get("frame"),
                "source": start_source,
                "confidence": "candidate" if start_source == "explicit_grid" else player.get("confidence"),
            },
            "resolved_goal": {
                "zone_id": goal.zone_id,
                "position": {"x": goal.x, "y": goal.y, "z": goal.z},
                **({"global": {"type": "global_grid", "space": "gen5-matrix-grid-v1", "matrix_id": goal_matrix_id, "x": goal.x, "y": goal.y, "z": goal.z}} if goal_matrix_id is not None else {}),
                **({"zone_resolved_from_global": True} if goal_meta.get("zone_resolved") else {}),
            },
            "route_detail": {
                "start": start.public(),
                "goal": goal.public(),
                "nodes": path,
                "actions": action_segments,
                "continuous_input": True,
                "turns": turns,
                "optimization": optimization,
            },
            "segments": [
                {
                    "kind": "matrix_global" if start.zone_id != goal.zone_id else "local",
                    "zone_id": start.zone_id if start.zone_id == goal.zone_id else None,
                    "matrix_id": start_matrix_id,
                    "from": start.public(),
                    "to": goal.public(),
                    "steps": steps,
                    "turns": turns,
                    "optimization": optimization,
                    "cost": result.get("cost", float(steps)),
                    "confidence": route_confidence,
                    "source": route_source,
                    "evidence": "rom_static_collision_candidate" if route_source == "candidate_static" else "direct_player_runtime_observation",
                    "path": path,
                    "actions": action_segments,
                }
            ],
            "cost": {"steps": steps, "turns": turns, "zone_transitions": len(result.get("zone_transitions") or []), "connectors": 0},
            "zone_transitions": result.get("zone_transitions") or [],
            "movement": movement,
            "warnings": warnings,
            "blockers": [],
            "constraints": {
                "allowed_tile_count": len(allowed_xy) if allowed_xy is not None else None,
                "path_within_allowed_tiles": True if allowed_xy is not None else None,
            },
        }
        if normalized_interaction is not None:
            response["interaction"] = normalized_interaction
            response["interaction_target"] = normalized_interaction["target"]
            response["stand_tile"] = normalized_interaction["stand_tile"]
            response["facing"] = normalized_interaction["facing"]
            response["turn_only"] = normalized_interaction["turn_only"]
        return response
