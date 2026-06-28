"""Shared helpers for tool modules."""

from __future__ import annotations

import json
from typing import Any

from ..client import NumuApiError
from ..formatting import ValidationError


def err(exc: Exception, context: dict[str, Any] | None = None) -> str:
    """Render any tool failure as a clean, model-readable message.

    Appends targeted recovery hints based on the error so the model can
    self-correct (wrong id, expired token, plan limit, timeout, …) instead of
    just failing. ``context`` (e.g. the tool name and id) is echoed back to help
    the model retry correctly.
    """
    if isinstance(exc, ValidationError):
        base = f"Invalid input: {exc}"
    elif isinstance(exc, NumuApiError):
        base = f"NUMU API error: {exc}"
    else:
        base = f"Unexpected error: {exc}"

    lower = str(exc).lower()
    hints: list[str] = []
    if "not found" in lower or "404" in lower:
        hints.append(
            "Hint: double-check the id — use the matching list/search tool to "
            "find a valid one."
        )
    if "authentication failed" in lower or "401" in lower or "token" in lower:
        hints.append(
            "Hint: the access token may be expired or revoked — the merchant can "
            "mint a new one in the dashboard."
        )
    if "not allowed" in lower or "permission" in lower or "403" in lower:
        hints.append(
            "Hint: this is a permissions/plan block — call get_capabilities to "
            "see what's available."
        )
    if "did not respond" in lower or "timeout" in lower or "could not reach" in lower:
        hints.append("Hint: the API was slow/unreachable — retry in a few seconds.")
    if "uuid" in lower:
        hints.append("Hint: the id must be a valid UUID.")

    if context:
        hints.append(f"Context: {json.dumps(context, default=str)}")

    return base + ("\n" + "\n".join(hints) if hints else "")


def record_mutation(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    status: str = "success",
    summary: str = "",
    undo: dict[str, Any] | None = None,
) -> None:
    """Write one mutation to the audit log (best-effort; never raises)."""
    from ..audit import log_action

    try:
        from ..runtime import get_settings

        store_id = str(get_settings().store_id)
    except Exception:  # noqa: BLE001
        store_id = "unknown"
    log_action(
        store_id=store_id,
        tool_name=tool_name,
        arguments=arguments,
        result_status=status,
        result_summary=summary,
        undo_payload=undo,
    )


def dumps(obj: Any) -> str:
    """JSON-encode a result for the model (stable, human-readable)."""
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def page_meta(payload: Any) -> dict[str, Any]:
    """Extract pagination fields from a list payload, whatever the shape."""
    if not isinstance(payload, dict):
        return {}
    if "pagination" in payload and isinstance(payload["pagination"], dict):
        p = payload["pagination"]
    else:
        p = payload
    return {
        "total": p.get("total"),
        "page": p.get("page"),
        "page_size": p.get("page_size"),
        "total_pages": p.get("total_pages"),
    }


def items_of(payload: Any) -> list[Any]:
    """Return the list of items from a paginated or list payload."""
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return payload["items"]
        if isinstance(payload.get("data"), list):
            return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


async def fetch_all(
    client: Any,
    path: str,
    *,
    max_items: int = 500,
    page_size: int = 100,
    params: dict[str, Any] | None = None,
) -> list[Any]:
    """Page through a list endpoint up to ``max_items`` (bounded, never infinite).

    Used by the intelligence tools, which need many rows to compute statistics.
    """
    out: list[Any] = []
    page = 1
    while len(out) < max_items:
        p = dict(params or {})
        p.update({"page": page, "limit": min(page_size, max_items - len(out))})
        payload = await client.get(path, params=p)
        items = items_of(payload)
        out.extend(items)
        meta = page_meta(payload)
        total_pages = meta.get("total_pages")
        if not items or (total_pages and page >= total_pages):
            break
        page += 1
    return out[:max_items]
