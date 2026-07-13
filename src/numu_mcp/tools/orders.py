"""Order tools (read-mostly in the MVP). Scope: orders:read / orders:write."""

from typing import Any

from ..client import store_request
from ..server import mcp

READ_ONLY = {"readOnlyHint": True}


def _trim_order(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "order_number": o.get("order_number"),
        "status": o.get("status"),
        "payment_status": o.get("payment_status"),
        "payment_method": o.get("payment_method"),
        "total": o.get("total") or o.get("total_amount"),
        "currency": o.get("currency"),
        "customer_name": o.get("customer_name")
        or (o.get("customer") or {}).get("name")
        or (o.get("customer") or {}).get("full_name"),
        "customer_phone": o.get("customer_phone") or (o.get("customer") or {}).get("phone"),
        "items_count": len(o.get("items") or []) or o.get("items_count"),
        "created_at": o.get("created_at"),
    }


def _items(payload: Any) -> tuple[list, Any]:
    if isinstance(payload, dict):
        return payload.get("items") or payload.get("orders") or [], payload.get("total")
    return payload or [], None


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "List orders with filters: status (pending/processing/shipped/delivered/"
        "cancelled...), payment_status, text search (order number / customer), pagination."
    ),
)
async def list_orders(
    status: str | None = None,
    payment_status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    params: dict = {"page": page, "per_page": min(per_page, 100)}
    if status:
        params["status"] = status
    if payment_status:
        params["payment_status"] = payment_status
    if search:
        params["search"] = search
    payload = await store_request("GET", "/orders/", params=params)
    items, total = _items(payload)
    return {"total": total if total is not None else len(items),
            "page": page,
            "orders": [_trim_order(o) for o in items]}


@mcp.tool(
    annotations=READ_ONLY,
    description="Get one order in full detail: items, amounts, customer, addresses, payment.",
)
async def get_order(order_id: str) -> Any:
    return await store_request("GET", f"/orders/{order_id}")


@mcp.tool(
    annotations=READ_ONLY,
    description="Get an order's event timeline (created, paid, shipped, delivered, ...).",
)
async def get_order_timeline(order_id: str) -> Any:
    return await store_request("GET", f"/orders/{order_id}/timeline")


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    description=(
        "Change an order's status (e.g. processing, shipped, delivered, cancelled). "
        "OUTWARD-FACING: may trigger customer notifications. Requires confirm=true — "
        "ask the merchant before calling with confirm."
    ),
)
async def update_order_status(order_id: str, new_status: str, confirm: bool = False) -> Any:
    if not confirm:
        return {
            "confirmation_required": True,
            "message": (
                f"This will set order {order_id} to '{new_status}' and may notify the "
                "customer. Ask the merchant to confirm, then call again with confirm=true."
            ),
        }
    return await store_request(
        "PATCH", f"/orders/{order_id}/status", json={"status": new_status}
    )


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "List abandoned checkouts (carts that never became orders), including contact "
        "info when available and the traffic source that brought the shopper."
    ),
)
async def list_abandoned_checkouts(page: int = 1, per_page: int = 20) -> Any:
    return await store_request(
        "GET", "/abandoned-checkouts/", params={"page": page, "per_page": min(per_page, 100)}
    )
