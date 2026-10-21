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
    return CliptudeClient(api_key=token, client_kind="mcp")


def _thumbnail_arg(prompt: str | None, reference_url: str | None) -> dict[str, str] | None:
    """create_job's thumbnail object from the flat tool arguments (None keeps the template's)."""
    thumbnail = {
        key: value.strip()
        for key, value in (("custom_prompt", prompt), ("reference_url", reference_url))
        if value and value.strip()
    }
    return thumbnail or None


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
        "Call list_templates, then create_job with template_id + title plus "
        "(no suitable template? get_template_options + list_voices, then create_template) "
        "To revise a finished video: open_editor_project, run_agent with timeline item ids, "
        "poll get_agent_run, then render_project and get_render. "
        "prompt, script, voiceover_path (uploaded audio, transcribed; skips TTS), "
        "or talking_head_path (presenter video on a Talking Head template). "
        "create_job returns immediately; poll get_job until completed or failed. "
        "To quote a price or check the balance first, call estimate_credits with the same "
        "template and prompt/script (an upload as voiceover_seconds); it creates nothing. "
        "For a script only (no MP4), use create_script. For narration audio only, "
        "use create_voiceover. To turn a completed script or voiceover job into an MP4 "
        "on the same job id, use continue_job (do not create a second job). "
        "Never set prompt to the video title. If the user only named a topic or title, "
        "web-search current facts, write spoken narration, and pass it as script. "
        "If the user pastes a full narration, pass it as script, not prompt. "
        "If they provide a voiceover file, pass voiceover_path and omit prompt/script. "
        "If they provide a talking-head recording, pass talking_head_path and omit "
        "prompt/script/voiceover_path (template format must be talking_head). "
        "Avatar Video: list_avatars gives avatar ids; put one in create_template settings as "
        "avatar_profile_id (with the voice), or pass avatar_profile_id on create_job to override. "
        "Avatar Video templates may include avatar_intro_only "
        "(Avatar Intro Only) and avatar_ai_footage (AI Image + AI Video); pass those "
        "on create_job to override the template, or omit them to keep the template. "
        "AGENT-PLANNED VIDEOS (Faceless Documentary, Top 10, Avatar Video): you plan every visual "
        "and Cliptude only voices, sources and renders. create_plan_job, poll get_job until "
        "plan_ready, get_narration (numbered sentences), get_kind_catalog (compact first), write the "
        "plan, check_plan until valid, lock_plan, continue_job, then get_plan_report. See the "
        "cliptude-plan skill."
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
        """List saved video templates (id, name, format). Avatar Video rows include avatar_intro_only and avatar_ai_footage. Empty list means the user has not saved a template in Cliptude yet."""
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
        """Get one saved template summary (not internal JSON). Avatar Video includes avatar_intro_only and avatar_ai_footage."""
        try:
            return _client().get_template(template_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get template options",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_template_options(
        format: Annotated[str, Field(description="Video format, e.g. faceless_documentary or vector_explainer")],
    ) -> dict[str, Any]:
        """Fields, choices and defaults that create_template accepts for one format (styles, colours, backgrounds, music, models, durations)."""
        try:
            return _client().template_options(format)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="List voices",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    def list_voices(
        provider: Annotated[str, Field(description="elevenlabs, minimax, cartesia, inworld or voicetory")] = "elevenlabs",
        language: Annotated[str | None, Field(description="Narration language, e.g. English")] = None,
    ) -> dict[str, Any]:
        """Voice catalog for one TTS provider. Use a voice's voice_id (and name, provider) in create_template."""
        try:
            return _client().list_voices(provider=provider, language=language)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create template")
    def create_template(
        name: Annotated[str, Field(min_length=1, max_length=120, description="Template name")],
        format: Annotated[str, Field(description="Video format, e.g. faceless_documentary")],
        settings: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Wizard settings: voice_id, voice_name, voice_provider plus any field from "
                    "get_template_options (unset fields take the wizard defaults). "
                    "Avatar Video: avatar_profile_id from list_avatars (gender must match the voice). "
                    "Vector Explainer: vector_character_id from get_template_options (vector_character.choices: "
                    "a built-in id such as nova, or one of the user's uploaded figures); omit it for no character. "
                    "Optional thumbnail: {reference_url} (a YouTube video link or https image whose style every "
                    "thumbnail copies) or {custom_prompt} (a standing brief: people, style, branding; each video "
                    "still sets its own subject and text). Omit it and each job picks a reference for its title."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Create a ready-to-use template without the create wizard. Values are validated like the wizard; returns the template (use its id in create_job)."""
        try:
            return _client().create_template(name=name, format=format, settings=settings)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Estimate credits",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def estimate_credits(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        prompt: Annotated[str | None, Field(description="Topic/brief, as you would send to create_job.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration, as you would send to create_job. Its length sets the estimate.")] = None,
        duration: Annotated[
            str | None,
            Field(description="Duration bucket such as 6-8. Omit to use the template (or estimate from script)."),
        ] = None,
        voiceover_seconds: Annotated[
            float | None,
            Field(gt=0, description="Length of the voiceover you would upload. Uploaded voiceovers bill at the lowest voice rate. Do not also send prompt or script."),
        ] = None,
        talking_head_seconds: Annotated[
            float | None,
            Field(gt=0, description="Length of the talking-head video you would upload (Talking Head templates only)."),
        ] = None,
        output: Annotated[
            str | None,
            Field(description='"video" (default), "plan", "script" or "voiceover" — the output you would request.'),
        ] = None,
        avatar_intro_only: Annotated[bool | None, Field(description="Avatar Video only, as in create_job.")] = None,
        avatar_ai_footage: Annotated[bool | None, Field(description="Avatar Video only, as in create_job.")] = None,
        avatar_profile_id: Annotated[
            int | None,
            Field(description="Avatar Video only: avatar id from list_avatars. Omit to use the template's avatar."),
        ] = None,
        avatar_engine: Annotated[
            str | None,
            Field(description='Avatar Video only: "iv" or "v" (list_avatars shows which each avatar supports; Avatar III is not offered). Omit to keep the template.'),
        ] = None,
        render_only: Annotated[
            bool | None,
            Field(description="Render-only plan job: you bring the voiceover (voiceover_path, or voiceover_url on a remote host), every picture and clip (url, or keep from upload_plan_media) and the graphics; Cliptude searches, writes, voices and generates nothing and charges only the render-only rate per minute."),
        ] = None,
        vector_reveal_mode: Annotated[
            str | None,
            Field(description='Vector Explainer only: "none" (stills with camera moves) or "video" (AI clips), to price a job that overrides the template.'),
        ] = None,
    ) -> dict[str, Any]:
        """Credits a job would need before you create it, with the same pricing as create_job. Creates and charges nothing.

        Returns credits_required (what is held to start; the final charge after render is usually lower),
        credits_estimate (before the safety buffer), credits_per_minute, estimated_seconds, voice_source for
        voice-priced formats, narration_credits for plan jobs, credits_available and enough_credits.
        Use it to quote the user a price or check the balance before create_job."""
        try:
            return _client().estimate_credits(
                template_id=template_id,
                prompt=prompt,
                script=script,
                duration=duration,
                voiceover_seconds=voiceover_seconds,
                talking_head_seconds=talking_head_seconds,
                output=output,
                avatar_intro_only=avatar_intro_only,
                avatar_ai_footage=avatar_ai_footage,
                avatar_profile_id=avatar_profile_id,
                avatar_engine=avatar_engine,
                render_only=render_only,
                vector_reveal_mode=vector_reveal_mode,
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="List avatars",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_avatars(
        section: Annotated[
            str | None,
            Field(description='"mine" (photo avatars the user created), "builtin", "public" or "favorites". Omit for all.'),
        ] = None,
        search: Annotated[str | None, Field(description="Part of the avatar's name.")] = None,
        gender: Annotated[str | None, Field(description='"male" or "female" to match the voice. Omit for all.')] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Avatars the user can put in an Avatar Video: id, name, section, gender, engines, thumbnail_url. Pass an id as avatar_profile_id to create_template (in settings), create_job, create_plan_job or estimate_credits. The avatar's gender must match the voice."""
        try:
            return _client().list_avatars(section=section, search=search, gender=gender, limit=limit, offset=offset)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="List vector characters",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_vector_characters() -> dict[str, Any]:
        """Characters for Vector Explainer videos: the built-in cast (id, name, descriptor, description, preview_url) and the user's own uploads (id, name, preview_url). Pass an id as vector_character_id in create_template settings, or on create_job / create_plan_job for one video. Also returns upload_guidance for making a new one."""
        try:
            return _client().list_vector_characters()
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Upload vector character")
    def upload_vector_character(
        name: Annotated[str, Field(min_length=1, max_length=120, description="A short name for the character, e.g. 'Dr. Maya'")],
        image_path: Annotated[str | None, Field(description="Local PNG, JPG or WEBP file. Stdio / local MCP only.")] = None,
        image_url: Annotated[str | None, Field(description="Public https link to the image (use on remote hosts).")] = None,
    ) -> dict[str, Any]:
        """Add the user's own Vector Explainer character and return its id (use it as vector_character_id). Use a picture the user supplied, or one you made for them: in that case show it to the user and upload only after they approve it. Best results: one full-body, front-facing character in flat vector style on a plain or transparent background, at least 1024 px, no text. It appears in the Cliptude wizard too."""
        try:
            path = (image_path or "").strip() or None
            url = (image_url or "").strip() or None
            if bool(path) == bool(url):
                return {"error": "Give image_path or image_url (one of them)."}
            return _client().upload_vector_character(image_path=path, image_url=url, name=name)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Delete vector character")
    def delete_vector_character(
        character_id: Annotated[str, Field(min_length=8, description="Uploaded character id from list_vector_characters")],
    ) -> dict[str, Any]:
        """Delete one of the user's uploaded characters (built-in ones cannot be deleted). Refused while a saved template uses it. Only when the user asks."""
        try:
            return _client().delete_vector_character(character_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create video job")
    def create_job(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief for Cliptude to write the VO. Never the video title. Prefer script after research.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration. Use this when the user gave a topic (after web search) or pasted a script. Cliptude does not rewrite it.")] = None,
        voiceover_path: Annotated[
            str | None,
            Field(
                description=(
                    "Local path to a narration audio file (MP3, M4A, WAV, …). "
                    "Cliptude transcribes it and skips TTS. Do not also send prompt or script. "
                    "Stdio / local MCP only — remote hosts should use the CLI."
                ),
            ),
        ] = None,
        talking_head_path: Annotated[
            str | None,
            Field(
                description=(
                    "Local path to a talking-head presenter video (MP4, MOV, WEBM, AVI). "
                    "Requires a Talking Head template. Cliptude transcribes it and skips TTS. "
                    "Do not also send prompt, script, or voiceover_path. "
                    "Stdio / local MCP only — remote hosts should use the CLI."
                ),
            ),
        ] = None,
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template (or estimate from script). Ignored when voiceover_path or talking_head_path is set — duration comes from the file."),
        ] = None,
        avatar_intro_only: Annotated[
            bool | None,
            Field(
                description=(
                    "Avatar Video only: Avatar Intro Only. True = HeyGen in a short opening. "
                    "Omit to use the saved template. Other formats reject this field."
                ),
            ),
        ] = None,
        avatar_ai_footage: Annotated[
            bool | None,
            Field(
                description=(
                    "Avatar Video only: AI Image + AI Video instead of stock and web footage. "
                    "Omit to use the saved template. Other formats reject this field."
                ),
            ),
        ] = None,
        avatar_profile_id: Annotated[
            int | None,
            Field(description="Avatar Video only: avatar id from list_avatars. Omit to use the template's avatar."),
        ] = None,
        avatar_engine: Annotated[
            str | None,
            Field(description='Avatar Video only: "iv" or "v" (list_avatars shows which each avatar supports; Avatar III is not offered). Omit to keep the template.'),
        ] = None,
        disable_animations: Annotated[
            bool | None,
            Field(description="True = no animated text reveals or motion graphics. Omit to use the saved template."),
        ] = None,
        disable_overlays: Annotated[
            bool | None,
            Field(description="True = no text overlays, stat cards, or graphic elements over footage. Captions are unaffected. Omit to use the saved template."),
        ] = None,
        disable_effects: Annotated[
            bool | None,
            Field(description="True = no color grading, film grain, light leaks, or stylized treatments. Ken Burns pan/zoom is unaffected. Omit to use the saved template."),
        ] = None,
        disable_transitions: Annotated[
            bool | None,
            Field(description="True = no scene transitions; hard cuts only. Omit to use the saved template."),
        ] = None,
        excluded_animation_types: Annotated[
            list[str] | None,
            Field(description="Animation-type strings to exclude from this video (see list_templates/get_template or the app's motion-graphics catalog for valid values). Omit to use the saved template."),
        ] = None,
        excluded_transition_kinds: Annotated[
            list[str] | None,
            Field(description="Transition slugs to exclude from auto-selection, e.g. 'flip-horizontal', 'film-burn'. Omit to use the saved template."),
        ] = None,
        voiceover_url: Annotated[
            str | None,
            Field(description="Public https link to the narration audio (MP3, M4A, WAV, OGG, FLAC, WEBM; up to 200 MB). Cliptude downloads it and uses it like an uploaded voiceover. Use this on remote hosts, which cannot send a local file. Omit prompt/script and voiceover_path."),
        ] = None,
        thumbnail_prompt: Annotated[
            str | None,
            Field(description="This video's thumbnail brief (subject, people, text, style), up to 2000 characters. Replaces the template's thumbnail for this job. Omit to use the template; omit thumbnail_reference_url too."),
        ] = None,
        thumbnail_reference_url: Annotated[
            str | None,
            Field(description="A YouTube video link or https image whose thumbnail style to copy for this job (the content still comes from this video). Omit to use the template's; without one, Cliptude picks a reference for the title. Not with thumbnail_prompt."),
        ] = None,
        vector_character_id: Annotated[
            str | None,
            Field(description='Vector Explainer only: a character id from list_vector_characters (built-in or uploaded), or "none", for this video. Omit to use the template\'s.'),
        ] = None,
        vector_reveal_mode: Annotated[
            str | None,
            Field(description='Vector Explainer only: "none" = drawn stills with camera moves (Ken Burns), "video" = every picture beat animated as an AI clip (costs more; check estimate_credits). Omit to use the template\'s.'),
        ] = None,
        pronunciations: Annotated[
            list[dict[str, str]] | None,
            Field(description='How the voice says hard words (ElevenLabs voices): [{"word": "Gmay", "alias": "guh-MAY"}] or [{"word": "Ordnung", "phoneme": "ˈɔɹt.nʊŋ", "alphabet": "ipa"}]. Script, captions and searches keep the real spelling. Replaces the template\'s list for this video; omit to keep it.'),
        ] = None,
    ) -> dict[str, Any]:
        """Start generation and return a job id immediately. Do not wait for the MP4. Poll get_job. Provide prompt, script, voiceover_path, or talking_head_path. Never copy the title into prompt; for a topic-only request write a spoken script after web search and pass script. Uploaded audio or talking-head video is transcribed. Avatar Video: optional avatar_intro_only and avatar_ai_footage override the template. Any format: optional disable_animations/disable_overlays/disable_effects/disable_transitions and excluded_animation_types/excluded_transition_kinds override the template's motion & effects settings."""
        try:
            vo = (voiceover_path or "").strip() or None
            vo_url = (voiceover_url or "").strip() or None
            th = (talking_head_path or "").strip() or None
            if vo and vo_url:
                return {"error": "Give voiceover_path or voiceover_url, not both."}
            if (vo or vo_url) and th:
                return {"error": "Upload either a talking head video or a voiceover — not both."}
            if (vo or vo_url or th) and ((prompt or "").strip() or (script or "").strip()):
                kind = "talking head video" if th else "voiceover"
                return {
                    "error": (
                        f"Upload a {kind}, or send a prompt / custom script — not both. "
                        "An uploaded file is transcribed and used as the narration."
                    )
                }
            return _client().create_job(
                template_id=template_id,
                title=title,
                prompt=prompt,
                script=script,
                duration=duration,
                voiceover_path=vo,
                voiceover_url=vo_url,
                talking_head_path=th,
                avatar_intro_only=avatar_intro_only,
                avatar_ai_footage=avatar_ai_footage,
                avatar_profile_id=avatar_profile_id,
                avatar_engine=avatar_engine,
                disable_animations=disable_animations,
                disable_overlays=disable_overlays,
                disable_effects=disable_effects,
                disable_transitions=disable_transitions,
                excluded_animation_types=excluded_animation_types,
                excluded_transition_kinds=excluded_transition_kinds,
                thumbnail=_thumbnail_arg(thumbnail_prompt, thumbnail_reference_url),
                vector_character_id=vector_character_id,
                vector_reveal_mode=vector_reveal_mode,
                pronunciations=pronunciations,
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create script job")
    def create_script(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str, Field(min_length=1, description="Topic or brief. Cliptude researches and writes spoken narration. Never the video title.")],
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template."),
        ] = None,
    ) -> dict[str, Any]:
        """Start a script-only job (no MP4). Returns a job id immediately. Poll get_job until completed; read script. Do not use create_job."""
        try:
            return _client().create_job(
                template_id=template_id,
                title=title,
                prompt=prompt,
                duration=duration,
                output="script",
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create voiceover job")
    def create_voiceover(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief. Cliptude writes the narration then voices it. Never the video title.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration to voice as-is. Omit prompt when you pass this.")] = None,
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template (or estimate from script)."),
        ] = None,
    ) -> dict[str, Any]:
        """Start a TTS-only job (no MP4). Returns a job id immediately. Poll get_job until completed; read voiceover.url. Provide prompt or script."""
        try:
            if not (prompt or "").strip() and not (script or "").strip():
                return {"error": "Provide prompt or script."}
            return _client().create_job(
                template_id=template_id,
                title=title,
                prompt=prompt,
                script=script,
                duration=duration,
                output="voiceover",
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Continue job into video")
    def continue_job(
        job_id: Annotated[str, Field(min_length=8, description="Completed script or voiceover job UUID, or a plan job with a locked plan")],
    ) -> dict[str, Any]:
        """Queue a completed script or voiceover job, or a plan job whose plan is locked, as a full video on the same id. Poll get_job until completed. Do not create a second job with the script or audio."""
        try:
            return _client().continue_job(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Create agent-planned job")
    def create_plan_job(
        template_id: Annotated[str, Field(min_length=8, description="Saved Faceless Documentary, Faceless Top 10, Avatar Video or Vector Explainer template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief; Cliptude writes the narration. Never the video title.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration to voice as-is. Omit prompt when you pass this.")] = None,
        voiceover_path: Annotated[str | None, Field(description="Local audio file to use as the narration (aligned, not re-voiced). Omit prompt/script. Stdio / local MCP only.")] = None,
        voiceover_url: Annotated[
            str | None,
            Field(description="Public https link to the narration audio (MP3, M4A, WAV, OGG, FLAC, WEBM; up to 200 MB). Cliptude downloads it and uses it like an uploaded voiceover. Use this on remote hosts, which cannot send a local file. Omit prompt/script and voiceover_path."),
        ] = None,
        duration: Annotated[str | None, Field(description="Duration bucket such as 6-8. Omit to use the template.")] = None,
        avatar_profile_id: Annotated[
            int | None,
            Field(description="Avatar Video only: avatar id from list_avatars. Omit to use the template's avatar."),
        ] = None,
        avatar_engine: Annotated[
            str | None,
            Field(description='Avatar Video only: "iv" or "v" (list_avatars shows which each avatar supports; Avatar III is not offered). Omit to keep the template.'),
        ] = None,
        render_only: Annotated[
            bool | None,
            Field(description="Render-only plan job: you bring the voiceover (voiceover_path, or voiceover_url on a remote host), every picture and clip (url, or keep from upload_plan_media) and the graphics; Cliptude searches, writes, voices and generates nothing and charges only the render-only rate per minute."),
        ] = None,
        thumbnail_prompt: Annotated[
            str | None,
            Field(description="This video's thumbnail brief (subject, people, text, style), up to 2000 characters. Replaces the template's thumbnail for this job. Omit to use the template; omit thumbnail_reference_url too."),
        ] = None,
        thumbnail_reference_url: Annotated[
            str | None,
            Field(description="A YouTube video link or https image whose thumbnail style to copy for this job (the content still comes from this video). Omit to use the template's; without one, Cliptude picks a reference for the title. Not with thumbnail_prompt."),
        ] = None,
        vector_character_id: Annotated[
            str | None,
            Field(description='Vector Explainer only: a character id from list_vector_characters (built-in or uploaded), or "none", for this video. Omit to use the template\'s.'),
        ] = None,
        vector_reveal_mode: Annotated[
            str | None,
            Field(description='Vector Explainer only: "none" = drawn stills with camera moves (Ken Burns), "video" = every picture beat animated as an AI clip (costs more; check estimate_credits). Omit to use the template\'s.'),
        ] = None,
        pronunciations: Annotated[
            list[dict[str, str]] | None,
            Field(description='How the voice says hard words (ElevenLabs voices): [{"word": "Gmay", "alias": "guh-MAY"}] or [{"word": "Ordnung", "phoneme": "ˈɔɹt.nʊŋ", "alphabet": "ipa"}]. Script, captions and searches keep the real spelling. Replaces the template\'s list for this video; omit to keep it.'),
        ] = None,
    ) -> dict[str, Any]:
        """Start a job whose visuals YOU plan. Cliptude writes (if needed) and voices the narration, then stops at status plan_ready. It needs the full website price in credits but charges only the narration now; the rest is charged when the video completes, and the total equals the website price. Poll get_job until plan_ready, then get_narration."""
        try:
            if not (prompt or "").strip() and not (script or "").strip() and not voiceover_path and not voiceover_url:
                return {"error": "Provide prompt, script, voiceover_path or voiceover_url."}
            return _client().create_job(
                template_id=template_id,
                title=title,
                prompt=prompt,
                script=script,
                voiceover_path=voiceover_path,
                voiceover_url=(voiceover_url or "").strip() or None,
                duration=duration,
                output="plan",
                avatar_profile_id=avatar_profile_id,
                avatar_engine=avatar_engine,
                render_only=render_only,
                thumbnail=_thumbnail_arg(thumbnail_prompt, thumbnail_reference_url),
                vector_character_id=vector_character_id,
                vector_reveal_mode=vector_reveal_mode,
                pronunciations=pronunciations,
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get narration",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_narration(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status plan_ready)")],
    ) -> dict[str, Any]:
        """The narration you plan against: numbered sentences with start/end seconds and every word's timing, narration_digest (copy it into the plan), suggested chapters, the template's theme and switched-off graphics, pricing (website price, already charged, due at continue), and narration_audio: a url to listen to (check names and borrowed words before you lock) and the API path that downloads the MP3. The sentence text is what the voice said, so a misspoken word shows up there too."""
        try:
            return _client().get_narration(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get graphics catalog",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_kind_catalog(
        format: Annotated[str | None, Field(description="faceless_documentary, faceless_top_10, avatar_video or vector_explainer")] = None,
        theme: Annotated[str | None, Field(description="Visual theme, to mark graphics the theme does not use")] = None,
        compact: Annotated[bool, Field(description="One summary line per graphic (start here; the full catalog is large)")] = True,
        types: Annotated[list[str] | None, Field(description="Full entries (description, prop_schema, media_slots, durations, preview) for just these animation types / overlay styles")] = None,
        premium: Annotated[bool, Field(description="Only the premium boards (marked premium: true)")] = False,
    ) -> dict[str, Any]:
        """Every motion graphic Cliptude can render: planner kinds, ~360 animation types and 20 overlay text styles. Read compact first, then fetch full entries for the types you intend to use: their props, media slots, hold times, caps and preview video. Entries marked `premium: true` are the premium boards (newest designs, each with its own timed sound cues); premium=true lists only those. Also lists `transitions` (slugs for beats[].transition_in), `reveal_styles` (beats[].reveal_next, with preview videos) and `sounds` (ids for graphic sound, transition_sound, reveal_sound and sfx, with a preview link to each file)."""
        try:
            return _client().get_kind_catalog(format=format, theme=theme, compact=compact and not types, types=types, premium=premium)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Check plan",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def check_plan(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status plan_ready)")],
        plan: Annotated[dict[str, Any], Field(description="The cliptude.plan/v1 plan object (see the cliptude-plan skill)")],
    ) -> dict[str, Any]:
        """Validate a plan without storing it. Free, no LLM, up to 60 a minute. Returns valid, errors (each with path, code, message, hint), warnings, a scorecard against the reference targets, and the cost due at continue. Fix every error and check again; warnings are advice."""
        try:
            return _client().put_plan(job_id, plan, dry_run=True)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Lock plan")
    def lock_plan(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status plan_ready)")],
        plan: Annotated[dict[str, Any], Field(description="The plan that check_plan reported valid")],
    ) -> dict[str, Any]:
        """Validate and store the plan, and apply its look (theme, colours, style, music) to the video. You may lock again to replace it until you call continue_job. Then call continue_job."""
        try:
            return _client().put_plan(job_id, plan, dry_run=False)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get locked plan",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_plan(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
    ) -> dict[str, Any]:
        """The locked plan, its hash and when it was locked."""
        try:
            return _client().get_plan(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Review the built video",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_plan_review(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status ready_for_edit)")],
    ) -> dict[str, Any]:
        """What the built design shows, before any render: every picture/clip in order (beat, time, the sentence under it, what you asked for, what was used, a link to the file) and every graphic (time on screen and the props it will be drawn with, or why it is missing). Review this, revise with check_plan/lock_plan + continue_job on the same job if needed, then render_project once."""
        try:
            return _client().get_plan_review(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Upload plan media",
        annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False),
    )
    def upload_plan_media(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
        path: Annotated[
            str,
            Field(description="Local picture (JPG/PNG/WEBP/GIF, up to 15 MB) or clip (MP4/MOV/WEBM/M4V, up to 300 MB). Stdio / local MCP only — remote hosts should use the CLI (cliptude upload-media) or give a public file link as media.url."),
        ],
    ) -> dict[str, Any]:
        """Send a picture or clip you found yourself to a plan job. The file goes straight to Cliptude's storage on signed URLs (no storage keys needed) and is checked to be a real picture or video. Returns {keep, file, media_type, size}: put {"keep": "<id>"} as a beat's media or a graphic's picture slot, with start_s/end_s on a beat to play only part of a clip."""
        try:
            return _client().upload_plan_media(job_id, path)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get plan sourcing report",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_plan_report(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (after continue_job)")],
    ) -> dict[str, Any]:
        """For each media request in your plan (beat footage, graphic pictures): what was used (source, query, file) or the fallback and why, plus graphics planned vs rendered. Available once the design is built."""
        try:
            return _client().get_plan_report(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get job",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_job(
        job_id: Annotated[str, Field(min_length=8, description="Job / video UUID")],
    ) -> dict[str, Any]:
        """Job status, progress, error, output url (SaaS) or path (Desktop), and script / voiceover when this was a partial job. can_continue is true when continue_job can turn it into a video."""
        try:
            return _client().get_job(job_id)
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
        title="Open editor project",
        annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False),
    )
    def open_editor_project(
        job_id: Annotated[str, Field(min_length=8, description="Completed video job id")],
    ) -> dict[str, Any]:
        """Open (or create) the timeline editor project for a completed video job. Returns project id, revision and a compact timeline (item id, type, start_s, end_s, text) to reference in run_agent."""
        try:
            return _client().open_editor_project(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get editor project",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_editor_project(
        project_id: Annotated[int, Field(description="Editor project id")],
    ) -> dict[str, Any]:
        """Current revision, compact timeline and latest render of an editor project."""
        try:
            return _client().get_editor_project(project_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Run Cliptude Agent")
    def run_agent(
        project_id: Annotated[int, Field(description="Editor project id")],
        prompt: Annotated[str, Field(min_length=1, description="Edit request in plain English")],
        referenced_item_ids: Annotated[
            list[str] | None, Field(description="Timeline item ids the request is about (from the timeline listing)")
        ] = None,
        tier: Annotated[str, Field(description="fast or smart")] = "fast",
        chat_id: Annotated[str | None, Field(description="Continue an existing agent chat")] = None,
    ) -> dict[str, Any]:
        """Ask Cliptude Agent to edit the timeline (swap footage, regenerate images or voiceover ranges, music, transitions, text). Returns a run; poll get_agent_run. Charges agent credits. Then render_project for a new MP4."""
        try:
            return _client().run_agent(
                project_id, prompt=prompt, tier=tier, referenced_item_ids=referenced_item_ids, chat_id=chat_id
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get agent run",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_agent_run(
        project_id: Annotated[int, Field(description="Editor project id")],
        run_id: Annotated[str, Field(min_length=8, description="Agent run id")],
    ) -> dict[str, Any]:
        """Status of a Cliptude Agent run plus the agent's messages."""
        try:
            return _client().get_agent_run(project_id, run_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Cancel agent run")
    def cancel_agent_run(
        project_id: Annotated[int, Field(description="Editor project id")],
        run_id: Annotated[str, Field(min_length=8, description="Agent run id")],
    ) -> dict[str, Any]:
        """Cancel a queued or running Cliptude Agent run."""
        try:
            return _client().cancel_agent_run(project_id, run_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(title="Render editor project")
    def render_project(
        project_id: Annotated[int, Field(description="Editor project id")],
    ) -> dict[str, Any]:
        """Render the edited timeline to a new MP4 (charged like an editor export). Poll get_render for output_url."""
        try:
            return _client().render_project(project_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get render",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def get_render(
        render_id: Annotated[int, Field(description="Editor render id")],
    ) -> dict[str, Any]:
        """Status of an editor render; output_url when completed."""
        try:
            return _client().get_render(render_id)
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
            "with title plus prompt (topic), script (full narration), "
            "voiceover_path (local audio file), or talking_head_path "
            "(presenter video on a Talking Head template). Poll get_job. "
            "Avatar Video templates expose avatar_intro_only and avatar_ai_footage; "
            "pass them on create_job to override the template."
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
