"""Human-in-the-loop confirmation gate for irreversible actions.

A small, in-process two-step gate for the few actions that can't be undone
(delete a product, refund an order, cancel an order). The first call returns a
short-lived token and a summary; the action only runs when the same call is
repeated with that token. Reversible actions don't use this — they rely on the
audit log + undo instead.

This is belt-and-suspenders on top of the model's own "confirm with the user"
behaviour: even if the model skips asking, it physically cannot perform an
irreversible action in a single step.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

# Actions that are not cleanly reversible and therefore require confirmation.
IRREVERSIBLE_TOOLS = {"delete_product", "refund_order", "cancel_order"}

_TTL_SECONDS = 300.0
_pending: dict[str, dict[str, Any]] = {}


def _purge_expired() -> None:
    now = time.monotonic()
    for tok in [t for t, a in _pending.items() if a["expires_at"] < now]:
        _pending.pop(tok, None)


def _issuing_store() -> str:
    """The store the token belongs to — multi-tenant tokens must not cross."""
    try:
        from .runtime import current_store_id

        return current_store_id()
    except Exception:  # noqa: BLE001
        return "default"


def issue_token(tool_name: str, arguments: dict[str, Any]) -> str:
    """Create and store a one-time confirmation token for an action."""
    _purge_expired()
    store = _issuing_store()
    # Vary the token by store, tool, args and a monotonic stamp so it's unique.
    seed = (
        f"{store}:{tool_name}:"
        f"{json.dumps(arguments, sort_keys=True, default=str)}:{time.monotonic()}"
    )
    token = hashlib.sha256(seed.encode()).hexdigest()[:16]
    _pending[token] = {
        "store": store,
        "tool_name": tool_name,
        "arguments": arguments,
        "expires_at": time.monotonic() + _TTL_SECONDS,
    }
    return token


def consume_token(token: str, tool_name: str, arguments: dict[str, Any]) -> bool:
    """Validate and consume a token. True only if it matches this exact action."""
    _purge_expired()
    action = _pending.get(token)
    if action is None:
        return False
    if action.get("store", "default") != _issuing_store():
        return False
    if action["tool_name"] != tool_name:
        return False
    if json.dumps(action["arguments"], sort_keys=True, default=str) != json.dumps(
        arguments, sort_keys=True, default=str
    ):
        return False
    _pending.pop(token, None)  # one-time use
    return True


def confirmation_message(tool_name: str, arguments: dict[str, Any], token: str) -> str:
    """A clear, model-facing prompt asking the user to confirm."""
    return (
        f"⚠️ This is an irreversible action ({tool_name}) and needs confirmation.\n"
        f"Details: {json.dumps(arguments, ensure_ascii=False, default=str)}\n\n"
        "Confirm with the user first. To proceed, call the same tool again with "
        f"confirm='{token}'. This token expires in 5 minutes."
    )
