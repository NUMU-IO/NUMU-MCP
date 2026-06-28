"""Formatting and input-validation helpers shared across tools.

NUMU stores money as integer minor units (cents/piastres). These helpers keep
that detail in one place and give the AI clean, human-readable output plus
strict input validation with actionable error messages.
"""

from __future__ import annotations

from uuid import UUID

# Currencies that use 3 decimal places (per NUMU's Money value object).
_THREE_DECIMAL = {"KWD", "BHD", "OMR"}

# Status enums copied verbatim from the backend domain entities. Used to
# validate inputs before they reach the API so the AI gets a precise message
# instead of a generic 422.
ORDER_STATUSES = (
    "draft",
    "pending_deposit",
    "pending",
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "returned",
    "cancelled",
    "refunded",
    "payment_failed",
)
PAYMENT_STATUSES = (
    "pending",
    "authorized",
    "paid",
    "partially_refunded",
    "refunded",
    "failed",
)
FULFILLMENT_STATUSES = ("unfulfilled", "partially_fulfilled", "fulfilled")
PRODUCT_STATUSES = ("draft", "active", "archived", "out_of_stock")
COUPON_TYPES = ("percentage", "fixed", "free_shipping", "buy_x_get_y", "tiered")
REFUND_TYPES = ("full", "partial")
REFUND_REASONS = (
    "defective",
    "wrong_item",
    "not_as_described",
    "customer_request",
    "duplicate_order",
    "other",
)
SHIPMENT_CARRIERS = ("bosta", "mylerz", "jt")
INVENTORY_FILTERS = ("all", "low_stock", "out_of_stock")


class ValidationError(ValueError):
    """Raised when a tool input fails local validation (before any API call)."""


def minor_to_major(amount: int | float | None, currency: str = "EGP") -> float:
    """Convert integer minor units to a major-unit float (e.g. 12345 → 123.45)."""
    if amount is None:
        return 0.0
    divisor = 1000 if currency.upper() in _THREE_DECIMAL else 100
    return round(amount / divisor, 3 if divisor == 1000 else 2)


def money(amount: int | float | None, currency: str = "EGP") -> str:
    """Format integer minor units as a readable money string: ``1,234.56 EGP``."""
    value = minor_to_major(amount, currency)
    decimals = 3 if currency.upper() in _THREE_DECIMAL else 2
    return f"{value:,.{decimals}f} {currency.upper()}"


def major_to_minor(amount: float | int, currency: str = "EGP") -> int:
    """Convert a major-unit amount to integer minor units (e.g. 123.45 → 12345)."""
    divisor = 1000 if currency.upper() in _THREE_DECIMAL else 100
    return int(round(float(amount) * divisor))


def validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    """Return ``value`` if it's in ``allowed``, else raise a clear error."""
    if value not in allowed:
        raise ValidationError(
            f"Invalid {field}: '{value}'. Must be one of: {', '.join(allowed)}."
        )
    return value


def validate_uuid(value: str, field: str) -> str:
    """Validate that ``value`` is a UUID; return it normalized as a string."""
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(
            f"Invalid {field}: '{value}' is not a valid UUID."
        ) from exc


def validate_positive_int(value: int, field: str, *, allow_zero: bool = False) -> int:
    """Validate a positive (or non-negative) integer."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer.")
    if allow_zero and value < 0:
        raise ValidationError(f"{field} must be zero or greater.")
    if not allow_zero and value <= 0:
        raise ValidationError(f"{field} must be greater than zero.")
    return value
