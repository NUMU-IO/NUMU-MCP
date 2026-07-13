"""Analytics tools — all read-only. Scope: analytics:read.

Periods follow the NUMU dashboard convention: pass `period` like "7d", "30d",
"90d" or explicit start/end ISO dates where supported.
"""

from typing import Any

from ..client import store_request
from ..server import mcp

READ_ONLY = {"readOnlyHint": True}


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "Store analytics overview for a period: revenue, orders, AOV, customers, "
        "conversion — the numbers on the merchant dashboard's Overview page."
    ),
)
async def get_analytics_overview(period: str = "30d") -> Any:
    return await store_request("GET", "/analytics/overview", params={"period": period})


@mcp.tool(
    annotations=READ_ONLY,
    description="Daily sales chart data (revenue/orders per day) for a period.",
)
async def get_sales_chart(period: str = "30d") -> Any:
    return await store_request("GET", "/analytics/sales-chart", params={"period": period})


@mcp.tool(
    annotations=READ_ONLY,
    description="Top products by revenue/units for a period.",
)
async def get_top_products(period: str = "30d", limit: int = 10) -> Any:
    return await store_request(
        "GET", "/analytics/top-products", params={"period": period, "limit": limit}
    )


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "Conversion funnel for a period: sessions -> product views -> add-to-cart -> "
        "checkout -> purchase, with drop-off at each step."
    ),
)
async def get_funnel(period: str = "30d") -> Any:
    return await store_request("GET", "/analytics/funnel", params={"period": period})


@mcp.tool(
    annotations=READ_ONLY,
    description="Traffic sources breakdown (facebook/google/tiktok/direct/...) for a period.",
)
async def get_traffic_sources(period: str = "30d") -> Any:
    return await store_request("GET", "/analytics/traffic-sources", params={"period": period})


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "Marketing attribution: revenue and orders per campaign/UTM source for a period — "
        "which ads and campaigns actually drive sales."
    ),
)
async def get_marketing_attribution(period: str = "30d") -> Any:
    return await store_request(
        "GET", "/analytics/marketing-attribution", params={"period": period}
    )


@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "COD rejection analytics: how many cash-on-delivery orders get refused/returned "
        "and what it costs — key input for return-rate decisions."
    ),
)
async def get_cod_rejections(period: str = "30d") -> Any:
    return await store_request("GET", "/analytics/cod-rejections", params={"period": period})
