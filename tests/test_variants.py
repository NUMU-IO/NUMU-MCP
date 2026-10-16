"""Tests for update_variant: the safe way for an AI to change variant prices.

Without it, ChatGPT refused to touch a product with variants (rewriting
`attributes` could wipe the option matrix). The tool must send only the
fields asked for, to that one variant, and record an undo snapshot.
"""

from __future__ import annotations

import asyncio
import json

from numu_mcp.tools import products

PID = "11111111-1111-1111-1111-111111111111"
VID = "22222222-2222-2222-2222-222222222222"


class _FakeClient:
    def __init__(self):
        self.patches = []

    async def get(self, path, **kwargs):
        assert path == f"products/{PID}/variants"
        return [
            {
                "id": VID,
                "option_values": {"size": "M"},
                "price": "250.00",
                "sku": "A-M",
            },
            {"id": "33333333-3333-3333-3333-333333333333", "price": "260.00"},
        ]

    async def patch(self, path, json=None):
        self.patches.append((path, json))
        return {"id": VID, "option_values": {"size": "M"}, "price": "300.00"}


def _run(monkeypatch, **kwargs):
    client = _FakeClient()
    recorded = []

    async def fake_record(*args, **kw):
        recorded.append(kw)

    monkeypatch.setattr(products, "get_client", lambda: client)
    monkeypatch.setattr(products, "record_mutation", fake_record)
    out = asyncio.run(products.update_variant(PID, **kwargs))
    return out, client, recorded


def test_updates_only_the_sent_fields_with_undo(monkeypatch):
    raw, client, recorded = _run(monkeypatch, variant_id=VID, price=300)
    out = json.loads(raw)
    assert client.patches == [(f"products/{PID}/variants/{VID}", {"price": 300})]
    assert out["updated"] is True and out["before"] == {"price": "250.00"}
    assert recorded[0]["undo"]["request"]["json"] == {"price": 250.0}


def test_refuses_a_variant_of_another_product(monkeypatch):
    out, client, _ = _run(
        monkeypatch, variant_id="44444444-4444-4444-4444-444444444444", price=1
    )
    assert client.patches == []
    assert "does not belong to this product" in out
