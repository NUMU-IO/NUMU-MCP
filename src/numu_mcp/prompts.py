"""MCP prompts — pre-built merchant workflows the AI can run on demand."""

from __future__ import annotations

from .app import mcp


@mcp.prompt(
    name="daily_merchant_briefing",
    description=(
        "Produce a concise daily briefing for the merchant: today's orders, "
        "revenue, and any stock that needs attention."
    ),
)
def daily_merchant_briefing() -> str:
    return (
        "You are the merchant's operations assistant for their NUMU store. "
        "Prepare today's briefing. Work through these steps using the available "
        "tools, then present a short, scannable summary:\n\n"
        "1. Read the `numu://store/overview` resource for context (store name, "
        "currency, counts).\n"
        "2. Call `sales_report(days=1)` for today's revenue, order count and "
        "average order value, plus the change vs. the prior period.\n"
        "3. Call `list_orders(limit=20)` and highlight orders that need action — "
        "e.g. status 'pending' or 'processing', and any unpaid orders "
        "(payment_status not 'paid').\n"
        "4. Call `inventory_stats()` and, if anything is low or out of stock, "
        "`list_inventory(filter='out_of_stock')` and "
        "`list_inventory(filter='low_stock')`.\n\n"
        "Then write the briefing with these sections: **Revenue today**, "
        "**Orders needing attention**, **Stock alerts**. Keep it tight, lead with "
        "numbers, and end with up to 3 concrete recommended actions. Use the "
        "store's currency. If a step returns an error, note it briefly and "
        "continue with the rest."
    )


@mcp.prompt(
    name="restock_alert",
    description=(
        "Find every out-of-stock and low-stock product and produce a "
        "prioritized restocking list."
    ),
)
def restock_alert() -> str:
    return (
        "Identify products that need restocking in this NUMU store and produce a "
        "prioritized restock list.\n\n"
        "1. Call `list_inventory(filter='out_of_stock')` — these are the highest "
        "priority (lost sales right now).\n"
        "2. Call `list_inventory(filter='low_stock')` — these are at risk soon.\n"
        "3. Optionally call `top_products(days=30)` to see which at-risk items "
        "are also best-sellers; those should jump to the top of the list.\n\n"
        "Present two tables — **Out of stock** and **Low stock** — each with "
        "product name, SKU, current quantity, and the low-stock threshold. Sort "
        "best-sellers first within each group. Finish with a one-line summary: "
        "how many products are out of stock and how many are low. If nothing "
        "needs restocking, say so plainly."
    )
