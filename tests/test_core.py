from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from bili_arrangement3.cli import filter_videos_by_sources
from bili_arrangement3.filters import (
    detect_commercial_content,
    detect_restricted_access,
    detect_yoga_follow_along,
    refresh_video_flags,
)
from bili_arrangement3.models import PlanRequest, Source, Video
from bili_arrangement3.planner import build_candidate_stats, draft_weekly_plan, select_slots
from bili_arrangement3.prompts import build_ai_prompt
from bili_arrangement3.storage import record_recent_usage, recent_bvids, save_json, load_json, upsert_videos_jsonl


class CoreTests(unittest.TestCase):
    def sample_videos(self):
        return [
            Video(
                bvid="BV1111111111",
                title="20分钟晨间瑜伽唤醒",
                url="https://www.bilibili.com/video/BV1111111111",
                owner_name="Yoga A",
                pubdate="2026-07-10",
                duration_seconds=1200,
                desc="适合早晨的舒展流动",
                tags=["瑜伽", "晨间"],
            ),
            Video(
                bvid="BV2222222222",
                title="肩颈放松跟练",
                url="https://www.bilibili.com/video/BV2222222222",
                owner_name="Yoga B",
                pubdate="2026-07-12",
                duration_seconds=900,
                desc="久坐肩颈修复",
                tags=["瑜伽", "肩颈"],
            ),
            Video(
                bvid="BV3333333333",
                title="核心力量流瑜伽",
                url="https://www.bilibili.com/video/BV3333333333",
                owner_name="Yoga C",
                pubdate="2026-07-14",
                duration_seconds=1800,
                desc="核心稳定和全身流动",
                tags=["瑜伽", "核心"],
            ),
        ]

    def test_restricted_detection(self):
        restricted, notes = detect_restricted_access(
            {"badge": {"text": "付费专享"}, "rights": {"ugc_pay": 1}},
            ["会员专享内容"],
        )
        self.assertTrue(restricted)
        self.assertTrue(notes)

    def test_commercial_detection(self):
        commercial, notes = detect_commercial_content(
            Video(
                bvid="BVx",
                title="广告合作课程",
                url="https://example.com",
                owner_name="某某工作室",
                desc="立即报名",
            )
        )
        self.assertTrue(commercial)
        self.assertTrue(notes)

    def test_yoga_follow_along_detection(self):
        intro = Video(
            bvid="BVintro",
            title="【心月粉·基础理疗】关于档位介绍&练习答疑",
            url="https://example.com/intro",
            desc="一期有关基础理疗的档位合集介绍",
            tags=["瑜伽", "跟练"],
        )
        practice = Video(
            bvid="BVpractice",
            title="【13min直腿拉伸】改善腿型 放松下肢 久坐人群一定要练",
            url="https://example.com/practice",
            desc="一期深度拉伸腿部的跟练",
            tags=["瑜伽", "跟练"],
        )
        self.assertFalse(detect_yoga_follow_along(intro)[0])
        self.assertTrue(detect_yoga_follow_along(practice)[0])

    def test_cached_video_flags_are_refreshed(self):
        video = Video(
            bvid="BVcharge",
            title="20分钟气血瑜伽",
            url="https://example.com/charge",
            desc="成为包月充电会员，支持创作并解锁更多专属视频。使用我的推荐链接。",
            tags=["瑜伽", "跟练"],
        )
        refreshed = refresh_video_flags(video)
        self.assertTrue(refreshed.restricted_access)
        self.assertTrue(refreshed.commercial)

    def test_yoga_draft_skips_intro_video(self):
        request = PlanRequest(topic="瑜伽", template="yoga", freshness="latest", days=1)
        intro = Video(
            bvid="BVintro",
            title="关于档位介绍&练习答疑",
            url="https://example.com/intro",
            pubdate="2026-07-14",
            tags=["瑜伽", "跟练"],
        )
        practice = Video(
            bvid="BVpractice",
            title="20分钟晨间瑜伽唤醒跟练",
            url="https://example.com/practice",
            pubdate="2026-07-10",
            duration_seconds=1200,
            tags=["瑜伽", "跟练"],
        )
        plan = draft_weekly_plan([intro, practice], request, today=date(2026, 7, 16))
        self.assertEqual(plan.items[0].video.bvid, "BVpractice")

    def test_cache_fallback_respects_source_filter(self):
        videos = [
            Video(bvid="BV1", title="A", url="https://example.com/1", owner_mid="543976958"),
            Video(bvid="BV2", title="B", url="https://example.com/2", owner_mid="62540916"),
        ]
        sources = [Source(id="up-543976958", mid="543976958")]
        filtered = filter_videos_by_sources(videos, sources)
        self.assertEqual([video.bvid for video in filtered], ["BV1"])

    def test_plan_draft_renders_week(self):
        request = PlanRequest(topic="瑜伽", template="yoga", freshness="latest", days=7)
        plan = draft_weekly_plan(self.sample_videos(), request)
        self.assertEqual(len(plan.items), 7)
        self.assertIn("瑜伽", plan.strategy)

    def test_prompt_mentions_pack(self):
        request = PlanRequest(topic="瑜伽", freshness="latest")
        prompt = build_ai_prompt(request, Path("/tmp/pack.json"), {"total": 3}, select_slots(request))
        self.assertIn("pack.json", prompt)
        self.assertIn("两周去重", prompt)

    def test_output_format_is_injected_from_reference_doc(self):
        """The reference doc is the only definition of the reply format."""
        from bili_arrangement3.prompts import format_doc_path, load_output_format

        self.assertIsNotNone(format_doc_path(), "references/planning_prompt.md not found")
        block = load_output_format()
        self.assertIn("使用的 skill 与路径", block)
        request = PlanRequest(topic="瑜伽", freshness="latest")
        prompt = build_ai_prompt(request, Path("/tmp/pack.json"), {"total": 3}, select_slots(request))
        self.assertIn(block, prompt)

    def test_skill_md_points_at_the_format_doc_instead_of_restating_it(self):
        from bili_arrangement3.prompts import format_doc_path

        # references/ sits directly under the skill root, which is the project root.
        skill_root = format_doc_path().parent.parent
        skill_md = skill_root / "SKILL.md"
        self.assertTrue(skill_md.is_file(), f"SKILL.md not found next to {skill_root}")
        text = skill_md.read_text(encoding="utf-8")
        self.assertIn("references/planning_prompt.md", text)
        self.assertNotIn("## 每日安排", text)

    def test_topic_defaults_stay_inside_topic_and_window(self):
        from bili_arrangement3.user_memory import append_episodic, get_topic_defaults

        memory = {"schema_version": 3, "episodic": [], "update_policy": {}}
        append_episodic(memory, "瑜伽", "膝盖避免深蹲", {"duration_min": 20, "duration_max": 40})
        append_episodic(memory, "专注音乐", "不要人声", {"duration_min": 30})

        music = get_topic_defaults(memory, "专注音乐")
        self.assertEqual(music["notes"], "不要人声")
        self.assertEqual(music["duration_min"], 30)
        self.assertNotIn("duration_max", music)

        memory["episodic"][0]["date"] = "2020-01-01"
        self.assertEqual(get_topic_defaults(memory, "瑜伽"), {})

    def test_migration_drops_semantic_and_procedural_tiers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from bili_arrangement3.user_memory import load_memory

            path = Path(tmpdir) / "user_memory.json"
            save_json(path, {
                "schema_version": 2,
                "semantic": {"domains": {"瑜伽": {"goal": "缓解肩颈久坐"}}},
                "procedural": {"瑜伽": {"scope": "mixed"}},
                "episodic": [{"date": "2026-08-17", "topic": "瑜伽", "user_note": "舒缓恢复"}],
            })
            memory = load_memory(path)
            self.assertEqual(memory["schema_version"], 3)
            self.assertNotIn("semantic", memory)
            self.assertNotIn("procedural", memory)
            self.assertEqual(len(memory["episodic"]), 1)

    def test_recent_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            save_json(state_path, {"recently_used": []})
            state = load_json(state_path, {})
            record_recent_usage(state, self.sample_videos()[:1], "run-1", "瑜伽")
            self.assertIn("bilibili:BV1111111111", recent_bvids(state, 14))

    def test_recent_history_matches_both_bvid_and_fingerprint(self):
        """
        The candidate-pool filter looked up bare bvids while history stored
        namespaced fingerprints, so removed_recent was always 0. Both spellings
        must resolve.
        """
        state = {"recently_used": []}
        video = self.sample_videos()[0]
        record_recent_usage(state, [video], "run-1", "瑜伽")
        recent = recent_bvids(state, 14)
        self.assertIn(video.fingerprint, recent)
        self.assertIn(video.bvid, recent)
        self.assertTrue(
            (video.fingerprint and video.fingerprint in recent)
            or (video.bvid and video.bvid in recent)
        )

    def test_jsonl_upsert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "videos.jsonl"
            upsert_videos_jsonl(path, self.sample_videos()[:1])
            updated = self.sample_videos()[:1]
            updated[0].title = "更新后的标题"
            upsert_videos_jsonl(path, updated)
            self.assertIn("更新后的标题", path.read_text(encoding="utf-8"))

    def test_wizard_keeps_its_own_cli_flags(self):
        """
        `wizard --offline --days 3 --platform all` used to be silently reset to
        `plan` defaults because the namespace was rebuilt from parse_args(["plan"]).
        """
        from unittest import mock

        from bili_arrangement3 import cli

        args = cli.build_parser().parse_args(
            ["wizard", "--offline", "--days", "3", "--platform", "all"]
        )
        captured = {}

        def fake_plan(namespace):
            captured["namespace"] = namespace
            return 0

        with mock.patch.object(cli, "cmd_plan", fake_plan), mock.patch(
            "builtins.input", return_value=""
        ):
            cli.cmd_wizard(args)

        namespace = captured["namespace"]
        self.assertTrue(namespace.offline, "--offline was dropped by wizard")
        self.assertEqual(namespace.days, 3, "--days was dropped by wizard")
        self.assertEqual(namespace.platform, "all", "--platform was dropped by wizard")
        self.assertEqual(namespace.scope, "mixed")   # wizard default choice B
        self.assertEqual(namespace.topic, "瑜伽")     # wizard default topic
        self.assertTrue(namespace.record)            # plan subcommand default

    def test_source_refresh_gate_trips_on_runs_or_days(self):
        """一次抓取后，只有「满 6 次调用」或「满 10 天」才需要重新拉取。"""
        from bili_arrangement3.storage import (
            bump_source_skipped,
            mark_source_fetched,
            source_is_due,
        )

        self.assertTrue(source_is_due(None), "从未抓取过的源必须立即拉取")

        fresh = mark_source_fetched()
        self.assertFalse(source_is_due(fresh, 6, 10), "刚抓完不应再拉")

        self.assertFalse(source_is_due({**fresh, "runs_since_fetch": 5}, 6, 10))
        self.assertTrue(source_is_due({**fresh, "runs_since_fetch": 6}, 6, 10), "满 6 次调用应重新拉取")

        just_inside = (date.today() - timedelta(days=9)).isoformat()
        just_outside = (date.today() - timedelta(days=10)).isoformat()
        self.assertFalse(source_is_due({"last_fetched_at": just_inside, "runs_since_fetch": 0}, 6, 10))
        self.assertTrue(source_is_due({"last_fetched_at": just_outside, "runs_since_fetch": 0}, 6, 10), "满 10 天应重新拉取")

        # 阈值设为 0 表示停用该条件
        self.assertFalse(source_is_due({**fresh, "runs_since_fetch": 99}, 0, 10))
        self.assertFalse(source_is_due({**fresh, "runs_since_fetch": 99}, 0, 0), "两个阈值都关闭则永不重拉")

        bumped = bump_source_skipped(fresh)
        self.assertEqual(bumped["runs_since_fetch"], 1, "跳过一次应把计数 +1")
        self.assertEqual(bump_source_skipped(None)["runs_since_fetch"], 1)

    def test_followed_source_gets_scorer_boost(self):
        """
        cli.py 写入 request.followed_mids，planner 却读 request._followed_mids，
        关注源 +2.0 的加权因此从未生效。两条只差关注关系的视频必须拉开 2.0 分。
        """
        from bili_arrangement3.planner import _rank_candidates

        request = PlanRequest(topic="瑜伽", freshness="latest", days=7, scope="mixed")
        request.followed_mids = ["543976958"]
        slot = select_slots(request)[0]
        followed = Video(
            bvid="BV1", title="瑜伽跟练", url="u",
            owner_mid="543976958", owner_name="YoginiYue", pubdate="2026-10-01",
        )
        stranger = Video(
            bvid="BV2", title="瑜伽跟练", url="u",
            owner_mid="999999", owner_name="路人UP", pubdate="2026-10-01",
        )

        ranked = _rank_candidates([stranger, followed], request, slot, None, None, set(), date.today())
        self.assertEqual(ranked[0][0].owner_mid, "543976958", "关注源应排在前面")
        self.assertAlmostEqual(ranked[0][1] - ranked[1][1], 2.0, places=5)

        # 只关注列表的 scope 下不加权（池子里全是关注源，加权没有区分度）
        request.scope = "following"
        ranked_same = _rank_candidates([stranger, followed], request, slot, None, None, set(), date.today())
        self.assertAlmostEqual(ranked_same[0][1] - ranked_same[1][1], 0.0, places=5)

    def test_https_context_loads_a_ca_bundle(self):
        """
        python.org 的 Python 在跑过 Install Certificates.command 之前没有任何 CA 包，
        所有 HTTPS 请求都会 CERTIFICATE_VERIFY_FAILED；而爬虫把 URLError 当可重试错误，
        于是一个缺失的证书包会被放大成长时间的空转重试。
        """
        import ssl

        from bili_arrangement3 import net

        bundle = net.ca_bundle()
        if bundle is not None:
            self.assertTrue(Path(bundle).is_file(), f"CA 包路径不存在: {bundle}")

        ctx = net.ssl_context()
        self.assertIsInstance(ctx, ssl.SSLContext)
        # 没有任何根证书 = 证书链验证必然失败
        self.assertGreater(
            ctx.cert_store_stats().get("x509_ca", 0),
            0,
            "SSL context 没有加载任何根证书，HTTPS 请求会全部失败",
        )

    def test_wbi_signature_sends_w_rid_not_wrid(self):
        """
        B站 要求 WBI 签名的字段名是 `w_rid`（带下划线）。写成 `wrid` 等于把签名丢掉，
        空间投稿接口会返回「访问权限不足」，于是所有被关注 UP 的投稿都抓不到。
        """
        from bili_arrangement3.crawler_bili import BiliCrawler

        crawler = BiliCrawler(cookie="SESSDATA=dummy", pause_seconds=0)
        # 塞一个固定的 mixin key，跳过取 key 的那次网络请求
        crawler._wbi_mixin_key = "0123456789abcdef0123456789abcdef"
        signed = crawler._sign_wbi_params({"mid": "1", "ps": 5})

        self.assertIn("w_rid", signed, "签名字段必须是 w_rid")
        self.assertNotIn("wrid", signed, "wrid 不是合法字段名，B站 会忽略它")
        self.assertIn("wts", signed, "签名必须带时间戳 wts")
        self.assertEqual(len(signed["w_rid"]), 32)
        self.assertEqual(signed["mid"], "1")

    def test_up_video_endpoints_tries_signed_endpoint_first(self):
        """
        非签名的 /x/space/arc/search 现在对每个源都返回「访问权限不足」，
        先试它等于每个源白跑一次往返（17 个源约 16 秒）。签名端点必须排第一。
        """
        from bili_arrangement3.crawler_bili import BiliCrawler

        crawler = BiliCrawler(cookie="x", pause_seconds=0)
        crawler._wbi_mixin_key = "0123456789abcdef0123456789abcdef"
        endpoints = crawler._up_video_endpoints("123", 5)

        self.assertTrue(endpoints[0][0].endswith("/x/space/wbi/arc/search"), endpoints[0][0])
        self.assertIn("w_rid", endpoints[0][1], "签名端点必须带 w_rid")
        self.assertEqual(len(endpoints), 2)
        self.assertTrue(endpoints[1][0].endswith("/x/space/arc/search"), "签名失败时才回落")

    def test_resolve_source_fetches_profile_once_per_mid(self):
        """
        collect_pack 先解析一遍所有源，fetch_up_videos 内部又解析一遍，
        同一个 mid 因此请求两次 UP 主页（17 个源 = 17 次多余请求）。
        """
        from dataclasses import replace as dc_replace

        from bili_arrangement3.crawler_bili import BiliCrawler

        crawler = BiliCrawler(cookie="x", pause_seconds=0)
        asked: list = []

        def fake_profile(source):
            asked.append(source.mid)
            return dc_replace(source, name="解析后的名字")

        crawler.fetch_up_profile = fake_profile
        # 没有名字的源必须解析
        nameless = Source(id="up-1", kind="up", mid="1")

        first = crawler.resolve_source(nameless)
        second = crawler.resolve_source(nameless)

        self.assertEqual(asked, ["1"], "同一个 mid 不应重复请求 UP 主页")
        self.assertEqual(first.name, "解析后的名字")
        self.assertEqual(second.name, "解析后的名字")
        self.assertEqual(second.id, "up-1")

    def test_resolve_source_skips_profile_call_when_name_known(self):
        """
        config/sources.json 里已经有 mid 和名字，再查一次主页只是重复请求。
        实测每个源一次，17 个源就是 17 次多余往返（约 16 秒）。
        """
        from bili_arrangement3.crawler_bili import BiliCrawler

        crawler = BiliCrawler(cookie="x", pause_seconds=0)
        asked: list = []
        crawler.fetch_up_profile = lambda source: (asked.append(source.mid) or source)

        resolved = crawler.resolve_source(Source(id="up-1", kind="up", mid="1", name="已知名字"))

        self.assertEqual(asked, [], "已知名字时不该请求 UP 主页")
        self.assertEqual(resolved.name, "已知名字")
        self.assertEqual(resolved.url, "https://space.bilibili.com/1")

    def test_fully_cached_run_skips_accessibility_probes(self):
        """
        所有源都在刷新节奏内时，本轮不该发出任何可访问性校验请求。
        fresh_keys 曾经在缓存回退之后计算，于是一轮「零抓取」的运行仍然
        对每个候选各发一次请求（实测 144 次 / 约 135 秒，占总耗时 67%）。
        """
        import os
        from unittest import mock

        from bili_arrangement3 import cli

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for sub in ("config", "data/cache", "outputs/runs", ".secrets"):
                (root / sub).mkdir(parents=True, exist_ok=True)
            (root / "config/sources.json").write_text(
                json.dumps({"sources": [{"id": "up-1", "kind": "up", "mid": "1", "name": "A", "enabled": True}]}),
                encoding="utf-8",
            )
            (root / "config/preferences.json").write_text(
                json.dumps({"source_refresh_every_runs": 6, "source_refresh_every_days": 10}),
                encoding="utf-8",
            )
            cached = Video(
                bvid="BV1", title="20分钟晚间舒缓瑜伽跟练", url="https://www.bilibili.com/video/BV1",
                owner_mid="1", owner_name="A", pubdate="2026-10-01",
                duration_seconds=1200, fetched_at=date.today().isoformat(), source_id="up-1",
            )
            (root / "data/cache/videos.jsonl").write_text(
                json.dumps(cached.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8",
            )
            # 这个源刚抓过，本轮不该再抓，也不该校验它的视频
            (root / "data/cache/source_sync.json").write_text(
                json.dumps({"up-1": {"last_fetched_at": date.today().isoformat(), "runs_since_fetch": 0}}),
                encoding="utf-8",
            )

            probes: list = []

            class FakeBili:
                def check_video_accessible(self, bvid):
                    probes.append(bvid)
                    return True

            class FakeCrawler:
                def __init__(self, *a, **k):
                    self._bili = FakeBili()

                def resolve_source(self, source):
                    return source

                def fetch_up_videos(self, *a, **k):
                    raise AssertionError("源都在节奏内，不应发起抓取")

                def search_videos(self, *a, **k):
                    return []

            args = cli.build_parser().parse_args(["plan", "--scope", "following"])
            args.sources = root / "config/sources.json"
            args.preferences = root / "config/preferences.json"
            args.videos = root / "data/cache/videos.jsonl"
            args.state = root / "data/state.json"
            args.cookie_file = root / ".secrets/bilibili_cookie.txt"

            with mock.patch.dict(os.environ, {"BILI_ARRANGEMENT3_ROOT": str(root), "OPENAI_API_KEY": ""}), \
                    mock.patch.object(cli, "UnifiedCrawler", FakeCrawler), \
                    mock.patch.object(cli, "DEFAULT_USER_MEMORY", root / "data/user_memory.json"):
                # DEFAULT_USER_MEMORY 是模块级常量，指向真实项目。不隔离的话
                # build_request 会把真实记忆里的 notes 预填进来，再经
                # apply_filter_dimensions 原地改写 topic_profile 的模块级缓存，
                # 污染同一进程里后续的测试。
                pack, _ = cli.collect_pack(args)

            self.assertEqual(probes, [], "缓存里的视频不该再做可访问性校验")
            self.assertEqual(pack.stats["sources_refreshed"], 0)
            self.assertEqual(pack.stats["sources_from_cache"], 1)
            self.assertEqual(len(pack.videos), 1)


if __name__ == "__main__":
    unittest.main()
