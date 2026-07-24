"""
crawler.py
==========
Unified crawler router.  Dispatches to crawler_bili or crawler_youtube
based on Source.platform.

Public interface used by cli.py:
  - source_from_text(text) → Source
  - parse_bvid(text) → str
  - BiliCrawlerError  (re-exported for compatibility)

  - UnifiedCrawler.resolve_source(source) → Source
  - UnifiedCrawler.fetch_video(bvid, source, query) → Video   [Bilibili only]
  - UnifiedCrawler.fetch_up_videos(source, max) → List[Video]
  - UnifiedCrawler.search_videos(keyword, max, owner_mid_filter) → List[Video]
"""
from __future__ import annotations

import re
from typing import List, Optional

from .crawler_bili import (
    BiliCrawler,
    BiliCrawlerError,
    parse_bvid,
    parse_mid,
    source_from_text,
)
from .crawler_youtube import YouTubeCrawler, YTCrawlerError
from .models import Source, Video


class UnifiedCrawler:
    """
    Thin router that holds one BiliCrawler and one optional YouTubeCrawler
    and delegates each call to the right backend based on Source.platform.
    """

    def __init__(
        self,
        bili_cookie: str = "",
        yt_api_key: str = "",
        enrich_tags: bool = False,
        pause_seconds: float = 0.8,
    ) -> None:
        self._bili = BiliCrawler(
            cookie=bili_cookie,
            enrich_tags=enrich_tags,
            pause_seconds=pause_seconds,
        )
        self._yt: Optional[YouTubeCrawler] = (
            YouTubeCrawler(api_key=yt_api_key) if yt_api_key else None
        )

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def _is_youtube(self, source: Source) -> bool:
        return source.platform == "youtube" or bool(source.channel_id)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def resolve_source(self, source: Source) -> Source:
        if self._is_youtube(source):
            if self._yt:
                return self._yt.resolve_source(source)
            return source
        return self._bili.resolve_source(source)

    def fetch_video(self, bvid: str, source: Optional[Source] = None, query: str = "") -> Video:
        """Fetch a single Bilibili video by BV ID."""
        return self._bili.fetch_video(bvid, source, query)

    def fetch_up_videos(self, source: Source, max_per_source: int = 20) -> List[Video]:
        if self._is_youtube(source):
            if not self._yt:
                raise YTCrawlerError("YouTube API key not configured. Pass --yt-key or set YT_API_KEY env var.")
            return self._yt.fetch_channel_videos(source, max_per_source)
        return self._bili.fetch_up_videos(source, max_per_source)

    def search_videos(
        self,
        keyword: str,
        max_results: int = 20,
        owner_mid_filter: Optional[set] = None,
        platform: str = "bilibili",
    ) -> List[Video]:
        """
        Search across a single platform.

        platform: "bilibili" | "youtube" | "all"
        When platform="all", searches both and merges results.
        """
        results: List[Video] = []
        if platform in {"bilibili", "all"}:
            bili_results = self._bili.search_videos(keyword, max_results, owner_mid_filter)
            results.extend(bili_results)
        if platform in {"youtube", "all"}:
            if self._yt:
                yt_results = self._yt.search_videos(keyword, max_results)
                results.extend(yt_results)
        return results
