"""HTTP client for Cliptude /api/v1. No Django imports."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from cliptude_mcp.credentials import stored_access_token

DEFAULT_SAAS_URL = "https://cliptude.com"
TALKING_HEAD_EXTS = {".mp4", ".mov", ".webm", ".avi"}
_request_token: ContextVar[str | None] = ContextVar("cliptude_request_token", default=None)
# The MCP tool being run: sent as X-Cliptude-Tool so Cliptude's API call log (videos/api_call_log.py) says which
# tool made each request. Set by server.name_tool_calls.
current_tool: ContextVar[str | None] = ContextVar("cliptude_current_tool", default=None)


def set_request_token(token: str | None):
    return _request_token.set(token)


def reset_request_token(token):
    _request_token.reset(token)


TARGETS = ("auto", "desktop", "cloud")
DESKTOP_NOT_RUNNING = (
    "The Cliptude desktop app is not running. Open Cliptude, sign in, then try again "
    "(or run `cliptude use cloud` to make videos on cliptude.com)."
)
DESKTOP_NO_KEY = "Sign in to the Cliptude desktop app once so AI assistants can use it, then try again."


def desktop_data_dirs() -> list[Path]:
    """Where the desktop app keeps desktop-runtime.json (same rules as desktop/paths.py)."""
    override = (os.environ.get("CLIPTUDE_DESKTOP_DATA_DIR") or "").strip()
    if override:
        return [Path(override).expanduser()]
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Cliptude"]
    if sys.platform.startswith("win"):
        return [Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Cliptude"]
    return [Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "cliptude"]


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def discover_desktop() -> dict | None:
    """{base_url, key} of a running desktop app, or None. The port changes every launch, so
    this is read on every client; the app answers /api/desktop/health/ when it is really up."""
    for folder in desktop_data_dirs():
        runtime = _read_json(folder / "desktop-runtime.json")
        port = runtime.get("django") or runtime.get("port") or runtime.get("django_port")
        if not port:
            continue
        base_url = f"http://127.0.0.1:{port}"
        try:
            ok = httpx.get(f"{base_url}/api/desktop/health/", timeout=2.0).status_code < 500
        except httpx.HTTPError:
            ok = False
        if ok:
            key = str(_read_json(folder / "local-api.json").get("key") or "")
            return {"base_url": base_url, "key": key}
    return None


def discover_desktop_base_url() -> str | None:
    found = discover_desktop()
    return found["base_url"] if found else None


def config_path() -> Path:
    return Path.home() / ".cliptude" / "config.json"


def chosen_target() -> str:
    """desktop, cloud or auto: CLIPTUDE_TARGET, else `cliptude use`, else auto."""
    value = (os.environ.get("CLIPTUDE_TARGET") or "").strip().lower()
    if value not in TARGETS:
        value = str(_read_json(config_path()).get("target") or "").strip().lower()
    return value if value in TARGETS else "auto"


def save_target(target: str) -> Path:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    path = config_path()
    data = _read_json(path)
    data["target"] = target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_connection(api_key: str | None = None) -> dict:
    """Where requests go and with which token: {base_url, token, runtime, error}.

    CLIPTUDE_BASE_URL wins (any token). Otherwise the desktop app when it is chosen, or when it
    is running and nothing points at the cloud; else cliptude.com. The desktop gets only its own
    local key and cliptude.com only its own login, so neither token is ever sent to the other."""
    explicit = (os.environ.get("CLIPTUDE_BASE_URL") or "").rstrip("/")
    if explicit:
        return {"base_url": explicit, "token": _resolve_token(api_key), "runtime": "custom", "error": ""}
    target = chosen_target()
    # A cloud API key in the environment (without CLIPTUDE_TARGET) has always meant cliptude.com.
    cloud_key_env = bool((os.environ.get("CLIPTUDE_API_KEY") or "").strip())
    if target == "desktop" or (target == "auto" and not cloud_key_env):
        desktop = discover_desktop()
        if desktop:
            token = (api_key or "").strip() or desktop["key"]
            return {"base_url": desktop["base_url"], "token": token, "runtime": "desktop",
                    "error": "" if token else DESKTOP_NO_KEY}
        if target == "desktop":
            return {"base_url": "", "token": "", "runtime": "desktop", "error": DESKTOP_NOT_RUNNING}
    return {"base_url": DEFAULT_SAAS_URL, "token": _resolve_token(api_key), "runtime": "cloud", "error": ""}


def resolve_base_url() -> str:
    return resolve_connection()["base_url"] or DEFAULT_SAAS_URL


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


LOCAL_FILES_REFUSED = (
    "{param} reads a file on the machine running this MCP server, which a remote (hosted) server does not "
    "allow. Upload the file instead: narration with start_voiceover_upload + finish_voiceover_upload and pass "
    "the voiceover_url it returns; pictures and clips for a plan job with start_media_upload + "
    "finish_media_upload; a character image as image_url (a public https link). Or run the local "
    "`cliptude-mcp` / `cliptude` CLI, which can read your files."
)


def _uuid_seg(value: Any, label: str = "job_id") -> str:
    """A UUID path segment in canonical form; anything else (slashes, ?, #, ..) is refused."""
    try:
        canonical = str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        raise CliptudeApiError(f"{label} must be a UUID such as 1b4e28ba-2fa1-41d2-883f-0016d3cca427.", status=400) from None
    return quote(canonical, safe="")


def _int_seg(value: Any, label: str = "project_id") -> str:
    """A positive integer path segment; anything else is refused."""
    text = str(value).strip() if not isinstance(value, bool) else ""
    if not re.fullmatch(r"[0-9]{1,18}", text):
        raise CliptudeApiError(f"{label} must be a whole number.", status=400)
    return quote(text, safe="")


_PRIVATE_URL_RE = re.compile(
    r"\b[a-z][a-z0-9+.-]*://"
    r"(?:localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|169\.254(?:\.\d{1,3}){2}|0\.0\.0\.0|\[[0-9a-f:]+\]"
    r"|[a-z0-9_-]+(?:\.(?:internal|local|lan|svc|cluster\.local))*)"
    r"(?::\d+)?(?![a-z0-9.-])[^\s'\"<>)]*",
    re.IGNORECASE,
)


def scrub_internal(value: Any, internal_urls: list[str] | tuple[str, ...] = ()) -> Any:
    """Remove internal API addresses (the server-side base URL, docker hosts, private IPs) from error text.

    Strings, lists and dicts are scrubbed recursively; public https links (billing_url ...) are kept."""
    if isinstance(value, dict):
        return {k: scrub_internal(v, internal_urls) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_internal(v, internal_urls) for v in value]
    if not isinstance(value, str):
        return value
    text = value
    for url in internal_urls:
        url = (url or "").strip().rstrip("/")
        if not url:
            continue
        text = text.replace(url, "<cliptude-api>")
        host = url.split("://", 1)[-1].split("/", 1)[0]
        if host:
            text = text.replace(host, "<cliptude-api>")
    return _PRIVATE_URL_RE.sub("<cliptude-api>", text)


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


def _login_issuer() -> str:
    """The server the stored browser login belongs to, or ""."""
    try:
        from cliptude_mcp.credentials import load_credentials

        return str((load_credentials() or {}).get("issuer") or "").rstrip("/")
    except Exception:
        return ""


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


def _review_job_fields(review_token: str | None, acknowledge: list[str] | None, review_only: bool | None,
                       *, as_strings: bool = False) -> dict[str, Any]:
    """The job review: Cliptude answers a job without a review_token with the review (HTTP 428)."""
    out: dict[str, Any] = {}
    if review_token:
        out["review_token"] = str(review_token)
    if acknowledge:
        out["acknowledge"] = ",".join(acknowledge) if as_strings else list(acknowledge)
    if review_only:
        out["review_only"] = "true" if as_strings else True
    return out


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
    disable_sound_effects: bool | None = None,
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
        "disable_sound_effects": disable_sound_effects,
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


# Completing an upload checks the file; with split_shots it also cuts it into clips and checks each one, which
# takes minutes for a trailer. Wait up to the server's own request limit (300 s) rather than the usual 30 s.
SPLIT_SHOTS_COMPLETE_TIMEOUT_S = 310.0
UPLOAD_COMPLETE_TIMEOUT_S = 120.0


def _complete_timeout(default: float, split_shots: bool) -> float:
    return max(default, SPLIT_SHOTS_COMPLETE_TIMEOUT_S if split_shots else UPLOAD_COMPLETE_TIMEOUT_S)


def _timed_out(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TimeoutException) or isinstance(getattr(exc, "__cause__", None), httpx.TimeoutException)


class CliptudeClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        client_kind: str | None = None,
    ):
        # "cli" or "mcp": sent as X-Cliptude-Client so Cliptude records where a job was started.
        self.client_kind = (client_kind or os.environ.get("CLIPTUDE_CLIENT_KIND") or "").strip().lower()
        self.timeout = timeout
        self.connection_error = ""
        # False on a hosted (HTTP) MCP server: a remote caller must never make it read or write local files.
        self.allow_local_files = True
        if base_url:
            self.base_url = base_url.rstrip("/")
            self.api_key = _resolve_token(api_key)
            self.runtime = "custom"
        else:
            conn = resolve_connection(api_key)
            self.base_url = conn["base_url"].rstrip("/")
            self.api_key = conn["token"]
            self.runtime = conn["runtime"]
            self.connection_error = conn["error"]
        # Only a cliptude.com browser login (~/.cliptude/credentials.json) can be refreshed: on the default
        # cloud target, or when CLIPTUDE_BASE_URL names the server that login came from (its access token
        # expires within the hour, and every call after that failed "Invalid or revoked API key").
        explicit_token = bool(
            (api_key or "").strip()
            or (_request_token.get() or "").strip()
            or (os.environ.get("CLIPTUDE_API_KEY") or "").strip()
        )
        self._session_file = not explicit_token and (
            self.runtime == "cloud" or (self.runtime == "custom" and _login_issuer() == self.base_url)
        )

    def _local_file(self, value: str | Path, param: str) -> Path:
        """A path on this machine, only when this client may touch local files (never on a hosted server)."""
        if not self.allow_local_files:
            raise CliptudeApiError(LOCAL_FILES_REFUSED.format(param=param), status=400)
        return Path(value)

    def _headers(self, *, json_content: bool = True) -> dict[str, str]:
        if self.connection_error:
            raise CliptudeApiError(self.connection_error)
        if not self.api_key:
            raise CliptudeApiError("Not authenticated. Run: cliptude auth login")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            **django_proxy_headers(self.base_url),
        }
        if self.client_kind:
            headers["X-Cliptude-Client"] = self.client_kind
        tool = current_tool.get()
        if tool:
            headers["X-Cliptude-Tool"] = tool
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

    def request(
        self, method: str, path: str, *, params=None, json_body=None, timeout: float | None = None, _retried: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=timeout or self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        if resp.status_code == 401 and not _retried and self._maybe_refresh():
            return self.request(method, path, params=params, json_body=json_body, timeout=timeout, _retried=True)
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
        return self.request("GET", f"/api/v1/templates/{_uuid_seg(template_id, 'template_id')}")

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
        disable_sound_effects: bool | None = None,
        excluded_animation_types: list[str] | None = None,
        excluded_transition_kinds: list[str] | None = None,
        render_only: bool | None = None,
        thumbnail: dict[str, str] | None = None,
        vector_character_id: str | None = None,
        vector_reveal_mode: str | None = None,
        pronunciations: list[dict[str, str]] | None = None,
        review_token: str | None = None,
        acknowledge: list[str] | None = None,
        review_only: bool | None = None,
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
            disable_sound_effects=disable_sound_effects,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
        )
        motion_fields_str = _motion_control_job_fields(
            disable_animations=disable_animations,
            disable_overlays=disable_overlays,
            disable_effects=disable_effects,
            disable_transitions=disable_transitions,
            disable_sound_effects=disable_sound_effects,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
            as_strings=True,
        )
        th_path = (str(talking_head_path).strip() if talking_head_path else "") or None
        vo_path = (str(voiceover_path).strip() if voiceover_path else "") or None
        for param, value in (("talking_head_path", th_path), ("voiceover_path", vo_path)):
            if value:
                self._local_file(value, param)
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
                disable_sound_effects=disable_sound_effects,
                excluded_animation_types=excluded_animation_types,
                excluded_transition_kinds=excluded_transition_kinds,
                thumbnail=thumbnail,
                review=_review_job_fields(review_token, acknowledge, review_only),
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
                **_review_job_fields(review_token, acknowledge, review_only, as_strings=True),
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
        body.update(_review_job_fields(review_token, acknowledge, review_only))
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
        path = self._local_file(file_path, file_field)
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
        disable_sound_effects: bool | None = None,
        excluded_animation_types: list[str] | None = None,
        excluded_transition_kinds: list[str] | None = None,
        thumbnail: dict[str, str] | None = None,
        review: dict[str, Any] | None = None,
    ) -> Any:
        review = review or {}
        review_strings = {k: (",".join(v) if isinstance(v, list) else ("true" if v is True else v))
                          for k, v in review.items()}
        motion_extra = _motion_control_job_fields(
            disable_animations=disable_animations,
            disable_overlays=disable_overlays,
            disable_effects=disable_effects,
            disable_transitions=disable_transitions,
            disable_sound_effects=disable_sound_effects,
            excluded_animation_types=excluded_animation_types,
            excluded_transition_kinds=excluded_transition_kinds,
            as_strings=True,
        )
        path = self._local_file(talking_head_path, "talking_head_path")
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
                **review_strings,
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
                **review_strings,
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
                disable_sound_effects=disable_sound_effects,
                excluded_animation_types=excluded_animation_types,
                excluded_transition_kinds=excluded_transition_kinds,
            )
        )
        body.update(review)
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
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}")

    def continue_job(
        self, job_id: str, *, thumbnail: dict[str, str] | None = None, review_token: str | None = None,
    ) -> Any:
        """Queue a completed script/voiceover job, or a plan job with a locked plan, as a full video.

        Without review_token Cliptude answers with the review (HTTP 428) and queues nothing."""
        body = {**_thumbnail_job_field(thumbnail), **_review_job_fields(review_token, None, None)}
        return self.request("POST", f"/api/v1/jobs/{_uuid_seg(job_id)}/continue", json_body=body or None)

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
        script: str | None = None,
        plan: bool = False,
        review_token: str | None = None,
        acknowledge: list[str] | None = None,
    ) -> Any:
        """Queue the 80/20 educational script for a product; returns {script_id, ...} immediately.

        A paid draft (plan, or no script) first answers with the review (HTTP 428): resend with review_token.

        script: narration you researched and wrote yourself; the job is script_ready at once.
        plan: you plan every visual; the job stops at plan_ready with the sentence timings."""
        body: dict[str, Any] = {"product_id": product_id}
        for key, value in (
            ("angle", angle), ("duration", duration), ("template_id", template_id), ("title", title),
            ("discount_code", discount_code), ("cta_url", cta_url), ("cta_text", cta_text),
            ("image_urls", image_urls), ("reviews", reviews), ("script", script),
        ):
            if value not in (None, "", []):
                body[key] = value
        if plan:
            body["plan"] = True
        body.update(_review_job_fields(review_token, acknowledge, None))
        return self.request("POST", "/api/v1/commerce/scripts", json_body=body)

    def render_product_video(
        self, job_id: str, *, aspect_ratio: str = "16:9", thumbnail: dict[str, str] | None = None,
        review_token: str | None = None,
    ) -> Any:
        """Turn a ready product script into the video on the same job id (returns immediately).

        thumbnail: {custom_prompt} or {reference_url} for this video's thumbnail (optional).
        Without review_token Cliptude answers with the review (HTTP 428) and builds nothing."""
        body = {"aspect_ratio": aspect_ratio, **_thumbnail_job_field(thumbnail),
                **_review_job_fields(review_token, None, None)}
        return self.request("POST", f"/api/v1/commerce/videos/{_uuid_seg(job_id)}/render", json_body=body)

    def get_product_video_status(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/commerce/videos/{_uuid_seg(job_id)}")

    # Agent-planned videos -------------------------------------------------------------------------

    def upload_plan_media(self, job_id: str, path: str | Path, *, split_shots: bool = False) -> Any:
        """Send a picture or clip you found to a plan job; returns {keep, file, media_type, size} for media.keep.

        split_shots: a long source (a trailer, a featurette) is also cut into clips on its cuts, each checked and
        kept like an upload: shots=[{keep, from_s, seconds, notes?} or {from_s, skipped}]."""
        path = self._local_file(path, "path")
        if not path.is_file():
            raise CliptudeApiError(f"File not found: {path}")
        base = f"/api/v1/jobs/{_uuid_seg(job_id)}/media"
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
            return self.request(
                "POST", f"{base}/complete", json_body={**body, **({"split_shots": True} if split_shots else {})},
                timeout=_complete_timeout(self.timeout, split_shots),
            )
        except Exception as exc:
            if _timed_out(exc):
                # The bytes are uploaded and Cliptude is still checking (and cutting) them: aborting now would
                # delete a file the server is about to keep.
                raise CliptudeApiError(
                    "Cliptude is still processing this upload; it took longer than the wait. Upload a shorter "
                    "source (split_shots handles about two minutes at a time) or try again."
                ) from exc
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
        return self._start_signed_upload(f"/api/v1/jobs/{_uuid_seg(job_id)}/media", filename=filename, size=size, content_type=content_type)

    def finish_media_upload(self, job_id: str, *, key: str, upload_id: str, split_shots: bool = False) -> Any:
        body = {"key": key, "upload_id": upload_id, **({"split_shots": True} if split_shots else {})}
        return self.request("POST", f"/api/v1/jobs/{_uuid_seg(job_id)}/media/complete", json_body=body,
                            timeout=_complete_timeout(self.timeout, split_shots))

    def abort_media_upload(self, job_id: str, *, key: str, upload_id: str) -> Any:
        return self.request("POST", f"/api/v1/jobs/{_uuid_seg(job_id)}/media/abort", json_body={"key": key, "upload_id": upload_id})

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
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/narration")

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
        path = f"/api/v1/jobs/{_uuid_seg(job_id)}/plan" + ("?dry_run=1" if dry_run else "")
        try:
            return self.request("PUT", path, json_body=plan)
        except CliptudeApiError as exc:
            if exc.status in (400, 409, 413) and isinstance(exc.payload, dict) and "errors" in exc.payload:
                return {**exc.payload, "http_status": exc.status}
            raise

    def get_plan(self, job_id: str) -> Any:
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/plan")

    def get_plan_report(self, job_id: str) -> Any:
        """What sourcing used for each media request of the locked plan, and every fallback."""
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/plan/report")

    def get_plan_review(self, job_id: str) -> Any:
        """The built design, beat by beat and graphic by graphic, with links to the media (review before render)."""
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/plan/review")

    def get_plan_candidates(self, job_id: str, *, beats: list[str] | None = None,
                            start_s: float | None = None, end_s: float | None = None) -> Any:
        """Every picture or clip sourcing checked for the chosen beats (passed or turned down), each with a keep id."""
        params: dict[str, Any] = {}
        if beats:
            params["beats"] = ",".join(str(b) for b in beats)
        if start_s is not None:
            params["start_s"] = start_s
        if end_s is not None:
            params["end_s"] = end_s
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/plan/candidates", params=params)

    def view_candidates(self, job_id: str, *, beats: list[str] | None = None,
                        start_s: float | None = None, end_s: float | None = None) -> Any:
        """The candidates of up to 6 beats as labelled contact sheets (sheets[].jpeg_base64)."""
        params: dict[str, Any] = {}
        if beats:
            params["beats"] = ",".join(str(b) for b in beats)
        if start_s is not None:
            params["start_s"] = start_s
        if end_s is not None:
            params["end_s"] = end_s
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/plan/candidates/sheets", params=params,
                            timeout=180)

    def download_voiceover_file(self, job_id: str, dest: Path) -> Path:
        dest = self._local_file(dest, "dest")
        url = f"{self.base_url}/api/v1/jobs/{_uuid_seg(job_id)}/voiceover"
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
        return self.request("POST", f"/api/v1/jobs/{_uuid_seg(job_id)}/cancel")

    # --- Timeline editor + Cliptude Agent -------------------------------------------------

    def open_editor_project(self, job_id: str) -> Any:
        """Open (or create) the editor project for a completed video job; includes a compact timeline."""
        return self.request("GET", f"/api/v1/jobs/{_uuid_seg(job_id)}/editor-project")

    def get_editor_project(self, project_id: int | str, *, design: bool = False) -> Any:
        params = {"design": "1"} if design else None
        return self.request("GET", f"/api/v1/editor-projects/{_int_seg(project_id)}", params=params)

    def view_scenes(self, project_id: int | str, *, start_s: float = 0.0, end_s: float | None = None) -> Any:
        """Contact sheets (base64 JPEG) of the timeline's pictures, clip frames and graphics, with a legend."""
        params: dict[str, Any] = {"start_s": start_s}
        if end_s is not None:
            params["end_s"] = end_s
        return self.request("GET", f"/api/v1/editor-projects/{_int_seg(project_id)}/scenes", params=params)

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
        review_token: str | None = None,
    ) -> Any:
        """Ask Cliptude Agent to edit the timeline. Uses the project's current revision unless given.

        Without review_token Cliptude answers with the review (HTTP 428) and starts nothing."""
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
        body.update(_review_job_fields(review_token, None, None))
        return self.request("POST", f"/api/v1/editor-projects/{_int_seg(project_id)}/agent/runs", json_body=body)

    def get_agent_run(self, project_id: int | str, run_id: str) -> Any:
        return self.request("GET", f"/api/v1/editor-projects/{_int_seg(project_id)}/agent/runs/{_uuid_seg(run_id, 'run_id')}")

    def cancel_agent_run(self, project_id: int | str, run_id: str) -> Any:
        return self.request("POST", f"/api/v1/editor-projects/{_int_seg(project_id)}/agent/runs/{_uuid_seg(run_id, 'run_id')}/cancel")

    def render_project(
        self, project_id: int | str, *, accept_quality_findings: bool = False, review_token: str | None = None,
        skip_frame_review: bool = False,
    ) -> Any:
        """Render the edited timeline to a new MP4: free the first time, then 10% of the original generation.

        A paid export first answers with the review (HTTP 428): resend with review_token. An agent-planned job
        answers 409 frames_not_viewed until view_scenes covered the timeline (skip_frame_review overrides)."""
        body: dict[str, Any] = {"accept_quality_findings": True} if accept_quality_findings else {}
        if skip_frame_review:
            body["skip_frame_review"] = True
        body.update(_review_job_fields(review_token, None, None))
        return self.request("POST", f"/api/v1/editor-projects/{_int_seg(project_id)}/render", json_body=body or None)

    def get_render(self, render_id: int | str) -> Any:
        return self.request("GET", f"/api/v1/editor-renders/{_int_seg(render_id, 'render_id')}")

    def get_account(self) -> Any:
        return self.request("GET", "/api/v1/account")

    # Browser pickers (clients without MCP Apps widgets) ---------------------------------------------

    def create_pick(self, kind: str, payload_source: dict[str, Any]) -> Any:
        """A cliptude.com/pick link showing a widget in the browser; Cliptude builds what it shows."""
        return self.request("POST", "/api/v1/picks", json_body={"kind": kind, "payload_source": payload_source})

    def get_pick(self, pick_id: str) -> Any:
        return self.request("GET", f"/api/v1/picks/{_uuid_seg(pick_id, 'pick_id')}")

    def save_choice(self, message: str, choice: dict | None = None) -> Any:
        """Keep the user's pick from a widget on Cliptude (the agent reads it with get_choice)."""
        return self.request("POST", "/api/v1/choices", json_body={"message": message, "choice": choice or {}})

    def get_choice(self) -> Any:
        """The user's latest pick from a Cliptude widget, or choice null."""
        return self.request("GET", "/api/v1/choices")

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
        image_file: tuple[str, bytes, str] | None = None,
        name: str = "",
        _retried: bool = False,
    ) -> Any:
        """Add a Vector Explainer character from a local image, a public image URL, or (file name, bytes, mime)
        sent by a widget; returns its id."""
        if sum(bool(x) for x in (image_path, (image_url or "").strip(), image_file)) != 1:
            raise CliptudeApiError("Give image_path, image_url or the image itself (one of them).")
        if image_file:
            return self._post_vector_character_file(image_file, name, _retried)
        if image_url:
            return self.request(
                "POST", "/api/v1/vector-characters", json_body={"image_url": image_url.strip(), "name": name}
            )
        path = self._local_file(image_path, "image_path")
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

    def _post_vector_character_file(self, image_file: tuple[str, bytes, str], name: str, _retried: bool) -> Any:
        file_name, raw, mime = image_file
        try:
            resp = httpx.post(
                f"{self.base_url}/api/v1/vector-characters",
                headers=self._headers(json_content=False),
                data={"name": name},
                files={"image": (file_name, raw, mime)},
                timeout=max(self.timeout, 120.0),
            )
        except httpx.HTTPError as exc:
            raise CliptudeApiError(str(exc)) from exc
        if resp.status_code == 401 and not _retried and self._maybe_refresh():
            return self._post_vector_character_file(image_file, name, True)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}
        if resp.status_code >= 400:
            self._raise_http(resp, payload=payload)
        return payload

    def delete_vector_character(self, character_id: str) -> Any:
        """Delete one of your uploaded characters (refused while a template uses it)."""
        return self.request("DELETE", f"/api/v1/vector-characters/{_uuid_seg(character_id, 'character_id')}")

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
