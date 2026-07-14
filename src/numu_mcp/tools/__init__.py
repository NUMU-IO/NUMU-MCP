"""MCP tools — the actions an AI can take on the store.

Importing this package registers every tool on the shared FastMCP instance.
"""

from . import (  # noqa: F401
    analytics,
    batch,
    categories,
    customers,
    discounts,
    intelligence,
    inventory,
    orders,
    products,
    shipping,
    store,
    theme,
)

__all__ = [
    "analytics",
    "batch",
    "categories",
    "customers",
    "discounts",
    "intelligence",
    "inventory",
    "orders",
    "products",
    "shipping",
    "store",
    "theme",
]
