from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from .models import PlanRequest, Slot
from .paths import project_root

FORMAT_DOC_RELATIVE = Path("references") / "planning_prompt.md"
FORMAT_BLOCK_BEGIN = "<!-- OUTPUT_FORMAT:BEGIN -->"
FORMAT_BLOCK_END = "<!-- OUTPUT_FORMAT:END -->"
_FORMAT_MISSING = (
    "## 输出格式\n\n"
    "没找到 references/planning_prompt.md 里的 OUTPUT_FORMAT 段落，无法注入输出格式。"
    "回复前先读那个文件，按其中的五段格式输出。"
)


def format_doc_path() -> Optional[Path]:
    """Locate references/planning_prompt.md in either the skill or public layout."""
    here = Path(__file__).resolve()
    candidates = [
        project_root() / FORMAT_DOC_RELATIVE,
        project_root() / "skill" / "bilibili-arrangement3" / FORMAT_DOC_RELATIVE,
        here.parents[2] / FORMAT_DOC_RELATIVE,
        here.parents[2] / "skill" / "bilibili-arrangement3" / FORMAT_DOC_RELATIVE,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_output_format() -> str:
    """
    Read the output-format section out of references/planning_prompt.md.
    That file is the single source of truth — SKILL.md points at it and this
    prompt embeds it verbatim, so the three cannot drift apart.
    """
    path = format_doc_path()
    if path is None:
        return _FORMAT_MISSING
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _FORMAT_MISSING
    start = text.find(FORMAT_BLOCK_BEGIN)
    end = text.find(FORMAT_BLOCK_END)
    if start == -1 or end == -1 or end < start:
        return _FORMAT_MISSING
    return text[start + len(FORMAT_BLOCK_BEGIN):end].strip()


def _candidate_ratio_text(stats: Dict[str, object], request: PlanRequest) -> str:
    eligible = stats.get("eligible_count", 0)
    if not isinstance(eligible, (int, float)) or eligible == 0:
        return "无法计算"
    ratio = eligible / max(1, request.days)
    suffix = "✓ 充足" if ratio >= 3.0 else "⚠️ 不足 3 倍，编排质量可能受限"
    return f"{ratio:.1f}x — {suffix}"


WIZARD_PROMPT = """你想怎么整理这次 B 站内容？

1. 内容范围
A. 只从我的关注列表里选
B. 关注列表 + 主题关键词一起混合
C. 只按主题关键词全站搜索
D. 我直接提供 UP 名/主页链接/视频链接

2. 更新权重
A. 最新优先（默认）
B. 新旧均衡
C. 历史补全优先

3. 约束
A. 默认过滤商业内容、充电专属、付费/会员内容
B. 额外限制单个视频时长
C. 额外排除某些 UP 或关键词
D. 不过滤，仅整理候选池

4. 产出
A. 一周安排（默认）
B. 先只生成候选池和 AI 编排 prompt

补充说明：
- 如果你要的是瑜伽跟练，默认会优先真正可练的内容，介绍、答疑、档位说明、课程说明会被跳过。
- 你可以只给关注列表，也可以只给主题，也可以两者一起给。
"""


def build_ai_prompt(
    request: PlanRequest,
    pack_path: Path,
    stats: Dict[str, object],
    slots: Sequence[Slot],
) -> str:
    slot_names = "、".join(slot.title for slot in slots[: request.days])
    output_format = load_output_format()
    user_context_block = ""
    if request.notes:
        user_context_block = f"""
## 用户当前状态与约束

{request.notes}

在选择和编排内容时，必须优先符合上述状态和约束，不符合的视频宁可跳过，不要为了凑数而推荐。
"""
    return f"""# Bilibili AI 编排任务

你会读取候选池 JSON，然后输出一份可以直接执行的 B 站内容安排。

候选池文件：
{pack_path}
{user_context_block}
## 用户选择

- 主题：{request.topic or "未指定"}
- 范围：{request.scope}
- 更新权重：{request.freshness}
- 天数：{request.days}
- 两周去重：最近 {request.dedupe_window_days} 天内用过的视频不要重复
- 默认过滤：商业内容={request.exclude_commercial}，付费/充电/会员内容={request.exclude_restricted}
- 额外关键词：{"、".join(request.keywords) if request.keywords else "无"}
- 候选概况：{stats}
- 最低候选倍率：候选池数量应至少是输出天数的 3 倍（当前 {stats.get('eligible_count', '?')} 候选 / {request.days} 天 = {_candidate_ratio_text(stats, request)}）

## 编排原则

1. 先排除候选池里标记为 commercial 或 restricted_access 的内容，除非用户明确要求保留。
2. 严格遵守"用户当前状态与约束"中的身体限制和目标。不符合约束的内容即使标题再吸引人也要跳过。
3. 如果主题是跟练类内容，只能选择实际可跟练的视频；标题/简介像"档位介绍、练习答疑、课程说明、合集介绍、UP 主自述、vlog、直播回放"的内容必须跳过。
4. 不把"UP 名搜索结果"当成最新内容依据。UP 名只用于解析 UID；真正的 UP 最新内容应来自空间投稿接口。关键词搜索结果只作为补充来源。
5. 最新内容优先时，优先考虑近期发布，但不要连续塞入同一 UP、同一类型、同一身体部位或同一种标题结构的视频。
6. 多样性要参与判断：尽量分散 UP 主、来源类型、练习目标、强度、视频时长。
7. 主题贴合度要高于机械的新旧排序；如果最新视频明显不贴主题，可以用稍旧但更合适的内容。
8. 如果候选不足，不要硬编，说明缺口：缺少来源、缺少某类主题、还是接口没抓到。
9. **内容符合度精筛**：候选池已通过硬规则粗筛，但粗筛无法判断语义层面是否真正符合用户的主题需求。在选择最终内容之前，你必须对每个候选做以下判断——
   - 这个视频的**实际内容类型**是否匹配用户要的主题？（例如：用户要"瑜伽跟练"，一个标题含"瑜伽"但实际是访谈/开箱/日常vlog的视频不算；用户要"肯定语音频"，一个讲"如何写肯定语"的教程不算）
   - 这个视频是否**可以直接被用户使用**？（跟练类必须能直接跟着做；音频类必须能直接听着用；不能是"关于这个主题的讨论/科普/评测"）
   - 如果不确定，宁可跳过，从剩余候选中选更明确的。候选池数量是输出数量的至少 3 倍，你有充分的挑选余地。

## 默认周计划槽位

本次建议槽位：{slot_names}

如果主题是瑜伽或跟练，常用节奏是：晨间唤醒、核心稳定、肩颈修复、流动燃脂、髋腿拉伸、完整跟练、睡前恢复。
如果用户有生理期或身体限制，节奏调整为：温和启动、上肢轻度、拉伸放松、低冲击全身、恢复修复、整体收尾。
如果主题不是跟练类，用等价的"入门、近期重点、方法框架、案例、交叉视角、深度、回顾"结构重命名。

{output_format}
"""
