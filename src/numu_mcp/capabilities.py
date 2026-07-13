"""Plan & permission awareness — what this store can actually do right now.

This is what makes the server behave like a real integration rather than a
fixed list of tools: it probes the merchant's live plan, usage, feature flags
and the connecting user's permissions, then maps that onto the MCP tool surface
so the AI knows — before acting — which actions are available, which are blocked,
and why (e.g. "needs the Pro plan" or "monthly order limit reached").

The probe is cached briefly (TTL) so repeated reads during one conversation are
cheap, while still picking up plan/permission changes within a minute.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .client import NumuApiError
from .runtime import get_client

_CACHE_TTL_SECONDS = 60.0

# Tool → (permission domain, required plan feature).
# `permission_domain` is matched against the granted permission codes
# (e.g. "orders.view", "orders.edit" all share the "orders" domain). A None
# feature means the action isn't gated by a plan feature flag.
TOOL_REQUIREMENTS: dict[str, dict[str, str | None]] = {
    # Orders
    "list_orders": {"domain": "orders", "feature": None},
    "get_order": {"domain": "orders", "feature": None},
    "update_order_status": {"domain": "orders", "feature": None},
    "cancel_order": {"domain": "orders", "feature": None},
    "refund_order": {"domain": "orders", "feature": None},
    # Products
    "list_products": {"domain": "products", "feature": None},
    "get_product": {"domain": "products", "feature": None},
    "create_product": {"domain": "products", "feature": None},
    "update_product": {"domain": "products", "feature": None},
    "delete_product": {"domain": "products", "feature": None},
    "list_categories": {"domain": "products", "feature": None},
    # Inventory
    "list_inventory": {"domain": "products", "feature": None},
    "inventory_stats": {"domain": "products", "feature": None},
    "adjust_inventory": {"domain": "products", "feature": None},
    # Customers
    "search_customers": {"domain": "customers", "feature": None},
    "get_customer": {"domain": "customers", "feature": None},
    "get_customer_orders": {"domain": "customers", "feature": None},
    # Discounts — gated by the discount_codes plan feature
    "create_discount": {"domain": "coupons", "feature": "discount_codes"},
    "list_discounts": {"domain": "coupons", "feature": "discount_codes"},
    "deactivate_discount": {"domain": "coupons", "feature": "discount_codes"},
    # Analytics — gated by the analytics plan feature
    "sales_report": {"domain": "analytics", "feature": "analytics"},
    "revenue_over_time": {"domain": "analytics", "feature": "analytics"},
    "top_products": {"domain": "analytics", "feature": "analytics"},
    # Shipping
    "list_shipments": {"domain": "shipping", "feature": None},
    "create_shipment": {"domain": "shipping", "feature": None},
    "get_shipment": {"domain": "shipping", "feature": None},
    "track_shipment": {"domain": "shipping", "feature": None},
}


@dataclass
class Capabilities:
    """A resolved snapshot of what the store/connection can do."""

    plan: str | None = None
    plan_display_name: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    is_owner: bool = False
    permission_codes: set[str] = field(default_factory=set)
    permission_domains: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    # ── Capability questions the tools ask ────────────────────────────────
    def has_feature(self, feature: str | None) -> bool:
        if feature is None:
            return True
        # Absence of explicit flag → assume allowed (fail open to the API).
        return self.features.get(feature, True)

    def has_domain(self, domain: str | None) -> bool:
        if domain is None or self.is_owner:
            return True
        if not self.permission_domains:
            # Couldn't load permissions → don't block; let the API decide.
            return True
        return domain in self.permission_domains

    def tool_allowed(self, tool_name: str) -> tuple[bool, str | None]:
        """Return (allowed, reason_if_blocked) for a tool name."""
        req = TOOL_REQUIREMENTS.get(tool_name)
        if not req:
            return True, None
        if not self.has_feature(req["feature"]):
            return False, (
                f"Your plan ({self.plan_display_name or self.plan or 'current'}) "
                f"does not include '{req['feature']}'. Upgrade the plan to use this."
            )
        if not self.has_domain(req["domain"]):
            return False, (
                f"The connected user lacks '{req['domain']}' permissions for this "
                "action."
            )
        return True, None

    def usage_note(self, resource: str) -> str | None:
        """A short 'used X of Y' note for products / orders, if at/near limit."""
        block = self.usage.get(resource)
        if not isinstance(block, dict) or block.get("unlimited"):
            return None
        used, limit = block.get("used"), block.get("limit")
        if isinstance(used, int) and isinstance(limit, int) and limit > 0:
            return f"{used} of {limit} {resource} used"
        return None


def _domains_from_permissions(perms: Any) -> tuple[set[str], set[str]]:
    """Flatten a permissions payload into (codes, domains).

    Handles the two shapes the API may return: a flat list of codes, or a dict
    of code→bool (only truthy codes count).
    """
    codes: set[str] = set()
    if isinstance(perms, dict):
        for k, v in perms.items():
            if v:
                codes.add(str(k))
    elif isinstance(perms, list):
        codes = {str(c) for c in perms}
    domains = {c.split(".")[0] for c in codes if "." in c}
    return codes, domains


class CapabilityProbe:
    """Loads and caches live capabilities, keyed per store (multi-tenant safe)."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Capabilities, float]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    @staticmethod
    def _store_key() -> str:
        from .runtime import current_store_id

        try:
            return current_store_id()
        except Exception:  # noqa: BLE001 - never let cache keying break a probe
            return "default"

    async def get(self, *, force: bool = False) -> Capabilities:
        # Note: time.monotonic is allowed and fine for a TTL.
        key = self._store_key()
        hit = self._cache.get(key)
        if (
            not force
            and hit is not None
            and (time.monotonic() - hit[1]) < _CACHE_TTL_SECONDS
        ):
            return hit[0]

        caps = Capabilities()
        client = get_client()

        try:
            usage = await client.get("plan/usage")
            if isinstance(usage, dict):
                caps.plan = usage.get("plan")
                caps.plan_display_name = usage.get("display_name")
                caps.features = (
                    usage.get("features") if isinstance(usage.get("features"), dict) else {}
                )
                caps.usage = {
                    "products": usage.get("products", {}),
                    "orders_this_month": usage.get("orders_this_month", {}),
                }
        except NumuApiError as exc:
            caps.notes.append(f"plan usage unavailable: {exc}")

        try:
            me = await client.request("GET", "staff/me", store_scoped=False)
            if isinstance(me, dict):
                caps.is_owner = bool(me.get("is_owner"))
                codes, domains = _domains_from_permissions(me.get("permissions"))
                caps.permission_codes = codes
                caps.permission_domains = domains
        except NumuApiError as exc:
            caps.notes.append(f"permissions unavailable: {exc}")

        self._cache[key] = (caps, time.monotonic())
        return caps


# Module-level singleton shared by the resource, the tool, and gated tools.
probe = CapabilityProbe()


async def guard(tool_name: str) -> str | None:
    """Pre-flight a plan/permission-gated tool.

    Returns ``None`` when the action is allowed, or a ready-to-return,
    user-facing message explaining why it's blocked (and how to unblock it).
    Fails open: if capabilities can't be loaded, the call proceeds and the API
    remains the source of truth.
    """
    try:
        caps = await probe.get()
    except Exception:  # noqa: BLE001 - never let the guard itself break a tool
        return None
    allowed, reason = caps.tool_allowed(tool_name)
    if allowed:
        return None
    return f"Not available: {reason}"
