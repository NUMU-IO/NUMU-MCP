"""OAuth sign-in for the hosted server, so ChatGPT and Claude connect by URL.

ChatGPT's "Add custom MCP server" offers OAuth or no auth — never a pasted
header — so a merchant could not connect with a ``numu_pat_`` key. This module
lets them paste ``https://mcp.numueg.app/mcp`` (or upload the plugin archive)
and sign in instead:

1. A request without a key gets a 401 whose ``WWW-Authenticate`` points at
   ``/.well-known/oauth-protected-resource`` (RFC 9728). That names this server
   as its own authorization server (RFC 8414 metadata below).
2. The client registers (DCR) or uses its metadata-document client id (CIMD)
   and opens ``/authorize``. The request is parked and the merchant is sent to
   the hub's consent page, ``{hub}/mcp/authorize``.
3. The merchant picks a store and approves. The hub mints an ordinary
   ``numu_pat_`` key with the merchant's own session and POSTs it to
   ``/authorize/complete``, which hands the client a one-time code.
4. The client trades the code at ``/token`` (PKCE S256) and gets that key as
   its access token. Every later call is a normal pass-through request.

The access token IS the store key, so revoking it in the hub (Settings →
Connect your AI) disconnects the client; its next call gets the 401 challenge
and it asks the merchant to sign in again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from . import tenancy
from .app import mcp
from .runtime import get_settings

# The only addresses a code may be sent to: ChatGPT's callbacks
# (developers.openai.com/plugins/build/auth) and Claude's. DCR and CIMD
# clients are both checked against this list instead of a client registry.
_REDIRECT = re.compile(
    r"https://chatgpt\.com/(connector_platform_oauth_redirect|connector/oauth/[A-Za-z0-9_-]+)"
    r"|https://claude\.(ai|com)/api/mcp/auth_callback"
)

#: Paths the auth middleware lets through without a key.
PUBLIC_PATHS = frozenset(
    {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/register",
        "/authorize",
        "/authorize/complete",
        "/token",
        "/chatgpt-plugin.zip",
    }
)

_REQUEST_TTL = 900  # the merchant may have to log in to the hub first
_CODE_TTL = 300
# /authorize needs no key, so a flood must not grow memory without bound
# (the container is capped at 384 MB). The oldest entries go first.
_MAX_ENTRIES = 10_000
_ICON = Path(__file__).with_name("assets") / "icon.png"

# ponytail: in memory, one uvicorn process. A restart mid sign-in only makes
# the merchant start again; move to Redis if this ever runs more than one.
_pending: dict[str, dict[str, Any]] = {}
_codes: dict[str, dict[str, Any]] = {}


def _put(store: dict[str, dict[str, Any]], value: dict[str, Any], ttl: int) -> str:
    now = time.time()
    for key in [k for k, v in store.items() if v["exp"] < now]:
        del store[key]
    while len(store) >= _MAX_ENTRIES:
        store.pop(next(iter(store)))
    key = secrets.token_urlsafe(32)
    store[key] = {**value, "exp": now + ttl}
    return key


def _take(store: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Single use: the entry is gone after the first read, valid or not."""
    item = store.pop(key, None)
    return item if item and item["exp"] >= time.time() else None


def _issuer() -> str:
    return get_settings().public_url


def _form(body: bytes) -> dict[str, str]:
    return dict(parse_qsl(body.decode("utf-8", "replace")))


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _back(
    redirect_uri: str, state: str | None, status: int = 302, **params: str
) -> Response:
    """Return to the client. Every response carries ``iss`` (RFC 9207)."""
    params["iss"] = _issuer()
    if state is not None:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=status)


def _page(en: str, ar: str, status: int) -> Response:
    return PlainTextResponse(f"{ar}\n{en}", status_code=status)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def protected_resource(request: Request) -> Response:
    issuer = _issuer()
    return JSONResponse(
        {
            "resource": f"{issuer}/mcp",
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "resource_name": "NUMU",
        }
    )


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def authorization_server(request: Request) -> Response:
    issuer = _issuer()
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }
    )


@mcp.custom_route("/register", methods=["POST"])
async def register(request: Request) -> Response:
    try:
        meta = await request.json()
    except ValueError:
        meta = None
    uris = meta.get("redirect_uris") if isinstance(meta, dict) else None
    if not (
        isinstance(uris, list)
        and uris
        and all(isinstance(u, str) and _REDIRECT.fullmatch(u) for u in uris)
    ):
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": "Only ChatGPT and Claude can connect to NUMU.",
            },
            status_code=400,
        )
    # ponytail: stateless DCR — nothing is stored. /authorize checks the
    # redirect against _REDIRECT, which is the real gate.
    return JSONResponse(
        {
            "client_id": f"numu-{secrets.token_urlsafe(16)}",
            "client_id_issued_at": int(time.time()),
            "client_name": meta.get("client_name"),
            "redirect_uris": uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
    )


@mcp.custom_route("/authorize", methods=["GET"])
async def authorize(request: Request) -> Response:
    q = request.query_params
    redirect_uri = q.get("redirect_uri", "")
    if not _REDIRECT.fullmatch(redirect_uri):
        # Never redirect to an unknown address: that is how codes get stolen.
        return _page(
            "This app can't connect to NUMU.", "التطبيق ده مينفعش يتربط بـ NUMU.", 400
        )
    state = q.get("state")
    if (
        q.get("response_type") != "code"
        or not q.get("client_id")
        or not q.get("code_challenge")
        or q.get("code_challenge_method") != "S256"
    ):
        return _back(
            redirect_uri,
            state,
            error="invalid_request",
            error_description="client_id and a PKCE S256 code_challenge are required.",
        )
    request_id = _put(
        _pending,
        {
            "client_id": q["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": q["code_challenge"],
        },
        _REQUEST_TTL,
    )
    client = "ChatGPT" if redirect_uri.startswith("https://chatgpt.com/") else "Claude"
    query = urlencode({"request": request_id, "client": client})
    return RedirectResponse(
        f"{get_settings().hub_url}/mcp/authorize?{query}", status_code=302
    )


@mcp.custom_route("/authorize/complete", methods=["POST"])
async def complete(request: Request) -> Response:
    """The hub's consent page posts here: ``request`` plus ``token`` or ``deny``."""
    form = _form(await request.body())
    pending = _take(_pending, form.get("request", ""))
    if pending is None:
        return _page(
            "This sign-in link expired. Start again from ChatGPT or Claude.",
            "رابط الدخول ده انتهى. ابدأ تاني من ChatGPT أو Claude.",
            400,
        )
    redirect_uri, state = pending["redirect_uri"], pending["state"]
    if form.get("deny"):
        return _back(redirect_uri, state, 303, error="access_denied")
    token = form.get("token", "")
    try:
        if not token.startswith("numu_pat_"):
            raise PermissionError
        await tenancy.resolve(token, get_settings().api_base)
    except PermissionError:
        return _back(redirect_uri, state, 303, error="access_denied")
    except ConnectionError:
        return _back(redirect_uri, state, 303, error="temporarily_unavailable")
    code = _put(
        _codes,
        {
            "token": token,
            "client_id": pending["client_id"],
            "redirect_uri": redirect_uri,
            "code_challenge": pending["code_challenge"],
        },
        _CODE_TTL,
    )
    return _back(redirect_uri, state, 303, code=code)


@mcp.custom_route("/token", methods=["POST"])
async def token(request: Request) -> Response:
    form = _form(await request.body())
    if form.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    code = _take(_codes, form.get("code", ""))
    if (
        code is None
        or form.get("client_id", code["client_id"]) != code["client_id"]
        or form.get("redirect_uri", code["redirect_uri"]) != code["redirect_uri"]
        or not hmac.compare_digest(
            _s256(form.get("code_verifier", "")).encode(),
            code["code_challenge"].encode(),
        )
    ):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse(
        {"access_token": code["token"], "token_type": "Bearer"},
        headers={"Cache-Control": "no-store"},
    )


@mcp.custom_route("/chatgpt-plugin.zip", methods=["GET"])
async def chatgpt_plugin(request: Request) -> Response:
    """The archive for ChatGPT's "Upload plugin archive": the server plus OAuth."""
    issuer = _issuer()
    plugin = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "numu",
        "version": "1.0.0",
        "description": (
            "Run your NUMU store from ChatGPT: products, orders, customers, "
            "analytics and more."
        ),
        "homepage": "https://numueg.app",
        "extensions": {
            "com.openai": {
                "interface": {
                    "displayName": "NUMU",
                    "shortDescription": "Run your NUMU store from ChatGPT",
                    "longDescription": (
                        "Manage your NUMU store in plain language: add and edit "
                        "products, check orders and customers, read sales reports, "
                        "run discounts and update your theme. Sign in with your "
                        "NUMU account to connect."
                    ),
                    "developerName": "NUMU",
                    "websiteURL": "https://numueg.app",
                    "composerIcon": "./assets/icon.png",
                    "logo": "./assets/icon.png",
                }
            }
        },
    }
    servers = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "numu": {
                "type": "streamable-http",
                "url": f"{issuer}/mcp",
                "extensions": {
                    "com.openai": {"auth": {"type": "oauth", "client": {"mode": "dcr"}}}
                },
            }
        },
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(plugin, indent=2))
        archive.writestr("mcp.json", json.dumps(servers, indent=2))
        archive.write(_ICON, "assets/icon.png")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="numu-chatgpt-plugin.zip"'
        },
    )
