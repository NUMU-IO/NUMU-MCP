"""Async HTTP client for the NUMU REST API.

Thin wrapper around ``httpx`` that:
  * injects the Personal Access Token as a Bearer header,
  * targets the configured store (``/api/v1/stores/{store_id}/…``),
  * unwraps NUMU's ``{success, data, message}`` envelope, and
  * turns every failure (HTTP error, timeout, bad JSON) into a single
    ``NumuApiError`` carrying a clean, human-readable message for the AI.

Tools never see raw ``httpx`` exceptions — they either get the ``data`` payload
or a ``NumuApiError`` whose ``str()`` is safe to surface to the model.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class NumuApiError(Exception):
    """A normalized, readable error from the NUMU API or transport layer."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def _extract_error_message(response: httpx.Response) -> tuple[str, str | None]:
    """Pull the most useful message + machine code out of an error response."""
    try:
        body = response.json()
    except Exception:
        text = response.text.strip()
        return (text or f"HTTP {response.status_code}"), None

    if isinstance(body, dict):
        code = body.get("code")
        # NUMU standard envelope: {"success": false, "error": "...", "code": "..."}
        if body.get("error"):
            return str(body["error"]), code
        # FastAPI default / permission errors: {"detail": ... }
        detail = body.get("detail")
        if isinstance(detail, dict):
            # e.g. permission failure: {"error": "...", "required": [...], "missing": [...]}
            parts = [str(detail.get("error", "Request rejected"))]
            if detail.get("missing"):
                parts.append(f"missing permissions: {', '.join(detail['missing'])}")
            return " — ".join(parts), code
        if detail:
            return str(detail), code
        if body.get("message"):
            return str(body["message"]), code
    return f"HTTP {response.status_code}", None


def _with_plan_hint(message: str, code: str | None) -> str:
    """Append upgrade guidance when an error looks like a plan/limit block."""
    haystack = f"{message} {code or ''}".lower()
    if any(k in haystack for k in ("plan", "limit", "upgrade", "quota")):
        return (
            f"{message} (This looks like a subscription-plan limit — upgrading "
            "the merchant's plan, or checking get_capabilities, may unlock it.)"
        )
    return message


class NumuClient:
    """Authenticated, store-scoped HTTP client for one NUMU store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=settings.timeout,
            verify=settings.verify_ssl,
            headers={
                "Authorization": f"Bearer {settings.access_token}",
                "Accept": "application/json",
                "User-Agent": "numu-mcp/0.1",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, path: str, *, store_scoped: bool) -> str:
        base = (
            self._settings.store_api_base
            if store_scoped
            else self._settings.api_base
        )
        return f"{base}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        store_scoped: bool = True,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Perform a request and return the unwrapped ``data`` payload.

        Returns ``None`` for empty (204) responses. Raises ``NumuApiError`` on
        any HTTP error, timeout, connection failure, or malformed response.
        """
        url = self._url(path, store_scoped=store_scoped)
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        try:
            response = await self._client.request(
                method, url, params=clean_params, json=json
            )
        except httpx.TimeoutException as exc:
            raise NumuApiError(
                f"The NUMU API did not respond within {self._settings.timeout:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise NumuApiError(
                f"Could not reach the NUMU API: {exc}"
            ) from exc

        if response.status_code == 401:
            raise NumuApiError(
                "Authentication failed — the access token is invalid, expired, "
                "or revoked.",
                status_code=401,
            )
        if response.status_code in (402, 403):
            message, code = _extract_error_message(response)
            message = _with_plan_hint(message, code)
            raise NumuApiError(
                f"Not allowed: {message}", status_code=response.status_code, code=code
            )
        if response.status_code == 404:
            message, code = _extract_error_message(response)
            raise NumuApiError(message or "Not found.", status_code=404, code=code)
        if response.status_code >= 400:
            message, code = _extract_error_message(response)
            message = _with_plan_hint(message, code)
            raise NumuApiError(message, status_code=response.status_code, code=code)

        if response.status_code == 204 or not response.content:
            return None

        try:
            body = response.json()
        except Exception as exc:
            raise NumuApiError("The NUMU API returned a non-JSON response.") from exc

        # Unwrap the standard {success, data, message} envelope when present.
        if isinstance(body, dict) and "data" in body and "success" in body:
            return body["data"]
        return body

    # ── Convenience verbs ────────────────────────────────────────────────
    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)
