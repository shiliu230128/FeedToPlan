from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from .auth import cookie_status, delete_cookie, resolve_cookie, set_cookie_interactive, set_cookie_value
from .crawler import UnifiedCrawler, parse_bvid, source_from_text
from .crawler_bili import BiliCrawlerError
from .filters import detect_follow_along, refresh_video_flags, requires_follow_along_filter
from .models import CandidatePack, PlanRequest, Source, Video, dedupe_keep_order, slugify
from .paths import cache_dir, config_dir, data_dir, project_root, runs_dir, secrets_dir
from .planner import build_candidate_stats, draft_weekly_plan, select_slots
from .prompts import WIZARD_PROMPT, build_ai_prompt
from .render import render_brief_markdown, render_draft_markdown
from .storage import (
    ensure_layout,
    load_json,
    load_preferences,
    load_sources,
    load_state,
    load_videos_jsonl,
    prune_recent_usage,
    recent_bvids,
    record_recent_usage,
    remove_inaccessible_bvids,
    save_json,
    save_sources,
    save_state,
    upsert_source,
    upsert_videos_jsonl,
)


PROJECT_ROOT = project_root()
DEFAULT_SOURCES = config_dir() / "sources.json"
DEFAULT_PREFERENCES = config_dir() / "preferences.json"
DEFAULT_VIDEOS = cache_dir() / "videos.jsonl"
DEFAULT_STATE = data_dir() / "state.json"
DEFAULT_COOKIE_FILE = secrets_dir() / "bilibili_cookie.txt"
DEFAULT_USER_MEMORY = data_dir() / "user_memory.json"

DEFAULT_PREFERENCES_DATA = {
    "default_topic": "瑜伽",
    "default_scope": "mixed",
    "default_freshness": "latest",
    "default_days": 7,
    "default_template": "auto",
    "dedupe_window_days": 14,
    "exclude_commercial": True,
    "exclude_restricted": True,
    "max_per_source": 20,
    "search_limit": 50,
}

DEFAULT_STATE_DATA = {
    "recently_used": [],
    "last_run_dir": "",
    "last_pack_path": "",
    "last_prompt_path": "",
    "last_draft_path": "",
}

def resolve_yt_api_key(explicit: str = "") -> str:
    """Return YouTube Data API key from arg, env var, or secrets file."""
    import os
    if explicit:
        return explicit.strip()
    env = os.environ.get("YT_API_KEY", "")
    if env:
        return env.strip()
    key_file = secrets_dir() / "youtube_api_key.txt"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    return ""


def resolve_ai_api_key(explicit: str = "") -> str:
    """Return OpenAI-compatible API key for topic-profile generation."""
    import os
    if explicit:
        return explicit.strip()
    for var in ("OPENAI_API_KEY", "AI_API_KEY"):
        env = os.environ.get(var, "")
        if env:
            return env.strip()
    key_file = secrets_dir() / "openai_api_key.txt"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    return ""



SOURCE_FIELDS = [
    "id",
    "kind",
    "name",
    "url",
    "mid",
    "bvid",
    "tags",
    "notes",
    "enabled",
]


def main(argv: Optional[List[str]] = None) -> int:
    ensure_workspace()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


def ensure_workspace() -> None:
    ensure_layout(PROJECT_ROOT)
    if not DEFAULT_PREFERENCES.exists():
        save_json(DEFAULT_PREFERENCES, DEFAULT_PREFERENCES_DATA)
    if not DEFAULT_SOURCES.exists():
        save_json(DEFAULT_SOURCES, {"sources": []})
    if not DEFAULT_STATE.exists():
        save_json(DEFAULT_STATE, DEFAULT_STATE_DATA)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-arrangement3",
        description="Collect Bilibili videos and produce prompt-driven weekly plans.",
    )
    parser.add_argument("--version", action="version", version="bili-arrangement3 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    add = subparsers.add_parser("add-source", help="Add a permanent UP/video source")
    add.add_argument("value", help="UP homepage URL, video URL, or UP name")
    add.add_argument("--id", default="")
    add.add_argument("--name", default="")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    add.set_defaults(func=cmd_add_source)

    ls = subparsers.add_parser("list-sources", help="List stored sources")
    ls.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    ls.set_defaults(func=cmd_list_sources)

    sync = subparsers.add_parser("sync", help="Crawl Bilibili and build a candidate pack")
    add_workflow_args(sync)
    sync.set_defaults(func=cmd_sync)

    brief = subparsers.add_parser("brief", help="Write the AI prompt and brief for the latest or chosen pack")
    add_pack_args(brief)
    brief.set_defaults(func=cmd_brief)

    draft = subparsers.add_parser("draft", help="Write a heuristic fallback weekly draft")
    add_pack_args(draft)
    draft.add_argument("--record", action="store_true", default=True)
    draft.add_argument("--no-record", action="store_false", dest="record")
    draft.set_defaults(func=cmd_draft)

    plan = subparsers.add_parser("plan", help="Sync, brief, and draft in one shot")
    add_workflow_args(plan)
    plan.add_argument("--record", action="store_true", default=True)
    plan.add_argument("--no-record", action="store_false", dest="record")
    plan.set_defaults(func=cmd_plan)

    wizard = subparsers.add_parser("wizard", help="Interactive no-brainer intake")
    add_workflow_args(wizard)
    wizard.set_defaults(func=cmd_wizard)

    sync_src = subparsers.add_parser("sync-sources", help="Pull following list and auto-populate source files")
    sync_src.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    sync_src.add_argument("--preferences", type=Path, default=DEFAULT_PREFERENCES)
    sync_src.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    sync_src.add_argument("--max-count", type=int, default=500)
    sync_src.set_defaults(func=cmd_sync_sources)

    history = subparsers.add_parser("history", help="Show recent no-repeat history")
    history.add_argument("--state", type=Path, default=DEFAULT_STATE)
    history.set_defaults(func=cmd_history)

    auth = subparsers.add_parser("auth", help="Manage a local Bilibili cookie")
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_set = auth_sub.add_parser("set-cookie", help="Store a cookie locally")
    auth_set.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    auth_set.add_argument("--stdin", action="store_true")
    auth_set.set_defaults(func=cmd_auth_set_cookie)

    auth_status = auth_sub.add_parser("status", help="Show cookie status")
    auth_status.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    auth_status.set_defaults(func=cmd_auth_status)

    auth_delete = auth_sub.add_parser("delete-cookie", help="Delete the local cookie file")
    auth_delete.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    auth_delete.set_defaults(func=cmd_auth_delete)
    return parser


def add_pack_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pack", type=Path, default=None, help="Candidate pack JSON path")
    parser.add_argument("--run-dir", type=Path, default=None, help="Run directory containing pack.json")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)


def add_workflow_args(parser: argparse.ArgumentParser) -> None:
    add_pack_args(parser)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--preferences", type=Path, default=DEFAULT_PREFERENCES)
    parser.add_argument("--videos", type=Path, default=DEFAULT_VIDEOS)
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument("--scope", choices=["following", "topic", "mixed", "following-topic", "links"], default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--freshness", choices=["latest", "balanced", "classic"], default="")
    parser.add_argument("--template", choices=["auto", "yoga", "generic"], default="")
    parser.add_argument("--days", type=int, default=0)
    parser.add_argument("--dedupe-window-days", type=int, default=0)
    parser.add_argument("--duration-min", type=int, default=0)
    parser.add_argument("--duration-max", type=int, default=0)
    parser.add_argument("--max-per-source", type=int, default=0)
    parser.add_argument("--search-limit", type=int, default=0)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--save-source", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Force a fresh crawl instead of using cache only")
    parser.add_argument("--offline", action="store_true", help="Skip Bilibili network calls and use the local cache only")
    parser.add_argument("--yt-key", default="", help="YouTube Data API v3 key (or set YT_API_KEY env var)")
    parser.add_argument("--ai-key", default="", help="OpenAI API key for dynamic topic profiles (or set OPENAI_API_KEY env var)")
    parser.add_argument("--platform", choices=["bilibili", "youtube", "all"], default="bilibili", help="Which platform(s) to search")
    parser.add_argument("--min-view-count", type=int, default=0, help="Exclude videos with fewer plays (0 = auto: 100 for keyword-search results, 0 for followed UPs)")
    parser.add_argument("--notes", default="")


def build_request(args: argparse.Namespace) -> PlanRequest:
    from .user_memory import get_domain_defaults, load_memory
    preferences = load_preferences(args.preferences)
    topic = args.topic or str(preferences.get("default_topic") or "瑜伽")
    scope = args.scope or str(preferences.get("default_scope") or "mixed")
    freshness = args.freshness or str(preferences.get("default_freshness") or "latest")
    template = args.template or str(preferences.get("default_template") or "auto")
    days = args.days or int(preferences.get("default_days") or 7)
    dedupe_window_days = args.dedupe_window_days or int(preferences.get("dedupe_window_days") or 14)
    max_per_source = args.max_per_source or int(preferences.get("max_per_source") or 20)
    search_limit = args.search_limit or int(preferences.get("search_limit") or 50)
    duration_min = args.duration_min
    duration_max = args.duration_max
    notes = str(args.notes or "")

    # Apply memory defaults for fields not explicitly set via CLI
    memory = load_memory(DEFAULT_USER_MEMORY)
    mem_defaults = get_domain_defaults(memory, topic)
    if not scope or scope == str(preferences.get("default_scope") or "mixed"):
        scope = mem_defaults.get("scope", scope)
    if not freshness or freshness == str(preferences.get("default_freshness") or "latest"):
        freshness = mem_defaults.get("freshness", freshness)
    if not duration_min:
        duration_min = int(mem_defaults.get("duration_min", 0))
    if not duration_max:
        duration_max = int(mem_defaults.get("duration_max", 0))
    if not notes and mem_defaults.get("notes"):
        notes = mem_defaults["notes"]

    return PlanRequest(
        topic=topic,
        scope=scope,
        freshness=freshness,
        template=template,
        days=max(1, min(days, 14)),
        dedupe_window_days=max(0, dedupe_window_days),
        exclude_restricted=bool(preferences.get("exclude_restricted", True)),
        exclude_commercial=bool(preferences.get("exclude_commercial", True)),
        max_per_source=max_per_source,
        search_limit=search_limit,
        keywords=dedupe_keep_order([*args.keyword, topic] if topic else args.keyword),
        source_inputs=list(args.source or []),
        duration_min=max(0, duration_min),
        duration_max=max(0, duration_max),
        # Auto-default: 100 for search-result heavy scopes when not explicitly set
        min_view_count=max(0, getattr(args, "min_view_count", 0) or (
            int(preferences.get("min_view_count") or 0)
            or (100 if scope in {"topic", "mixed"} else 0)
        )),
        notes=notes,
    )


def run_id_for_request(request: PlanRequest) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(request.topic or 'bilibili')}"


def run_dir_for_request(request: PlanRequest) -> Path:
    return runs_dir() / run_id_for_request(request)


def make_source_objects(args: argparse.Namespace) -> List[Source]:
    sources = load_sources(args.sources)
    sources.extend(source_from_text(value) for value in args.source)
    return dedupe_sources(sources)


def dedupe_sources(sources: Sequence[Source]) -> List[Source]:
    by_key: dict[str, Source] = {}
    for source in sources:
        key = source.mid or source.bvid or source.url or source.id
        if key in by_key:
            by_key[key] = merge_sources(by_key[key], source)
        else:
            by_key[key] = source
    return list(by_key.values())


def merge_sources(base: Source, incoming: Source) -> Source:
    return Source(
        id=base.id or incoming.id,
        kind=base.kind or incoming.kind,
        name=incoming.name or base.name,
        url=incoming.url or base.url,
        mid=incoming.mid or base.mid,
        bvid=incoming.bvid or base.bvid,
        tags=dedupe_keep_order([*base.tags, *incoming.tags]),
        notes=" | ".join(part for part in [base.notes, incoming.notes] if part),
        enabled=base.enabled and incoming.enabled,
        added_at=base.added_at or incoming.added_at,
    )


def filter_videos_by_sources(videos: Sequence[Video], sources: Sequence[Source]) -> List[Video]:
    mids = {source.mid for source in sources if source.mid}
    bvids = {source.bvid for source in sources if source.bvid}
    source_ids = {source.id for source in sources if source.id}
    source_urls = {source.url for source in sources if source.url}
    if not mids and not bvids and not source_ids and not source_urls:
        return list(videos)
    filtered: List[Video] = []
    for video in videos:
        matched_source_ids = set(video.matched_sources or [])
        if video.owner_mid and video.owner_mid in mids:
            filtered.append(video)
            continue
        if video.bvid and video.bvid in bvids:
            filtered.append(video)
            continue
        if video.source_id and video.source_id in source_ids:
            filtered.append(video)
            continue
        if video.source_url and video.source_url in source_urls:
            filtered.append(video)
            continue
        if matched_source_ids & source_ids:
            filtered.append(video)
    return filtered


def collect_pack(
    args: argparse.Namespace,
    request: Optional[PlanRequest] = None,
) -> tuple[CandidatePack, Path]:
    request = request or build_request(args)
    state = load_state(args.state)
    prune_recent_usage(state, request.dedupe_window_days)
    cookie, cookie_source = resolve_cookie(args.cookie_file)
    yt_api_key = resolve_yt_api_key(getattr(args, "yt_key", "") or "")
    crawler = UnifiedCrawler(bili_cookie=cookie, yt_api_key=yt_api_key)
    offline = bool(getattr(args, "offline", False))
    persistent_sources = load_sources(args.sources)
    ad_hoc_sources = [source_from_text(value) for value in args.source]
    if getattr(args, "save_source", False) and ad_hoc_sources:
        merged_sources = dedupe_sources([*persistent_sources, *ad_hoc_sources])
        save_sources(args.sources, merged_sources)
        persistent_sources = merged_sources
    # Auto-sync followings on first run (when source pool is empty and cookie is available)
    if not persistent_sources and cookie:
        try:
            from .source_discovery import sync_bili_followings
            from .crawler_bili import BiliCrawler as _BiliCrawler
            _sync_crawler = _BiliCrawler(cookie=cookie, pause_seconds=0.6)
            sync_bili_followings(_sync_crawler, config_dir=config_dir(), project_root=project_root(), verbose=False)
            persistent_sources = load_sources(args.sources)
        except Exception:
            import sys
            print("[warn] 关注列表同步失败，可稍后手动运行 sync-sources", file=sys.stderr)

    source_pool = dedupe_sources([*persistent_sources, *ad_hoc_sources])
    resolved_sources = list(source_pool) if offline else [crawler.resolve_source(source) for source in source_pool]
    resolved_sources = dedupe_sources(resolved_sources)
    # Track followed MIDs for scorer boost
    request.followed_mids = [s.mid for s in resolved_sources if s.mid]
    query_terms = dedupe_keep_order([request.topic, *request.keywords])
    raw_videos: List[Video] = []
    errors: List[str] = []

    if not offline and request.scope in {"following", "mixed", "following-topic", "links"}:
        for source in resolved_sources:
            try:
                if source.kind == "video" or source.bvid:
                    bvid = source.bvid or parse_bvid(source.url)
                    if bvid:
                        raw_videos.append(crawler.fetch_video(bvid, source))
                elif source.kind == "up":
                    raw_videos.extend(crawler.fetch_up_videos(source, max_per_source=request.max_per_source))
            except Exception as exc:
                errors.append(f"{source.id}: {exc}")
                if request.scope in {"mixed", "topic", "following-topic"}:
                    fallback_terms = dedupe_keep_order(
                        [
                            f"{source.display_name} {request.topic}".strip(),
                            source.display_name,
                        ]
                    )
                    for term in fallback_terms:
                        try:
                            fallback = crawler.search_videos(
                                term,
                                max_results=request.search_limit,
                                owner_mid_filter={source.mid} if source.mid else None,
                            )
                            if not source.mid and source.display_name:
                                fallback = [
                                    video
                                    for video in fallback
                                    if source.display_name.lower() in video.owner_name.lower()
                                ]
                            raw_videos.extend(fallback)
                        except Exception as fallback_exc:
                            errors.append(f"fallback:{source.id}:{term}: {fallback_exc}")

    if not offline and request.scope in {"topic", "mixed", "following-topic"}:
        allowed_mids = {source.mid for source in resolved_sources if source.mid}
        for term in query_terms:
            try:
                if request.scope == "following-topic":
                    raw_videos.extend(
                        crawler.search_videos(term, max_results=request.search_limit, owner_mid_filter=allowed_mids)
                    )
                else:
                    raw_videos.extend(crawler.search_videos(term, max_results=request.search_limit, platform=getattr(args, "platform", "bilibili")))
            except Exception as exc:
                errors.append(f"search:{term}: {exc}")

    used_cache_fallback = False
    if not raw_videos:
        raw_videos = load_videos_jsonl(args.videos)
        used_cache_fallback = True
        if request.scope in {"following", "following-topic", "links"} and resolved_sources:
            raw_videos = filter_videos_by_sources(raw_videos, resolved_sources)
    raw_videos = [refresh_video_flags(video) for video in raw_videos]
    cached_videos = upsert_videos_jsonl(args.videos, raw_videos)
    raw_keys = {video.fingerprint for video in raw_videos if video.fingerprint}
    current_videos = [video for video in cached_videos if video.fingerprint in raw_keys] if raw_keys else []
    if not current_videos:
        current_videos = raw_videos

    recent = recent_bvids(state, request.dedupe_window_days)
    eligible: List[Video] = []
    removed = Counter()
    from .topic_profile import load_profile as _load_profile
    _topic_profile = _load_profile(request.topic, data_dir(), resolve_ai_api_key(getattr(args, "ai_key", "") or ""))
    # Apply dimension-based + smart keyword filtering from user notes
    from .filters import apply_filter_dimensions
    _topic_profile = apply_filter_dimensions(_topic_profile, request.notes or "")
    require_follow_along = requires_follow_along_filter(_topic_profile)
    stale_bvids: set = set()
    for video in current_videos:
        if video.bvid and video.bvid in recent:
            removed["recent"] += 1
            continue
        if request.exclude_restricted and video.restricted_access:
            removed["restricted"] += 1
            continue
        if request.exclude_commercial and video.commercial:
            removed["commercial"] += 1
            continue
        if request.duration_min and video.duration_minutes and video.duration_minutes < request.duration_min:
            removed["duration_min"] += 1
            continue
        if request.duration_max and video.duration_minutes and video.duration_minutes > request.duration_max:
            removed["duration_max"] += 1
            continue
        # Low-quality filter: skip videos with fewer plays than the threshold.
        # Applies only when view_count is known (>0) to avoid filtering followed-UP content
        # that may not yet have high counts. For search results, default is 100.
        if request.min_view_count and video.view_count and video.view_count < request.min_view_count:
            removed["low_quality"] += 1
            continue
        if require_follow_along and _topic_profile:
            follow_along, _notes = detect_follow_along(video, _topic_profile)
            if not follow_along:
                removed["non_follow_along"] += 1
                continue
        # Space constraint enforcement: when user specifies posture (站立/坐姿),
        # reject videos that don't explicitly declare a matching posture in title/desc.
        if _topic_profile.required_space_terms:
            searchable = video.searchable_text()
            if not any(term in searchable for term in _topic_profile.required_space_terms):
                removed["wrong_posture"] += 1
                continue
        # Accessibility check: skip videos that have been deleted or made private since crawl.
        # Runs after all other filters to minimize API calls. Results are cached per crawler instance.
        if not offline and video.platform == "bilibili" and video.bvid:
            if not crawler._bili.check_video_accessible(video.bvid):
                removed["inaccessible"] += 1
                stale_bvids.add(video.bvid)
                continue
        eligible.append(video)

    # Prune inaccessible videos from the local cache so they don't pollute future runs
    if stale_bvids and not offline:
        pruned = remove_inaccessible_bvids(args.videos, stale_bvids)
        if pruned:
            removed["cache_pruned"] = pruned

    stats = build_candidate_stats(eligible)
    stats.update(
        {
            "raw_fetch_count": len(raw_videos),
            "eligible_count": len(eligible),
            "removed_recent": removed.get("recent", 0),
            "removed_restricted": removed.get("restricted", 0),
            "removed_commercial": removed.get("commercial", 0),
            "removed_duration_min": removed.get("duration_min", 0),
            "removed_duration_max": removed.get("duration_max", 0),
            "removed_low_quality": removed.get("low_quality", 0),
            "removed_non_follow_along": removed.get("non_follow_along", 0),
            "resolved_source_count": len(resolved_sources),
            "query_terms": query_terms,
            "errors": errors[:8],
            "cookie_source": cookie_source or "",
            "used_cache_fallback": used_cache_fallback,
            "offline": offline,
        }
    )
    pack = CandidatePack(
        request=request,
        sources=resolved_sources,
        videos=eligible,
        stats=stats,
        run_id=run_id_for_request(request),
    )
    run_dir = run_dir_for_request(request)
    run_dir.mkdir(parents=True, exist_ok=True)
    state["last_run_dir"] = str(run_dir)
    state["last_pack_path"] = str(run_dir / "pack.json")
    save_state(args.state, state)
    save_json(run_dir / "pack.json", pack.to_dict())
    return pack, run_dir


def load_pack(args: argparse.Namespace) -> tuple[CandidatePack, Path, Path]:
    pack_path = resolve_pack_path(args)
    if not pack_path.exists():
        raise SystemExit(f"Pack not found: {pack_path}")
    pack = CandidatePack.from_dict(load_json(pack_path, {}))
    pack.videos = [refresh_video_flags(video) for video in pack.videos]
    run_dir = pack_path.parent
    return pack, pack_path, run_dir


def resolve_pack_path(args: argparse.Namespace) -> Path:
    if args.pack:
        return args.pack
    if args.run_dir:
        return args.run_dir / "pack.json"
    state = load_state(args.state)
    last_pack = str(state.get("last_pack_path") or "")
    if last_pack:
        return Path(last_pack)
    last_run_dir = str(state.get("last_run_dir") or "")
    if last_run_dir:
        return Path(last_run_dir) / "pack.json"
    raise SystemExit("No pack found. Run `plan` or `sync` first, or pass --pack / --run-dir.")


def save_outputs(
    args: argparse.Namespace,
    pack: CandidatePack,
    run_dir: Path,
    record: bool = True,
) -> tuple[Path, Path, Path]:
    state = load_state(args.state)
    request = pack.request
    slots = select_slots(request, data_dir())
    pack_path = run_dir / "pack.json"
    prompt_path = run_dir / "prompt.md"
    brief_path = run_dir / "brief.md"
    draft_path = run_dir / "draft.md"
    prompt_text = build_ai_prompt(request, pack_path, pack.stats, slots)
    brief_text = render_brief_markdown(request, pack_path, prompt_path, draft_path, pack.stats)
    draft_plan = draft_weekly_plan(pack.videos, request, set(recent_bvids(state, request.dedupe_window_days)))
    draft_text = render_draft_markdown(draft_plan)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    brief_path.write_text(brief_text, encoding="utf-8")
    draft_path.write_text(draft_text, encoding="utf-8")
    state["last_pack_path"] = str(pack_path)
    state["last_prompt_path"] = str(prompt_path)
    state["last_draft_path"] = str(draft_path)
    state["last_run_dir"] = str(run_dir)
    if record:
        selected = [item.video for item in draft_plan.items if item.video]
        record_recent_usage(state, selected, pack.run_id, request.topic)
        prune_recent_usage(state, request.dedupe_window_days)
    save_state(args.state, state)
    _prune_old_runs()
    return brief_path, prompt_path, draft_path


def cmd_add_source(args: argparse.Namespace) -> int:
    source = source_from_text(args.value, source_id=args.id or "")
    if args.name:
        source.name = args.name
    if args.tag:
        source.tags = dedupe_keep_order(args.tag)
    upsert_source(args.sources, source)
    print(f"added_source={source.id}")
    return 0


def cmd_list_sources(args: argparse.Namespace) -> int:
    sources = load_sources(args.sources)
    for source in sources:
        tags = ",".join(source.tags) if source.tags else "-"
        print(f"{source.id}\t{source.kind}\t{source.display_name}\t{source.url or '-'}\t{source.mid or source.bvid or '-'}\t{tags}")
    print(f"source_count={len(sources)}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    pack, run_dir = collect_pack(args)
    print(str(run_dir / "pack.json"))
    print(f"candidate_count={len(pack.videos)}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    pack, pack_path, run_dir = load_pack(args)
    brief_path, prompt_path, draft_path = save_outputs(args, pack, run_dir, record=False)
    print(str(brief_path))
    print(str(prompt_path))
    print(str(draft_path))
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    pack, pack_path, run_dir = load_pack(args)
    brief_path, prompt_path, draft_path = save_outputs(args, pack, run_dir, record=bool(args.record))
    print(str(draft_path))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    pack, run_dir = collect_pack(args)
    brief_path, prompt_path, draft_path = save_outputs(args, pack, run_dir, record=bool(args.record))
    print(str(run_dir / "pack.json"))
    print(str(brief_path))
    print(str(prompt_path))
    print(str(draft_path))
    _update_memory_after_plan(args, pack.request)
    return 0


def cmd_wizard(args: argparse.Namespace) -> int:
    print(WIZARD_PROMPT)

    # ── Step 1: topic ────────────────────────────────────────────────────────
    topic = input("主题或关键词（如：瑜伽 / 居家健身 / 冥想 / 疗愈音乐，默认：瑜伽）\n> ").strip() or "瑜伽"

    # ── Step 2: goal / current state → notes ────────────────────────────────
    goal = input("这次的目标或当前状态？（如：减脂 / 缓解肩颈 / 睡前放松 / 压力大，回车跳过）\n> ").strip()
    constraints = input("需要回避的内容或身体限制？（如：膝盖不好 / 不要跳跃 / 无歌词，回车跳过）\n> ").strip()
    notes_parts = [p for p in [goal, constraints] if p]
    notes = "；".join(notes_parts)

    # ── Step 3: duration ─────────────────────────────────────────────────────
    duration_min_text = input("视频最短多少分钟？（回车不限制）\n> ").strip()
    duration_min = int(duration_min_text) if duration_min_text.isdigit() else 0
    duration_max_text = input("视频最长多少分钟？（回车不限制）\n> ").strip()
    duration_max = int(duration_max_text) if duration_max_text.isdigit() else 0

    # ── Step 4: scope ────────────────────────────────────────────────────────
    print("\n内容来源：\nA. 只从关注列表\nB. 关注列表 + 主题搜索（默认）\nC. 只全站搜索\nD. 我直接提供 UP/链接")
    scope_choice = input("> ").strip().upper() or "B"
    scope = {"A": "following", "B": "mixed", "C": "topic", "D": "links"}.get(scope_choice, "mixed")

    # ── Step 5: freshness ────────────────────────────────────────────────────
    print("\n更新权重：\nA. 最新优先（默认）\nB. 新旧均衡\nC. 历史补全")
    freshness_choice = input("> ").strip().upper() or "A"
    freshness = {"A": "latest", "B": "balanced", "C": "classic"}.get(freshness_choice, "latest")

    # ── Step 6: extra keywords / ad-hoc sources ──────────────────────────────
    extra_keywords = input("\n额外关键词（逗号分隔，回车跳过）\n> ").strip()
    source_text = input("临时 UP/主页/视频链接（逗号分隔，回车跳过）\n> ").strip()
    save_choice = input("是否保存这些临时来源到关注列表？(y/N)\n> ").strip().lower()
    save_source = save_choice in {"y", "yes", "1", "true"}

    keyword_list = [item.strip() for item in extra_keywords.split(",") if item.strip()]
    source_list = [item.strip() for item in source_text.split(",") if item.strip()]

    # Persist wizard input to semantic memory
    if notes or duration_min or duration_max:
        from .user_memory import ingest_wizard_notes, load_memory, save_memory
        memory = load_memory(DEFAULT_USER_MEMORY)
        ingest_wizard_notes(memory, topic, notes, duration_min=duration_min, duration_max=duration_max)
        save_memory(DEFAULT_USER_MEMORY, memory)

    # Use parser defaults for plan subcommand, then override wizard-specific values
    parser = build_parser()
    try:
        namespace = parser.parse_args(["plan"])
    except SystemExit:
        namespace = argparse.Namespace()
    namespace.scope = scope
    namespace.topic = topic
    namespace.freshness = freshness
    namespace.keyword = keyword_list
    namespace.source = source_list
    namespace.save_source = save_source
    namespace.duration_min = duration_min
    namespace.duration_max = duration_max
    namespace.notes = notes
    namespace.func = cmd_plan
    return cmd_plan(namespace)


def cmd_history(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    prune_recent_usage(state, 30)
    for item in reversed(state.get("recently_used", [])):
        pid = item.get("platform") or "bilibili"
        vid = item.get("platform_id") or item.get("bvid") or item.get("fingerprint", "")
        print(f"{item.get('used_at')}\t{item.get('topic')}\t{item.get('title')}\t{pid}:{vid}")
    print(f"history_count={len(state.get('recently_used', []))}")
    return 0


def cmd_auth_set_cookie(args: argparse.Namespace) -> int:
    if args.stdin:
        value = set_cookie_value(args.cookie_file, sys.stdin.read())
    else:
        value = set_cookie_interactive(args.cookie_file)
    print(f"cookie_saved={args.cookie_file}")
    print(f"cookie_length={len(value)}")
    return 0


def cmd_auth_status(args: argparse.Namespace) -> int:
    present, source = cookie_status(args.cookie_file)
    print(f"cookie_configured={'true' if present else 'false'}")
    if source:
        print(f"cookie_source={source}")
    return 0


def cmd_auth_delete(args: argparse.Namespace) -> int:
    deleted = delete_cookie(args.cookie_file)
    print(f"cookie_deleted={'true' if deleted else 'false'}")
    return 0



def _update_memory_after_plan(args: argparse.Namespace, request: "PlanRequest") -> None:
    """Write episodic entry and maybe update procedural layer after a plan run."""
    from .user_memory import append_episodic, load_memory, maybe_update_procedural, save_memory
    memory = load_memory(DEFAULT_USER_MEMORY)
    notes = request.notes or ""
    append_episodic(
        memory,
        topic=request.topic,
        user_note=notes,
        params={
            "scope": request.scope,
            "freshness": request.freshness,
            "duration_min": request.duration_min,
            "duration_max": request.duration_max,
        },
    )
    maybe_update_procedural(
        memory,
        topic=request.topic,
        params={
            "scope": request.scope,
            "freshness": request.freshness,
            "duration_min": request.duration_min,
            "duration_max": request.duration_max,
        },
    )
    save_memory(DEFAULT_USER_MEMORY, memory)


def cmd_sync_sources(args: argparse.Namespace) -> int:
    """Pull Bilibili following list and auto-classify into source bucket files."""
    from .source_discovery import sync_bili_followings
    cookie, cookie_source = resolve_cookie(args.cookie_file)
    if not cookie:
        print("error: no Bilibili cookie configured. Run: bili-arrangement3 auth set-cookie")
        return 1
    from .crawler_bili import BiliCrawler
    crawler = BiliCrawler(cookie=cookie, pause_seconds=0.6)
    stats = sync_bili_followings(
        crawler,
        config_dir=config_dir(),
        project_root=project_root(),
        verbose=True,
    )
    for bucket_id, added in stats.items():
        print(f"sync_sources_{bucket_id}_added={added}")
    return 0



def _prune_old_runs(keep: int = 15) -> None:
    """Delete oldest timestamped run directories, keeping the most recent *keep* ones."""
    runs = runs_dir()
    if not runs.exists():
        return
    dirs = sorted(
        (d for d in runs.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    for old_dir in dirs[:-keep] if len(dirs) > keep else []:
        try:
            import shutil
            shutil.rmtree(old_dir)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
