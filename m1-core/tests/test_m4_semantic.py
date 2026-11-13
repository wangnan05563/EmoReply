"""T01096（M4.1 / FR-1.6）：语义指向判定·验收测试。

SRS 验收口径：测试集准确率 ≥85%；「无需回复」误判为「需回复」≤5%。
"""
import os

import pytest

from emoreply.models import Notification
from emoreply.preprocess import decide, is_directed_to_me, preprocess
from emoreply.semantic import (
    LlmSemanticJudge,
    RuleSemanticJudge,
    build_seed_dataset,
    evaluate_seed,
)

SEED = os.path.join(os.path.dirname(__file__), "data", "pointing_seed.jsonl")
MY_NAMES = ["小王", "王工", "阿王"]


def group_msg(body: str) -> Notification:
    return Notification(source="wechat", timestamp=1000.0, body=body,
                        sender="同事", chat="项目群", is_group=True)


# ---------- 规则信号层 ----------

def test_rule_explicit_name_hits():
    j = RuleSemanticJudge(MY_NAMES)
    assert j.judge(group_msg("小王，方案发我一下")).points_to_me is True


def test_rule_second_person_hits():
    j = RuleSemanticJudge(MY_NAMES)
    assert j.judge(group_msg("你昨天提的 bug 修复了吗")).points_to_me is True


def test_rule_broadcast_ignored():
    j = RuleSemanticJudge(MY_NAMES)
    assert j.judge(group_msg("大家下午三点开会，请准时参加")).points_to_me is False


def test_rule_generic_question_ignored():
    j = RuleSemanticJudge(MY_NAMES)
    assert j.judge(group_msg("有人知道今晚食堂有什么菜吗")).points_to_me is False


def test_rule_empty_body_ignored():
    j = RuleSemanticJudge(MY_NAMES)
    assert j.judge(group_msg("")).points_to_me is False


# ---------- 种子测试集验收（SRS 指标） ----------

def test_seed_dataset_metrics_rule_layer():
    rows = build_seed_dataset(SEED)
    assert len(rows) >= 40, "种子集应 ≥40 条"
    metrics = evaluate_seed(RuleSemanticJudge(MY_NAMES), rows, group_msg)
    assert metrics["accuracy"] >= 0.85, f"acc={metrics['accuracy']:.2f} 低于 0.85"
    assert metrics["false_positive_rate"] <= 0.05, \
        f"FP={metrics['false_positive_rate']:.3f} 超 5%：{metrics['fp_cases']}"


def test_seed_dataset_metrics_llm_layer_fallback_same():
    # LLM 层全部抛错 → 全部回退规则层 → 指标与规则层一致
    rows = build_seed_dataset(SEED)

    def broken_llm(prompt: str) -> str:
        raise RuntimeError("network down")

    j = LlmSemanticJudge(llm_call=broken_llm,
                         fallback=RuleSemanticJudge(MY_NAMES))
    metrics = evaluate_seed(j, rows, group_msg)
    assert metrics["accuracy"] >= 0.85
    assert metrics["false_positive_rate"] <= 0.05


# ---------- LLM 语义层 ----------

def test_llm_yes_answer():
    j = LlmSemanticJudge(llm_call=lambda p: "YES", fallback=RuleSemanticJudge(MY_NAMES))
    assert j.judge(group_msg("这事儿得你拍板")).points_to_me is True


def test_llm_no_answer():
    j = LlmSemanticJudge(llm_call=lambda p: "NO", fallback=RuleSemanticJudge(MY_NAMES))
    assert j.judge(group_msg("下午茶拼单，要的接龙")).points_to_me is False


def test_llm_unparseable_falls_back():
    # 模型输出不可解析（如思考过程）→ 回退规则层：无信号默认不指向
    j = LlmSemanticJudge(llm_call=lambda p: "让我想想……", fallback=RuleSemanticJudge(MY_NAMES))
    assert j.judge(group_msg("有人去爬山吗")).points_to_me is False


def test_llm_failure_falls_back():
    def broken(prompt: str) -> str:
        raise TimeoutError("timeout")

    j = LlmSemanticJudge(llm_call=broken, fallback=RuleSemanticJudge(MY_NAMES))
    assert j.judge(group_msg("小王在吗")).points_to_me is True  # 规则层兜底命中称呼


# ---------- 首期行为零回归 ----------

def test_semantic_off_keeps_phase1_behavior():
    # 不传 semantic_judge：群聊无 @/引用 → 不指向（首期硬规则，零回归）
    assert is_directed_to_me(group_msg("小王，方案发我一下")) is False
    n = group_msg("小王，方案发我一下")
    n.mentions_me = True
    assert is_directed_to_me(n) is True


def test_private_chat_always_directed():
    n = Notification(source="wechat", timestamp=1.0, body="在吗", sender="李四", is_group=False)
    assert is_directed_to_me(n) is True


def test_decide_generates_with_semantic_judge():
    j = RuleSemanticJudge(MY_NAMES)
    d = decide(group_msg("小王，方案发我一下"), semantic_judge=j)
    assert d.action == "generate"


def test_decide_drops_without_semantic_judge():
    d = decide(group_msg("小王，方案发我一下"))
    assert d.action == "drop"


def test_preprocess_pipeline_with_judge():
    j = RuleSemanticJudge(MY_NAMES)
    decisions = preprocess([group_msg("小王，方案发我一下"),
                            group_msg("大家下午三点开会")], semantic_judge=j)
    actions = [d.action for d in decisions]
    assert actions == ["generate", "drop"]
