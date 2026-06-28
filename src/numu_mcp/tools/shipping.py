"""Shipping tools: list shipments, create shipment, view & track."""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import (
    SHIPMENT_CARRIERS,
    money,
    validate_choice,
    validate_uuid,
)
from ..runtime import get_client
from ._base import dumps, err, items_of, record_mutation


def _shipment_summary(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s.get("id"),
        "order_id": s.get("order_id"),
        "carrier": s.get("carrier"),
        "status": s.get("status"),
        "tracking_number": s.get("tracking_number"),
        "tracking_url": s.get("tracking_url"),
        "cod_amount": money(s.get("cod_amount"), "EGP"),
        "cod_collected": s.get("cod_collected"),
        "created_at": s.get("created_at"),
        "delivered_at": s.get("delivered_at"),
    }


@mcp.tool()
async def list_shipments(
    status: str | None = None,
    carrier: str | None = None,
    order_id: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> str:
    """List shipments for the store with optional filters.

    Args:
        status: Filter by shipment status (e.g. created, in_transit, delivered,
            cancelled, returned).
        carrier: Filter by carrier: bosta, mylerz, or jt.
        order_id: Show shipments for a specific order (UUID).
        skip: Offset for pagination.
        limit: Page size, 1-100.
    """
    try:
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if status:
            params["status"] = status
        if carrier:
            params["carrier"] = validate_choice(carrier, SHIPMENT_CARRIERS, "carrier")
        if order_id:
            params["order_id"] = validate_uuid(order_id, "order_id")
        payload = await get_client().get("shipments", params=params)
        shipments = [
            _shipment_summary(s) for s in items_of(payload) if isinstance(s, dict)
        ]
        return dumps({"count": len(shipments), "shipments": shipments})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def create_shipment(
    order_id: str,
    carrier: str = "bosta",
    shipping_method: str = "standard",
    notes: str | None = None,
) -> str:
    """Create a shipment for an order with a carrier.

    Args:
        order_id: The order to ship (UUID).
        carrier: bosta, mylerz, or jt. Defaults to bosta.
        shipping_method: Carrier shipping method (default 'standard').
        notes: Optional note for the shipment.
    """
    try:
        oid = validate_uuid(order_id, "order_id")
        c = validate_choice(carrier, SHIPMENT_CARRIERS, "carrier")
        body: dict[str, Any] = {
            "order_id": oid,
            "carrier": c,
            "shipping_method": shipping_method,
        }
        if notes:
            body["notes"] = notes
        shipment = await get_client().post("shipments", json=body)
        summary = (
            _shipment_summary(shipment) if isinstance(shipment, dict) else shipment
        )
        sid = shipment.get("id") if isinstance(shipment, dict) else None
        record_mutation(
            "create_shipment",
            {"order_id": oid, "carrier": c},
            summary=f"created shipment {sid} for order {oid}",
        )
        return dumps({"created": True, "shipment": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "create_shipment", "order_id": order_id})


@mcp.tool()
async def get_shipment(shipment_id: str) -> str:
    """Get full details for a single shipment.

    Args:
        shipment_id: The shipment's UUID.
    """
    try:
        sid = validate_uuid(shipment_id, "shipment_id")
        shipment = await get_client().get(f"shipments/{sid}")
        return dumps(shipment)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def track_shipment(shipment_id: str) -> str:
    """Fetch live tracking details for a shipment from the carrier.

    Args:
        shipment_id: The shipment's UUID.
    """
    try:
        sid = validate_uuid(shipment_id, "shipment_id")
        tracking = await get_client().get(f"shipments/{sid}/track")
        return dumps(tracking)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
