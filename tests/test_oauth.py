"""Tests for the OAuth sign-in that lets ChatGPT and Claude connect by URL.

The flow hands a live store key to whoever completes it, so the gates are
covered: only ChatGPT/Claude callbacks receive codes, codes are single use,
PKCE is enforced, and a revoked key cannot be smuggled through consent.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from numu_mcp import oauth, tenancy
from numu_mcp.app import mcp

ISSUER = "https://mcp.example"
CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "v" * 43


@pytest.fixture
def client(monkeypatch):
    settings = SimpleNamespace(
        public_url=ISSUER,
        hub_url="https://hub.example",
        api_base="https://api.example/api/v1",
    )
    monkeypatch.setattr(oauth, "get_settings", lambda: settings)

    async def fake_resolve(token, api_base):
        if token != "numu_pat_good":
            raise PermissionError("revoked")

    monkeypatch.setattr(tenancy, "resolve", fake_resolve)
    app = tenancy.PassthroughAuthMiddleware(
        mcp.streamable_http_app(),
        api_base=settings.api_base,
        public_url=ISSUER,
        public_paths=oauth.PUBLIC_PATHS,
    )
    return TestClient(app, base_url=ISSUER, follow_redirects=False)


def _consent_request(client, redirect_uri=CHATGPT):
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "https://chatgpt.com/oauth/client.json",
            "redirect_uri": redirect_uri,
            "state": "s1",
            "code_challenge": oauth._s256(VERIFIER),
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 302
    hub = urlsplit(r.headers["location"])
    assert (hub.netloc, hub.path) == ("hub.example", "/mcp/authorize")
    query = parse_qs(hub.query)
    assert query["client"] == ["ChatGPT"]
    return query["request"][0]


def _back_to_client(response):
    assert response.status_code == 303
    back = urlsplit(response.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CHATGPT
    params = parse_qs(back.query)
    assert params["state"] == ["s1"] and params["iss"] == [ISSUER]
    return params


def _sign_in(client):
    request_id = _consent_request(client)
    params = _back_to_client(
        client.post(
            "/authorize/complete",
            data={"request": request_id, "token": "numu_pat_good"},
        )
    )
    return params["code"][0]


def _exchange(client, code, verifier=VERIFIER):
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CHATGPT,
            "client_id": "https://chatgpt.com/oauth/client.json",
            "code_verifier": verifier,
        },
    )


def test_discovery(client):
    r = client.post("/mcp", json={})
    assert r.status_code == 401
    assert (
        r.headers["www-authenticate"]
        == f'Bearer resource_metadata="{ISSUER}/.well-known/oauth-protected-resource"'
    )

    resource = client.get("/.well-known/oauth-protected-resource").json()
    assert resource["resource"] == f"{ISSUER}/mcp"
    assert resource["authorization_servers"] == [ISSUER]

    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["issuer"] == ISSUER
    assert "S256" in server["code_challenge_methods_supported"]


def test_sign_in_returns_the_store_key_once(client):
    assert _exchange(client, _sign_in(client), verifier="wrong").json() == {
        "error": "invalid_grant"
    }

    code = _sign_in(client)
    r = _exchange(client, code)
    assert r.status_code == 200
    assert r.json() == {"access_token": "numu_pat_good", "token_type": "Bearer"}
    assert r.headers["cache-control"] == "no-store"
    assert _exchange(client, code).json() == {"error": "invalid_grant"}


def test_unknown_redirects_never_get_a_code(client):
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "x",
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": "c",
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 400 and "location" not in r.headers

    assert (
        client.post(
            "/register", json={"redirect_uris": ["https://evil.example/cb"]}
        ).status_code
        == 400
    )
    registered = client.post("/register", json={"redirect_uris": [CHATGPT]})
    assert registered.status_code == 201 and registered.json()["client_id"]


def test_deny_bad_key_and_stale_request(client):
    denied = _back_to_client(
        client.post(
            "/authorize/complete",
            data={"request": _consent_request(client), "deny": "1"},
        )
    )
    assert denied["error"] == ["access_denied"]

    revoked = _back_to_client(
        client.post(
            "/authorize/complete",
            data={"request": _consent_request(client), "token": "numu_pat_bad"},
        )
    )
    assert revoked["error"] == ["access_denied"] and "code" not in revoked

    assert (
        client.post("/authorize/complete", data={"request": "nope"}).status_code == 400
    )


def test_plugin_archive(client):
    r = client.get("/chatgpt-plugin.zip")
    assert r.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(archive.namelist()) == {"plugin.json", "mcp.json", "assets/icon.png"}
    servers = json.loads(archive.read("mcp.json"))["mcpServers"]
    assert servers["numu"]["url"] == f"{ISSUER}/mcp"


def test_parked_requests_are_bounded(monkeypatch):
    monkeypatch.setattr(oauth, "_MAX_ENTRIES", 3)
    store: dict = {}
    keys = [oauth._put(store, {"n": i}, 60) for i in range(5)]
    assert len(store) == 3
    assert oauth._take(store, keys[0]) is None
    assert oauth._take(store, keys[4])["n"] == 4
