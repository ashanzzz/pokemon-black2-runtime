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
from typing import Any


@dataclass(frozen=True, order=True)
class NavNode:
    zone_id: int
    x: int
    y: int
    z: int

    @classmethod
    def from_player(cls, player: dict[str, Any] | None) -> "NavNode | None":
        if not player or not isinstance(player.get("zone_id"), int):
            return None
        g = player.get("grid") or {}
        if not all(isinstance(g.get(k), int) for k in ("x", "y", "z")):
            return None
        return cls(int(player["zone_id"]), int(g["x"]), int(g["y"]), int(g["z"]))

    def key(self) -> str:
        return f"{self.zone_id}:{self.x}:{self.y}:{self.z}"

    def public(self) -> dict[str, int]:
        return {"zone_id": self.zone_id, "x": self.x, "y": self.y, "z": self.z}


@dataclass
class ObservedNavigationGraph:
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _loaded: bool = False
    _nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _edges: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _last_node: NavNode | None = None
    _last_frame: int | None = None

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
            except (OSError, ValueError):
                self._nodes, self._edges = {}, {}
            self._loaded = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": "black2-observed-navigation/v1",
            "semantics": "edges are learned from actual PlayerRuntime tile/elevation transitions; static permission bytes are not promoted here",
            "nodes": self._nodes,
            "edges": self._edges,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _edge_kind(a: NavNode, b: NavNode) -> str | None:
        if a.zone_id != b.zone_id:
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
        frame = int(player.get("frame") or 0) if player else 0
        if node is None:
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
            if self._last_node is not None and self._last_node != node:
                kind = self._edge_kind(self._last_node, node)
                if kind is not None:
                    edge_record = self._record_edge(self._last_node, node, kind, frame, source)
            self._last_node = node
            self._last_frame = frame
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
        forward = one(a, b)
        # Normal movement edges are reversible after direct observation of one
        # direction only as a candidate.  Do not assume cross-Zone warp reverses.
        if kind != "zone_transition":
            reverse = one(b, a)
            reverse["inferred_reverse"] = True
        return {"from": a.public(), **forward}

    def reset_trace(self) -> None:
        self._last_node = None
        self._last_frame = None

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

    @staticmethod
    def _heuristic(a: NavNode, b: NavNode) -> float:
        return abs(a.x - b.x) + abs(a.z - b.z) + 0.35 * abs(a.y - b.y)

    def nearest_known_node(self, zone_id: int, x: int, z: int, y: int | None = None, max_radius: int = 2) -> NavNode | None:
        self._ensure_loaded()
        candidates: list[tuple[float, NavNode]] = []
        for value in self._nodes.values():
            if value.get("zone_id") != zone_id:
                continue
            node = NavNode(zone_id, int(value["x"]), int(value["y"]), int(value["z"]))
            dxz = abs(node.x - x) + abs(node.z - z)
            if dxz > max_radius:
                continue
            score = dxz + (0.1 * abs(node.y - y) if y is not None else 0.0)
            candidates.append((score, node))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def find_path(self, start: NavNode, goal: NavNode) -> dict[str, Any]:
        self._ensure_loaded()
        if start.zone_id != goal.zone_id:
            return {"reachable": False, "reason": "cross-zone routing requires verified warp edges; not enabled as a general shortcut", "path": [], "confidence": "unresolved"}
        if start.key() not in self._nodes or goal.key() not in self._nodes:
            return {"reachable": False, "reason": "start or goal has not been observed on this elevation layer", "path": [], "confidence": "unresolved"}
        queue: list[tuple[float, float, str]] = [(self._heuristic(start, goal), 0.0, start.key())]
        costs = {start.key(): 0.0}
        parent: dict[str, str] = {}
        while queue:
            _f, g, key = heappop(queue)
            if key == goal.key():
                break
            if g != costs.get(key):
                continue
            for dest, edge in (self._edges.get(key) or {}).items():
                if dest not in self._nodes:
                    continue
                if edge.get("kind") == "zone_transition":
                    continue
                # Repeated direct observations are slightly preferred.
                obs = int(edge.get("observations", 1))
                step_cost = 1.0 + (0.15 if edge.get("kind") == "step_with_elevation_change" else 0.0) - min(0.2, obs * 0.02)
                ng = g + step_cost
                if ng >= costs.get(dest, float("inf")):
                    continue
                costs[dest] = ng
                parent[dest] = key
                d = self._nodes[dest]
                node = NavNode(int(d["zone_id"]), int(d["x"]), int(d["y"]), int(d["z"]))
                heappush(queue, (ng + self._heuristic(node, goal), ng, dest))
        if goal.key() not in costs:
            return {"reachable": False, "reason": "no connected observed path on this layer", "path": [], "confidence": "verified_observation_graph"}
        keys = [goal.key()]
        while keys[-1] != start.key():
            keys.append(parent[keys[-1]])
        keys.reverse()
        path = [dict(self._nodes[k], world_samples=(self._nodes[k].get("world_samples") or [])[-2:]) for k in keys]
        return {"reachable": True, "path": path, "steps": max(0, len(path) - 1), "cost": costs[goal.key()], "confidence": "verified_observation_graph", "reason": "all route edges come from observed PlayerRuntime movement"}


observed_navigation_graph = ObservedNavigationGraph()
