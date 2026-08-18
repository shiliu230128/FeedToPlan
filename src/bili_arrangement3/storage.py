from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .models import Source, Video, dedupe_keep_order, merge_videos


# ---------------------------------------------------------------------------
# Videos cache TTL: entries not refreshed within this many days are dropped.
# ---------------------------------------------------------------------------
VIDEO_CACHE_TTL_DAYS = 60

# outputs/runs/ retention: keep this many most-recent run directories.
RUNS_KEEP_COUNT = 15


def ensure_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def ensure_layout(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "cache").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "runs").mkdir(parents=True, exist_ok=True)
    (root / ".secrets").mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_sources(path: Path) -> List[Source]:
    payload = load_json(path, {"sources": []})
    raw_sources = payload.get("sources") if isinstance(payload, dict) else []
    if not isinstance(raw_sources, list):
        return []
    return [Source.from_dict(item or {}) for item in raw_sources]


def save_sources(path: Path, sources: Sequence[Source]) -> None:
    save_json(path, {"sources": [source.to_dict() for source in sources]})


def upsert_source(path: Path, source: Source) -> List[Source]:
    sources = load_sources(path)
    by_id = {item.id: item for item in sources}
    by_id[source.id] = source
    merged = list(by_id.values())
    save_sources(path, merged)
    return merged


def load_preferences(path: Path) -> Dict[str, Any]:
    payload = load_json(path, {})
    return payload if isinstance(payload, dict) else {}


def load_videos_jsonl(path: Path) -> List[Video]:
    if not path.exists():
        return []
    videos: List[Video] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            videos.append(Video.from_dict(json.loads(line)))
    return videos


def upsert_videos_jsonl(path: Path, videos: Iterable[Video], ttl_days: int = VIDEO_CACHE_TTL_DAYS) -> List[Video]:
    by_key: Dict[str, Video] = {}
    for video in load_videos_jsonl(path):
        key = video.fingerprint
        if key:
            by_key[key] = video
    for video in videos:
        key = video.fingerprint
        if not key:
            continue
        if key in by_key:
            by_key[key] = merge_videos(by_key[key], video)
        else:
            by_key[key] = video
    # TTL pruning: drop entries whose fetched_at is older than ttl_days
    cutoff = date.today() - timedelta(days=max(0, ttl_days))
    merged: List[Video] = []
    dropped = 0
    for video in by_key.values():
        fetched = _parse_date_safe(video.fetched_at)
        if fetched and fetched < cutoff:
            dropped += 1
            continue
        merged.append(video)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for video in merged:
            handle.write(json.dumps(video.to_dict(), ensure_ascii=False) + "\n")
    return merged


def remove_inaccessible_bvids(path: Path, bvids: set) -> int:
    """
    Drop videos whose bvids are in *bvids* from the JSONL cache.
    Returns the number of entries removed.
    """
    if not path.exists() or not bvids:
        return 0
    removed = 0
    kept: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if entry.get("bvid") in bvids:
                removed += 1
            else:
                kept.append(line)
    if removed:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def _parse_date_safe(value: str) -> "date | None":
    """Parse first 10 chars of an ISO date/datetime string. Returns None on failure."""
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def load_state(path: Path) -> Dict[str, Any]:
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("recently_used", [])
    payload.setdefault("last_run_dir", "")
    payload.setdefault("last_pack_path", "")
    payload.setdefault("last_prompt_path", "")
    payload.setdefault("last_draft_path", "")
    return payload


def save_state(path: Path, state: Dict[str, Any]) -> None:
    save_json(path, state)


def prune_recent_usage(state: Dict[str, Any], window_days: int) -> None:
    if window_days <= 0:
        state["recently_used"] = []
        return
    cutoff = date.today() - timedelta(days=window_days)
    kept = []
    for item in state.get("recently_used", []):
        used_at = str(item.get("used_at") or "")
        try:
            used_date = date.fromisoformat(used_at[:10])
        except ValueError:
            continue
        if used_date >= cutoff:
            kept.append(item)
    state["recently_used"] = kept


def recent_bvids(state: Dict[str, Any], window_days: int) -> set[str]:
    """
    Keys of recently used videos. Both spellings go in: the platform-namespaced
    fingerprint ("bilibili:BV1x") and the bare bvid ("BV1x"), so a caller
    holding either one gets a hit.
    """
    prune_recent_usage(state, window_days)
    result: set[str] = set()
    for item in state.get("recently_used", []):
        fp = str(item.get("fingerprint") or "").strip()
        if fp:
            result.add(fp)
        bvid = str(item.get("bvid") or "").strip()
        if bvid:
            result.add(bvid)
    return result


def record_recent_usage(
    state: Dict[str, Any],
    videos: Sequence[Video],
    run_id: str,
    topic: str,
) -> None:
    entries = list(state.get("recently_used", []))
    for video in videos:
        pid = video.platform or "bilibili"
        vid = video.platform_id or video.bvid
        entries.append(
            {
                "bvid": video.bvid,
                "platform_id": vid,
                "platform": pid,
                "fingerprint": video.fingerprint,
                "title": video.title,
                "used_at": date.today().isoformat(),
                "run_id": run_id,
                "topic": topic,
            }
        )
    state["recently_used"] = entries


def merge_unique_strings(*groups: Sequence[str]) -> List[str]:
    merged: List[str] = []
    for group in groups:
        merged.extend(group)
    return dedupe_keep_order(merged)

