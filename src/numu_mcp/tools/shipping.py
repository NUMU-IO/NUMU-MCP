"""Shipping tools: shipping zones & rates, plus shipments (create/view/track).

Zone/rate tools take and report money in **EGP major units**, never cents.
The storage layer is cents throughout, and the one time that boundary was
crossed by hand a COD fee meant to be 15.00 was stored as 1500.00 and added
to every order. Callers here say ``80``, the tool multiplies.
"""

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


def _egp(cents: Any) -> str:
    try:
        return f"{int(cents) / 100:,.2f} EGP"
    except (TypeError, ValueError):
        return "—"


def _to_cents(egp: float) -> int:
    """EGP major units -> integer cents."""
    return int(round(float(egp) * 100))


def _rate_view(r: dict[str, Any]) -> dict[str, Any]:
    cfg = r.get("config") or {}
    out: dict[str, Any] = {
        "rate_id": r.get("id"),
        "label": r.get("label"),
        "rate_type": r.get("rate_type"),
        "is_active": r.get("is_active"),
    }
    if "amount_cents" in cfg:
        out["amount"] = _egp(cfg.get("amount_cents"))
    if "free_when_subtotal_gte_cents" in cfg:
        out["free_when_order_over"] = _egp(cfg.get("free_when_subtotal_gte_cents"))
    if "bands" in cfg:
        out["bands"] = [
            {
                "up_to_grams": b.get("max_weight_g"),
                "amount": _egp(b.get("amount_cents")),
            }
            for b in cfg.get("bands") or []
        ]
    return out


@mcp.tool()
async def list_shipping_zones() -> str:
    """List delivery zones with their governorates, COD settings and rates.

    All money is shown in EGP. ``cod_fee`` is ADDED to the shipping charge
    whenever the shopper pays cash on delivery — check it before assuming a
    zone's quote is just its rate.
    """
    try:
        zones = await get_client().get("shipping/zones")
        out = []
        for z in items_of(zones):
            out.append(
                {
                    "zone_id": z.get("id"),
                    "name": z.get("name"),
                    "name_ar": z.get("name_ar"),
                    "governorates": z.get("governorate_codes"),
                    "cod_enabled": z.get("cod_enabled"),
                    "cod_fee": _egp(z.get("cod_fee_cents")),
                    "is_active": z.get("is_active"),
                    "eta_days": [
                        z.get("estimated_days_min"),
                        z.get("estimated_days_max"),
                    ],
                    "rates": [_rate_view(r) for r in z.get("rates") or []],
                }
            )
        return dumps({"count": len(out), "zones": out})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "list_shipping_zones"})


@mcp.tool()
async def update_shipping_zone(
    zone_id: str,
    cod_fee_egp: float | None = None,
    cod_enabled: bool | None = None,
    governorate_codes: list[str] | None = None,
) -> str:
    """Update a delivery zone. Only provided fields change.

    Args:
        zone_id: The zone's UUID (from list_shipping_zones).
        cod_fee_egp: Cash-on-delivery fee in EGP, ADDED on top of the
            shipping rate for COD orders. Pass 0 for no fee. This is a
            per-order surcharge, not a free-shipping threshold — for
            "free delivery over X" use set_shipping_rate(free_over_egp=X).
        cod_enabled: Whether cash on delivery is offered in this zone.
        governorate_codes: Replaces zone membership atomically. A
            governorate in no zone cannot check out at all.
    """
    try:
        from ..formatting import ValidationError

        zid = validate_uuid(zone_id, "zone_id")
        body: dict[str, Any] = {}
        if cod_fee_egp is not None:
            if cod_fee_egp < 0:
                raise ValidationError("cod_fee_egp cannot be negative.")
            body["cod_fee_cents"] = _to_cents(cod_fee_egp)
        if cod_enabled is not None:
            body["cod_enabled"] = cod_enabled
        if governorate_codes is not None:
            body["governorate_codes"] = governorate_codes
        if not body:
            raise ValidationError("Provide at least one field to update.")

        before = await get_client().get(f"shipping/zones/{zid}")
        prev = (
            {k: before.get(k) for k in body if k in before}
            if isinstance(before, dict)
            else {}
        )
        updated = await get_client().patch(f"shipping/zones/{zid}", json=body)
        await record_mutation(
            "update_shipping_zone",
            {"zone_id": zid, "fields": list(body)},
            summary=f"updated zone {zid} {list(body)}",
            undo=(
                {
                    "description": f"Restore zone {zid} {list(prev)}",
                    "request": {
                        "method": "PATCH",
                        "path": f"shipping/zones/{zid}",
                        "json": prev,
                        "store_scoped": True,
                    },
                }
                if prev
                else None
            ),
        )
        return dumps(
            {
                "updated": True,
                "zone": {
                    "name": updated.get("name"),
                    "cod_enabled": updated.get("cod_enabled"),
                    "cod_fee": _egp(updated.get("cod_fee_cents")),
                }
                if isinstance(updated, dict)
                else updated,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_shipping_zone"})


@mcp.tool()
async def set_shipping_rate(
    zone_id: str,
    rate_id: str,
    amount_egp: float,
    free_over_egp: float | None = None,
    label: str | None = None,
) -> str:
    """Set a zone's delivery charge, optionally free above an order value.

    Args:
        zone_id: The zone's UUID.
        rate_id: The rate's UUID (from list_shipping_zones).
        amount_egp: What the shopper pays for delivery, in EGP (e.g. 80).
        free_over_egp: Order subtotal in EGP at or above which delivery
            becomes free (e.g. 1500). Omit to charge ``amount_egp`` on
            every order regardless of size.
        label: Optional new display label.
    """
    try:
        from ..formatting import ValidationError

        zid = validate_uuid(zone_id, "zone_id")
        rid = validate_uuid(rate_id, "rate_id")
        if amount_egp < 0:
            raise ValidationError("amount_egp cannot be negative.")
        if free_over_egp is not None and free_over_egp <= 0:
            raise ValidationError(
                "free_over_egp must be greater than 0 — omit it for a flat rate."
            )

        if free_over_egp is None:
            config = {"type": "flat", "amount_cents": _to_cents(amount_egp)}
        else:
            config = {
                "type": "free_over",
                "amount_cents": _to_cents(amount_egp),
                "free_when_subtotal_gte_cents": _to_cents(free_over_egp),
            }
        body: dict[str, Any] = {"config": config}
        if label is not None:
            body["label"] = label

        updated = await get_client().patch(
            f"shipping/zones/{zid}/rates/{rid}", json=body
        )
        await record_mutation(
            "set_shipping_rate",
            {"zone_id": zid, "rate_id": rid, "config": config},
            summary=(
                f"rate {rid} -> {amount_egp:g} EGP"
                + (f", free over {free_over_egp:g} EGP" if free_over_egp else "")
            ),
        )
        return dumps(
            {"updated": True, "rate": _rate_view(updated)}
            if isinstance(updated, dict)
            else {"updated": True}
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "set_shipping_rate"})


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
        await record_mutation(
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
