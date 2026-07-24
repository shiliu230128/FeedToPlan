from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

from .models import PlanRequest, Slot


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

## 编排原则

1. 先排除候选池里标记为 commercial 或 restricted_access 的内容，除非用户明确要求保留。
2. 严格遵守"用户当前状态与约束"中的身体限制和目标。不符合约束的内容即使标题再吸引人也要跳过。
3. 如果主题是跟练类内容，只能选择实际可跟练的视频；标题/简介像"档位介绍、练习答疑、课程说明、合集介绍、UP 主自述、vlog、直播回放"的内容必须跳过。
4. 不把"UP 名搜索结果"当成最新内容依据。UP 名只用于解析 UID；真正的 UP 最新内容应来自空间投稿接口。关键词搜索结果只作为补充来源。
5. 最新内容优先时，优先考虑近期发布，但不要连续塞入同一 UP、同一类型、同一身体部位或同一种标题结构的视频。
6. 多样性要参与判断：尽量分散 UP 主、来源类型、练习目标、强度、视频时长。
7. 主题贴合度要高于机械的新旧排序；如果最新视频明显不贴主题，可以用稍旧但更合适的内容。
8. 如果候选不足，不要硬编，说明缺口：缺少来源、缺少某类主题、还是接口没抓到。

## 默认周计划槽位

本次建议槽位：{slot_names}

如果主题是瑜伽或跟练，常用节奏是：晨间唤醒、核心稳定、肩颈修复、流动燃脂、髋腿拉伸、完整跟练、睡前恢复。
如果用户有生理期或身体限制，节奏调整为：温和启动、上肢轻度、拉伸放松、低冲击全身、恢复修复、整体收尾。
如果主题不是跟练类，用等价的"入门、近期重点、方法框架、案例、交叉视角、深度、回顾"结构重命名。

## 输出格式

请输出 Markdown：

```markdown
# {{主题}}安排

## 编排思路
一段话说明为什么这样排，必须提到时效性、多样性、两周去重、主题匹配，以及如何响应用户的当前状态约束。

## 每日安排

### 周一｜{{日期}}｜{{槽位}}
视频：[{{标题}}]({{播放链接}})
UP 主：{{名称}}
发布时间：{{日期}}
时长：约 {{分钟}} 分钟
推荐理由：{{结合当天槽位、主题、时效性、多样性，以及是否符合用户状态约束说明}}

...

## 候选不足或风险
只在需要时列出。
```
"""
