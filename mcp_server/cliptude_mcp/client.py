"""HTTP client for Cliptude /api/v1. No Django imports."""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx

from cliptude_mcp.credentials import stored_access_token

DEFAULT_SAAS_URL = "https://cliptude.com"
_request_token: ContextVar[str | None] = ContextVar("cliptude_request_token", default=None)


def set_request_token(token: str | None):
    return _request_token.set(token)


def reset_request_token(token):
    _request_token.reset(token)


def discover_desktop_base_url() -> str | None:
    appdata = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    candidates: list[Path] = []
    if appdata:
        candidates.append(Path(appdata) / "Cliptude" / "desktop-runtime.json")
    home = Path.home()
    candidates.append(home / "Library" / "Application Support" / "Cliptude" / "desktop-runtime.json")
    candidates.append(home / ".cliptude" / "desktop-runtime.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        except json.JSONDecodeError:
            continue
        port = data.get("port") or data.get("django_port")
        if port:
            return f"http://127.0.0.1:{port}"
    return None


def resolve_base_url() -> str:
    explicit = (os.environ.get("CLIPTUDE_BASE_URL") or "").rstrip("/")
    if explicit:
        return explicit
    desktop = discover_desktop_base_url()
    return desktop or DEFAULT_SAAS_URL


def django_proxy_headers(base_url: str) -> dict[str, str]:
    """Mark docker-internal HTTP as HTTPS so Django skips SECURE_SSL_REDIRECT.

    Production Django 301s ``http://web:5021`` to ``https://web:5021``. That
    handshake times out inside the compose network, so MCP token verification
    always failed after a successful OAuth exchange.
    """
    if (base_url or "").strip().lower().startswith("http://"):
        return {"X-Forwarded-Proto": "https"}
    return {}


class CliptudeApiError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _resolve_token(explicit: str | None) -> str:
    return (
        (explicit or "").strip()
        or (_request_token.get() or "").strip()
        or (os.environ.get("CLIPTUDE_API_KEY") or "").strip()
        or stored_access_token()
    )


class CliptudeClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        self.api_key = _resolve_token(api_key)
        self.timeout = timeout
        self._session_file = not bool(
            (api_key or "").strip()
            or (_request_token.get() or "").strip()
            or (os.environ.get("CLIPTUDE_API_KEY") or "").strip()
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise CliptudeApiError("Not authenticated. Run: cliptude auth login")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            **django_proxy_headers(self.base_url),
        }

    def _maybe_refresh(self) -> bool:
        if not self._session_file:
            return False
        from cliptude_mcp.auth_login import refresh_access_token
        from cliptude_mcp.credentials import load_credentials

        creds = load_credentials()
        if not creds:
            return False
        updated = refresh_access_token(creds)
        if not updated:
            return False
        self.api_key = str(updated.get("access_token") or "")
        return bool(self.api_key)

    def request(self, method: str, path: str, *, params=None, json_body=None, _retried: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        if resp.status_code == 401 and not _retried and self._maybe_refresh():
            return self.request(method, path, params=params, json_body=json_body, _retried=True)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}
        if resp.status_code >= 400:
            message = payload.get("error") if isinstance(payload, dict) else str(payload)
            if resp.status_code == 401:
                message = message or "Not authenticated. Run: cliptude auth login"
            raise CliptudeApiError(message or f"HTTP {resp.status_code}", status=resp.status_code, payload=payload)
        return payload

    def list_templates(self, *, limit: int = 20, offset: int = 0) -> Any:
        return self.request("GET", "/api/v1/templates", params={"limit": limit, "offset": offset})

    def get_template(self, template_id: str) -> Any:
        return self.request("GET", f"/api/v1/templates/{template_id}")

    def create_job(
        self,
        *,
        template_id: str,
        title: str,
        prompt: str | None = None,
        script: str | None = None,
        faceless_coding: str | None = None,
        duration: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"template_id": template_id, "title": title}
        if prompt:
            body["prompt"] = prompt
        if script:
            body["script"] = script
        if faceless_coding:
            body["faceless_coding"] = faceless_coding
        if duration:
            body["duration"] = duration
        return self.request("POST", "/api/v1/jobs", json_body=body)

    def get_job(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/jobs/{job_id}")

    def download_coding_kit(self, job_id: str) -> Any:
        """Return coding_kit url/path from get_job. Never downloads zip bytes."""
        job = self.get_job(job_id)
        kit = job.get("coding_kit") if isinstance(job, dict) else None
        if not kit:
            raise CliptudeApiError("Coding kit is not ready yet. Poll until status is awaiting_project.")
        return kit

    def download_coding_kit_file(self, job_id: str, dest: Path) -> Path:
        url = f"{self.base_url}/api/v1/jobs/{job_id}/coding-kit"
        try:
            with httpx.stream(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/zip, application/json",
                    **django_proxy_headers(self.base_url),
                },
                timeout=self.timeout,
                follow_redirects=True,
            ) as resp:
                if resp.status_code >= 400:
                    try:
                        payload = resp.read()
                        data = json.loads(payload)
                    except Exception:
                        data = {"error": resp.reason_phrase}
                    message = data.get("error") if isinstance(data, dict) else str(data)
                    raise CliptudeApiError(message or f"HTTP {resp.status_code}", status=resp.status_code, payload=data)
                dest = Path(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
        except CliptudeApiError:
            raise
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        return dest

    def submit_faceless_project(self, job_id: str, zip_path: str | Path) -> Any:
        path = Path(zip_path)
        if not path.is_file():
            raise CliptudeApiError(f"Project zip not found: {path}")
        url = f"{self.base_url}/api/v1/jobs/{job_id}/project"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            **django_proxy_headers(self.base_url),
        }
        try:
            with path.open("rb") as fh:
                resp = httpx.post(
                    url,
                    headers=headers,
                    files={"zip": (path.name, fh, "application/zip")},
                    timeout=max(self.timeout, 120.0),
                )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}
        if resp.status_code >= 400:
            message = payload.get("error") if isinstance(payload, dict) else str(payload)
            raise CliptudeApiError(message or f"HTTP {resp.status_code}", status=resp.status_code, payload=payload)
        return payload

    def list_jobs(self, *, status: str | None = None, limit: int = 20, offset: int = 0) -> Any:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self.request("GET", "/api/v1/jobs", params=params)

    def cancel_job(self, job_id: str) -> Any:
        return self.request("POST", f"/api/v1/jobs/{job_id}/cancel")

    def get_account(self) -> Any:
        return self.request("GET", "/api/v1/account")
