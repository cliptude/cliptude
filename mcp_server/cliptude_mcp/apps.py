"""MCP Apps (interactive widgets) for hosts that render them: Claude Desktop, claude.ai.

Spec: https://modelcontextprotocol.io/specification/draft/extensions/apps (SEP-1865, `io.modelcontextprotocol/ui`).

Each widget is a `ui://cliptude/...` HTML resource (served as `text/html;profile=mcp-app`) that the host shows in a
sandboxed iframe next to a tool's result:

- list_avatars           -> ui://cliptude/avatars.html      avatar thumbnails, pick one
- list_voices            -> ui://cliptude/voices.html       play voice previews, pick one
- list_vector_characters -> ui://cliptude/characters.html   Vector Explainer cast and uploads, pick one
- get_render, get_product_video_status, show_video
                         -> ui://cliptude/player.html       watch the MP4, or "not rendered yet"
- show_choices           -> ui://cliptude/choices.html      music (play), backgrounds, style packs, themes, captions
- show_publish_kit       -> ui://cliptude/publish-kit.html  thumbnails, title ideas, description, chapters, tags

The existing tools keep their names, parameters and JSON (the text content stays the same for clients without
Apps); only `_meta.ui.resourceUri` is added to them (`bind_existing_tools`). show_choices, show_video and
show_publish_kit are new, thin tools: binding a widget to get_template_options, get_job or the editor-project
tools would draw a widget on every one of their (frequent, polling) calls.

A widget picks by posting a `ui/message` into the chat ("Use the voice ... (voice_id ...)"), so the model gets the
id without the user typing it. The avatar and voice pickers can also make new ones in the chat (create_avatar,
design_voice_previews / save_designed_voice, clone_voice in creation.py), calling those tools with `tools/call`.
"""

from __future__ import annotations

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

# uri -> (file, name, description)
WIDGETS: dict[str, tuple[str, str, str]] = {
    AVATARS_URI: ("avatars.html", "Avatar picker", "Avatar thumbnails to pick the presenter of an Avatar Video."),
    VOICES_URI: ("voices.html", "Voice picker", "Voice previews to listen to and pick a narration voice."),
    CHARACTERS_URI: ("characters.html", "Character picker", "Vector Explainer characters to pick from."),
    PLAYER_URI: ("player.html", "Video player", "Plays a finished Cliptude MP4, or shows that it is not rendered yet."),
    CHOICES_URI: ("choices.html", "Setting picker", "Previews of music, backgrounds and styles to pick a template setting."),
    PUBLISH_KIT_URI: ("publish-kit.html", "Publish kit", "Thumbnails, title ideas, description, chapters and tags."),
}

# Existing tools that get a widget, by name.
BOUND_TOOLS: dict[str, str] = {
    "list_avatars": AVATARS_URI,
    "list_voices": VOICES_URI,
    "list_vector_characters": CHARACTERS_URI,
    "get_render": PLAYER_URI,
    "get_product_video_status": PLAYER_URI,
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
    return html.replace("/*@BASE_CSS@*/", css).replace("/*@BRIDGE_JS@*/", bridge)


def media_domains(*, local: bool) -> list[str]:
    return list(MEDIA_DOMAINS) + (list(LOCAL_MEDIA_DOMAINS) if local else [])


def _ui_meta(uri: str) -> dict[str, Any]:
    # `ui.resourceUri` is the spec's key; the flat `ui/resourceUri` is its deprecated form, kept for hosts that
    # shipped before the rename.
    return {"ui": {"resourceUri": uri}, "ui/resourceUri": uri}


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

    apps = Apps()
    csp = ResourceCsp(resource_domains=media_domains(local=local))
    for uri, (file_name, name, description) in WIDGETS.items():
        apps.add_html_resource(
            uri,
            widget_html(file_name),
            name=name,
            title=name,
            description=description,
            csp=csp,
            permissions=_PERMISSIONS.get(uri),
            prefers_border=True,
        )

    @apps.tool(
        resource_uri=CHOICES_URI,
        meta={"ui/resourceUri": CHOICES_URI},
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
        meta={"ui/resourceUri": PLAYER_URI},
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
        meta={"ui/resourceUri": PUBLISH_KIT_URI},
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


def bind_existing_tools(mcp: Any) -> list[str]:
    """Stamp `_meta.ui.resourceUri` on the existing tools that have a widget; returns the names bound.

    The tools are registered by server.py as before, so their names, parameters and results do not change.
    """
    manager = getattr(mcp, "_tool_manager", None)
    bound = []
    for name, uri in BOUND_TOOLS.items():
        tool = manager.get_tool(name) if manager is not None else None
        if tool is None:
            continue
        tool.meta = {**(tool.meta or {}), **_ui_meta(uri)}
        bound.append(name)
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
