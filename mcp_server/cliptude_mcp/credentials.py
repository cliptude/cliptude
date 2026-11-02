"""Local CLI session: ~/.cliptude/credentials.json."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def credentials_path() -> Path:
    override = os.environ.get("CLIPTUDE_CREDENTIALS")
    if override:
        return Path(override)
    return Path.home() / ".cliptude" / "credentials.json"


def load_credentials() -> dict[str, Any] | None:
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return data


def save_credentials(data: dict[str, Any]) -> Path:
    """Write the login atomically, owner-only (0600) from the moment the file exists (folder 0700)."""
    path = credentials_path()
    folder = path.parent
    if not folder.exists():
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(folder, 0o700)  # mkdir's mode is masked by the umask
        except OSError:
            pass
    payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    tmp = folder / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)  # in case the umask kept bits back; never wider than 0600
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def clear_credentials() -> None:
    path = credentials_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def stored_access_token() -> str:
    data = load_credentials()
    if not data:
        return ""
    return str(data.get("access_token") or "")
