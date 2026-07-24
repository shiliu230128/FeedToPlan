"""
source_discovery.py
===================
Automatic source discovery and classification.

When the user runs `sync-sources` (or on first `plan` run with no sources),
this module:

  1. Pulls the full Bilibili following list via cookie.
  2. Classifies each UP into one or more topic buckets using keyword matching
     against their name + sign (bio).
  3. Merges into the appropriate sources JSON file without losing manual edits.

Future extension points:
  - YouTube subscriptions (needs YT API key + channel listing)
  - Manual channel import from a URL or text list

Classification is intentionally simple: keyword lists per bucket. This avoids
an AI call on every sync and keeps it fast and offline-capable.

Buckets live in:
    config/source_buckets.json   (user-editable, auto-created if missing)

Each bucket:
    {
      "id": "music_healing",
      "label": "音乐/疗愈",
      "sources_file": "config/sources_music.json",
      "keywords": ["疗愈", "冥想", "healing", ...],
      "name_keywords": ["音乐", "music", ...]
    }

A source is classified into a bucket if ANY keyword appears in
  lower(name) + lower(sign/bio).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Source, dedupe_keep_order


# ---------------------------------------------------------------------------
# Default bucket definitions (written on first run if file missing)
# ---------------------------------------------------------------------------

DEFAULT_BUCKETS: List[Dict[str, Any]] = [
    {
        "id": "yoga",
        "label": "瑜伽/健身",
        "sources_file": "config/sources.json",
        "keywords": ["瑜伽", "yoga", "跟练", "普拉提", "pilates", "健身", "拉伸",
                     "冥想", "meditation", "kiyoga", "yogini"],
        "name_keywords": ["瑜伽", "yoga", "pilates", "健身"],
    },
    {
        "id": "music_healing",
        "label": "音乐/疗愈/冥想",
        "sources_file": "config/sources_music.json",
        "keywords": ["疗愈", "healing", "颂钵", "音疗", "528", "432", "冥想",
                     "meditation", "轻音乐", "ambient", "lofi", "白噪音",
                     "bgm", "playlist", "歌单", "音乐", "music", "reiki",
                     "灵气", "频率", "助眠", "sleep"],
        "name_keywords": ["音乐", "music", "疗愈", "healing", "冥想",
                          "meditation", "颂钵", "lofi", "chillhop",
                          "电台", "radio", "声音", "sound"],
    },
]


# ---------------------------------------------------------------------------
# Bucket config management
# ---------------------------------------------------------------------------

def load_buckets(config_dir: Path) -> List[Dict[str, Any]]:
    path = config_dir / "source_buckets.json"
    if not path.exists():
        path.write_text(
            json.dumps(DEFAULT_BUCKETS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _score_source(source: Source, bucket: Dict[str, Any]) -> int:
    """Return a non-negative score; higher = better fit."""
    name_lower = source.name.lower()
    text = name_lower + " " + source.notes.lower()
    score = 0
    for kw in bucket.get("name_keywords") or []:
        if kw.lower() in name_lower:
            score += 3   # name match is stronger signal
    for kw in bucket.get("keywords") or []:
        if kw.lower() in text:
            score += 1
    return score


def classify_sources(
    sources: List[Source],
    buckets: List[Dict[str, Any]],
    min_score: int = 1,
) -> Dict[str, List[Source]]:
    """
    Return {bucket_id: [Source, ...]} for every bucket with at least one match.
    A source can appear in multiple buckets.
    """
    result: Dict[str, List[Source]] = {b["id"]: [] for b in buckets}
    for source in sources:
        for bucket in buckets:
            if _score_source(source, bucket) >= min_score:
                result[bucket["id"]].append(source)
    return result


# ---------------------------------------------------------------------------
# Merge: existing file + newly discovered sources (no overwrites of manual edits)
# ---------------------------------------------------------------------------

def _load_sources_file(path: Path) -> List[Source]:
    from .storage import load_sources
    return load_sources(path) if path.exists() else []


def _save_sources_file(path: Path, sources: List[Source]) -> None:
    from .storage import save_sources
    path.parent.mkdir(parents=True, exist_ok=True)
    save_sources(path, sources)


def merge_into_sources_file(
    path: Path,
    new_sources: List[Source],
    tag: str = "",
) -> tuple[int, int]:
    """
    Merge *new_sources* into the JSON file at *path*.
    - Existing entries are NOT overwritten (their tags/notes/enabled are preserved).
    - Truly new entries are appended with `notes="auto-discovered"` and optional tag.
    Returns (added, total).
    """
    existing = _load_sources_file(path)
    existing_by_mid = {s.mid: s for s in existing if s.mid}
    added = 0
    for src in new_sources:
        if src.mid and src.mid in existing_by_mid:
            continue   # already known, keep existing entry unchanged
        tags = dedupe_keep_order([*src.tags, tag] if tag else src.tags)
        from dataclasses import replace
        _save = replace(src, tags=tags, notes=src.notes or "auto-discovered")
        existing.append(_save)
        if src.mid:
            existing_by_mid[src.mid] = _save
        added += 1
    _save_sources_file(path, existing)
    return added, len(existing)


# ---------------------------------------------------------------------------
# Main entry point: sync Bilibili followings → buckets
# ---------------------------------------------------------------------------

def sync_bili_followings(
    crawler,           # BiliCrawler instance (already has cookie)
    config_dir: Path,
    project_root: Path,
    max_count: int = 500,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    1. Get authenticated user MID.
    2. Fetch all followings.
    3. Classify into buckets.
    4. Merge each bucket into its sources file.
    Returns {bucket_id: new_sources_added}.
    """
    mid = crawler.get_self_mid()
    if not mid:
        raise RuntimeError("Could not determine logged-in user MID. Is the cookie valid?")

    if verbose:
        print(f"Fetching followings for mid={mid}...")
    followings = crawler.fetch_followings(mid, max_count=max_count)
    if verbose:
        print(f"  Found {len(followings)} followings")

    buckets = load_buckets(config_dir)
    classified = classify_sources(followings, buckets)

    stats: Dict[str, int] = {}
    for bucket in buckets:
        bid = bucket["id"]
        sources_path = project_root / bucket["sources_file"]
        matched = classified.get(bid) or []
        added, total = merge_into_sources_file(sources_path, matched, tag=bid)
        stats[bid] = added
        if verbose:
            print(f"  [{bucket['label']}] {len(matched)} matched → {added} new → {total} total in {sources_path.name}")

    return stats


def sync_yt_subscriptions(
    yt_crawler,        # YouTubeCrawler instance
    config_dir: Path,
    project_root: Path,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    Placeholder for YouTube subscription import.
    YouTube Data API v3 does not expose subscription list without OAuth 2.0
    (API key alone is insufficient). This will be implemented when OAuth flow
    is added. For now, YouTube channels must be added manually via:
        bili-arrangement3 add-source "https://www.youtube.com/channel/UCxxx"
    """
    if verbose:
        print("YouTube subscription sync requires OAuth 2.0 (not yet implemented).")
        print("Add YouTube channels manually with: add-source <channel_url>")
    return {}
