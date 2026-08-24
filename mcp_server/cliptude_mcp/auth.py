"""TokenVerifier for Streamable HTTP MCP. Validates OAuth tokens and clp_ API keys via REST."""

from __future__ import annotations

import os
from typing import Any

import httpx

from cliptude_mcp.client import resolve_base_url


def _resource_url() -> str:
    return (os.environ.get("MCP_RESOURCE_URL") or "https://mcp.cliptude.com/mcp").rstrip("/")


def _issuer_url() -> str:
    return (os.environ.get("OAUTH_ISSUER") or "https://cliptude.com").rstrip("/")


class CliptudeTokenVerifier:
    """Accept django-oauth-toolkit access tokens and hashed clp_ API keys.

    Verification is delegated to GET /api/v1/account so this process never imports Django.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        self.timeout = timeout

    async def verify_token(self, token: str) -> Any:
        token = (token or "").strip()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/account",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        email = ""
        if isinstance(payload, dict):
            email = str(payload.get("email") or "")
        try:
            from mcp.server.auth.provider import AccessToken
        except ImportError:  # pragma: no cover - stdio tests without the SDK
            from types import SimpleNamespace

            return SimpleNamespace(
                token=token,
                client_id=email or "cliptude",
                scopes=["jobs"],
                resource=_resource_url(),
            )
        return AccessToken(
            token=token,
            client_id=email or "cliptude",
            scopes=["jobs"],
            resource=_resource_url(),
        )


def auth_settings():
    from pydantic import AnyHttpUrl
    from mcp.server.auth.settings import AuthSettings

    return AuthSettings(
        issuer_url=AnyHttpUrl(_issuer_url()),
        resource_server_url=AnyHttpUrl(_resource_url()),
        required_scopes=["jobs"],
    )
