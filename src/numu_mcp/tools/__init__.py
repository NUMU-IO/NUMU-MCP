"""MCP tools — the actions an AI can take on the store.

Importing this package registers every tool on the shared FastMCP instance.
"""

from . import (  # noqa: F401
    analytics,
    customers,
    discounts,
    inventory,
    orders,
    products,
    shipping,
    store,
)

__all__ = [
    "analytics",
    "customers",
    "discounts",
    "inventory",
    "orders",
    "products",
    "shipping",
    "store",
]
