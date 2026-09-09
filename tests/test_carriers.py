"""Tests for carrier resolution in the MCP server.

This service had no test suite and its CD has no rollback — a bad deploy
is ~15 minutes of 502s — so the carrier slice is covered before it ships.

The bug these guard against: `create_shipment` defaulted to
`carrier="bosta"`, and the API silently normalised any unrecognised slug
to Bosta too. Between them, an AI-driven shipment with a typo'd or
omitted carrier booked a real Bosta delivery.
"""

from __future__ import annotations

import pytest

from numu_mcp import carriers
from numu_mcp.formatting import SHIPMENT_CARRIERS, ValidationError, validate_choice


@pytest.fixture(autouse=True)
def _clear_cache():
    carriers.reset_cache()
    yield
    carriers.reset_cache()


class _FakeClient:
    def __init__(self, payload, *, fail=False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    async def get(self, path, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("network down")
        return self.payload


def _patch_client(monkeypatch, client):
    import numu_mcp.runtime as runtime

    monkeypatch.setattr(runtime, "get_client", lambda: client)


CATALOG = {
    "data": [
        {
            "slug": "bosta",
            "name_en": "Bosta",
            "capabilities": {"supports_cancel": True},
        },
        {
            "slug": "mylerz",
            "name_en": "Mylerz",
            "capabilities": {"supports_cancel": False},
        },
        {"slug": "jt", "name_en": "J&T Express", "capabilities": {}},
    ]
}


class TestKnownCarriers:
    @pytest.mark.asyncio
    async def test_reads_the_api_registry(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(CATALOG))
        assert await carriers.known_carriers() == ("bosta", "mylerz", "jt")

    @pytest.mark.asyncio
    async def test_falls_back_when_the_api_fails(self, monkeypatch):
        """A catalog lookup failing must not break the tools."""
        _patch_client(monkeypatch, _FakeClient(None, fail=True))
        assert await carriers.known_carriers() == SHIPMENT_CARRIERS

    @pytest.mark.asyncio
    async def test_falls_back_on_an_empty_catalog(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient({"data": []}))
        assert await carriers.known_carriers() == SHIPMENT_CARRIERS

    @pytest.mark.asyncio
    async def test_result_is_cached(self, monkeypatch):
        client = _FakeClient(CATALOG)
        _patch_client(monkeypatch, client)
        await carriers.known_carriers()
        await carriers.known_carriers()
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient({"data": "not-a-list"}))
        assert await carriers.known_carriers() == SHIPMENT_CARRIERS


class TestFallbackMirrorsTheRegistry:
    def test_fallback_is_not_empty(self):
        assert SHIPMENT_CARRIERS

    @pytest.mark.asyncio
    async def test_fallback_is_a_subset_of_the_live_catalog(self, monkeypatch):
        """Listing a carrier the API lacks would let a tool submit a slug
        the API rejects with 400."""
        _patch_client(monkeypatch, _FakeClient(CATALOG))
        live = set(await carriers.known_carriers())
        assert set(SHIPMENT_CARRIERS) <= live


class TestCarrierValidation:
    def test_rejects_an_unknown_slug(self):
        with pytest.raises(ValidationError):
            validate_choice("bostaa", SHIPMENT_CARRIERS, "carrier")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_choice("", SHIPMENT_CARRIERS, "carrier")

    @pytest.mark.parametrize("slug", SHIPMENT_CARRIERS)
    def test_accepts_known_slugs(self, slug):
        assert validate_choice(slug, SHIPMENT_CARRIERS, "carrier") == slug


class TestCreateShipmentSignature:
    def test_carrier_has_no_default(self):
        """The core regression: an omitted carrier must not become Bosta."""
        import inspect

        from numu_mcp.tools import shipping

        fn = getattr(shipping.create_shipment, "fn", shipping.create_shipment)
        param = inspect.signature(fn).parameters["carrier"]
        assert param.default is inspect.Parameter.empty, (
            "create_shipment.carrier must stay required — a default books a "
            "real delivery with a carrier the merchant never chose"
        )


class TestCapabilities:
    @pytest.mark.asyncio
    async def test_returns_declared_capabilities(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(CATALOG))
        assert (await carriers.carrier_capabilities("bosta"))["supports_cancel"] is True

    @pytest.mark.asyncio
    async def test_unknown_carrier_returns_empty(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(CATALOG))
        assert await carriers.carrier_capabilities("aramex") == {}

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(None, fail=True))
        assert await carriers.carrier_capabilities("bosta") == {}
