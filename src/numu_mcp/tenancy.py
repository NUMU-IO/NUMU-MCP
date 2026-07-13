"""Per-request tenant context for hosted (multi-tenant) deployments.

In single-tenant mode (stdio, or HTTP with ``NUMU_MCP_ACCESS_TOKEN`` set) the
token and store id come from the environment and this module stays inert.

In multi-tenant pass-through mode (HTTP transport with NO env token) every MCP
request must carry ``Authorization: Bearer numu_pat_…``. The ASGI middleware
below resolves that token to its store binding via NUMU-api's
``GET /auth/api-key/me`` (which also enforces scopes and hard store binding
server-side) and stashes a :class:`TenantContext` in a ``ContextVar``. Contexts
propagate into the tasks the MCP session manager spawns per request, so tools,
resources and the HTTP client all see the right store without any plumbing.

The server process itself stores no credentials: the token is forwarded
verbatim to NUMU-api on every call and only a SHA-256 hash of it is used as a
cache key for the (short-lived) store-binding lookup.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx

_INFO_TTL_SECONDS = 300.0


@dataclass
class TenantContext:
    """The resolved identity of one authenticated MCP request."""

    token: str
    store_id: str
    store_name: str | None = None
    subdomain: str | None = None
    currency: str | None = None
    scopes: list[str] | None = field(default=None)


_current: ContextVar[TenantContext | None] = ContextVar(
    "numu_mcp_tenant", default=None
)


def current() -> TenantContext | None:
    """The tenant of the request being served, or ``None`` (single-tenant)."""
    return _current.get()


def current_store_id() -> str | None:
    ctx = _current.get()
    return ctx.store_id if ctx else None


def current_token() -> str | None:
    ctx = _current.get()
    return ctx.token if ctx else None


# ── token → store-binding cache ─────────────────────────────────────────────
_info_cache: dict[str, tuple[float, TenantContext]] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def resolve(token: str, api_base: str, *, timeout: float = 15.0) -> TenantContext:
    """Resolve a PAT to its store binding via ``GET /auth/api-key/me`` (cached).

    Raises ``PermissionError`` with a human-readable message when NUMU rejects
    the token, and ``ConnectionError`` when the API is unreachable.
    """
    key = _cache_key(token)
    hit = _info_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]

    url = f"{api_base}/auth/api-key/me"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        raise ConnectionError(f"Could not reach the NUMU API to verify the token: {exc}") from exc

    if response.status_code in (401, 403):
        raise PermissionError(
            "NUMU rejected this API key (invalid, expired, or revoked). "
            "Create a new key in the NUMU dashboard under Settings → Connect your AI (MCP)."
        )
    if response.status_code >= 400:
        raise ConnectionError(
            f"NUMU API error while verifying the token (HTTP {response.status_code})."
        )

    body: Any = response.json()
    data = body.get("data") if isinstance(body, dict) and "data" in body else body
    store_id = (data or {}).get("store_id")
    if not store_id:
        raise PermissionError(
            "This API key is not bound to a store. Mint a key from the store's "
            "dashboard (Settings → Connect your AI) so it carries a store binding."
        )

    ctx = TenantContext(
        token=token,
        store_id=str(store_id),
        store_name=data.get("store_name"),
        subdomain=data.get("subdomain"),
        currency=data.get("currency"),
        scopes=data.get("scopes"),
    )
    _info_cache[key] = (now + _INFO_TTL_SECONDS, ctx)
    return ctx


class PassthroughAuthMiddleware:
    """Pure-ASGI middleware enforcing per-request bearer auth (multi-tenant).

    Rejects unauthenticated requests with a JSON 401 before they reach the MCP
    session manager; on success sets the ``TenantContext`` for the request's
    lifetime so everything downstream is store-scoped.
    """

    def __init__(self, app: Any, api_base: str) -> None:
        self._app = app
        self._api_base = api_base

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return

        auth = ""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                auth = value.decode("latin-1")
                break

        if not auth.lower().startswith("bearer "):
            await self._reject(
                send,
                401,
                "Missing NUMU API key. Configure this MCP connection with the "
                "header 'Authorization: Bearer numu_pat_...'. Create a key in "
                "the NUMU dashboard under Settings → Connect your AI (MCP).",
            )
            return

        token = auth[7:].strip()
        try:
            ctx = await resolve(token, self._api_base)
        except PermissionError as exc:
            await self._reject(send, 401, str(exc))
            return
        except ConnectionError as exc:
            await self._reject(send, 502, str(exc))
            return

        reset = _current.set(ctx)
        try:
            await self._app(scope, receive, send)
        finally:
            _current.reset(reset)

    @staticmethod
    async def _reject(send: Any, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
