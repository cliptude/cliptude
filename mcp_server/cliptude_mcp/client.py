"""HTTP client for Cliptude /api/v1. No Django imports."""

from __future__ import annotations

import json
import mimetypes
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx

from cliptude_mcp.credentials import stored_access_token

DEFAULT_SAAS_URL = "https://cliptude.com"
TALKING_HEAD_EXTS = {".mp4", ".mov", ".webm", ".avi"}
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


def _talking_head_content_type(ext: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }.get((ext or "").lower(), "video/mp4")


def _probe_local_video_duration_seconds(path: Path) -> float | None:
    """Return duration via ffprobe when available; None if ffprobe is missing or fails."""
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        value = float((result.stdout or "").strip())
        if value > 0:
            return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


def _resolve_token(explicit: str | None) -> str:
    return (
        (explicit or "").strip()
        or (_request_token.get() or "").strip()
        or (os.environ.get("CLIPTUDE_API_KEY") or "").strip()
        or stored_access_token()
    )


def _avatar_job_fields(
    avatar_intro_only: bool | None,
    avatar_ai_footage: bool | None,
    *,
    as_strings: bool = False,
    avatar_profile_id: int | str | None = None,
    avatar_engine: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if avatar_profile_id not in (None, ""):
        fields["avatar_profile_id"] = str(avatar_profile_id) if as_strings else int(avatar_profile_id)
    if avatar_engine:
        fields["avatar_engine"] = str(avatar_engine).strip().lower()
    if avatar_intro_only is not None:
        fields["avatar_intro_only"] = (
            ("true" if avatar_intro_only else "false") if as_strings else bool(avatar_intro_only)
        )
    if avatar_ai_footage is not None:
        fields["avatar_ai_footage"] = (
            ("true" if avatar_ai_footage else "false") if as_strings else bool(avatar_ai_footage)
        )
    return fields


def _thumbnail_job_field(thumbnail: dict[str, str] | None, *, as_string: bool = False) -> dict[str, Any]:
    """create_job's per-job thumbnail ({custom_prompt} or {reference_url}); multipart sends it as JSON text."""
    cleaned = {k: str(v).strip() for k, v in (thumbnail or {}).items() if v is not None and str(v).strip()}
    if not cleaned:
        return {}
    return {"thumbnail": json.dumps(cleaned) if as_string else cleaned}


def _motion_control_job_fields(
    *,
    disable_animations: bool | None = None,
    disable_overlays: bool | None = None,
    disable_effects: bool | None = None,
    disable_transitions: bool | None = None,
    excluded_animation_types: list[str] | None = None,
    excluded_transition_kinds: list[str] | None = None,
    as_strings: bool = False,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    bool_fields = {
        "disable_animations": disable_animations,
        "disable_overlays": disable_overlays,
        "disable_effects": disable_effects,
        "disable_transitions": disable_transitions,
    }
    for key, value in bool_fields.items():
        if value is not None:
            fields[key] = ("true" if value else "false") if as_strings else bool(value)
    list_fields = {
        "excluded_animation_types": excluded_animation_types,
        "excluded_transition_kinds": excluded_transition_kinds,
    }
    for key, value in list_fields.items():
        if value is not None:
            fields[key] = ",".join(value) if as_strings else list(value)
    return fields


class CliptudeClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        client_kind: str | None = None,
    ):
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        # "cli" or "mcp": sent as X-Cliptude-Client so Cliptude records where a job was started.
        self.client_kind = (client_kind or os.environ.get("CLIPTUDE_CLIENT_KIND") or "").strip().lower()
        self.api_key = _resolve_token(api_key)
        self.timeout = timeout
        self._session_file = not bool(
            (api_key or "").strip()
            or (_request_token.get() or "").strip()
            or (os.environ.get("CLIPTUDE_API_KEY") or "").strip()
        )

    def _headers(self, *, json_content: bool = True) -> dict[str, str]:
        if not self.api_key:
            raise CliptudeApiError("Not authenticated. Run: cliptude auth login")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            **django_proxy_headers(self.base_url),
        }
        if self.client_kind:
            headers["X-Cliptude-Client"] = self.client_kind
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

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

    def create_template(self, *, name: str, format: str, settings: dict[str, Any]) -> Any:
        """Create a ready-to-use template from settings (no create wizard)."""
        return self.request(
            "POST", "/api/v1/templates", json_body={"name": name, "format": format, "settings": settings}
        )

    def template_options(self, format: str) -> Any:
        return self.request("GET", "/api/v1/templates/options", params={"format": format})

    def list_voices(self, *, provider: str = "elevenlabs", language: str | None = None) -> Any:
        params = {"provider": provider}
        if language:
            params["language"] = language
        return self.request("GET", "/api/v1/voices", params=params)

    def create_job(
        self,
        *,
        template_id: str,
        title: str,
        prompt: str | None = None,
        script: str | None = None,
        duration: str | None = None,
        voiceover_path: str | Path | None = None,
        voiceover_url: str | None = None,
        talking_head_path: str | Path | None = None,
        output: str | None = None,
        avatar_intro_only: bool | None = None,
        avatar_ai_footage: bool | None = None,
        avatar_profile_id: int | str | None = None,
        avatar_engine: str | None = None,
        disable_animations: bool | None = None,
        disable_overlays: bool | None = None,
        disable_effects: bool | None = None,
        disable_transitions: bool | None = None,
        excluded_animation_types: list[str] | None = None,
        excluded_transition_kinds: list[str] | None = None,
        render_only: bool | None = None,
        thumbnail: dict[str, str] | None = None,
        vector_character_id: str | None = None,
        vector_reveal_mode: str | None = None,
        pronunciations: list[dict[str, str]] | None = None,
    ) -> Any:
        if voiceover_path and voiceover_url:
            raise CliptudeApiError("Give voiceover_path or voiceover_url, not both.")
        if render_only and ((output or "").strip().lower() != "plan" or not (voiceover_path or voiceover_url)):
            raise CliptudeApiError(
                "render_only is a plan job with your own narration: pass output=\"plan\" and a voiceover "
                "(voiceover_path, or voiceover_url for a public link)."
            )
        motion_fields = _motion_control_job_fields(
            disable_animations=disable_animations,
            disable_overlays=disable_overlays,
            disable_effects=disable_effects,
            disable_transitions=disable_transitions,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
        )
        motion_fields_str = _motion_control_job_fields(
            disable_animations=disable_animations,
            disable_overlays=disable_overlays,
            disable_effects=disable_effects,
            disable_transitions=disable_transitions,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
            as_strings=True,
        )
        th_path = (str(talking_head_path).strip() if talking_head_path else "") or None
        vo_path = (str(voiceover_path).strip() if voiceover_path else "") or None
        output_kind = (output or "").strip().lower() or None
        if output_kind and output_kind not in ("video", "script", "voiceover", "plan"):
            raise CliptudeApiError('output must be "video", "script", "voiceover", or "plan".')
        if output_kind in ("script", "voiceover") and (th_path or vo_path):
            raise CliptudeApiError(
                "Script and voiceover jobs cannot upload a voiceover or talking-head file."
            )
        if th_path and vo_path:
            raise CliptudeApiError("Upload either a talking head video or a voiceover — not both.")
        if th_path and (prompt or script):
            raise CliptudeApiError(
                "Upload a talking head video, or send a prompt / custom script — not both. "
                "An uploaded talking head is transcribed and used as the narration."
            )
        if vo_path and (prompt or script):
            raise CliptudeApiError(
                "Upload a voiceover, or send a prompt / custom script — not both. "
                "An uploaded file is transcribed and used as the narration."
            )
        if th_path:
            return self._create_job_talking_head(
                template_id=template_id,
                title=title,
                talking_head_path=th_path,
                avatar_intro_only=avatar_intro_only,
                avatar_ai_footage=avatar_ai_footage,
                disable_animations=disable_animations,
                disable_overlays=disable_overlays,
                disable_effects=disable_effects,
                disable_transitions=disable_transitions,
                excluded_animation_types=excluded_animation_types,
                excluded_transition_kinds=excluded_transition_kinds,
                thumbnail=thumbnail,
            )
        if vo_path:
            extra_data = {
                **_avatar_job_fields(
                    avatar_intro_only, avatar_ai_footage, as_strings=True,
                    avatar_profile_id=avatar_profile_id, avatar_engine=avatar_engine,
                ),
                **motion_fields_str,
                **({"output": output_kind} if output_kind == "plan" else {}),
                **({"render_only": "true"} if render_only else {}),
                **_thumbnail_job_field(thumbnail, as_string=True),
                **({"vector_character_id": str(vector_character_id)} if vector_character_id else {}),
                **({"vector_reveal_mode": str(vector_reveal_mode)} if vector_reveal_mode else {}),
                **({"pronunciations": json.dumps(pronunciations)} if pronunciations is not None else {}),
            }
            return self._create_job_multipart(
                template_id=template_id,
                title=title,
                duration=duration,
                file_field="voiceover",
                file_path=vo_path,
                missing_message=f"Voiceover file not found: {vo_path}",
                extra_data=extra_data or None,
            )
        body: dict[str, Any] = {"template_id": template_id, "title": title}
        if prompt:
            body["prompt"] = prompt
        if script:
            body["script"] = script
        if duration:
            body["duration"] = duration
        if output_kind and output_kind != "video":
            body["output"] = output_kind
        if voiceover_url:
            # A public link to the narration: Cliptude downloads it (remote hosts cannot upload a file).
            body["voiceover_url"] = str(voiceover_url).strip()
        if render_only:
            body["render_only"] = True
        body.update(_avatar_job_fields(
            avatar_intro_only, avatar_ai_footage,
            avatar_profile_id=avatar_profile_id, avatar_engine=avatar_engine,
        ))
        body.update(motion_fields)
        body.update(_thumbnail_job_field(thumbnail))
        if vector_character_id:
            body["vector_character_id"] = str(vector_character_id)
        if vector_reveal_mode:
            body["vector_reveal_mode"] = str(vector_reveal_mode)
        if pronunciations is not None:
            body["pronunciations"] = pronunciations
        return self.request("POST", "/api/v1/jobs", json_body=body)

    def _raise_http(self, resp, *, payload=None) -> None:
        if payload is None:
            try:
                payload = resp.json()
            except ValueError:
                payload = {"error": resp.text}
        message = payload.get("error") if isinstance(payload, dict) else str(payload)
        if resp.status_code == 401:
            message = message or "Not authenticated. Run: cliptude auth login"
        raise CliptudeApiError(
            message or f"HTTP {resp.status_code}",
            status=resp.status_code,
            payload=payload,
        )

    def _create_job_multipart(
        self,
        *,
        template_id: str,
        title: str,
        duration: str | None,
        file_field: str,
        file_path: str | Path,
        missing_message: str,
        extra_data: dict[str, str] | None = None,
        _retried: bool = False,
    ) -> Any:
        path = Path(file_path)
        if not path.is_file():
            raise CliptudeApiError(missing_message)
        data: dict[str, str] = {"template_id": str(template_id), "title": title}
        if duration:
            data["duration"] = duration
        if extra_data:
            data.update(extra_data)
        url = f"{self.base_url}/api/v1/jobs"
        headers = self._headers(json_content=False)
        try:
            with path.open("rb") as fh:
                resp = httpx.post(
                    url,
                    headers=headers,
                    data=data,
                    files={file_field: (path.name, fh, "application/octet-stream")},
                    timeout=max(self.timeout, 300.0),
                )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        if resp.status_code == 401 and not _retried and self._maybe_refresh():
            return self._create_job_multipart(
                template_id=template_id,
                title=title,
                duration=duration,
                file_field=file_field,
                file_path=file_path,
                missing_message=missing_message,
                extra_data=extra_data,
                _retried=True,
            )
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}
        if resp.status_code >= 400:
            self._raise_http(resp, payload=payload)
        return payload

    def _abort_talking_head_upload(self, key: str | None, upload_id: str | None) -> None:
        if not key or not upload_id:
            return
        try:
            self.request(
                "POST",
                "/api/v1/uploads/talking-head/abort",
                json_body={"key": key, "upload_id": upload_id},
            )
        except Exception:
            pass

    def _create_job_talking_head(
        self,
        *,
        template_id: str,
        title: str,
        talking_head_path: str | Path,
        avatar_intro_only: bool | None = None,
        avatar_ai_footage: bool | None = None,
        disable_animations: bool | None = None,
        disable_overlays: bool | None = None,
        disable_effects: bool | None = None,
        disable_transitions: bool | None = None,
        excluded_animation_types: list[str] | None = None,
        excluded_transition_kinds: list[str] | None = None,
        thumbnail: dict[str, str] | None = None,
    ) -> Any:
        motion_extra = _motion_control_job_fields(
            disable_animations=disable_animations,
            disable_overlays=disable_overlays,
            disable_effects=disable_effects,
            disable_transitions=disable_transitions,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
            as_strings=True,
        )
        path = Path(talking_head_path)
        if not path.is_file():
            raise CliptudeApiError(f"Talking head file not found: {path}")
        ext = path.suffix.lower()
        if ext not in TALKING_HEAD_EXTS:
            raise CliptudeApiError(
                f'Unsupported file type "{ext}". Please upload MP4, MOV, WEBM, or AVI.'
            )
        size = path.stat().st_size
        duration = _probe_local_video_duration_seconds(path)
        init = self.request(
            "POST",
            "/api/v1/uploads/talking-head/init",
            json_body={
                "filename": path.name,
                "size": size,
                "content_type": _talking_head_content_type(ext),
            },
        )
        if not isinstance(init, dict) or init.get("fallback"):
            extra_data = {
                **_avatar_job_fields(avatar_intro_only, avatar_ai_footage, as_strings=True),
                **motion_extra,
                **_thumbnail_job_field(thumbnail, as_string=True),
            }
            return self._create_job_multipart(
                template_id=template_id,
                title=title,
                duration=None,
                file_field="talking_head",
                file_path=path,
                missing_message=f"Talking head file not found: {path}",
                extra_data=extra_data or None,
            )
        key = str(init.get("key") or "").strip()
        upload_id = str(init.get("upload_id") or "").strip()
        if duration is None:
            self._abort_talking_head_upload(key, upload_id)
            extra_data = {
                **_avatar_job_fields(avatar_intro_only, avatar_ai_footage, as_strings=True),
                **motion_extra,
                **_thumbnail_job_field(thumbnail, as_string=True),
            }
            return self._create_job_multipart(
                template_id=template_id,
                title=title,
                duration=None,
                file_field="talking_head",
                file_path=path,
                missing_message=f"Talking head file not found: {path}",
                extra_data=extra_data or None,
            )
        try:
            self._upload_talking_head_parts(path, init)
            self.request(
                "POST",
                "/api/v1/uploads/talking-head/complete",
                json_body={"key": key, "upload_id": upload_id},
            )
        except Exception:
            self._abort_talking_head_upload(key, upload_id)
            raise
        body: dict[str, Any] = {
            "template_id": str(template_id),
            "title": title,
            "talking_head_r2_key": key,
            "talking_head_duration": str(duration),
        }
        body.update(_avatar_job_fields(avatar_intro_only, avatar_ai_footage))
        body.update(_thumbnail_job_field(thumbnail))
        body.update(
            _motion_control_job_fields(
                disable_animations=disable_animations,
                disable_overlays=disable_overlays,
                disable_effects=disable_effects,
                disable_transitions=disable_transitions,
                excluded_animation_types=excluded_animation_types,
                excluded_transition_kinds=excluded_transition_kinds,
            )
        )
        return self.request("POST", "/api/v1/jobs", json_body=body)

    def _upload_talking_head_parts(self, path: Path, init: dict[str, Any]) -> None:
        self._upload_parts(path, init, "/api/v1/uploads/talking-head/sign-parts", "talking head")

    def _upload_parts(self, path: Path, init: dict[str, Any], sign_path: str, label: str) -> None:
        """PUT the file straight to R2, part by part, on URLs Cliptude signs (no R2 keys here)."""
        key = str(init.get("key") or "").strip()
        upload_id = str(init.get("upload_id") or "").strip()
        part_size = int(init.get("part_size") or 0)
        part_count = int(init.get("part_count") or 0)
        if not key or not upload_id or part_size <= 0 or part_count <= 0:
            raise CliptudeApiError(f"The {label} upload init returned an incomplete plan.")
        with path.open("rb") as fh:
            for start in range(0, part_count, 50):
                nums = list(range(start + 1, min(start + 50, part_count) + 1))
                signed = self.request(
                    "POST",
                    sign_path,
                    json_body={"key": key, "upload_id": upload_id, "part_numbers": nums},
                )
                urls = (signed or {}).get("urls") if isinstance(signed, dict) else None
                if not isinstance(urls, dict):
                    raise CliptudeApiError(f"Could not sign {label} upload parts.")
                for num in nums:
                    url = urls.get(str(num))
                    if not url:
                        raise CliptudeApiError(f"Could not sign part {num}.")
                    chunk = fh.read(part_size)
                    try:
                        resp = httpx.put(url, content=chunk, timeout=max(self.timeout, 300.0))
                    except httpx.HTTPError as exc:
                        raise CliptudeApiError(str(exc)) from exc
                    if resp.status_code >= 400:
                        raise CliptudeApiError(
                            f"Failed to upload {label} part {num}.",
                            status=resp.status_code,
                        )

    def get_job(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/jobs/{job_id}")

    def continue_job(self, job_id: str) -> Any:
        """Queue a completed script/voiceover job, or a plan job with a locked plan, as a full video."""
        return self.request("POST", f"/api/v1/jobs/{job_id}/continue")

    # Product videos (store products: Shopify, any store page) --------------------------------------

    def fetch_product_context(self, product_url_or_query: str, *, store_domain: str | None = None) -> Any:
        """Read a store product (URL, connected-store id/handle, or a search on store_domain) and get 3 angles."""
        body: dict[str, Any] = {"url_or_query": product_url_or_query}
        if store_domain:
            body["store_domain"] = store_domain
        return self.request("POST", "/api/v1/commerce/products/fetch", json_body=body)

    def draft_product_script(
        self,
        product_id: str,
        *,
        angle: Any = None,
        duration: str | int | None = None,
        template_id: str | None = None,
        title: str | None = None,
        discount_code: str | None = None,
        cta_url: str | None = None,
        cta_text: str | None = None,
        image_urls: list[str] | None = None,
        reviews: list[dict] | None = None,
    ) -> Any:
        """Queue the 80/20 educational script for a product; returns {script_id, ...} immediately."""
        body: dict[str, Any] = {"product_id": product_id}
        for key, value in (
            ("angle", angle), ("duration", duration), ("template_id", template_id), ("title", title),
            ("discount_code", discount_code), ("cta_url", cta_url), ("cta_text", cta_text),
            ("image_urls", image_urls), ("reviews", reviews),
        ):
            if value not in (None, "", []):
                body[key] = value
        return self.request("POST", "/api/v1/commerce/scripts", json_body=body)

    def render_product_video(self, job_id: str, *, aspect_ratio: str = "16:9") -> Any:
        """Turn a ready product script into the video on the same job id (returns immediately)."""
        return self.request("POST", f"/api/v1/commerce/videos/{job_id}/render", json_body={"aspect_ratio": aspect_ratio})

    def get_product_video_status(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/commerce/videos/{job_id}")

    # Agent-planned videos -------------------------------------------------------------------------

    def upload_plan_media(self, job_id: str, path: str | Path) -> Any:
        """Send a picture or clip you found to a plan job; returns {keep, file, media_type, size} for media.keep."""
        path = Path(path)
        if not path.is_file():
            raise CliptudeApiError(f"File not found: {path}")
        base = f"/api/v1/jobs/{job_id}/media"
        init = self.request(
            "POST",
            f"{base}/init",
            json_body={
                "filename": path.name,
                "size": path.stat().st_size,
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            },
        )
        body = {"key": (init or {}).get("key"), "upload_id": (init or {}).get("upload_id")}
        try:
            self._upload_parts(path, init, f"{base}/sign-parts", "media")
            return self.request("POST", f"{base}/complete", json_body=body)
        except Exception:
            try:
                self.request("POST", f"{base}/abort", json_body=body)
            except Exception:
                pass
            raise

    def _start_signed_upload(self, base: str, *, filename: str, size: int, content_type: str | None) -> dict[str, Any]:
        """Open a multipart upload and sign every part: [{part, offset, length, url}] for the caller to PUT."""
        if int(size or 0) <= 0:
            raise CliptudeApiError("size (the file size in bytes) is required.")
        ctype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        init = self.request("POST", f"{base}/init", json_body={"filename": filename, "size": int(size), "content_type": ctype})
        key, upload_id = str((init or {}).get("key") or ""), str((init or {}).get("upload_id") or "")
        part_size, part_count = int((init or {}).get("part_size") or 0), int((init or {}).get("part_count") or 0)
        if not key or not upload_id or part_size <= 0 or part_count <= 0:
            raise CliptudeApiError("The upload init returned an incomplete plan.")
        parts: list[dict[str, Any]] = []
        for start in range(0, part_count, 50):
            nums = list(range(start + 1, min(start + 50, part_count) + 1))
            signed = self.request("POST", f"{base}/sign-parts", json_body={"key": key, "upload_id": upload_id, "part_numbers": nums})
            urls = (signed or {}).get("urls") if isinstance(signed, dict) else None
            if not isinstance(urls, dict):
                raise CliptudeApiError("Could not sign the upload parts.")
            for num in nums:
                offset = (num - 1) * part_size
                parts.append({"part": num, "offset": offset, "length": min(part_size, int(size) - offset), "url": urls.get(str(num))})
        return {"key": key, "upload_id": upload_id, "part_size": part_size, "part_count": part_count, "size": int(size), "parts": parts}

    def start_media_upload(self, job_id: str, *, filename: str, size: int, content_type: str | None = None) -> dict[str, Any]:
        """Signed PUT URLs for a picture or clip of a plan job, for a host that sends the bytes itself."""
        return self._start_signed_upload(f"/api/v1/jobs/{job_id}/media", filename=filename, size=size, content_type=content_type)

    def finish_media_upload(self, job_id: str, *, key: str, upload_id: str) -> Any:
        return self.request("POST", f"/api/v1/jobs/{job_id}/media/complete", json_body={"key": key, "upload_id": upload_id})

    def abort_media_upload(self, job_id: str, *, key: str, upload_id: str) -> Any:
        return self.request("POST", f"/api/v1/jobs/{job_id}/media/abort", json_body={"key": key, "upload_id": upload_id})

    def start_voiceover_upload(self, *, filename: str, size: int, content_type: str | None = None) -> dict[str, Any]:
        """Signed PUT URLs for a narration file, before the job exists."""
        return self._start_signed_upload("/api/v1/uploads/voiceover", filename=filename, size=size, content_type=content_type)

    def finish_voiceover_upload(self, *, key: str, upload_id: str) -> Any:
        """Returns {voiceover_url, key, expires_in_s}: pass voiceover_url to create_job / create_plan_job."""
        return self.request("POST", "/api/v1/uploads/voiceover/complete", json_body={"key": key, "upload_id": upload_id})

    def abort_voiceover_upload(self, *, key: str, upload_id: str) -> Any:
        return self.request("POST", "/api/v1/uploads/voiceover/abort", json_body={"key": key, "upload_id": upload_id})

    def get_narration(self, job_id: str) -> Any:
        """Numbered sentences (with word timings), digest, pricing and template flags of a plan job."""
        return self.request("GET", f"/api/v1/jobs/{job_id}/narration")

    def get_kind_catalog(
        self,
        *,
        format: str | None = None,
        theme: str | None = None,
        compact: bool = False,
        types: list[str] | None = None,
        premium: bool = False,
    ) -> Any:
        """Every graphic an agent can place. compact: one summary line each; types: full entries for those;
        premium: only the premium boards."""
        params = {k: v for k, v in (("format", format), ("theme", theme)) if v}
        if compact:
            params["compact"] = "1"
        if premium:
            params["premium"] = "1"
        if types:
            params["types"] = ",".join(types)
        return self.request("GET", "/api/v1/catalog/kinds", params=params or None)

    def put_plan(self, job_id: str, plan: dict[str, Any], *, dry_run: bool = True) -> Any:
        """Check a plan (dry_run) or lock it. A failing check returns its report, not an exception."""
        path = f"/api/v1/jobs/{job_id}/plan" + ("?dry_run=1" if dry_run else "")
        try:
            return self.request("PUT", path, json_body=plan)
        except CliptudeApiError as exc:
            if exc.status in (400, 409, 413) and isinstance(exc.payload, dict) and "errors" in exc.payload:
                return {**exc.payload, "http_status": exc.status}
            raise

    def get_plan(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/jobs/{job_id}/plan")

    def get_plan_report(self, job_id: str) -> Any:
        """What sourcing used for each media request of the locked plan, and every fallback."""
        return self.request("GET", f"/api/v1/jobs/{job_id}/plan/report")

    def get_plan_review(self, job_id: str) -> Any:
        """The built design, beat by beat and graphic by graphic, with links to the media (review before render)."""
        return self.request("GET", f"/api/v1/jobs/{job_id}/plan/review")

    def download_voiceover_file(self, job_id: str, dest: Path) -> Path:
        url = f"{self.base_url}/api/v1/jobs/{job_id}/voiceover"
        try:
            with httpx.stream(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "audio/mpeg, application/json",
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

    def list_jobs(self, *, status: str | None = None, limit: int = 20, offset: int = 0) -> Any:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self.request("GET", "/api/v1/jobs", params=params)

    def cancel_job(self, job_id: str) -> Any:
        return self.request("POST", f"/api/v1/jobs/{job_id}/cancel")

    # --- Timeline editor + Cliptude Agent -------------------------------------------------

    def open_editor_project(self, job_id: str) -> Any:
        """Open (or create) the editor project for a completed video job; includes a compact timeline."""
        return self.request("GET", f"/api/v1/jobs/{job_id}/editor-project")

    def get_editor_project(self, project_id: int | str, *, design: bool = False) -> Any:
        params = {"design": "1"} if design else None
        return self.request("GET", f"/api/v1/editor-projects/{project_id}", params=params)

    def view_scenes(self, project_id: int | str, *, start_s: float = 0.0, end_s: float | None = None) -> Any:
        """Contact sheets (base64 JPEG) of the timeline's pictures, clip frames and graphics, with a legend."""
        params: dict[str, Any] = {"start_s": start_s}
        if end_s is not None:
            params["end_s"] = end_s
        return self.request("GET", f"/api/v1/editor-projects/{project_id}/scenes", params=params)

    def agent_tiers(self) -> Any:
        return self.request("GET", "/api/v1/agent/tiers")

    def run_agent(
        self,
        project_id: int | str,
        *,
        prompt: str,
        tier: str = "fast",
        referenced_item_ids: list[str] | None = None,
        chat_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Ask Cliptude Agent to edit the timeline. Uses the project's current revision unless given."""
        import uuid

        if expected_revision is None:
            project = self.get_editor_project(project_id).get("project") or {}
            expected_revision = int(project.get("revision") or 0)
        body: dict[str, Any] = {
            "prompt": prompt,
            "tier": tier,
            "referenced_item_ids": list(referenced_item_ids or []),
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key or uuid.uuid4().hex,
        }
        if chat_id:
            body["chat_id"] = chat_id
        return self.request("POST", f"/api/v1/editor-projects/{project_id}/agent/runs", json_body=body)

    def get_agent_run(self, project_id: int | str, run_id: str) -> Any:
        return self.request("GET", f"/api/v1/editor-projects/{project_id}/agent/runs/{run_id}")

    def cancel_agent_run(self, project_id: int | str, run_id: str) -> Any:
        return self.request("POST", f"/api/v1/editor-projects/{project_id}/agent/runs/{run_id}/cancel")

    def render_project(self, project_id: int | str) -> Any:
        """Render the edited timeline to a new MP4: free the first time, then 10% of the original generation."""
        return self.request("POST", f"/api/v1/editor-projects/{project_id}/render")

    def get_render(self, render_id: int | str) -> Any:
        return self.request("GET", f"/api/v1/editor-renders/{render_id}")

    def get_account(self) -> Any:
        return self.request("GET", "/api/v1/account")

    def estimate_credits(
        self,
        *,
        template_id: str,
        prompt: str | None = None,
        script: str | None = None,
        duration: str | None = None,
        voiceover_seconds: float | None = None,
        talking_head_seconds: float | None = None,
        output: str | None = None,
        avatar_intro_only: bool | None = None,
        avatar_ai_footage: bool | None = None,
        avatar_profile_id: int | str | None = None,
        avatar_engine: str | None = None,
        render_only: bool | None = None,
        vector_reveal_mode: str | None = None,
    ) -> Any:
        """Credits a job would need; creates nothing."""
        body: dict[str, Any] = {"template_id": template_id}
        if render_only:
            body["render_only"] = True
        for key, value in (
            ("prompt", prompt),
            ("script", script),
            ("duration", duration),
            ("voiceover_seconds", voiceover_seconds),
            ("talking_head_seconds", talking_head_seconds),
            ("output", output),
            ("vector_reveal_mode", vector_reveal_mode),
        ):
            if value is not None and value != "":
                body[key] = value
        body.update(_avatar_job_fields(
            avatar_intro_only, avatar_ai_footage,
            avatar_profile_id=avatar_profile_id, avatar_engine=avatar_engine,
        ))
        return self.request("POST", "/api/v1/jobs/estimate", json_body=body)

    def list_vector_characters(self) -> Any:
        """Vector Explainer characters: the built-in cast and your uploads (ids for vector_character_id)."""
        return self.request("GET", "/api/v1/vector-characters")

    def upload_vector_character(
        self,
        *,
        image_path: str | Path | None = None,
        image_url: str | None = None,
        name: str = "",
        _retried: bool = False,
    ) -> Any:
        """Add a Vector Explainer character from a local image or a public image URL; returns its id."""
        if bool(image_path) == bool((image_url or "").strip()):
            raise CliptudeApiError("Give image_path or image_url (one of them).")
        if image_url:
            return self.request(
                "POST", "/api/v1/vector-characters", json_body={"image_url": image_url.strip(), "name": name}
            )
        path = Path(image_path)
        if not path.is_file():
            raise CliptudeApiError(f"Character image not found: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as fh:
                resp = httpx.post(
                    f"{self.base_url}/api/v1/vector-characters",
                    headers=self._headers(json_content=False),
                    data={"name": name},
                    files={"image": (path.name, fh, mime)},
                    timeout=max(self.timeout, 120.0),
                )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        if resp.status_code == 401 and not _retried and self._maybe_refresh():
            return self.upload_vector_character(image_path=image_path, name=name, _retried=True)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}
        if resp.status_code >= 400:
            self._raise_http(resp, payload=payload)
        return payload

    def delete_vector_character(self, character_id: str) -> Any:
        """Delete one of your uploaded characters (refused while a template uses it)."""
        return self.request("DELETE", f"/api/v1/vector-characters/{character_id}")

    def list_avatars(
        self,
        *,
        section: str | None = None,
        search: str | None = None,
        gender: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Any:
        """Avatars for Avatar Video (ids for avatar_profile_id)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        for key, value in (("section", section), ("search", search), ("gender", gender)):
            if value:
                params[key] = value
        return self.request("GET", "/api/v1/avatars", params=params)
