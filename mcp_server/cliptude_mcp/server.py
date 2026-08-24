"""stdio / Streamable HTTP MCP server. Never writes to stdout except JSON-RPC."""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Any

from pydantic import Field

from cliptude_mcp.client import CliptudeApiError, CliptudeClient, set_request_token

logger = logging.getLogger("cliptude_mcp")
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


def _client() -> CliptudeClient:
    token = None
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
        if access is not None and getattr(access, "token", None):
            token = access.token
            set_request_token(token)
    except Exception:
        pass
    return CliptudeClient(api_key=token)


def _err(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, CliptudeApiError):
        return {"error": str(exc), "status": exc.status, "detail": exc.payload}
    return {"error": str(exc)}


def mcp_allowed_hosts(resource_url: str | None = None) -> list[str]:
    """Host header values the Streamable HTTP transport should accept.

    The SDK auto-allowlists only localhost when streamable_http_app() is called
    with the default host. Public MCP is reached as mcp.cliptude.com via nginx,
    so authenticated POSTs otherwise return 421 Invalid Host header.
    """
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    extra = (os.environ.get("MCP_ALLOWED_HOSTS") or "").strip()
    if extra:
        hosts.extend(h.strip() for h in extra.split(",") if h.strip())
        return hosts
    from urllib.parse import urlparse

    parsed = urlparse(
        resource_url
        or os.environ.get("MCP_RESOURCE_URL")
        or "https://mcp.cliptude.com/mcp"
    )
    hostname = (parsed.hostname or "").strip()
    if hostname:
        hosts.append(hostname)
        hosts.append(f"{hostname}:*")
    return hosts


def mcp_allowed_origins(resource_url: str | None = None) -> list[str]:
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    extra = (os.environ.get("MCP_ALLOWED_ORIGINS") or "").strip()
    if extra:
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
        return origins
    from urllib.parse import urlparse

    parsed = urlparse(
        resource_url
        or os.environ.get("MCP_RESOURCE_URL")
        or "https://mcp.cliptude.com/mcp"
    )
    hostname = (parsed.hostname or "").strip()
    scheme = (parsed.scheme or "https").strip() or "https"
    if hostname:
        origins.append(f"{scheme}://{hostname}")
        origins.append(f"{scheme}://{hostname}:*")
    return origins


def build_server(*, with_http_auth: bool = False):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    kwargs: dict[str, Any] = {
        "instructions": (
            "Generate Cliptude videos from a saved template. "
            "Call list_templates, then create_job with template_id + title + prompt or script. "
            "create_job returns immediately; poll get_job until completed or failed. "
            "Never set prompt to the video title. If the user only named a topic or title, "
            "web-search current facts, write spoken narration, and pass it as script. "
            "If the user pastes a full narration, pass it as script, not prompt."
        ),
    }
    if with_http_auth:
        from cliptude_mcp.auth import CliptudeTokenVerifier, auth_settings

        kwargs["token_verifier"] = CliptudeTokenVerifier()
        kwargs["auth"] = auth_settings()

    mcp = MCPServer("Cliptude", **kwargs)

    @mcp.tool(
        title="List templates",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_templates(
        limit: Annotated[int, Field(ge=1, le=100, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Offset")] = 0,
    ) -> dict[str, Any]:
        """List saved video templates (id, name, format). Empty list means the user has not saved a template in Cliptude yet."""
        try:
            return _client().list_templates(limit=limit, offset=offset)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get template",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_template(
        template_id: Annotated[str, Field(min_length=8, description="Template UUID")],
    ) -> dict[str, Any]:
        """Get one saved template summary (not internal JSON)."""
        try:
            return _client().get_template(template_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create video job")
    def create_job(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief for Cliptude to write the VO. Never the video title. Prefer script after research.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration. Use this when the user gave a topic (after web search) or pasted a script. Cliptude does not rewrite it.")] = None,
        faceless_coding: Annotated[
            str | None,
            Field(description='Use "external" for Faceless Explainer BYO HTML. Omit for Cliptude coding.'),
        ] = None,
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template (or estimate from script)."),
        ] = None,
    ) -> dict[str, Any]:
        """Start generation and return a job id immediately. Do not wait for the MP4. Poll get_job. Provide prompt and/or script. Never copy the title into prompt; for a topic-only request write a spoken script after web search and pass script."""
        try:
            return _client().create_job(
                template_id=template_id,
                title=title,
                prompt=prompt,
                script=script,
                faceless_coding=faceless_coding,
                duration=duration,
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get job",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_job(
        job_id: Annotated[str, Field(min_length=8, description="Job / video UUID")],
    ) -> dict[str, Any]:
        """Job status, progress, error, and output url (SaaS) or path (Desktop)."""
        try:
            return _client().get_job(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Download coding kit",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def download_coding_kit(
        job_id: Annotated[str, Field(min_length=8, description="Job UUID")],
    ) -> dict[str, Any]:
        """Return coding_kit url or path after awaiting_project. Never returns zip bytes. Do not wait for the MP4."""
        try:
            return _client().download_coding_kit(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Submit Faceless project")
    def submit_faceless_project(
        job_id: Annotated[str, Field(min_length=8, description="Job UUID")],
        zip_path: Annotated[str, Field(min_length=1, description="Local path to the project zip")],
    ) -> dict[str, Any]:
        """Upload a local Faceless project zip (storyboard.json + frames). Returns 202. Poll get_job. Do not wait for the MP4."""
        try:
            return _client().submit_faceless_project(job_id, zip_path)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="List jobs",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_jobs(
        status: Annotated[str | None, Field(description="Optional status filter")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Recent jobs for this account."""
        try:
            return _client().list_jobs(status=status, limit=limit, offset=offset)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Cancel job",
        annotations=ToolAnnotations(destructive_hint=True, open_world_hint=False),
    )
    def cancel_job(
        job_id: Annotated[str, Field(min_length=8, description="Job UUID")],
    ) -> dict[str, Any]:
        """Cancel a queued or running job."""
        try:
            return _client().cancel_job(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get account",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_account() -> dict[str, Any]:
        """Plan, credits remaining, and concurrency in use."""
        try:
            return _client().get_account()
        except Exception as exc:
            return _err(exc)

    @mcp.resource("cliptude://templates")
    def templates_resource() -> str:
        """Saved templates."""
        try:
            return str(_client().list_templates(limit=50))
        except Exception as exc:
            return str(_err(exc))

    @mcp.resource("cliptude://jobs/{job_id}")
    def job_resource(job_id: str) -> str:
        """One job snapshot."""
        try:
            return str(_client().get_job(job_id))
        except Exception as exc:
            return str(_err(exc))

    @mcp.prompt()
    def generate_video() -> str:
        """Use a saved Cliptude template to generate a video from a topic or a custom script."""
        return (
            "List my Cliptude templates, pick the one I name, then create_job "
            "with title plus prompt (topic) or script (full narration). Poll get_job."
        )

    return mcp


def _cors_json(payload: dict[str, Any]):
    from starlette.responses import JSONResponse

    response = JSONResponse(payload)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _discovery_starlette(inner):
    """Cursor's OAuth callback rediscovers AS via origin-root well-known, not WWW-Authenticate.

    RFC 9728 path-inserted metadata lives at /.well-known/oauth-protected-resource/mcp.
    Cursor also requests /.well-known/oauth-protected-resource (no /mcp). A 404 there
    makes it treat mcp.cliptude.com as the authorization server and hang on token exchange.

    The inner Streamable HTTP app's lifespan must be forwarded. Mount() does not
    start it, and without session_manager.run() every POST /mcp raises
    ``Task group is not initialized``.
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    from cliptude_mcp.auth import authorization_server_metadata, protected_resource_metadata

    async def options(_request):
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    async def prm(_request):
        return _cors_json(protected_resource_metadata())

    async def oauth_as(_request):
        return _cors_json(authorization_server_metadata())

    inner_lifespan = inner.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with inner_lifespan(inner):
            yield

    return Starlette(
        routes=[
            Route("/.well-known/oauth-protected-resource", prm, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource", options, methods=["OPTIONS"]),
            Route("/.well-known/oauth-authorization-server", oauth_as, methods=["GET"]),
            Route("/.well-known/oauth-authorization-server", options, methods=["OPTIONS"]),
            Mount("/", app=inner),
        ],
        lifespan=lifespan,
    )


def _run_http(mcp, host: str, port: int) -> None:
    inner = None
    transport_security = None
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=mcp_allowed_hosts(),
            allowed_origins=mcp_allowed_origins(),
        )
    except ImportError:
        transport_security = None
    for attr in ("streamable_http_app", "http_app"):
        fn = getattr(mcp, attr, None)
        if not callable(fn):
            continue
        try:
            inner = fn(host=host, transport_security=transport_security)
        except TypeError:
            inner = fn()
        break
    if inner is None:
        mcp.run(transport="streamable-http", host=host, port=port)
        return
    import uvicorn

    uvicorn.run(_discovery_starlette(inner), host=host, port=port)


def main() -> None:
    transport = (os.environ.get("CLIPTUDE_MCP_TRANSPORT") or "stdio").strip().lower()
    http_mode = transport in {"http", "streamable-http", "streamable_http"}
    mcp = build_server(with_http_auth=http_mode)
    if http_mode:
        host = os.environ.get("CLIPTUDE_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("CLIPTUDE_MCP_PORT") or "8787")
        logger.info("Starting Streamable HTTP MCP on %s:%s", host, port)
        _run_http(mcp, host, port)
        return
    mcp.run()


if __name__ == "__main__":
    main()
