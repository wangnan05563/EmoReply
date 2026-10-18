"""意图与情感理解（REQ-006 / FR-2.1 / FR-2.2 / FR-2.4）。

规则版基线：只读通知场景下上下文有限，先用可解释的硬规则兜住
「通知类不回复」（FR-2.4 的核心是压误判：无需回复误判为需回复 ≤5%），
意图/情绪供提示词增强使用；LLM 语义版分类在云端生成层叠加，不在此处。
全部纯函数，不依赖 Android 运行时。
"""
import re
from typing import Optional, Tuple

# ---- FR-2.1 消息分类：问询 / 请求 / 闲聊 / 情绪倾诉 / 通知 / 冲突 / 邀约 ----

# 顺序即优先级：强信号在前。通知类最靠前——它是 FR-2.4 压误判的关键。
_INTENT_RULES = [
    ("通知", re.compile(
        r"验证码|校验码|取件码|快递|包裹|已签收|已发货|物流|账单|扣款|还款日|"
        r"会议提醒|日程|签到|打卡|系统通知|广告|优惠|促销|限时|余额不足|工单|"
        r"\[(图片|文件|链接|名片)\]")),
    ("冲突", re.compile(
        r"你总是|你从来|不想再|分手|绝交|投诉|骗子|欺骗|敷衍|忍无可忍|太过分")),
    ("情绪倾诉", re.compile(
        r"好累|累死|好难过|伤心|想哭|崩溃|烦死|压力大|委屈|焦虑|失眠|emo")),
    ("邀约", re.compile(
        r"一起|约[个饭]|聚餐|吃饭|喝咖啡|喝奶茶|看电影|有空吗|出来玩|聚一聚")),
    ("请求", re.compile(
        r"帮我|帮忙|麻烦你|拜托|能不能|可不可以|麻烦帮|劳驾|借[我个]")),
    ("问询", re.compile(
        r"[??？]$|[??？]\s*$|吗[??？]?$|呢[？?]?$|什么时候|几点|多少钱|多少个|"
        r"怎么|为什么|为啥|是不是|有没有|哪个|谁知道")),
]

DEFAULT_INTENT = "闲聊"

# ---- FR-2.2 情感识别：开心 / 愤怒 / 焦虑 / 中性 + 紧急度 ----

_EMOTION_RULES = [
    ("愤怒", re.compile(r"气死|生气|无语|过分|差劲|忍无可忍|骗子|投诉|恶心|服了")),
    ("焦虑", re.compile(r"着急|急死|担心|焦虑|来不及|怎么办|截止|延误|出事|糟了")),
    ("开心", re.compile(r"太好了|哈哈|开心|棒|厉害|谢谢|感谢|喜欢|赞|顺利")),
]

_URGENT = re.compile(r"现在|马上|立刻|尽快|紧急|今天[之内]?必须|今晚|下班前|越快越好")


def classify_intent(text: Optional[str]) -> str:
    """FR-2.1：规则版消息分类。空文本归「通知」（无可判定内容，按需处理）。"""
    if not text or not text.strip():
        return "通知"
    for intent, pattern in _INTENT_RULES:
        if pattern.search(text):
            return intent
    return DEFAULT_INTENT


def classify_emotion(text: Optional[str]) -> Tuple[str, str]:
    """FR-2.2：返回 (情绪, 紧急度)。紧急度 ∈ {高, 普通}。"""
    if not text:
        return "中性", "普通"
    for emotion, pattern in _EMOTION_RULES:
        if pattern.search(text):
            e = emotion
            break
    else:
        e = "中性"
    urgency = "高" if _URGENT.search(text) else "普通"
    return e, urgency


# ---- FR-2.4 是否需回复 ----

# 强「无需回复」信号：即使意图规则漏判也拦下（压 FR-2.4 误判率）
_NO_REPLY = re.compile(
    r"验证码|校验码|取件码|已签收|已发货|扣款成功|支付成功|到账|余额不足|"
    r"会议[已将]开始|日程提醒|系统自动|请勿回复|本条为系统消息|广告")


def needs_reply(text: Optional[str], intent: Optional[str] = None) -> bool:
    """FR-2.4：判定是否需要回复。纯通知类 → False（无需回复）。"""
    if intent == "通知":
        return False
    if text and _NO_REPLY.search(text):
        return False
    return True


def classify(text: Optional[str]) -> dict:
    """便捷入口：一次产出意图 / 情绪 / 紧急度 / 是否需回复。"""
    intent = classify_intent(text)
    emotion, urgency = classify_emotion(text)
    return {
        "intent": intent,
        "emotion": emotion,
        "urgency": urgency,
        "needsReply": needs_reply(text, intent),
    }
