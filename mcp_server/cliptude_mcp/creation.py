"""MCP tools that make the user's own avatar and voices: create_avatar, design_voice_previews, save_designed_voice,
clone_voice. The avatar and voice picker widgets call them with `tools/call`; the model can call them too.

Files travel as base64 in the tool call (the widget reads the file the user picked, or a recording), never as a
path on the hosted server: a local path is accepted only by the stdio server, which runs on the user's computer.
The server-side rules are the website's (videos/creation_api.py): paid plans, the 55-credit avatar goes through a
428 price review first, clones need the same consent as the Clone a voice page, every voice and avatar is private.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from cliptude_mcp.client import CliptudeApiError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CLONE_CONSENT_TEXT = (
    "I confirm this is my own voice, or I have the voice owner's explicit consent and the rights to clone it. "
    "I will not use cloned voices to impersonate or deceive others, as required by the Terms of Service."
)


def _file_arg(
    *, data_base64: str | None, file_name: str | None, path: str | None, local: bool, what: str
) -> tuple[str, bytes, str] | None:
    """(name, bytes, mime) from base64 or (stdio only) a local path; None when neither was given."""
    if path:
        if not local:
            raise CliptudeApiError(f"This server cannot read files on your computer: send the {what} as base64.")
        p = Path(path).expanduser()
        if not p.is_file():
            raise CliptudeApiError(f"{what.capitalize()} not found: {p}")
        raw = p.read_bytes()
        name = p.name
    elif data_base64:
        text = data_base64.split(",", 1)[1] if data_base64.startswith("data:") else data_base64
        try:
            raw = base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise CliptudeApiError(f"The {what} is not valid base64.") from exc
        name = (file_name or "").strip() or what.replace(" ", "_")
    else:
        return None
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CliptudeApiError(f"The {what} is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not raw:
        raise CliptudeApiError(f"The {what} is empty.")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return name, raw, mime


CLONE_PROVIDERS = frozenset({"elevenlabs", "minimax", "cartesia", "inworld", "voicetory"})


def _path_refused(local: bool, name: str, value: Any) -> dict[str, Any] | None:
    """A hosted server never reads its own disk for a caller (same code as the server's other tools)."""
    if local or value is None or not str(value).strip():
        return None
    from cliptude_mcp.server import LOCAL_FILES_REFUSED

    return {"error": LOCAL_FILES_REFUSED.format(param=name), "code": "local_path_not_allowed"}


def post_form(client: Any, path: str, data: dict[str, Any], files: dict[str, tuple[str, bytes, str]] | None = None,
              *, _retried: bool = False) -> Any:
    """Multipart/form POST to the Cliptude API with the client's auth (client.request only sends JSON)."""
    try:
        resp = httpx.post(
            f"{client.base_url}{path}",
            headers=client._headers(json_content=False),
            data={k: v for k, v in data.items() if v not in (None, "")},
            files=files or None,
            timeout=max(float(client.timeout or 0), 180.0),
        )
    except httpx.HTTPError as exc:
        raise CliptudeApiError(str(exc)) from exc
    if resp.status_code == 401 and not _retried and client._maybe_refresh():
        return post_form(client, path, data, files, _retried=True)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": resp.text}
    if resp.status_code >= 400:
        client._raise_http(resp, payload=payload)
    return payload


def register_creation_tools(
    mcp: Any, client: Callable[[], Any], err: Callable[[Exception], dict[str, Any]], *, local: bool
) -> None:
    @mcp.tool(
        title="Create avatar",
        annotations=ToolAnnotations(title="Create avatar", read_only_hint=False, destructive_hint=False,
                                    open_world_hint=True),
    )
    def create_avatar(
        display_name: Annotated[str, Field(min_length=1, max_length=100, description="Name for the new avatar")],
        gender: Annotated[str | None, Field(description='"male" or "female" (needed for a description; a photo is detected)')] = None,
        appearance: Annotated[str | None, Field(description="Describe the presenter, when there is no photo")] = None,
        photo_base64: Annotated[str | None, Field(description="A landscape 16:9 JPG/PNG/WEBP photo, base64 (max 10 MB)")] = None,
        photo_file_name: Annotated[str | None, Field(description="The photo's file name, e.g. me.jpg")] = None,
        photo_path: Annotated[str | None, Field(description="Local photo path (only when Cliptude runs on this computer)")] = None,
        review_token: Annotated[str | None, Field(description="From the 428 review, after the user approved the price")] = None,
    ) -> dict[str, Any]:
        """Create the user's own private photo avatar for Avatar Video, from a 16:9 photo of them (or someone who agreed) or a description. It costs 55 credits: the first call returns a review (status 428, detail.review with the price) and creates nothing; show it to the user and call again with the same arguments and review_token once they approve. HeyGen finishes it in a few minutes; list_avatars(section="mine") shows it then."""
        refused = _path_refused(local, "photo_path", photo_path)
        if refused:
            return refused
        try:
            photo = _file_arg(data_base64=photo_base64, file_name=photo_file_name, path=photo_path, local=local,
                              what="photo")
            data = {"display_name": display_name, "gender": gender, "appearance": appearance,
                    "review_token": review_token}
            return post_form(client(), "/api/v1/avatars/new", data,
                             {"photo_file": photo} if photo else None)
        except Exception as exc:
            return err(exc)

    @mcp.tool(
        title="Design voice previews",
        annotations=ToolAnnotations(title="Design voice previews", read_only_hint=False, destructive_hint=False,
                                    open_world_hint=True),
    )
    def design_voice_previews(
        provider: Annotated[str, Field(description="elevenlabs, minimax or inworld")],
        description: Annotated[str, Field(min_length=10, max_length=1000, description="The voice to design: age, gender, accent, tone, pace")],
        language: Annotated[str | None, Field(description="Narration language, e.g. English")] = None,
    ) -> CallToolResult:
        """Design a new narration voice from a description: returns a few previews (preview_id) to listen to. Keep one with save_designed_voice. Paid plans; a user keeps one designed voice per provider. The audio goes to the voice picker widget only; the text result lists the preview ids."""
        try:
            payload = post_form(client(), f"/api/v1/voice-designs/{provider.strip().lower()}/previews",
                                {"description": description, "language": language})
        except Exception as exc:
            failure = err(exc)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(failure, default=str))],
                                  structured_content=failure)
        previews = payload.get("previews") or [] if isinstance(payload, dict) else []
        summary = {
            "provider": provider,
            "previews": [{"preview_id": p.get("preview_id")} for p in previews],
            "rounds_left_today": payload.get("rounds_left_today") if isinstance(payload, dict) else None,
            "next": "Let the user listen in the voice picker (or describe the previews), then save_designed_voice "
                    "with the preview_id they pick and a voice_name.",
        }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(summary))],
            structured_content={"provider": provider, **(payload if isinstance(payload, dict) else {})},
        )

    @mcp.tool(
        title="Save designed voice",
        annotations=ToolAnnotations(title="Save designed voice", read_only_hint=False, destructive_hint=False,
                                    open_world_hint=True),
    )
    def save_designed_voice(
        provider: Annotated[str, Field(description="Same provider as the previews")],
        preview_id: Annotated[str, Field(min_length=1, description="The preview the user picked")],
        voice_name: Annotated[str, Field(min_length=1, max_length=100, description="Name for the voice")],
    ) -> dict[str, Any]:
        """Keep one designed preview as the user's own private voice. It then appears in list_voices under my_voices and can be used as voice_id (with voice_provider) in create_template."""
        try:
            return post_form(client(), f"/api/v1/voice-designs/{provider.strip().lower()}",
                             {"preview_id": preview_id, "voice_name": voice_name})
        except Exception as exc:
            return err(exc)

    @mcp.tool(
        title="Clone voice",
        annotations=ToolAnnotations(title="Clone voice", read_only_hint=False, destructive_hint=False,
                                    open_world_hint=True),
    )
    def clone_voice(
        provider: Annotated[str, Field(description="elevenlabs, minimax, cartesia, inworld or voicetory")],
        voice_name: Annotated[str, Field(min_length=1, max_length=100, description="Name for the clone")],
        consent: Annotated[bool, Field(description="True only after the user confirmed: " + CLONE_CONSENT_TEXT)],
        audio_base64: Annotated[str | None, Field(description="30 s to 3 min of clear speech (MP3, M4A or WAV), base64, max 10 MB")] = None,
        audio_file_name: Annotated[str | None, Field(description="The recording's file name, e.g. me.mp3")] = None,
        audio_path: Annotated[str | None, Field(description="Local recording path (only when Cliptude runs on this computer)")] = None,
    ) -> dict[str, Any]:
        """Clone the user's own voice from a recording, private to their account; it then appears in list_voices under my_voices. Paid plans. Ask the user to confirm the consent statement first and pass consent=true only after they do."""
        refused = _path_refused(local, "audio_path", audio_path)
        if refused:
            return refused
        provider = (provider or "").strip().lower()
        if provider not in CLONE_PROVIDERS:
            return {"error": f"provider must be one of: {', '.join(sorted(CLONE_PROVIDERS))}."}
        if not consent:
            return {"error": "The user must confirm: " + CLONE_CONSENT_TEXT}
        try:
            audio = _file_arg(data_base64=audio_base64, file_name=audio_file_name, path=audio_path, local=local,
                              what="recording")
            if audio is None:
                return {"error": "Send the recording as audio_base64."}
            return post_form(client(), f"/api/v1/voice-clones/{provider}",
                             {"voice_name": voice_name, "voice_consent": "true"}, {"clone_audio": audio})
        except Exception as exc:
            return err(exc)
