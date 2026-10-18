"""上下文拼装与提示词组装（FR-3.1 上下文策略 + 附录 C-2 C16）。

只读通知拿不到真实聊天记录，只能用同会话近期通知拼「伪历史」。伪历史极易超窗
（compare T00181 实测输入 195504 > 模型上限 131072 直接失败），因此拼装后必须先
估算 token、超限按策略截断，而不是把失败留给调用方。
"""
# T01343 修复：前向引用（如 build_opening_prompt 在 PromptBundle 定义之前引用其注解）
# 会在模块加载时求值报 NameError——延迟注解求值一次性解决。
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .models import Notification

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

SYSTEM_TEMPLATE = (
    "你是聊天回复助手，为「{me}」起草一条可直接发送的回复。\n"
    "约束：\n"
    "1) 只依据给定上下文，不得编造对方未提及的事实；\n"
    "2) 语气{style}，不冒犯、不敷衍、不过度承诺；\n"
    "3) 直接输出正文，不要使用 markdown 格式符；\n"
    "4) 即使上下文不完整或混乱，也基于已有信息给出最合适的回复——"
    "严禁输出「请提供更多信息/聊天记录」之类请求澄清的元话语。"
)


# ===== T01159：候选表情（默认开启）=====
# 提示词只给「1-2 个、贴合语气、不堆砌」的软约束——硬编码插入表情会破坏语义（C10），
# 交给模型按语气自然使用；输出侧由 sanitize.normalize_emoji 兜底防堆砌。
EMOJI_HINT = (
    "\n5) 正文中可自然使用 1-2 个 emoji 表情（如 😊👍🙏），贴合当前语气与情绪；"
    "不得堆砌连续表情，也不得用表情替代必要文字。"
)


# ===== T01158 FR-7.3：语气强度（连续滑杆：理性 ↔ 活泼）=====
# 4 档风格（FR-3.2）之上叠加连续强度：风格定「基本人设」，强度定「表达热度」，
# 二者都进 system，避免把 4 档硬编码成强度档位而丢失风格差异。
TONE_MIN, TONE_MAX, TONE_DEFAULT = 0, 100, 50

_TONE_BANDS = (
    (20, "语气极克制、偏理性：措辞精简，避免感叹号与情绪化表达"),
    (40, "语气偏理性：稳重得体，少用夸张表达"),
    (60, "语气中性自然：亲切但不过火"),
    (80, "语气偏活泼：可用轻松措辞与适度感叹"),
    (101, "语气活泼热情：可适度使用感叹与轻松表达，但仍不得过度承诺"),
)

# FR-7.1 换一批：注入的排除项上限（过多会挤占 token 且稀释约束）
EXCLUDE_LIMIT = 8
EXCLUDE_SNIPPET = 60

# T02224：候选条数是请求时就定死的「数量契约」——生成器 n、提示词措辞与输出侧
# 理由配对（sanitize.pair_unmarked_reasons）三处必须同源，否则配对会算错。
DEFAULT_CANDIDATES = 2
OPENING_CANDIDATES = 3


def tone_hint(tone: Union[int, str, None]) -> str:
    """语气强度 → 提示词描述句（越界值收敛到 [0,100]）。"""
    try:
        t = int(tone)
    except (TypeError, ValueError):
        t = TONE_DEFAULT
    t = max(TONE_MIN, min(TONE_MAX, t))
    for upper, hint in _TONE_BANDS:
        if t < upper:
            return hint
    return _TONE_BANDS[-1][1]


# ===== T01349：多风格并行候选（单请求多语气，不增加 RTT）=====
# 钉钉一次给 4-6 条跨风格候选，用「让用户挑」对冲「模型单条不稳定」；本项目候选
# 受单值 tone 约束基本同语气。三档变体取 tone 五档带中三个互异描述区（35/58/80
# 分别命中「偏理性/中性自然/偏活泼」），约束模型单请求内跨语气分布，RTT 不变。
MULTI_TONE_VARIANTS = (35, 58, 80)
MULTI_TONE_LABELS = ("克制", "中性", "活泼")


def tone_variants_hint(tones) -> str:
    """多风格并行指令：逐档列出语气描述，要求按序各出一条（顺序即档位顺序）。"""
    parts = []
    for i, t in enumerate(tones):
        label = MULTI_TONE_LABELS[i] if i < len(MULTI_TONE_LABELS) else f"档{i + 1}"
        parts.append(f"{i + 1}) {label}档（{tone_hint(t)}）")
    return ("\n语气强度（多风格并行）：本次必须按以下语气从上到下各生成一条候选，"
            "顺序与上列一致，不得打乱，不得合并成同一语气：\n" + "\n".join(parts))


def build_exclude_hint(exclude: Optional[List[str]]) -> str:
    """FR-7.1：已给候选的排除提示（换一批时不得重复）。"""
    items = [ (e or "").strip() for e in (exclude or []) if (e or "").strip() ]
    if not items:
        return ""
    lines = "；".join(
        f"{i + 1}) {e[:EXCLUDE_SNIPPET]}" for i, e in enumerate(items[:EXCLUDE_LIMIT])
    )
    return f"\n换一批：以下候选已给出过，必须换角度或换措辞，不得重复：{lines}。"


# ===== T01244 FR-8.2：关系×场景×目的三维筛选（约束注入 + 默认继承上次选择）=====
# 三维都是「生成前约束」，非空才注入（缺省不注入保持 M1 行为）。语义：关系定亲疏、
# 场景定语气基调、目的定表达意图；三者与既有 relation/intent 叠加不冲突。
SCENARIO_HINTS = {
    "闲聊": "对方在闲聊，回话要轻松自然，不必刻意推进",
    "工作": "工作语境，回话要专业得体、就事论事",
}
PURPOSE_HINTS = {
    "安抚": "目的是安抚对方情绪，先共情再给建议",
    "拒绝": "目的是礼貌表达拒绝或边界，语气温和但立场清晰",
    "推进": "目的是推进事情落地，回话要给出明确下一步",
    "闲聊": "目的是延续对话，不必追求结果",
}


def build_filter_hint(relation: str = "", scenario: str = "", purpose: str = "") -> str:
    """FR-8.2：三维筛选注入提示词（非空才注入，全空返回空串）。"""
    parts = []
    if relation:
        parts.append(f"对方关系：{relation}（语气贴合关系亲疏，不越界）")
    if scenario:
        parts.append(f"场景：{SCENARIO_HINTS.get(scenario, scenario)}")
    if purpose:
        parts.append(f"目的：{PURPOSE_HINTS.get(purpose, purpose)}")
    if not parts:
        return ""
    return "\n4) " + "；".join(parts) + "。"


# ===== T01246 FR-8.4：场景化开场建议（无对话上下文时选场景生成开场话术）=====
# 开场与普通回复不同：没有 current 消息，只有「目标场景」。每场景给出独立风格约束，
# 期望产出 3 条可直接发送的开场候选（内容|理由 同一般候选，便于复用 sanitize）。
OPENING_SCENARIOS = ("破冰", "重连", "节日祝福")
_OPENING_HINTS = {
    "破冰": "对方是初次或久未交流，回话要自然不刻意、降低社交压力，给出轻松的话题切入点",
    "重连": "与久未联系的人重建联系，回话要真诚承接过去、不留尴尬，自然推进",
    "节日祝福": "节日祝福语境，回话要应景得体、不套路化，可带适度温度",
}


def build_opening_prompt(scenario: str, style: str = "亲切",
                         max_tokens: int = 4096) -> PromptBundle:
    """构造场景化开场建议的提示词束。

    current 置空串（无上下文），场景约束入 system；history 恒为空（开场无对话历史）。
    期望模型产出 3 条开场候选，输出后在管线侧经 sanitize 拆条与护栏。
    """
    scenario = scenario if scenario in _OPENING_HINTS else "破冰"
    hint = _OPENING_HINTS[scenario]
    system = (
        "你是聊天回复助手，为「我」起草一条可直接发送的**开场**话术（对方主动场合），约束：\n"
        "1) 场景：{hint}；\n"
        "2) 语气{style}，不冒犯、不敷衍、不过度承诺；\n"
        "3) 直接输出正文，不要使用 markdown 格式符；\n"
        "4) 给出 3 条风格各异、互不重复的开场候选，每条按「内容|理由」输出，"
        "理由用一句话说明情商点，可以省略。"
    ).format(hint=hint, style=style)
    budget = max_tokens - estimate_tokens(system) - 512
    return PromptBundle(
        system=system,
        history=[],
        current="",
        style=style,
        estimated_tokens=estimate_tokens(system),
        dropped_history=0,
        truncated=budget < 0,
    )


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。

    刻意不引入 tokenizer 依赖——移动端要的是「不超窗」的安全边界，不是精确计费。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)


@dataclass
class PromptBundle:
    system: str
    history: List[str] = field(default_factory=list)
    current: str = ""
    style: str = "亲切"
    estimated_tokens: int = 0
    dropped_history: int = 0
    truncated: bool = False


def _render(n: Notification) -> str:
    return f"{n.sender or '对方'}：{n.body}"


def _intent_section(intent: str, emotion: str, urgency: str, relation: str,
                    knowledge: List[str]) -> str:
    """FR-2.1/2.2（REQ-006）：意图/情绪/紧急度/关系/知识 → system 附加段（空则不注入）。"""
    if not intent:
        return ""
    extra = f"\n4) 消息类型：{intent}。"
    if emotion:
        extra += f"对方情绪：{emotion}"
        if urgency:
            extra += f"（紧急度：{urgency}）"
        extra += "。回应需贴合情绪与紧急度。"
        if relation:
            extra += f"对方关系：{relation}（语气贴合关系亲疏，不越界）。"
        if knowledge:
            extra += ("参考资料（已按相关性筛选；仅在与当前消息相关时参考，"
                      f"不得据此编造对方未提及的事实）：{'；'.join(knowledge)}。")
    return extra


def _history_window(history: List[Notification], history_limit: int,
                    budget: int) -> "tuple[List[str], int, int]":
    """伪历史压预算：超预算时从**最旧**开始丢弃，返回 (保留行, 丢弃条数, 已用 token)。"""
    lines = [_render(n) for n in history if (n.body or "").strip()][-history_limit:]
    kept: List[str] = []
    used = 0
    for line in reversed(lines):
        cost = estimate_tokens(line)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return kept, len(lines) - len(kept), used


def build_prompt(current: Notification,
                 history: List[Notification],
                 me: str = "我",
                 style: str = "亲切",
                 max_tokens: int = 4096,
                 history_limit: int = 5,
                 intent: str = "",
                 emotion: str = "",
                 urgency: str = "",
                 relation: str = "",
                 scenario: str = "",   # T01244 FR-8.2：三维筛选（场景）
                 purpose: str = "",    # T01244 FR-8.2：三维筛选（目的）
                 knowledge: Optional[List[str]] = None,
                 emoji: bool = True,
                 tone: Optional[int] = None,
                 tone_variants: Optional[List[int]] = None,   # T01349：多风格并行（优先于单值 tone）
                 exclude: Optional[List[str]] = None) -> PromptBundle:
    """组装提示词，并把伪历史压到 token 预算内。

    history 需按时间升序且不含 current；超预算时从**最旧**开始丢弃——
    越近的对话对语气判断越关键，system 与当前消息则永不丢弃。
    intent/emotion/urgency 由 classify.py 产出（REQ-006），非空时并入 system
    提示词增强生成（FR-2.1/2.2 → FR-3.1）；缺省不注入，保持 M1 行为不变。
    """
    system = SYSTEM_TEMPLATE.format(me=me, style=style)
    system += _intent_section(intent, emotion, urgency, relation, knowledge or [])
    # T01244 FR-8.2：三维筛选独立注入，与 intent 分支解耦——intent/emotion 缺失时
    # 三维约束仍生效。relation 仅在 intent 分支的 emotion 子分支注入过（即 intent 与
    # emotion 同时非空时）——此时不再由 filter_hint 重复注入；其余情况交 filter_hint。
    relation_hint = relation if not (intent and emotion) else ""
    filter_hint = build_filter_hint(relation=relation_hint, scenario=scenario, purpose=purpose)
    if filter_hint:
        system += filter_hint
    # T01159：表情约束（emoji=False 时不注入，输出侧再剥掉，保证「关=无表情」）
    if emoji:
        system += EMOJI_HINT
    # T01158 FR-7.3：语气强度（None 表示沿用旧行为，仅风格生效）
    # T01349：tone_variants 非空时优先（多风格并行一次出多档），单值 tone 忽略
    if tone_variants:
        system += tone_variants_hint(tone_variants)
    elif tone is not None:
        system += f"\n语气强度：{tone_hint(tone)}"
    # T01158 FR-7.1：换一批——把已给候选作为排除项注入
    exclude_hint = build_exclude_hint(exclude)
    if exclude_hint:
        system += exclude_hint
    current_line = _render(current)
    # 预留回复本身的空间，避免「输入刚好压线、输出直接撞顶」
    budget = max_tokens - estimate_tokens(system) - estimate_tokens(current_line) - 512

    kept, dropped, used = _history_window(history, history_limit, budget)
    return PromptBundle(
        system=system,
        history=kept,
        current=current_line,
        style=style,
        estimated_tokens=estimate_tokens(system) + used + estimate_tokens(current_line),
        dropped_history=dropped,
        truncated=dropped > 0,
    )

# ===== T01130：输入框文字美化（独立于回复生成，仅润色不改意）=====

BEAUTIFY_SYSTEM = (
    "你是文字润色助手。请对用户给出的文本做美化，严格遵守：\n"
    "1) 保持核心语义不变，不得改变文本语言（中文美化中文，英文美化英文）；\n"
    "2) 只优化句式结构与措辞，使表达更自然流畅、更得体；\n"
    "3) 不添加原文未包含的信息，不删减原文关键信息；\n"
    "4) 保留原有语气倾向（正式/随意、热情/冷静）；\n"
    "5) 若原文已足够清晰流畅，原样输出即可，不做无意义改动；\n"
    "6) 直接输出美化后的文本，不要任何解释或前后缀，不要使用 markdown 格式符。"
)


def build_beautify_prompt(text: str, max_tokens: int = 4096) -> PromptBundle:
    """构造输入框文字美化的提示词束。

    current=原文，history 恒为空（美化只看单段文本，无对话上下文）。
    预算预留 512 token 给输出，与 build_prompt 同策略。
    """
    budget = max_tokens - estimate_tokens(BEAUTIFY_SYSTEM) - estimate_tokens(text) - 512
    return PromptBundle(
        system=BEAUTIFY_SYSTEM,
        history=[],
        current=text,
        style="自然",
        estimated_tokens=estimate_tokens(BEAUTIFY_SYSTEM) + estimate_tokens(text),
        dropped_history=0,
        truncated=budget < 0,
    )
