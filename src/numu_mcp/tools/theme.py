"""Theme-engine tools (NUMU V3 storefront themes).

Let the assistant SEE the active theme (what's editable + current values) and
EDIT it. Edits are written to a **draft** first — exactly like the visual
customizer — and only go live when ``publish_theme`` is called (confirmation-
gated, because it changes the storefront every shopper sees).

Model of the theme payload (``ThemeSettingsV3``):
  {
    "schema_version": 3,
    "theme_id": "...", "theme_slug": "...",
    "global_settings": { <setting_id>: <value>, ... },   # colors, fonts, logo…
    "templates": { <name>: { sections/blocks … } },       # per-page layout
    "section_groups": { ... },                            # header/footer
    "external_theme": { ... }                             # BYOT bundle (optional)
  }

Editing model:
  * ``update_theme_global_settings`` — the common case: merge key/values into
    ``global_settings`` (brand colors, fonts, logo shape/size, …).
  * ``patch_theme_draft`` — power tool: deep-merge an arbitrary partial into the
    draft (use after ``get_theme`` shows you the exact structure, e.g. to change
    one section's settings). Dicts merge recursively; lists/scalars replace.
Both write the DRAFT. Nothing is visible to shoppers until ``publish_theme``.
Undo for theme edits is ``discard_theme_changes`` (revert the whole draft) —
finer-grained undo isn't offered because the payload is a single document.
"""

from __future__ import annotations

from typing import Any

from ..app import mcp
from ..formatting import ValidationError
from ..guards import confirmation_message, consume_token, issue_token
from ..runtime import get_client
from ._base import dumps, err, record_mutation

_EDITOR = "themes/v3/editor"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into ``base`` (in place). Lists/scalars replace."""
    for key, value in patch.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _schema_keys(settings_schema: Any) -> list[dict[str, Any]]:
    """Summarize a settings_schema into a compact list of editable fields."""
    out: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "id" in node and "type" in node:
                out.append(
                    {
                        "id": node.get("id"),
                        "type": node.get("type"),
                        "label": node.get("label"),
                    }
                )
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(settings_schema)
    return out


async def _get_draft() -> dict[str, Any]:
    draft = await get_client().get(f"{_EDITOR}/draft")
    if not isinstance(draft, dict):
        raise ValidationError("The store has no editable V3 theme draft.")
    return draft


async def _save_draft(payload: dict[str, Any], summary: str) -> Any:
    return await get_client().request(
        "PUT",
        f"{_EDITOR}/autosave",
        json={"payload": payload, "change_summary": summary},
        store_scoped=True,
    )


@mcp.tool()
async def get_theme(verbose: bool = False) -> str:
    """See the store's active theme: which settings are editable and their
    current (draft) values, plus the page templates and sections.

    Call this before editing so you use valid setting ids. Global settings are
    the store-wide knobs (brand colors, fonts, logo). Templates hold per-page
    sections.

    Args:
        verbose: Include the full section-level schema too (large). Default off.
    """
    try:
        client = get_client()
        schemas = await client.get(f"{_EDITOR}/schemas", cache_ttl=120)
        draft = await _get_draft()

        result: dict[str, Any] = {
            "theme_id": (schemas or {}).get("theme_id"),
            "theme_slug": (schemas or {}).get("theme_slug"),
            "theme_type": (schemas or {}).get("theme_type"),
            "global_settings_current": draft.get("global_settings", {}),
            "global_settings_editable": _schema_keys(
                (schemas or {}).get("settings_schema", {})
            ),
            "templates": sorted((draft.get("templates") or {}).keys()),
            "section_groups": sorted((draft.get("section_groups") or {}).keys()),
            "note": (
                "Edit with update_theme_global_settings (brand-wide) or "
                "patch_theme_draft (anything). Changes save to a DRAFT; call "
                "publish_theme to make them live."
            ),
        }
        if verbose:
            result["section_schemas"] = (schemas or {}).get("section_schemas", {})
            result["templates_detail"] = draft.get("templates", {})
        return dumps(result)
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "get_theme"})


@mcp.tool()
async def update_theme_global_settings(settings: dict[str, Any]) -> str:
    """Update store-wide theme settings (brand colors, fonts, logo, etc.).

    Merges the given key/values into the theme's global_settings and saves the
    DRAFT (not live). Use get_theme first to see valid setting ids and current
    values. Call publish_theme afterwards to make the change visible to shoppers.

    Args:
        settings: A dict of global setting_id -> new value to merge in.
    """
    try:
        if not isinstance(settings, dict) or not settings:
            raise ValidationError("settings must be a non-empty object.")
        draft = await _get_draft()
        gs = draft.get("global_settings")
        if not isinstance(gs, dict):
            gs = {}
        draft["global_settings"] = _deep_merge(gs, settings)

        await _save_draft(draft, f"MCP: update global settings {list(settings)}")
        await record_mutation(
            "update_theme_global_settings",
            {"keys": list(settings)},
            summary=f"updated theme global settings {list(settings)} (draft)",
        )
        return dumps(
            {
                "updated": True,
                "scope": "draft",
                "changed_keys": list(settings),
                "reminder": "Call publish_theme to make this live for shoppers.",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "update_theme_global_settings"})


@mcp.tool()
async def patch_theme_draft(patch: dict[str, Any]) -> str:
    """Deep-merge an arbitrary partial into the theme draft (power tool).

    Use after get_theme(verbose=True) shows you the exact structure — e.g. to
    change one section's settings: {"templates": {"index": {"sections": {"<id>":
    {"settings": {"heading": "Eid Sale"}}}}}}. Dicts merge recursively; lists and
    scalars replace. Saves to the DRAFT; call publish_theme to go live.

    Args:
        patch: A partial ThemeSettingsV3 object to merge into the current draft.
    """
    try:
        if not isinstance(patch, dict) or not patch:
            raise ValidationError("patch must be a non-empty object.")
        # Guard against clobbering identity fields.
        for locked in ("theme_id", "schema_version"):
            patch.pop(locked, None)
        draft = await _get_draft()
        _deep_merge(draft, patch)

        await _save_draft(draft, f"MCP: patch draft {list(patch)}")
        await record_mutation(
            "patch_theme_draft",
            {"top_level_keys": list(patch)},
            summary=f"patched theme draft {list(patch)} (draft)",
        )
        return dumps(
            {
                "patched": True,
                "scope": "draft",
                "top_level_keys": list(patch),
                "reminder": "Call publish_theme to make this live for shoppers.",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "patch_theme_draft"})


@mcp.tool()
async def publish_theme(confirm: str | None = None) -> str:
    """Publish the theme draft — makes all pending changes LIVE for shoppers.

    Customer-facing, so it's confirmation-gated. Call once without `confirm` to
    get a token, confirm the change with the merchant, then call again with it.
    (Reversible afterwards via the theme's version history in the dashboard.)

    Args:
        confirm: The confirmation token from the first call.
    """
    try:
        args: dict[str, Any] = {}
        if not confirm:
            return confirmation_message(
                "publish_theme", args, issue_token("publish_theme", args)
            )
        if not consume_token(confirm, "publish_theme", args):
            return (
                "Confirmation token is invalid or expired. "
                "Re-run publish_theme to get a new one."
            )
        result = await get_client().post(f"{_EDITOR}/publish")
        await record_mutation(
            "publish_theme", args, summary="published theme draft (now live)"
        )
        return dumps({"published": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "publish_theme"})


@mcp.tool()
async def discard_theme_changes(confirm: str | None = None) -> str:
    """Discard the theme draft — revert all unpublished edits to the live theme.

    This is the undo for theme edits. Confirmation-gated because it permanently
    throws away pending draft changes.

    Args:
        confirm: The confirmation token from the first call.
    """
    try:
        args: dict[str, Any] = {}
        if not confirm:
            token = issue_token("discard_theme_changes", args)
            return confirmation_message("discard_theme_changes", args, token)
        if not consume_token(confirm, "discard_theme_changes", args):
            return (
                "Confirmation token is invalid or expired. "
                "Re-run discard_theme_changes to get a new one."
            )
        result = await get_client().post(f"{_EDITOR}/discard")
        await record_mutation(
            "discard_theme_changes", args, summary="discarded theme draft edits"
        )
        return dumps({"discarded": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        return err(exc, context={"tool": "discard_theme_changes"})
