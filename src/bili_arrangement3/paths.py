from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("BILI_ARRANGEMENT3_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def data_dir() -> Path:
    return project_root() / "data"


def cache_dir() -> Path:
    return data_dir() / "cache"


def source_sync_path() -> Path:
    """Bookkeeping for when each followed source was last crawled."""
    return cache_dir() / "source_sync.json"


def runs_dir() -> Path:
    return project_root() / "outputs" / "runs"


def secrets_dir() -> Path:
    return project_root() / ".secrets"

