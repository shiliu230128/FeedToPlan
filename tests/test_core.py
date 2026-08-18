from __future__ import annotations

import tempfile
import unittest
from datetime import date
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

        skill_md = format_doc_path().parent.parent / "SKILL.md"
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


if __name__ == "__main__":
    unittest.main()
