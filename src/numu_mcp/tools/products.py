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


def _check_options_variants(
    options: list[dict[str, Any]] | None, variants: list[dict[str, Any]] | None
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Validate the option/variant matrix before it reaches the API, so the
    model gets a precise message instead of a half-built product."""
    from ..formatting import ValidationError

    axes: dict[str, set[str]] = {}
    if options is not None:
        if len(options) > 3:
            raise ValidationError("A product can have at most 3 options.")
        clean = []
        for i, opt in enumerate(options):
            name = str(opt.get("name") or "").strip()
            values = [str(v).strip() for v in opt.get("values") or [] if str(v).strip()]
            if not name or not values:
                raise ValidationError(f"Option {i + 1} needs a name and values.")
            axes[name] = set(values)
            clean.append({"name": name, "values": values, "position": i})
        options = clean
    if variants is not None:
        for i, v in enumerate(variants):
            if v.get("price") is None:
                raise ValidationError(f"Variant {i + 1} needs a price.")
            for key, val in (v.get("option_values") or {}).items():
                if axes and (key not in axes or str(val) not in axes[key]):
                    raise ValidationError(
                        f"Variant {i + 1}: '{key}: {val}' is not one of the options."
                    )
    return options, variants


def _apply_extras(body: dict[str, Any], fields: dict[str, Any]) -> None:
    """Copy the optional product fields that were actually given."""
    for key, value in fields.items():
        if value is not None:
            body[key] = value


@mcp.tool()
async def create_product(
    name: str,
    price: float | None = None,
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
    options: list[dict[str, Any]] | None = None,
    variants: list[dict[str, Any]] | None = None,
    short_description: str | None = None,
    brand: str | None = None,
    slug: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    weight: float | None = None,
    attributes: dict[str, Any] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """Create a new product, with its options and variants in one call.

    To copy the structure of an existing product (same options, same kind of
    variants), read it with get_product first and reuse its options/variants.
    Add pictures after creating with add_product_image (or pass image URLs).

    Args:
        name: Product name (required).
        price: Selling price in major units, e.g. 49.99. Optional when
            variants are given (then it is the cheapest variant's price).
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
        options: The product's option axes (max 3), e.g.
            [{"name": "Format",
              "values": ["Paperback", "Hardcover", "Special edition"]},
             {"name": "Paper", "values": ["White", "Creamy"]}].
        variants: One entry per combination you sell, e.g.
            [{"option_values": {"Format": "Paperback", "Paper": "White"},
              "price": 180, "compare_at_price": 220, "sku": "VIC-PB-W",
              "inventory_quantity": 5}]. `price` is required on each; the keys
            of option_values must be option names and the values must be
            listed under that option. 3 formats x 2 papers = 6 variants.
        short_description: Short summary shown near the price.
        brand: Brand, or the author for a book.
        slug: URL slug (auto-generated from the name when omitted).
        seo_title: SEO title (max 70 chars).
        seo_description: SEO description (max 160 chars).
        weight: Shipping weight in kg.
        attributes: Free-form key/value attributes.
        extra_fields: Any other field the NUMU product API accepts (e.g.
            sale_price, sale_starts_at, sale_ends_at, related_product_ids,
            requires_shipping, tax_exempt, template_suffix).
    """
    try:
        from ..formatting import ValidationError

        if not name.strip():
            raise ValidationError("name must not be empty.")
        options, variants = _check_options_variants(options, variants)
        if price is None:
            if not variants:
                raise ValidationError("Give a price, or variants with prices.")
            price = min(float(v["price"]) for v in variants)
        body: dict[str, Any] = dict(extra_fields or {})
        body |= {
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
        _apply_extras(
            body,
            {
                "options": options,
                "variants": variants,
                "short_description": short_description,
                "brand": brand,
                "slug": slug,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "weight": weight,
                "attributes": attributes,
            },
        )

        product = await get_client().post("products", json=body)
        summary = _product_summary(product) if isinstance(product, dict) else product
        new_id = product.get("id") if isinstance(product, dict) else None
        await record_mutation(
            "create_product",
            {"name": body["name"], "price": price, "variants": len(variants or [])},
            summary=f"created product {new_id}",
        )
        out: dict[str, Any] = {"created": True, "product": summary}
        if isinstance(product, dict) and product.get("variants"):
            out["variants"] = [_variant_summary(v) for v in product["variants"]]
        return dumps(out)
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
    options: list[dict[str, Any]] | None = None,
    variants: list[dict[str, Any]] | None = None,
    short_description: str | None = None,
    brand: str | None = None,
    slug: str | None = None,
    weight: float | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """Update fields on an existing product. Only provided fields are changed.

    Changing ONE variant's price/stock/SKU: use update_variant. Adding or
    removing ONE variant: create_variant / delete_variant. Pass options and
    variants here only to rebuild the whole option set (e.g. turn a simple
    product into a 6-variant one, or add a new option axis).

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
        options: The FULL new option list (max 3), same shape as create_product.
        variants: The FULL new variant list, same shape as create_product.
            Include each existing variant's "id" (from list_variants) to keep
            it; a variant left out is DELETED. Send options and variants
            together when the axes change.
        short_description: Short summary shown near the price.
        brand: Brand, or the author for a book.
        slug: URL slug.
        weight: Shipping weight in kg.
        extra_fields: Any other field the NUMU product API accepts (e.g.
            sale_price, sale_starts_at, sale_ends_at, related_product_ids).
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        options, variants = _check_options_variants(options, variants)
        body: dict[str, Any] = dict(extra_fields or {})
        _apply_extras(
            body,
            {
                "options": options,
                "variants": variants,
                "short_description": short_description,
                "brand": brand,
                "slug": slug,
                "weight": weight,
            },
        )
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


# update_variant's money fields come back from the API as strings ("250.00").
_VARIANT_MONEY = {"price", "compare_at_price", "cost_price"}


def _variant_summary(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": v.get("id"),
        "options": v.get("option_values"),
        "price": v.get("price"),
        "compare_at_price": v.get("compare_at_price"),
        "sku": v.get("sku"),
        "inventory_quantity": v.get("inventory_quantity"),
        "in_stock": v.get("is_in_stock"),
    }


@mcp.tool()
async def list_variants(product_id: str) -> str:
    """List a product's variants (e.g. each size/colour) with their ids,
    option values, price, SKU and stock. Use the ids with update_variant.

    Args:
        product_id: The product's UUID.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        variants = await get_client().get(f"products/{pid}/variants")
        return dumps({"variants": [_variant_summary(v) for v in items_of(variants)]})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "list_variants", "product_id": product_id})


@mcp.tool()
async def update_variant(
    product_id: str,
    variant_id: str,
    price: float | None = None,
    compare_at_price: float | None = None,
    cost_price: float | None = None,
    sku: str | None = None,
    inventory_quantity: int | None = None,
) -> str:
    """Update ONE variant of a product (its price, compare-at price, cost,
    SKU or stock). Only the fields you pass change; the product's other
    variants and options are untouched. This is the safe way to change prices
    on a product with variants — do not rewrite `attributes` for that.

    The product's base (listing) price follows its cheapest variant
    automatically. To change several variants, call this once per variant
    (get the ids from list_variants).

    Args:
        product_id: The product's UUID.
        variant_id: The variant's UUID (from list_variants).
        price: New price in major units (e.g. 450 for EGP 450).
        compare_at_price: New "was" price in major units.
        cost_price: New cost price in major units.
        sku: New SKU (must be unique in the store).
        inventory_quantity: New absolute stock for this variant.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        vid = validate_uuid(variant_id, "variant_id")
        sent = {
            "price": price,
            "compare_at_price": compare_at_price,
            "cost_price": cost_price,
            "sku": sku,
            "inventory_quantity": inventory_quantity,
        }
        body = {k: v for k, v in sent.items() if v is not None}
        if not body:
            from ..formatting import ValidationError

            raise ValidationError("Provide at least one field to update.")

        # Snapshot the changed fields so undo_last_action can restore them.
        variants = items_of(await get_client().get(f"products/{pid}/variants"))
        before = next((v for v in variants if v.get("id") == vid), None)
        if before is None:
            from ..formatting import ValidationError

            raise ValidationError("That variant does not belong to this product.")
        restore: dict[str, Any] = {}
        for key in body:
            prev = before.get(key)
            if key in _VARIANT_MONEY and prev is not None:
                prev = float(prev)
            restore[key] = prev

        path = f"products/{pid}/variants/{vid}"
        updated = await get_client().patch(path, json=body)
        await record_mutation(
            "update_variant",
            {"product_id": pid, "variant_id": vid, "fields": list(body)},
            summary=f"updated {list(body)} on variant {vid} of product {pid}",
            undo={
                "description": f"Restore variant {vid} fields {list(restore)}",
                "request": {
                    "method": "PATCH",
                    "path": path,
                    "json": restore,
                    "store_scoped": True,
                },
            },
        )
        return dumps(
            {
                "updated": True,
                "before": {k: before.get(k) for k in body},
                "variant": _variant_summary(updated)
                if isinstance(updated, dict)
                else updated,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(
            exc,
            context={
                "tool": "update_variant",
                "product_id": product_id,
                "variant_id": variant_id,
            },
        )


@mcp.tool()
async def create_variant(
    product_id: str,
    option_values: dict[str, str],
    price: float,
    compare_at_price: float | None = None,
    cost_price: float | None = None,
    sku: str | None = None,
    inventory_quantity: int = 0,
    image_url: str | None = None,
) -> str:
    """Add ONE variant to an existing product (e.g. a new "Special edition /
    Creamy" combination). The option names and values must already exist on
    the product; to add a new option or value, use update_product with the
    full options + variants instead.

    Args:
        product_id: The product's UUID.
        option_values: e.g. {"Format": "Special edition", "Paper": "Creamy"}.
        price: Price in major units.
        compare_at_price: "Was" price in major units.
        cost_price: Cost in major units.
        sku: SKU (must be unique in the store).
        inventory_quantity: Starting stock.
        image_url: Image shown when this variant is selected.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        body: dict[str, Any] = {
            "option_values": option_values,
            "price": price,
            "inventory_quantity": inventory_quantity,
        }
        _apply_extras(
            body,
            {
                "compare_at_price": compare_at_price,
                "cost_price": cost_price,
                "sku": sku,
                "image_url": image_url,
            },
        )
        variant = await get_client().post(f"products/{pid}/variants", json=body)
        vid = variant.get("id") if isinstance(variant, dict) else None
        await record_mutation(
            "create_variant",
            {"product_id": pid, "option_values": option_values, "price": price},
            summary=f"added variant {vid} to product {pid}",
        )
        return dumps(
            {
                "created": True,
                "variant": _variant_summary(variant)
                if isinstance(variant, dict)
                else variant,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "create_variant", "product_id": product_id})


@mcp.tool()
async def delete_variant(
    product_id: str, variant_id: str, confirm: str | None = None
) -> str:
    """Remove ONE variant from a product. Irreversible — requires confirmation.

    Call once without `confirm` to get a token, confirm with the user, then
    call again passing that token as `confirm`.

    Args:
        product_id: The product's UUID.
        variant_id: The variant's UUID (from list_variants).
        confirm: The confirmation token from the first call.
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        vid = validate_uuid(variant_id, "variant_id")
        args = {"product_id": pid, "variant_id": vid}
        if not confirm:
            return confirmation_message(
                "delete_variant", args, issue_token("delete_variant", args)
            )
        if not consume_token(confirm, "delete_variant", args):
            return (
                "Confirmation token is invalid or expired. "
                "Re-run delete_variant to get a new one."
            )
        await get_client().delete(f"products/{pid}/variants/{vid}")
        await record_mutation(
            "delete_variant", args, summary=f"deleted variant {vid} of product {pid}"
        )
        return dumps({"deleted": True, "product_id": pid, "variant_id": vid})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "delete_variant", "variant_id": variant_id})


_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # the API's own upload limit


async def _download_public_image(url: str) -> tuple[bytes, str, str]:
    """Fetch an image from a public https URL, refusing private addresses.

    The server fetches on behalf of the caller, so every hop is checked: https
    only, a public IP, at most 3 redirects, an image content type, <= 5 MB.

    ponytail: the IP is checked before httpx connects, so a DNS answer that
    changes between the two (rebinding) is not caught; the caller is already an
    authenticated store key, which bounds the risk. Pin the resolved IP if
    this ever accepts anonymous input.
    """
    import asyncio
    import ipaddress
    from urllib.parse import urljoin, urlsplit

    import httpx

    from ..formatting import ValidationError

    async def check(target: str) -> None:
        parts = urlsplit(target)
        if parts.scheme != "https" or not parts.hostname:
            raise ValidationError("image_url must be an https:// link to an image.")
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or 443
        )
        if not infos or any(
            not ipaddress.ip_address(info[4][0]).is_global for info in infos
        ):
            raise ValidationError("That image address is not a public website.")

    current = url
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for _ in range(4):
            await check(current)
            async with client.stream(
                "GET", current, headers={"User-Agent": "Mozilla/5.0 numu-mcp"}
            ) as res:
                if res.is_redirect and res.headers.get("location"):
                    current = urljoin(current, res.headers["location"])
                    continue
                if res.status_code != 200:
                    raise ValidationError(
                        f"Could not download the image (HTTP {res.status_code})."
                    )
                ctype = res.headers.get("content-type", "").split(";")[0].strip()
                if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    raise ValidationError(
                        "That link is not a JPEG/PNG/WebP/GIF image "
                        f"({ctype or 'unknown'})."
                    )
                data = bytearray()
                async for chunk in res.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _IMAGE_MAX_BYTES:
                        raise ValidationError("The image is larger than 5 MB.")
                name = urlsplit(current).path.rsplit("/", 1)[-1] or "image"
                return bytes(data), ctype, name
    raise ValidationError("The image link redirects too many times.")


@mcp.tool()
async def add_product_image(product_id: str, image_url: str) -> str:
    """Upload a picture to a product from a public https image link.

    NUMU downloads it, converts it to WebP in three sizes and hosts it, then
    adds it to the product's images (the first image is the main one). Prefer
    this over passing raw URLs in `images`, which leaves the picture on the
    other site.

    Args:
        product_id: The product's UUID.
        image_url: A public https link to a JPEG, PNG, WebP or GIF (max 5 MB).
    """
    try:
        pid = validate_uuid(product_id, "product_id")
        data, ctype, name = await _download_public_image(image_url)
        result = await get_client().request(
            "POST", f"products/{pid}/images", files={"file": (name, data, ctype)}
        )
        await record_mutation(
            "add_product_image",
            {"product_id": pid, "image_url": image_url},
            summary=f"uploaded an image to product {pid}",
        )
        return dumps({"uploaded": True, "image": result})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "add_product_image", "product_id": product_id})


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
            return confirmation_message(
                "delete_product", args, issue_token("delete_product", args)
            )
        if not consume_token(confirm, "delete_product", args):
            return "Confirmation token is invalid or expired. Re-run delete_product to get a new one."
        await get_client().delete(f"products/{pid}")
        await record_mutation("delete_product", args, summary=f"deleted product {pid}")
        return dumps({"deleted": True, "product_id": pid})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "delete_product", "product_id": product_id})
