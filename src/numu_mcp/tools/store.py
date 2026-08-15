"""Store/meta tools: capabilities introspection, plan & usage, categories.

These make the server self-describing — the AI can ask what it's allowed to do
(given the plan and the connected user's permissions) before attempting work.
"""

from __future__ import annotations

import json as _json
from typing import Any

from ..app import mcp
from ..capabilities import TOOL_REQUIREMENTS, probe
from ..runtime import current_store_id, get_client
from ._base import dumps, err, record_mutation


def _capability_view(caps: Any) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name in sorted(TOOL_REQUIREMENTS):
        allowed, reason = caps.tool_allowed(name)
        tools[name] = {"available": allowed, **({"reason": reason} if reason else {})}

    def _usage_block(key: str) -> dict[str, Any]:
        block = dict(caps.usage.get(key, {}) or {})
        used, limit = block.get("used"), block.get("limit")
        if (
            not block.get("unlimited")
            and isinstance(used, int)
            and isinstance(limit, int)
            and limit > 0
        ):
            block["remaining"] = max(0, limit - used)
            block["percent_used"] = round(used / limit * 100, 1)
        return block

    return {
        "plan": caps.plan,
        "plan_display_name": caps.plan_display_name,
        "is_owner": caps.is_owner,
        "features": caps.features,
        "usage": {
            "products": _usage_block("products"),
            "orders_this_month": _usage_block("orders_this_month"),
        },
        "permission_domains": sorted(caps.permission_domains),
        "tools": tools,
        "notes": caps.notes,
    }


@mcp.tool()
async def get_capabilities() -> str:
    """Describe what this store/connection can currently do.

    Returns the active plan, usage vs. limits (with remaining and % used),
    enabled plan features, the connected user's permission domains, and a
    per-tool availability map (with a reason when a tool is blocked). Call this
    first when unsure whether an action is permitted on this plan."""
    try:
        caps = await probe.get()
        return dumps(_capability_view(caps))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def refresh_capabilities() -> str:
    """Force-refresh the cached plan/permission snapshot, then return it.

    Use after the merchant changes plan or staff permissions mid-conversation."""
    try:
        probe.invalidate()
        caps = await probe.get(force=True)
        return dumps(_capability_view(caps))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


_ORDER_STATUS_TRIGGERS = ("confirmed", "processing", "shipped", "delivered")

# Fields echoed back on the PUT so a partial edit doesn't blank them. The
# endpoint replaces the whole ``tracking.meta`` config, so anything omitted
# reverts to its schema default — that is how a store ends up with
# ``pixel_enabled: false`` and no events. ``capi_access_token`` is
# deliberately NOT here: the API keeps the stored credential when the field
# is absent, and the GET only ever returns a masked form we could not
# resubmit anyway.
_META_TRACKING_PASSTHROUGH = (
    "pixel_id",
    "pixel_enabled",
    "capi_enabled",
    "test_event_code",
    "consent_required",
    "purchase_trigger",
    "lead_trigger",
    "whatsapp_lead_enabled",
    "pixels",
    "consent_settings",
    "ad_account_id",
    "page_id",
)


@mcp.tool()
async def get_meta_tracking() -> str:
    """Read the store's Meta Pixel / Conversions API configuration.

    Shows which legs are live (``pixel_enabled`` / ``capi_enabled``), the
    pixel id, whether a CAPI token is on file (masked), and the COD-aware
    ``purchase_trigger`` / ``lead_trigger``. The raw access token is never
    returned.
    """
    try:
        cfg = await get_client().get("settings/tracking/meta")
        return dumps(cfg)
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "get_meta_tracking"})


@mcp.tool()
async def update_meta_tracking(
    purchase_trigger: str | None = None,
    lead_trigger: str | None = None,
    pixel_enabled: bool | None = None,
    capi_enabled: bool | None = None,
    test_event_code: str | None = None,
) -> str:
    """Change specific Meta tracking fields, preserving everything else.

    Read-modify-write: the underlying endpoint is a PUT that REPLACES the
    whole Meta config, so this reads the current settings first and overrides
    only the fields you name. Never sends the CAPI access token — omitting it
    is what keeps the stored credential intact.

    Args:
        purchase_trigger: Order status that fires the server-side Purchase —
            one of confirmed/processing/shipped/delivered. Essential for COD:
            with no trigger set, Purchase only fires on a payment-provider
            webhook, and a cash-on-delivery order never produces one, so no
            server-side Purchase is sent at all. Pass "none" to clear.
        lead_trigger: Same, for the Lead event. Pass "none" to clear.
        pixel_enabled: Turn the browser Pixel on/off.
        capi_enabled: Turn the Conversions API on/off.
        test_event_code: Events-Manager test code, or "none" to clear.
    """
    try:
        from ..formatting import ValidationError

        for name, val in (
            ("purchase_trigger", purchase_trigger),
            ("lead_trigger", lead_trigger),
        ):
            if val is not None and val != "none" and val not in _ORDER_STATUS_TRIGGERS:
                raise ValidationError(
                    f"{name} must be one of "
                    f"{', '.join(_ORDER_STATUS_TRIGGERS)} (or 'none' to clear)."
                )

        client = get_client()
        current = await client.get("settings/tracking/meta")
        if not isinstance(current, dict):
            raise ValidationError("Could not read current Meta tracking settings.")
        if not current.get("pixel_id"):
            raise ValidationError(
                "No Meta pixel is configured for this store — set the Pixel ID "
                "in the dashboard first."
            )

        body: dict[str, Any] = {
            k: current.get(k) for k in _META_TRACKING_PASSTHROUGH if k in current
        }
        body.setdefault("pixel_enabled", False)
        body.setdefault("capi_enabled", False)
        body.setdefault("consent_required", False)
        # `debug_mode` is a live-window flag, not stored config — re-asserting
        # the GET's value would silently extend the window by another hour.
        body["debug_mode"] = False

        overrides: dict[str, Any] = {}
        if purchase_trigger is not None:
            overrides["purchase_trigger"] = (
                None if purchase_trigger == "none" else purchase_trigger
            )
        if lead_trigger is not None:
            overrides["lead_trigger"] = None if lead_trigger == "none" else lead_trigger
        if pixel_enabled is not None:
            overrides["pixel_enabled"] = pixel_enabled
        if capi_enabled is not None:
            overrides["capi_enabled"] = capi_enabled
        if test_event_code is not None:
            overrides["test_event_code"] = (
                None if test_event_code == "none" else test_event_code
            )
        if not overrides:
            raise ValidationError("Provide at least one field to update.")
        body.update(overrides)

        before = {k: current.get(k) for k in overrides}
        updated = await client.request(
            "PUT", "settings/tracking/meta", json=body, store_scoped=True
        )
        await record_mutation(
            "update_meta_tracking",
            {"fields": list(overrides)},
            summary=f"updated Meta tracking {list(overrides)}",
            undo={
                "description": f"Restore Meta tracking {list(before)}",
                "request": {
                    "method": "PUT",
                    "path": "settings/tracking/meta",
                    "json": {**body, **before},
                    "store_scoped": True,
                },
            },
        )
        return dumps({"updated": True, "before": before, "after": overrides,
                      "settings": updated})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_meta_tracking"})


@mcp.tool()
async def get_plan_and_usage() -> str:
    """Get the merchant's current plan with live resource usage vs. limits
    (products, monthly orders) and which plan features are enabled."""
    try:
        usage = await get_client().get("plan/usage")
        return dumps(usage)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def list_recent_actions(limit: int = 25) -> str:
    """List recent changes the AI made to this store — the audit trail.

    Shows tool name, arguments, outcome and whether each is still undoable. Use
    it to answer "what did the AI change?" and to find something to undo.

    Args:
        limit: How many recent entries to return (1-100).
    """
    try:
        from ..audit import get_recent_actions

        store_id = current_store_id()
        limit = min(max(int(limit), 1), 100)
        actions = await get_recent_actions(store_id, limit=limit)
        return dumps({"actions": actions})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "list_recent_actions"})


@mcp.tool()
async def undo_last_action() -> str:
    """Reverse the most recent reversible change (price/status/stock/coupon edit).

    Replays the stored inverse request(s) for the latest undoable action, marks
    it undone, and records the undo itself. Irreversible actions (deletes,
    refunds, cancellations) are never undoable here."""
    try:
        from ..audit import get_undoable_actions, mark_undone

        store_id = current_store_id()
        actions = await get_undoable_actions(store_id, limit=1)
        if not actions:
            return "No undoable actions found in the recent history."

        last = actions[0]
        # Both backends return undo_payload as a dict; tolerate a JSON string.
        payload = last["undo_payload"]
        if isinstance(payload, str):
            payload = _json.loads(payload)
        requests = payload.get("requests") or (
            [payload["request"]] if payload.get("request") else []
        )
        if not requests:
            return "The last action has no replayable undo."

        client = get_client()
        replayed = 0
        errors: list[str] = []
        for r in requests:
            try:
                await client.request(
                    r["method"],
                    r["path"],
                    store_scoped=r.get("store_scoped", True),
                    json=r.get("json"),
                )
                replayed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        await mark_undone(last["id"])
        await record_mutation(
            "undo_last_action",
            {"undone_action_id": last["id"], "undone_tool": last["tool_name"]},
            summary=payload.get("description", ""),
        )
        return dumps(
            {
                "undone": True,
                "reversed_action": last["tool_name"],
                "description": payload.get("description"),
                "requests_replayed": replayed,
                "errors": errors,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "undo_last_action"})


@mcp.tool()
async def update_store_info(
    description: str | None = None,
    name: str | None = None,
) -> str:
    """Update the store's public profile fields.

    These render in the storefront header, the meta/OG description, and the
    JSON-LD structured data on every page — customer- and crawler-facing.

    Args:
        description: New public store description.
        name: New public store display name.
    """
    try:
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if name is not None:
            body["name"] = name
        if not body:
            from ..formatting import ValidationError

            raise ValidationError("Provide at least one field to update.")
        client = get_client()
        sid = current_store_id()
        before = await client.request("GET", f"stores/{sid}", store_scoped=False)
        undo = None
        if isinstance(before, dict):
            restore = {k: before.get(k) for k in body}
            undo = {
                "description": f"Restore store fields {list(restore)}",
                "request": {
                    "method": "PATCH",
                    "path": f"stores/{sid}",
                    "json": restore,
                    "store_scoped": False,
                },
            }
        store = await client.request(
            "PATCH", f"stores/{sid}", store_scoped=False, json=body
        )
        out = (
            {k: store.get(k) for k in ("id", "name", "description")}
            if isinstance(store, dict)
            else store
        )
        await record_mutation(
            "update_store_info",
            {"fields": list(body)},
            summary=f"updated store fields {list(body)}",
            undo=undo,
        )
        return dumps({"updated": True, "store": out})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_store_info"})


@mcp.tool()
async def list_categories(include_inactive: bool = False) -> str:
    """List the store's product categories (useful when creating/updating
    products and assigning a category_id).

    Args:
        include_inactive: Include inactive categories too.
    """
    try:
        data = await get_client().get(
            "categories",
            params={"include_inactive": include_inactive},
            cache_ttl=120,
        )
        items = data if isinstance(data, list) else []
        categories = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "slug": c.get("slug"),
                "parent_id": c.get("parent_id"),
                "is_active": c.get("is_active"),
            }
            for c in items
            if isinstance(c, dict)
        ]
        return dumps({"count": len(categories), "categories": categories})
    except Exception as exc:  # noqa: BLE001
        return err(exc)
