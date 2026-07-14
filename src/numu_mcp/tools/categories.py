"""Category (collection) tools: create, update, delete.

Categories are the store's product collections — the groupings shoppers browse
on the storefront and that products are assigned to via ``category_id``. Listing
lives in ``store.list_categories``; this module adds the write operations.

Reversible edits register an inverse request so ``undo_last_action`` can roll
them back; deletion is irreversible and therefore confirmation-gated.
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import ValidationError, validate_uuid
from ..guards import confirmation_message, consume_token, issue_token
from ..runtime import get_client
from ._base import dumps, err, record_mutation

# Fields update_category can change — snapshotted before-state drives undo.
_UNDOABLE_FIELDS = (
    "name",
    "description",
    "image_url",
    "parent_id",
    "position",
    "is_active",
)


def _summary(cat: Any) -> dict[str, Any]:
    if not isinstance(cat, dict):
        return {"raw": cat}
    return {
        "id": cat.get("id"),
        "name": cat.get("name"),
        "slug": cat.get("slug"),
        "parent_id": cat.get("parent_id"),
        "position": cat.get("position"),
        "is_active": cat.get("is_active"),
        "product_count": cat.get("product_count"),
    }


def _bust_cache() -> None:
    # list_categories caches for 120s; drop it so a fresh list reflects writes.
    from .. import cache as _cache

    _cache.invalidate("categories")


@mcp.tool()
async def create_category(
    name: str,
    description: str | None = None,
    image_url: str | None = None,
    parent_id: str | None = None,
    position: int = 0,
    is_active: bool = True,
) -> str:
    """Create a product category (collection).

    Args:
        name: Category name (required).
        description: Optional description shown on the collection page.
        image_url: Optional cover image URL.
        parent_id: Parent category UUID to nest under (omit for a top-level one).
        position: Sort position; lower shows first.
        is_active: Whether the category is visible on the storefront.
    """
    try:
        if not name or not name.strip():
            raise ValidationError("name is required.")
        body: dict[str, Any] = {
            "name": name.strip(),
            "position": position,
            "is_active": is_active,
        }
        if description is not None:
            body["description"] = description
        if image_url is not None:
            body["image_url"] = image_url
        if parent_id is not None:
            body["parent_id"] = validate_uuid(parent_id, "parent_id")

        created = await get_client().post("categories", json=body)
        _bust_cache()

        new_id = created.get("id") if isinstance(created, dict) else None
        undo = None
        if new_id:
            undo = {
                "description": f"Delete category {new_id} (undo of create)",
                "request": {
                    "method": "DELETE",
                    "path": f"categories/{new_id}",
                    "store_scoped": True,
                },
            }
        await record_mutation(
            "create_category",
            {"name": body["name"]},
            summary=f"created category '{body['name']}'"
            + (f" ({new_id})" if new_id else ""),
            undo=undo,
        )
        return dumps({"created": True, "category": _summary(created)})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "create_category", "name": name})


@mcp.tool()
async def update_category(
    category_id: str,
    name: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    parent_id: str | None = None,
    position: int | None = None,
    is_active: bool | None = None,
) -> str:
    """Update fields on a category. Only provided fields change.

    Args:
        category_id: The category's UUID.
        name: New name.
        description: New description.
        image_url: New cover image URL.
        parent_id: New parent category UUID (nesting).
        position: New sort position.
        is_active: Show/hide on the storefront.
    """
    try:
        cid = validate_uuid(category_id, "category_id")
        body: dict[str, Any] = {}
        for key, value in (
            ("name", name),
            ("description", description),
            ("image_url", image_url),
            ("position", position),
            ("is_active", is_active),
        ):
            if value is not None:
                body[key] = value
        if parent_id is not None:
            body["parent_id"] = validate_uuid(parent_id, "parent_id")
        if not body:
            raise ValidationError("Provide at least one field to update.")

        # Snapshot before-state of the changed fields for undo.
        before = await get_client().get(f"categories/{cid}")
        undo = None
        if isinstance(before, dict):
            restore = {k: before.get(k) for k in body if k in _UNDOABLE_FIELDS}
            if restore:
                undo = {
                    "description": f"Restore category {cid} fields {list(restore)}",
                    "request": {
                        "method": "PATCH",
                        "path": f"categories/{cid}",
                        "json": restore,
                        "store_scoped": True,
                    },
                }

        updated = await get_client().patch(f"categories/{cid}", json=body)
        _bust_cache()
        await record_mutation(
            "update_category",
            {"category_id": cid, "fields": list(body)},
            summary=f"updated {list(body)} on category {cid}",
            undo=undo,
        )
        return dumps({"updated": True, "category": _summary(updated)})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_category", "category_id": category_id})


@mcp.tool()
async def delete_category(category_id: str, confirm: str | None = None) -> str:
    """Delete a category permanently. Irreversible — requires confirmation.

    Products keep existing but lose this category assignment. Call once without
    `confirm` to get a token, confirm with the user, then call again with it.

    Args:
        category_id: The category's UUID.
        confirm: The confirmation token from the first call.
    """
    try:
        cid = validate_uuid(category_id, "category_id")
        args = {"category_id": cid}
        if not confirm:
            return confirmation_message(
                "delete_category", args, issue_token("delete_category", args)
            )
        if not consume_token(confirm, "delete_category", args):
            return (
                "Confirmation token is invalid or expired. "
                "Re-run delete_category to get a new one."
            )
        await get_client().delete(f"categories/{cid}")
        _bust_cache()
        await record_mutation(
            "delete_category", args, summary=f"deleted category {cid}"
        )
        return dumps({"deleted": True, "category_id": cid})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "delete_category", "category_id": category_id})
