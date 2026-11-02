"""Browser fallback for the widgets, for MCP clients that cannot show MCP Apps.

Claude Code and Codex in a terminal, Cursor and other clients without MCP Apps cannot draw the avatar, voice,
music or review widgets. For them the widget-bound tools add `browser_picker_url` and `pick_id` to their
result: a cliptude.com/pick/... link (signed, 15 minutes, only for the signed-in owner) that opens the same
widget in the user's browser. Their pick, or their click on Confirm in a review, is stored on Cliptude;
`get_pick(pick_id)` reads it. The server builds what the page shows (POST /api/v1/picks with where to read it
from, never the content), and a review is only approved there: the agent then resends the call with the
review_token from get_pick. Clients that negotiated MCP Apps get exactly the old results.

How support is detected: the client's `initialize` capabilities (or the per-request capabilities on
2026-07-28) carry `extensions["io.modelcontextprotocol/ui"]` with the `text/html;profile=mcp-app` MIME type
(`mcp.server.apps.client_supports_apps`). The SDK parses `initialize` with the latest schema, so a client on
protocol 2025-11-25 that sends `capabilities.extensions` is seen too, although the server cannot advertise the
extension back at that version. Hosts that put it under `experimental`, and the hosts known to render widgets
(claude.ai / Claude Desktop, ChatGPT) by client name, count as supporting it as well.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

EXTENSION_ID = "io.modelcontextprotocol/ui"
APP_MIME_TYPE = "text/html;profile=mcp-app"
# Client names (initialize clientInfo.name) of hosts that render MCP Apps widgets even if they do not say so.
WIDGET_HOST_CLIENTS = frozenset({"claude-ai", "openai-mcp", "chatgpt"})
CTX_KWARG = "_cliptude_ctx"

PICKER_NOTE = (
    "This client shows no widgets, so the choice opens in the user's browser: give the user browser_picker_url "
    "(in a terminal agent, offer to open it), wait until they say they are done, then call get_pick with "
    "pick_id. Only status \"done\" carries their pick; a review counts as approved only when get_pick shows "
    "confirmed: true (or the user says OK in this chat). The link works for 15 minutes, once, for the signed-in "
    "owner only."
)

# Tools whose results get a browser picker, and the pick kind.
LIST_KINDS: dict[str, str] = {
    "list_avatars": "avatars",
    "list_voices": "voices",
    "list_vector_characters": "characters",
    "show_choices": "choices",
    "list_templates": "templates",
    "get_template": "template",
    "create_template": "template",
    "get_job": "job",
    "get_product_video_status": "job",
    "get_render": "video",
    "show_video": "video",
    "show_publish_kit": "publish_kit",
}


def enabled() -> bool:
    """CLIPTUDE_BROWSER_PICKER=0 turns the fallback off (an operator switch, and the tests' default)."""
    return (os.environ.get("CLIPTUDE_BROWSER_PICKER") or "1").strip().lower() not in ("0", "false", "no", "off")


def client_renders_apps(ctx: Any) -> bool:
    """Whether the connected client shows MCP Apps widgets. Unknown (no context) counts as yes: no fallback."""
    if ctx is None:
        return True
    try:
        from mcp.server.apps import client_supports_apps
    except ImportError:  # pragma: no cover - an SDK without Apps serves no widgets at all
        client_supports_apps = None
    try:
        if client_supports_apps is not None and client_supports_apps(ctx):
            return True
        capabilities = ctx.client_capabilities
    except Exception:
        return True
    experimental = getattr(capabilities, "experimental", None) or {}
    if isinstance(experimental, dict) and EXTENSION_ID in experimental:
        return True
    try:
        params = ctx.request_context.session.client_params
        name = str(getattr(getattr(params, "client_info", None), "name", "") or "").strip().lower()
    except Exception:
        name = ""
    return name in WIDGET_HOST_CLIENTS


def _review_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """The reviewed call's arguments, for the pick to hand back; file contents stay with the agent."""
    out = {}
    for key, value in args.items():
        if key in ("review_token", "acknowledge") or value is None:
            continue
        if key.endswith("_base64"):
            out[key] = "<the same file as before>"
            continue
        out[key] = value
    return out


def _is_script(data: dict[str, Any]) -> bool:
    """views.js isScript: a written script waiting for the user's approval."""
    if not isinstance(data.get("script"), str) or not data["script"].strip():
        return False
    if data.get("stage"):
        return data["stage"] == "script_ready"
    return data.get("output_kind") == "script" and data.get("status") == "completed"


def _is_ready(data: dict[str, Any]) -> bool:
    stage = data.get("stage") or ""
    if stage:
        return stage in ("ready_for_review", "rendered")
    kind = data.get("output_kind") or "video"
    return data.get("status") in ("ready_for_edit", "completed") and kind in ("video", "plan")


def pick_source(name: str, args: dict[str, Any], result: Any) -> tuple[str, dict[str, Any]] | None:
    """(kind, payload_source) of the browser picker for one tool result, or None when there is nothing to pick."""
    from cliptude_mcp.apps import REVIEW_TOOLS

    if not isinstance(result, dict):
        return None
    if name in REVIEW_TOOLS:
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        if result.get("status") == 428 and detail.get("review") and detail.get("review_token"):
            return "review", {"tool": name, "arguments": _review_arguments(args),
                              "review_token": detail["review_token"]}
        return None
    if result.get("error"):
        return None
    kind = LIST_KINDS.get(name)
    if kind is None:
        return None
    if name == "list_avatars":
        return kind, {k: args.get(k) for k in ("section", "search", "gender", "limit", "offset") if args.get(k) is not None}
    if name == "list_voices":
        return kind, {k: args.get(k) for k in ("provider", "language") if args.get(k)}
    if name == "show_choices":
        return kind, {"format": args.get("format"), "field": args.get("field")}
    if name == "get_template":
        return kind, {"template_id": args.get("template_id")}
    if name == "create_template":
        template_id = result.get("id") or (result.get("template") or {}).get("id")
        return (kind, {"template_id": template_id}) if template_id else None
    if name in ("get_job", "get_product_video_status"):
        source = {"job_id": args.get("job_id"), "product": name == "get_product_video_status"}
        if _is_script(result):
            return "script", source
        return (kind, source) if _is_ready(result) else None  # progress alone: nothing to pick
    if name == "get_render":
        render = result.get("render") if isinstance(result.get("render"), dict) else {}
        return (kind, {"render_id": args.get("render_id")}) if render.get("output_url") else None
    if name == "show_video":
        return (kind, {"job_id": args.get("job_id")}) if result.get("url") else None
    if name == "show_publish_kit":
        return kind, {"job_id": args.get("job_id")}
    return kind, {}


def _with_picker(result: dict[str, Any], pick: Any) -> dict[str, Any]:
    if not isinstance(pick, dict) or not pick.get("url") or not pick.get("pick_id"):
        return result
    return {**result, "browser_picker_url": pick["url"], "pick_id": pick["pick_id"],
            "browser_picker": PICKER_NOTE}


def _wrap(tool: Any, name: str, client: Callable[[], Any]) -> None:
    original = tool.fn
    if getattr(original, "_cliptude_picker", False) or getattr(tool, "is_async", False):
        return
    if tool.context_kwarg not in (None, CTX_KWARG):
        return  # the tool takes the context itself; leave it alone

    @functools.wraps(original)
    def with_picker(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.pop(CTX_KWARG, None)
        result = original(*args, **kwargs)
        if not enabled() or client_renders_apps(ctx):
            return result
        source = pick_source(name, kwargs, result)
        if source is None:
            return result
        try:
            pick = client().create_pick(source[0], source[1])
        except Exception:
            return result  # the tool's own answer matters more than the link
        return _with_picker(result, pick)

    with_picker._cliptude_picker = True  # type: ignore[attr-defined]
    tool.fn = with_picker
    tool.context_kwarg = CTX_KWARG


def add_browser_fallback(mcp: Any, client: Callable[[], Any]) -> list[str]:
    """Give the widget-bound tools a browser picker for clients without MCP Apps; returns the names wrapped."""
    from cliptude_mcp.apps import REVIEW_TOOLS

    manager = getattr(mcp, "_tool_manager", None)
    wrapped = []
    for name in (*LIST_KINDS, *REVIEW_TOOLS):
        tool = manager.get_tool(name) if manager is not None else None
        if tool is None:
            continue
        _wrap(tool, name, client)
        if getattr(tool.fn, "_cliptude_picker", False):
            wrapped.append(name)
    return wrapped


def register_pick_tools(mcp: Any, client: Callable[[], Any], err: Callable[[Exception], dict[str, Any]]) -> None:
    from mcp.types import ToolAnnotations

    # A click in a widget (Claude): the pick is kept on Cliptude, because claude.ai warns on messages a widget
    # drafts and does not pass ui/update-model-context to the model (videos/widget_choices.py).
    @mcp.tool(
        title="Save choice",
        annotations=ToolAnnotations(title="Save choice", read_only_hint=False, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
        meta={"ui": {"visibility": ["app"]}, "openai/widgetAccessible": True},
    )
    def save_choice(
        message: Annotated[str, Field(min_length=1, max_length=2000, description="The pick in words, with its exact id")],
        choice: Annotated[dict[str, Any] | None, Field(description="The pick's fields, e.g. {template_id: ...}")] = None,
    ) -> dict[str, Any]:
        """Called by Cliptude's picker widgets when the user clicks a choice; not for the agent. The agent reads the pick with get_choice."""
        try:
            return client().save_choice(message, choice)
        except Exception as exc:
            return err(exc)

    @mcp.tool(
        title="Get choice",
        annotations=ToolAnnotations(title="Get choice", read_only_hint=True, open_world_hint=False),
    )
    def get_choice() -> dict[str, Any]:
        """The user's latest click in a Cliptude picker (template, voice, avatar, character, music, background, style), saved in the last two hours. Call it when the user says next, continue or proceed after you showed a picker: message names the pick and its exact id, choice has the fields (e.g. template_id, voice_id, avatar_profile_id), saved_at says when. If choice is null or older than the picker you showed, ask the user to click their pick in the widget."""
        try:
            return client().get_choice()
        except Exception as exc:
            return err(exc)

    @mcp.tool(
        title="Get pick",
        annotations=ToolAnnotations(title="Get pick", read_only_hint=True, open_world_hint=False),
    )
    def get_pick(
        pick_id: Annotated[str, Field(min_length=8, description="pick_id from a result's browser picker")],
    ) -> dict[str, Any]:
        """What the user picked or confirmed on a browser picker page (browser_picker_url, for clients that show no widgets). Returns {status: open|done|expired, result}. Only status done carries their pick: result.message and result.choice (the exact ids), or for a review result.confirmed with tool, arguments, review_token and acknowledge to resend that call once. Call it after the user says they are done; while open, ask them to finish on the page."""
        try:
            return client().get_pick(pick_id)
        except Exception as exc:
            return err(exc)
