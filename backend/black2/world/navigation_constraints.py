"""Typed, explainable constraints shared by observed and ROM navigation.

The ROM and runtime layers deliberately expose evidence, not conclusions.  A
constraint therefore carries its source and confidence, while the evaluator
applies the caller's policy to decide whether it is a hard block, a soft cost,
or a terminal target.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal


ConstraintBehavior = Literal[
    "hard_block", "soft_cost", "terminal", "occupancy", "warning", "unknown",
]
ConstraintKind = Literal[
    "dynamic_actor", "script_trigger", "story_gate", "trainer_sight",
    "warp", "capability_gate", "field_obstacle", "encounter_grass",
]


DEFAULT_AGENT_POLICY: dict[str, Any] = {
    "dynamic_actor": "hard_avoid",
    "script_trigger_unknown": "soft_avoid",
    "story_gate_active": "hard_avoid",
    "trainer_sight": "soft_avoid",
    "unexpected_warp": "hard_avoid",
    "capability_gate_unavailable": "hard_avoid",
    "field_obstacle": "hard_avoid",
    "encounter_grass": "soft_avoid",
}

_DEFAULT_SOFT_COSTS = {
    "script_trigger": 25.0,
    "trainer_sight": 10.0,
    "encounter_grass": 15.0,
}


def constraint_knowledge_state(constraint: "NavigationConstraint") -> str:
    """Describe what kind of evidence backs a public route constraint.

    The source takes priority over confidence here.  In particular, a ROM
    record may be structurally well decoded, but it has not become a live
    trainer, sight-line, or script observation merely because it was decoded.
    """
    source = str(constraint.source or "").lower()
    confidence = str(constraint.confidence or "").lower()
    metadata = constraint.metadata if isinstance(constraint.metadata, dict) else {}
    metadata_text = " ".join(
        str(metadata.get(key, "")).lower()
        for key in ("evidence", "evidence_source", "source_kind", "provenance")
    )
    evidence_text = f"{source} {metadata_text}"

    # Static ROM evidence must always win over a generic "verified" label.
    # It proves that a record exists, not that its dynamic trigger is live.
    if "rom" in evidence_text or "static" in evidence_text:
        return "static_candidate"
    if "memory" in evidence_text:
        return "memory"
    if "predict" in evidence_text:
        return "predicted"
    if "observ" in evidence_text:
        return "observed"
    if "runtime" in evidence_text or "ram" in evidence_text:
        if confidence in {
            "runtime", "verified", "verified_runtime", "runtime_verified", "confirmed",
        } or str(constraint.status).lower() in {
            "present", "active", "confirmed_active", "verified", "verified_active",
        }:
            return "runtime_verified"
        return "runtime_candidate"
    return "unresolved"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _tile(value: Any) -> tuple[int | None, int, int, int] | None:
    if isinstance(value, (tuple, list)) and len(value) >= 4:
        zone, x, y, z = value[:4]
    elif isinstance(value, dict):
        zone, x, y, z = value.get("zone_id"), value.get("x"), value.get("y"), value.get("z")
    else:
        return None
    x_i, y_i, z_i = _int(x), _int(y), _int(z)
    if x_i is None or y_i is None or z_i is None:
        return None
    return _int(zone), x_i, y_i, z_i


@dataclass(frozen=True)
class NavigationConstraint:
    constraint_id: str
    kind: ConstraintKind | str
    behavior: ConstraintBehavior | str
    tiles: tuple[tuple[int | None, int, int, int], ...]
    cost: float | None
    dynamic: bool
    confidence: str
    source: str
    status: str
    metadata: dict[str, Any]

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tiles"] = [
            {"zone_id": zone, "x": x, "y": y, "z": z}
            for zone, x, y, z in self.tiles
        ]
        return payload

    @classmethod
    def from_public(cls, value: Any) -> "NavigationConstraint | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        constraint_id = value.get("constraint_id")
        kind = value.get("kind")
        behavior = value.get("behavior")
        if not constraint_id or not kind or not behavior:
            return None
        # API callers are allowed to send arbitrary JSON while Pydantic only
        # validates the outer request shape.  Treat a malformed tiles value as
        # an unusable constraint instead of letting iteration/type conversion
        # escape as a 500 from the route layer.
        raw_tiles = value.get("tiles")
        if not isinstance(raw_tiles, (list, tuple)):
            raw_tiles = ()
        tiles = tuple(tile for item in raw_tiles if (tile := _tile(item)) is not None)
        if not tiles:
            return None
        raw_cost = value.get("cost")
        try:
            cost = float(raw_cost) if raw_cost is not None else None
        except (TypeError, ValueError):
            cost = None
        raw_metadata = value.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        return cls(
            constraint_id=str(constraint_id), kind=str(kind), behavior=str(behavior),
            tiles=tiles, cost=cost, dynamic=bool(value.get("dynamic", False)),
            confidence=str(value.get("confidence") or "unresolved"),
            source=str(value.get("source") or "unknown"),
            status=str(value.get("status") or "unresolved"),
            metadata=metadata,
        )


def normalize_constraints(values: Iterable[Any] = ()) -> list[NavigationConstraint]:
    result: list[NavigationConstraint] = []
    seen: set[str] = set()
    for value in values or ():
        constraint = NavigationConstraint.from_public(value)
        if constraint is None or constraint.constraint_id in seen:
            continue
        seen.add(constraint.constraint_id)
        result.append(constraint)
    return result


def compile_occupancy_as_constraints(values: Iterable[Any] = ()) -> list[NavigationConstraint]:
    """Keep the legacy occupancy input while giving it typed semantics."""
    # Local import avoids making navigation_planning and this module import
    # each other during application startup.
    from .navigation_planning import normalize_occupancy

    result: list[NavigationConstraint] = []
    for item in normalize_occupancy(values):
        grid = item.get("grid") or {}
        zone, x, y, z = item.get("zone_id"), grid.get("x"), grid.get("y"), grid.get("z")
        if x is None or y is None or z is None:
            continue
        actor_id = item.get("actor_id") or item.get("id")
        suffix = str(actor_id) if actor_id is not None else f"{zone}:{x}:{y}:{z}"
        result.append(NavigationConstraint(
            constraint_id=f"dynamic_actor:{suffix}", kind="dynamic_actor", behavior="occupancy",
            tiles=((_int(zone), int(x), int(y), int(z)),), cost=None, dynamic=True,
            confidence="runtime", source="runtime_actor_system", status="present",
            metadata={"actor_id": actor_id} if actor_id is not None else {},
        ))
    return result


class ConstraintEvaluator:
    """Apply one policy consistently to observed and static route search."""

    def __init__(
        self, constraints: Iterable[Any] = (), policy: dict[str, Any] | None = None,
    ) -> None:
        self.constraints = tuple(normalize_constraints(constraints))
        self.policy = {**DEFAULT_AGENT_POLICY, **dict(policy or {})}

    @staticmethod
    def _policy_key(constraint: NavigationConstraint) -> str:
        if constraint.kind == "script_trigger" and constraint.status not in {"active", "confirmed_active"}:
            return "script_trigger_unknown"
        if constraint.kind == "story_gate" and constraint.status in {"unavailable", "active", "confirmed_active"}:
            return "story_gate_active"
        if constraint.kind == "warp" and constraint.behavior != "terminal":
            return "unexpected_warp"
        if constraint.kind == "capability_gate" and constraint.status not in {"available", "verified_available"}:
            return "capability_gate_unavailable"
        return str(constraint.kind)

    def _effective_behavior(self, constraint: NavigationConstraint) -> tuple[str, float | None]:
        if str(constraint.behavior) == "terminal":
            return "terminal", None
        configured = self.policy.get(self._policy_key(constraint))
        if configured is not None:
            value = str(configured)
            if value in {"hard_avoid", "hard_block", "block"}:
                return "hard_block", None
            if value in {"soft_avoid", "soft_cost", "avoid_unknown"}:
                return "soft_cost", constraint.cost if constraint.cost is not None else _DEFAULT_SOFT_COSTS.get(str(constraint.kind))
            if value in {"allow", "ignore"}:
                return "warning", None
            if value == "terminal":
                return "terminal", None
        behavior = str(constraint.behavior)
        if behavior == "unknown":
            return "soft_cost", constraint.cost if constraint.cost is not None else _DEFAULT_SOFT_COSTS.get(str(constraint.kind), 0.0)
        if behavior == "occupancy":
            return "hard_block", None
        return behavior, constraint.cost

    def decision(self, constraint: NavigationConstraint | dict[str, Any]) -> dict[str, Any]:
        """Expose the policy decision used by route search.

        This is deliberately derived from the same evaluator used by A*, so a
        public hazard cannot claim a different intervention policy from the
        one that selected the route.
        """
        normalized = NavigationConstraint.from_public(constraint)
        if normalized is None:
            return {
                "policy_key": None,
                "policy_value": None,
                "effective_behavior": "unknown",
                "effective_cost": None,
                "interruption_policy": "warn",
            }
        effective_behavior, effective_cost = self._effective_behavior(normalized)
        policy_key = self._policy_key(normalized)
        policy_value = self.policy.get(policy_key)
        policy_value_text = str(policy_value) if policy_value is not None else None
        if effective_behavior == "terminal":
            # Terminal is an allowed target in the evaluator.  A policy on a
            # Warp's normal traversal mode is intentionally not applied to a
            # constraint that is already an explicit terminal target.
            interruption_policy = "allow"
        elif policy_value_text in {"allow", "ignore", "terminal"}:
            # Internally allow/ignore is represented as a warning behavior so
            # it remains visible to A*.  The public intervention policy must
            # still honor the caller's explicit permission to proceed.
            interruption_policy = "allow"
        elif policy_value_text in {
            "hard_avoid", "hard_block", "block", "soft_avoid", "soft_cost", "avoid_unknown",
        }:
            interruption_policy = "avoid"
        elif effective_behavior in {"hard_block", "soft_cost"}:
            interruption_policy = "avoid"
        elif effective_behavior == "warning":
            interruption_policy = "warn"
        else:
            # A terminal target is intentional, and a caller's allow/ignore
            # override must remain visible as an allow rather than a warning.
            interruption_policy = "allow"
        return {
            "policy_key": policy_key,
            "policy_value": policy_value_text,
            "effective_behavior": effective_behavior,
            "effective_cost": effective_cost,
            "interruption_policy": interruption_policy,
        }

    def evaluate(self, node: Any, *, is_goal: bool = False) -> dict[str, Any]:
        zone = _int(getattr(node, "zone_id", None))
        x = _int(getattr(node, "x", None))
        y = _int(getattr(node, "y", None))
        z = _int(getattr(node, "z", None))
        if zone is None or x is None or y is None or z is None:
            return {"blocked": True, "extra_cost": 0.0, "terminal": False, "constraints": []}
        matched: list[NavigationConstraint] = []
        for constraint in self.constraints:
            if any(
                tile_zone in {None, zone} and (tile_x, tile_y, tile_z) == (x, y, z)
                for tile_zone, tile_x, tile_y, tile_z in constraint.tiles
            ):
                matched.append(constraint)
        blocked = False
        terminal = False
        extra_cost = 0.0
        blocking: list[dict[str, Any]] = []
        for constraint in matched:
            behavior, cost = self._effective_behavior(constraint)
            if behavior == "hard_block":
                blocked = True
                blocking.append(constraint.public())
            elif behavior == "soft_cost":
                extra_cost += float(cost or 0.0)
            elif behavior == "terminal":
                terminal = True
        # A terminal constraint is an allowed destination, but it must never
        # erase a separate hard block that happens to share its tile.
        return {
            "blocked": blocked, "extra_cost": extra_cost, "terminal": terminal,
            "constraints": [constraint.public() for constraint in matched],
            "blocking_constraints": blocking,
        }

    def public(self) -> list[dict[str, Any]]:
        return [constraint.public() for constraint in self.constraints]

    def summary(self, path: Iterable[Any]) -> dict[str, int]:
        encountered: dict[str, dict[str, Any]] = {}
        for node in path:
            for item in self.evaluate(node).get("constraints") or ():
                encountered.setdefault(str(item.get("constraint_id")), item)
        hard = soft = unknown = 0
        for item in encountered.values():
            constraint = NavigationConstraint.from_public(item)
            if constraint is None:
                continue
            behavior, _ = self._effective_behavior(constraint)
            if behavior == "hard_block":
                hard += 1
            elif behavior == "soft_cost":
                soft += 1
                if constraint.kind == "script_trigger" and constraint.status not in {"active", "confirmed_active"}:
                    unknown += 1
        return {"hard_avoided": hard, "soft_crossed": soft, "unknown_events_avoided": unknown}
