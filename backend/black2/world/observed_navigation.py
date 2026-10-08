"""Evidence-first layered navigation graph for Pokémon Black 2.

A flat 2D collision grid cannot represent bridges, stairs, floors, or two
walkable surfaces sharing X/Z at different elevations.  This module therefore
learns a graph from *observed* PlayerRuntime transitions.  Nodes include the
runtime grid elevation (GPos.y) and edges are promoted only when the player was
actually observed moving between them.

ROM permission bytes remain useful for candidate visualization, but are not used
here as execution-grade truth until their semantics have been independently
validated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heappop, heappush
import json
from pathlib import Path
import threading
from typing import Any, Callable


@dataclass(frozen=True, order=True)
class NavNode:
    zone_id: int
    x: int
    y: int
    z: int
    matrix_id: int | None = None

    @classmethod
    def from_player(cls, player: dict[str, Any] | None) -> "NavNode | None":
        if not player or not isinstance(player.get("zone_id"), int):
            return None
        g = player.get("grid") or {}
        if not all(isinstance(g.get(k), int) for k in ("x", "y", "z")):
            return None
        matrix = player.get("matrix_id")
        if matrix is None and isinstance(player.get("global"), dict):
            matrix = player["global"].get("matrix_id")
        matrix_id = int(matrix) if isinstance(matrix, int) and not isinstance(matrix, bool) else None
        return cls(int(player["zone_id"]), int(g["x"]), int(g["y"]), int(g["z"]), matrix_id)

    def key(self) -> str:
        return f"m{self.matrix_id if self.matrix_id is not None else '?'}:{self.zone_id}:{self.x}:{self.y}:{self.z}"

    def public(self) -> dict[str, int]:
        value = {"zone_id": self.zone_id, "x": self.x, "y": self.y, "z": self.z}
        if self.matrix_id is not None:
            value["matrix_id"] = self.matrix_id
        return value


@dataclass
class ObservedNavigationGraph:
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _loaded: bool = False
    _nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _edges: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _last_node: NavNode | None = None
    _last_frame: int | None = None
    _last_session_id: str | None = None
    _matrix_for_zone: Callable[[int], int | None] | None = None
    _zone_owner: Callable[[int, int, int], int | None] | None = None

    @property
    def path(self) -> Path:
        return self.project_root / "runtime" / "navigation" / "observed_graph.json"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._nodes = data.get("nodes") or {}
                self._edges = data.get("edges") or {}
                # v1 records did not include Matrix identity in their keys.
                # Keep them readable but never promote them into a cross-Zone
                # authorization path after the address schema change.
                legacy_node_keys = {key for key in self._nodes if not str(key).startswith("m")}
                for key in legacy_node_keys:
                    if isinstance(self._nodes.get(key), dict):
                        self._nodes[key]["legacy_matrix_unverified"] = True
                # Stores written before direct observations were explicit are
                # useful for display, but cannot authorize automatic input.
                for source, outgoing in self._edges.items():
                    for target, edge in outgoing.items():
                        if "direct_observations" not in edge:
                            edge["legacy_unverified"] = True
                        if source in legacy_node_keys or target in legacy_node_keys:
                            edge["legacy_matrix_unverified"] = True
            except (OSError, ValueError):
                self._nodes, self._edges = {}, {}
            self._loaded = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": "black2-observed-navigation/v2",
            "semantics": "edges are learned from actual PlayerRuntime tile/elevation transitions; static permission bytes are not promoted here",
            "nodes": self._nodes,
            "edges": self._edges,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def configure_matrix_identity(
        self,
        *,
        matrix_for_zone: Callable[[int], int | None] | None = None,
        zone_owner: Callable[[int, int, int], int | None] | None = None,
    ) -> None:
        """Configure ROM-backed identity resolvers without changing stored data."""
        self._matrix_for_zone = matrix_for_zone
        self._zone_owner = zone_owner

    def _with_matrix_identity(self, node: NavNode) -> NavNode:
        if node.matrix_id is not None or self._matrix_for_zone is None:
            return node
        try:
            matrix_id = self._matrix_for_zone(node.zone_id)
        except (IndexError, OSError, RuntimeError, TypeError, ValueError):
            matrix_id = None
        return NavNode(node.zone_id, node.x, node.y, node.z, matrix_id) if isinstance(matrix_id, int) else node

    def _edge_kind(self, a: NavNode, b: NavNode) -> str | None:
        if a.matrix_id != b.matrix_id:
            return None
        if a.zone_id != b.zone_id:
            dx, dz, dy = abs(a.x - b.x), abs(a.z - b.z), abs(a.y - b.y)
            if a.matrix_id is None or dx + dz != 1 or dy > 1 or self._zone_owner is None:
                return None
            try:
                from_owner = self._zone_owner(a.matrix_id, a.x, a.z)
                to_owner = self._zone_owner(b.matrix_id, b.x, b.z)
            except (IndexError, OSError, RuntimeError, TypeError, ValueError):
                return None
            if from_owner != a.zone_id or to_owner != b.zone_id:
                return None
            return "zone_transition"
        dx, dz, dy = abs(a.x - b.x), abs(a.z - b.z), abs(a.y - b.y)
        if dx + dz == 1:
            return "step" if dy == 0 else "step_with_elevation_change"
        if dx == 0 and dz == 0 and dy > 0:
            return "vertical_same_tile"
        return None

    def observe_player(self, player: dict[str, Any] | None, *, source: str = "calibration") -> dict[str, Any]:
        self._ensure_loaded()
        node = NavNode.from_player(player)
        node = self._with_matrix_identity(node) if node is not None else None
        frame_value = player.get("frame") if player else None
        frame = int(frame_value) if isinstance(frame_value, int) else None
        session_value = player.get("session_id") if player else None
        session_id = str(session_value) if session_value is not None else None
        if node is None:
            self.reset_trace()
            return {"ok": False, "reason": "player grid/zone unresolved"}
        with self._lock:
            entry = self._nodes.setdefault(node.key(), {**node.public(), "observations": 0, "world_samples": []})
            entry["observations"] = int(entry.get("observations", 0)) + 1
            world = (player or {}).get("world") or {}
            if all(isinstance(world.get(k), (int, float)) for k in ("x", "y", "z")):
                samples = entry.setdefault("world_samples", [])
                sample = {"frame": frame, "x": float(world["x"]), "y": float(world["y"]), "z": float(world["z"])}
                if not samples or samples[-1] != sample:
                    samples.append(sample)
                    del samples[:-12]

            edge_record = None
            trace_continuous = (
                self._last_node is not None
                and self._last_frame is not None
                and frame is not None
                and frame > self._last_frame
                and frame - self._last_frame <= 180
                and self._last_session_id == session_id
                and (
                    session_id is not None
                    or source == "calibration"
                    or source.startswith("calibration:")
                )
            )
            if trace_continuous and self._last_node != node:
                kind = self._edge_kind(self._last_node, node)
                if kind is not None:
                    edge_record = self._record_edge(self._last_node, node, kind, frame, source)
            self._last_node = node
            self._last_frame = frame
            self._last_session_id = session_id
            if edge_record or entry["observations"] in (1, 5, 20):
                self._save()
        return {"ok": True, "node": node.public(), "edge": edge_record}

    def _record_edge(self, a: NavNode, b: NavNode, kind: str, frame: int, source: str) -> dict[str, Any]:
        def one(src: NavNode, dst: NavNode) -> dict[str, Any]:
            by_src = self._edges.setdefault(src.key(), {})
            rec = by_src.setdefault(dst.key(), {
                "to": dst.public(), "kind": kind, "observations": 0, "first_frame": frame, "last_frame": frame, "sources": [],
            })
            rec["observations"] = int(rec.get("observations", 0)) + 1
            rec["last_frame"] = frame
            if source not in rec.setdefault("sources", []):
                rec["sources"].append(source)
            return rec
        reverse_existing = (self._edges.get(b.key()) or {}).get(a.key())
        reverse_was_direct = bool(reverse_existing and not reverse_existing.get("inferred_reverse"))
        forward = one(a, b)
        forward["direct_observations"] = int(forward.get("direct_observations", 0)) + 1
        # If this record was previously created as an inferred reverse edge,
        # a traversal in this direction now promotes it to direct evidence.
        forward.pop("inferred_reverse", None)
        # A migration marker can remain on an edge that was later observed
        # directly in the current Matrix-aware schema.  Retaining it would
        # permanently hide a valid local route even though direct evidence is
        # now present.
        forward.pop("legacy_unverified", None)
        if a.matrix_id is not None and b.matrix_id is not None:
            forward.pop("legacy_matrix_unverified", None)
        # Normal movement edges are reversible after direct observation of one
        # direction only as a candidate.  Do not assume cross-Zone warp reverses.
        if kind != "zone_transition":
            reverse = one(b, a)
            if not reverse_was_direct and int(reverse.get("direct_observations", 0)) == 0:
                reverse["inferred_reverse"] = True
        return {"from": a.public(), **forward}

    def reset_trace(self) -> None:
        self._last_node = None
        self._last_frame = None
        self._last_session_id = None

    def has_direct_edge(self, start: NavNode, goal: NavNode) -> bool:
        """Return whether this exact direction still has direct observed evidence."""
        self._ensure_loaded()
        start, goal = self._with_matrix_identity(start), self._with_matrix_identity(goal)
        with self._lock:
            edge = (self._edges.get(start.key()) or {}).get(goal.key())
            return self._is_direct_local_edge(edge)

    @staticmethod
    def _is_direct_local_edge(edge: dict[str, Any] | None) -> bool:
        return bool(
            edge
            and edge.get("kind") != "zone_transition"
            # A same-X/Z elevation jump is retained as raw transition
            # evidence, but it has no cardinal input mapping.  The live
            # stair calibration showed that a cached jump of this shape can
            # actually be a multi-tile ramp sampled between frames.  Until
            # a dedicated vertical/rail action contract exists, exposing it
            # as an executable edge would authorize a blind button.
            and edge.get("kind") != "vertical_same_tile"
            and not edge.get("inferred_reverse")
            and not edge.get("legacy_unverified")
            and int(edge.get("direct_observations", 0)) > 0
        )

    def preview_component(
        self,
        zone_id: int,
        *,
        anchor: dict[str, Any] | None = None,
        limit: int = 256,
    ) -> dict[str, Any]:
        """Return one bounded directed component for an offline map preview.

        The start is historical evidence, not a claim about the live player.
        Only the same directly observed edges accepted by the executor are
        exposed, so the frontend can demonstrate a real route without turning
        a synthetic camera anchor into a traversable node.
        """
        self._ensure_loaded()
        limit = max(1, min(int(limit), 2048))
        with self._lock:
            zone_keys = {
                key for key, value in self._nodes.items()
                if value.get("zone_id") == int(zone_id)
            }
            adjacency: dict[str, list[str]] = {}
            for source in sorted(zone_keys):
                targets = [
                    target for target, edge in (self._edges.get(source) or {}).items()
                    if target in zone_keys and self._is_direct_local_edge(edge)
                ]
                if targets:
                    adjacency[source] = sorted(targets)

            requested = {
                key: int(anchor[key])
                for key in ("x", "y", "z")
                if isinstance((anchor or {}).get(key), int)
            }
            if not adjacency:
                return {
                    "format": "black2-navigation-observations/v1",
                    "status": "unresolved",
                    "zone_id": int(zone_id),
                    "anchor": requested or None,
                    "start": None,
                    "nodes": [],
                    "edges": [],
                    "reachable_node_count": 0,
                    "truncated": False,
                    "execution_eligible": False,
                    "reason": "No directly observed local edge is available in this Zone.",
                    "confidence": "unresolved",
                }

            def start_score(key: str) -> tuple[float, str]:
                value = self._nodes[key]
                if {"x", "z"}.issubset(requested):
                    score = abs(int(value["x"]) - requested["x"]) + abs(int(value["z"]) - requested["z"])
                    if "y" in requested:
                        score += 0.1 * abs(int(value["y"]) - requested["y"])
                    return score, key
                newest = max(
                    (int(edge.get("last_frame", 0)) for edge in (self._edges.get(key) or {}).values()
                     if self._is_direct_local_edge(edge)),
                    default=0,
                )
                return -float(newest), key

            start_key = min(adjacency, key=start_score)
            queue = [start_key]
            visited: list[str] = []
            seen = {start_key}
            for source in queue:
                if len(visited) >= limit:
                    break
                visited.append(source)
                for target in adjacency.get(source, []):
                    if target not in seen:
                        seen.add(target)
                        queue.append(target)

            visible = set(visited)
            nodes = [
                {
                    **NavNode(
                        int(self._nodes[key]["zone_id"]), int(self._nodes[key]["x"]),
                        int(self._nodes[key]["y"]), int(self._nodes[key]["z"]),
                        self._nodes[key].get("matrix_id"),
                    ).public(),
                    "observations": int(self._nodes[key].get("observations", 0)),
                }
                for key in visited
            ]
            edges = []
            for source in visited:
                for target in adjacency.get(source, []):
                    if target not in visible:
                        continue
                    edge = self._edges[source][target]
                    edges.append({
                        "from": NavNode(
                            **{k: int(self._nodes[source][k]) for k in ("zone_id", "x", "y", "z")},
                            matrix_id=self._nodes[source].get("matrix_id"),
                        ).public(),
                        "to": NavNode(
                            **{k: int(self._nodes[target][k]) for k in ("zone_id", "x", "y", "z")},
                            matrix_id=self._nodes[target].get("matrix_id"),
                        ).public(),
                        "kind": edge.get("kind"),
                        "direct_observations": int(edge.get("direct_observations", 0)),
                    })
            return {
                "format": "black2-navigation-observations/v1",
                "status": "available",
                "zone_id": int(zone_id),
                "anchor": requested or None,
                "start": nodes[0],
                "nodes": nodes,
                "edges": edges,
                "reachable_node_count": len(nodes),
                "truncated": len(seen) > len(nodes),
                "execution_eligible": False,
                "reason": "Historical directly observed component for read-only route previews.",
                "confidence": "verified_observation_graph",
            }

    def status(self) -> dict[str, Any]:
        self._ensure_loaded()
        edge_count = sum(len(v) for v in self._edges.values())
        zones = sorted({int(v["zone_id"]) for v in self._nodes.values() if isinstance(v.get("zone_id"), int)})
        return {
            "format": "black2-observed-navigation-status/v1",
            "node_count": len(self._nodes),
            "directed_edge_count": edge_count,
            "zones": zones,
            "path": str(self.path),
            "confidence": "verified_observation_graph",
        }

    def tile_evidence(self, node: NavNode) -> dict[str, Any]:
        """Read exact-layer occupation and directed movement evidence, without probing."""
        import copy

        self._ensure_loaded()
        node = self._with_matrix_identity(node)
        with self._lock:
            entry = self._nodes.get(node.key())
            outgoing = self._edges.get(node.key(), {})
            direct = [
                copy.deepcopy(edge) for edge in outgoing.values()
                if not edge.get("inferred_reverse") and not edge.get("legacy_unverified")
                and edge.get("kind") != "zone_transition"
                and edge.get("kind") != "vertical_same_tile"
                and int(edge.get("direct_observations", 0)) > 0
            ]
            return {
                "occupied_observations": (entry or {}).get("observations", 0),
                "last_world_sample": copy.deepcopy(((entry or {}).get("world_samples") or [None])[-1]),
                "outgoing_direct_edges": direct,
                "scope": "historical exact Zone/X/Y/Z; no guarantee against current actors or story flags",
            }

    @staticmethod
    def _heuristic(a: NavNode, b: NavNode) -> float:
        # Every traversable edge costs at least 0.8 after observation bonus.
        return 0.8 * (abs(a.x - b.x) + abs(a.z - b.z))

    def nearest_known_node(self, zone_id: int, x: int, z: int, y: int | None = None, max_radius: int = 2) -> NavNode | None:
        self._ensure_loaded()
        resolved_matrix = self._with_matrix_identity(NavNode(zone_id, x, y or 0, z)).matrix_id
        candidates: list[tuple[float, NavNode]] = []
        for value in self._nodes.values():
            if value.get("zone_id") != zone_id:
                continue
            if resolved_matrix is not None and value.get("matrix_id") != resolved_matrix:
                continue
            node = NavNode(zone_id, int(value["x"]), int(value["y"]), int(value["z"]), value.get("matrix_id"))
            dxz = abs(node.x - x) + abs(node.z - z)
            if dxz > max_radius:
                continue
            score = dxz + (0.1 * abs(node.y - y) if y is not None else 0.0)
            candidates.append((score, node))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def find_path(
        self, start: NavNode, goal: NavNode, *, require_direct_observation: bool = False,
        constraint_evaluator: Any | None = None,
    ) -> dict[str, Any]:
        self._ensure_loaded()
        start, goal = self._with_matrix_identity(start), self._with_matrix_identity(goal)
        if start.zone_id != goal.zone_id:
            return {"reachable": False, "reason": "cross-zone routing requires verified warp edges; not enabled as a general shortcut", "path": [], "confidence": "unresolved"}
        if start.key() not in self._nodes or goal.key() not in self._nodes:
            return {"reachable": False, "reason": "start or goal has not been observed on this elevation layer", "path": [], "confidence": "unresolved"}
        queue: list[tuple[float, float, str]] = [(self._heuristic(start, goal), 0.0, start.key())]
        costs = {start.key(): 0.0}
        constraint_costs = {start.key(): 0.0}
        parent: dict[str, str] = {}
        encountered: dict[str, dict[str, Any]] = {}
        blocked_constraints: dict[str, dict[str, Any]] = {}
        while queue:
            _f, g, key = heappop(queue)
            if key == goal.key():
                break
            if g != costs.get(key):
                continue
            for dest, edge in (self._edges.get(key) or {}).items():
                if dest not in self._nodes:
                    continue
                if edge.get("kind") in {"zone_transition", "vertical_same_tile"}:
                    continue
                if require_direct_observation and edge.get("inferred_reverse"):
                    continue
                if require_direct_observation and (
                    edge.get("legacy_unverified") or int(edge.get("direct_observations", 0)) <= 0
                ):
                    continue
                # Repeated direct observations are slightly preferred.
                obs = int(edge.get("observations", 1))
                step_cost = 1.0 + (0.15 if edge.get("kind") == "step_with_elevation_change" else 0.0) - min(0.2, obs * 0.02)
                d = self._nodes[dest]
                node = NavNode(
                    int(d["zone_id"]), int(d["x"]), int(d["y"]), int(d["z"]), d.get("matrix_id"),
                )
                evaluation = (
                    constraint_evaluator.evaluate(node, is_goal=node == goal)
                    if constraint_evaluator is not None
                    else {"blocked": False, "extra_cost": 0.0, "constraints": []}
                )
                for item in evaluation.get("constraints") or ():
                    encountered[str(item.get("constraint_id"))] = item
                if evaluation.get("blocked"):
                    for item in evaluation.get("blocking_constraints") or ():
                        blocked_constraints[str(item.get("constraint_id"))] = item
                    continue
                extra_cost = float(evaluation.get("extra_cost", 0.0) or 0.0)
                ng = g + step_cost + extra_cost
                if ng >= costs.get(dest, float("inf")):
                    continue
                costs[dest] = ng
                constraint_costs[dest] = constraint_costs.get(key, 0.0) + extra_cost
                parent[dest] = key
                heappush(queue, (ng + self._heuristic(node, goal), ng, dest))
        if goal.key() not in costs:
            return {
                "reachable": False,
                "reason": "navigation policy blocked all observed paths" if blocked_constraints else "no connected observed path on this layer",
                "policy_blocked": bool(blocked_constraints),
                "blocking_constraints": list(blocked_constraints.values()),
                "constraints_encountered": list(encountered.values()),
                "path": [], "confidence": "verified_observation_graph",
            }
        keys = [goal.key()]
        while keys[-1] != start.key():
            keys.append(parent[keys[-1]])
        keys.reverse()
        path = [dict(self._nodes[k], world_samples=(self._nodes[k].get("world_samples") or [])[-2:]) for k in keys]
        return {
            "reachable": True, "path": path, "steps": max(0, len(path) - 1),
            "cost": costs[goal.key()], "constraint_cost": constraint_costs.get(goal.key(), 0.0),
            "constraints_encountered": list(encountered.values()),
            "confidence": "verified_observation_graph",
            "reason": "all route edges come from observed PlayerRuntime movement",
        }


observed_navigation_graph = ObservedNavigationGraph()
