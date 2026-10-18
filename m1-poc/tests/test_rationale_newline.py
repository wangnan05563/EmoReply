"""候选「理由」被换行拆成独立候选的修复。

根因（真机截图实测）：LLM 把理由换到下一行——「内容|」+换行+「理由」，或
「内容」+换行+「|理由」。expand_candidates 逐行拆后产出两条独立候选：
前一条丢理由（渲染时独占整行）、后一条把理由当正文（截图里候选 4
「认同对方的判断，给予正面反馈，同时表明会采取行动跟进」恰是候选 3 的理由）。
后果是候选区前两条分成「回复 + 理由」两栏、后两条不分栏，且理由被误当回复；
按 FR-3.3「≥90% 候选附解释」的验收准则，这是不达标。

修复：管道符是「这两行本属同一条」的显式标记，据此先把悬空管道符的两行
合并，再交给 split_rationale 切分。无管道符标记时不合并，避免误并真实候选。
"""
from emoreply.sanitize import expand_candidates, split_rationale


# 截图现场：候选 3 的内容与其理由被换行拆开（理由行以 | 开头）
SCREENSHOT_RAW = "\n".join([
    "收到，蓝色按钮确实不太实用，我去反馈下看看能不能优化掉。|认可对方观察，并表达后续跟进意愿，语气自然克制，体现合作态度",
    "明白，这几个按钮留着没什么价值，我这边去确认下能否统一调整，有结果同步你。|侧重事实澄清和后续行动，语气简洁专业，不给对方额外负担",
    "收到，这些按钮确实可以优化掉，我这边记录一下",
    "|认同对方的判断，给予正面反馈，同时表明会采取行动跟进",
])


def _parse(raw):
    """复刻 Pipeline 的消费方式：expand → split，产出 (文本, 理由) 列表。"""
    return [split_rationale(line) for line in expand_candidates(raw)]


class TestRationaleOnNextLine:
    def test_screenshot_case_all_candidates_get_rationale(self):
        parsed = _parse(SCREENSHOT_RAW)
        assert len(parsed) == 3
        assert all(rationale for _, rationale in parsed), f"有候选丢理由: {parsed}"

    def test_screenshot_case_reason_not_a_separate_candidate(self):
        texts = [text for text, _ in _parse(SCREENSHOT_RAW)]
        assert "认同对方的判断，给予正面反馈，同时表明会采取行动跟进" not in texts

    def test_screenshot_case_pairing_is_correct(self):
        parsed = _parse(SCREENSHOT_RAW)
        assert parsed[2] == (
            "收到，这些按钮确实可以优化掉，我这边记录一下",
            "认同对方的判断，给予正面反馈，同时表明会采取行动跟进",
        )

    def test_leading_pipe_on_next_line(self):
        raw = "好的呀\n|语气亲近"
        assert _parse(raw) == [("好的呀", "语气亲近")]

    def test_leading_pipe_with_space_on_next_line(self):
        raw = "好的呀\n| 语气亲近"
        assert _parse(raw) == [("好的呀", "语气亲近")]

    def test_trailing_pipe_then_next_line(self):
        raw = "好的呀|\n语气亲近"
        assert _parse(raw) == [("好的呀", "语气亲近")]

    def test_two_candidates_each_split_do_not_cross_merge(self):
        raw = "第一句\n|理由一\n第二句\n|理由二"
        assert _parse(raw) == [("第一句", "理由一"), ("第二句", "理由二")]


class TestNoRegression:
    def test_plain_multiline_candidates_untouched(self):
        # 无悬空管道符 → 不合并，两条独立候选（各自无理由）
        raw = "第一句\n第二句"
        assert _parse(raw) == [("第一句", None), ("第二句", None)]

    def test_normal_inline_pairs_untouched(self):
        raw = "第一句|理由一\n第二句|理由二"
        assert _parse(raw) == [("第一句", "理由一"), ("第二句", "理由二")]

    def test_single_line_pair_untouched(self):
        assert _parse("好的呀|语气亲近") == [("好的呀", "语气亲近")]

    def test_trailing_pipe_without_following_line_still_cleaned(self):
        # 末行悬空管道符（无下一行可并）→ 仍走 T01131 的尾部剥离
        assert _parse("太好了，好好享受这段旅行时光。|") == [("太好了，好好享受这段旅行时光。", None)]