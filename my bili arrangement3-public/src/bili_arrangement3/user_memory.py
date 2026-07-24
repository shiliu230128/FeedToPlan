"""
user_memory.py
==============
Three-tier user memory system for bili-arrangement3.

Tiers:
- semantic  : stable user preferences, goals, constraints per domain
- episodic  : rolling log of recent sessions (last 10), each a brief summary
- procedural: inferred default parameters per domain, refreshed every 10 days

Storage: data/user_memory.json
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 2
EPISODIC_MAX = 10
PROCEDURAL_REFRESH_DAYS = 10

_EMPTY: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "last_updated": "",
    "semantic": {
        "domains": {},
        "global_constraints": [],
        "active_domain": "",
    },
    "episodic": [],
    "procedural": {},
    "update_policy": {
        "episodic_max_entries": EPISODIC_MAX,
        "procedural_update_interval_days": PROCEDURAL_REFRESH_DAYS,
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
    # migrate v1 → v2 if needed
    if data.get("schema_version", 1) < SCHEMA_VERSION:
        data = _migrate(data)
    _ensure_structure(data)
    return data


def save_memory(path: Path, memory: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    memory["last_updated"] = date.today().isoformat()
    path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Read helpers — called before building a PlanRequest
# ---------------------------------------------------------------------------

def get_domain_defaults(memory: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """
    Return a dict of suggested PlanRequest overrides for *topic*.
    Keys may include: duration_min, duration_max, notes, scope, sources, freshness.
    Caller should apply these only when the corresponding CLI arg is not explicitly set.
    """
    domain = _domain(memory, topic)
    procedural = memory.get("procedural", {}).get(topic, {})

    notes_parts: List[str] = []
    if domain.get("goal"):
        notes_parts.append(str(domain["goal"]))
    if domain.get("constraints"):
        notes_parts.extend(str(c) for c in domain["constraints"])

    result: Dict[str, Any] = {}
    if notes_parts:
        result["notes"] = "；".join(notes_parts)
    dr = domain.get("duration_range", [])
    if isinstance(dr, list) and len(dr) == 2:
        if dr[0]:
            result["duration_min"] = int(dr[0])
        if dr[1]:
            result["duration_max"] = int(dr[1])
    # procedural layer: inferred defaults (lower priority than semantic)
    for key in ("scope", "freshness"):
        if key in procedural and key not in result:
            result[key] = procedural[key]
    return result


def get_recent_context(memory: Dict[str, Any], topic: str, n: int = 3) -> str:
    """Return a short text summary of the last *n* episodic entries for *topic*."""
    entries = [
        e for e in memory.get("episodic", [])
        if e.get("topic") == topic
    ][-n:]
    if not entries:
        return ""
    lines = []
    for e in entries:
        date_str = e.get("date", "")
        note = e.get("user_note", "")
        if date_str or note:
            lines.append(f"- {date_str}: {note}" if note else f"- {date_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write helpers — called after a session completes or user provides new info
# ---------------------------------------------------------------------------

def update_semantic(
    memory: Dict[str, Any],
    topic: str,
    *,
    goal: str = "",
    constraints: Optional[List[str]] = None,
    duration_range: Optional[List[int]] = None,
    preferred_style: Optional[List[str]] = None,
    disliked: Optional[List[str]] = None,
) -> None:
    """Merge new semantic info into the domain entry for *topic*."""
    domain = _domain(memory, topic)
    if goal:
        domain["goal"] = goal
    if constraints is not None:
        domain["constraints"] = constraints
    if duration_range is not None:
        domain["duration_range"] = duration_range
    if preferred_style is not None:
        domain["preferred_style"] = preferred_style
    if disliked is not None:
        domain["disliked"] = disliked
    memory["semantic"]["domains"][topic] = domain
    memory["semantic"]["active_domain"] = topic


def append_episodic(
    memory: Dict[str, Any],
    topic: str,
    user_note: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one session summary to the episodic log and prune to max."""
    entry: Dict[str, Any] = {
        "date": date.today().isoformat(),
        "topic": topic,
        "user_note": user_note,
    }
    if params:
        entry["params"] = {k: v for k, v in params.items() if v}
    episodic: List[Dict[str, Any]] = memory.get("episodic", [])
    episodic.append(entry)
    max_entries = int(
        memory.get("update_policy", {}).get("episodic_max_entries", EPISODIC_MAX)
    )
    memory["episodic"] = episodic[-max_entries:]


def maybe_update_procedural(
    memory: Dict[str, Any],
    topic: str,
    params: Dict[str, Any],
) -> bool:
    """
    Update procedural defaults for *topic* if the refresh interval has elapsed.
    Returns True when an update was performed.
    """
    interval = int(
        memory.get("update_policy", {}).get(
            "procedural_update_interval_days", PROCEDURAL_REFRESH_DAYS
        )
    )
    last_updated_str = memory.get("last_updated", "")
    try:
        last_updated = date.fromisoformat(last_updated_str)
    except ValueError:
        last_updated = date.min

    if (date.today() - last_updated).days < interval:
        return False

    procedural = memory.setdefault("procedural", {})
    existing = procedural.get(topic, {})
    for key in ("scope", "freshness", "duration_min", "duration_max"):
        v = params.get(key)
        if v:
            existing[key] = v
    procedural[topic] = existing
    return True


# ---------------------------------------------------------------------------
# Extract semantic info from wizard notes string
# (lightweight — no LLM call, just stores the raw notes)
# ---------------------------------------------------------------------------

def ingest_wizard_notes(
    memory: Dict[str, Any],
    topic: str,
    notes: str,
    duration_min: int = 0,
    duration_max: int = 0,
) -> None:
    """
    Called after wizard completes. Stores whatever the user said in the
    semantic layer so it's available next session.
    """
    domain = _domain(memory, topic)
    if notes and notes != domain.get("_last_notes"):
        domain["_last_notes"] = notes
        # Append to constraints if it looks like a limitation
        constraint_keywords = ["膝盖", "腰", "颈", "限制", "不适合", "不要", "避免", "无法"]
        new_constraints = [
            part.strip()
            for part in notes.split("；")
            if any(kw in part for kw in constraint_keywords)
        ]
        if new_constraints:
            existing = list(domain.get("constraints", []))
            for c in new_constraints:
                if c not in existing:
                    existing.append(c)
            domain["constraints"] = existing
        # Store goal (first part before constraint)
        goal_parts = [
            part.strip()
            for part in notes.split("；")
            if not any(kw in part for kw in constraint_keywords)
        ]
        if goal_parts and not domain.get("goal"):
            domain["goal"] = goal_parts[0]
    dr = domain.get("duration_range", [0, 0])
    if duration_min:
        dr = [duration_min, dr[1] if len(dr) > 1 else 0]
    if duration_max:
        dr = [dr[0] if dr else 0, duration_max]
    if duration_min or duration_max:
        domain["duration_range"] = dr
    memory["semantic"]["domains"][topic] = domain
    memory["semantic"]["active_domain"] = topic


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _domain(memory: Dict[str, Any], topic: str) -> Dict[str, Any]:
    return dict(memory.get("semantic", {}).get("domains", {}).get(topic, {}))


def _ensure_structure(data: Dict[str, Any]) -> None:
    data.setdefault("semantic", {})
    data["semantic"].setdefault("domains", {})
    data["semantic"].setdefault("global_constraints", [])
    data["semantic"].setdefault("active_domain", "")
    data.setdefault("episodic", [])
    data.setdefault("procedural", {})
    data.setdefault("update_policy", {
        "episodic_max_entries": EPISODIC_MAX,
        "procedural_update_interval_days": PROCEDURAL_REFRESH_DAYS,
    })


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate v1 (flat user_profile) to v2 (three-tier)."""
    new = _deep_copy(_EMPTY)
    new["schema_version"] = SCHEMA_VERSION
    old_profile = data.get("user_profile", {})
    if old_profile:
        topic = old_profile.get("active_domain") or data.get("semantic", {}).get("active_domain") or "通用"
        domain: Dict[str, Any] = {}
        if old_profile.get("fitness_goal"):
            domain["goal"] = old_profile["fitness_goal"]
        if old_profile.get("physical_constraints"):
            domain["constraints"] = [old_profile["physical_constraints"]]
        if old_profile.get("preferred_duration_range"):
            domain["duration_range"] = old_profile["preferred_duration_range"]
        if old_profile.get("preferred_topics"):
            domain["preferred_style"] = old_profile["preferred_topics"]
        if old_profile.get("disliked_content"):
            domain["disliked"] = old_profile["disliked_content"]
        new["semantic"]["domains"][topic] = domain
        new["semantic"]["active_domain"] = topic
    # carry over episodic if it was already a list
    if isinstance(data.get("episodic"), list):
        new["episodic"] = data["episodic"][-EPISODIC_MAX:]
    return new


def _deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj))
