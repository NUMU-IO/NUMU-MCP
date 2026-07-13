"""NUMU MCP server — connect any AI to a NUMU store.

Streamable HTTP, stateless (2026-07-28 spec direction), pass-through auth:
the client's `Authorization: Bearer numu_pat_...` header is forwarded to
NUMU-api, which owns validation, store binding, scopes, tenancy, and rate
limits. This process stores no merchant credentials and no session state.

Run (dev):      python -m numu_mcp
Run (prod):     uvicorn numu_mcp.server:app --host 0.0.0.0 --port 8100
"""

from fastmcp import FastMCP

from .config import settings

INSTRUCTIONS = """\
You are connected to a single NUMU store (NUMU is an e-commerce platform for
the Egyptian/MENA market — think "Shopify for Egypt"). The API key this
connection carries is bound to exactly one store and a set of scopes.

Start every session by calling `get_store_snapshot` to learn the store's
identity, currency, and what you are allowed to do.

Conventions:
- All money values are integer cents (piastres); display strings are provided.
- Content fields are bilingual: {"en": ..., "ar": ...}. Egyptian merchants
  usually prefer Arabic-first.
- Destructive or outward-facing actions require explicit confirm flags.
- If a tool reports a missing scope, tell the merchant to mint a key with that
  scope in the NUMU dashboard (Settings -> Connect your AI).
"""

mcp = FastMCP(
    name="NUMU",
    instructions=INSTRUCTIONS,
    version="0.1.0",
    website_url="https://numueg.app",
)

# Tool modules register themselves against `mcp` on import.
from . import tools  # noqa: E402,F401

app = mcp.http_app(stateless_http=True, transport="http")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
