from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

"""
crawler_bili.py
===============
Bilibili-specific crawler. Wraps the Bilibili API (search, UP space, single
video) and normalises results into the shared Video / Source dataclasses.
"""
from .filters import detect_commercial_content, detect_restricted_access
from .models import (
    Source,
    Video,
    dedupe_keep_order,
    parse_duration_text,
    slugify,
    utc_now_iso,
)


BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10,})")
MID_RE = re.compile(r"space\.bilibili\.com/(\d+)")

WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BiliCrawlerError(RuntimeError):
    pass


def parse_bvid(text: str) -> str:
    match = BVID_RE.search(text or "")
    return match.group(1) if match else ""


def parse_mid(text: str) -> str:
    raw = str(text or "").strip()
    match = MID_RE.search(raw)
    if match:
        return match.group(1)
    if raw.isdigit() and len(raw) >= 3:
        return raw
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.endswith("bilibili.com") and parts:
        for part in parts:
            if part.isdigit():
                return part
    return ""


def source_from_text(value: str, source_id: str = "") -> Source:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Source value cannot be empty")
    bvid = parse_bvid(text)
    if bvid:
        return Source(
            id=source_id or f"video-{bvid}",
            kind="video",
            name=bvid,
            url=f"https://www.bilibili.com/video/{bvid}",
            bvid=bvid,
            added_at=utc_now_iso(),
        )
    mid = parse_mid(text)
    if mid:
        return Source(
            id=source_id or f"up-{mid}",
            kind="up",
            name=f"UP {mid}",
            url=f"https://space.bilibili.com/{mid}",
            mid=mid,
            added_at=utc_now_iso(),
        )
    return Source(
        id=source_id or f"up-name-{slugify(text)}",
        kind="up",
        name=text,
        added_at=utc_now_iso(),
    )


class BiliCrawler:
    def __init__(
        self,
        cookie: str = "",
        timeout: int = 12,
        pause_seconds: float = 0.8,
        enrich_tags: bool = True,
    ) -> None:
        self.cookie = cookie
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.enrich_tags = enrich_tags
        self._wbi_mixin_key = ""

    def resolve_source(self, source: Source) -> Source:
        if source.kind != "up":
            return source
        if not source.mid:
            resolved = self.search_user(source.name or source.id)
            return replace(
                resolved,
                id=source.id,
                tags=source.tags,
                notes=source.notes,
                added_at=source.added_at,
            )
        try:
            return self.fetch_up_profile(source)
        except Exception:
            import sys
            print(f"[warn] 无法获取 UP {source.url} 主页信息，使用原始来源", file=sys.stderr)
            return source

    def fetch_followings(self, mid: str, max_count: int = 500) -> List[Source]:
        """
        Fetch the authenticated user's following list from Bilibili.
        Requires a valid cookie (logged-in session).
        Returns a list of Source objects with platform="bilibili", kind="up".
        """
        if not self.cookie:
            raise BiliCrawlerError("fetch_followings requires a logged-in cookie")
        sources: List[Source] = []
        page = 1
        while len(sources) < max_count:
            payload = self._get_json(
                "https://api.bilibili.com/x/relation/followings",
                {
                    "vmid": mid,
                    "pn": page,
                    "ps": 50,
                    "order": "desc",
                    "order_type": "attention",
                },
            )
            items = (payload.get("data") or {}).get("list") or []
            if not items:
                break
            for item in items:
                item_mid = str(item.get("mid") or "")
                name = str(item.get("uname") or "")
                if item_mid:
                    sources.append(Source(
                        id=f"up-{item_mid}",
                        kind="up",
                        platform="bilibili",
                        name=name,
                        url=f"https://space.bilibili.com/{item_mid}",
                        mid=item_mid,
                        added_at=utc_now_iso(),
                    ))
            total = (payload.get("data") or {}).get("total") or 0
            if len(sources) >= total or len(items) < 50:
                break
            page += 1
        return sources[:max_count]

    def get_self_mid(self) -> str:
        """Return the MID of the currently authenticated user."""
        data = self._get_json("https://api.bilibili.com/x/web-interface/nav", {})
        return str((data.get("data") or {}).get("mid") or "")

    def search_user(self, name: str) -> Source:
        payload = self._get_json(
            "https://api.bilibili.com/x/web-interface/search/type",
            {
                "search_type": "bili_user",
                "keyword": name,
                "page": 1,
                "page_size": 10,
            },
        )
        results = ((payload.get("data") or {}).get("result") or [])
        if not results:
            raise BiliCrawlerError(f"No Bilibili user matched: {name}")
        exact = [
            item
            for item in results
            if strip_html(str(item.get("uname") or "")).lower() == name.lower()
        ]
        picked = exact[0] if exact else results[0]
        mid = str(picked.get("mid") or "")
        if not mid:
            raise BiliCrawlerError(f"Bilibili user search returned no mid for: {name}")
        resolved_name = strip_html(str(picked.get("uname") or name))
        return Source(
            id=f"up-{mid}",
            kind="up",
            name=resolved_name,
            url=f"https://space.bilibili.com/{mid}",
            mid=mid,
            added_at=utc_now_iso(),
        )

    def fetch_up_profile(self, source: Source) -> Source:
        mid = source.mid or parse_mid(source.url)
        if not mid:
            raise BiliCrawlerError(f"Source has no mid: {source.id}")
        payload = self._get_json(
            "https://api.bilibili.com/x/web-interface/card",
            {"mid": mid, "photo": "false"},
        )
        card = ((payload.get("data") or {}).get("card") or {})
        name = str(card.get("name") or source.name or f"UP {mid}")
        return replace(source, name=name, url=source.url or f"https://space.bilibili.com/{mid}", mid=mid)

    def fetch_video(self, bvid: str, source: Optional[Source] = None, query: str = "") -> Video:
        if not bvid:
            raise BiliCrawlerError("Missing bvid")
        payload = self._get_json(
            "https://api.bilibili.com/x/web-interface/view",
            {"bvid": bvid},
        )
        data = payload.get("data") or {}
        if not data:
            raise BiliCrawlerError(f"Could not fetch video: {bvid}")
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        pubdate = data.get("pubdate")
        pubdate_text = (
            datetime.fromtimestamp(int(pubdate)).date().isoformat()
            if pubdate
            else ""
        )
        source_id = source.id if source else f"video-{bvid}"
        source_url = source.url if source else f"https://www.bilibili.com/video/{bvid}"
        video = Video(
            bvid=bvid,
            platform="bilibili",
            platform_id=bvid,
            title=str(data.get("title") or ""),
            url=f"https://www.bilibili.com/video/{bvid}",
            owner_name=str(owner.get("name") or ""),
            owner_mid=str(owner.get("mid") or ""),
            pubdate=pubdate_text,
            duration_seconds=int(data.get("duration") or 0),
            view_count=int(stat.get("view") or 0),
            desc=str(data.get("desc") or ""),
            tags=list(source.tags if source else []),
            source_id=source_id,
            source_url=source_url,
            source_kind=source.kind if source else "video",
            fetched_at=utc_now_iso(),
            matched_queries=[query] if query else [],
            matched_sources=[source_id],
        )
        return self._finalize_video(video, data)

    def fetch_up_videos(self, source: Source, max_per_source: int = 20) -> List[Video]:
        resolved = self.resolve_source(source)
        mid = resolved.mid or parse_mid(resolved.url)
        if not mid:
            raise BiliCrawlerError(f"Source has no mid: {source.id}")
        last_error = ""
        for url, params in self._up_video_endpoints(mid, max_per_source):
            try:
                payload = self._get_json(url, params)
                vlist = _extract_vlist(payload)
                if vlist:
                    return [
                        self._video_from_up_item(item, resolved)
                        for item in vlist[:max_per_source]
                    ]
            except Exception as exc:
                last_error = str(exc)
        raise BiliCrawlerError(f"Could not fetch UP videos for mid={mid}. Last error: {last_error}")

    def search_videos(
        self,
        keyword: str,
        max_results: int = 20,
        owner_mid_filter: Optional[set[str]] = None,
    ) -> List[Video]:
        if not keyword:
            return []
        payload = self._get_json(
            "https://api.bilibili.com/x/web-interface/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "page": 1,
                "page_size": max_results,
                "order": "pubdate",
            },
        )
        results = ((payload.get("data") or {}).get("result") or [])
        videos = [self._video_from_search_item(item, keyword) for item in results[:max_results]]
        if owner_mid_filter:
            videos = [video for video in videos if video.owner_mid in owner_mid_filter]
        return videos

    def fetch_tags(self, bvid: str) -> List[str]:
        if not bvid:
            return []
        try:
            payload = self._get_json(
                "https://api.bilibili.com/x/tag/archive/tags",
                {"bvid": bvid},
            )
        except Exception:
            import sys
            print(f"[warn] 获取视频 {bvid} 标签失败", file=sys.stderr)
            return []
        return [
            str(item.get("tag_name") or "")
            for item in (payload.get("data") or [])
            if item.get("tag_name")
        ]

    def _video_from_up_item(self, item: Dict[str, Any], source: Source) -> Video:
        bvid = str(item.get("bvid") or "")
        created = item.get("created") or item.get("pubdate")
        pubdate = (
            datetime.fromtimestamp(int(created)).date().isoformat()
            if created
            else ""
        )
        tags = dedupe_keep_order([str(item.get("typename") or ""), *source.tags])
        stat = item.get("stat") or {}
        video = Video(
            bvid=bvid,
            platform="bilibili",
            platform_id=bvid,
            title=strip_html(str(item.get("title") or "")),
            url=f"https://www.bilibili.com/video/{bvid}" if bvid else str(item.get("arcurl") or ""),
            owner_name=source.display_name,
            owner_mid=source.mid or parse_mid(source.url),
            pubdate=pubdate,
            duration_seconds=parse_duration_text(item.get("length") or item.get("duration") or ""),
            view_count=int(item.get("play") or stat.get("view") or 0),
            desc=strip_html(str(item.get("description") or item.get("desc") or "")),
            tags=tags,
            source_id=source.id,
            source_url=source.url,
            source_kind=source.kind,
            fetched_at=utc_now_iso(),
            matched_sources=[source.id],
        )
        return self._finalize_video(video, item)

    def _video_from_search_item(self, item: Dict[str, Any], keyword: str) -> Video:
        bvid = str(item.get("bvid") or "")
        pubdate = item.get("pubdate") or item.get("senddate")
        pubdate_text = (
            datetime.fromtimestamp(int(pubdate)).date().isoformat()
            if pubdate
            else ""
        )
        tags = dedupe_keep_order([str(item.get("typename") or ""), keyword])
        video = Video(
            bvid=bvid,
            platform="bilibili",
            platform_id=bvid,
            title=strip_html(str(item.get("title") or "")),
            url=f"https://www.bilibili.com/video/{bvid}" if bvid else str(item.get("arcurl") or ""),
            owner_name=strip_html(str(item.get("author") or "")),
            owner_mid=str(item.get("mid") or ""),
            pubdate=pubdate_text,
            duration_seconds=parse_duration_text(item.get("duration") or ""),
            view_count=int(item.get("play") or item.get("view") or 0),
            desc=strip_html(str(item.get("description") or "")),
            tags=tags,
            source_id=f"search-{slugify(keyword)}",
            source_url=f"bilibili-search:{keyword}",
            source_kind="keyword",
            fetched_at=utc_now_iso(),
            matched_queries=[keyword],
            matched_sources=[f"search:{keyword}"],
        )
        return self._finalize_video(video, item)

    def _finalize_video(self, video: Video, metadata: Any) -> Video:
        # enrich_tags is disabled by default to avoid per-video API calls that dominate runtime
        tags = video.tags
        restricted, access_notes = detect_restricted_access(
            metadata,
            [video.title, video.desc, " ".join(tags)],
        )
        # Also re-check the rights sub-dict directly if present (handles ugc_pay etc.)
        if isinstance(metadata, dict):
            rights = metadata.get("rights") or {}
            pay_flags = {k for k, v in rights.items() if v and any(
                kw in k.lower() for kw in ["pay", "charge", "upower", "premium", "vip"]
            )}
            if pay_flags:
                restricted = True
                access_notes = dedupe_keep_order([*access_notes, *[f"rights.{k}" for k in pay_flags]])
        video = replace(
            video,
            tags=tags,
            restricted_access=restricted,
            access_notes=access_notes,
        )
        commercial, commercial_notes = detect_commercial_content(video)
        return replace(video, commercial=commercial, commercial_notes=commercial_notes)

    def _up_video_endpoints(self, mid: str, max_per_source: int) -> List[tuple[str, Dict[str, Any]]]:
        params = {
            "mid": mid,
            "pn": 1,
            "ps": max_per_source,
            "order": "pubdate",
            "platform": "web",
            "web_location": "1550101",
        }
        endpoints = [
            ("https://api.bilibili.com/x/space/arc/search", {"mid": mid, "pn": 1, "ps": max_per_source, "order": "pubdate"}),
        ]
        try:
            endpoints.append(
                (
                    "https://api.bilibili.com/x/space/wbi/arc/search",
                    self._sign_wbi_params(params),
                )
            )
        except Exception:
            import sys
            print(f"[warn] WBI 签名失败，使用非签名端点", file=sys.stderr)
        return endpoints

    def check_video_accessible(self, bvid: str) -> bool:
        """
        Return True if the video exists and is publicly accessible.
        Uses a lightweight API call (view info) and checks for:
        - code != 0  (deleted / banned)
        - redirects or empty data
        - title is empty or '[已失效]'
        - rights dict indicating payment/charging (ugc_pay, pay, charging etc.)
        """
        if not bvid:
            return False
        try:
            payload = self._get_json(
                "https://api.bilibili.com/x/web-interface/view",
                {"bvid": bvid},
            )
            data = payload.get("data") or {}
            if not data:
                return False
            title = str(data.get("title") or "")
            # Bilibili marks deleted videos with these titles
            if not title or "已失效" in title or "视频去哪了" in title or "稿件不可见" in title:
                return False
            # Check rights dict for payment/charging flags
            rights = data.get("rights") or {}
            if rights.get("pay") or rights.get("ugc_pay") or rights.get("is_charging_arc"):
                return False
            # Check ugc_pay sub-object (nested payment info)
            ugc_pay = data.get("ugc_pay") or {}
            if ugc_pay.get("is_ugc_pay") or ugc_pay.get("pay_type"):
                return False
            return True
        except BiliCrawlerError:
            return False
        except Exception:
            import sys
            print(f"[warn] 检查视频 {bvid} 可访问性失败", file=sys.stderr)
            return False

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        time.sleep(self.pause_seconds)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = Request(url, headers=headers)
        backoff = 1.0
        for attempt in range(4):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code in (412, 429) and attempt < 3:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                raise BiliCrawlerError(f"HTTP {exc.code}: {body[:180]}") from exc
            except URLError as exc:
                if attempt < 3:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise BiliCrawlerError(f"Network error: {exc}") from exc
            break
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise BiliCrawlerError("Unexpected non-object JSON response")
        code = payload.get("code", 0)
        if code not in (0, "0", None):
            raise BiliCrawlerError(str(payload.get("message") or payload.get("msg") or payload))
        return payload

    def _sign_wbi_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mixin_key = self._get_wbi_mixin_key()
        signed = dict(params)
        signed["wts"] = int(time.time())
        cleaned = {
            key: _sanitize_wbi_value(value)
            for key, value in sorted(signed.items(), key=lambda item: item[0])
        }
        query = urlencode(cleaned)
        signed["wrid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed

    def _get_wbi_mixin_key(self) -> str:
        if self._wbi_mixin_key:
            return self._wbi_mixin_key
        payload = self._get_json("https://api.bilibili.com/x/web-interface/nav")
        wbi_img = ((payload.get("data") or {}).get("wbi_img") or {})
        img_key = _extract_wbi_key(str(wbi_img.get("img_url") or ""))
        sub_key = _extract_wbi_key(str(wbi_img.get("sub_url") or ""))
        raw_key = img_key + sub_key
        if len(raw_key) < 64:
            raise BiliCrawlerError("Could not obtain WBI key")
        self._wbi_mixin_key = "".join(raw_key[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]
        return self._wbi_mixin_key


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return (
        text.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )


def _extract_vlist(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    list_obj = data.get("list") or {}
    if isinstance(list_obj, dict) and isinstance(list_obj.get("vlist"), list):
        return list_obj["vlist"]
    if isinstance(data.get("vlist"), list):
        return data["vlist"]
    if isinstance(list_obj, list):
        return list_obj
    return []


def _extract_wbi_key(url: str) -> str:
    filename = urlparse(url).path.rsplit("/", 1)[-1]
    return filename.split(".", 1)[0]


def _sanitize_wbi_value(value: Any) -> str:
    return re.sub(r"[!'()*]", "", str(value))

