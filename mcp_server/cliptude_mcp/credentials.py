"""Local CLI session: ~/.cliptude/credentials.json."""

from __future__ import annotations

import json
import os
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
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
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
