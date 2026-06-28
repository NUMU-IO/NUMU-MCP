"""Inventory tools: view stock, stock stats, and adjust stock levels."""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import (
    INVENTORY_FILTERS,
    ValidationError,
    money,
    validate_choice,
    validate_uuid,
)
from ..runtime import get_client
from ._base import dumps, err, items_of, record_mutation


@mcp.tool()
async def list_inventory(
    filter: str = "all",
    page: int = 1,
    limit: int = 50,
) -> str:
    """List products with their current stock levels.

    Args:
        filter: 'all', 'low_stock', or 'out_of_stock'.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        f = validate_choice(filter, INVENTORY_FILTERS, "filter")
        payload = await get_client().get(
            "inventory", params={"filter": f, "page": page, "limit": limit}
        )
        items = [
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "sku": it.get("sku"),
                "quantity": it.get("quantity"),
                "low_stock_threshold": it.get("low_stock_threshold"),
                "is_low_stock": it.get("is_low_stock"),
                "is_out_of_stock": it.get("is_out_of_stock"),
                "status": it.get("status"),
            }
            for it in items_of(payload)
            if isinstance(it, dict)
        ]
        return dumps({"filter": f, "count": len(items), "inventory": items})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def inventory_stats() -> str:
    """Get a summary of inventory health: total products, low-stock and
    out-of-stock counts, and total inventory value."""
    try:
        stats = await get_client().get("inventory/stats")
        if isinstance(stats, dict):
            currency = stats.get("currency", "EGP")
            out: dict[str, Any] = dict(stats)
            if stats.get("total_inventory_value") is not None:
                out["total_inventory_value_formatted"] = money(
                    stats["total_inventory_value"], currency
                )
            return dumps(out)
        return dumps(stats)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def adjust_inventory(
    product_id: str,
    adjustment: int,
    reason: str | None = None,
) -> str:
    """Adjust a product's stock by a relative amount.

    Use a positive number to add stock and a negative number to remove it.
    To set an absolute quantity instead, use update_product(quantity=...).

    Args:
        product_id: The product's UUID.
        adjustment: Relative change, e.g. 10 to add ten, -3 to remove three.
        reason: Optional note recorded with the adjustment.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        if not isinstance(adjustment, int) or isinstance(adjustment, bool):
            raise ValidationError("adjustment must be an integer.")
        if adjustment == 0:
            raise ValidationError("adjustment must be non-zero.")
        body: dict[str, Any] = {"product_id": pid, "adjustment": adjustment}
        if reason:
            body["reason"] = reason
        result = await get_client().post("inventory/adjust", json=body)
        undo = {
            "description": f"Reverse stock adjustment of {adjustment} on {pid}",
            "request": {
                "method": "POST",
                "path": "inventory/adjust",
                "json": {
                    "product_id": pid,
                    "adjustment": -adjustment,
                    "reason": "undo previous adjustment",
                },
                "store_scoped": True,
            },
        }
        await record_mutation(
            "adjust_inventory",
            {"product_id": pid, "adjustment": adjustment},
            summary=f"adjusted {pid} by {adjustment}",
            undo=undo,
        )
        return dumps({"adjusted": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "adjust_inventory", "product_id": product_id})
