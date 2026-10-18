"""REQ-006 / REQ-009（FR-2.1 / 2.2 / 2.4 / 3.2 / 3.3）验收测试。

规则版分类的可测试下限：分类可辨识、通知类压误判、提示词注入向后兼容、
解释句格式解析健壮。F1 指标（≥0.85/0.80）需标注测试集，属真机/数据侧验收。
"""
import pytest

from emoreply.classify import classify, classify_emotion, classify_intent, needs_reply
from emoreply.compose import build_prompt
from emoreply.models import Notification
from emoreply.pipeline import NO_REPLY_NOTICE, ReplyPipeline
from emoreply.sanitize import split_rationale


def _notif(body: str, **kw) -> Notification:
    kw.setdefault("source", "wechat")
    kw.setdefault("timestamp", 1000)
    return Notification(body=body, **kw)


def _stub(replies):
    return lambda prompt: list(replies)


# ---- FR-2.1 意图分类 ----

@pytest.mark.parametrize("text,expected", [
    ("你的快递已到菜鸟驿站，取件码 8-2-3001", "通知"),
    ("【支付宝】您有一笔账单 35.00 元，扣款成功", "通知"),
    ("周日一起吃个饭吗？", "邀约"),      # 邀约优先于问询（邀约规则在前）
    ("帮我看看这个文档能不能提交", "请求"),
    ("下午几点开会？", "问询"),
    ("最近好累，压力好大，有点崩溃", "情绪倾诉"),
    ("你总是这样，从来不考虑我，太过分了", "冲突"),
    ("今天天气不错", "闲聊"),
])
def test_intent_classification(text, expected):
    assert classify_intent(text) == expected


def test_intent_empty_is_notification():
    assert classify_intent("") == "通知"
    assert classify_intent(None) == "通知"


# ---- FR-2.2 情感与紧急度 ----

@pytest.mark.parametrize("text,emotion", [
    ("太好了，顺利拿到offer，哈哈", "开心"),
    ("你这样做真的很过分，我气死了", "愤怒"),
    ("糟糕了，来不及了，怎么办啊，急死", "焦虑"),
    ("明天下午两点碰一下", "中性"),
])
def test_emotion_classification(text, emotion):
    assert classify_emotion(text)[0] == emotion


def test_urgency_high_on_deadline_words():
    assert classify_emotion("这个必须马上处理")[1] == "高"
    assert classify_emotion("有空聊聊")[1] == "普通"


# ---- FR-2.4 是否需回复（核心：压误判）----

@pytest.mark.parametrize("text", [
    "验证码 283910，请勿泄露",
    "您的包裹已签收",
    "【钉钉】日程提醒：15:00 项目周会",
    "支付成功：¥199.00",
])
def test_no_reply_for_pure_notifications(text):
    assert needs_reply(text) is False


@pytest.mark.parametrize("text", [
    "什么时候给我答复？",
    "帮我个忙呗",
    "周末约饭不？",
])
def test_reply_for_real_messages(text):
    assert needs_reply(text) is True


def test_pipeline_notification_no_candidates_but_notice():
    pipeline = ReplyPipeline(generator=_stub(["收到，谢谢～"]))
    outcome = pipeline.handle(_notif("验证码 283910，10 分钟内有效"), [])
    assert outcome.candidates == []
    assert outcome.notice == NO_REPLY_NOTICE


# ---- 提示词注入（FR-2.1/2.2 → FR-3.1）----

def test_build_prompt_injects_intent_and_emotion():
    prompt = build_prompt(_notif("下午几点开会？"), [], intent="问询", emotion="中性", urgency="普通")
    assert "消息类型：问询" in prompt.system
    assert "对方情绪：中性" in prompt.system


def test_build_prompt_without_classification_unchanged():
    """不传分类信息时与 M1 行为完全一致（向后兼容）。"""
    legacy = build_prompt(_notif("在吗"), [])
    assert "消息类型" not in legacy.system
    assert legacy.system == build_prompt(_notif("在吗"), [], intent="", emotion="").system


# ---- FR-3.2 风格可切换（四风格 system 提示词互异）----

def test_four_styles_produce_distinct_prompts():
    systems = {s: build_prompt(_notif("在吗"), [], style=s).system for s in ("正式", "亲切", "简短", "幽默")}
    assert len(set(systems.values())) == 4


# ---- FR-3.3 解释句解析 ----

def test_split_rationale_with_rationale():
    text, rationale = split_rationale("好的，我看一下晚点回你|对方在等确认，先给个响应避免悬置")
    assert text == "好的，我看一下晚点回你"
    assert rationale == "对方在等确认，先给个响应避免悬置"


def test_split_rationale_without_rationale():
    text, rationale = split_rationale("收到，谢谢～")
    assert text == "收到，谢谢～"
    assert rationale is None


def test_split_rationale_content_containing_pipe():
    text, rationale = split_rationale("选 A|B 都行|理由放最后")
    assert text == "选 A|B 都行"
    assert rationale == "理由放最后"


def test_pipeline_passes_rationale_to_candidate():
    pipeline = ReplyPipeline(generator=_stub(["好的，马上看|让对方知道已处理，降低焦虑"]))
    outcome = pipeline.handle(_notif("那个文件急用，现在能发我吗？"), [])
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].rationale == "让对方知道已处理，降低焦虑"


# ---- classify 便捷入口 ----

def test_classify_combined():
    info = classify("验证码 7748")
    assert info["intent"] == "通知"
    assert info["needsReply"] is False
