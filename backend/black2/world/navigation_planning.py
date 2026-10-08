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
from .navigation_constraints import (
    ConstraintEvaluator,
    NavigationConstraint,
    constraint_knowledge_state,
    compile_occupancy_as_constraints,
    normalize_constraints,
)


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
        y_change = (path[index].get("y") != path[index - 1].get("y"))
        count = 1
        if not y_change:
            while index + count < len(path):
                next_delta = (
                    path[index + count]["x"] - path[index + count - 1]["x"],
                    path[index + count]["z"] - path[index + count - 1]["z"],
                )
                if names.get(next_delta, "Invalid") != direction:
                    break
                if path[index + count].get("y") != path[index + count - 1].get("y"):
                    break
                count += 1
        actions.append({
            "direction": direction,
            "steps": count,
            "from": path[index - 1],
            "to": path[index + count - 1],
            "elevation_transition": y_change,
        })
        index += count
    return actions


def _hazard_trigger_condition(
    constraint: NavigationConstraint, *, knowledge_state: str,
) -> dict[str, Any]:
    """Explain the route event without promoting static records to runtime fact."""
    kind = str(constraint.kind)
    event, required_state = {
        "trainer_sight": (
            "enter_trainer_sight_tile",
            "the trainer sight-line is active when the player enters the tile",
        ),
        "script_trigger": (
            "enter_script_trigger_tile",
            "the script activation condition is satisfied on entry",
        ),
        "story_gate": (
            "enter_story_gate_tile",
            "the story-gate state permits or blocks entry",
        ),
        "warp": (
            "enter_warp_tile",
            "the warp transition activates when the player enters the tile",
        ),
        "dynamic_actor": (
            "enter_actor_occupied_tile",
            "the actor remains on the tile at execution time",
        ),
    }.get(kind, (
        "enter_constrained_tile",
        "the constraint remains active when the player enters the tile",
    ))
    condition: dict[str, Any] = {
        "event": event,
        "required_state": required_state,
        "status": str(constraint.status),
        "runtime_confirmation_required": knowledge_state not in {
            "runtime_verified", "observed",
        },
    }
    trainer_id = constraint.metadata.get("trainer_id")
    if kind == "trainer_sight" and trainer_id is not None:
        condition["trainer_id"] = trainer_id
    if kind == "warp" and "navigation_target" in constraint.metadata:
        condition["navigation_target"] = bool(constraint.metadata["navigation_target"])
    return condition


def _route_hazards(
    path: list[NavNode], constraint_evaluator: ConstraintEvaluator,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Project route constraints onto the final chosen path.

    Search explores branches that do not end up in the plan.  Public hazards
    must instead describe only constraints whose tiles occur in the returned
    route, with the same policy evaluation that search used.
    """
    by_id = {constraint.constraint_id: constraint for constraint in constraint_evaluator.constraints}
    matched: dict[str, dict[str, Any]] = {}
    blocked_on_path: list[dict[str, Any]] = []
    last_index = len(path) - 1

    for route_index, node in enumerate(path):
        evaluation = constraint_evaluator.evaluate(node, is_goal=route_index == last_index)
        for item in evaluation.get("constraints") or ():
            constraint_id = str(item.get("constraint_id"))
            constraint = by_id.get(constraint_id) or NavigationConstraint.from_public(item)
            if constraint is None:
                continue
            current = matched.get(constraint.constraint_id)
            if current is None:
                public = constraint.public()
                decision = constraint_evaluator.decision(constraint)
                knowledge_state = constraint_knowledge_state(constraint)
                current = {
                    "constraint_id": constraint.constraint_id,
                    "kind": constraint.kind,
                    "tiles": public["tiles"],
                    "source": constraint.source,
                    "confidence": constraint.confidence,
                    "status": constraint.status,
                    "metadata": public["metadata"],
                    "knowledge_state": knowledge_state,
                    "trigger_condition": _hazard_trigger_condition(
                        constraint, knowledge_state=knowledge_state,
                    ),
                    "interruption_policy": decision["interruption_policy"],
                    "decision": decision,
                    "route_indices": [],
                    "first_route_index": route_index,
                }
                matched[constraint.constraint_id] = current
            current["route_indices"].append(route_index)

        # A compliant planner filters these during A*.  This final invariant
        # keeps a legacy provider that ignores constraint_evaluator from
        # exposing a hard-blocked tile as an executable public route.  The
        # starting tile is intentionally excluded: the avatar may already be
        # standing there when runtime state changes beneath it.
        if route_index > 0 and evaluation.get("blocked"):
            for item in evaluation.get("blocking_constraints") or ():
                blocked_on_path.append({**item, "route_index": route_index})

    hazards = sorted(
        matched.values(),
        key=lambda item: (int(item["first_route_index"]), str(item["constraint_id"])),
    )
    by_kind: dict[str, int] = {}
    by_policy: dict[str, int] = {}
    by_knowledge_state: dict[str, int] = {}
    for hazard in hazards:
        kind = str(hazard["kind"])
        interruption_policy = str(hazard["interruption_policy"])
        knowledge_state = str(hazard["knowledge_state"])
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_policy[interruption_policy] = by_policy.get(interruption_policy, 0) + 1
        by_knowledge_state[knowledge_state] = by_knowledge_state.get(knowledge_state, 0) + 1
    summary = {
        "total": len(hazards),
        "by_kind": dict(sorted(by_kind.items())),
        "by_interruption_policy": dict(sorted(by_policy.items())),
        "by_knowledge_state": dict(sorted(by_knowledge_state.items())),
        "soft_cost_crossed": sum(
            hazard["decision"]["effective_behavior"] == "soft_cost"
            for hazard in hazards
        ),
        "terminal_crossed": sum(
            hazard["decision"]["effective_behavior"] == "terminal"
            for hazard in hazards
        ),
        "runtime_revalidation_required": sum(
            hazard["knowledge_state"] not in {"runtime_verified", "observed"}
            for hazard in hazards
        ),
    }
    decisions = [
        {
            "constraint_id": hazard["constraint_id"],
            "first_route_index": hazard["first_route_index"],
            "route_indices": list(hazard["route_indices"]),
            **hazard["decision"],
        }
        for hazard in hazards
    ]
    return hazards, summary, decisions, blocked_on_path


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
        configure_identity = getattr(self.graph, "configure_matrix_identity", None)
        if callable(configure_identity):
            # Resolve lazily at observation time.  RuntimeHub must not trigger
            # ROM parsing merely because it records an ordinary local step.
            configure_identity(
                matrix_for_zone=self._matrix_id_for_observation_zone,
                zone_owner=self._zone_owner_for_observation,
            )

    def _matrix_id_for_observation_zone(self, zone_id: int) -> int | None:
        provider = self._resolve_static_provider()
        try:
            return int(provider.rom.zone(int(zone_id)).matrix_id)
        except (AttributeError, IndexError, OSError, RuntimeError, TypeError, ValueError):
            return None

    def _zone_owner_for_observation(self, matrix_id: int, x: int, z: int) -> int | None:
        provider = self._resolve_static_provider()
        resolver = getattr(provider, "resolve_zone_for_global", None)
        if not callable(resolver):
            return None
        try:
            return resolver(int(matrix_id), int(x), int(z))
        except (IndexError, OSError, RuntimeError, TypeError, ValueError):
            return None

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
        movement_mode: str = "walk",
        constraint_evaluator: ConstraintEvaluator | None = None,
        allow_unverified_terrain: bool = False,
    ) -> bool:
        provider = self._resolve_static_provider()
        checker = getattr(provider, "has_candidate_edge", None) if provider is not None else None
        if not callable(checker):
            return False
        try:
            return bool(checker(
                start, goal, player_sample=self.player_sample(), occupied=occupied,
                movement_mode=movement_mode,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            ))
        except TypeError:
            # Keep third-party/test providers written against the original
            # two-argument contract usable.
            try:
                return bool(checker(
                    start, goal, player_sample=self.player_sample(),
                    movement_mode=movement_mode,
                ))
            except TypeError:
                # Providers written before movement-mode support may still be
                # used for walk/run plans. Keep the old contract as a final
                # compatibility fallback; callers must still pass the mode
                # to providers that implement it.
                try:
                    return bool(checker(start, goal, player_sample=self.player_sample()))
                except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError):
                    return False
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
                "static_tile_predicates": {
                    "walk": "clear collision and not water/surf-required",
                    "run": "walk predicate plus ZoneHeader.enable_running and OnFoot runtime",
                    "bike": "walk predicate plus ZoneHeader.enable_cycling, Cycling runtime, and no blocks_cycling material",
                    "surf": "water or water_edge only plus active Surf runtime",
                },
                "dynamic_activation": {
                    "mount_bike": "not auto-started; caller must establish Cycling PlayerRuntime",
                    "start_surf": "not auto-started; caller must establish Surf PlayerRuntime",
                    "per_step_verification": "required after every input segment",
                },
            },
            "directed_terrain": {
                "supported": True,
                "sources": ["TileClass directional barriers", "ledge_direction"],
                "policy": "both source and destination direction checks must permit an edge",
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
        path: Iterable[NavNode] = (), inventory: dict[str, Any] | None = None,
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
        equipment = (player or {}).get("equipment") if isinstance((player or {}).get("equipment"), dict) else {}
        player_caps = (player or {}).get("capabilities") if isinstance((player or {}).get("capabilities"), dict) else {}

        # Gen-V does not expose Running Shoes as a normal consumable.  The
        # native field code calls FieldPlayer_CheckRunningShoesFlag.  Prefer a
        # future runtime flag/equipment decoder, then fall back to bag
        # identities for Bicycle.  When the bag is unavailable we preserve the
        # legacy ZoneHeader behaviour, but mark the evidence as unknown so the
        # caller can see why a strict decision was not possible.
        bag_known = bool(isinstance(inventory, dict) and inventory.get("contents_known"))
        bag_items = inventory.get("items") if isinstance(inventory, dict) else []
        bag_items = bag_items if isinstance(bag_items, list) else []
        def has_item(*, identifiers: set[str], item_ids: set[int] = frozenset(), game_indices: set[int] = frozenset()) -> bool:
            """Check for an item by identifier (preferred), internal item_id, or game_index.

            item_id and game_index are kept separate because they are completely
            different ID spaces and must NOT be interchanged.
            Example: Level Ball has item_id=450, game_index=493.
                     Bicycle has item_id=427, game_index=450.
            Mixing them caused a false-positive match on Level Ball.
            """
            for item in bag_items:
                if not isinstance(item, dict) or int(item.get("quantity", 0) or 0) <= 0:
                    continue
                if item.get("identifier") in identifiers:
                    return True
                try:
                    raw_item_id = item.get("item_id")
                    raw_game_index = item.get("game_index")
                    if raw_item_id is not None and int(raw_item_id) in item_ids:
                        return True
                    if raw_game_index is not None and int(raw_game_index) in game_indices:
                        return True
                except (TypeError, ValueError):
                    pass
            return False

        running_explicit = equipment.get("running_shoes", player_caps.get("running_shoes"))
        bicycle_explicit = equipment.get("bicycle", player_caps.get("bicycle"))
        if isinstance(running_explicit, bool):
            has_running_shoes = running_explicit
            running_evidence = "runtime_equipment"
        else:
            # In Pokémon Black 2 / White 2, Running Shoes are gifted by Mom upon
            # exiting Aspertia City early in the story and are active across the
            # whole game. Running does not use a selectable bag slot.
            # When playing in any adventure zone (or when bag items are present),
            # Running Shoes are story-confirmed.
            has_running_shoes = True
            running_evidence = "story_confirmed"

        if isinstance(bicycle_explicit, bool):
            has_bicycle = bicycle_explicit
            bicycle_evidence = "runtime_equipment"
        else:
            # Internal ROM item_id=450 is Bicycle (DexStore game_index=450, id=427).
            has_bicycle = has_item(
                identifiers={"bicycle"},
                item_ids={450, 427},
                game_indices={450},
            ) if bag_known else None
            bicycle_evidence = (
                "bag_confirmed" if has_bicycle is True
                else "bag_not_found" if has_bicycle is False
                else "unknown"
            )

        on_foot = transport in (None, "", "OnFoot")
        can_dismount = (transport == "Cycling")
        foot_accessible = on_foot or can_dismount
        run_item_ok = has_running_shoes is True or (has_running_shoes is None and allow_running is True)
        bike_item_ok = has_bicycle is True or (has_bicycle is None and transport == "Cycling")
        available = {
            "walk": foot_accessible,
            "run": foot_accessible and allow_running is True and run_item_ok,
            "bike": allow_cycling is True and not bike_blockers and (transport == "Cycling" or (on_foot and has_bicycle is True)),
            "surf": transport == "Surf",
        }
        owned = {
            "running_shoes": has_running_shoes,
            "bicycle": has_bicycle,
        }
        startable = {
            "walk": foot_accessible,
            "run": foot_accessible and allow_running is True and (has_running_shoes is True),
            "bike": allow_cycling is True and not bike_blockers and has_bicycle is True,
            "surf": transport == "Surf",
        }
        reasons = {
            "walk": "PlayerRuntime is on foot" if on_foot else "Player is on bicycle and can automatically dismount to walk on catwalk/restricted terrain",
            "run": "Running Shoes are owned and ZoneHeader enables running (hold B to run)" if available["run"] else (
                "Running Shoes are not present in the verified bag" if has_running_shoes is False else
                "running is disabled by ZoneHeader or current transport"
            ),
            "bike": (
                "Cycling is active and route permits riding" if transport == "Cycling" and available["bike"]
                else "Bicycle is owned and route is clear (selected for long-distance cruise)" if available["bike"] and (max(0, len(path) - 1) if path else 0) > 4
                else "Short route (<=4 steps); running is preferred to avoid mounting animation" if has_bicycle is True and available.get("run") and (max(0, len(path) - 1) if path else 0) <= 4
                else "Bicycle is owned in bag; riding is enabled by ZoneHeader (use Bicycle to ride)" if has_bicycle is True and allow_cycling is True and not bike_blockers
                else "Bicycle is not present in the verified bag" if has_bicycle is False
                else "Bicycle ownership is unresolved" if has_bicycle is None
                else "cycling is disabled by ZoneHeader, terrain, or current transport"
            ),
            "surf": "PlayerRuntime is already in Surf transport" if available["surf"] else "Surf requires a verified active Surf transport",
        }
        if mode == "auto":
            # Prefer the fastest mode that is already safe to execute.  This
            # removes the previous artificial walk-only default while avoiding
            # speculative item/menu actions to mount a bike or start Surf.
            total_steps = max(0, len(path) - 1) if path else 0
            if transport == "Cycling" and available.get("bike"):
                selected = "bike"
            elif total_steps > 4 and available.get("bike"):
                selected = "bike"
            elif available.get("run"):
                selected = "run"
            elif available.get("bike"):
                selected = "bike"
            elif available.get("surf"):
                selected = "surf"
            else:
                selected = "walk"
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
            "owned": owned,
            "startable": startable,
            "reasons": reasons,
            "evidence": {**evidence, "equipment": {"running_shoes": running_evidence, "bicycle": bicycle_evidence}, "inventory_known": bag_known},
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
        # The live destination is enriched with the ROM Matrix identity by
        # `_resolve_destination_node`, while public interaction coordinates
        # intentionally use only zone/x/y/z.  Matrix id is useful for global
        # routing but must not make an otherwise identical standing tile fail
        # the interaction contract.
        if (stand.zone_id, stand.x, stand.y, stand.z) != (goal.zone_id, goal.x, goal.y, goal.z):
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
            meta = {"input_type": "grid", "matrix_id": self._matrix_id_for_zone(node.zone_id), "zone_resolved": False}
            return self._maybe_snap_door_portal(node, meta)
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
        resolved_node = NavNode(int(zone_id), x, y, z)
        resolved_meta = {"input_type": "global_grid", "matrix_id": matrix_id, "zone_resolved": True}
        return self._maybe_snap_door_portal(resolved_node, resolved_meta)

    def _maybe_snap_door_portal(self, node: NavNode, meta: dict[str, Any]) -> tuple[NavNode, dict[str, Any]]:
        """Snap unwalkable building door portals directly to their reachable doorstep."""
        provider = self._global_provider()
        if provider and hasattr(provider, "event_overlay_at"):
            try:
                overlays = list(provider.event_overlay_at(int(node.zone_id), int(node.x), int(node.z)))
                warp = next((it for it in overlays if it.get("kind") == "warp"), None)
                if warp:
                    geom = warp.get("door_geometry") or {}
                    doorsteps = geom.get("doorsteps") or []
                    surf = provider.surface_at(int(node.zone_id), int(node.x), int(node.z), y=int(node.y)) if hasattr(provider, "surface_at") else {}
                    if (geom.get("type") == "building_portal" or not surf.get("walkable")) and doorsteps:
                        best_ds = min(doorsteps, key=lambda ds: abs(ds[0] - node.x) + abs(ds[1] - node.z) + (0 if ds[0] == node.x else 10))
                        original_target = node.public()
                        node = NavNode(int(node.zone_id), best_ds[0], int(node.y), best_ds[1])
                        meta["original_door_target"] = original_target
                        meta["door_geometry"] = geom
                        meta["is_door_approach"] = True
            except Exception:
                pass
        return node, meta

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
        constraints: Iterable[Any] = (),
        policy: dict[str, Any] | None = None,
        interaction: dict[str, Any] | None = None,
        movement_mode: str = "auto",
        navigation_intent: str = "walk_to_tile",
        allowed_nodes: Iterable[Any] = (),
        allow_unverified_terrain: bool = False,
        inventory: dict[str, Any] | None = None,
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
            provider = self._global_provider()
            provider = self._global_provider()
            if provider and hasattr(provider, "surface_at"):
                try:
                    cur_surf = provider.surface_at(start.zone_id, start.x, start.z, y=start.y)
                    if not cur_surf.get("walkable"):
                        for alt_y in (start.y - 1, start.y + 1, start.y - 2, start.y + 2, 0, 1, 2, -1, -2):
                            alt_surf = provider.surface_at(start.zone_id, start.x, start.z, y=alt_y)
                            if alt_surf.get("walkable"):
                                start = NavNode(start.zone_id, start.x, alt_y, start.z)
                                break
                except Exception:
                    pass
        else:
            # Explicit starts are for offline/static planning only.
            player = {"status": "candidate", "confidence": "candidate", "frame": None}
            start, start_meta = self._resolve_start_node(start_position, player)
            start_source = "explicit_global_grid" if start_meta.get("input_type") == "global_grid" else "explicit_grid"

        goal, goal_meta = self._resolve_destination_node(destination, start)
        start_matrix_id = self._matrix_id_for_zone(start.zone_id)
        goal_matrix_id = self._matrix_id_for_zone(goal.zone_id)
        # Matrix identity is part of an observed-node address. Keep it on the
        # planning nodes so an m0 observation cannot later be queried as m?.
        start = NavNode(start.zone_id, start.x, start.y, start.z, start.matrix_id if start.matrix_id is not None else start_matrix_id)
        goal = NavNode(goal.zone_id, goal.x, goal.y, goal.z, goal.matrix_id if goal.matrix_id is not None else goal_matrix_id)
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

        # Resolve the active transport before invoking either graph.  The
        # static provider must receive the selected mode; otherwise an
        # unblocked water tile or a cycling-blocked tile can leak into an
        # otherwise valid ROM candidate route.  ``movement_capabilities`` is
        # re-evaluated with the final path below (where route-specific bike
        # blockers are known).
        movement_player = self.player_sample() if start_source == "player_runtime" else None
        try:
            provisional_movement = self.movement_capabilities(
                start.zone_id, requested=movement_mode, player=movement_player, path=(), inventory=inventory,
            )
            effective_movement_mode = str(provisional_movement.get("selected") or "walk")
        except NavigationPlanningError:
            # Preserve the original error semantics after route discovery;
            # this fallback only keeps the provider call well-typed when the
            # live transport is unresolved.
            effective_movement_mode = str(movement_mode or "walk").lower()

        # An observed route is authoritative for geometry, but a caller's
        # current actor occupancy still makes individual tiles unavailable.
        # Treat such a route as temporarily unusable so the ROM candidate
        # planner can choose a detour around the actor.
        occupied_nodes = self._occupied_nodes(occupied, zone_id=start.zone_id, y=start.y)
        occupied_nodes.discard((start.x, start.z))
        normalized_occupied = normalize_occupancy(
            occupied, default_zone=start.zone_id, default_y=start.y,
        )
        normalized_constraints = [
            *compile_occupancy_as_constraints(normalized_occupied),
            *normalize_constraints(constraints),
        ]
        constraint_evaluator = ConstraintEvaluator(normalized_constraints, policy=policy)
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
            self.graph.find_path(
                start, goal, require_direct_observation=True,
                constraint_evaluator=constraint_evaluator,
            )
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
            same_matrix_cross_zone = (
                start.zone_id != goal.zone_id
                and start_matrix_id is not None
                and start_matrix_id == goal_matrix_id
            )
            global_finder = getattr(provider, "find_global_path", None) if provider is not None else None
            finder = (
                global_finder if same_matrix_cross_zone and callable(global_finder)
                else getattr(provider, "find_path", None) if provider is not None else None
            )
            # An explicit start is an offline/static coordinate. Passing the
            # live PlayerRuntime sample would anchor terrain-layer resolution
            # at a different player position and can make a valid route look
            # blocked. Live samples are only evidence for runtime starts.
            static_player_sample = self.player_sample() if start_source == "player_runtime" else None
            if callable(finder):
                try:
                    if same_matrix_cross_zone and finder is global_finder:
                        try:
                            static_result = finder(
                                start, matrix_id=start_matrix_id, x=goal.x, y=goal.y, z=goal.z,
                                movement_mode=effective_movement_mode,
                                player_sample=static_player_sample, occupied=normalized_occupied,
                                constraint_evaluator=constraint_evaluator,
                                allow_unverified_terrain=allow_unverified_terrain,
                            )
                        except TypeError:
                            try:
                                static_result = finder(
                                        start, matrix_id=start_matrix_id, x=goal.x, y=goal.y, z=goal.z,
                                        player_sample=static_player_sample, occupied=normalized_occupied,
                                )
                            except TypeError:
                                static_result = finder(
                                        start, matrix_id=start_matrix_id, x=goal.x, y=goal.y, z=goal.z,
                                        player_sample=static_player_sample,
                                )
                    else:
                        try:
                            static_result = finder(
                                start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                allowed=normalized_allowed, constraint_evaluator=constraint_evaluator,
                                movement_mode=effective_movement_mode,
                                allow_unverified_terrain=allow_unverified_terrain,
                            )
                            # 若 auto 模式下优先尝试的自行车因独木桥/窄道地形阻断失败，自动回退尝试奔跑/步行
                            if not static_result.get("reachable") and movement_mode == "auto" and effective_movement_mode == "bike":
                                for fb_mode in ("run", "walk"):
                                    fb_res = finder(
                                        start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                        allowed=normalized_allowed, constraint_evaluator=constraint_evaluator,
                                        movement_mode=fb_mode,
                                        allow_unverified_terrain=allow_unverified_terrain,
                                    )
                                    if fb_res.get("reachable"):
                                        static_result = fb_res
                                        effective_movement_mode = fb_mode
                                        break
                        except TypeError:
                            # Compatibility for providers written before the
                            # allowed-subgraph contract. Preserve dynamic actor
                            # occupancy first; only drop the new constraint. The
                            # final path invariant below still rejects any escape
                            # from allowed_nodes.
                            try:
                                static_result = finder(
                                    start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                    allowed=normalized_allowed,
                                )
                            except TypeError:
                                try:
                                    static_result = finder(
                                        start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                    )
                                except TypeError:
                                        static_result = finder(start, goal, player_sample=static_player_sample)
                except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
                    static_result = {
                        "reachable": False,
                        "reason": f"static provider unavailable: {type(exc).__name__}",
                        "path": [],
                    }
                if static_result.get("reachable") or static_result.get("policy_blocked"):
                    result = static_result
                    if static_result.get("reachable"):
                        route_source = "candidate_static"
                        route_confidence = "candidate_static"
                        warnings.append({
                            "code": "NAV_STATIC_CANDIDATE",
                            "message": "Route is compiled from ROM static collision candidates; every step requires live PlayerRuntime verification.",
                        })
                        if allow_unverified_terrain and int(static_result.get("unverified_tile_count") or 0) > 0:
                            warnings.append({
                                "code": "NAV_UNVERIFIED_TERRAIN_CANDIDATE",
                                "message": "The route crosses flag-clear terrain whose material decode is unverified; every landing must be accepted by live PlayerRuntime before continuing.",
                                "tile_count": int(static_result.get("unverified_tile_count") or 0),
                            })
        if not result.get("reachable") and result.get("policy_blocked"):
            raise NavigationPlanningError(
                "NAV_POLICY_BLOCKED",
                "The navigation policy blocks every connected route to the destination.",
                details={
                    "blocking_constraints": result.get("blocking_constraints") or [],
                    "possible_relaxation": {"policy": "allow or soften the blocking constraint kind"},
                    "start": start.public(), "goal": goal.public(),
                },
            )
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
        def public_path_node(value: dict[str, Any]) -> dict[str, int]:
            zone_id = int(value["zone_id"])
            point = {"zone_id": zone_id, "x": int(value["x"]), "y": int(value["y"]), "z": int(value["z"])}
            matrix_id = value.get("matrix_id")
            if not isinstance(matrix_id, int) or isinstance(matrix_id, bool):
                matrix_id = self._matrix_id_for_zone(zone_id)
            if isinstance(matrix_id, int) and not isinstance(matrix_id, bool):
                point["matrix_id"] = matrix_id
            return point

        path = [public_path_node(p) for p in result.get("path", [])]
        path_nodes = [NavNode(p["zone_id"], p["x"], p["y"], p["z"], p.get("matrix_id")) for p in path]
        route_hazards, route_hazard_summary, route_hazard_decisions, blocked_on_path = _route_hazards(
            path_nodes, constraint_evaluator,
        )
        if blocked_on_path:
            raise NavigationPlanningError(
                "NAV_POLICY_BLOCKED",
                "The navigation policy blocks a tile returned by the route provider.",
                details={
                    "blocking_constraints": blocked_on_path,
                    "possible_relaxation": {"policy": "allow or soften the blocking constraint kind"},
                    "start": start.public(), "goal": goal.public(),
                },
            )
        # Preserve the pre-v14 raw constraint list for consumers that already
        # render it.  route_hazards is the path-indexed, policy-explained API.
        constraints_by_id = {
            constraint.constraint_id: constraint
            for constraint in constraint_evaluator.constraints
        }
        constraints_encountered = [
            constraints_by_id[hazard["constraint_id"]].public()
            for hazard in route_hazards
            if hazard["constraint_id"] in constraints_by_id
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
            path=path_nodes,
            inventory=inventory,
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
            "navigation_constraints": constraint_evaluator.public(),
            "constraint_summary": constraint_evaluator.summary(path_nodes),
            "constraints_encountered": constraints_encountered,
            "route_hazards": route_hazards,
            "route_hazard_summary": route_hazard_summary,
            "route_hazard_decisions": route_hazard_decisions,
            "policy": constraint_evaluator.policy,
            # Keep the effective policy auditable.  The evaluator always
            # starts with DEFAULT_AGENT_POLICY; a non-empty request policy
            # only overrides keys supplied by the caller.
            "policy_source": "request_override" if policy else "default_only",
            "terrain_policy": result.get("terrain_policy") or {
                "allow_unverified_terrain": bool(allow_unverified_terrain),
                "unknown_tiles": "excluded_by_default" if not allow_unverified_terrain else "candidate_with_live_landing_verification",
                "execution": "requires_per_step_PlayerRuntime_landing_verification",
            },
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
