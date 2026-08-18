from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence


DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_date_text(value: Any) -> Optional[date]:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc).date()
        normalized = text.replace("Z", "+00:00")
        if "T" in normalized or "+" in normalized:
            return datetime.fromisoformat(normalized).date()
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def parse_duration_text(text: Any) -> int:
    raw = str(text or "").strip()
    if not raw:
        return 0
    parts = [part for part in raw.split(":") if part]
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(values) == 3:
        return values[0] * 3600 + values[1] * 60 + values[2]
    if len(values) == 2:
        return values[0] * 60 + values[1]
    if len(values) == 1:
        return values[0]
    return 0


def slugify(text: str) -> str:
    chars = []
    for ch in text.lower().strip():
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            chars.append(ch)
        elif ch in {" ", "_", "-"}:
            chars.append("-")
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:64] or "item"


def dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def merge_video_lists(base: Sequence[str], incoming: Sequence[str]) -> List[str]:
    return dedupe_keep_order([*base, *incoming])


@dataclass
class Source:
    id: str
    kind: str = "up"          # "up" | "channel" | "video"
    platform: str = "bilibili"  # "bilibili" | "youtube"
    name: str = ""
    url: str = ""
    mid: str = ""              # Bilibili UP MID
    bvid: str = ""             # Bilibili video BV ID
    channel_id: str = ""       # YouTube channel ID (UCxxx...)
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True
    added_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Source":
        _id = data.get("id") or data.get("name") or data.get("url")
        if not _id:
            _id = f"source-{abs(hash(str(data)))}"
        return cls(
            id=str(_id),
            kind=str(data.get("kind") or data.get("type") or "up"),
            platform=str(data.get("platform") or "bilibili"),
            name=str(data.get("name") or ""),
            url=str(data.get("url") or ""),
            mid=str(data.get("mid") or ""),
            bvid=str(data.get("bvid") or ""),
            channel_id=str(data.get("channel_id") or ""),
            tags=list(data.get("tags") or []),
            notes=str(data.get("notes") or ""),
            enabled=bool(data.get("enabled", True)),
            added_at=str(data.get("added_at") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "platform": self.platform,
            "name": self.name,
            "url": self.url,
            "mid": self.mid,
            "bvid": self.bvid,
            "channel_id": self.channel_id,
            "tags": self.tags,
            "notes": self.notes,
            "enabled": self.enabled,
            "added_at": self.added_at,
        }

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.mid:
            return f"UP {self.mid}"
        if self.channel_id:
            return f"YT {self.channel_id}"
        if self.bvid:
            return self.bvid
        return self.id


@dataclass
class Video:
    bvid: str
    title: str
    url: str
    platform: str = "bilibili"   # "bilibili" | "youtube"
    platform_id: str = ""        # bvid for bili, videoId for YT — unique per platform
    owner_name: str = ""
    owner_mid: str = ""
    pubdate: str = ""
    duration_seconds: int = 0
    view_count: int = 0          # total plays; 0 = unknown. Used for low-quality filtering.
    desc: str = ""
    tags: List[str] = field(default_factory=list)
    source_id: str = ""
    source_url: str = ""
    source_kind: str = ""
    fetched_at: str = ""
    restricted_access: bool = False
    access_notes: List[str] = field(default_factory=list)
    commercial: bool = False
    commercial_notes: List[str] = field(default_factory=list)
    matched_queries: List[str] = field(default_factory=list)
    matched_sources: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Video":
        return cls(
            bvid=str(data.get("bvid") or ""),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
            platform=str(data.get("platform") or "bilibili"),
            platform_id=str(data.get("platform_id") or data.get("bvid") or ""),
            owner_name=str(data.get("owner_name") or data.get("author") or ""),
            owner_mid=str(data.get("owner_mid") or data.get("mid") or ""),
            pubdate=str(data.get("pubdate") or ""),
            duration_seconds=int(data.get("duration_seconds") or data.get("duration") or 0),
            view_count=int(data.get("view_count") or 0),
            desc=str(data.get("desc") or data.get("description") or ""),
            tags=list(data.get("tags") or []),
            source_id=str(data.get("source_id") or ""),
            source_url=str(data.get("source_url") or ""),
            source_kind=str(data.get("source_kind") or ""),
            fetched_at=str(data.get("fetched_at") or ""),
            restricted_access=bool(data.get("restricted_access", False)),
            access_notes=list(data.get("access_notes") or []),
            commercial=bool(data.get("commercial", False)),
            commercial_notes=list(data.get("commercial_notes") or []),
            matched_queries=list(data.get("matched_queries") or []),
            matched_sources=list(data.get("matched_sources") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bvid": self.bvid,
            "platform": self.platform,
            "platform_id": self.platform_id,
            "title": self.title,
            "url": self.url,
            "owner_name": self.owner_name,
            "owner_mid": self.owner_mid,
            "pubdate": self.pubdate,
            "duration_seconds": self.duration_seconds,
            "view_count": self.view_count,
            "desc": self.desc,
            "tags": self.tags,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "fetched_at": self.fetched_at,
            "restricted_access": self.restricted_access,
            "access_notes": self.access_notes,
            "commercial": self.commercial,
            "commercial_notes": self.commercial_notes,
            "matched_queries": self.matched_queries,
            "matched_sources": self.matched_sources,
        }

    @property
    def publish_date(self) -> Optional[date]:
        return parse_date_text(self.pubdate)

    @property
    def age_days(self) -> int:
        published = self.publish_date
        if not published:
            return 10**9
        return max(0, (date.today() - published).days)

    @property
    def duration_minutes(self) -> int:
        if self.duration_seconds <= 0:
            return 0
        return max(1, round(self.duration_seconds / 60))

    @property
    def fingerprint(self) -> str:
        # platform-namespaced to avoid BV/videoId collision across platforms
        pid = self.platform_id or self.bvid
        if pid:
            return f"{self.platform}:{pid}"
        return self.url or self.title

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.title,
                self.desc,
                self.owner_name,
                " ".join(self.tags),
                self.source_id,
            ]
        ).lower()


def merge_videos(base: Video, incoming: Video) -> Video:
    return replace(
        base,
        title=incoming.title or base.title,
        url=incoming.url or base.url,
        owner_name=incoming.owner_name or base.owner_name,
        owner_mid=incoming.owner_mid or base.owner_mid,
        pubdate=incoming.pubdate or base.pubdate,
        duration_seconds=max(base.duration_seconds, incoming.duration_seconds),
        desc=incoming.desc if len(incoming.desc) >= len(base.desc) else base.desc,
        tags=dedupe_keep_order([*base.tags, *incoming.tags]),
        source_id=incoming.source_id or base.source_id,
        source_url=incoming.source_url or base.source_url,
        source_kind=incoming.source_kind or base.source_kind,
        fetched_at=max(base.fetched_at, incoming.fetched_at),
        restricted_access=base.restricted_access or incoming.restricted_access,
        access_notes=dedupe_keep_order([*base.access_notes, *incoming.access_notes]),
        commercial=base.commercial or incoming.commercial,
        commercial_notes=dedupe_keep_order([*base.commercial_notes, *incoming.commercial_notes]),
        matched_queries=dedupe_keep_order([*base.matched_queries, *incoming.matched_queries]),
        matched_sources=dedupe_keep_order([*base.matched_sources, *incoming.matched_sources]),
    )


@dataclass
class PlanRequest:
    topic: str = "瑜伽"
    scope: str = "mixed"
    freshness: str = "latest"
    template: str = "auto"
    days: int = 7
    dedupe_window_days: int = 14
    exclude_restricted: bool = True
    exclude_commercial: bool = True
    max_per_source: int = 20
    search_limit: int = 50
    keywords: List[str] = field(default_factory=list)
    source_inputs: List[str] = field(default_factory=list)
    duration_min: int = 0
    duration_max: int = 0
    min_view_count: int = 0      # drop videos with fewer plays (0 = no filter). Default 100 for search results.
    notes: str = ""              # user context carried through the entire pipeline
    followed_mids: List[str] = field(default_factory=list)  # MIDs of followed UPs for scorer boost

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanRequest":
        return cls(
            topic=str(data.get("topic") or "瑜伽"),
            scope=str(data.get("scope") or "mixed"),
            freshness=str(data.get("freshness") or "latest"),
            template=str(data.get("template") or "auto"),
            days=int(data.get("days") or 7),
            dedupe_window_days=int(data.get("dedupe_window_days") or 14),
            exclude_restricted=bool(data.get("exclude_restricted", True)),
            exclude_commercial=bool(data.get("exclude_commercial", True)),
            max_per_source=int(data.get("max_per_source") or 20),
            search_limit=int(data.get("search_limit") or 50),
            keywords=list(data.get("keywords") or []),
            source_inputs=list(data.get("source_inputs") or []),
            duration_min=int(data.get("duration_min") or 0),
            duration_max=int(data.get("duration_max") or 0),
            min_view_count=int(data.get("min_view_count") or 0),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "scope": self.scope,
            "freshness": self.freshness,
            "template": self.template,
            "days": self.days,
            "dedupe_window_days": self.dedupe_window_days,
            "exclude_restricted": self.exclude_restricted,
            "exclude_commercial": self.exclude_commercial,
            "max_per_source": self.max_per_source,
            "search_limit": self.search_limit,
            "keywords": self.keywords,
            "source_inputs": self.source_inputs,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "min_view_count": self.min_view_count,
            "notes": self.notes,
        }


@dataclass
class Slot:
    title: str
    intent: str
    keywords: List[str] = field(default_factory=list)


@dataclass
class DraftItem:
    day_index: int
    day_name: str
    date_label: str
    slot_title: str
    intent: str
    video: Optional[Video]
    reason: str
    alternatives: List[Video] = field(default_factory=list)


@dataclass
class DraftPlan:
    request: PlanRequest
    strategy: str
    items: List[DraftItem]
    generated_at: str = field(default_factory=utc_now_iso)


@dataclass
class CandidatePack:
    request: PlanRequest
    sources: List[Source]
    videos: List[Video]
    stats: Dict[str, Any] = field(default_factory=dict)
    generated_at: str = field(default_factory=utc_now_iso)
    run_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CandidatePack":
        return cls(
            request=PlanRequest.from_dict(data.get("request") or {}),
            sources=[Source.from_dict(item or {}) for item in data.get("sources") or []],
            videos=[Video.from_dict(item or {}) for item in data.get("videos") or []],
            stats=dict(data.get("stats") or {}),
            generated_at=str(data.get("generated_at") or utc_now_iso()),
            run_id=str(data.get("run_id") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "request": self.request.to_dict(),
            "stats": self.stats,
            "sources": [source.to_dict() for source in self.sources],
            "videos": [video.to_dict() for video in self.videos],
        }
