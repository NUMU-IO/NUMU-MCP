"""Customer tools: search, view profile, view order history."""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import money, validate_uuid
from ..runtime import get_client
from ._base import dumps, err, items_of, page_meta


def _customer_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "name": c.get("full_name") or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "total_orders": c.get("total_orders"),
        "total_spent": money(c.get("total_spent"), "EGP"),
        "is_verified": c.get("is_verified"),
        "created_at": c.get("created_at"),
    }


@mcp.tool()
async def search_customers(
    query: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> str:
    """Search/list the store's customers by name or email.

    Args:
        query: Search text matched against name and email. Omit to list all.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if query:
            params["query"] = query
        payload = await get_client().get("customers", params=params)
        customers = [
            _customer_summary(c) for c in items_of(payload) if isinstance(c, dict)
        ]
        return dumps({"pagination": page_meta(payload), "customers": customers})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def get_customer(customer_id: str) -> str:
    """Get a customer's full profile.

    Args:
        customer_id: The customer's UUID.
    """
    try:
        cid = validate_uuid(customer_id, "customer_id")
        c = await get_client().get(f"customers/{cid}")
        if isinstance(c, dict):
            out = dict(c)
            if c.get("total_spent") is not None:
                out["total_spent_formatted"] = money(c["total_spent"], "EGP")
            return dumps(out)
        return dumps(c)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def get_customer_orders(
    customer_id: str,
    page: int = 1,
    limit: int = 20,
) -> str:
    """List a customer's order history (most recent first).

    Args:
        customer_id: The customer's UUID.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        cid = validate_uuid(customer_id, "customer_id")
        payload = await get_client().get(
            "orders", params={"customer_id": cid, "page": page, "limit": limit}
        )
        orders = [
            {
                "id": o.get("id"),
                "order_number": o.get("order_number"),
                "status": o.get("status"),
                "payment_status": o.get("payment_status"),
                "total": money(o.get("total"), o.get("currency", "EGP")),
                "created_at": o.get("created_at"),
            }
            for o in items_of(payload)
            if isinstance(o, dict)
        ]
        return dumps({"pagination": page_meta(payload), "orders": orders})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
