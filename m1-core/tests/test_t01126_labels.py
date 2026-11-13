# -*- coding: utf-8 -*-
"""T01126：候选回复不得混入「候选回复/理由」标题与 markdown 标签——快速点选防污染。"""
from emoreply.sanitize import expand_candidates, split_rationale, clean_candidate


# ---- 标题行整行丢弃 ----

def test_expand_drops_heading_label_lines():
    raw = "## 候选回复\n好的呀，我马上过来。\n## 理由\n语气轻松，符合朋友关系。"
    parts = expand_candidates(raw)
    assert parts == ["好的呀，我马上过来。", "语气轻松，符合朋友关系。"]


def test_expand_drops_label_only_line_with_colon():
    raw = "候选回复：\n好的呀。\n理由：\n语气轻松"
    parts = expand_candidates(raw)
    assert parts == ["好的呀。", "语气轻松"]


def test_expand_all_label_lines_returns_empty():
    # LLM 只输出了标题没正文 → 无候选（上层按无候选降级）
    assert expand_candidates("## 候选回复\n## 理由") == []


def test_normal_text_with_label_word_not_dropped():
    # 正文合法包含「理由」二字的句子不能被整行丢弃
    raw = "我不认同这个理由，我们再聊聊。"
    assert expand_candidates(raw) == ["我不认同这个理由，我们再聊聊。"]


# ---- 标签前缀剥离 ----

def test_split_rationale_strips_label_prefix():
    text, rationale = split_rationale("候选回复：好的呀。|语气轻松")
    assert text == "好的呀。"
    assert rationale == "语气轻松"


def test_split_rationale_inline_rationale_fallback():
    # LLM 忘带管道符：「正文 理由：xxx」内联结构
    text, rationale = split_rationale("候选回复：好的呀，我马上过来。理由：语气轻松，降低焦虑")
    assert text == "好的呀，我马上过来。"
    assert rationale == "语气轻松，降低焦虑"


def test_split_rationale_plain_unchanged():
    text, rationale = split_rationale("好的，马上看")
    assert text == "好的，马上看"
    assert rationale is None


# ---- clean_candidate 兜底 ----

def test_clean_candidate_strips_markdown_symbols():
    assert clean_candidate("**好的**，马上看。") == "好的，马上看。"
    assert clean_candidate("### 收到了") == "收到了"
