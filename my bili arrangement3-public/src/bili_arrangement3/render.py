from __future__ import annotations

from pathlib import Path
from typing import Dict

from .models import CandidatePack, DraftPlan, PlanRequest


def render_brief_markdown(
    request: PlanRequest,
    pack_path: Path,
    prompt_path: Path,
    draft_path: Path,
    stats: Dict[str, object],
) -> str:
    lines = [
        f"# {request.topic or 'B 站内容'}候选池说明",
        "",
        "## 运行结果",
        f"- 候选池：{pack_path}",
        f"- AI 编排 prompt：{prompt_path}",
        f"- 本地 draft：{draft_path}",
        "",
        "## 用户选择",
        f"- 范围：{request.scope}",
        f"- 更新权重：{request.freshness}",
        f"- 天数：{request.days}",
        f"- 两周去重窗口：{request.dedupe_window_days} 天",
        f"- 过滤商业内容：{request.exclude_commercial}",
        f"- 过滤付费/充电/会员内容：{request.exclude_restricted}",
        "",
        "## 候选概况",
    ]
    for key, value in stats.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def render_draft_markdown(plan: DraftPlan) -> str:
    topic = plan.request.topic or "B 站内容"
    lines = [
        f"# {topic}一周安排 draft",
        "",
        "## 编排思路",
        plan.strategy,
        "",
        "这是一份本地启发式 draft。正式使用时，建议让 skill 读取 `pack.json` 和 `prompt.md` 后再做一次 AI 编排。",
        "",
        "## 每日安排",
    ]
    for item in plan.items:
        lines.extend(["", f"### {item.day_name}｜{item.date_label}｜{item.slot_title}", ""])
        lines.append(f"安排意图：{item.intent}")
        if not item.video:
            lines.extend(["", "视频：暂无匹配内容", f"理由：{item.reason}"])
            continue
        video = item.video
        lines.extend(["", f"视频：[{video.title}]({video.url})"])
        if video.owner_name:
            lines.append(f"UP 主：{video.owner_name}")
        if video.publish_date:
            lines.append(f"发布时间：{video.publish_date.isoformat()}")
        if video.duration_minutes:
            lines.append(f"时长：约 {video.duration_minutes} 分钟")
        if video.tags:
            lines.append("标签：" + "、".join(video.tags[:8]))
        if video.matched_queries:
            lines.append("命中关键词：" + "、".join(video.matched_queries))
        lines.append(f"推荐理由：{item.reason}")
        if item.alternatives:
            alt_titles = "；".join(f"[{alt.title}]({alt.url})" for alt in item.alternatives[:2])
            lines.append(f"备选：{alt_titles}")
    return "\n".join(lines) + "\n"


def pack_to_dict(pack: CandidatePack) -> Dict[str, object]:
    return {
        "run_id": pack.run_id,
        "generated_at": pack.generated_at,
        "request": pack.request.__dict__,
        "stats": pack.stats,
        "sources": [source.to_dict() for source in pack.sources],
        "videos": [video.to_dict() for video in pack.videos],
    }

