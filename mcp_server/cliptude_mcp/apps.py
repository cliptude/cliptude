"""MCP Apps (interactive widgets) for hosts that render them: Claude Desktop, claude.ai, ChatGPT.

Spec: https://modelcontextprotocol.io/specification/draft/extensions/apps (SEP-1865, `io.modelcontextprotocol/ui`).

Each widget is a `ui://cliptude/...` HTML resource (served as `text/html;profile=mcp-app`) that the host shows in a
sandboxed iframe next to a tool's result:

- list_avatars           -> ui://cliptude/avatars.html      avatar thumbnails, pick one
- list_voices            -> ui://cliptude/voices.html       play voice previews, pick one
- list_vector_characters -> ui://cliptude/characters.html   Vector Explainer cast and uploads, pick one
- get_render, show_video -> ui://cliptude/player.html       watch the MP4, or "not rendered yet"
- show_choices           -> ui://cliptude/choices.html      music (play), backgrounds, style packs, themes, captions
- show_publish_kit       -> ui://cliptude/publish-kit.html  thumbnails, title ideas, description, chapters, tags
- list_templates         -> ui://cliptude/templates.html    template gallery, "Use this template"
- create_template, get_template
                         -> ui://cliptude/template.html     one template's card: voice, presenter, music, look, switches
- create_job, create_plan_job, create_script, create_voiceover, continue_job, render_product_video,
  draft_product_script, run_agent, render_project, create_avatar
                         -> ui://cliptude/review.html       the 428 review to confirm (or the result card)
- get_job, get_product_video_status, get_narration
                         -> ui://cliptude/job.html          progress card, the script to approve, or the publish kit

The existing tools keep their names, parameters and JSON (the text content stays the same for clients without
Apps); `bind_existing_tools` adds `_meta.ui.resourceUri` (plus ChatGPT's `openai/outputTemplate` alias) and tags a
428 review with the tool that returned it (`tool`), so the review widget knows what to call again.

A widget picks by posting a `ui/message` into the chat ("Use the voice ... (voice_id ...)"), so the model gets the
id without the user typing it. Calls a widget makes itself (`tools/call`) happen only on the user's click: the
review widget's Confirm resends the reviewed call with its review_token, never on its own.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from importlib import resources
from typing import Annotated, Any

from pydantic import Field

try:  # mcp >= 2.3 ships the Apps extension; older SDKs simply serve no widgets.
    from mcp.server.apps import APP_MIME_TYPE, Apps, ResourceCsp, ResourcePermissions
except ImportError:  # pragma: no cover - exercised only on an old SDK
    Apps = None  # type: ignore[assignment,misc]
    APP_MIME_TYPE = "text/html;profile=mcp-app"
    ResourceCsp = ResourcePermissions = None  # type: ignore[assignment,misc]

AVATARS_URI = "ui://cliptude/avatars.html"
VOICES_URI = "ui://cliptude/voices.html"
CHARACTERS_URI = "ui://cliptude/characters.html"
PLAYER_URI = "ui://cliptude/player.html"
CHOICES_URI = "ui://cliptude/choices.html"
PUBLISH_KIT_URI = "ui://cliptude/publish-kit.html"
TEMPLATES_URI = "ui://cliptude/templates.html"
TEMPLATE_URI = "ui://cliptude/template.html"
REVIEW_URI = "ui://cliptude/review.html"
JOB_URI = "ui://cliptude/job.html"

# uri -> (file, name, description)
WIDGETS: dict[str, tuple[str, str, str]] = {
    AVATARS_URI: ("avatars.html", "Avatar picker", "Avatar thumbnails to pick the presenter of an Avatar Video."),
    VOICES_URI: ("voices.html", "Voice picker", "Voice previews to listen to and pick a narration voice."),
    CHARACTERS_URI: ("characters.html", "Character picker", "Vector Explainer characters to pick from."),
    PLAYER_URI: ("player.html", "Video player", "Plays a finished Cliptude MP4, or shows that it is not rendered yet."),
    CHOICES_URI: ("choices.html", "Setting picker", "Previews of music, backgrounds and styles to pick a template setting."),
    PUBLISH_KIT_URI: ("publish-kit.html", "Publish kit", "Thumbnails, title ideas, description, chapters and tags."),
    TEMPLATES_URI: ("templates.html", "Templates", "The user's saved templates, to pick one for a video."),
    TEMPLATE_URI: ("template.html", "Template", "One template: voice, presenter, music, background, style and switches."),
    REVIEW_URI: ("review.html", "Review and confirm", "What a job or spend does and costs, for the user to confirm."),
    JOB_URI: ("job.html", "Job", "A job's progress, its script to approve, or its publish kit when ready."),
}

# Tools whose first call answers with a 428 review (detail.review + review_token) before they spend credits.
REVIEW_TOOLS: tuple[str, ...] = (
    "create_job", "create_plan_job", "create_script", "create_voiceover", "continue_job", "render_product_video",
    "draft_product_script", "run_agent", "render_project", "create_avatar",
)
# Of those, the ones that take acknowledge ids (the job review's acknowledgements_required).
ACKNOWLEDGE_TOOLS: frozenset[str] = frozenset({
    "create_job", "create_plan_job", "create_script", "create_voiceover", "draft_product_script",
})

# Existing tools that get a widget, by name.
BOUND_TOOLS: dict[str, str] = {
    "list_avatars": AVATARS_URI,
    "list_voices": VOICES_URI,
    "list_vector_characters": CHARACTERS_URI,
    "get_render": PLAYER_URI,
    "list_templates": TEMPLATES_URI,
    "create_template": TEMPLATE_URI,
    "get_template": TEMPLATE_URI,
    "get_job": JOB_URI,
    "get_product_video_status": JOB_URI,
    "get_narration": JOB_URI,
    **{name: REVIEW_URI for name in REVIEW_TOOLS},
}

# Tools a widget calls itself (tools/call). ChatGPT wants openai/widgetAccessible on them.
WIDGET_CALLED_TOOLS: frozenset[str] = frozenset({
    *REVIEW_TOOLS, "list_avatars", "design_voice_previews", "save_designed_voice", "clone_voice", "show_publish_kit",
})

# ChatGPT's short status lines while a widget tool runs and after (openai/toolInvocation/*, at most 64 chars).
INVOCATION_TEXT: dict[str, tuple[str, str]] = {
    "list_avatars": ("Loading avatars…", "Avatars ready"),
    "list_voices": ("Loading voices…", "Voices ready"),
    "list_vector_characters": ("Loading characters…", "Characters ready"),
    "get_render": ("Checking the export…", "Export checked"),
    "show_video": ("Loading the video…", "Video ready"),
    "show_choices": ("Loading choices…", "Choices ready"),
    "show_publish_kit": ("Opening the publish kit…", "Publish kit ready"),
    "list_templates": ("Loading templates…", "Templates ready"),
    "create_template": ("Saving the template…", "Template saved"),
    "get_template": ("Loading the template…", "Template ready"),
    "get_job": ("Checking the job…", "Job checked"),
    "get_product_video_status": ("Checking the product video…", "Product video checked"),
    "get_narration": ("Loading the narration…", "Narration ready"),
    **{name: ("Preparing the review…", "Review ready") for name in REVIEW_TOOLS},
}

# Where the widgets' pictures, audio and video come from (CSP img-src/media-src via resourceDomains).
MEDIA_DOMAINS: tuple[str, ...] = (
    # Avatar previews (videos/avatar_preview.py: HeyGen thumbnails proxied and signed, because HeyGen's own
    # files2.heygen.ai links answer 403 to browsers), built-in Vector characters (/static/vector_explainer/),
    # uploaded characters (/media/), library music (/static/music/), thumbnails not yet on the CDN (/media/).
    "https://cliptude.com",
    "https://www.cliptude.com",
    # Public R2 bucket: library background thumbnails, generated thumbnails, uploaded backgrounds, samples.
    "https://cdn.cliptude.com",
    # Signed MP4 links of finished videos and editor renders (private R2 bucket, videos/r2_storage.py).
    "https://*.r2.cloudflarestorage.com",
    # Voice previews: ElevenLabs (shared-library previews on storage.googleapis.com/eleven-public-prod and the
    # API host), Cartesia (preview_file_url), Voicetory (preview_audio). MiniMax and Inworld have none.
    "https://storage.googleapis.com",
    "https://api.us.elevenlabs.io",
    "https://api.elevenlabs.io",
    "https://files.cartesia.ai",
    "https://voicetory.com",
)
# The Desktop app serves its own media on a random localhost port (stdio MCP only).
LOCAL_MEDIA_DOMAINS: tuple[str, ...] = ("http://127.0.0.1:*", "http://localhost:*")

# show_choices: field -> (kind the widget draws, heading)
CHOICE_FIELDS: dict[str, tuple[str, str]] = {
    "background_music_library_file": ("music", "Background music"),
    "background_image": ("image", "Background"),
    "faceless_documentary_style": ("style", "Style pack"),
    "visual_theme": ("theme", "Visual theme"),
    "caption_style": ("caption", "Caption style"),
    "vector_illustration_style": ("text", "Illustration style"),
    "vector_reveal_mode": ("text", "Motion"),
}
# Fields an agent-planned video also sets in its plan's look.
_PLAN_LOOK_FIELDS = frozenset({"background_music_library_file", "visual_theme", "faceless_documentary_style"})


def _permissions() -> dict[str, Any]:
    if ResourcePermissions is None:
        return {}
    return {
        # Copy buttons (titles, description, tags) use the Clipboard API.
        PUBLISH_KIT_URI: ResourcePermissions(clipboard_write={}),
        # "Clone a voice" can record a sample in the widget; uploading a file works without it.
        VOICES_URI: ResourcePermissions(microphone={}),
    }


_PERMISSIONS = _permissions()


def apps_available() -> bool:
    return Apps is not None


def widget_html(file_name: str) -> str:
    """A widget's HTML with the shared bridge and stylesheet inlined (no external script or style)."""
    pkg = resources.files("cliptude_mcp") / "widgets"
    html = (pkg / file_name).read_text(encoding="utf-8")
    bridge = (pkg / "bridge.js").read_text(encoding="utf-8")
    css = (pkg / "base.css").read_text(encoding="utf-8")
    views = (pkg / "views.js").read_text(encoding="utf-8")
    html = html.replace("/*@BASE_CSS@*/", css).replace("/*@BRIDGE_JS@*/", bridge)
    return html.replace("/*@VIEWS_JS@*/", views)


def media_domains(*, local: bool) -> list[str]:
    return list(MEDIA_DOMAINS) + (list(LOCAL_MEDIA_DOMAINS) if local else [])


def _openai_meta(name: str, uri: str) -> dict[str, Any]:
    """ChatGPT's aliases (Apps SDK reference): it reads `_meta.ui.resourceUri` too, and documents these."""
    meta: dict[str, Any] = {"openai/outputTemplate": uri}
    invoking, invoked = INVOCATION_TEXT.get(name, ("", ""))
    if invoking:
        meta["openai/toolInvocation/invoking"] = invoking[:64]
        meta["openai/toolInvocation/invoked"] = invoked[:64]
    if name in WIDGET_CALLED_TOOLS:
        meta["openai/widgetAccessible"] = True
    return meta


def _ui_meta(uri: str, name: str = "") -> dict[str, Any]:
    # `ui.resourceUri` is the spec's key; the flat `ui/resourceUri` is its deprecated form, kept for hosts that
    # shipped before the rename.
    return {"ui": {"resourceUri": uri}, "ui/resourceUri": uri, **_openai_meta(name, uri)}


def _resource_meta(uri: str, description: str, *, local: bool) -> dict[str, Any]:
    """`_meta` of a widget resource: the spec's `ui` block plus ChatGPT's openai/widget* aliases."""
    domains = media_domains(local=local)
    ui: dict[str, Any] = {"csp": ResourceCsp(resource_domains=domains).model_dump(by_alias=True, exclude_none=True)}
    permissions = _PERMISSIONS.get(uri)
    if permissions is not None:
        ui["permissions"] = permissions.model_dump(by_alias=True, exclude_none=True)
    ui["prefersBorder"] = True
    return {
        "ui": ui,
        # The widgets fetch nothing themselves (no connect_domains); pictures, audio and video come from these.
        "openai/widgetCSP": {"connect_domains": [], "resource_domains": domains},
        "openai/widgetDescription": description,
        "openai/widgetPrefersBorder": True,
    }


def build_apps(
    client: Callable[[], Any],
    err: Callable[[Exception], dict[str, Any]],
    *,
    local: bool,
):
    """The Apps extension with every widget resource and the new show_* tools; None on an SDK without Apps."""
    if Apps is None:
        return None
    from mcp.types import ToolAnnotations

    from mcp.server.mcpserver.resources import TextResource

    apps = Apps()
    for uri, (file_name, name, description) in WIDGETS.items():
        apps.add_resource(
            TextResource(
                uri=uri,
                name=name,
                title=name,
                description=description,
                mime_type=APP_MIME_TYPE,
                meta=_resource_meta(uri, description, local=local),
                text=widget_html(file_name),
            )
        )

    @apps.tool(
        resource_uri=CHOICES_URI,
        meta={"ui/resourceUri": CHOICES_URI, **_openai_meta("show_choices", CHOICES_URI)},
        name="show_choices",
        title="Show setting choices",
        annotations=ToolAnnotations(title="Show setting choices", read_only_hint=True, open_world_hint=False),
    )
    def show_choices(
        format: Annotated[str, Field(description="Video format, e.g. faceless_documentary or vector_explainer")],
        field: Annotated[
            str,
            Field(description="Setting to choose: " + ", ".join(CHOICE_FIELDS)),
        ],
    ) -> dict[str, Any]:
        """Let the user preview and pick one template setting: background music (play each track), background picture, style pack (colour swatches), visual theme, caption style, or the Vector Explainer illustration style / motion. In hosts that show apps the user picks in a widget and the choice arrives as their message; otherwise read choices and ask. Returns {field, choices:[{value, label, ...preview links}], default, usage}."""
        field = (field or "").strip()
        if field not in CHOICE_FIELDS:
            return {"error": f"field must be one of: {', '.join(CHOICE_FIELDS)}"}
        try:
            options = client().template_options(format)
        except Exception as exc:
            return err(exc)
        return choices_payload(options, format, field)

    @apps.tool(
        resource_uri=PLAYER_URI,
        meta={"ui/resourceUri": PLAYER_URI, **_openai_meta("show_video", PLAYER_URI)},
        name="show_video",
        title="Show video",
        annotations=ToolAnnotations(title="Show video", read_only_hint=True, open_world_hint=False),
    )
    def show_video(
        job_id: Annotated[str, Field(min_length=8, description="Job / video UUID")],
    ) -> dict[str, Any]:
        """Show a finished job's MP4 in a player the user can watch in the chat (hosts that show apps), with an open link. For an edited video, get_render shows its export the same way. Returns {job_id, title, status, url}; url is empty until the video is ready."""
        try:
            job = client().get_job(job_id)
        except Exception as exc:
            return err(exc)
        return video_payload(job, job_id)

    @apps.tool(
        resource_uri=PUBLISH_KIT_URI,
        meta={"ui/resourceUri": PUBLISH_KIT_URI, **_openai_meta("show_publish_kit", PUBLISH_KIT_URI)},
        name="show_publish_kit",
        title="Show publish kit",
        annotations=ToolAnnotations(title="Show publish kit", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def show_publish_kit(
        job_id: Annotated[str, Field(min_length=8, description="Completed video job id")],
    ) -> dict[str, Any]:
        """The finished video in a player, then its generated thumbnails, title ideas, YouTube description, chapters and tags with copy buttons (hosts that show apps). Call it as soon as a video is ready (rendered, or built and waiting for review): it is the one place the user watches the video and gets everything to publish it. Opens the video's editor project like open_editor_project. Returns {job_id, project_id, title, video, thumbnails, publish_metadata}; video.url is the latest export, else the job's own MP4, empty until one exists."""
        try:
            data = client().open_editor_project(job_id)
        except Exception as exc:
            return err(exc)
        payload = publish_kit_payload(data, job_id)
        if not payload.get("error") and not (payload.get("video") or {}).get("url"):
            try:
                job = client().get_job(job_id)
            except Exception:
                job = None
            output = job.get("output") if isinstance(job, dict) and isinstance(job.get("output"), dict) else {}
            if output.get("url"):
                payload["video"] = {"url": output["url"], "source": "original", "note": ""}
        return payload

    return apps


def _tag_reviews(tool: Any, name: str) -> None:
    """A 428 review result also names the tool that returned it, so the review widget can call it again."""
    original = tool.fn
    if getattr(original, "_cliptude_tagged", False) or getattr(tool, "is_async", False):
        return

    @functools.wraps(original)
    def tagged(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if isinstance(result, dict) and result.get("status") == 428 and "tool" not in result:
            return {**result, "tool": name}
        return result

    tagged._cliptude_tagged = True  # type: ignore[attr-defined]
    tool.fn = tagged


def bind_existing_tools(mcp: Any) -> list[str]:
    """Stamp `_meta.ui.resourceUri` on the existing tools that have a widget; returns the names bound.

    The tools are registered by server.py (and creation.py) as before, so their names and parameters do not
    change; a 428 review also says which tool returned it (`tool`).
    """
    manager = getattr(mcp, "_tool_manager", None)
    bound = []
    for name, uri in BOUND_TOOLS.items():
        tool = manager.get_tool(name) if manager is not None else None
        if tool is None:
            continue
        tool.meta = {**(tool.meta or {}), **_ui_meta(uri, name)}
        if name in REVIEW_TOOLS:
            _tag_reviews(tool, name)
        bound.append(name)
    for name in sorted(WIDGET_CALLED_TOOLS - set(BOUND_TOOLS)):
        tool = manager.get_tool(name) if manager is not None else None
        if tool is not None:
            tool.meta = {**(tool.meta or {}), "openai/widgetAccessible": True}
    return bound


def choices_payload(options: Any, format_value: str, field: str) -> dict[str, Any]:
    kind, label = CHOICE_FIELDS[field]
    if not isinstance(options, dict):
        return {"error": "Cliptude returned no template options."}
    if options.get("error"):
        return options
    entry = (options.get("fields") or {}).get(field)
    if not isinstance(entry, dict):
        return {"error": f"{format_value} templates have no {field} setting."}
    details = entry.get("choice_details")
    if not isinstance(details, list):  # An older Cliptude server: values only.
        details = [{"value": value, "label": str(value)} for value in entry.get("choices") or []]
    usage = f"Set {field} in create_template settings"
    usage += " (or in the plan's look on an agent-planned video)." if field in _PLAN_LOOK_FIELDS else "."
    if field == "background_music_library_file":
        usage += " With it, set background_music_mode to library; none means no music."
    return {
        "format": format_value,
        "field": field,
        "kind": kind,
        "label": label,
        "description": entry.get("description") or entry.get("note") or "",
        "default": entry.get("default"),
        "current": entry.get("default"),
        "choices": details,
        "usage": usage,
    }


def video_payload(job: Any, job_id: str) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {"error": "Cliptude returned no job."}
    if job.get("error") and not job.get("status"):
        return job
    output = job.get("output") if isinstance(job.get("output"), dict) else {}
    return {
        "job_id": str(job.get("id") or job.get("job_id") or job_id),
        "title": job.get("title") or "",
        "status": job.get("status") or "",
        "progress": job.get("progress"),
        "current_step": job.get("current_step") or "",
        "error": job.get("error") or "",
        "url": output.get("url") or "",
        "path": output.get("path") or "",
        "note": (
            "Saved on this computer: open path." if output.get("path") and not output.get("url") else ""
        ),
    }


def _project_video(project: dict[str, Any]) -> dict[str, Any]:
    """The newest finished export of the editor project, for the publish kit's player."""
    render = project.get("latest_render") if isinstance(project.get("latest_render"), dict) else {}
    url = ""
    if str(render.get("status") or "completed").lower() == "completed":
        url = str(render.get("output_url") or project.get("latest_render_url") or "")
    if url:
        return {"url": url, "source": "export", "note": ""}
    note = "Not rendered yet: render_project makes the MP4." if render.get("status") not in (None, "", "completed") else ""
    return {"url": "", "source": "", "note": note}


def publish_kit_payload(data: Any, job_id: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"error": "Cliptude returned no editor project."}
    if data.get("error"):
        return data
    project = data.get("project") if isinstance(data.get("project"), dict) else data
    meta = project.get("publish_metadata") if isinstance(project.get("publish_metadata"), dict) else {}
    return {
        "job_id": job_id,
        "project_id": project.get("id"),
        "title": project.get("title") or "",
        "video": _project_video(project),
        "thumbnails": project.get("thumbnails") or {"status": "", "items": []},
        "publish_metadata": {
            "title_variants": meta.get("title_variants") or [],
            "description": meta.get("description") or "",
            "chapters": meta.get("chapters") or "",
            "tags": meta.get("tags") or [],
            "downloads": meta.get("downloads") or {},
        },
    }
