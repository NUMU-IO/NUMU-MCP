"""Shared runtime singletons.

FastMCP registers tools/resources/prompts at import time, but they only need
the HTTP client when actually invoked. We hold the client and settings here and
initialise them once in ``server.main()`` before the transport starts.
"""

from __future__ import annotations

from .client import NumuClient
from .config import Settings

_settings: Settings | None = None
_client: NumuClient | None = None


def init_runtime(settings: Settings, client: NumuClient) -> None:
    global _settings, _client
    _settings = settings
    _client = client


def get_client() -> NumuClient:
    if _client is None:
        raise RuntimeError("Runtime not initialised — call init_runtime() first.")
    return _client


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Runtime not initialised — call init_runtime() first.")
    return _settings


def current_store_id() -> str:
    """The store this request targets.

    Multi-tenant mode: the store bound to the caller's token (set per-request
    by the pass-through middleware). Single-tenant mode: the env-configured
    store.
    """
    from . import tenancy

    return tenancy.current_store_id() or str(get_settings().store_id)
