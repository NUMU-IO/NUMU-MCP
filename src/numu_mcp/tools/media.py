"""Media tools: product image upload. Scope: catalog:write (images live under products)."""

import base64
import binascii

import httpx

from ..client import NumuError, store_request
from ..server import mcp

_MAX_BYTES = 5 * 1024 * 1024  # NUMU-api rejects > 5 MB
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False},
    description=(
        "Upload an image to a product. Provide EITHER `image_url` (we fetch it) OR "
        "`image_base64` (raw base64 of the file). Accepts JPEG/PNG/WebP/GIF up to 5 MB. "
        "NUMU strips EXIF, converts to WebP and generates thumbnail/medium/large variants "
        "on Cloudflare R2 automatically. Returns the uploaded image URLs."
    ),
)
async def upload_product_image(
    product_id: str,
    image_url: str | None = None,
    image_base64: str | None = None,
    filename: str = "image.jpg",
    content_type: str = "image/jpeg",
) -> dict:
    if bool(image_url) == bool(image_base64):
        raise NumuError("Provide exactly one of image_url or image_base64.")

    if image_url:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(image_url)
        if resp.status_code != 200:
            raise NumuError(f"Could not fetch image_url (HTTP {resp.status_code}).")
        content = resp.content
        fetched_type = resp.headers.get("content-type", "").split(";")[0].strip()
        if fetched_type in _ALLOWED_TYPES:
            content_type = fetched_type
    else:
        try:
            content = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise NumuError(f"image_base64 is not valid base64: {exc}") from exc

    if len(content) > _MAX_BYTES:
        raise NumuError(f"Image is {len(content) / 1024 / 1024:.1f} MB; the limit is 5 MB.")
    if content_type not in _ALLOWED_TYPES:
        raise NumuError(f"Unsupported content_type '{content_type}'. Use one of {sorted(_ALLOWED_TYPES)}.")

    return await store_request(
        "POST",
        f"/products/{product_id}/images",
        files={"file": (filename, content, content_type)},
    )
