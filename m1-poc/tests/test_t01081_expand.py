# -*- coding: utf-8 -*-
"""T01081：多候选挤段拆分护栏（防止点任一候选发送全部选项+解释）。"""
from emoreply.sanitize import expand_candidates, split_rationale
from emoreply.pipeline import ReplyPipeline
from emoreply.models import Notification


# ---- expand_candidates ----

def test_expand_single_line_unchanged():
    assert expand_candidates("好的，马上看|让对方知道已处理") == ["好的，马上看|让对方知道已处理"]


def test_expand_multiline():
    raw = "好的，马上看|降低焦虑\n我看看再回你|争取思考时间"
    assert expand_candidates(raw) == ["好的，马上看|降低焦虑", "我看看再回你|争取思考时间"]


def test_expand_inline_numbered():
    raw = "选项1：好的，马上看|理由一 选项2：我看看再回你|理由二"
    parts = expand_candidates(raw)
    assert len(parts) == 2
    assert parts[0].startswith("好的")
    assert parts[1].startswith("我看看")


def test_expand_numbered_candidate_label():
    raw = "候选1：收到，谢谢～ 候选2：好嘞，知道了"
    assert len(expand_candidates(raw)) == 2


def test_expand_numbered_with_paren_label():
    # T01082 实测：agnes 模型输出「**候选1（情感共鸣）**：xxx」——剥 markdown 后前缀带括注
    raw = "候选1（情感共鸣）：非常抱歉给您带来困扰|先共情 候选2（行动建议）：我马上排查|收集细节"
    parts = expand_candidates(raw)
    assert len(parts) == 2
    assert parts[0].startswith("非常抱歉")
    assert parts[1].startswith("我马上排查")


def test_expand_strips_line_prefixes():
    raw = "1. 好的，马上看\n2. 我看看再回你"
    assert expand_candidates(raw) == ["好的，马上看", "我看看再回你"]


def test_expand_empty():
    assert expand_candidates("") == []
    assert expand_candidates(None) == []


def test_expand_plain_multisentence_not_split():
    # 无编号的多句正文（一条候选的正常长句）不得被误拆
    raw = "收到，我下午三点前把周报发你，另外会议纪要我整理好也一并同步。"
    assert expand_candidates(raw) == [raw]


# ---- 端到端：单条 giant raw → 拆成多条候选，每条只含纯文本 ----

def _notif(body="这周周报什么时候发？", sender="老板"):
    return Notification(source="wechat", timestamp=1000, body=body, sender=sender, chat=sender)


def test_pipeline_giant_raw_becomes_separate_candidates():
    giant = ("选项1：好的，今天下班前发你|给出明确时限 选项2：我整理好就发，预计明天上午|争取质量优先")

    class GiantGenerator:
        def __call__(self, prompt):
            return [giant]  # 模拟不支持 n>1 的服务把两候选挤进一个 content

    outcome = ReplyPipeline(generator=GiantGenerator()).handle(_notif(), [])
    assert len(outcome.candidates) == 2
    # 每条候选是纯文本：不含「选项」编号、不含解释句
    for c in outcome.candidates:
        assert "选项" not in c.text
        assert "理由" not in c.text
        assert "|" not in c.text
    assert outcome.candidates[0].rationale == "给出明确时限"
    assert outcome.candidates[1].rationale == "争取质量优先"


def test_commit_text_is_single_plain_line():
    """IME 注入前最后一道防线：候选 text 必须无换行、无解释句。"""
    text, rationale = split_rationale("好的，马上看|让对方知道已处理")
    assert "\n" not in text
    assert rationale is not None  # 解释只作展示
    assert text == "好的，马上看"
