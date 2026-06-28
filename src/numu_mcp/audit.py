"""Audit log + undo storage — pluggable backend.

Two interchangeable backends, chosen by ``NUMU_MCP_AUDIT_BACKEND``:

  * ``sqlite`` (default) — a local file. Zero-config, no credentials, great for
    a per-merchant/stdio install. State lives on the machine running the MCP.
  * ``api`` — durable, Postgres-backed via the NUMU API
    (``/stores/{id}/audit``). The MCP stays **stateless** (no disk, no DB
    credentials), so it can run as a horizontally-scaled service (e.g. AWS
    Fargate) with the audit trail centralized and visible in the dashboard.

Either way the public async API is identical, so the tools don't care which is
active. Both return audit entries in the same normalized shape:
``{id, timestamp, tool_name, arguments, result_status, result_summary,
undo_payload, is_undone, undoable}``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None


def _backend() -> str:
    try:
        from .runtime import get_settings

        return get_settings().audit_backend
    except Exception:  # noqa: BLE001
        return "sqlite"


# ── SQLite backend (local, sync internals) ────────────────────────────────────

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


def _sqlite_init() -> None:
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_store ON audit_log(store_id)")
        conn.commit()
    finally:
        conn.close()


def _sqlite_row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    undo = row["undo_payload"]
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "tool_name": row["tool_name"],
        "arguments": json.loads(row["arguments"]) if row["arguments"] else None,
        "result_status": row["result_status"],
        "result_summary": row["result_summary"],
        "undo_payload": json.loads(undo) if undo else None,
        "is_undone": bool(row["is_undone"]),
        "undoable": bool(undo) and not row["is_undone"],
    }


def _sqlite_log(
    *, store_id: str, tool_name: str, arguments: dict, result_status: str,
    result_summary: str, undo_payload: dict | None,
) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (id, timestamp, store_id, tool_name, arguments, "
            "result_status, result_summary, undo_payload, is_undone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                str(uuid.uuid4()),
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


def _sqlite_recent(store_id: str, limit: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE store_id = ? ORDER BY timestamp DESC LIMIT ?",
            (store_id, limit),
        ).fetchall()
        return [_sqlite_row_to_entry(r) for r in rows]
    finally:
        conn.close()


def _sqlite_undoable(store_id: str, limit: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE store_id = ? AND undo_payload IS NOT NULL "
            "AND result_status = 'success' AND is_undone = 0 "
            "ORDER BY timestamp DESC LIMIT ?",
            (store_id, limit),
        ).fetchall()
        return [_sqlite_row_to_entry(r) for r in rows]
    finally:
        conn.close()


def _sqlite_mark(audit_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("UPDATE audit_log SET is_undone = 1 WHERE id = ?", (audit_id,))
        conn.commit()
    finally:
        conn.close()


# ── API backend (durable Postgres via NUMU API, async) ────────────────────────

async def _api_log(
    *, tool_name: str, arguments: dict, result_status: str,
    result_summary: str, undo_payload: dict | None,
) -> None:
    from .runtime import get_client

    details: dict[str, Any] = {
        "arguments": arguments,
        "result_status": result_status,
        "result_summary": str(result_summary)[:1000],
        "is_undone": False,
    }
    if undo_payload:
        details["undo_payload"] = undo_payload
    await get_client().post(
        "audit",
        json={
            "action": tool_name,
            "severity": "info" if result_status == "success" else "warning",
            "details": details,
        },
    )


async def _api_recent(limit: int) -> list[dict[str, Any]]:
    from .runtime import get_client

    data = await get_client().get("audit", params={"limit": limit})
    return data if isinstance(data, list) else []


async def _api_undoable(limit: int) -> list[dict[str, Any]]:
    from .runtime import get_client

    data = await get_client().get("audit", params={"limit": limit, "undoable": True})
    return data if isinstance(data, list) else []


async def _api_mark(audit_id: str) -> None:
    from .runtime import get_client

    await get_client().post(f"audit/{audit_id}/mark-undone")


# ── Public facade ─────────────────────────────────────────────────────────────

def init_audit_db() -> None:
    """Prepare storage. SQLite creates its table; API mode is a no-op."""
    if _backend() == "sqlite":
        _sqlite_init()


async def log_action(
    *, store_id: str, tool_name: str, arguments: dict, result_status: str,
    result_summary: str, undo_payload: dict | None = None,
) -> None:
    """Record one mutation (best-effort; never raises into the caller)."""
    try:
        if _backend() == "api":
            await _api_log(
                tool_name=tool_name, arguments=arguments,
                result_status=result_status, result_summary=result_summary,
                undo_payload=undo_payload,
            )
        else:
            _sqlite_log(
                store_id=store_id, tool_name=tool_name, arguments=arguments,
                result_status=result_status, result_summary=result_summary,
                undo_payload=undo_payload,
            )
    except Exception:  # noqa: BLE001 - auditing must never break a tool
        pass


async def get_recent_actions(store_id: str, limit: int = 25) -> list[dict[str, Any]]:
    if _backend() == "api":
        return await _api_recent(limit)
    return _sqlite_recent(store_id, limit)


async def get_undoable_actions(store_id: str, limit: int = 10) -> list[dict[str, Any]]:
    if _backend() == "api":
        return await _api_undoable(limit)
    return _sqlite_undoable(store_id, limit)


async def mark_undone(audit_id: str) -> None:
    try:
        if _backend() == "api":
            await _api_mark(audit_id)
        else:
            _sqlite_mark(audit_id)
    except Exception:  # noqa: BLE001
        pass
