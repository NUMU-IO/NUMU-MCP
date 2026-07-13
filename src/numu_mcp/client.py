"""HTTP client layer: forwards the caller's NUMU API key to NUMU-api.

Auth model (pass-through):
  MCP client sends  Authorization: Bearer numu_sk_...
  -> we forward it verbatim to NUMU-api, which validates the key,
     binds the request to the key's store via RLS, and enforces scopes.
  The MCP server itself stores no credentials and keeps no session state.
"""

import hashlib
import time
from typing import Any

import httpx
import structlog
from fastmcp.server.dependencies import get_http_headers

from .config import settings

log = structlog.get_logger()


class NumuError(Exception):
    """Raised with a message that is safe and useful to show to the model/merchant."""


def _bearer_token() -> str:
    # FastMCP strips `authorization` from get_http_headers() unless explicitly
    # included — we are a pass-through proxy, so we need it.
    headers = get_http_headers(include={"authorization"}) or {}
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise NumuError(
            "Missing NUMU API key. Configure this MCP connection with header "
            "'Authorization: Bearer numu_pat_...'. Create a key in your NUMU dashboard "
            "under Settings -> Connect your AI (MCP)."
        )
    return auth[7:].strip()


# ---- key-info cache (token-hash -> (expires_at, info)) --------------------
_key_info_cache: dict[str, tuple[float, dict]] = {}


async def get_key_info() -> dict:
    """Resolve the calling key to its store binding + scopes (cached).

    Backed by NUMU-api `GET /auth/api-key/me`.
    Returns: {store_id, store_name, subdomain, scopes: [...], tenant_id}
    """
    token = _bearer_token()
    cache_key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    hit = _key_info_cache.get(cache_key)
    if hit and hit[0] > now:
        return hit[1]

    info = await request("GET", "/auth/api-key/me")
    _key_info_cache[cache_key] = (now + settings.key_info_ttl, info)
    return info


def require_scope(info: dict, scope: str) -> None:
    scopes = info.get("scopes") or []
    if scope not in scopes and "*" not in scopes:
        raise NumuError(
            f"This API key lacks the '{scope}' scope. Ask the store owner to create a key "
            f"with that scope in the NUMU dashboard (Settings -> MCP / AI access)."
        )


async def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: Any | None = None,
    files: Any | None = None,
    data: dict | None = None,
) -> Any:
    """Call NUMU-api with the caller's key; unwrap the {data: T} envelope."""
    token = _bearer_token()
    url = f"{settings.numu_api_url}{path}"
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.request(
            method,
            url,
            params=params,
            json=json,
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 401:
        raise NumuError("NUMU rejected this API key (revoked, expired, or invalid). "
                        "Create a fresh key in the NUMU dashboard.")
    if resp.status_code == 403:
        raise NumuError("This API key is not allowed to perform that action "
                        "(missing scope or wrong store).")
    if resp.status_code >= 400:
        detail: Any
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            detail = (err or {}).get("message") or (err or {}).get("message_en") \
                or body.get("detail") or resp.text[:300]
        except Exception:
            detail = resp.text[:300]
        log.warning("numu_api_error", path=path, status=resp.status_code)
        raise NumuError(f"NUMU API error ({resp.status_code}): {detail}")

    if resp.status_code == 204 or not resp.content:
        return {}
    body = resp.json()
    if isinstance(body, dict) and "data" in body and set(body) <= {
        "data",
        "meta",
        "message",
        "success",
        "pagination",
    }:
        return body["data"]
    return body


async def store_request(method: str, path: str, **kwargs) -> Any:
    """Call a store-scoped route, resolving the store id from the key."""
    info = await get_key_info()
    return await request(method, f"/stores/{info['store_id']}{path}", **kwargs)


def money(cents: Any, currency: str = "EGP") -> dict:
    """Integer-cents in, cents + human display out (platform convention)."""
    try:
        c = int(cents)
    except (TypeError, ValueError):
        return {"cents": None, "display": None}
    return {"cents": c, "display": f"{c / 100:,.2f} {currency}"}
