"""MCP resources — read-only context the AI loads to understand the store.

Resources are addressed by URI:
  * ``numu://store/overview``    — store profile, plan, counts, money settings
  * ``numu://products/catalog``  — full catalog with stock levels & status
"""

from __future__ import annotations

import json
from typing import Any

from .app import mcp
from .client import NumuApiError
from .formatting import money
from .runtime import get_client, get_settings


async def _safe(coro: Any) -> Any:
    """Await a client call, converting API errors into an inline marker.

    Keeps a resource useful even if one underlying endpoint (e.g. plan usage)
    is unavailable for this plan/permission set.
    """
    try:
        return await coro
    except NumuApiError as exc:
        return {"unavailable": str(exc)}


def _total_from_list(payload: Any) -> int | None:
    if isinstance(payload, dict):
        if "total" in payload:
            return payload.get("total")
        if "pagination" in payload and isinstance(payload["pagination"], dict):
            return payload["pagination"].get("total")
    return None


@mcp.resource(
    "numu://store/overview",
    name="Store overview",
    description=(
        "A snapshot of the store: name, status, plan & usage, currency, enabled "
        "payment methods, product/order/customer counts, and inventory health. "
        "Read this first to ground any merchant task."
    ),
    mime_type="application/json",
)
async def store_overview() -> str:
    client = get_client()
    settings = get_settings()

    store = await _safe(
        client.request(
            "GET", f"stores/{settings.store_id}", store_scoped=False, cache_ttl=120
        )
    )
    currency = "EGP"
    if isinstance(store, dict):
        currency = store.get("default_currency") or "EGP"

    plan = await _safe(client.get("plan/usage"))
    payment = await _safe(client.get("settings/payment"))
    inventory = await _safe(client.get("inventory/stats"))
    orders_page = await _safe(client.get("orders", params={"page": 1, "limit": 1}))
    customers_page = await _safe(
        client.get("customers", params={"page": 1, "limit": 1})
    )

    overview: dict[str, Any] = {
        "store": {
            "id": store.get("id") if isinstance(store, dict) else None,
            "name": store.get("name") if isinstance(store, dict) else None,
            "status": store.get("status") if isinstance(store, dict) else None,
            "subdomain": store.get("subdomain") if isinstance(store, dict) else None,
            "store_url": store.get("store_url") if isinstance(store, dict) else None,
            "country": store.get("country") if isinstance(store, dict) else None,
            "default_currency": currency,
            "default_language": (
                store.get("default_language") if isinstance(store, dict) else None
            ),
            "contact_email": (
                store.get("contact_email") if isinstance(store, dict) else None
            ),
        }
        if isinstance(store, dict)
        else store,
        "plan": plan,
        "payment_methods": payment,
        "counts": {
            "products": (
                inventory.get("total_products")
                if isinstance(inventory, dict)
                else None
            ),
            "orders": _total_from_list(orders_page),
            "customers": _total_from_list(customers_page),
        },
        "inventory_health": {
            "low_stock": (
                inventory.get("low_stock_count")
                if isinstance(inventory, dict)
                else None
            ),
            "out_of_stock": (
                inventory.get("out_of_stock_count")
                if isinstance(inventory, dict)
                else None
            ),
            "total_inventory_value": (
                money(inventory.get("total_inventory_value"), currency)
                if isinstance(inventory, dict)
                and inventory.get("total_inventory_value") is not None
                else None
            ),
        },
    }
    return json.dumps(overview, indent=2, ensure_ascii=False, default=str)


@mcp.resource(
    "numu://products/catalog",
    name="Product catalog",
    description=(
        "The store's product catalog (first 100 products) with stock levels, "
        "price, category, SKU, and active/inactive status. Use the product "
        "tools for full pagination, search, or edits."
    ),
    mime_type="application/json",
)
async def product_catalog() -> str:
    client = get_client()
    settings = get_settings()

    store = await _safe(
        client.request(
            "GET", f"stores/{settings.store_id}", store_scoped=False, cache_ttl=120
        )
    )
    currency = (
        store.get("default_currency", "EGP") if isinstance(store, dict) else "EGP"
    )

    page = await client.get(
        "products", params={"page": 1, "limit": 100, "sort_by": "name"}
    )
    items = page.get("items", []) if isinstance(page, dict) else []
    total = _total_from_list(page)

    products = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "sku": p.get("sku"),
            "status": p.get("status"),
            "price": money(_price_to_minor(p.get("price"), currency), currency),
            "quantity": p.get("quantity"),
            "in_stock": p.get("is_in_stock"),
            "low_stock": p.get("is_low_stock"),
            "category_id": p.get("category_id"),
        }
        for p in items
        if isinstance(p, dict)
    ]

    return json.dumps(
        {
            "total_products": total,
            "showing": len(products),
            "currency": currency,
            "products": products,
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    )


@mcp.resource(
    "numu://store/capabilities",
    name="Store capabilities",
    description=(
        "What this store/connection can do right now: active plan, usage vs. "
        "limits, enabled plan features, the connected user's permission domains, "
        "and a per-tool availability map (with reasons when blocked). Read this "
        "to decide whether an action is permitted before attempting it."
    ),
    mime_type="application/json",
)
async def store_capabilities() -> str:
    from .capabilities import TOOL_REQUIREMENTS, probe

    caps = await probe.get()
    tools = {}
    for name in sorted(TOOL_REQUIREMENTS):
        allowed, reason = caps.tool_allowed(name)
        tools[name] = {"available": allowed, **({"reason": reason} if reason else {})}
    return json.dumps(
        {
            "plan": caps.plan,
            "plan_display_name": caps.plan_display_name,
            "is_owner": caps.is_owner,
            "features": caps.features,
            "usage": caps.usage,
            "permission_domains": sorted(caps.permission_domains),
            "tools": tools,
            "notes": caps.notes,
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    )


@mcp.resource(
    "numu://system/health",
    name="System health",
    description=(
        "Health of the MCP server and its link to the NUMU API: config summary, "
        "API reachability + latency, token validity, and the audit DB status. "
        "Read this to diagnose connectivity before blaming a tool."
    ),
    mime_type="application/json",
)
async def system_health() -> str:
    import time

    client = get_client()
    settings = get_settings()
    health: dict[str, Any] = {
        "status": "unknown",
        "config": {
            "base_url": settings.base_url,
            "store_id": str(settings.store_id),
            "transport": settings.transport,
        },
        "api": "unknown",
        "token": "unknown",
    }
    try:
        start = time.monotonic()
        # plan/usage is a cheap, store-scoped, auth-required call — a good probe.
        await client.get("plan/usage")
        health["api"] = "healthy"
        health["api_latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        health["token"] = "valid"
        health["status"] = "healthy"
    except NumuApiError as exc:
        health["api"] = "unreachable_or_error"
        health["error"] = str(exc)
        health["status"] = "degraded"
        if "authentication failed" in str(exc).lower():
            health["token"] = "invalid"
    return json.dumps(health, indent=2, ensure_ascii=False, default=str)


@mcp.resource(
    "numu://audit/recent",
    name="Recent AI actions",
    description=(
        "The audit trail of changes the AI has made to this store (most recent "
        "first): tool, arguments, outcome, and whether each is still undoable. "
        "Answers 'what did the AI change?'."
    ),
    mime_type="application/json",
)
async def recent_audit() -> str:
    from .audit import get_recent_actions

    settings = get_settings()
    actions = await get_recent_actions(str(settings.store_id), limit=25)
    return json.dumps({"actions": actions}, indent=2, ensure_ascii=False, default=str)


def _price_to_minor(price: Any, currency: str) -> int | None:
    """Product list prices come back as a decimal string in major units.

    Convert to minor units so the shared ``money`` formatter can render them
    consistently with the rest of the server.
    """
    if price is None:
        return None
    try:
        from .formatting import major_to_minor

        return major_to_minor(float(price), currency)
    except (ValueError, TypeError):
        return None
