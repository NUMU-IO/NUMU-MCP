"""Batch tools — operate on many records in parallel instead of one-by-one.

Reads run concurrently for speed; the batch *write* records a single audit entry
whose undo payload replays the reverse of every sub-change at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..app import mcp
from ..formatting import ORDER_STATUSES, validate_choice, validate_uuid
from ..runtime import get_client
from ._base import dumps, err, record_mutation

_MAX_BATCH = 50


@mcp.tool()
async def batch_get_orders(order_ids: list[str]) -> str:
    """Fetch multiple orders concurrently (much faster than one call each).

    Args:
        order_ids: List of order UUIDs (max 50).
    """
    try:
        if not order_ids:
            return dumps({"requested": 0, "orders": [], "errors": []})
        ids = [validate_uuid(o, "order_id") for o in order_ids[:_MAX_BATCH]]
        client = get_client()

        async def one(oid: str) -> dict[str, Any]:
            try:
                return {"order_id": oid, "data": await client.get(f"orders/{oid}")}
            except Exception as exc:  # noqa: BLE001
                return {"order_id": oid, "error": str(exc)}

        results = await asyncio.gather(*[one(i) for i in ids])
        ok = [r["data"] for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]
        return dumps(
            {"requested": len(ids), "succeeded": len(ok), "failed": len(bad),
             "orders": ok, "errors": bad}
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "batch_get_orders"})


@mcp.tool()
async def batch_get_products(product_ids: list[str]) -> str:
    """Fetch multiple products concurrently.

    Args:
        product_ids: List of product UUIDs (max 50).
    """
    try:
        if not product_ids:
            return dumps({"requested": 0, "products": [], "errors": []})
        ids = [validate_uuid(p, "product_id") for p in product_ids[:_MAX_BATCH]]
        client = get_client()

        async def one(pid: str) -> dict[str, Any]:
            try:
                return {"product_id": pid, "data": await client.get(f"products/{pid}")}
            except Exception as exc:  # noqa: BLE001
                return {"product_id": pid, "error": str(exc)}

        results = await asyncio.gather(*[one(i) for i in ids])
        ok = [r["data"] for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]
        return dumps(
            {"requested": len(ids), "succeeded": len(ok), "failed": len(bad),
             "products": ok, "errors": bad}
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "batch_get_products"})


@mcp.tool()
async def batch_update_order_status(
    order_ids: list[str], status: str, reason: str | None = None
) -> str:
    """Set the same status on many orders at once (e.g. mark a batch shipped).

    Records one audit entry; undo_last_action will restore every order's prior
    status together.

    Args:
        order_ids: List of order UUIDs (max 50).
        status: Target status (e.g. processing, shipped, delivered).
        reason: Optional note applied to each.
    """
    try:
        if not order_ids:
            return dumps({"requested": 0, "results": []})
        new_status = validate_choice(status, ORDER_STATUSES, "status")
        ids = [validate_uuid(o, "order_id") for o in order_ids[:_MAX_BATCH]]
        client = get_client()
        body: dict[str, Any] = {"status": new_status}
        if reason:
            body["reason"] = reason

        async def one(oid: str) -> dict[str, Any]:
            try:
                before = await client.get(f"orders/{oid}")
                prev = before.get("status") if isinstance(before, dict) else None
                await client.patch(f"orders/{oid}/status", json=body)
                return {"order_id": oid, "ok": True, "previous": prev}
            except Exception as exc:  # noqa: BLE001
                return {"order_id": oid, "ok": False, "error": str(exc)}

        results = await asyncio.gather(*[one(i) for i in ids])

        # Build a single batch-undo that restores each order's prior status.
        undo_requests = [
            {
                "method": "PATCH",
                "path": f"orders/{r['order_id']}/status",
                "json": {"status": r["previous"]},
                "store_scoped": True,
            }
            for r in results
            if r.get("ok") and r.get("previous") and r["previous"] != new_status
        ]
        undo = (
            {
                "description": f"Restore prior status of {len(undo_requests)} order(s)",
                "requests": undo_requests,
            }
            if undo_requests
            else None
        )
        succeeded = sum(1 for r in results if r.get("ok"))
        await record_mutation(
            "batch_update_order_status",
            {"count": len(ids), "status": new_status},
            summary=f"set {succeeded}/{len(ids)} orders to {new_status}",
            undo=undo,
        )
        return dumps(
            {"requested": len(ids), "succeeded": succeeded, "results": results}
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "batch_update_order_status"})


@mcp.tool()
async def batch_adjust_inventory(items: list[dict[str, Any]]) -> str:
    """Adjust stock for many products at once.

    Records one audit entry; undo_last_action reverses every adjustment together.

    Args:
        items: List of objects, each ``{"product_id": "<uuid>", "adjustment": <int>}``.
            Positive adds stock, negative removes. Max 50.
    """
    try:
        if not items:
            return dumps({"requested": 0, "results": []})
        client = get_client()

        async def one(entry: dict[str, Any]) -> dict[str, Any]:
            try:
                pid = validate_uuid(str(entry.get("product_id")), "product_id")
                adj = int(entry.get("adjustment"))
                if adj == 0:
                    return {"product_id": pid, "ok": False, "error": "adjustment is 0"}
                await client.post(
                    "inventory/adjust", json={"product_id": pid, "adjustment": adj}
                )
                return {"product_id": pid, "ok": True, "adjustment": adj}
            except Exception as exc:  # noqa: BLE001
                return {"product_id": entry.get("product_id"), "ok": False, "error": str(exc)}

        results = await asyncio.gather(*[one(e) for e in items[:_MAX_BATCH]])
        undo_requests = [
            {
                "method": "POST",
                "path": "inventory/adjust",
                "json": {"product_id": r["product_id"], "adjustment": -r["adjustment"]},
                "store_scoped": True,
            }
            for r in results
            if r.get("ok")
        ]
        undo = (
            {
                "description": f"Reverse {len(undo_requests)} stock adjustment(s)",
                "requests": undo_requests,
            }
            if undo_requests
            else None
        )
        succeeded = sum(1 for r in results if r.get("ok"))
        await record_mutation(
            "batch_adjust_inventory",
            {"count": len(results)},
            summary=f"adjusted {succeeded}/{len(results)} products",
            undo=undo,
        )
        return dumps({"requested": len(results), "succeeded": succeeded, "results": results})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "batch_adjust_inventory"})
