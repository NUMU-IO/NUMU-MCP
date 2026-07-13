"""Catalog tools: products, categories, inventory. Scope: catalog:read / catalog:write."""

from typing import Any

from ..client import store_request
from ..server import mcp

READ_ONLY = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}


def _trim_product(p: dict) -> dict:
    """Keep the fields a model actually needs; drop bulky internals."""
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "slug": p.get("slug"),
        "sku": p.get("sku"),
        "status": p.get("status"),
        "price": p.get("price"),
        "compare_at_price": p.get("compare_at_price"),
        "currency": p.get("price_currency") or p.get("currency"),
        "quantity": p.get("quantity"),
        "category_id": p.get("category_id"),
        "tags": p.get("tags"),
        "images": p.get("images"),
        "seo_title": p.get("seo_title"),
        "seo_description": p.get("seo_description"),
        "created_at": p.get("created_at"),
    }


def _items(payload: Any) -> tuple[list, Any]:
    """Normalize list endpoints that return either [..] or {items: [..], total: n}."""
    if isinstance(payload, dict):
        return payload.get("items") or payload.get("products") or [], payload.get("total")
    return payload or [], None


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "List products in the store. Supports text search, status filter "
        "(active/draft/archived), category filter and pagination."
    ),
)
async def list_products(
    search: str | None = None,
    status: str | None = None,
    category_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    params: dict = {"page": page, "per_page": min(per_page, 100)}
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    if category_id:
        params["category_id"] = category_id
    payload = await store_request("GET", "/products/", params=params)
    items, total = _items(payload)
    return {"total": total if total is not None else len(items),
            "page": page,
            "products": [_trim_product(p) for p in items]}


@mcp.tool(
    annotations=READ_ONLY,
    description="Get one product with full details (variants, options, images, SEO).",
)
async def get_product(product_id: str) -> dict:
    return await store_request("GET", f"/products/{product_id}")


@mcp.tool(
    annotations=WRITE,
    description=(
        "Create a product. Prices are DECIMAL MAJOR UNITS of the store currency "
        "(e.g. 420.00 EGP, not cents). `images` is a list of image URLs (upload files "
        "first with upload_product_image, or pass existing URLs). `description` allows "
        "HTML. Set status='draft' unless the merchant explicitly wants it live."
    ),
)
async def create_product(
    name: str,
    price: float,
    description: str | None = None,
    short_description: str | None = None,
    compare_at_price: float | None = None,
    quantity: int = 0,
    sku: str | None = None,
    category_id: str | None = None,
    tags: list[str] | None = None,
    images: list[str] | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    status: str = "draft",
) -> dict:
    body: dict = {
        "name": name,
        "price": str(price),
        "quantity": quantity,
        "status": status,
        "images": images or [],
        "tags": tags or [],
    }
    if description is not None:
        body["description"] = description
    if short_description is not None:
        body["short_description"] = short_description
    if compare_at_price is not None:
        body["compare_at_price"] = str(compare_at_price)
    if sku is not None:
        body["sku"] = sku
    if category_id is not None:
        body["category_id"] = category_id
    if seo_title is not None:
        body["seo_title"] = seo_title
    if seo_description is not None:
        body["seo_description"] = seo_description
    created = await store_request("POST", "/products/", json=body)
    return _trim_product(created) if isinstance(created, dict) else created


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    description=(
        "Partially update a product: any of name, description, price (major units), "
        "compare_at_price, quantity, status (active/draft/archived), category_id, tags, "
        "seo_title, seo_description. Only the fields you pass change."
    ),
)
async def update_product(
    product_id: str,
    name: str | None = None,
    description: str | None = None,
    price: float | None = None,
    compare_at_price: float | None = None,
    quantity: int | None = None,
    status: str | None = None,
    category_id: str | None = None,
    tags: list[str] | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
) -> dict:
    body: dict = {}
    for key, value in {
        "name": name,
        "description": description,
        "quantity": quantity,
        "status": status,
        "category_id": category_id,
        "tags": tags,
        "seo_title": seo_title,
        "seo_description": seo_description,
    }.items():
        if value is not None:
            body[key] = value
    if price is not None:
        body["price"] = str(price)
    if compare_at_price is not None:
        body["compare_at_price"] = str(compare_at_price)
    if not body:
        return {"warning": "No fields to update were provided."}
    updated = await store_request("PATCH", f"/products/{product_id}", json=body)
    return _trim_product(updated) if isinstance(updated, dict) else updated


@mcp.tool(
    annotations=READ_ONLY,
    description="List product categories (id, name, slug, product counts).",
)
async def list_categories() -> Any:
    return await store_request("GET", "/categories/")


@mcp.tool(
    annotations=WRITE,
    description="Create a product category. Name is required; description optional.",
)
async def create_category(name: str, description: str | None = None) -> Any:
    body: dict = {"name": name}
    if description is not None:
        body["description"] = description
    return await store_request("POST", "/categories/", json=body)
