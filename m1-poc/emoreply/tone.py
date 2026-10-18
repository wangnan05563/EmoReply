"""T01348：关系感知 → 语气默认档位（m1-poc 事实源，Android 侧 core/Tone.kt 同构镜像）。

来源（T01345 竞品分析）：钉钉「AI 灵动回复」的高情商感知主要来自「先识别双方
身份与关系，再决定语气分布」。本项目 PersonaStore.relation 与 UxSettings.relation
（三维筛选）的数据一直在库里，但 tone 是全局单值滑杆，不随对端关系变化。

设计：
- TONE_AUTO = -1 作为 tone 的哨兵值（0 是合法手动值，不能复用）；
- AUTO 时按关系映射默认档位（对齐任务单建议区间）：
  上级/客户/外部 → 35（克制）；同事/平级 → 58（中性）；好友/家人/恋人 → 75（偏活泼）；
- 关系别名归一化（上级→领导、平级→同事、外部→客户、好友→朋友），
  兼容 UxSettings.RELATIONS（领导/客户/恋人/朋友）与 PersonaStore.RELATIONS 两套口径；
- 联系人级手动覆盖（PersonaStore.tone_manual）优先级最高（T01537：联系人 > 全局 > 关系推档）：
  用户按联系人手动调过的档位，优先于全局滑杆（含手动值），不被任何全局态盖掉；
- tone=None 旧行为不变（提示词不注入语气强度行）。
"""
from typing import Optional

from .compose import TONE_DEFAULT, TONE_MAX, TONE_MIN

# AUTO 哨兵：任何 <= TONE_AUTO 的 tone 值视为「自动」
TONE_AUTO = -1

# 归一化关系 → 默认语气档位（数值落在任务单建议区间内）
RELATION_TONE = {
    "领导": 35,   # 上级/客户/外部：克制、精简、避免感叹与过度承诺
    "客户": 35,
    "同事": 58,   # 平级：中性自然
    "朋友": 75,   # 好友/家人/恋人：偏活泼
    "家人": 75,
    "恋人": 75,
    "陌生": TONE_DEFAULT,  # 未标注 → 与全局默认一致（不因 AUTO 产生行为漂移）
}

# 关系别名归一化（两套枚举口径 + 常见同义词）
RELATION_ALIASES = {
    "上级": "领导",
    "老板": "领导",
    "外部": "客户",
    "平级": "同事",
    "好友": "朋友",
}


def clamp_tone(value: int) -> int:
    """收敛到 [TONE_MIN, TONE_MAX]。"""
    return max(TONE_MIN, min(TONE_MAX, int(value)))


def normalize_relation(relation: Optional[str]) -> str:
    """关系别名归一化；空/None → 「陌生」。"""
    r = (relation or "").strip()
    return RELATION_ALIASES.get(r, r) if r else "陌生"


def tone_for_relation(relation: Optional[str]) -> int:
    """归一化关系 → 默认语气档位；未收录关系按「陌生」处理。"""
    return RELATION_TONE.get(normalize_relation(relation), TONE_DEFAULT)


def resolve_tone(tone: Optional[int],
                 relation: Optional[str] = None,
                 override: Optional[int] = None) -> Optional[int]:
    """tone 解析（T01537 优先级）：联系人级 > 全局 > 关系推档；None 旧行为不变。

    - tone is None → None（调用方不注入语气强度行，FR-7.3 旧行为）；
    - override 非空 → 联系人级手动覆盖，优先于全局（无论 AUTO 还是手动值）；
    - tone <= TONE_AUTO → 关系推档；
    - 其余（0-100 或越界手动值）→ clamp 后返回。

    T01348 旧语义「override 仅在 AUTO 时生效」已由 T01537 升级为
    「联系人级覆盖一切全局值」——用户在 IME 里按联系人手调后，
    不该被全局滑杆静默盖掉（任务单验收：覆盖优先级 联系人 > 全局 > 关系推档）。
    """
    if tone is None:
        return None
    if override is not None:
        return clamp_tone(override)
    t = int(tone)
    if t <= TONE_AUTO:
        return tone_for_relation(relation)
    return clamp_tone(t)
