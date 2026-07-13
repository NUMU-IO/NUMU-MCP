"""Tool registration: import every domain module so its @mcp.tool decorators run."""

from . import analytics, catalog, media, orders, store  # noqa: F401
