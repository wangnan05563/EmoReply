"""T01130：输入框文字美化——prompt 构造验收。

验收点：
1. system 含全部硬约束（语义不变/语言一致/不增不删/语气保留/无改动则原样/直接输出）；
2. current=原文、history 恒为空（美化无对话上下文）；
3. 超长文本时 truncated 置位（不超窗安全边界，与 build_prompt 同策略）。
"""
from emoreply.compose import BEAUTIFY_SYSTEM, build_beautify_prompt

CONSTRAINTS = [
    "核心语义不变",
    "中文美化中文",
    "不添加原文未包含的信息",
    "不删减原文关键信息",
    "语气倾向",
    "原样输出",
    "不要使用 markdown",
]


def test_system_contains_all_constraints():
    for c in CONSTRAINTS:
        assert c in BEAUTIFY_SYSTEM


def test_current_is_text_and_history_empty():
    bundle = build_beautify_prompt("儿子睡着了，我明晚过去")
    assert bundle.current == "儿子睡着了，我明晚过去"
    assert bundle.history == []


def test_tokens_estimated():
    bundle = build_beautify_prompt("hello world")
    assert bundle.estimated_tokens > 0


def test_oversize_text_marked_truncated():
    # 构造超预算文本（system+文本+512 预留 > 4096）→ truncated 置位
    bundle = build_beautify_prompt("长" * 5000)
    assert bundle.truncated is True


def test_normal_text_not_truncated():
    bundle = build_beautify_prompt("稍等，我这边有点事")
    assert bundle.truncated is False
