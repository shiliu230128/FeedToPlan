from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .filters import detect_follow_along, requires_follow_along_filter
from .models import DAY_NAMES, DraftItem, DraftPlan, PlanRequest, Slot, Video, dedupe_keep_order
from .topic_profile import load_profile


def select_slots(request: PlanRequest, data_root: Optional[Path] = None, ai_api_key: str = "") -> List[Slot]:
    """Return slots for this request from topic profile or explicit template override."""
    key = ai_api_key or None
    if request.template == "yoga":
        profile = load_profile("瑜伽", data_root, key)
        return profile.slots
    if request.template == "generic":
        profile = load_profile("__generic__", data_root, key)
        return profile.slots
    if data_root is None:
        from .paths import data_dir
        data_root = data_dir()
    profile = load_profile(request.topic, data_root, key)
    return profile.slots if profile.slots else load_profile("__generic__", data_root, key).slots


def build_candidate_stats(videos: Sequence[Video]) -> Dict[str, object]:
    source_counts = Counter(video.source_id or video.source_kind or "unknown" for video in videos)
    owner_counts = Counter(video.owner_name or "unknown" for video in videos)
    age_buckets = Counter(_age_bucket(video.age_days) for video in videos)
    platform_counts = Counter(video.platform or "bilibili" for video in videos)
    return {
        "total_candidates": len(videos),
        "source_counts": dict(source_counts),
        "owner_counts_top": owner_counts.most_common(8),
        "age_buckets": dict(age_buckets),
        "platform_counts": dict(platform_counts),
    }


def draft_weekly_plan(
    videos: Sequence[Video],
    request: PlanRequest,
    recent_fingerprints: Optional[set] = None,
    today: Optional[date] = None,
    data_root: Optional[Path] = None,
    ai_api_key: str = "",
) -> DraftPlan:
    today = today or date.today()
    recent_fingerprints = recent_fingerprints or set()
    if data_root is None:
        from .paths import data_dir
        data_root = data_dir()
    profile = load_profile(request.topic, data_root, ai_api_key or None)
    slots = select_slots(request, data_root, ai_api_key)
    candidates = [video for video in videos if _eligible(video, request, recent_fingerprints, profile)]
    items: List[DraftItem] = []
    used_keys: set = set()
    previous_owner = ""
    previous_source = ""
    for index, slot in enumerate(slots[: request.days]):
        ranked = _rank_candidates(candidates, request, slot,
            previous_owner=previous_owner, previous_source=previous_source,
            used_keys=used_keys, today=today)
        selected = ranked[0][0] if ranked else None
        alternatives = [video for video, _ in ranked[1:4]]
        if selected:
            used_keys.add(selected.fingerprint)
            previous_owner = selected.owner_name
            previous_source = selected.source_id or selected.source_kind
        items.append(DraftItem(
            day_index=index, day_name=DAY_NAMES[index % len(DAY_NAMES)],
            date_label=(today + timedelta(days=index)).isoformat(),
            slot_title=slot.title, intent=slot.intent, video=selected, alternatives=alternatives,
            reason=_build_reason(selected, slot, request, today) if selected else "当前缓存里没有足够合适的内容。",
        ))
    return DraftPlan(request=request, strategy=build_strategy(request, slots), items=items)


def build_strategy(request: PlanRequest, slots: Sequence[Slot]) -> str:
    topic = request.topic or "内容"
    freshness_text = {
        "latest": "更偏向最近发布的内容，但不会牺牲主题匹配和多样性。",
        "balanced": "新旧内容会一起看，重点避免一周里过于单调。",
        "classic": "时间权重会放轻，优先补充更稳的经典内容。",
    }.get(request.freshness, "按主题、时效和多样性做综合判断。")
    slot_names = "\u3001".join(slot.title for slot in slots[: request.days])
    return f"\u672c\u6b21\u4ee5\u201c{topic}\u201d\u4e3a\u4e2d\u5fc3\uff0c\u6309 {request.days} \u5929\u6392\u6210 {slot_names}\u3002{freshness_text}\u540c\u4e00\u521b\u4f5c\u8005\u5c3d\u91cf\u4e0d\u8fde\u7eed\u51fa\u73b0\u3002"


def _eligible(video, request, recent_fingerprints, profile):
    if video.fingerprint and video.fingerprint in recent_fingerprints:
        return False
    if request.exclude_restricted and video.restricted_access:
        return False
    if request.exclude_commercial and video.commercial:
        return False
    if request.duration_min and video.duration_minutes and video.duration_minutes < request.duration_min:
        return False
    if request.duration_max and video.duration_minutes and video.duration_minutes > request.duration_max:
        return False
    if profile and requires_follow_along_filter(profile):
        follow_along, _ = detect_follow_along(video, profile)
        if not follow_along:
            return False
    return True


def _rank_candidates(videos, request, slot, previous_owner, previous_source, used_keys, today):
    # Build followed-source MID set for boosting when scope includes non-followed content.
    # When scope is "following" only, every video IS from a followed source so the boost
    # has no differential effect — we skip it to avoid inflating all scores equally.
    followed_mids: set = set()
    scope = getattr(request, "scope", "mixed")
    if scope not in {"following", "links"}:
        # Mixed / topic / following-topic: boost videos from followed sources
        followed_mids = {
            mid.strip()
            for mid in (getattr(request, "_followed_mids", None) or [])
            if mid.strip()
        }

    scored = []
    for video in videos:
        key = video.fingerprint
        if key in used_keys:
            continue
        score = 0.0
        score += _topic_score(video, request.topic) * 4.0
        score += _keyword_score(video, slot.keywords) * 3.0
        score += _freshness_score(video, request.freshness, today) * 5.0
        score += _duration_score(video, slot.title) * 1.5
        # Followed-source boost: only meaningful when pool includes non-followed content
        if followed_mids and video.owner_mid and video.owner_mid in followed_mids:
            score += 2.0
        if previous_owner and video.owner_name == previous_owner:
            score -= 1.5
        if previous_source and (video.source_id or video.source_kind) == previous_source:
            score -= 1.0
        if len(video.tags) >= 4:
            score += 0.2
        scored.append((video, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _topic_score(video, topic):
    tokens = tokenize(topic)
    if not tokens:
        return 0.3
    text = video.searchable_text()
    hits = sum(1 for token in tokens if token in text)
    return min(1.0, hits / max(1, len(tokens)))


def _keyword_score(video, keywords):
    tokens = [token for keyword in keywords for token in tokenize(keyword)]
    if not tokens:
        return 0.2
    text = video.searchable_text()
    hits = sum(1 for token in tokens if token in text)
    return min(1.0, hits / max(2, len(tokens)))


def _freshness_score(video, mode, today):
    published = video.publish_date
    if not published:
        return 0.0
    age_days = max(0, (today - published).days)
    half_life = {"latest": 20, "balanced": 60, "classic": 180}.get(mode, 30)
    weight = {"latest": 1.8, "balanced": 1.2, "classic": 0.7}.get(mode, 1.0)
    return weight * math.exp(-age_days / half_life)


def _duration_score(video, slot_title):
    minutes = video.duration_minutes
    if minutes <= 0:
        return 0.3
    slot_title = slot_title.lower()
    if any(word in slot_title for word in ["\u5b8c\u6574", "\u6df1\u5ea6", "\u56de\u987e"]):
        return min(1.0, minutes / 45.0)
    if any(word in slot_title for word in ["\u6668\u95f4", "\u7761\u524d", "\u5feb\u901f"]):
        return max(0.2, 1.0 - abs(minutes - 15) / 30.0)
    return 0.7


def _build_reason(video, slot, request, today):
    parts = [slot.intent]
    if request.topic and request.topic in video.searchable_text():
        parts.append(f"\u6807\u9898\u3001\u7b80\u4ecb\u6216\u6807\u7b7e\u91cc\u80fd\u76f4\u63a5\u770b\u5230\u201c{request.topic}\u201d\u3002")
    if video.publish_date:
        parts.append(f"\u53d1\u5e03\u65f6\u95f4\u662f {video.publish_date.isoformat()}\uff0c\u66f4\u7b26\u5408\u201c{request.freshness}\u201d\u7684\u6743\u91cd\u3002")
    if video.owner_name:
        parts.append(f"\u6765\u81ea {video.owner_name}\uff0c\u80fd\u548c\u5176\u4ed6\u5929\u7684\u6765\u6e90\u62c9\u5f00\u4e00\u70b9\u3002")
    if video.duration_minutes:
        parts.append(f"\u65f6\u957f\u7ea6 {video.duration_minutes} \u5206\u949f\u3002")
    return "".join(parts)


def tokenize(text):
    normalized = normalize_text(text)
    if not normalized:
        return []
    ascii_tokens = re.findall(r"[0-9a-zA-Z]+", normalized)
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]+", normalized)
    tokens = []
    for chunk in chinese_chunks:
        tokens.append(chunk)
        if len(chunk) > 2:
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    tokens.extend(ascii_tokens)
    return dedupe_keep_order(tokens)


def normalize_text(text):
    return " ".join(str(text or "").lower().split())


def _age_bucket(age_days):
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 180:
        return "31-180d"
    return "180d+"
