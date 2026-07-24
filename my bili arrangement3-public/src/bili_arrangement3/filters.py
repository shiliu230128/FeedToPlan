"""
filters.py
==========
Content filtering for arrangement3.

Arrangement 3 filtering design:
  - YOGA_FOLLOW_ALONG_* term lists removed from this file.
  - detect_follow_along() is now profile-driven: it accepts a TopicProfile
    and applies its strong/support/hard_negative/soft_negative term lists.
  - requires_follow_along_filter() still exists but is now topic-agnostic:
    it returns True whenever the profile has any filter terms defined.
  - Bilibili-specific restricted-access detection (metadata flags) is kept
    in this file; it is a no-op for YouTube videos (no metadata dict passed).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Sequence, Tuple

from .models import Video, dedupe_keep_order

if TYPE_CHECKING:
    from .topic_profile import TopicProfile


# ---------------------------------------------------------------------------
# Bilibili restricted-access detection
# ---------------------------------------------------------------------------

RESTRICTED_TEXT_TERMS = [
    "充电专属", "充电专享", "充电会员", "包月充电", "解锁更多专属视频",
    "会员专享", "大会员专享", "付费专享", "付费视频", "收费专享",
    "仅粉丝可见", "upower", "试看",
]

RESTRICTED_FLAG_KEYS = {
    "arc_pay", "ep_pay", "is_charge_video", "is_chargeable", "is_only_pay",
    "is_paid", "is_premium_only", "is_upower_exclusive", "is_upower_preview",
    "need_pay", "only_pay", "paid", "pay", "pay_required", "premium_only",
    "upower_exclusive", "upower_preview", "vip_only",
}

COMMERCIAL_TEXT_TERMS = [
    "广告", "推广", "赞助", "合作", "带货", "课程", "付费", "报名",
    "私教", "一对一", "会员课", "旗舰店", "训练营", "推荐链接", "购买链接", "店铺",
]

COMMERCIAL_OWNER_TERMS = [
    "工作室", "培训", "学院", "机构", "品牌", "旗舰",
]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def contains_any(text: str, terms: Sequence[str]) -> List[str]:
    normalized = normalize_text(text)
    return [term for term in terms if normalize_text(term) and normalize_text(term) in normalized]


# ---------------------------------------------------------------------------
# Restricted / commercial detection
# ---------------------------------------------------------------------------

def detect_restricted_access(metadata: Any = None, text_parts: Iterable[str] = ()) -> Tuple[bool, List[str]]:
    notes: List[str] = []
    for text in text_parts:
        notes.extend(_text_notes(text))
    notes.extend(_metadata_notes(metadata))
    return bool(notes), dedupe_keep_order(notes)


def _metadata_notes(value: Any, key_path: str = "") -> List[str]:
    notes: List[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{key_path}.{key_text}" if key_path else key_text
            if key_text.lower() in RESTRICTED_FLAG_KEYS and _truthy(child):
                notes.append(f"metadata:{child_path}")
            if isinstance(child, str) and _looks_like_text_hint(key_text):
                notes.extend(_text_notes(child, prefix=f"metadata:{child_path}"))
            notes.extend(_metadata_notes(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            notes.extend(_metadata_notes(child, f"{key_path}[{index}]"))
    return notes


def _text_notes(text: str, prefix: str = "text") -> List[str]:
    normalized = normalize_text(text)
    return [f"{prefix}:{term}" for term in RESTRICTED_TEXT_TERMS if normalize_text(term) in normalized]


def _looks_like_text_hint(key: str) -> bool:
    key = key.lower()
    return any(part in key for part in ["badge", "desc", "description", "label", "message", "status", "tag", "text", "title"])


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null", "no"}
    return bool(value)


def detect_commercial_content(video: Video) -> Tuple[bool, List[str]]:
    notes = []
    notes.extend([f"title:{term}" for term in contains_any(video.title, COMMERCIAL_TEXT_TERMS)])
    notes.extend([f"desc:{term}" for term in contains_any(video.desc, COMMERCIAL_TEXT_TERMS)])
    notes.extend([f"tag:{term}" for term in contains_any(" ".join(video.tags), COMMERCIAL_TEXT_TERMS)])
    notes.extend([f"owner:{term}" for term in contains_any(video.owner_name, COMMERCIAL_OWNER_TERMS)])
    return bool(notes), dedupe_keep_order(notes)


def refresh_video_flags(video: Video) -> Video:
    restricted, access_notes = detect_restricted_access(
        None, [video.title, video.desc, " ".join(video.tags)],
    )
    flagged = replace(
        video,
        restricted_access=video.restricted_access or restricted,
        access_notes=dedupe_keep_order([*video.access_notes, *access_notes]),
    )
    commercial, commercial_notes = detect_commercial_content(flagged)
    return replace(
        flagged,
        commercial=flagged.commercial or commercial,
        commercial_notes=dedupe_keep_order([*flagged.commercial_notes, *commercial_notes]),
    )


# ---------------------------------------------------------------------------
# Topic-profile-driven follow-along filter (replaces yoga-specific logic)
# ---------------------------------------------------------------------------

def requires_follow_along_filter(profile: "Optional[TopicProfile]") -> bool:
    """
    Return True when the profile has enough terms to act as a content gate.
    Passing None or an empty profile skips filtering (generic/unknown topics).
    """
    if profile is None:
        return False
    return profile.needs_filter()


def detect_follow_along(video: Video, profile: "TopicProfile") -> Tuple[bool, List[str]]:
    """
    Decide whether *video* is a practice/follow-along video for the given topic.

    Decision logic (same structure as the old yoga filter, now term-list agnostic):
      1. Hard negative in title+desc → reject immediately.
      2. Soft negative in title and no strong signal in title → reject.
      3. Strong term anywhere in title+desc → accept.
      4. ≥2 support terms in full text and at least one topic keyword → accept.
      5. Default → reject.
    """
    title = video.title
    desc = video.desc
    tags_text = " ".join(video.tags)
    title_desc = f"{title} {desc}"
    full_text = f"{title_desc} {tags_text}"

    hard_negative_hits = contains_any(title_desc, profile.hard_negative)
    title_soft_hits = contains_any(title, profile.soft_negative)
    title_strong_hits = contains_any(title, profile.strong_terms)
    body_strong_hits = contains_any(title_desc, profile.strong_terms)
    support_hits = contains_any(full_text, profile.support_terms)

    notes: List[str] = []
    notes.extend([f"hard_negative:{t}" for t in hard_negative_hits])
    notes.extend([f"title_soft:{t}" for t in title_soft_hits])
    notes.extend([f"title_strong:{t}" for t in title_strong_hits])
    notes.extend([f"body_strong:{t}" for t in body_strong_hits])
    notes.extend([f"support:{t}" for t in support_hits])

    if hard_negative_hits:
        return False, dedupe_keep_order(notes)
    if title_soft_hits and not title_strong_hits:
        return False, dedupe_keep_order(notes)
    if body_strong_hits:
        return True, dedupe_keep_order(notes)
    topic_keyword_hits = contains_any(full_text, profile.support_terms[:3])
    if len(support_hits) >= 2 and topic_keyword_hits:
        return True, dedupe_keep_order(notes)
    return False, dedupe_keep_order(notes or ["missing_signal"])


# ---------------------------------------------------------------------------
# Dimension-based smart filtering
# ---------------------------------------------------------------------------

def apply_filter_dimensions(profile: "TopicProfile", notes: str) -> "TopicProfile":
    """
    Augment a topic profile's filter terms based on dimension rules + smart keyword extraction.

    Two-layer approach:
      1. Predefined dimensions (profile.filter_dimensions): structured constraints
         like space/intensity/equipment. Matched values go to strong_terms,
         non-matched values in the same dimension go to hard_negative.
         For the "space" dimension, also sets profile.required_space_terms
         so the filter loop can enforce that videos explicitly declare
         their posture (站立/坐姿/椅子).
      2. Smart keyword extraction: parse free-text constraint phrases from notes
         like "不要X""避免X""偏好X" and inject terms dynamically.

    Returns the same profile object (mutated in place).
    """
    profile.required_space_terms = []
    if not notes:
        return profile

    _notes = notes.lower()

    # ── Layer 1: Predefined dimension matching ──────────────────────────
    for dim_name, dim_groups in (profile.filter_dimensions or {}).items():
        if not dim_groups:
            continue
        # Collect ALL matched values (e.g. user says "站立或坐凳子" → both matched)
        matched_values = []
        for group_label, group_terms in dim_groups.items():
            group_terms_list = [group_terms] if isinstance(group_terms, str) else group_terms
            if any(_text_contains(_notes, term) for term in group_terms_list):
                matched_values.append(group_label)
        if not matched_values:
            continue

        # Put ALL matched values' terms into strong_terms
        for mv in matched_values:
            mv_terms = dim_groups.get(mv, [])
            mv_terms_list = [mv_terms] if isinstance(mv_terms, str) else mv_terms
            profile.strong_terms = list(dict.fromkeys(profile.strong_terms + mv_terms_list))

        # Put only UNMATCHED values' terms into hard_negative
        for other_label, other_terms in dim_groups.items():
            if other_label in matched_values:
                continue
            other_terms_list = [other_terms] if isinstance(other_terms, str) else other_terms
            profile.hard_negative = list(dict.fromkeys(profile.hard_negative + other_terms_list))

        # Space dimension: require videos to explicitly declare matching posture
        if dim_name == "space":
            profile.required_space_terms = []
            for mv in matched_values:
                mv_terms = dim_groups.get(mv, [])
                profile.required_space_terms.extend(mv_terms if isinstance(mv_terms, list) else [mv_terms])
            profile.required_space_terms = list(dict.fromkeys(profile.required_space_terms))

    # ── Layer 2: Smart keyword extraction from notes ─────────────────────
    exclude_terms = _extract_constraint_keywords(notes)
    profile.hard_negative = list(dict.fromkeys(profile.hard_negative + exclude_terms))

    return profile


_NEG_PATTERNS = [
    r"不要\s*([^，。；,.!！\n]{1,20})",
    r"避免\s*([^，。；,.!！\n]{1,20})",
    r"不能\s*([^，。；,.!！\n]{1,20})",
    r"不适合\s*([^，。；,.!！\n]{1,20})",
    r"不做\s*([^，。；,.!！\n]{1,20})",
    r"没有\s*([^，。；,.!！\n]{1,20})",
    r"排除\s*([^，。；,.!！\n]{1,20})",
    r"跳过\s*([^，。；,.!！\n]{1,20})",
]


def _extract_constraint_keywords(notes: str) -> List[str]:
    """Extract negation/constraint keywords from free-text notes."""
    terms: List[str] = []
    for pattern in _NEG_PATTERNS:
        for match in re.finditer(pattern, notes):
            raw = match.group(1).strip()
            # Split conjunctions: "深蹲和跳跃" → ["深蹲","跳跃"]
            for part in re.split(r"[和、与,，/]", raw):
                part = part.strip()
                if part and len(part) >= 2:
                    terms.append(part)
    return terms


def _text_contains(text: str, term: str) -> bool:
    """Case-insensitive substring match for Chinese-friendly partial matching."""
    return term.lower() in text.lower()

def requires_follow_along_filter_legacy(topic: str = "", template: str = "") -> bool:
    """Legacy shim: returns True for yoga topics. Use requires_follow_along_filter() + profile for new code."""
    text = normalize_text(f"{topic} {template}")
    return any(term in text for term in ["瑜伽", "yoga", "跟练", "follow along"])


def detect_yoga_follow_along(video: Video) -> Tuple[bool, List[str]]:
    """Legacy shim: delegates to detect_follow_along() with the built-in yoga profile."""
    from .topic_profile import TopicProfile, _BUILTIN_YOGA
    profile = TopicProfile(_BUILTIN_YOGA)
    return detect_follow_along(video, profile)
