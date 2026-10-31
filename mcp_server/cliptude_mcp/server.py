"""stdio / Streamable HTTP MCP server. Never writes to stdout except JSON-RPC."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import Image
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


# Fields that make an MCP result too big to read (a 90 minute-class timeline index, per-word timings,
# whole-voiceover segment arrays) without helping an agent decide anything.
_BULKY_OPERATION_KEYS = frozenset({
    "voice_segments", "chunk_alignments", "chunk_durations", "voiceover_chunks", "timing_metadata",
})


def _compact_operation(operation: Any) -> Any:
    if not isinstance(operation, dict):
        return operation
    out: dict[str, Any] = {}
    for key, value in operation.items():
        if key in _BULKY_OPERATION_KEYS and isinstance(value, (list, dict)):
            out[key] = f"<{len(value)} entries omitted>"
        else:
            out[key] = value
    return out


def _compact_agent_run(data: Any) -> Any:
    """Agent run with the voice-segment arrays of its operations summarised (they repeat in messages)."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    run = out.get("run")
    if isinstance(run, dict) and isinstance(run.get("applied_operations"), list):
        out["run"] = {**run, "applied_operations": [_compact_operation(op) for op in run["applied_operations"]]}
    messages = out.get("messages")
    if isinstance(messages, list):
        compact_messages = []
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("metadata"), dict):
                meta = dict(message["metadata"])
                if isinstance(meta.get("operations"), list):
                    meta["operations"] = [_compact_operation(op) for op in meta["operations"]]
                message = {**message, "metadata": meta}
            compact_messages.append(message)
        out["messages"] = compact_messages
    return out


def _compact_editor_project(data: Any) -> Any:
    """Editor project without voice_timing.spokenEventIndex (every word of the narration, 100 KB+)."""
    if not isinstance(data, dict):
        return data
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("voice_timing"), dict):
        return data
    timing = project["voice_timing"]
    kept = {k: v for k, v in timing.items() if k != "spokenEventIndex"}
    index = timing.get("spokenEventIndex")
    if isinstance(index, dict) and isinstance(index.get("words"), list):
        kept["spoken_words"] = len(index["words"])
    return {**data, "project": {**project, "voice_timing": kept}}


def _compact_narration(data: Any, *, include_words: bool) -> Any:
    """Narration sentences without per-word timings unless asked (they are most of the payload)."""
    if include_words or not isinstance(data, dict) or not isinstance(data.get("sentences"), list):
        return data
    sentences = []
    for sentence in data["sentences"]:
        if isinstance(sentence, dict) and "words" in sentence:
            sentence = {k: v for k, v in sentence.items() if k != "words"}
        sentences.append(sentence)
    return {**data, "sentences": sentences, "words_omitted": True}


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
        "prompt, script, voiceover_path (uploaded audio of at least 6 minutes, transcribed; skips TTS), "
        "or talking_head_path (presenter video of at least 6 minutes on a Talking Head template). "
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
        "Before you create a template or job with an avatar, show the user its thumbnail_url and name and "
        "wait for them to confirm it is the presenter they want (names repeat; the avatar is paid for). "
        "Avatar Video templates may include avatar_intro_only "
        "(Avatar Intro Only) and avatar_ai_footage (AI Image + AI Video); pass those "
        "on create_job to override the template, or omit them to keep the template. "
        "REVIEW BEFORE EVERY JOB: create_job, create_plan_job, create_script and create_voiceover first return "
        "a review (status 428, detail.review) and create nothing. Show the user every setting in it in plain "
        "words (voice, model tier, media sources incl. general web crawling, animations/motion graphics, charts, "
        "overlays, transitions, sound effects, music, A-roll) plus every warning and the credits, and ask them to "
        "confirm. Then call the same tool again with the same arguments and review_token. For each item in "
        "detail.review.acknowledgements_required (no_general_web_content: no YouTube or general web content, which "
        "can lower video quality a lot; no_video_footage; no_motion_graphics; no_music; no_sound_effects; "
        "no_transitions) tell the user what it costs and pass "
        "its id in acknowledge only after they explicitly approve; otherwise fix the template. "
        "AGENT-PLANNED VIDEOS (Faceless Documentary, Top 10, Avatar Video): you plan every visual "
        "and Cliptude only voices, sources and renders. create_plan_job, poll get_job until "
        "plan_ready, get_narration (numbered sentences), get_kind_catalog (compact first), write the "
        "plan, check_plan until valid, lock_plan, continue_job, then get_plan_report. Before giving the user "
        "the editor link, open_editor_project and look at EVERY frame with view_scenes (0 to the end): replace "
        "any dead body or gore, other brands, logos, album art, footage of a game, match or show the video is not about, title cards, pictures whose place or era "
        "contradicts the story (a search naming a place can return a namesake elsewhere), near-black frames, clips floating in black bars, broken AI pictures and any picture held over ~8 s, in a free revision, then review again. Generic "
        "shots of places name the story's setting (\"empty bed mud house\", not \"empty bed dark room\"). A video "
        "about a video game, sport, creator or TV show searches the real thing (\"GTA 6 gameplay driving\", "
        "\"Bellingham Real Madrid goal\", \"Kai Cenat stream\"): its own footage is the footage, broadcaster and "
        "channel logos included, never real-world stand-ins for it. True "
        "stories (real events and people) set sense.true_story and sense.people and never search for people: a "
        "stranger on screen is taken for the real person (check_plan refuses it, stand_in_person; a dated topic "
        "naming people counts as true unless true_story is false, and its year is the default era). See the "
        "cliptude-plan skill. Avatar Video and Talking Head plans never show the presenter only in "
        "full screen: make about one in four presenter_windows split_left/split_right with collage_media and "
        "put a speaker board (talking_head_*, dual_metric_talking_head) in most full-screen windows; check_plan "
        "refuses all-full-screen windows (presenter_only_full_screen). Treat presenter_no_speaker_boards, "
        "presenter_no_splits, kind_overused, over_cap and few_board_pictures warnings as must-fix. "
        "On footage videos also fix query_too_long, query_style_words and same_subject_searches (write each "
        "query as 2-5 words naming the subject, as clips are titled, and put the shot in intent), and on "
        "video_needs_web_crawling ask the user to turn on General Web Crawling before building. "
        "PRODUCT VIDEOS (the user's connected store, or a product page from a store they sell for): "
        "fetch_product_context, show the user the 3 angles, draft_product_script with their pick and a "
        "duration (6-8 to 50-60 minutes), poll get_product_video_status until script_ready, let the user "
        "review the script, render_product_video (this starts the build, not the final MP4), poll until "
        "ready_for_review, open_editor_project and look at every picture and graphic yourself with "
        "view_scenes (contact sheets; media links may not open for you), check no picture or board shows another brand "
        "(shop shelves in the product part especially), give the user editor_url, and only after they approve render with "
        "render_project + get_render. "
        "Never publish anything to a store or YouTube without the user's explicit go-ahead."
        ),
    }
    if with_http_auth:
        from cliptude_mcp.auth import CliptudeTokenVerifier, auth_settings

        kwargs["token_verifier"] = CliptudeTokenVerifier()
        kwargs["auth"] = auth_settings()

    mcp = MCPServer("Cliptude", **kwargs)

    @mcp.tool(
        title="List templates",
        annotations=ToolAnnotations(title="List templates", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Get template", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Get template options", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="List voices", read_only_hint=True, open_world_hint=True),
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

    @mcp.tool(
        title="Create template",
        annotations=ToolAnnotations(title="Create template", read_only_hint=False, destructive_hint=False),
    )
    def create_template(
        name: Annotated[str, Field(min_length=1, max_length=120, description="Template name")],
        format: Annotated[str, Field(description="Video format, e.g. faceless_documentary")],
        settings: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Wizard settings: voice_id, voice_name, voice_provider plus any field from "
                    "get_template_options (unset fields take the wizard defaults). Set only what the user asked for: "
                    "each field's description says what turning it on or off does, and the source and graphics "
                    "switches (source_general_web_crawling, enable_broll_stock_footage, enable_animations, ...) "
                    "decide whether the video can have real footage and boards at all. "
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
        """Create a ready-to-use template without the create wizard. Values are validated like the wizard; returns the template (use its id in create_job) and `warnings`: choices that will change what the video can show (no footage, AI stills, no graphics). Tell the user every warning in plain words and fix the ones they did not ask for before making a video."""
        try:
            return _client().create_template(name=name, format=format, settings=settings)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Estimate credits",
        annotations=ToolAnnotations(title="Estimate credits", read_only_hint=True, open_world_hint=False),
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
            Field(description="Render-only plan job: you bring the voiceover (voiceover_path, or voiceover_url: on a remote host from start_voiceover_upload), every picture and clip (url, or keep from upload_plan_media / start_media_upload) and the graphics; Cliptude searches, writes, voices and generates nothing and charges only the render-only rate per minute."),
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
        annotations=ToolAnnotations(title="List avatars", read_only_hint=True, open_world_hint=False),
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
        """Avatars the user can put in an Avatar Video: id, name, section, gender, engines, thumbnail_url. Pass an id as avatar_profile_id to create_template (in settings), create_job, create_plan_job or estimate_credits. The avatar's gender must match the voice. Show the user the thumbnail_url and name and get their OK before you spend credits on it: several avatars share a name."""
        try:
            return _client().list_avatars(section=section, search=search, gender=gender, limit=limit, offset=offset)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="List vector characters",
        annotations=ToolAnnotations(title="List vector characters", read_only_hint=True, open_world_hint=False),
    )
    def list_vector_characters() -> dict[str, Any]:
        """Characters for Vector Explainer videos: the built-in cast (id, name, descriptor, description, preview_url) and the user's own uploads (id, name, preview_url). Pass an id as vector_character_id in create_template settings, or on create_job / create_plan_job for one video. Also returns upload_guidance for making a new one."""
        try:
            return _client().list_vector_characters()
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Upload vector character",
        annotations=ToolAnnotations(title="Upload vector character", read_only_hint=False, destructive_hint=False),
    )
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

    @mcp.tool(
        title="Delete vector character",
        annotations=ToolAnnotations(title="Delete vector character", read_only_hint=False, destructive_hint=True),
    )
    def delete_vector_character(
        character_id: Annotated[str, Field(min_length=8, description="Uploaded character id from list_vector_characters")],
    ) -> dict[str, Any]:
        """Delete one of the user's uploaded characters (built-in ones cannot be deleted). Refused while a saved template uses it. Only when the user asks."""
        try:
            return _client().delete_vector_character(character_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Create video job",
        annotations=ToolAnnotations(title="Create video job", read_only_hint=False, destructive_hint=False),
    )
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
                    "Local path to a talking-head presenter video (MP4, MOV, WEBM, AVI), at least 6 minutes long. "
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
            Field(description="Public https link to the narration audio (MP3, M4A, WAV, OGG, FLAC, WEBM; up to 200 MB; at least 6 minutes long). Cliptude downloads it and uses it like an uploaded voiceover. On a remote host, upload a local file with start_voiceover_upload + finish_voiceover_upload and pass the voiceover_url it returns. Omit prompt/script and voiceover_path."),
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
        review_token: Annotated[
            str | None,
            Field(description="From the review this tool returned when called without it (status 428, detail.review_token). Only send it after showing the user every setting and warning in detail.review and getting their confirmation."),
        ] = None,
        acknowledge: Annotated[
            list[str] | None,
            Field(description="Ids from detail.review.acknowledgements_required (e.g. no_general_web_content) that the user explicitly approved after you explained each one. Never fill these in yourself."),
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
                review_token=review_token,
                acknowledge=acknowledge,
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

    @mcp.tool(
        title="Create script job",
        annotations=ToolAnnotations(title="Create script job", read_only_hint=False, destructive_hint=False),
    )
    def create_script(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str, Field(min_length=1, description="Topic or brief. Cliptude researches and writes spoken narration. Never the video title.")],
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template."),
        ] = None,
        review_token: Annotated[
            str | None,
            Field(description="From the review this tool returned when called without it (status 428, detail.review_token). Only send it after showing the user every setting and warning in detail.review and getting their confirmation."),
        ] = None,
        acknowledge: Annotated[
            list[str] | None,
            Field(description="Ids from detail.review.acknowledgements_required (e.g. no_general_web_content) that the user explicitly approved after you explained each one. Never fill these in yourself."),
        ] = None,
    ) -> dict[str, Any]:
        """Start a script-only job (no MP4). Returns a job id immediately. Poll get_job until completed; read script. Do not use create_job."""
        try:
            return _client().create_job(
                review_token=review_token,
                acknowledge=acknowledge,
                template_id=template_id,
                title=title,
                prompt=prompt,
                duration=duration,
                output="script",
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Create voiceover job",
        annotations=ToolAnnotations(title="Create voiceover job", read_only_hint=False, destructive_hint=False),
    )
    def create_voiceover(
        template_id: Annotated[str, Field(min_length=8, description="Saved template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief. Cliptude writes the narration then voices it. Never the video title.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration to voice as-is. Omit prompt when you pass this.")] = None,
        duration: Annotated[
            str | None,
            Field(description="Wizard duration bucket such as 3-4 or 6-8. Omit to use the template (or estimate from script)."),
        ] = None,
        review_token: Annotated[
            str | None,
            Field(description="From the review this tool returned when called without it (status 428, detail.review_token). Only send it after showing the user every setting and warning in detail.review and getting their confirmation."),
        ] = None,
        acknowledge: Annotated[
            list[str] | None,
            Field(description="Ids from detail.review.acknowledgements_required (e.g. no_general_web_content) that the user explicitly approved after you explained each one. Never fill these in yourself."),
        ] = None,
    ) -> dict[str, Any]:
        """Start a TTS-only job (no MP4). Returns a job id immediately. Poll get_job until completed; read voiceover.url. Provide prompt or script."""
        try:
            if not (prompt or "").strip() and not (script or "").strip():
                return {"error": "Provide prompt or script."}
            return _client().create_job(
                review_token=review_token,
                acknowledge=acknowledge,
                template_id=template_id,
                title=title,
                prompt=prompt,
                script=script,
                duration=duration,
                output="voiceover",
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Continue job into video",
        annotations=ToolAnnotations(title="Continue job into video", read_only_hint=False, destructive_hint=False),
    )
    def continue_job(
        job_id: Annotated[str, Field(min_length=8, description="Completed script or voiceover job UUID, or a plan job with a locked plan")],
    ) -> dict[str, Any]:
        """Queue a completed script or voiceover job, or a plan job whose plan is locked, as a full video on the same id. Poll get_job until completed. Do not create a second job with the script or audio."""
        try:
            return _client().continue_job(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Fetch product context",
        annotations=ToolAnnotations(title="Fetch product context", read_only_hint=True, destructive_hint=False,
                                    open_world_hint=True),
    )
    def fetch_product_context(
        product_url_or_query: Annotated[str, Field(min_length=1, description=(
            "A product page URL from the user's own store (Shopify, e.g. https://store.com/products/handle; "
            "WooCommerce, e.g. https://store.com/product/name/, is read through its Store API; Etsy and others "
            "work through the page's structured data), a connected store's "
            "product id/handle, or a search term together with store_domain."))],
        store_domain: Annotated[str | None, Field(description=(
            "Store to search or read from, e.g. acme.com or acme.myshopify.com. Uses the user's connected "
            "store when it matches."))] = None,
    ) -> dict[str, Any]:
        """Read a store product: title, price, variants, description and its real image URLs, saved as product_id, plus 3 educational YouTube video angles (80% teaching, 20% product). When no angles could be suggested the list is empty with an angles_note: pass your own angle (a title, or {title, problem, hook}) to draft_product_script. A search that matches several products returns candidates; fetch again with the chosen URL. Show the user the angles before drafting."""
        try:
            return _client().fetch_product_context(product_url_or_query, store_domain=store_domain)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Draft product video script",
        annotations=ToolAnnotations(title="Draft product video script", read_only_hint=False, destructive_hint=False),
    )
    def draft_product_script(
        product_id: Annotated[str, Field(min_length=8, description="product_id from fetch_product_context")],
        angle: Annotated[int | str | dict | None, Field(description=(
            "Index (0-2) of an angle from fetch_product_context, an angle object {title, problem, hook}, or "
            "a custom educational title. Default 0."))] = None,
        duration: Annotated[str | int | None, Field(description=(
            "Length: 6-8, 10-12, 18-20, 30-40 or 50-60 (minutes), or a number of minutes from 6 to 60. "
            "Default 6-8."))] = None,
        template_id: Annotated[str | None, Field(description=(
            "Template to use (voice, look, music): a Faceless Documentary, Top 10 or Avatar Video template "
            "(an Avatar Video one puts the avatar on screen). Omit to use the latest Faceless Documentary one, "
            "or a default Product Explainer template."))] = None,
        title: Annotated[str | None, Field(description="Video title. Defaults to the angle title.")] = None,
        discount_code: Annotated[str | None, Field(description="Discount code the merchant gave. Never invent one.")] = None,
        cta_url: Annotated[str | None, Field(description="Where viewers should go. Defaults to the product page.")] = None,
        image_urls: Annotated[list[str] | None, Field(description=(
            "Which of the product's image URLs to show (defaults to all of them)."))] = None,
        reviews: Annotated[list[dict] | None, Field(description=(
            "Up to 3 real customer reviews the merchant gave you, word for word: "
            "[{quote, author, rating (1-5), verified}]. Enables the review card. Never write or "
            "paraphrase a review."))] = None,
    ) -> dict[str, Any]:
        """Queue the narration for a product video: an educational documentary for about 80% of its length, then a natural bridge to the product (real specs, price, offer) in the last 20%. Returns script_id (the job id) immediately; poll get_product_video_status until stage is script_ready, then show the user the script."""
        try:
            return _client().draft_product_script(
                product_id, angle=angle, duration=duration, template_id=template_id, title=title,
                discount_code=discount_code, cta_url=cta_url, image_urls=image_urls, reviews=reviews,
            )
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Render product video",
        annotations=ToolAnnotations(title="Render product video", read_only_hint=False, destructive_hint=False),
    )
    def render_product_video(
        script_id: Annotated[str, Field(min_length=8, description="script_id / job_id from draft_product_script, once script_ready")],
        aspect_ratio: Annotated[str, Field(description="16:9 (YouTube), 9:16, 1:1 or 4:5")] = "16:9",
    ) -> dict[str, Any]:
        """Start BUILDING the video from the approved script on the same job id: voiceover, sourcing, motion graphics, and the merchant's real product photos composited into the closing product boards. This does not render the final MP4: when the build is done (ready_for_review) review it, show the user the editor_url and wait for their go-ahead before render_project. Returns {job_id, status, stage, editor_url, estimated_minutes, credits_reserved, credits} immediately (credits: charged so far, held while it builds, and the full-generation estimate); quote them to the user. Never wait in this call. Poll get_product_video_status."""
        try:
            return _client().render_product_video(script_id, aspect_ratio=aspect_ratio)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get product video status",
        annotations=ToolAnnotations(title="Get product video status", read_only_hint=True, destructive_hint=False),
    )
    def get_product_video_status(
        job_id: Annotated[str, Field(min_length=8, description="Job id from draft_product_script / render_product_video")],
    ) -> dict[str, Any]:
        """Stage of a product video (script_drafting, script_ready, producing, ready_for_review, rendered, failed) with the script once written, the editor_url to preview, the MP4 URL once rendered, credits {charged, reserved, estimate, estimate_with_buffer} and next_step telling you what to do. The editor's export_credits_due is 0 until a first export exists (the first export of a video is free)."""
        try:
            return _client().get_product_video_status(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Create agent-planned job",
        annotations=ToolAnnotations(title="Create agent-planned job", read_only_hint=False, destructive_hint=False),
    )
    def create_plan_job(
        template_id: Annotated[str, Field(min_length=8, description="Saved Faceless Documentary, Faceless Top 10, Avatar Video or Vector Explainer template UUID")],
        title: Annotated[str, Field(min_length=1, max_length=255, description="Video title")],
        prompt: Annotated[str | None, Field(description="Topic/brief; Cliptude writes the narration. Never the video title.")] = None,
        script: Annotated[str | None, Field(description="Spoken narration to voice as-is. Omit prompt when you pass this.")] = None,
        voiceover_path: Annotated[str | None, Field(description="Local audio file to use as the narration (aligned, not re-voiced), at least 6 minutes long. Omit prompt/script. Stdio / local MCP only.")] = None,
        voiceover_url: Annotated[
            str | None,
            Field(description="Public https link to the narration audio (MP3, M4A, WAV, OGG, FLAC, WEBM; up to 200 MB; at least 6 minutes long). Cliptude downloads it and uses it like an uploaded voiceover. On a remote host, upload a local file with start_voiceover_upload + finish_voiceover_upload and pass the voiceover_url it returns. Omit prompt/script and voiceover_path."),
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
            Field(description="Render-only plan job: you bring the voiceover (voiceover_path, or voiceover_url: on a remote host from start_voiceover_upload), every picture and clip (url, or keep from upload_plan_media / start_media_upload) and the graphics; Cliptude searches, writes, voices and generates nothing and charges only the render-only rate per minute."),
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
        review_token: Annotated[
            str | None,
            Field(description="From the review this tool returned when called without it (status 428, detail.review_token). Only send it after showing the user every setting and warning in detail.review and getting their confirmation."),
        ] = None,
        acknowledge: Annotated[
            list[str] | None,
            Field(description="Ids from detail.review.acknowledgements_required (e.g. no_general_web_content) that the user explicitly approved after you explained each one. Never fill these in yourself."),
        ] = None,
    ) -> dict[str, Any]:
        """Start a job whose visuals YOU plan. Cliptude writes (if needed) and voices the narration, then stops at status plan_ready. It needs the full website price in credits but charges only the narration now; the rest is charged when the video completes, and the total equals the website price. Poll get_job until plan_ready, then get_narration."""
        try:
            if not (prompt or "").strip() and not (script or "").strip() and not voiceover_path and not voiceover_url:
                return {"error": "Provide prompt, script, voiceover_path or voiceover_url."}
            return _client().create_job(
                review_token=review_token,
                acknowledge=acknowledge,
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
        annotations=ToolAnnotations(title="Get narration", read_only_hint=True, open_world_hint=False),
    )
    def get_narration(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status plan_ready)")],
        include_words: Annotated[
            bool, Field(description="Add every word's start/end to each sentence (large: about 10 KB a minute). Phrases in a plan are matched server-side, so leave it off unless you need word times.")
        ] = False,
    ) -> dict[str, Any]:
        """The narration you plan against: numbered sentences with start/end seconds (and every word's timing with include_words), narration_digest (copy it into the plan), suggested chapters, the template's theme and switched-off graphics, pricing (website price, already charged, due at continue), and narration_audio: a url to listen to (check names and borrowed words before you lock) and the API path that downloads the MP3. The sentence text is what the voice said, so a misspoken word shows up there too."""
        try:
            return _compact_narration(_client().get_narration(job_id), include_words=include_words)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get graphics catalog",
        annotations=ToolAnnotations(title="Get graphics catalog", read_only_hint=True, open_world_hint=False),
    )
    def get_kind_catalog(
        format: Annotated[str | None, Field(description="faceless_documentary, faceless_top_10, avatar_video or vector_explainer")] = None,
        theme: Annotated[str | None, Field(description="Visual theme, to mark graphics the theme does not use")] = None,
        compact: Annotated[bool, Field(description="One summary line per graphic (start here; the full catalog is large)")] = True,
        types: Annotated[list[str] | None, Field(description="Full entries (description, prop_schema, media_slots, durations, preview) for just these animation types / overlay styles")] = None,
        premium: Annotated[bool, Field(description="Only the premium boards (marked premium: true)")] = False,
    ) -> dict[str, Any]:
        """Every motion graphic Cliptude can render: planner kinds, ~360 animation types and 20 overlay text styles. Read compact first, then fetch full entries for the types you intend to use: their props, media slots, hold times, caps and preview video. Entries marked `premium: true` are the premium boards (newest designs, each with its own timed sound cues); premium=true lists only those. Also lists `transitions` (slugs for beats[].transition_in), `reveal_styles` (beats[].reveal_next, with preview videos), `clip_effects` (beats[].clip_effect: a CapCut-style look or camera move on a beat's picture) and `sounds` (ids for graphic sound, transition_sound, reveal_sound and sfx, with a preview link to each file)."""
        try:
            return _client().get_kind_catalog(format=format, theme=theme, compact=compact and not types, types=types, premium=premium)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Check plan",
        annotations=ToolAnnotations(title="Check plan", read_only_hint=True, open_world_hint=False),
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

    @mcp.tool(
        title="Lock plan",
        annotations=ToolAnnotations(title="Lock plan", read_only_hint=False, destructive_hint=False),
    )
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
        annotations=ToolAnnotations(title="Get locked plan", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Review the built video", read_only_hint=True, open_world_hint=False),
    )
    def get_plan_review(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID (status ready_for_edit)")],
    ) -> dict[str, Any]:
        """What the built design shows, before any render: every picture/clip in order (beat, time, the sentence under it, what you asked for, what was used, a link to the file) and every graphic (time on screen and the props it will be drawn with, or why it is missing), plus quality_findings: Cliptude's quality checks on the build. Fix every finding with fix_in "plan" (follow its how); fix_in "cliptude" ones are not yours. Revise with check_plan/lock_plan + continue_job on the same job, review again, then render_project once (it refuses while plan findings remain unless accept_quality_findings is true)."""
        try:
            return _client().get_plan_review(job_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Upload plan media",
        annotations=ToolAnnotations(title="Upload plan media", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def upload_plan_media(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
        path: Annotated[
            str,
            Field(description="Local picture (JPG/PNG/WEBP/GIF, up to 15 MB) or clip (MP4/MOV/WEBM/M4V, up to 300 MB). Stdio / local MCP only: on a remote host (mcp.cliptude.com) use start_media_upload + finish_media_upload, which work from any agent that can run curl."),
        ],
        split_shots: Annotated[bool, Field(description="For a long source clip (a trailer, featurette, interview): also cut it into clips of 3-12 s that start and end on its cuts, each checked, measured and kept like an upload, with near-black and white-flash parts left out. Returns shots=[{keep, from_s, seconds, notes?}]. Use it instead of cutting clips yourself.")] = False,
    ) -> dict[str, Any]:
        """Send a picture or clip you found yourself to a plan job. The file goes straight to Cliptude's storage on signed URLs (no storage keys needed) and is checked to be a real picture or video, and for nudity and gore (refused). Returns {keep, file, media_type, size, notes?}: put {"keep": "<id>"} as a beat's media or a graphic's picture slot, with start_s/end_s on a beat to play only part of a clip. notes say where a clip runs into a title or logo card or a weapon aimed at someone (trim it with start_s/end_s as they say), and what the build review will report on the file (a channel watermark, an end screen, captions): use another file then."""
        try:
            return _client().upload_plan_media(job_id, path, split_shots=split_shots)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Start media upload",
        annotations=ToolAnnotations(title="Start media upload", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def start_media_upload(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
        filename: Annotated[str, Field(min_length=1, description="The file's name with its extension: JPG/PNG/WEBP/GIF picture (up to 15 MB) or MP4/MOV/WEBM/M4V clip (up to 300 MB).")],
        size: Annotated[int, Field(gt=0, description="The file's size in bytes (e.g. `wc -c < file` or `stat`).")],
        content_type: Annotated[str | None, Field(description="MIME type; omit to infer it from the file name.")] = None,
    ) -> dict[str, Any]:
        """Upload a picture or clip to a plan job from ANY host, remote included: Cliptude signs a PUT URL for each part and you send the bytes yourself, straight to Cliptude's storage. Then call finish_media_upload with the same key and upload_id to get its keep id. Each part: PUT bytes [offset, offset+length) of the file to its url, e.g. `tail -c +$((offset+1)) FILE | head -c LENGTH | curl -sf -X PUT --data-binary @- "URL"` (one part: `curl -sf -X PUT --upload-file FILE "URL"`). URLs expire in 1 hour."""
        try:
            out = _client().start_media_upload(job_id, filename=filename, size=size, content_type=content_type)
            out["next"] = "PUT every part to its url, then finish_media_upload(job_id, key, upload_id)."
            return out
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Finish media upload",
        annotations=ToolAnnotations(title="Finish media upload", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def finish_media_upload(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
        key: Annotated[str, Field(min_length=1, description="key from start_media_upload")],
        upload_id: Annotated[str, Field(min_length=1, description="upload_id from start_media_upload")],
        split_shots: Annotated[bool, Field(description="For a long source clip (a trailer, featurette, interview): also cut it into clips of 3-12 s that start and end on its cuts, each checked, measured and kept like an upload, with near-black and white-flash parts left out. Returns shots=[{keep, from_s, seconds, notes?}]. Use it instead of cutting clips yourself.")] = False,
    ) -> dict[str, Any]:
        """Close an upload after every part is PUT. Cliptude checks it is a real picture or video (nudity and gore are refused) and returns {keep, file, media_type, size, notes?}: put {"keep": "<id>"} as a beat's media or a graphic's picture slot (start_s/end_s on a beat to play part of a clip). notes say where a clip runs into a title or logo card or a weapon aimed at someone: trim it as they say."""
        try:
            return _client().finish_media_upload(job_id, key=key, upload_id=upload_id, split_shots=split_shots)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Abort media upload",
        annotations=ToolAnnotations(title="Abort media upload", read_only_hint=False, destructive_hint=True, open_world_hint=False),
    )
    def abort_media_upload(
        job_id: Annotated[str, Field(min_length=8, description="Plan job UUID")],
        key: Annotated[str, Field(min_length=1, description="key from start_media_upload")],
        upload_id: Annotated[str, Field(min_length=1, description="upload_id from start_media_upload")],
    ) -> dict[str, Any]:
        """Cancel an upload you will not finish (frees the parts already sent)."""
        try:
            return _client().abort_media_upload(job_id, key=key, upload_id=upload_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Start voiceover upload",
        annotations=ToolAnnotations(title="Start voiceover upload", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def start_voiceover_upload(
        filename: Annotated[str, Field(min_length=1, description="The narration file's name: MP3, M4A, WAV, OGG, FLAC or WEBM (up to 200 MB).")],
        size: Annotated[int, Field(gt=0, description="The file's size in bytes.")],
        content_type: Annotated[str | None, Field(description="MIME type; omit to infer it from the file name.")] = None,
    ) -> dict[str, Any]:
        """Upload your own narration before the job exists, from ANY host, remote included (for render_only plan jobs, or create_job with your voiceover). PUT each part's bytes to its url exactly as for start_media_upload, then call finish_voiceover_upload: it returns a voiceover_url to pass to create_plan_job / create_job. URLs expire in 1 hour."""
        try:
            out = _client().start_voiceover_upload(filename=filename, size=size, content_type=content_type)
            out["next"] = "PUT every part to its url, then finish_voiceover_upload(key, upload_id) for the voiceover_url."
            return out
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Finish voiceover upload",
        annotations=ToolAnnotations(title="Finish voiceover upload", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def finish_voiceover_upload(
        key: Annotated[str, Field(min_length=1, description="key from start_voiceover_upload")],
        upload_id: Annotated[str, Field(min_length=1, description="upload_id from start_voiceover_upload")],
    ) -> dict[str, Any]:
        """Close a narration upload. Returns {voiceover_url, key, expires_in_s}: pass voiceover_url to create_plan_job (render_only or not) or create_job within the hour. The job checks the audio when it downloads it."""
        try:
            return _client().finish_voiceover_upload(key=key, upload_id=upload_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get plan sourcing report",
        annotations=ToolAnnotations(title="Get plan sourcing report", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Get job", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="List jobs", read_only_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Cancel job", read_only_hint=False, destructive_hint=True, open_world_hint=False),
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
        annotations=ToolAnnotations(title="Open editor project", read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    def open_editor_project(
        job_id: Annotated[str, Field(min_length=8, description="Completed video job id")],
    ) -> dict[str, Any]:
        """Open (or create) the timeline editor project for a completed video job. Returns project id, revision, a compact timeline (item id, type, start_s, end_s, text) to reference in run_agent, and export_credits_due: what render_project would charge now."""
        try:
            return _compact_editor_project(_client().open_editor_project(job_id))
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get editor project",
        annotations=ToolAnnotations(title="Get editor project", read_only_hint=True, open_world_hint=False),
    )
    def get_editor_project(
        project_id: Annotated[int, Field(description="Editor project id")],
    ) -> dict[str, Any]:
        """Current revision, compact timeline, latest render and export_credits_due (what render_project would charge now) of an editor project."""
        try:
            return _compact_editor_project(_client().get_editor_project(project_id))
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="View scenes",
        annotations=ToolAnnotations(title="View scenes", read_only_hint=True, open_world_hint=False),
    )
    def view_scenes(
        project_id: Annotated[int, Field(description="Editor project id")],
        start_s: Annotated[float, Field(ge=0, description="First second to show")] = 0.0,
        end_s: Annotated[float | None, Field(description="Stop before this second (default: the end)")] = None,
    ) -> list[str | Image]:
        """See what the timeline shows, without opening any link: numbered contact sheets of every picture, a frame of every clip and every board (graphic) drawn as the export draws it, with the footage under it and anything on top, with the narration under each, plus a legend (tile, item_id, start_s, end_s, words, said, flags: REPEATED PICTURE, HELD, AI STILL, DARK, BARS, BARE BOARD, BOARD PENDING). Up to 24 tiles per call; call again from next_start_s. When the answer has boards_pending, the boards are still rendering: call again with the same start_s in about a minute. When it has boards_not_drawn, that page is not reviewed: do not pass it. Use it to review a built video yourself (other brands, wrong pictures, repeats, leftover text, boards with no picture, wrong or placeholder text on a board) before giving the user the editor link, and again after run_agent to check the change."""
        try:
            data = _client().view_scenes(project_id, start_s=start_s, end_s=end_s)
        except Exception as exc:
            return [json.dumps(_err(exc))]
        sheets = data.pop("sheets", None) or []
        out: list[str | Image] = [json.dumps(data, ensure_ascii=False)]
        for sheet in sheets:
            out.append(Image(data=base64.b64decode(sheet["jpeg_base64"]), format="jpeg"))
        return out

    @mcp.tool(
        title="Run Cliptude Agent",
        annotations=ToolAnnotations(title="Run Cliptude Agent", read_only_hint=False, destructive_hint=False),
    )
    def run_agent(
        project_id: Annotated[int, Field(description="Editor project id")],
        prompt: Annotated[str, Field(min_length=1, description="Edit request in plain English")],
        referenced_item_ids: Annotated[
            list[str] | None, Field(description="Timeline item ids the request is about (from the timeline listing)")
        ] = None,
        tier: Annotated[str, Field(description="fast or smart")] = "fast",
        chat_id: Annotated[str | None, Field(description="Continue an existing agent chat")] = None,
    ) -> dict[str, Any]:
        """Ask Cliptude Agent to edit the timeline (swap footage, regenerate images or voiceover ranges, music, transitions, text). One kind of change per run: picture swaps and text edits go in separate runs. Several clips can get new pictures in one run: reference them all and describe each one's picture by its item id; a clip with nothing suitable is left unchanged and named in the reply. Swapping a graphic's picture keeps the graphic and its words. To use one exact picture (a product cover, a screenshot), paste its https link in the prompt: it is downloaded and applied as is, not searched for. Returns a run; poll get_agent_run. Charges agent credits. Then render_project for a new MP4, after view_scenes on the changed range."""
        try:
            return _compact_agent_run(_client().run_agent(
                project_id, prompt=prompt, tier=tier, referenced_item_ids=referenced_item_ids, chat_id=chat_id
            ))
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get agent run",
        annotations=ToolAnnotations(title="Get agent run", read_only_hint=True, open_world_hint=False),
    )
    def get_agent_run(
        project_id: Annotated[int, Field(description="Editor project id")],
        run_id: Annotated[str, Field(min_length=8, description="Agent run id")],
    ) -> dict[str, Any]:
        """Status of a Cliptude Agent run plus the agent's messages."""
        try:
            return _compact_agent_run(_client().get_agent_run(project_id, run_id))
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Cancel agent run",
        annotations=ToolAnnotations(title="Cancel agent run", read_only_hint=False, destructive_hint=True),
    )
    def cancel_agent_run(
        project_id: Annotated[int, Field(description="Editor project id")],
        run_id: Annotated[str, Field(min_length=8, description="Agent run id")],
    ) -> dict[str, Any]:
        """Cancel a queued or running Cliptude Agent run."""
        try:
            return _client().cancel_agent_run(project_id, run_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Render editor project",
        annotations=ToolAnnotations(title="Render editor project", read_only_hint=False, destructive_hint=False),
    )
    def render_project(
        project_id: Annotated[int, Field(description="Editor project id")],
        accept_quality_findings: Annotated[
            bool,
            Field(description="Agent-planned jobs only: render although get_plan_review still lists quality_findings with fix_in \"plan\". Set it only after trying to fix them, or when the user says to ship as is."),
        ] = False,
    ) -> dict[str, Any]:
        """Render the edited timeline to a new MP4. The first export of a project is free; later exports cost 10% of the original generation (export_credits_due on the project says the exact amount). Poll get_render for output_url. On an agent-planned job, a build with quality_findings the plan can fix returns 409 quality_findings_open: revise the plan and review again first."""
        try:
            return _client().render_project(project_id, accept_quality_findings=accept_quality_findings)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get render",
        annotations=ToolAnnotations(title="Get render", read_only_hint=True, open_world_hint=False),
    )
    def get_render(
        render_id: Annotated[int, Field(description="Editor render id")],
    ) -> dict[str, Any]:
        """Status of an editor render; output_url when completed. The link plays in the browser and expires in 7 days, but the MP4 stays in the account: call get_render again for a fresh link instead of telling the user to download before it expires."""
        try:
            return _client().get_render(render_id)
        except Exception as exc:
            return _err(exc)

    @mcp.tool(
        title="Get account",
        annotations=ToolAnnotations(title="Get account", read_only_hint=True, open_world_hint=False),
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
