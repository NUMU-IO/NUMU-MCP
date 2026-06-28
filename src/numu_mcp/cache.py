"""A tiny in-memory TTL cache used *narrowly* and deliberately.

Caching is risky in an operations tool: a stale read can make the AI act on
wrong data. So this is applied only to **stable, low-churn** reads (the store
profile, category list) — never to volatile, decision-critical lists like
orders, products or inventory. Writes invalidate by key substring.
"""

from __future__ import annotations

import time
from typing import Any

_cache: dict[str, tuple[Any, float]] = {}


def get(key: str) -> Any | None:
    """Return a cached value if present and unexpired, else None."""
    hit = _cache.get(key)
    if hit is None:
        return None
    value, expiry = hit
    if time.monotonic() < expiry:
        return value
    _cache.pop(key, None)
    return None


def set(key: str, value: Any, ttl: float = 60.0) -> None:  # noqa: A001 - cache verb
    """Store a value under ``key`` for ``ttl`` seconds."""
    _cache[key] = (value, time.monotonic() + ttl)


def invalidate(substring: str) -> int:
    """Drop every cache entry whose key contains ``substring``. Returns count."""
    keys = [k for k in _cache if substring in k]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


def clear() -> None:
    _cache.clear()
