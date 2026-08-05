"""Product tools: list, view, create, update, delete.

Unlike orders (which use integer minor units), the product API accepts and
returns prices as decimal **major** units (e.g. 49.99). So price arguments here
are passed through unchanged.
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import (
    PRODUCT_STATUSES,
    validate_choice,
    validate_uuid,
)
from ..guards import confirmation_message, consume_token, issue_token
from ..runtime import get_client
from ._base import dumps, err, items_of, page_meta, record_mutation

_PRODUCT_TYPES = ("physical", "digital", "service")
# Fields update_product can change — used to snapshot before-state for undo.
_UNDOABLE_FIELDS = (
    "name",
    "price",
    "sku",
    "description",
    "quantity",
    "low_stock_threshold",
    "compare_at_price",
    "cost_price",
    "category_id",
    "status",
    "tags",
    "images",
    "seo_title",
    "seo_description",
    "attributes",
)
_NUMERIC_FIELDS = {"price", "compare_at_price", "cost_price"}


def _product_summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "sku": p.get("sku"),
        "status": p.get("status"),
        "price": p.get("price"),
        "currency": p.get("price_currency"),
        "quantity": p.get("quantity"),
        "in_stock": p.get("is_in_stock"),
        "low_stock": p.get("is_low_stock"),
        "category_id": p.get("category_id"),
    }


@mcp.tool()
async def list_products(
    search: str | None = None,
    status: str | None = None,
    category_id: str | None = None,
    sku: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    page: int = 1,
    limit: int = 20,
) -> str:
    """List products in the catalog with optional filters and sorting.

    Args:
        search: Free-text search across product fields.
        status: Filter by status: draft, active, archived, out_of_stock.
        category_id: Filter by category UUID.
        sku: Partial SKU match.
        sort_by: One of: name, price, created_at, updated_at, quantity.
        sort_order: 'asc' or 'desc'.
        page: Page number (1-indexed).
        limit: Page size, 1-100.
    """
    try:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = validate_choice(status, PRODUCT_STATUSES, "status")
        if category_id:
            params["category_id"] = validate_uuid(category_id, "category_id")
        if search:
            params["search"] = search
        if sku:
            params["sku"] = sku
        if sort_by:
            params["sort_by"] = validate_choice(
                sort_by,
                ("name", "price", "created_at", "updated_at", "quantity"),
                "sort_by",
            )
            params["sort_order"] = validate_choice(
                sort_order, ("asc", "desc"), "sort_order"
            )
        payload = await get_client().get("products", params=params)
        products = [
            _product_summary(p) for p in items_of(payload) if isinstance(p, dict)
        ]
        return dumps({"pagination": page_meta(payload), "products": products})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def get_product(product_id: str) -> str:
    """Get full details for a single product, including variants if any.

    Args:
        product_id: The product's UUID.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        product = await get_client().get(f"products/{pid}")
        return dumps(product)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool()
async def create_product(
    name: str,
    price: float,
    sku: str | None = None,
    description: str | None = None,
    product_type: str = "physical",
    quantity: int = 0,
    low_stock_threshold: int = 5,
    compare_at_price: float | None = None,
    cost_price: float | None = None,
    category_id: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> str:
    """Create a new product.

    Args:
        name: Product name (required).
        price: Selling price in major units, e.g. 49.99 (required).
        sku: Stock-keeping unit.
        description: Long description.
        product_type: physical, digital, or service.
        quantity: Initial stock quantity.
        low_stock_threshold: Quantity at which the product is "low stock".
        compare_at_price: Original/strikethrough price in major units.
        cost_price: Your cost in major units (for margin reporting).
        category_id: Category UUID.
        status: draft, active, or archived. Defaults to the API default.
        tags: List of tag strings.
        images: List of image URLs.
    """
    try:
        if not name.strip():
            from ..formatting import ValidationError

            raise ValidationError("name must not be empty.")
        body: dict[str, Any] = {
            "name": name.strip(),
            "price": price,
            "product_type": validate_choice(
                product_type, _PRODUCT_TYPES, "product_type"
            ),
            "quantity": quantity,
            "low_stock_threshold": low_stock_threshold,
        }
        if sku:
            body["sku"] = sku
        if description:
            body["description"] = description
        if compare_at_price is not None:
            body["compare_at_price"] = compare_at_price
        if cost_price is not None:
            body["cost_price"] = cost_price
        if category_id:
            body["category_id"] = validate_uuid(category_id, "category_id")
        if status:
            body["status"] = validate_choice(status, PRODUCT_STATUSES, "status")
        if tags:
            body["tags"] = tags
        if images:
            body["images"] = images

        product = await get_client().post("products", json=body)
        summary = _product_summary(product) if isinstance(product, dict) else product
        new_id = product.get("id") if isinstance(product, dict) else None
        await record_mutation(
            "create_product",
            {"name": body["name"], "price": price},
            summary=f"created product {new_id}",
        )
        return dumps({"created": True, "product": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "create_product"})


@mcp.tool()
async def update_product(
    product_id: str,
    name: str | None = None,
    price: float | None = None,
    sku: str | None = None,
    description: str | None = None,
    quantity: int | None = None,
    low_stock_threshold: int | None = None,
    compare_at_price: float | None = None,
    cost_price: float | None = None,
    category_id: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    images: list[str] | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Update fields on an existing product. Only provided fields are changed.

    Args:
        product_id: The product's UUID.
        name: New name.
        price: New price in major units (e.g. 49.99).
        sku: New SKU.
        description: New description.
        quantity: New stock quantity (use adjust_inventory for relative changes).
        low_stock_threshold: New low-stock threshold.
        compare_at_price: New compare-at price.
        cost_price: New cost price.
        category_id: New category UUID.
        status: draft, active, or archived.
        tags: Replace tags with this list.
        images: Replace images with this list.
        seo_title: SEO page title override (max 70 chars; API-enforced).
        seo_description: SEO meta description (max 160 chars; API-enforced).
        attributes: Replace the FULL attributes object (advanced). Always
            fetch the product first and pass a modified copy — variants and
            other metadata live in here and a partial dict would drop them.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if price is not None:
            body["price"] = price
        if sku is not None:
            body["sku"] = sku
        if description is not None:
            body["description"] = description
        if quantity is not None:
            body["quantity"] = quantity
        if low_stock_threshold is not None:
            body["low_stock_threshold"] = low_stock_threshold
        if compare_at_price is not None:
            body["compare_at_price"] = compare_at_price
        if cost_price is not None:
            body["cost_price"] = cost_price
        if category_id is not None:
            body["category_id"] = validate_uuid(category_id, "category_id")
        if status is not None:
            body["status"] = validate_choice(status, PRODUCT_STATUSES, "status")
        if tags is not None:
            body["tags"] = tags
        if images is not None:
            body["images"] = images
        if seo_title is not None:
            body["seo_title"] = seo_title
        if seo_description is not None:
            body["seo_description"] = seo_description
        if attributes is not None:
            body["attributes"] = attributes
        if not body:
            from ..formatting import ValidationError

            raise ValidationError("Provide at least one field to update.")

        # Snapshot the before-state of exactly the fields being changed, so the
        # edit can be reversed via undo_last_action.
        before = await get_client().get(f"products/{pid}")
        undo = None
        if isinstance(before, dict):
            restore: dict[str, Any] = {}
            for key in body:
                if key not in _UNDOABLE_FIELDS:
                    continue
                prev = before.get(key)
                if key in _NUMERIC_FIELDS and prev is not None:
                    try:
                        prev = float(prev)
                    except (TypeError, ValueError):
                        prev = None
                restore[key] = prev
            if restore:
                undo = {
                    "description": f"Restore product {pid} fields {list(restore)}",
                    "request": {
                        "method": "PATCH",
                        "path": f"products/{pid}",
                        "json": restore,
                        "store_scoped": True,
                    },
                }

        product = await get_client().patch(f"products/{pid}", json=body)
        summary = _product_summary(product) if isinstance(product, dict) else product
        await record_mutation(
            "update_product",
            {"product_id": pid, "fields": list(body)},
            summary=f"updated {list(body)} on product {pid}",
            undo=undo,
        )
        return dumps({"updated": True, "product": summary})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_product", "product_id": product_id})


@mcp.tool()
async def delete_product(product_id: str, confirm: str | None = None) -> str:
    """Delete a product permanently. Irreversible — requires confirmation.

    Call once without `confirm` to get a confirmation token, confirm with the
    user, then call again passing that token as `confirm`.

    Args:
        product_id: The product's UUID.
        confirm: The confirmation token from the first call.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        args = {"product_id": pid}
        if not confirm:
            return confirmation_message("delete_product", args, issue_token("delete_product", args))
        if not consume_token(confirm, "delete_product", args):
            return "Confirmation token is invalid or expired. Re-run delete_product to get a new one."
        await get_client().delete(f"products/{pid}")
        await record_mutation("delete_product", args, summary=f"deleted product {pid}")
        return dumps({"deleted": True, "product_id": pid})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "delete_product", "product_id": product_id})
