"""Business-intelligence tools — turn raw store data into decisions.

Pure-Python analytics (no external AI/ML). Each tool fetches the real data,
runs a function from ``numu_mcp.intelligence``, and returns a compact, readable
result. Velocity-based tools source sales velocity from the analytics
``top-products`` endpoint and degrade gracefully if analytics isn't on the plan.
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..client import NumuApiError
from ..formatting import money
from ..intelligence import (
    classify_inventory_health,
    compute_rfm_segments,
    velocity_from_top_products,
)
from ..intelligence import suggest_price_adjustments as _suggest_prices
from ..runtime import get_client
from ._base import dumps, err, fetch_all, items_of


async def _velocity(days: int = 30) -> dict[str, float]:
    """Per-product units/day from analytics top-products; {} if unavailable."""
    try:
        top = await get_client().get(
            "analytics/top-products", params={"days": days, "limit": 20}
        )
        return velocity_from_top_products(items_of(top), days)
    except NumuApiError:
        return {}


@mcp.tool()
async def analyze_customer_segments() -> str:
    """Segment customers with RFM (Recency, Frequency, Monetary) analysis.

    Returns groups like champions, loyal, at-risk, about-to-sleep and lost, each
    with a count, total revenue and the top customers — so the merchant knows who
    to reward and who to win back. Pure statistics; no external service."""
    try:
        client = get_client()
        orders = await fetch_all(client, "orders", max_items=2000)
        if not orders:
            return dumps({"message": "No orders yet — nothing to segment.", "segments": {}})

        currency = "EGP"
        for o in orders:
            if isinstance(o, dict) and o.get("currency"):
                currency = o["currency"]
                break

        result = compute_rfm_segments(orders)
        segments = result.get("segments", {})
        summary: dict[str, Any] = {}
        for name, members in segments.items():
            summary[name] = {
                "count": len(members),
                "total_value": money(
                    sum(m["monetary_cents"] for m in members), currency
                ),
                "top": [
                    {
                        "name": m["name"],
                        "customer_id": m["customer_id"],
                        "orders": m["frequency"],
                        "spent": money(m["monetary_cents"], currency),
                        "last_order_days_ago": m["recency_days"],
                    }
                    for m in members[:5]
                ],
            }
        return dumps(
            {"customers_scored": result.get("customers_scored", 0), "segments": summary}
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "analyze_customer_segments"})


@mcp.tool()
async def analyze_inventory_health() -> str:
    """Classify every product as critical / low / healthy / overstock using stock
    level cross-referenced with 30-day sales velocity, including estimated days
    of cover. Highlights what will stock out soon and what's dead weight."""
    try:
        client = get_client()
        inventory = await fetch_all(
            client, "inventory", max_items=1000, params={"filter": "all"}
        )
        velocity = await _velocity(30)
        health = classify_inventory_health(inventory, velocity)
        return dumps(
            {
                "summary": {k: len(v) for k, v in health.items()},
                "velocity_source": "analytics (30d)" if velocity else "unavailable",
                "critical": health["critical"][:25],
                "low": health["low"][:25],
                "overstock": health["overstock"][:25],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "analyze_inventory_health"})


@mcp.tool()
async def suggest_price_adjustments() -> str:
    """Suggest data-driven price changes from 30-day velocity and stock levels:
    raise prices on fast-moving low-stock items, mark down dead stock. Advisory
    only — it never changes prices; use update_product to apply a suggestion."""
    try:
        client = get_client()
        products = await fetch_all(client, "products", max_items=500)
        velocity = await _velocity(30)
        suggestions = _suggest_prices(products, velocity)
        return dumps(
            {
                "count": len(suggestions),
                "velocity_source": "analytics (30d)" if velocity else "unavailable",
                "suggestions": suggestions,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "suggest_price_adjustments"})
