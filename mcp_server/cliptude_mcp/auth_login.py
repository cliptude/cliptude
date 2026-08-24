"""Browser OAuth login for the CLI (PKCE + loopback callback)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from cliptude_mcp.client import DEFAULT_SAAS_URL, resolve_base_url
from cliptude_mcp.credentials import save_credentials


class AuthLoginError(Exception):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _issuer() -> str:
    return resolve_base_url().rstrip("/") or DEFAULT_SAAS_URL


def register_public_client(issuer: str, redirect_uri: str, timeout: float = 20.0) -> str:
    resp = httpx.post(
        f"{issuer}/o/register/",
        json={
            "client_name": "Cliptude CLI",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise AuthLoginError(f"Could not register CLI OAuth client ({resp.status_code}).")
    data = resp.json()
    client_id = data.get("client_id")
    if not client_id:
        raise AuthLoginError("OAuth registration did not return a client_id.")
    return str(client_id)


def authorize_url(
    issuer: str,
    *,
    client_id: str,
    redirect_uri: str,
    challenge: str,
    state: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "jobs",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{issuer}/o/authorize/?{query}"


def exchange_code(
    issuer: str,
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    resp = httpx.post(
        f"{issuer}/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise AuthLoginError(f"Token exchange failed ({resp.status_code}).")
    data = resp.json()
    if not data.get("access_token"):
        raise AuthLoginError("Token exchange did not return an access token.")
    return data


def refresh_access_token(creds: dict[str, Any], timeout: float = 20.0) -> dict[str, Any] | None:
    refresh = creds.get("refresh_token")
    client_id = creds.get("client_id")
    issuer = (creds.get("issuer") or _issuer()).rstrip("/")
    if not refresh or not client_id:
        return None
    resp = httpx.post(
        f"{issuer}/o/token/",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        return None
    data = resp.json()
    if not data.get("access_token"):
        return None
    merged = dict(creds)
    merged["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        merged["refresh_token"] = data["refresh_token"]
    save_credentials(merged)
    return merged


def _wait_for_callback(
    host: str,
) -> tuple[HTTPServer, dict[str, str | None], threading.Event]:
    result: dict[str, str | None] = {"code": None, "state": None, "error": None}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.rstrip("/") != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            result["code"] = (qs.get("code") or [None])[0]
            result["state"] = (qs.get("state") or [None])[0]
            result["error"] = (qs.get("error") or [None])[0]
            body = (
                b"<html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h1>Cliptude CLI</h1><p>Signed in. You can close this tab.</p>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer((host, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, result, done


def login(*, open_browser: bool = True) -> dict[str, Any]:
    issuer = _issuer()
    host = "127.0.0.1"
    server, result, done = _wait_for_callback(host)
    try:
        port = server.server_address[1]
        redirect_uri = f"http://{host}:{port}/callback"
        client_id = register_public_client(issuer, redirect_uri)
        verifier, challenge = pkce_pair()
        state = _b64url(secrets.token_bytes(16))
        url = authorize_url(
            issuer,
            client_id=client_id,
            redirect_uri=redirect_uri,
            challenge=challenge,
            state=state,
        )
        if open_browser:
            webbrowser.open(url)
        if not done.wait(300):
            raise AuthLoginError("Timed out waiting for browser sign-in.")
        if result.get("error"):
            raise AuthLoginError(f"Sign-in was denied ({result['error']}).")
        if result.get("state") != state:
            raise AuthLoginError("OAuth state mismatch.")
        if not result.get("code"):
            raise AuthLoginError("No authorization code returned.")
        tokens = exchange_code(
            issuer,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code=str(result["code"]),
            verifier=verifier,
        )
        creds = {
            "issuer": issuer,
            "client_id": client_id,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token") or "",
            "token_type": tokens.get("token_type") or "Bearer",
        }
        save_credentials(creds)
        return creds
    finally:
        server.shutdown()
        server.server_close()
