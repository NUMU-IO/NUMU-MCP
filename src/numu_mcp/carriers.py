"""Carrier list, fetched from the API's registry rather than hardcoded.

``SHIPMENT_CARRIERS`` in ``formatting.py`` was a private copy of the
platform's carrier list — a fourth hardcoded surface alongside the API's
shipments route, its settings route and the merchant hub. Copies drift:
J&T was creatable through the API but missing from its settings route for
exactly this reason.

The API now exposes its registry at ``GET shipments/carriers``. This
module reads that, caches it for the process, and falls back to the
static tuple if the call fails — an MCP tool must not stop working
because a catalog lookup timed out.

Deliberately conservative: this service has **no test suite of its own
before this change and no CD rollback** (a bad deploy is ~15 minutes of
502s), so the fetch is best-effort and never raises into a tool.
"""

from __future__ import annotations

import time
from typing import Any

from .formatting import SHIPMENT_CARRIERS

#: Cache the catalog for the process. Carriers change on deploys, not
#: minute to minute, so a long TTL is fine and keeps tool calls fast.
_TTL_SECONDS = 900

_cache: dict[str, Any] = {"carriers": None, "fetched_at": 0.0}


def _fresh() -> bool:
    return (
        _cache["carriers"] is not None
        and (time.monotonic() - _cache["fetched_at"]) < _TTL_SECONDS
    )


async def known_carriers() -> tuple[str, ...]:
    """Carrier slugs the API accepts, falling back to the static tuple.

    Never raises: a failed lookup returns the fallback so tools keep
    working.
    """
    if _fresh():
        return _cache["carriers"]

    try:
        from .runtime import get_client

        payload = await get_client().get("shipments/carriers")
        data = payload.get("data") if isinstance(payload, dict) else payload
        slugs = tuple(
            c["slug"] for c in (data or []) if isinstance(c, dict) and c.get("slug")
        )
        if slugs:
            _cache["carriers"] = slugs
            _cache["fetched_at"] = time.monotonic()
            return slugs
    except Exception:
        # Best-effort by design — see the module docstring.
        pass

    return SHIPMENT_CARRIERS


async def carrier_capabilities(slug: str) -> dict[str, bool]:
    """Declared capabilities for one carrier, or ``{}`` if unknown.

    Lets a tool explain *why* an action is unavailable instead of
    surfacing a bare 501 from the API.
    """
    try:
        from .runtime import get_client

        payload = await get_client().get("shipments/carriers")
        data = payload.get("data") if isinstance(payload, dict) else payload
        for entry in data or []:
            if isinstance(entry, dict) and entry.get("slug") == slug:
                caps = entry.get("capabilities")
                return caps if isinstance(caps, dict) else {}
    except Exception:
        pass
    return {}


def reset_cache() -> None:
    """Clear the cached catalog. For tests and after a deploy."""
    _cache["carriers"] = None
    _cache["fetched_at"] = 0.0


__all__ = ["carrier_capabilities", "known_carriers", "reset_cache"]
