# NUMU MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes a
single **NUMU** merchant store to any MCP-compatible AI (Claude Desktop, Claude
in the IDE, or any other client). Once connected, the AI can **read** the store's
state and **take real merchant actions** — orders, products, inventory,
customers, discounts, analytics and shipping — exactly as a human operator could,
bounded by the merchant's own permissions and plan.

Built with the official **MCP Python SDK** (FastMCP) so it stays consistent with
the FastAPI backend it talks to. No Node.js required.

> 📖 For a deep, end-to-end architecture walkthrough (request lifecycle, auth,
> plan-awareness, extending the server), see **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**.

---

## How it works

```
┌────────────┐   MCP (stdio / HTTP)   ┌───────────────┐   HTTPS + PAT   ┌─────────────┐
│ Claude /   │ ─────────────────────► │ numu-mcp      │ ──────────────► │  NUMU API   │
│ MCP client │                        │ (this server) │   Bearer token  │  (FastAPI)  │
└────────────┘                        └───────────────┘                 └─────────────┘
```

- **Authentication** uses a **Personal Access Token (PAT)** — a long-lived,
  hashed API token minted by the store owner. The MCP server sends it as
  `Authorization: Bearer numu_pat_…`. The backend resolves the PAT to the owning
  user and applies the **same RBAC permissions and subscription-plan limits** as
  a normal login. If an action isn't allowed, the AI receives a clear
  "Not allowed" message.
- **One server = one store.** The store id and origin are configured per
  instance. Each merchant runs (or is given) their own server pointed at their
  store's subdomain — which is also how the backend resolves the tenant.
- **Money** is handled for you: amounts are shown formatted (`1,234.56 EGP`);
  when a tool asks for an amount you pass major units (e.g. `49.99`).

---

## What it exposes

### Resources (read context)
| URI | Description |
|-----|-------------|
| `numu://store/overview` | Store profile, status, plan & usage, currency, payment methods, product/order/customer counts, inventory health. |
| `numu://store/capabilities` | The live capability map: active plan, usage vs. limits, enabled plan features, the connected user's permission domains, and a **per-tool availability map** (with reasons when blocked). |
| `numu://products/catalog` | First 100 products with stock levels, price, category and status. |
| `numu://system/health` | MCP↔API health: config, reachability + latency, token validity. |
| `numu://audit/recent` | The audit trail of changes the AI made (most recent first). |

### Tools (actions)
| Domain | Tools |
|--------|-------|
| **Store / plan / audit** | `get_capabilities`, `refresh_capabilities`, `get_plan_and_usage`, `list_categories`, `list_recent_actions`, `undo_last_action` |
| **Categories / collections** | `create_category`, `update_category`, `delete_category` |
| **Theme engine (V3)** | `get_theme`, `update_theme_global_settings`, `patch_theme_draft`, `publish_theme`, `discard_theme_changes` |
| **Intelligence** | `analyze_customer_segments` (RFM), `analyze_inventory_health` (days-of-cover), `suggest_price_adjustments` |
| **Batch** | `batch_get_orders`, `batch_get_products`, `batch_update_order_status`, `batch_adjust_inventory` |
| **Orders** | `list_orders`, `get_order`, `update_order_status`, `cancel_order`, `refund_order` |
| **Products** | `list_products`, `get_product`, `create_product`, `update_product`, `delete_product` |
| **Inventory** | `list_inventory`, `inventory_stats`, `adjust_inventory` |
| **Customers** | `search_customers`, `get_customer`, `get_customer_orders` |
| **Discounts** | `create_discount`, `list_discounts`, `deactivate_discount` |
| **Analytics** | `sales_report`, `revenue_over_time`, `top_products` |
| **Shipping** | `list_shipments`, `create_shipment`, `get_shipment`, `track_shipment` |

### Prompts (workflows)
| Name | What it does |
|------|--------------|
| `daily_merchant_briefing` | Today's revenue, orders needing attention, and stock alerts. |
| `restock_alert` | Prioritized list of out-of-stock and low-stock products. |

---

## Plan & permission awareness

This server behaves like a real integration, not a static tool list. On demand
(cached ~60s) it probes the merchant's **live plan, usage and limits**
(`/plan/usage`) and the connected user's **effective permissions** (`/staff/me`),
and maps them onto the tool surface:

- **`get_capabilities`** / the `numu://store/capabilities` resource tells the AI
  which tools are available on the current plan/role and why others are blocked
  (e.g. *"Your plan (Demo) does not include 'discount_codes'."*).
- **Plan-gated tools** pre-flight before acting: discount tools require the
  `discount_codes` feature; analytics tools require the `analytics` feature. If
  the plan doesn't include them, the AI gets an actionable upgrade message
  instead of a raw error.
- **Permission-gated tools** respect the connecting user's permission domains
  (owners bypass, as in the backend).
- **Plan-limit errors** from the API (e.g. product/order caps) are rewritten
  with upgrade guidance so the AI can explain the fix to the merchant.

The probe **fails open**: if capabilities can't be loaded, calls still go
through and the backend remains the single source of truth for authorization.

---

## Intelligence & safety layer

Beyond CRUD, the server adds a thin layer of *good software engineering* (no
external AI, no extra services) that makes the assistant safer and smarter:

- **Audit log + undo.** Every change the AI makes is recorded (`list_recent_actions`,
  `numu://audit/recent`). Reversible edits (price/status/stock/coupon, incl. batch)
  store an *inverse request* so `undo_last_action` can roll them back. Storage is
  pluggable via `NUMU_MCP_AUDIT_BACKEND`: **`sqlite`** (default, local file — best
  for per-merchant/stdio) or **`api`** (durable Postgres written by the NUMU API —
  use for remote/AWS so the server stays stateless). See
  [docs/DEPLOY.md](docs/DEPLOY.md).
- **Confirmation gate.** Irreversible actions (`delete_product`, `refund_order`,
  `cancel_order`) can't run in one step — they return a token, you confirm with
  the user, then re-call with `confirm=<token>`.
- **Business intelligence.** `analyze_customer_segments` (RFM),
  `analyze_inventory_health` (days-of-cover), and `suggest_price_adjustments`
  turn raw data into recommendations using pure Python statistics. Velocity is
  sourced from the analytics endpoint and degrades gracefully if analytics isn't
  on the plan.
- **Batch operations.** `batch_*` tools act on up to 50 records concurrently;
  batch writes register a single audit entry with a combined undo.
- **Recovery-aware errors.** Failures come back with targeted hints (wrong id,
  expired token, plan limit, timeout) so the model can self-correct.
- **Health check.** `numu://system/health` reports API reachability, latency and
  token validity.

---

## Prerequisites

1. **Python 3.11+**
2. A running **NUMU API** with the PAT feature (this repo ships the backend
   migration — see below).
3. Your **store id** (UUID) and the store **origin** (e.g.
   `https://mystore.numueg.app`).

---

## 1) Backend: enable Personal Access Tokens

The PAT feature lives in the `NUMU-api` repo (branch `dev`). Apply the migration
once:

```bash
cd NUMU-api
alembic upgrade head      # creates public.personal_access_tokens
```

This adds three endpoints under the existing store routes (owner-only):

```
POST   /api/v1/stores/{store_id}/access-tokens     # create (returns the secret once)
GET    /api/v1/stores/{store_id}/access-tokens     # list (metadata only)
DELETE /api/v1/stores/{store_id}/access-tokens/{id} # revoke
```

### Mint a token

The create endpoint is protected like the rest of the dashboard, so authenticate
as the store owner first. Using `curl`:

```bash
# 1. Log in (returns access_token in the body and as a cookie)
TOKEN=$(curl -s -X POST https://mystore.numueg.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","password":"••••••"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['tokens']['access_token'])")

# 2. Create a PAT for the MCP server
curl -s -X POST https://mystore.numueg.app/api/v1/stores/<STORE_ID>/access-tokens \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Claude MCP"}'
```

The response contains `data.token` — a value starting with `numu_pat_`. **Copy it
now; it is shown only once.** (You can also add a “Create access token” button to
the merchant dashboard that calls the same endpoint.)

---

## 2) Install the MCP server

Using **uv** (recommended):

```bash
cd mcp
uv venv && uv pip install -e .
```

Or plain `pip`:

```bash
cd mcp
python -m venv .venv
.venv/Scripts/activate      # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e .
```

This installs the `numu-mcp` command.

---

## 3) Configure

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `NUMU_MCP_BASE_URL` | ✅ | Store origin, e.g. `https://mystore.numueg.app`. The subdomain selects the tenant. Don't include `/api/v1`. |
| `NUMU_MCP_STORE_ID` | ✅ | The store's UUID. |
| `NUMU_MCP_ACCESS_TOKEN` | ✅ | The `numu_pat_…` token. |
| `NUMU_MCP_TRANSPORT` | | `stdio` (default) or `http`. |
| `NUMU_MCP_HOST` / `NUMU_MCP_PORT` | | Bind address for `http` (default `127.0.0.1:8765`). |
| `NUMU_MCP_TIMEOUT` | | Request timeout in seconds (default 30). |
| `NUMU_MCP_VERIFY_SSL` | | `true` (default). Set `false` only for local self-signed certs. |
| `NUMU_MCP_LOG_LEVEL` | | `INFO` (default). |
| `NUMU_MCP_AUDIT_BACKEND` | | `sqlite` (default, local file) or `api` (durable Postgres via the NUMU API — use for remote/AWS deployments). |
| `NUMU_MCP_DATA_DIR` | | Where the SQLite audit DB lives (default `mcp/data/`). Only used when `audit_backend=sqlite`. |

---

## 4) Run

**Local (stdio):**

```bash
numu-mcp
# or: python -m numu_mcp
```

**Remote (streamable HTTP):**

```bash
NUMU_MCP_TRANSPORT=http NUMU_MCP_PORT=8765 numu-mcp
# serves the MCP endpoint at http://127.0.0.1:8765/mcp
```

---

## 5) Connect to Claude Desktop

Open Claude Desktop → **Settings → Developer → Edit Config** (this opens
`claude_desktop_config.json`):

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add a `numu` server. Use the **absolute path** to the Python that has the package
installed (e.g. the venv), and pass configuration via `env` (Claude Desktop does
not load your shell or `.env`):

```jsonc
{
  "mcpServers": {
    "numu": {
      "command": "C:\\path\\to\\mcp\\.venv\\Scripts\\numu-mcp.exe",
      "env": {
        "NUMU_MCP_BASE_URL": "https://mystore.numueg.app",
        "NUMU_MCP_STORE_ID": "00000000-0000-0000-0000-000000000000",
        "NUMU_MCP_ACCESS_TOKEN": "numu_pat_xxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

On macOS/Linux the command is `/path/to/mcp/.venv/bin/numu-mcp`. If you prefer not
to rely on the console script, use the interpreter directly:

```jsonc
{
  "mcpServers": {
    "numu": {
      "command": "/path/to/mcp/.venv/bin/python",
      "args": ["-m", "numu_mcp"],
      "env": { "NUMU_MCP_BASE_URL": "…", "NUMU_MCP_STORE_ID": "…", "NUMU_MCP_ACCESS_TOKEN": "…" }
    }
  }
}
```

Restart Claude Desktop. You should see the **numu** tools appear (hammer icon).
Try: *“Give me today's merchant briefing.”*

### Connecting a remote (HTTP) server

For clients that support remote MCP servers, run with `NUMU_MCP_TRANSPORT=http`
and point the client at `http://<host>:<port>/mcp`. Put it behind TLS and treat
the PAT as a secret in transit.

---

## Deploying (Docker / AWS)

For a shared, remote server, build the image and run it in HTTP mode with the
durable audit backend:

```bash
docker build -t numu-mcp .
docker run -p 8765:8765 \
  -e NUMU_MCP_BASE_URL=https://mystore.numueg.app \
  -e NUMU_MCP_STORE_ID=<STORE_UUID> \
  -e NUMU_MCP_ACCESS_TOKEN=numu_pat_xxx \
  -e NUMU_MCP_AUDIT_BACKEND=api \
  numu-mcp
```

In `api` audit mode the container is **stateless** (no disk, no DB credentials),
so it scales horizontally. Full AWS ECS/Fargate walkthrough (ECR, Secrets
Manager, ALB, health checks) is in **[docs/DEPLOY.md](docs/DEPLOY.md)**.

---

## Testing with MCP Inspector

The official inspector is the quickest way to poke at the server:

```bash
npx @modelcontextprotocol/inspector numu-mcp
```

It lets you list/call tools, read resources, and run prompts interactively
(requires Node only for the inspector itself — the server stays pure Python).

---

## Security notes

- The PAT is a bearer credential. Store it in the client's secret config, never
  in source. Rotate by minting a new token and revoking the old one
  (`DELETE …/access-tokens/{id}`).
- Tokens can be given an expiry (`expires_in_days` when creating).
- All authorization is enforced server-side by the NUMU API (RBAC + plan
  limits). This MCP server adds no privileges of its own.
- For least privilege, mint the PAT under a staff account whose role grants only
  the permissions the AI should have.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Missing required variable(s)` on start | Set `NUMU_MCP_BASE_URL`, `NUMU_MCP_STORE_ID`, `NUMU_MCP_ACCESS_TOKEN` (in `.env` or the Claude `env` block). |
| `Authentication failed — the access token is invalid…` | The PAT is wrong, revoked, or expired. Mint a new one. |
| `Access token is not valid for this store` | The `NUMU_MCP_BASE_URL` subdomain doesn't match the store the PAT was minted for. |
| `Not allowed: …` on an action | The owner/role lacks that permission, or the plan doesn't include the feature. |
| Tools don't appear in Claude Desktop | Check the command path is absolute and points at the venv; view Claude's MCP logs. |

---

## Project layout

```
mcp/
├── pyproject.toml
├── .env.example
├── README.md
└── src/numu_mcp/
    ├── app.py          # FastMCP instance + operator instructions
    ├── config.py       # env-driven settings (pydantic-settings)
    ├── client.py       # async httpx client, envelope unwrap, errors, opt-in cache
    ├── capabilities.py # plan/permission probe + per-tool availability map
    ├── intelligence.py # pure-Python RFM, inventory health, price rules
    ├── audit.py        # SQLite audit log + inverse-request undo
    ├── guards.py       # confirmation gate for irreversible actions
    ├── cache.py        # narrow in-memory TTL cache (stable reads only)
    ├── formatting.py   # money + input validation + status enums
    ├── runtime.py      # shared client/settings singletons
    ├── resources.py    # overview, capabilities, catalog, health, audit
    ├── prompts.py      # daily briefing, restock alert
    ├── server.py       # entrypoint + transport selection + audit DB init
    └── tools/          # store/plan/audit, intelligence, batch, orders, products,
                        # inventory, customers, discounts, analytics, shipping
```
