# -*- coding: utf-8 -*-
"""T01082：多轮对话高情商回复——验收标准自动化用例。"""
from emoreply.guardrail import screen_candidate
from emoreply.pipeline import ReplyPipeline
from emoreply.models import Notification


def _notif(body, sender="客户", timestamp=1000):
    return Notification(source="wechat", timestamp=timestamp, body=body, sender=sender, chat=sender)


# ---- 验收 4：候选不出现过度营销用语 ----

def test_overselling_blocked():
    assert screen_candidate("亲，这个问题马上帮您解决~").risky
    assert screen_candidate("宝贝别着急哦~").risky
    assert screen_candidate("么么哒，收到啦").risky
    assert screen_candidate("尊敬的用户您好").risky


def test_normal_words_not_blocked():
    # 正常词不得误伤：亲人/母亲/亲爱的家人在自然句中不带称呼位标点
    assert not screen_candidate("这件事我会跟亲人商量一下").risky
    assert not screen_candidate("母亲节快到了，记得问候妈妈").risky
    assert not screen_candidate("好的，收到，马上处理").risky


# ---- 验收 1：连续 5 轮对话，正确关联首轮上下文 ----

def test_five_turn_context_kept():
    """5 轮历史在 HISTORY_LIMIT=5 内全部保留且按时间升序传给生成器。"""
    seen = {}

    def gen(prompt):
        seen["history"] = list(prompt.history)
        seen["current"] = prompt.current
        return ["好的，我记下了|表示已跟进"]

    pipeline = ReplyPipeline(generator=gen)
    turns = ["首页打不开了", "重启了还是不行", "提示错误码 500", "急用，麻烦快点", "现在完全登录不上"]
    past = []  # 与 NotifyCaptureService.history / pipeline.run(byChat) 同构：调用方累积
    for i, body in enumerate(turns):
        pipeline.handle(_notif(body, timestamp=1000 + i), past)
        past.append(_notif(body, timestamp=1000 + i))
    # 第 6 条（当前消息）处理时，前 5 条恰好是完整 5 轮历史
    pipeline.handle(_notif("到底什么时候能好？", timestamp=2000), past)
    h = seen["history"]
    assert len(h) == 5                       # 连续 5 轮全部关联
    assert turns[0] in h[0]                  # 首轮上下文在历史最前
    assert list(turns) == [line.split("：", 1)[1] for line in h]
    assert "到底什么时候能好" in seen["current"]


# ---- 验收 2/3：点选内容为纯文本、编辑后与输入框一致（结构保证） ----

def test_candidate_text_plain_and_editable():
    """候选 text 经 cleanCandidate 剥离 markdown/HTML，且与 rationale 分离——
    注入输入框的即纯文本，人工编辑不存在二次转换。"""
    from emoreply.sanitize import split_rationale
    text, rationale = split_rationale("**好的**，<p>马上处理</p>|先响应降低焦虑")
    assert text == "好的，马上处理"           # 无 markdown/HTML 标记
    assert rationale == "先响应降低焦虑"        # 解释独立，不进输入框


# ---- 表达克制：不过度承诺由既有 deceptive 护栏覆盖 ----

def test_no_overpromise():
    assert screen_candidate("跟着买稳赚不赔").risky
    assert screen_candidate("我保证你能赚回来").risky
    # 非承诺语境不得误伤
    assert not screen_candidate("我保证按你说的顺序检查一遍").risky
