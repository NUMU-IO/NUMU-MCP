"""Store/meta tools: capabilities introspection, plan & usage, categories.

These make the server self-describing — the AI can ask what it's allowed to do
(given the plan and the connected user's permissions) before attempting work.
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..capabilities import TOOL_REQUIREMENTS, probe
from ..runtime import get_client
from ._base import dumps, err


def _capability_view(caps: Any) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name in sorted(TOOL_REQUIREMENTS):
        allowed, reason = caps.tool_allowed(name)
        tools[name] = {"available": allowed, **({"reason": reason} if reason else {})}

    def _usage_block(key: str) -> dict[str, Any]:
        block = dict(caps.usage.get(key, {}) or {})
        used, limit = block.get("used"), block.get("limit")
        if (
            not block.get("unlimited")
            and isinstance(used, int)
            and isinstance(limit, int)
            and limit > 0
        ):
            block["remaining"] = max(0, limit - used)
            block["percent_used"] = round(used / limit * 100, 1)
        return block

    return {
        "plan": caps.plan,
        "plan_display_name": caps.plan_display_name,
        "is_owner": caps.is_owner,
        "features": caps.features,
        "usage": {
            "products": _usage_block("products"),
            "orders_this_month": _usage_block("orders_this_month"),
        },
        "permission_domains": sorted(caps.permission_domains),
        "tools": tools,
        "notes": caps.notes,
    }


@mcp.tool()
async def get_capabilities() -> str:
    """Describe what this store/connection can currently do.

    Returns the active plan, usage vs. limits (with remaining and % used),
    enabled plan features, the connected user's permission domains, and a
    per-tool availability map (with a reason when a tool is blocked). Call this
    first when unsure whether an action is permitted on this plan."""
    try:
        caps = await probe.get()
        return dumps(_capability_view(caps))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def refresh_capabilities() -> str:
    """Force-refresh the cached plan/permission snapshot, then return it.

    Use after the merchant changes plan or staff permissions mid-conversation."""
    try:
        probe.invalidate()
        caps = await probe.get(force=True)
        return dumps(_capability_view(caps))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def get_plan_and_usage() -> str:
    """Get the merchant's current plan with live resource usage vs. limits
    (products, monthly orders) and which plan features are enabled."""
    try:
        usage = await get_client().get("plan/usage")
        return dumps(usage)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def list_categories(include_inactive: bool = False) -> str:
    """List the store's product categories (useful when creating/updating
    products and assigning a category_id).

    Args:
        include_inactive: Include inactive categories too.
    """
    try:
        data = await get_client().get(
            "categories", params={"include_inactive": include_inactive}
        )
        items = data if isinstance(data, list) else []
        categories = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "slug": c.get("slug"),
                "parent_id": c.get("parent_id"),
                "is_active": c.get("is_active"),
            }
            for c in items
            if isinstance(c, dict)
        ]
        return dumps({"count": len(categories), "categories": categories})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
