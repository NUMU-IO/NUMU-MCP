"""Configuration for the NUMU MCP server.

Everything is read from environment variables (prefix ``NUMU_MCP_``) or an
optional ``.env`` file. Nothing is hard-coded — credentials in particular must
never live in source. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="NUMU_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Connection to the NUMU API ────────────────────────────────────────
    base_url: str = Field(
        description=(
            "Origin of the merchant's store, e.g. https://mystore.numueg.app . "
            "The store subdomain matters — it resolves the tenant. The /api/v1 "
            "prefix is added automatically; do not include it here."
        ),
    )
    store_id: UUID = Field(
        description="The store's UUID. All operations target this single store.",
    )
    access_token: str = Field(
        description="A NUMU Personal Access Token (starts with 'numu_pat_').",
    )

    # ── HTTP behaviour ────────────────────────────────────────────────────
    timeout: float = Field(
        default=30.0, ge=1.0, le=300.0, description="Per-request timeout in seconds."
    )
    verify_ssl: bool = Field(
        default=True,
        description="Verify TLS certificates. Only disable for local dev.",
    )

    # ── MCP transport ─────────────────────────────────────────────────────
    transport: str = Field(
        default="stdio",
        description="MCP transport: 'stdio' (local) or 'http' (remote/streamable).",
    )
    host: str = Field(
        default="127.0.0.1", description="Bind host when transport='http'."
    )
    port: int = Field(
        default=8765, ge=1, le=65535, description="Bind port when transport='http'."
    )

    log_level: str = Field(default="INFO", description="Python logging level.")

    data_dir: str = Field(
        default="",
        description=(
            "Directory for the local audit DB and other server-side state. "
            "Defaults to a 'data' folder next to the package if left blank."
        ),
    )

    audit_backend: str = Field(
        default="sqlite",
        description=(
            "Where the audit/undo trail is stored: 'sqlite' (local file, best "
            "for per-merchant/stdio use) or 'api' (durable Postgres via the NUMU "
            "API — required for stateless/remote deployments like AWS)."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if v.endswith("/api/v1"):
            v = v[: -len("/api/v1")]
        return v

    @field_validator("transport")
    @classmethod
    def _normalize_transport(cls, v: str) -> str:
        v = v.strip().lower()
        # Accept a few friendly aliases for the streamable-http transport.
        if v in {"http", "streamable-http", "streamable_http", "remote"}:
            return "streamable-http"
        if v in {"stdio", "local"}:
            return "stdio"
        raise ValueError("transport must be 'stdio' or 'http'")

    @field_validator("audit_backend")
    @classmethod
    def _normalize_audit_backend(cls, v: str) -> str:
        v = v.strip().lower()
        if v in {"sqlite", "local"}:
            return "sqlite"
        if v in {"api", "postgres", "remote"}:
            return "api"
        raise ValueError("audit_backend must be 'sqlite' or 'api'")

    @field_validator("access_token")
    @classmethod
    def _check_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("access_token must not be empty")
        return v

    @property
    def resolved_data_dir(self) -> str:
        """Absolute path for server-side state (audit DB, etc.)."""
        from pathlib import Path

        if self.data_dir.strip():
            return self.data_dir.strip()
        # Default: a 'data' folder at the package root (…/mcp/data).
        return str(Path(__file__).resolve().parents[2] / "data")

    @property
    def store_api_base(self) -> str:
        """Base path for store-scoped endpoints: ``…/api/v1/stores/{store_id}``."""
        return f"{self.base_url}/api/v1/stores/{self.store_id}"

    @property
    def api_base(self) -> str:
        """Base path for non-store endpoints: ``…/api/v1``."""
        return f"{self.base_url}/api/v1"


def load_settings() -> Settings:
    """Load and validate settings, raising a readable error if misconfigured."""
    return Settings()  # type: ignore[call-arg]
