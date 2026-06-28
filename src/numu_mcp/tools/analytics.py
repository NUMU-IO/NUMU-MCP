"""Analytics tools: sales report, revenue over time, top products.

All analytics endpoints share a date window. Pass ``days`` for a rolling
window (default 30), or an explicit ``start_date``/``end_date`` pair (ISO-8601,
provided together).
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..capabilities import guard
from ..formatting import ValidationError, money, validate_choice
from ..runtime import get_client
from ._base import dumps, err, items_of


def _window_params(
    days: int | None, start_date: str | None, end_date: str | None
) -> dict[str, Any]:
    if (start_date is None) ^ (end_date is None):
        raise ValidationError(
            "start_date and end_date must be provided together."
        )
    params: dict[str, Any] = {}
    if start_date and end_date:
        params["start_date"] = start_date
        params["end_date"] = end_date
    elif days is not None:
        if days <= 0:
            raise ValidationError("days must be greater than zero.")
        params["days"] = days
    return params


@mcp.tool()
async def sales_report(
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get a sales summary for a period: total sales, order count, average
    order value, and change vs. the previous period.

    Args:
        days: Rolling window length in days (used when no explicit dates given).
        start_date: ISO-8601 start (e.g. 2026-06-01). Must accompany end_date.
        end_date: ISO-8601 end. Must accompany start_date.
    """
    try:
        blocked = await guard("sales_report")
        if blocked:
            return blocked
        params = _window_params(days, start_date, end_date)
        data = await get_client().get("analytics/overview", params=params)
        if isinstance(data, dict):
            currency = data.get("currency", "EGP")
            out = dict(data)
            for f in ("total_sales", "avg_order_value"):
                if data.get(f) is not None:
                    out[f"{f}_formatted"] = money(data[f], currency)
            return dumps(out)
        return dumps(data)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def revenue_over_time(
    days: int = 30,
    granularity: str = "day",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get a revenue time-series (sales and order counts per bucket).

    Args:
        days: Rolling window length in days.
        granularity: Bucket size: 'hour' (max 7 days) or 'day' (max 365 days).
        start_date: ISO-8601 start. Must accompany end_date.
        end_date: ISO-8601 end. Must accompany start_date.
    """
    try:
        blocked = await guard("revenue_over_time")
        if blocked:
            return blocked
        params = _window_params(days, start_date, end_date)
        params["granularity"] = validate_choice(
            granularity, ("hour", "day"), "granularity"
        )
        data = await get_client().get("analytics/sales-chart", params=params)
        points = [
            {
                "date": pt.get("date"),
                "sales": money(pt.get("sales"), "EGP"),
                "orders": pt.get("orders"),
            }
            for pt in items_of(data)
            if isinstance(pt, dict)
        ]
        return dumps({"granularity": params["granularity"], "series": points})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def top_products(
    days: int = 30,
    limit: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get the best-selling products for a period by revenue.

    Args:
        days: Rolling window length in days.
        limit: How many products to return (1-20).
        start_date: ISO-8601 start. Must accompany end_date.
        end_date: ISO-8601 end. Must accompany start_date.
    """
    try:
        blocked = await guard("top_products")
        if blocked:
            return blocked
        params = _window_params(days, start_date, end_date)
        params["limit"] = limit
        data = await get_client().get("analytics/top-products", params=params)
        products = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "sku": p.get("sku"),
                "quantity_sold": p.get("quantity_sold"),
                "revenue": money(p.get("revenue"), "EGP"),
                "percentage": p.get("percentage"),
            }
            for p in items_of(data)
            if isinstance(p, dict)
        ]
        return dumps({"top_products": products})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
