"""
user_memory.py
==============
One rolling log of recent sessions, scoped per topic.

Design (schema v3):
- A single store: `episodic`. Each entry is one session — date, topic, the
  user's own note, and the params that run used.
- Defaults for a new run are derived from the most recent entries of the SAME
  topic, inside `context_window_days`. Two things follow from that: a yoga
  constraint can never leak into a music arrangement, and a stale note ages
  out on its own instead of needing manual deletion.
- The entry cap is per topic, so one busy topic cannot evict another's history.

Storage: data/user_memory.json
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 3
EPISODIC_MAX_PER_TOPIC = 10
CONTEXT_WINDOW_DAYS = 45
CONTEXT_ENTRIES = 3

_EMPTY: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "last_updated": "",
    "episodic": [],
    "update_policy": {
        "episodic_max_entries_per_topic": EPISODIC_MAX_PER_TOPIC,
        "context_window_days": CONTEXT_WINDOW_DAYS,
        "context_entries": CONTEXT_ENTRIES,
    },
}


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_memory(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _deep_copy(_EMPTY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _deep_copy(_EMPTY)
    if int(data.get("schema_version", 1) or 1) < SCHEMA_VERSION:
        data = _migrate(data)
    _ensure_structure(data)
    return data


def save_memory(path: Path, memory: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    memory["last_updated"] = date.today().isoformat()
    memory["episodic"] = _prune_per_topic(
        memory.get("episodic", []),
        _policy(memory, "episodic_max_entries_per_topic", EPISODIC_MAX_PER_TOPIC),
    )
    path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Read helpers — called before building a PlanRequest
# ---------------------------------------------------------------------------

def recent_entries(
    memory: Dict[str, Any],
    topic: str,
    *,
    within_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Entries for *topic*, newest first, dropping anything past the window."""
    window = within_days if within_days is not None else _policy(
        memory, "context_window_days", CONTEXT_WINDOW_DAYS
    )
    count = limit if limit is not None else _policy(
        memory, "context_entries", CONTEXT_ENTRIES
    )
    today = date.today()
    picked: List[tuple] = []
    for index, entry in enumerate(memory.get("episodic", [])):
        if entry.get("topic") != topic:
            continue
        entry_date = _parse_date(entry.get("date"))
        if entry_date is None:
            continue
        if window > 0 and (today - entry_date).days > window:
            continue
        picked.append((entry_date, index, entry))
    picked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    entries = [entry for _, _, entry in picked]
    return entries[:count] if count > 0 else entries


def get_topic_defaults(memory: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """
    Suggested PlanRequest overrides for *topic*, derived from its own recent
    sessions. Newest non-empty value wins; nothing outside *topic* is consulted.
    Keys may include: notes, scope, freshness, duration_min, duration_max.
    """
    result: Dict[str, Any] = {}
    for entry in recent_entries(memory, topic):
        note = str(entry.get("user_note") or "").strip()
        if note and "notes" not in result:
            result["notes"] = note
        params = entry.get("params") or {}
        for key in ("scope", "freshness", "duration_min", "duration_max"):
            value = params.get(key)
            if value and key not in result:
                result[key] = value
    for key in ("duration_min", "duration_max"):
        if key in result:
            try:
                result[key] = int(result[key])
            except (TypeError, ValueError):
                result.pop(key)
    return result


def get_recent_context(memory: Dict[str, Any], topic: str, n: int = CONTEXT_ENTRIES) -> str:
    """Short text digest of this topic's recent sessions, oldest line first."""
    lines = []
    for entry in reversed(recent_entries(memory, topic, limit=n)):
        date_str = entry.get("date", "")
        note = entry.get("user_note", "")
        if date_str or note:
            lines.append(f"- {date_str}: {note}" if note else f"- {date_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write helper — called after a session completes
# ---------------------------------------------------------------------------

def append_episodic(
    memory: Dict[str, Any],
    topic: str,
    user_note: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one session summary; keep at most N entries per topic."""
    entry: Dict[str, Any] = {
        "date": date.today().isoformat(),
        "topic": topic,
        "user_note": user_note,
    }
    if params:
        entry["params"] = {k: v for k, v in params.items() if v}
    episodic = list(memory.get("episodic", []))
    episodic.append(entry)
    memory["episodic"] = _prune_per_topic(
        episodic, _policy(memory, "episodic_max_entries_per_topic", EPISODIC_MAX_PER_TOPIC)
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _policy(memory: Dict[str, Any], key: str, fallback: int) -> int:
    try:
        return int(memory.get("update_policy", {}).get(key, fallback))
    except (AttributeError, TypeError, ValueError):
        return fallback


def _prune_per_topic(episodic: Any, max_per_topic: int) -> List[Dict[str, Any]]:
    """Keep the newest *max_per_topic* entries of each topic, order preserved."""
    entries = [e for e in list(episodic or []) if isinstance(e, dict)]
    if max_per_topic <= 0:
        return entries
    seen: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for entry in reversed(entries):
        topic = str(entry.get("topic") or "")
        if seen.get(topic, 0) >= max_per_topic:
            continue
        seen[topic] = seen.get(topic, 0) + 1
        kept.append(entry)
    kept.reverse()
    return kept


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _ensure_structure(data: Dict[str, Any]) -> None:
    data["schema_version"] = SCHEMA_VERSION
    if not isinstance(data.get("episodic"), list):
        data["episodic"] = []
    policy = data.get("update_policy")
    if not isinstance(policy, dict):
        policy = {}
        data["update_policy"] = policy
    policy.setdefault("episodic_max_entries_per_topic", EPISODIC_MAX_PER_TOPIC)
    policy.setdefault("context_window_days", CONTEXT_WINDOW_DAYS)
    policy.setdefault("context_entries", CONTEXT_ENTRIES)
    policy.pop("episodic_max_entries", None)
    policy.pop("procedural_update_interval_days", None)
    data.pop("semantic", None)
    data.pop("procedural", None)


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    v1/v2 → v3. The semantic and procedural tiers are dropped: a stored goal
    could never be corrected, constraints could only be appended, and the
    procedural refresh never fired because it compared against a timestamp that
    every save rewrote. What they held is already restated in the session notes,
    which age out on their own.
    """
    new = _deep_copy(_EMPTY)
    new["episodic"] = _prune_per_topic(data.get("episodic"), EPISODIC_MAX_PER_TOPIC)
    return new


def _deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj))
