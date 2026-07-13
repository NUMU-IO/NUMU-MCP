# numu-mcp

**NUMU MCP server** — connect any AI (Claude, ChatGPT, Cursor, agent SDKs…) to a NUMU store over the [Model Context Protocol](https://modelcontextprotocol.io).

```
AI client ──streamable HTTP──▶ numu-mcp ──REST /api/v1──▶ NUMU-api
           Authorization: Bearer numu_pat_...  (forwarded verbatim)
```

## Design

- **Stateless, pass-through auth.** The client's `Authorization: Bearer numu_pat_…` header (a NUMU personal access token minted in the merchant hub under **Settings → Connect your AI (MCP)**) is forwarded to NUMU-api on every call. NUMU-api owns validation, per-store binding, scopes, tenancy (RLS) and rate limits. This process stores **no** credentials and **no** session state, so it scales behind a plain load balancer.
- **Per-store keys.** A token is bound to exactly one store; tools never take a `store_id` parameter. `get_store_snapshot` tells the model which store it is operating on and with which scopes.
- **Curated, task-shaped tools** (20 in the MVP): store snapshot, catalog (list/get/create/update products, categories), product image upload (URL or base64 → R2), orders (read + confirmed status change), abandoned checkouts, and the analytics suite (overview, sales chart, top products, funnel, traffic sources, marketing attribution, COD rejections).
- **Safety:** read-only tools are annotated `readOnlyHint`; outward-facing mutations (e.g. `update_order_status`) require `confirm=true` and instruct the model to ask the merchant first. Scope enforcement happens server-side in NUMU-api — a `catalog:read`-only key gets a 403 on writes no matter what a client sends.

## Run

```bash
pip install -e .
NUMU_API_URL=http://127.0.0.1:8001/api/v1 python -m numu_mcp   # serves http://127.0.0.1:8100/mcp
```

| Env var | Default | Notes |
|---|---|---|
| `NUMU_API_URL` | `http://127.0.0.1:8001/api/v1` | **must include `/api/v1`** |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8100` | |
| `MCP_KEY_INFO_TTL` | `300` | seconds to cache token→store lookups |
| `MCP_REQUEST_TIMEOUT` | `30` | upstream timeout (s) |

Production: `docker build -t numu-mcp .` and run behind the existing droplet nginx/Cloudflare at `mcp.numueg.app`.

## Connect a client

- **Claude.ai** → Settings → Connectors → Add custom connector → URL `https://mcp.numueg.app/mcp` + header `Authorization: Bearer numu_pat_…`
- **Claude Code:** `claude mcp add --transport http numu https://mcp.numueg.app/mcp --header "Authorization: Bearer numu_pat_…"`
- **ChatGPT:** Settings → Apps & Connectors → Developer mode → new app with the same URL/header.
- **Cursor / anything MCP:** `{"mcpServers":{"numu":{"url":"…/mcp","headers":{"Authorization":"Bearer …"}}}}`

The merchant hub's **Settings → Connect your AI (MCP)** page generates these snippets with the real key.

## Backend counterpart (NUMU-api)

- `personal_access_tokens` gained a `scopes` JSONB column (`pat_scopes_20260713` migration).
- Central enforcement in `_resolve_pat_principal`: hard store binding, default-deny scope map (`required_scope_for`), and PATs can never manage PATs.
- `GET /api/v1/auth/api-key/me` identifies the calling token (store, scopes, currency) — used by `get_store_snapshot`.

## Roadmap

Phase 2: SEO/content tools, marketing + pixels, theme customizer (draft→preview→confirm→publish), trust-network risk tools, elicitation-based confirmations, audit surfacing in the hub. Phase 3: OAuth 2.1 + DCR for the Claude connectors directory / ChatGPT app store. See `REports/NUMU-MCP-report.md` in the workspace root.
