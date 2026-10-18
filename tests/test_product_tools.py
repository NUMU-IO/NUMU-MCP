"""Tests for the full product toolset: create with options + variants, add and
remove one variant, and upload an image from a link.

ChatGPT refused to create books with their six Format x Paper variants
because create_product had no way to send them. These pin the shapes the API
expects and the guards that stop a half-built product.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from numu_mcp.formatting import ValidationError
from numu_mcp.tools import products

PID = "11111111-1111-1111-1111-111111111111"
VID = "22222222-2222-2222-2222-222222222222"
OPTIONS = [
    {"name": "Format", "values": ["Paperback", "Hardcover", "Special edition"]},
    {"name": "Paper", "values": ["White", "Creamy"]},
]


def _variants():
    prices = {"Paperback": 180, "Hardcover": 250, "Special edition": 320}
    return [
        {"option_values": {"Format": f, "Paper": p}, "price": prices[f]}
        for f in prices
        for p in ("White", "Creamy")
    ]


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        if path == "products":
            return {"id": PID, "name": json["name"], "variants": json.get("variants")}
        return {"id": VID, **json}

    async def delete(self, path):
        self.calls.append(("DELETE", path, None))

    async def request(self, method, path, files=None, **kw):
        self.calls.append((method, path, files))
        return {"url": "https://cdn.example/x.webp"}


@pytest.fixture
def client(monkeypatch):
    fake = _FakeClient()

    async def fake_record(*args, **kw):
        pass

    monkeypatch.setattr(products, "get_client", lambda: fake)
    monkeypatch.setattr(products, "record_mutation", fake_record)
    return fake


def test_create_product_with_six_variants(client):
    out = json.loads(
        asyncio.run(
            products.create_product(
                name="Vicious", options=OPTIONS, variants=_variants()
            )
        )
    )
    method, path, body = client.calls[0]
    assert (method, path) == ("POST", "products")
    assert body["price"] == 180  # cheapest variant when no price is given
    assert [o["position"] for o in body["options"]] == [0, 1]
    assert len(body["variants"]) == 6
    assert out["created"] is True and len(out["variants"]) == 6


def test_bad_matrix_is_refused_before_the_api(client):
    bad = _variants()
    bad[0]["option_values"]["Paper"] = "Glossy"
    out = asyncio.run(products.create_product(name="X", options=OPTIONS, variants=bad))
    assert "'Paper: Glossy' is not one of the options" in out
    assert client.calls == []
    with pytest.raises(ValidationError):
        products._check_options_variants(OPTIONS * 2, None)
    with pytest.raises(ValidationError):
        products._check_options_variants(None, [{"option_values": {}}])


def test_create_and_delete_one_variant(client):
    asyncio.run(
        products.create_variant(
            PID, {"Format": "Paperback", "Paper": "White"}, price=199, sku="V-1"
        )
    )
    assert client.calls[0][:2] == ("POST", f"products/{PID}/variants")
    assert client.calls[0][2]["sku"] == "V-1"

    first = asyncio.run(products.delete_variant(PID, VID))
    assert not any(c[0] == "DELETE" for c in client.calls)  # needs confirmation
    assert "confirm" in first.lower()


def test_image_links_must_be_public_https():
    for url in (
        "http://example.com/a.png",
        "https://127.0.0.1/a.png",
        "https://localhost/a.png",
    ):
        with pytest.raises(ValidationError):
            asyncio.run(products._download_public_image(url))


def test_add_product_image_uploads_the_file(client, monkeypatch):
    async def fake_download(url):
        return b"\x89PNG", "image/png", "cover.png"

    monkeypatch.setattr(products, "_download_public_image", fake_download)
    out = json.loads(
        asyncio.run(products.add_product_image(PID, "https://example.com/cover.png"))
    )
    method, path, files = client.calls[0]
    assert (method, path) == ("POST", f"products/{PID}/images")
    assert files == {"file": ("cover.png", b"\x89PNG", "image/png")}
    assert out["uploaded"] is True
