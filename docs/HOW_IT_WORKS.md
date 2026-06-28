# How the NUMU MCP Server Works

A deep, end-to-end explanation of what this server is, how a request flows from
an AI model all the way to the NUMU database and back, how authentication and
plan-awareness work, and how to extend it. Read this if you want to understand
or modify the server — the [README](../README.md) is the quick-start; this is
the architecture.

---

## 1. What this is, in one paragraph

The **Model Context Protocol (MCP)** is an open standard that lets an AI client
(Claude Desktop, Claude in the IDE, or any MCP-compatible app) talk to an
external "server" that exposes three kinds of things: **resources** (read-only
context), **tools** (actions with side effects), and **prompts** (reusable
workflows). The NUMU MCP server is one such server: it wraps a single NUMU
merchant store so an AI can *understand* the store and *operate* it — manage
orders, products, inventory, customers, discounts, analytics and shipping —
exactly as a human operator would, bounded by that merchant's real permissions
and subscription plan. It is written in Python with the official MCP SDK
(FastMCP) and talks to the existing NUMU FastAPI backend over HTTPS.

---

## 2. The big picture

```
┌──────────────────┐   MCP protocol (JSON-RPC)    ┌────────────────────┐   HTTPS + PAT    ┌──────────────┐
│  AI client       │  over stdio  OR  streamable  │  numu-mcp          │  Authorization:  │  NUMU API    │
│  (Claude, etc.)  │ ───────────  HTTP ────────►  │  (this server)     │  Bearer numu_pat │  (FastAPI)    │
│                  │ ◄───────────────────────────  │                    │ ───────────────► │              │
│  - lists tools   │   tool results / resources   │  - tools/resources │ ◄─────────────── │  RBAC + plan │
│  - calls tools   │                              │  - validation      │  JSON envelopes  │  RLS by      │
│  - reads context │                              │  - plan awareness  │                  │  subdomain   │
└──────────────────┘                              └────────────────────┘                  └──────────────┘
```

Two transport options, chosen by the `NUMU_MCP_TRANSPORT` env var:

- **stdio** — the server is launched as a subprocess by the AI client and they
  speak JSON-RPC over stdin/stdout. This is what Claude Desktop uses. stdout is
  the protocol channel, so the server logs to **stderr only**.
- **streamable-http** — the server runs as a long-lived HTTP service and clients
  connect to `http://host:port/mcp`. Use this for remote/shared deployments.

The server is **single-store**: one running instance is configured (via env) for
exactly one store id and one origin. The AI never passes a store id; it's
implicit. Each merchant runs (or is handed) their own instance.

---

## 3. The three MCP primitives, as NUMU exposes them

### Resources — "what the AI reads to understand the system"
Resources are addressed by URI and return text (here, JSON). They're like GET
endpoints meant to load context into the model.

| URI | Contents |
|-----|----------|
| `numu://store/overview` | Store profile, status, currency, plan + usage, payment methods, product/order/customer counts, inventory health. |
| `numu://store/capabilities` | The live capability map (plan, limits, features, the connected user's permission domains, and a per-tool availability map). |
| `numu://products/catalog` | First 100 products with price, stock, category, status. |

### Tools — "actions the AI can take"
Tools are like POST endpoints: they do something and return a result. Each tool
has a **clear English docstring** (the model reads it to decide when/how to call
the tool) and a typed signature (the SDK turns it into a JSON Schema the model
must satisfy). There are 30 tools across these domains:

- **Store / plan:** `get_capabilities`, `refresh_capabilities`, `get_plan_and_usage`, `list_categories`
- **Orders:** `list_orders`, `get_order`, `update_order_status`, `cancel_order`, `refund_order`
- **Products:** `list_products`, `get_product`, `create_product`, `update_product`, `delete_product`
- **Inventory:** `list_inventory`, `inventory_stats`, `adjust_inventory`
- **Customers:** `search_customers`, `get_customer`, `get_customer_orders`
- **Discounts:** `create_discount`, `list_discounts`, `deactivate_discount`
- **Analytics:** `sales_report`, `revenue_over_time`, `top_products`
- **Shipping:** `list_shipments`, `create_shipment`, `get_shipment`, `track_shipment`

### Prompts — "pre-built workflows"
Prompts are reusable instruction templates the user can invoke by name.

| Prompt | What it drives the AI to do |
|--------|------------------------------|
| `daily_merchant_briefing` | Read overview, pull today's sales, surface orders needing action and stock alerts, summarize with recommended actions. |
| `restock_alert` | List out-of-stock + low-stock products, cross-reference best-sellers, produce a prioritized restock list. |

---

## 4. The lifecycle of a single tool call

Take *"refund order #1234 fully"* as an example. Here's what actually happens:

1. **Discovery.** When the client connects, it calls the MCP `list_tools`,
   `list_resources`, `list_prompts` methods. FastMCP returns the registry that
   was built when the tool modules were imported (every `@mcp.tool()` decorator
   registered its function, docstring and JSON Schema). The model now "sees"
   `refund_order(order_id, refund_type, reason, amount?, reason_note?)`.

2. **The model calls the tool.** It sends an MCP `call_tool` request with the
   arguments it parsed from the user's intent.

3. **Plan/permission pre-flight (for gated tools).** Gated tools call
   `guard("<tool_name>")` first. `guard` consults the cached **capability probe**
   (§6). If the plan or role disallows the action, the tool returns a clean
   message like *"Not available: Your plan (Demo) does not include
   'discount_codes'."* — no API call is made. (`refund_order` isn't plan-gated,
   so this step is a no-op for it.)

4. **Local input validation.** Before any network call, helpers in
   `formatting.py` validate inputs: `order_id` must be a UUID, `refund_type`
   must be `full`/`partial`, `reason` must be one of the allowed enum values. A
   bad value returns `"Invalid input: …"` immediately — the model gets an
   actionable message instead of a 422 from the server.

5. **Money conversion.** For a *partial* refund, the tool fetches the order to
   learn its currency, then converts the human amount (e.g. `49.99`) to the
   integer minor units the API expects (`4999`). NUMU stores money as integer
   cents/piastres; the server hides that detail from the model.

6. **The HTTP call.** The tool calls `get_client().post("orders/<id>/refunds",
   json=…)`. The shared `NumuClient` (§5):
   - prepends the store-scoped base path
     `…/api/v1/stores/{store_id}/orders/<id>/refunds`,
   - attaches `Authorization: Bearer numu_pat_…`,
   - drops `None` query params, sends JSON.

7. **The backend authorizes and acts.** The NUMU API (§7) resolves the PAT to a
   user, resolves the tenant from the request's subdomain, checks RBAC + plan
   limits, performs the refund, and returns a `{success, data, message}`
   envelope.

8. **Response normalization.** `NumuClient` unwraps the envelope and returns
   just `data`. On any error (HTTP 4xx/5xx, timeout, bad JSON) it raises a single
   `NumuApiError` whose message is safe and readable; plan/limit errors get an
   "upgrade" hint appended.

9. **The tool returns.** It formats a concise result (`{"refund_created": true,
   "refund": {…}}`) as a JSON string. Any exception is caught and rendered by
   `err()` into a one-line, model-friendly message. **Tools never throw raw
   exceptions at the model** — they always return a string.

10. **The model replies to the user**, using the structured result.

---

## 5. The HTTP client (`client.py`) — the single choke point

Every backend call goes through `NumuClient`, which centralizes four concerns so
the tools stay tiny:

- **Auth & addressing.** One `httpx.AsyncClient` with the Bearer header baked in.
  `store_scoped=True` (default) targets `…/stores/{store_id}/…`; `store_scoped=False`
  targets `…/api/v1/…` (used for `staff/me` and the store record).
- **Envelope unwrapping.** NUMU returns `{success, data, message}`; the client
  returns `data` directly, or `None` for `204 No Content`.
- **Error normalization.** Status-specific handling: 401 → "authentication
  failed"; 402/403 → "Not allowed: …" (+ plan hint); 404 → the API's message;
  other 4xx/5xx → the extracted message. Timeouts and connection failures become
  readable `NumuApiError`s too. The extractor understands NUMU's error envelope
  *and* FastAPI's `{"detail": …}` (including the permission-failure dict shape).
- **Plan-limit hinting.** `_with_plan_hint` detects "plan/limit/quota/upgrade"
  signals and appends guidance pointing at `get_capabilities`.

---

## 6. Plan & permission awareness (`capabilities.py`) — the "real plugin" part

This is what makes the server feel like an integration rather than a static API
wrapper. It knows what the merchant's plan and the connecting user can actually
do, and it adapts.

**The probe.** `CapabilityProbe.get()` fetches two things and caches them for
~60 seconds:
- `GET /stores/{id}/plan/usage` → plan name, display name, products & monthly-
  orders usage vs. limits, and feature flags (`webhooks`, `custom_domain`,
  `api_access`, `analytics`, `discount_codes`).
- `GET /staff/me` → whether the connected user is the owner, and their effective
  permission codes. Codes like `orders.view`/`orders.edit` are reduced to
  **domains** (`orders`, `products`, …).

**The capability map.** `TOOL_REQUIREMENTS` declares, per tool, a permission
*domain* and an optional required *plan feature*. From that, `Capabilities`
answers:
- `tool_allowed(name)` → `(allowed, reason)`. Discounts require the
  `discount_codes` feature; analytics require `analytics`; everything checks the
  permission domain (owners bypass, mirroring the backend).
- `usage_note("products")` → "83 of 100 products used", etc.

**How it's used.**
- The `numu://store/capabilities` resource and `get_capabilities` tool expose the
  whole map so the AI can decide *before* acting.
- Gated tools call `guard(name)` and short-circuit with the reason if blocked.

**Fail-open by design.** If `plan/usage` or `staff/me` can't be loaded, the probe
records a note and **does not block** — calls proceed and the backend remains the
single source of truth for authorization. The MCP server never grants anything
the API wouldn't; it only *predicts and explains* to give a better experience.

---

## 7. Authentication: Personal Access Tokens (PAT)

The NUMU backend authenticates browser sessions with JWT cookies and had no
API-key mechanism for machine clients. This project added one (shipped as a PR
against the API's `dev` branch). It's how the MCP server authenticates.

**Token shape & storage.** A PAT looks like `numu_pat_<43 url-safe chars>` (256
bits of entropy). Only its **SHA-256 hash** is stored in
`public.personal_access_tokens`; the raw value is shown to the merchant exactly
once at creation. A token row carries `user_id`, `tenant_id`, optional
`store_id`, a display `token_prefix`, `expires_at`, `revoked_at`, `last_used_at`.

**Minting.** The store owner calls `POST /api/v1/stores/{store_id}/access-tokens`
(owner-only). The response includes the secret once. Revoke with `DELETE
…/access-tokens/{id}`.

**Resolution at request time.** The backend's auth dependencies were refactored
to a single `_resolve_principal`:
1. Read the bearer token (or cookie).
2. If it starts with `numu_pat_`, validate it against the table (exists, not
   revoked, not expired), load the owning user, and synthesize the same
   `TokenPayload` a JWT would produce (user id, email, role, tenant id).
3. Otherwise, take the existing JWT path **unchanged**.

Because a PAT produces the same principal object as a login, **everything
downstream — membership lookup, RBAC permission checks, plan-limit gates — applies
identically.** The PAT grants no special powers; it's just another way to prove
who you are.

**Tenant safety.** NUMU resolves the tenant from the request's **subdomain**
(`mystore.numueg.app`), and Postgres Row-Level Security filters every query by
that tenant. The PAT additionally carries the tenant it was minted for, and
`_resolve_principal` rejects a token replayed against a different store's
subdomain. So the MCP server must call the correct store origin — which is
exactly what `NUMU_MCP_BASE_URL` configures.

---

## 8. Configuration (`config.py`)

All configuration comes from environment variables (prefix `NUMU_MCP_`) or an
optional `.env`. Nothing is hard-coded; credentials never live in source.

| Var | Purpose |
|-----|---------|
| `NUMU_MCP_BASE_URL` | Store origin, e.g. `https://mystore.numueg.app`. The subdomain selects the tenant. `/api/v1` is appended automatically (and stripped if you include it). |
| `NUMU_MCP_STORE_ID` | The store UUID every call targets. |
| `NUMU_MCP_ACCESS_TOKEN` | The `numu_pat_…` token. |
| `NUMU_MCP_TRANSPORT` | `stdio` (default) or `http`. |
| `NUMU_MCP_HOST` / `NUMU_MCP_PORT` | Bind address for HTTP. |
| `NUMU_MCP_TIMEOUT` / `NUMU_MCP_VERIFY_SSL` / `NUMU_MCP_LOG_LEVEL` | HTTP + logging knobs. |

Validation happens at startup: `base_url` must be http(s); `transport` is
normalized (accepts `http`, `streamable-http`, `remote` → streamable-http);
missing required vars produce a friendly stderr message and a non-zero exit
(never a stack trace on stdout, which would corrupt the stdio channel).

---

## 9. Startup sequence (`server.py`)

1. Import the resource/tool/prompt modules — their decorators populate the
   FastMCP registry. (Registration needs no network or config.)
2. `load_settings()` — validate env; on failure, print a friendly message and
   exit 1.
3. Configure logging to **stderr**.
4. Build the `NumuClient` and store it (with settings) in `runtime.py` singletons
   that tools fetch lazily via `get_client()` / `get_settings()`.
5. `mcp.run(transport=…)` — start stdio or streamable-HTTP and serve forever.

The split between "register at import" and "initialize at run" is deliberate: it
lets tests and tooling import the package (to introspect the tool list) without
needing credentials or a live API.

---

## 10. File-by-file map

```
src/numu_mcp/
├── app.py          FastMCP instance + the operator "instructions" the model sees.
├── config.py       Env-driven settings, validation, derived URLs.
├── client.py       Async HTTP client: auth, envelope unwrap, error normalization, plan hints.
├── capabilities.py Plan/permission probe (cached), per-tool availability map, guard().
├── formatting.py   Money (minor↔major), status-enum constants, input validators.
├── runtime.py      Process-wide client/settings singletons.
├── resources.py    numu://store/overview, /capabilities, /products/catalog.
├── prompts.py      daily_merchant_briefing, restock_alert.
├── server.py       Entrypoint: load config → build client → run transport.
└── tools/
    ├── _base.py    err() + JSON/pagination helpers shared by all tools.
    ├── store.py    capabilities, plan/usage, categories.
    ├── orders.py   list/get/update-status/cancel/refund.
    ├── products.py list/get/create/update/delete.
    ├── inventory.py list/stats/adjust.
    ├── customers.py search/get/order-history.
    ├── discounts.py create/list/deactivate (plan-gated).
    ├── analytics.py sales report / revenue series / top products (plan-gated).
    └── shipping.py  list/create/get/track.
```

---

## 11. Design decisions worth knowing

- **Tools return strings, never throw.** Every tool wraps its body and converts
  exceptions via `err()`. This guarantees the model always gets a usable answer
  and keeps the protocol layer clean.
- **Validate locally, authorize remotely.** Enum/UUID/amount checks happen in the
  server (fast, precise feedback); permission and plan *authorization* is always
  the backend's job. The capability layer only predicts and explains.
- **Two money worlds, hidden.** Orders/analytics/inventory use integer minor
  units; the product API uses decimal major units. The server normalizes both so
  the model always sees/sends human amounts.
- **Idempotent, ID-driven.** List/search tools surface IDs; detail/write tools
  take IDs. Destructive actions (cancel/refund/delete/deactivate) are designed to
  be confirmed by the model with the user first (see the server instructions).
- **Single store, subdomain-bound.** Matches NUMU's tenant-per-subdomain model
  and keeps the blast radius of any one token to one store.

---

## 12. Extending the server: add a new tool

1. Pick the right module under `tools/` (or add one and import it in
   `tools/__init__.py`).
2. Write an async function decorated with `@mcp.tool()`. Use **builtin-typed**
   parameters (`str`, `int`, `bool`, `str | None`, `list[str]`) and a **clear
   docstring with an `Args:` section** — the model relies on both.
3. Validate inputs with `formatting.py` helpers; call the API via
   `get_client()`; format the result with `_base.dumps`; wrap the body in
   `try/except Exception` and `return err(exc)`.
4. If the action is plan/permission gated, add an entry to `TOOL_REQUIREMENTS` in
   `capabilities.py` and call `guard("<tool_name>")` at the top of the tool.
5. Verify it registers:

```bash
python - <<'PY'
import asyncio, sys; sys.path.insert(0, "src")
import numu_mcp.server
from numu_mcp.app import mcp
print([t.name for t in asyncio.run(mcp.list_tools())])
PY
```

---

## 13. Testing & inspecting

- **MCP Inspector** (interactive): `npx @modelcontextprotocol/inspector numu-mcp`
  — list/call tools, read resources, run prompts. (Node is only for the
  inspector; the server stays pure Python.)
- **Registration smoke test**: import the package and list tools/resources/
  prompts (as above) — needs no live API.
- **Error-path checks**: point `NUMU_MCP_BASE_URL` at a dead port and confirm
  tools return clean timeout/connection messages rather than raising.

---

## 14. Security model in one view

- The PAT is a bearer secret: keep it in the client's secret config, rotate by
  minting new + revoking old, and prefer minting under a least-privilege staff
  role so the AI only gets the permissions it needs.
- The MCP server adds **no privileges**; the NUMU API enforces RBAC + plan limits
  and RLS-by-tenant for every call.
- Transport: use TLS for streamable-HTTP; for stdio the secret is passed via the
  client's env block, never on the command line.
- The server logs to stderr and never logs the token; errors returned to the
  model are scrubbed to readable messages.
