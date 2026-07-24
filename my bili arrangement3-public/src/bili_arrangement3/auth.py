from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Tuple


ENV_COOKIE = "BILI_COOKIE"

# Centralized credentials directory — persists across project copies/renames.
# Priority for cookie resolution:
#   1. BILI_COOKIE env var  (CI / container)
#   2. Project-local  .secrets/bilibili_cookie.txt  (or symlink to shared dir)
#   3. Shared user-level ~/.config/bili-secrets/bilibili_cookie.txt
_SHARED_DIR = Path.home() / ".config" / "bili-secrets"
SHARED_COOKIE_FILE = _SHARED_DIR / "bilibili_cookie.txt"


def _candidate_paths(project_cookie_file: Path) -> list[Path]:
    """Return cookie file candidates in priority order."""
    return [
        project_cookie_file,           # project/.secrets/bilibili_cookie.txt (may be a symlink)
        SHARED_COOKIE_FILE,            # ~/.config/bili-secrets/bilibili_cookie.txt
    ]


def normalize_cookie(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    return text


def resolve_cookie(cookie_file: Path) -> Tuple[str, str]:
    """
    Resolve cookie value and the source it came from.
    Resolution order:
      1. BILI_COOKIE env var
      2. Project .secrets file (may be a symlink to shared dir)
      3. Shared ~/.config/bili-secrets/bilibili_cookie.txt
    """
    env = normalize_cookie(os.environ.get(ENV_COOKIE, ""))
    if env:
        return env, ENV_COOKIE
    for path in _candidate_paths(cookie_file):
        try:
            if path.exists():
                value = normalize_cookie(path.read_text(encoding="utf-8"))
                if value:
                    return value, f"file:{path}"
        except OSError:
            continue
    return "", ""


def set_cookie_value(cookie_file: Path, raw_value: str) -> str:
    """
    Write cookie to BOTH the shared dir and the project-local file (if not already a symlink).
    This ensures future projects that only look locally will still find it.
    """
    value = normalize_cookie(raw_value)
    # Always write to shared dir
    _SHARED_DIR.mkdir(parents=True, exist_ok=True)
    SHARED_COOKIE_FILE.write_text(value, encoding="utf-8")
    SHARED_COOKIE_FILE.chmod(0o600)
    # Write to project file only if it's a real file (not a symlink already pointing to shared)
    if not cookie_file.is_symlink():
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(value, encoding="utf-8")
        cookie_file.chmod(0o600)
    return value


def set_cookie_interactive(cookie_file: Path) -> str:
    value = normalize_cookie(getpass.getpass("Paste Bilibili cookie (input hidden): "))
    return set_cookie_value(cookie_file, value)


def delete_cookie(cookie_file: Path) -> bool:
    deleted = False
    for path in _candidate_paths(cookie_file):
        if path.exists() and not path.is_symlink():
            path.unlink()
            deleted = True
    return deleted


def cookie_status(cookie_file: Path) -> tuple[bool, str]:
    cookie, source = resolve_cookie(cookie_file)
    return bool(cookie), source

