"""NUMU MCP server entrypoint.

Run with stdio (default, for Claude Desktop / local clients) or streamable HTTP
(for remote clients), selected by the ``NUMU_MCP_TRANSPORT`` env var.

    numu-mcp            # uses env / .env
    python -m numu_mcp.server
"""

from __future__ import annotations

import logging
import sys

from pydantic import ValidationError

from .app import mcp
from .client import NumuClient
from .config import load_settings
from .runtime import init_runtime

# Importing these modules registers all resources, tools and prompts on `mcp`.
from . import prompts as _prompts  # noqa: E402,F401
from . import resources as _resources  # noqa: E402,F401
from . import tools as _tools  # noqa: E402,F401


def _fail(message: str) -> None:
    """Print a setup error to stderr (never stdout — that's the MCP channel)."""
    print(f"[numu-mcp] {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    try:
        settings = load_settings()
    except ValidationError as exc:
        missing = ", ".join(
            str(e["loc"][0]) for e in exc.errors() if e.get("type") == "missing"
        )
        hint = (
            f" Missing required variable(s): {missing}." if missing else ""
        )
        _fail(
            "Invalid configuration."
            + hint
            + " Set NUMU_MCP_BASE_URL, NUMU_MCP_STORE_ID and "
            "NUMU_MCP_ACCESS_TOKEN (see .env.example). Details:\n"
            + str(exc)
        )
        return

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    client = NumuClient(settings)
    init_runtime(settings, client)

    if settings.transport == "streamable-http":
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        logging.getLogger("numu-mcp").info(
            "Starting NUMU MCP server (HTTP) on %s:%s for store %s",
            settings.host,
            settings.port,
            settings.store_id,
        )
        mcp.run(transport="streamable-http")
    else:
        logging.getLogger("numu-mcp").info(
            "Starting NUMU MCP server (stdio) for store %s", settings.store_id
        )
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
