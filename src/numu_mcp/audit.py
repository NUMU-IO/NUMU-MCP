"""Audit log + undo for AI-performed mutations.

Every *mutating* tool records what it did to a local SQLite database: when, the
tool name, arguments, the outcome, a short summary, and — for reversible actions
— an **inverse HTTP request** that exactly undoes it. Reads are not audited (the
point is "what did the AI *change*").

The undo design is deliberately generic: instead of a per-tool dispatch map, a
reversible action stores the literal request that reverses it
(``{method, path, json, store_scoped}``). Undo just replays that request through
the normal client — so it benefits from the same auth, tenant routing and error
handling as everything else, and new reversible tools need no special undo code.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        from .runtime import get_settings

        data_dir = Path(get_settings().resolved_data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "audit.db"
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_audit_db() -> None:
    """Create the audit table/indexes if missing. Called once at startup."""
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                store_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT,
                result_status TEXT NOT NULL,
                result_summary TEXT,
                undo_payload TEXT,
                is_undone INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_store ON audit_log(store_id)"
        )
        conn.commit()
    finally:
        conn.close()


def log_action(
    *,
    store_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    result_status: str,
    result_summary: str,
    undo_payload: dict[str, Any] | None = None,
) -> str:
    """Record one mutation. Returns the audit id. Never raises into the caller."""
    audit_id = str(uuid.uuid4())
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO audit_log (id, timestamp, store_id, tool_name, "
                "arguments, result_status, result_summary, undo_payload, is_undone) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    audit_id,
                    datetime.now(UTC).isoformat(),
                    store_id,
                    tool_name,
                    json.dumps(arguments, default=str)[:4000],
                    result_status,
                    str(result_summary)[:1000],
                    json.dumps(undo_payload, default=str) if undo_payload else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - auditing must never break a tool
        pass
    return audit_id


def get_recent_actions(store_id: str, limit: int = 25) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, tool_name, arguments, result_status, "
            "result_summary, is_undone, (undo_payload IS NOT NULL) AS undoable "
            "FROM audit_log WHERE store_id = ? ORDER BY timestamp DESC LIMIT ?",
            (store_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_undoable_actions(store_id: str, limit: int = 10) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE store_id = ? AND undo_payload IS NOT NULL "
            "AND result_status = 'success' AND is_undone = 0 "
            "ORDER BY timestamp DESC LIMIT ?",
            (store_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_undone(audit_id: str) -> None:
    try:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE audit_log SET is_undone = 1 WHERE id = ?", (audit_id,)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
