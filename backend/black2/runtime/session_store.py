"""Unified SQLite session, event, and command store for Pokémon Black 2."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4


class SessionStore:
    """Thread-safe SQLite database for sessions, events, commands, and snapshots."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._active_session_id: str | None = None

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=2.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            if self.db_path != ":memory:":
                cur.execute("PRAGMA journal_mode = WAL;")
                cur.execute("PRAGMA synchronous = NORMAL;")
                cur.execute("PRAGMA busy_timeout = 2000;")
            cur.execute("PRAGMA foreign_keys = ON;")

            cur.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                ended_at REAL,
                rom_hash TEXT,
                rom_code TEXT,
                app_version TEXT,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                session_id TEXT,
                frame INTEGER,
                type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                summary TEXT NOT NULL,
                observed_at REAL NOT NULL,
                state_revision INTEGER NOT NULL DEFAULT 0,
                task_id TEXT,
                plan_id TEXT,
                correlation_id TEXT,
                data_json TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_session_seq ON events(session_id, seq);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
            CREATE INDEX IF NOT EXISTS idx_events_frame ON events(frame);
            CREATE INDEX IF NOT EXISTS idx_events_observed ON events(observed_at);

            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                session_id TEXT,
                seq INTEGER,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                correlation_id TEXT,
                parameters_json TEXT,
                result_json TEXT,
                error_json TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);
            CREATE INDEX IF NOT EXISTS idx_commands_session ON commands(session_id);

            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                session_id TEXT,
                frame INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                zone_id INTEGER,
                player_x INTEGER,
                player_y INTEGER,
                player_z INTEGER,
                mode TEXT,
                payload_json TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_frame ON snapshots(frame);
            CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id);
            """)
            self._conn.commit()

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    def start_session(
        self,
        session_id: str | None = None,
        rom_hash: str | None = None,
        rom_code: str | None = None,
        app_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        sid = session_id or f"session_{uuid4().hex[:12]}"
        now = time.time()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, started_at, rom_hash, rom_code, app_version, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    started_at = excluded.started_at,
                    rom_hash = coalesce(excluded.rom_hash, sessions.rom_hash),
                    rom_code = coalesce(excluded.rom_code, sessions.rom_code),
                    app_version = coalesce(excluded.app_version, sessions.app_version),
                    metadata_json = coalesce(excluded.metadata_json, sessions.metadata_json)
                """,
                (sid, now, rom_hash, rom_code, app_version, meta_json),
            )
            self._conn.commit()
            self._active_session_id = sid
        return sid

    def end_session(self, session_id: str | None = None) -> None:
        sid = session_id or self._active_session_id
        if not sid:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (now, sid),
            )
            self._conn.commit()
            if self._active_session_id == sid:
                self._active_session_id = None

    def record_event(
        self,
        event: Any,
        session_id: str | None = None,
    ) -> int:
        if isinstance(event, dict):
            event_id = event.get("event_id") or str(uuid4())
            event_type = event.get("type", "unknown")
            severity = event.get("severity") or "info"
            summary = event.get("summary") or ""
            observed_at = event.get("observed_at", time.time())
            frame = event.get("frame")
            state_revision = event.get("state_revision", 0)
            task_id = event.get("task_id")
            plan_id = event.get("plan_id")
            correlation_id = event.get("correlation_id")
            data = event.get("data")
            sid = session_id or event.get("session_id") or self._active_session_id
        else:
            event_id = getattr(event, "event_id", None) or str(uuid4())
            event_type = getattr(event, "type", "unknown")
            severity = getattr(event, "severity", "info") or "info"
            summary = getattr(event, "summary", "") or ""
            observed_at = getattr(event, "observed_at", time.time())
            frame = getattr(event, "frame", None)
            state_revision = getattr(event, "state_revision", 0)
            task_id = getattr(event, "task_id", None)
            plan_id = getattr(event, "plan_id", None)
            correlation_id = getattr(event, "correlation_id", None)
            data = getattr(event, "data", None)
            sid = session_id or getattr(event, "session_id", None) or self._active_session_id

        data_json = json.dumps(data, ensure_ascii=False) if data else None

        with self._lock:
            try:
                if sid:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO sessions (session_id, started_at) VALUES (?, ?)",
                        (sid, time.time()),
                    )
                cur = self._conn.execute(
                    """
                    INSERT INTO events (
                        event_id, session_id, frame, type, severity, summary,
                        observed_at, state_revision, task_id, plan_id, correlation_id, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, sid, frame, event_type, severity, summary,
                        observed_at, state_revision, task_id, plan_id, correlation_id, data_json,
                    ),
                )
                self._conn.commit()
                return cur.lastrowid
            except Exception:
                self._conn.rollback()
                raise

    def get_events(
        self,
        since_seq: int = 0,
        limit: int = 100,
        session_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = ["SELECT * FROM events WHERE seq > ?"]
        params: list[Any] = [since_seq]

        if session_id:
            query.append("AND session_id = ?")
            params.append(session_id)
        if event_type:
            query.append("AND type = ?")
            params.append(event_type)

        query.append("ORDER BY seq ASC LIMIT ?")
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(" ".join(query), params).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            if item.get("data_json"):
                try:
                    item["data"] = json.loads(item["data_json"])
                except (ValueError, TypeError):
                    item["data"] = None
            else:
                item["data"] = None
            del item["data_json"]
            results.append(item)
        return results

    def record_command(
        self,
        command_id: str,
        command_type: str,
        parameters: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or self._active_session_id
        now = time.time()
        param_json = json.dumps(parameters, ensure_ascii=False) if parameters else None

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO commands (command_id, session_id, type, status, created_at, correlation_id, parameters_json)
                VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (command_id, sid, command_type, now, correlation_id, param_json),
            )
            self._conn.commit()
        return command_id

    def update_command(
        self,
        command_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | str | None = None,
    ) -> None:
        now = time.time()
        result_json = json.dumps(result, ensure_ascii=False) if result else None
        if isinstance(error, str):
            error_json = json.dumps({"detail": error}, ensure_ascii=False)
        elif error is not None:
            error_json = json.dumps(error, ensure_ascii=False)
        else:
            error_json = None

        with self._lock:
            if status == "running":
                self._conn.execute(
                    "UPDATE commands SET status = ?, started_at = ? WHERE command_id = ?",
                    (status, now, command_id),
                )
            elif status in ("completed", "failed", "cancelled"):
                self._conn.execute(
                    """
                    UPDATE commands
                    SET status = ?, completed_at = ?, result_json = coalesce(?, result_json), error_json = ?
                    WHERE command_id = ?
                    """,
                    (status, now, result_json, error_json, command_id),
                )
            else:
                self._conn.execute(
                    "UPDATE commands SET status = ? WHERE command_id = ?",
                    (status, command_id),
                )
            self._conn.commit()

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if not row:
            return None
        res = dict(row)
        for k in ("parameters_json", "result_json", "error_json"):
            field = k.replace("_json", "")
            if res.get(k):
                try:
                    res[field] = json.loads(res[k])
                except (ValueError, TypeError):
                    res[field] = None
            else:
                res[field] = None
            del res[k]
        return res

    def record_snapshot(
        self,
        frame: int,
        zone_id: int | None = None,
        position: dict[str, Any] | tuple[int, int, int] | None = None,
        mode: str | None = None,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        snapshot_id = f"snap_{uuid4().hex[:12]}"
        sid = session_id or self._active_session_id
        now = time.time()
        px = py = pz = None
        if isinstance(position, dict):
            px, py, pz = position.get("x"), position.get("y"), position.get("z")
        elif isinstance(position, (tuple, list)) and len(position) >= 3:
            px, py, pz = position[0], position[1], position[2]

        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, session_id, frame, timestamp, zone_id,
                    player_x, player_y, player_z, mode, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, sid, frame, now, zone_id, px, py, pz, mode, payload_json),
            )
            self._conn.commit()
        return snapshot_id

    def get_snapshots(
        self,
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = ["SELECT * FROM snapshots"]
        params: list[Any] = []
        if session_id:
            query.append("WHERE session_id = ?")
            params.append(session_id)
        query.append("ORDER BY frame DESC LIMIT ?")
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(" ".join(query), params).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            if item.get("payload_json"):
                try:
                    item["payload"] = json.loads(item["payload_json"])
                except (ValueError, TypeError):
                    item["payload"] = None
            else:
                item["payload"] = None
            del item["payload_json"]
            results.append(item)
        return results

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            sessions_count = self._conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
            events_count = self._conn.execute("SELECT count(*) FROM events").fetchone()[0]
            commands_count = self._conn.execute("SELECT count(*) FROM commands").fetchone()[0]
            snapshots_count = self._conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]

        file_size = 0
        if self.db_path != ":memory:" and Path(self.db_path).is_file():
            file_size = Path(self.db_path).stat().st_size

        return {
            "sessions": sessions_count,
            "events": events_count,
            "commands": commands_count,
            "snapshots": snapshots_count,
            "file_size_bytes": file_size,
            "active_session": self._active_session_id,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
