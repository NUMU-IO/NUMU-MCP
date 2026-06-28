"""Discount (coupon) tools: create, list, deactivate."""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..capabilities import guard
from ..formatting import (
    COUPON_TYPES,
    ValidationError,
    validate_choice,
    validate_uuid,
)
from ..runtime import get_client
from ._base import dumps, err, items_of, page_meta


def _coupon_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "code": c.get("code"),
        "type": c.get("coupon_type"),
        "value": c.get("value"),
        "is_active": c.get("is_active"),
        "is_expired": c.get("is_expired"),
        "is_usable": c.get("is_usable"),
        "usage_count": c.get("usage_count"),
        "usage_limit": c.get("usage_limit"),
        "valid_from": c.get("valid_from"),
        "valid_until": c.get("valid_until"),
    }


@mcp.tool()
async def create_discount(
    code: str,
    coupon_type: str,
    value: float,
    min_order_amount: float | None = None,
    max_discount_amount: float | None = None,
    usage_limit: int | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> str:
    """Create a discount coupon.

    Args:
        code: The coupon code customers will enter (case-insensitive, unique).
        coupon_type: percentage, fixed, free_shipping, buy_x_get_y, or tiered.
        value: For 'percentage', the percent off (0-100). For 'fixed', the
            discount amount in major units (e.g. 50 for 50 EGP off). For
            free_shipping, use 0.
        min_order_amount: Minimum order subtotal (major units) for the coupon.
        max_discount_amount: Cap on the discount (major units), for percentage.
        usage_limit: Total number of times the coupon may be redeemed.
        valid_from: ISO-8601 start of validity (e.g. 2026-06-01T00:00:00).
        valid_until: ISO-8601 end of validity.
    """
    try:
        blocked = await guard("create_discount")
        if blocked:
            return blocked
        if not code.strip():
            raise ValidationError("code must not be empty.")
        ctype = validate_choice(coupon_type, COUPON_TYPES, "coupon_type")
        if ctype == "percentage" and not (0 <= value <= 100):
            raise ValidationError("For a percentage coupon, value must be 0-100.")
        body: dict[str, Any] = {
            "code": code.strip(),
            "coupon_type": ctype,
            "value": value,
        }
        if min_order_amount is not None:
            body["min_order_amount"] = min_order_amount
        if max_discount_amount is not None:
            body["max_discount_amount"] = max_discount_amount
        if usage_limit is not None:
            body["usage_limit"] = usage_limit
        if valid_from:
            body["valid_from"] = valid_from
        if valid_until:
            body["valid_until"] = valid_until

        coupon = await get_client().post("coupons", json=body)
        summary = _coupon_summary(coupon) if isinstance(coupon, dict) else coupon
        return dumps({"created": True, "discount": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def list_discounts(
    is_active: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> str:
    """List discount coupons.

    Args:
        is_active: Filter by active state. Omit to list all.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        blocked = await guard("list_discounts")
        if blocked:
            return blocked
        params: dict[str, Any] = {"page": page, "limit": limit}
        if is_active is not None:
            params["is_active"] = is_active
        payload = await get_client().get("coupons", params=params)
        coupons = [
            _coupon_summary(c) for c in items_of(payload) if isinstance(c, dict)
        ]
        return dumps({"pagination": page_meta(payload), "discounts": coupons})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def deactivate_discount(coupon_id: str) -> str:
    """Deactivate a discount coupon so it can no longer be redeemed.

    The coupon is kept (not deleted) and can be re-activated later from the
    merchant dashboard.

    Args:
        coupon_id: The coupon's UUID.
    """
    try:
        blocked = await guard("deactivate_discount")
        if blocked:
            return blocked
        cid = validate_uuid(coupon_id, "coupon_id")
        coupon = await get_client().patch(f"coupons/{cid}", json={"is_active": False})
        summary = _coupon_summary(coupon) if isinstance(coupon, dict) else coupon
        return dumps({"deactivated": True, "discount": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
