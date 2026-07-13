"""Orientation tools — the first call an AI should make in any session."""

from ..client import get_key_info, store_request
from ..server import mcp

READ_ONLY = {"readOnlyHint": True}


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "Get a one-call orientation snapshot of the connected NUMU store: identity, plan, "
        "catalog/order counts and currency. Call this FIRST in a session to learn which store "
        "you are operating on and what this API key is allowed to do (scopes)."
    ),
)
async def get_store_snapshot() -> dict:
    info = await get_key_info()
    snapshot: dict = {
        "store_id": info.get("store_id"),
        "store_name": info.get("store_name"),
        "subdomain": info.get("subdomain"),
        "key_scopes": info.get("scopes"),
        "currency": info.get("currency", "EGP"),
    }
    try:
        dashboard = await store_request("GET", "/dashboard/summary")
        snapshot["summary"] = dashboard
    except Exception:
        # Dashboard summary is a nice-to-have; identity + scopes are the contract.
        snapshot["summary"] = None
    return snapshot
