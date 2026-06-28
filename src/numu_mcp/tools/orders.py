"""Order tools: list, view, update status, cancel, refund."""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import (
    FULFILLMENT_STATUSES,
    ORDER_STATUSES,
    PAYMENT_STATUSES,
    REFUND_REASONS,
    REFUND_TYPES,
    major_to_minor,
    money,
    validate_choice,
    validate_uuid,
)
from ..runtime import get_client
from ._base import dumps, err, items_of, page_meta


def _order_summary(o: dict[str, Any]) -> dict[str, Any]:
    currency = o.get("currency", "EGP")
    return {
        "id": o.get("id"),
        "order_number": o.get("order_number"),
        "customer": o.get("customer_name"),
        "status": o.get("status"),
        "payment_status": o.get("payment_status"),
        "fulfillment_status": o.get("fulfillment_status"),
        "total": money(o.get("total"), currency),
        "items": o.get("item_count"),
        "created_at": o.get("created_at"),
    }


@mcp.tool()
async def list_orders(
    status: str | None = None,
    payment_status: str | None = None,
    fulfillment_status: str | None = None,
    search: str | None = None,
    customer_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> str:
    """List orders for the store, newest first, with optional filters.

    Args:
        status: Filter by order status. One of: draft, pending_deposit, pending,
            confirmed, processing, shipped, delivered, returned, cancelled,
            refunded, payment_failed.
        payment_status: Filter by payment status: pending, authorized, paid,
            partially_refunded, refunded, failed.
        fulfillment_status: unfulfilled, partially_fulfilled, fulfilled.
        search: Free-text search across order number / customer.
        customer_id: Show only this customer's orders (UUID).
        date_from: ISO-8601 lower bound on creation date (e.g. 2026-06-01).
        date_to: ISO-8601 upper bound on creation date.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = validate_choice(status, ORDER_STATUSES, "status")
        if payment_status:
            params["payment_status"] = validate_choice(
                payment_status, PAYMENT_STATUSES, "payment_status"
            )
        if fulfillment_status:
            params["fulfillment_status"] = validate_choice(
                fulfillment_status, FULFILLMENT_STATUSES, "fulfillment_status"
            )
        if customer_id:
            params["customer_id"] = validate_uuid(customer_id, "customer_id")
        if search:
            params["search"] = search
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to

        payload = await get_client().get("orders", params=params)
        orders = [_order_summary(o) for o in items_of(payload) if isinstance(o, dict)]
        return dumps({"pagination": page_meta(payload), "orders": orders})
    except Exception as exc:  # noqa: BLE001 - normalized for the model
        return err(exc)


@mcp.tool()
async def get_order(order_id: str) -> str:
    """Get the full details of a single order, including line items and addresses.

    Args:
        order_id: The order's UUID.
    """
    try:
        oid = validate_uuid(order_id, "order_id")
        o = await get_client().get(f"orders/{oid}")
        if not isinstance(o, dict):
            return dumps(o)
        currency = o.get("currency", "EGP")
        o = dict(o)
        # Re-render the money fields so the model sees readable amounts.
        for field in (
            "subtotal",
            "shipping_cost",
            "tax_amount",
            "discount_amount",
            "total",
        ):
            if o.get(field) is not None:
                o[f"{field}_formatted"] = money(o[field], currency)
        return dumps(o)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def update_order_status(order_id: str, status: str, reason: str | None = None) -> str:
    """Change an order's status (e.g. mark it processing, shipped, delivered).

    Args:
        order_id: The order's UUID.
        status: New status. One of: draft, pending_deposit, pending, confirmed,
            processing, shipped, delivered, returned, cancelled, refunded,
            payment_failed.
        reason: Optional note explaining the change.
    """
    try:
        oid = validate_uuid(order_id, "order_id")
        new_status = validate_choice(status, ORDER_STATUSES, "status")
        body: dict[str, Any] = {"status": new_status}
        if reason:
            body["reason"] = reason
        o = await get_client().patch(f"orders/{oid}/status", json=body)
        summary = _order_summary(o) if isinstance(o, dict) else o
        return dumps({"updated": True, "order": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def cancel_order(order_id: str, reason: str | None = None) -> str:
    """Cancel an order.

    Args:
        order_id: The order's UUID.
        reason: Optional cancellation reason (recorded on the order).
    """
    try:
        oid = validate_uuid(order_id, "order_id")
        params = {"reason": reason} if reason else None
        await get_client().delete(f"orders/{oid}", params=params)
        return dumps({"cancelled": True, "order_id": oid})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def refund_order(
    order_id: str,
    refund_type: str,
    reason: str,
    amount: float | None = None,
    reason_note: str | None = None,
) -> str:
    """Create a refund for an order (full or partial).

    Note: depending on the merchant's setup a refund may require approval and
    processing steps; this creates the refund request.

    Args:
        order_id: The order's UUID.
        refund_type: 'full' or 'partial'.
        reason: One of: defective, wrong_item, not_as_described,
            customer_request, duplicate_order, other.
        amount: Required for a partial refund — the amount in major units
            (e.g. 49.99). Ignored for a full refund.
        reason_note: Optional free-text note (max 1000 chars).
    """
    try:
        oid = validate_uuid(order_id, "order_id")
        rtype = validate_choice(refund_type, REFUND_TYPES, "refund_type")
        rreason = validate_choice(reason, REFUND_REASONS, "reason")
        body: dict[str, Any] = {"refund_type": rtype, "reason": rreason}
        if reason_note:
            body["reason_note"] = reason_note

        if rtype == "partial":
            if amount is None:
                from ..formatting import ValidationError

                raise ValidationError("A partial refund requires an 'amount'.")
            # Fetch the order to convert using its actual currency.
            order = await get_client().get(f"orders/{oid}")
            currency = order.get("currency", "EGP") if isinstance(order, dict) else "EGP"
            body["amount"] = major_to_minor(amount, currency)

        refund = await get_client().post(f"orders/{oid}/refunds", json=body)
        return dumps({"refund_created": True, "refund": refund})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
