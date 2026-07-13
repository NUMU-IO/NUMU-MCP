"""Runtime configuration for numu-mcp (env-driven, no secrets stored)."""

import os


class Settings:
    """All config comes from the environment; the server itself holds no merchant secrets.

    Merchant credentials arrive per-request as `Authorization: Bearer numu_sk_...`
    and are forwarded to NUMU-api, which validates them (pass-through auth).
    """

    # Must include /api/v1 (same convention as numu-storefront's NUMU_API_URL)
    numu_api_url: str = os.environ.get("NUMU_API_URL", "http://127.0.0.1:8001/api/v1").rstrip("/")

    host: str = os.environ.get("MCP_HOST", "0.0.0.0")
    port: int = int(os.environ.get("MCP_PORT", "8100"))

    # Seconds to cache key-info lookups (token -> store binding/scopes)
    key_info_ttl: int = int(os.environ.get("MCP_KEY_INFO_TTL", "300"))

    request_timeout: float = float(os.environ.get("MCP_REQUEST_TIMEOUT", "30"))


settings = Settings()
