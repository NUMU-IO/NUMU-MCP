# Connect your AI to your store

NUMU exposes your store to AI assistants through an **MCP server** (Model Context
Protocol). Once connected, an AI like **Claude** can read your store and *do real
work* for you — check today's orders, update statuses, manage products and stock,
create discounts, pull sales reports, arrange shipments — using only the
permissions your plan and staff role allow.

This page shows a merchant how to connect their AI in a few minutes.

::: tip What you'll need
- A NUMU store (you know its **store URL**, e.g. `https://mystore.numueg.app`).
- An MCP-capable AI client — e.g. **Claude Desktop** (free to install).
- A **Personal Access Token** for your store (created below).
:::

---

## 1. Create an access token

An access token is a secret that lets your AI act on your store on your behalf.
It's tied to your store and respects your plan and permissions.

> The token is shown **once**. Copy it somewhere safe (a password manager). You
> can revoke it any time, and create as many as you like (one per tool/device is
> good practice).

### Option A — from the dashboard (recommended)
Go to **Settings → Access Tokens → Create token**, give it a name (e.g.
"Claude"), and copy the value that starts with `numu_pat_…`.

::: info
If you don't see Access Tokens in your dashboard yet, use Option B below.
:::

### Option B — via the API
Log in and call the access-tokens endpoint for your store:

```bash
# 1) Log in to get a short-lived session token
TOKEN=$(curl -s -X POST https://mystore.numueg.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"••••••"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['tokens']['access_token'])")

# 2) Create a Personal Access Token named "Claude"
curl -s -X POST https://mystore.numueg.app/api/v1/stores/<STORE_ID>/access-tokens \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Claude"}'
```

The response contains `data.token` — your `numu_pat_…` value. (Your `<STORE_ID>`
is shown in the dashboard URL and in the API.)

---

## 2. Connect your AI client

You point your AI client at the NUMU MCP server and give it three things: your
**store URL**, your **store ID**, and your **access token**.

### Claude Desktop

1. Open **Claude Desktop → Settings → Developer → Edit Config**. This opens
   `claude_desktop_config.json`.
2. Add a `numu` server (use the hosted endpoint if your plan provides one, or run
   it yourself — see [Running the server](#_3-running-the-server-options)):

```jsonc
{
  "mcpServers": {
    "numu": {
      "command": "numu-mcp",
      "env": {
        "NUMU_MCP_BASE_URL": "https://mystore.numueg.app",
        "NUMU_MCP_STORE_ID": "your-store-id",
        "NUMU_MCP_ACCESS_TOKEN": "numu_pat_xxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

3. Restart Claude Desktop. You'll see the NUMU tools appear (the hammer icon).
4. Try it: *"Give me today's merchant briefing."*

### Other MCP clients
Any MCP-compatible client works. For clients that connect to a **remote** server,
use the HTTP endpoint your administrator provides (e.g.
`https://mcp.numueg.app/mcp`) and the same three values above.

---

## 3. Running the server (options)

| You want… | Use |
|-----------|-----|
| The simplest setup on your own computer | Install once: `pip install numu-mcp`, then the Claude Desktop config above launches it automatically. |
| A shared, always-on server | Run it with Docker (`docker run … numu-mcp`) or have NUMU host it for you. See the deployment guide. |

::: details Install on your computer (one time)
```bash
pip install numu-mcp
```
That gives you the `numu-mcp` command the Claude config calls. Nothing else to
run — Claude starts it for you.
:::

---

## 4. What you can ask it to do

Once connected, ask in plain language (English or Arabic). For example:

- **Orders:** "What orders came in today?" · "Mark order #1043 as shipped." ·
  "Refund order #1043 — the customer got a damaged item."
- **Products & stock:** "Which products are low or out of stock?" · "Add a new
  product called *Summer Tee* at 350 EGP with 40 in stock." · "Raise the price of
  the black hoodie by 10%."
- **Customers:** "Who are my VIP customers?" · "Show me Mariam's order history."
- **Discounts:** "Create a 15% coupon code SUMMER15 valid until end of July."
- **Insights:** "How were sales this week vs last week?" · "What are my best
  sellers this month?" · "Suggest price changes based on what's selling."
- **Daily routine:** "Run my daily briefing" — revenue, orders needing action,
  stock alerts and customers to watch, all at once.

---

## 5. It's safe by design

- **Your permissions apply.** The AI can only do what your plan and your staff
  role allow. If something isn't available, it tells you why (e.g. needs a plan
  upgrade) instead of failing silently.
- **Confirmation for risky actions.** Deleting a product, refunding, or
  cancelling an order requires an explicit confirmation step — the AI can't do
  these in one shot by accident.
- **Undo.** Reversible changes (prices, statuses, stock, coupons) can be rolled
  back: just say *"undo that."*
- **Full audit trail.** Every change the AI makes is recorded. Ask *"what did the
  AI change today?"* — or view it in your dashboard.
- **You're always in control.** Revoke a token any time and the AI instantly
  loses access.

---

## 6. Revoke or rotate a token

- **Dashboard:** Settings → Access Tokens → revoke.
- **API:** `DELETE /api/v1/stores/<STORE_ID>/access-tokens/<TOKEN_ID>`.

Rotate by creating a new token, updating your AI client's config, then revoking
the old one.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Tools don't appear in Claude | Re-check the three env values; make sure the command path is correct; restart Claude. |
| "Authentication failed" | The token is wrong, expired, or revoked — create a new one. |
| "Not allowed" on an action | Your plan or role doesn't include it — check your plan, or ask an owner. |
| "Not valid for this store" | `NUMU_MCP_BASE_URL` must be *your* store's URL (the subdomain identifies the store). |

---

::: tip Privacy & security
Treat your access token like a password. Don't paste it into chats or share it.
It only works against your own store, and you can revoke it instantly.
:::
