"""Shared helpers for tool modules."""

from __future__ import annotations

import json
from typing import Any

from ..client import NumuApiError
from ..formatting import ValidationError


def err(exc: Exception) -> str:
    """Render any tool failure as a clean, model-readable message."""
    if isinstance(exc, ValidationError):
        return f"Invalid input: {exc}"
    if isinstance(exc, NumuApiError):
        return f"NUMU API error: {exc}"
    return f"Unexpected error: {exc}"


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
