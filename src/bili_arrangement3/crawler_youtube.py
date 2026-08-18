"""
crawler_youtube.py
==================
YouTube Data API v3 crawler.  Uses only Python stdlib (urllib) — no extra deps.

Capabilities:
  - search_videos(keyword, max_results)  → List[Video]
  - fetch_channel_videos(source, max_per_source) → List[Video]
  - resolve_source(source) → Source  (fill in channel title)

API key must be supplied at construction time (api_key parameter).
Quota cost per 100 results: ~100 search units.  Daily free quota: 10,000 units.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import Source, Video, dedupe_keep_order, utc_now_iso


_BASE = "https://www.googleapis.com/youtube/v3"


class YTCrawlerError(Exception):
    pass


class YouTubeCrawler:
    def __init__(self, api_key: str, pause_seconds: float = 0.5) -> None:
        if not api_key:
            raise YTCrawlerError("YouTube API key is required")
        self.api_key = api_key
        self.pause_seconds = pause_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_source(self, source: Source) -> Source:
        """Fill in channel title and canonical channel_id from a Source."""
        if source.platform != "youtube":
            return source
        channel_id = source.channel_id or _extract_channel_id(source.url)
        if not channel_id:
            return source
        try:
            data = self._get(
                "channels",
                {"part": "snippet", "id": channel_id, "maxResults": 1},
            )
            items = data.get("items") or []
            if items:
                title = (items[0].get("snippet") or {}).get("title") or source.name
                return replace(source, name=title or source.name, channel_id=channel_id)
        except Exception:
            pass
        return replace(source, channel_id=channel_id)

    def fetch_channel_videos(self, source: Source, max_per_source: int = 20) -> List[Video]:
        """Fetch latest videos from a YouTube channel."""
        channel_id = source.channel_id or _extract_channel_id(source.url)
        if not channel_id:
            raise YTCrawlerError(f"Source has no channel_id: {source.id}")

        # Get the uploads playlist ID from channel info
        data = self._get(
            "channels",
            {"part": "contentDetails", "id": channel_id, "maxResults": 1},
        )
        items = data.get("items") or []
        if not items:
            raise YTCrawlerError(f"Channel not found: {channel_id}")
        uploads_playlist = (
            ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {})
            .get("uploads") or ""
        )
        if not uploads_playlist:
            raise YTCrawlerError(f"No uploads playlist for channel: {channel_id}")

        # Fetch playlist items (video IDs only, no quota-heavy details)
        page_token = ""
        video_ids: List[str] = []
        while len(video_ids) < max_per_source:
            batch_size = min(50, max_per_source - len(video_ids))
            params: Dict[str, Any] = {
                "part": "contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": batch_size,
            }
            if page_token:
                params["pageToken"] = page_token
            playlist_data = self._get("playlistItems", params)
            for item in playlist_data.get("items") or []:
                vid_id = ((item.get("contentDetails") or {}).get("videoId") or "")
                if vid_id:
                    video_ids.append(vid_id)
            page_token = playlist_data.get("nextPageToken") or ""
            if not page_token:
                break

        return self._fetch_video_details(video_ids[:max_per_source], source)

    def search_videos(self, keyword: str, max_results: int = 20) -> List[Video]:
        """Full-text search across YouTube."""
        if not keyword:
            return []
        # YouTube search returns up to 50 per page; cap at 50 to avoid >1 page
        page_size = min(max_results, 50)
        params: Dict[str, Any] = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "maxResults": page_size,
            "order": "date",
            "videoEmbeddable": "true",
        }
        data = self._get("search", params)
        video_ids = [
            (item.get("id") or {}).get("videoId") or ""
            for item in (data.get("items") or [])
        ]
        video_ids = [v for v in video_ids if v][:max_results]
        # Build lightweight Video objects from snippet (no extra quota cost)
        videos: List[Video] = []
        for item in (data.get("items") or [])[:max_results]:
            vid_id = (item.get("id") or {}).get("videoId") or ""
            if not vid_id:
                continue
            snippet = item.get("snippet") or {}
            pub_raw = snippet.get("publishedAt") or ""
            pubdate = pub_raw[:10] if pub_raw else ""
            videos.append(Video(
                bvid="",
                platform="youtube",
                platform_id=vid_id,
                title=str(snippet.get("title") or ""),
                url=f"https://www.youtube.com/watch?v={vid_id}",
                owner_name=str(snippet.get("channelTitle") or ""),
                owner_mid=str(snippet.get("channelId") or ""),
                pubdate=pubdate,
                desc=str(snippet.get("description") or ""),
                tags=[keyword],
                source_id=f"yt-search-{keyword[:32]}",
                source_url=f"youtube-search:{keyword}",
                source_kind="keyword",
                fetched_at=utc_now_iso(),
                matched_queries=[keyword],
                matched_sources=[f"yt-search:{keyword}"],
            ))
        # Enrich with duration via videos.list (batch, costs ~1 unit per 50)
        if video_ids:
            enriched = {v.platform_id: v for v in videos}
            details_data = self._get(
                "videos",
                {"part": "contentDetails", "id": ",".join(video_ids)},
            )
            for item in details_data.get("items") or []:
                vid_id = item.get("id") or ""
                duration_iso = ((item.get("contentDetails") or {}).get("duration") or "")
                secs = _parse_iso8601_duration(duration_iso)
                if vid_id in enriched:
                    enriched[vid_id] = replace(enriched[vid_id], duration_seconds=secs)
            videos = list(enriched.values())
        return videos

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_video_details(self, video_ids: List[str], source: Source) -> List[Video]:
        """Given video IDs, fetch snippet + contentDetails in one batch call."""
        if not video_ids:
            return []
        data = self._get(
            "videos",
            {"part": "snippet,contentDetails", "id": ",".join(video_ids[:50])},
        )
        videos: List[Video] = []
        for item in data.get("items") or []:
            vid_id = item.get("id") or ""
            snippet = item.get("snippet") or {}
            pub_raw = snippet.get("publishedAt") or ""
            pubdate = pub_raw[:10] if pub_raw else ""
            duration_iso = ((item.get("contentDetails") or {}).get("duration") or "")
            videos.append(Video(
                bvid="",
                platform="youtube",
                platform_id=vid_id,
                title=str(snippet.get("title") or ""),
                url=f"https://www.youtube.com/watch?v={vid_id}",
                owner_name=source.display_name,
                owner_mid=source.channel_id,
                pubdate=pubdate,
                desc=str(snippet.get("description") or ""),
                tags=dedupe_keep_order([
                    *(snippet.get("tags") or []),
                    *source.tags,
                ]),
                source_id=source.id,
                source_url=source.url,
                source_kind=source.kind,
                fetched_at=utc_now_iso(),
                duration_seconds=_parse_iso8601_duration(duration_iso),
                matched_sources=[source.id],
            ))
        return videos

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = {**params, "key": self.api_key}
        url = f"{_BASE}/{endpoint}?{urlencode(params)}"
        req = Request(url, headers={"Accept": "application/json"})
        time.sleep(self.pause_seconds)
        try:
            with urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            raise YTCrawlerError(f"YouTube API error ({endpoint}): {exc}") from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_channel_id(url: str) -> str:
    """Extract UCxxx channel ID from a YouTube channel URL or bare ID."""
    if not url:
        return ""
    # Already a channel ID
    if url.startswith("UC") and "/" not in url:
        return url
    # https://www.youtube.com/channel/UCxxx
    import re
    m = re.search(r"youtube\.com/channel/(UC[^/?&]+)", url)
    if m:
        return m.group(1)
    # Handle @handle URLs — caller should convert to channel_id via resolve_source
    return ""


def _parse_iso8601_duration(text: str) -> int:
    """Parse ISO 8601 duration string (e.g. PT1H2M3S) → seconds."""
    if not text:
        return 0
    import re
    m = re.fullmatch(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
        text,
    )
    if not m:
        return 0
    hours = int(m.group(4) or 0)
    minutes = int(m.group(5) or 0)
    seconds = int(float(m.group(6) or 0))
    return hours * 3600 + minutes * 60 + seconds
