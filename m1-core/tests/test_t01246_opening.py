"""T01246 FR-8.4 场景化开场建议：破冰/重连/节日祝福三场景，各生成开场候选。

验收（PRD FR-8.4）：三种场景各生成 3 条开场候选；无对话上下文时可用；开场候选
可注入、无顺从诈骗话术。本文件钉死 prompt 结构（场景约束注入）+ 管线产出。
"""
import pytest

from emoreply.compose import OPENING_SCENARIOS, build_opening_prompt
from emoreply.pipeline import ReplyPipeline


class TestOpeningPrompt:
    "FR-8.4 开场提示词构造"

    def test_three_scenarios(self):
        assert list(OPENING_SCENARIOS) == ["破冰", "重连", "节日祝福"]

    @pytest.mark.parametrize("scenario,keyword", [
        ("破冰", "话题切入点"),
        ("重连", "重建联系"),
        ("节日祝福", "应景得体"),
    ])
    def test_scenario_hint_injected(self, scenario, keyword):
        p = build_opening_prompt(scenario)
        assert keyword in p.system
        assert p.current == ""  # 开场无 current 消息
        assert p.history == []

    def test_unknown_scenario_falls_back(self):
        p = build_opening_prompt("不存在")
        assert "话题切入点" in p.system  # 回落到破冰

    def test_asks_three_candidates(self):
        p = build_opening_prompt("破冰")
        assert "3 条" in p.system

    def test_style_injected(self):
        p = build_opening_prompt("破冰", style="幽默")
        assert "语气幽默" in p.system


class TestOpeningPipeline:
    "FR-8.4 管线产出开场候选"

    def _pipe(self, raws):
        return ReplyPipeline(generator=lambda p: raws)

    def test_generates_candidates(self):
        pipe = self._pipe(["好久没联系，最近怎么样？|真诚问候\n看到你朋友圈的新动态，真不错～|自然找话题"])
        out = pipe.handle_opening("破冰")
        assert len(out.candidates) == 2
        assert out.candidates[0].text == "好久没联系，最近怎么样？"
        assert out.candidates[0].rationale == "真诚问候"

    def test_meta_and_unsafe_filtered(self):
        pipe = self._pipe(["请提供更多信息吧|元话语", "保证稳赚不赔|骗子", "今天天气不错|闲聊"])
        out = pipe.handle_opening("破冰")
        texts = [c.text for c in out.candidates]
        assert texts == ["今天天气不错"]

    def test_duplicate_dropped(self):
        pipe = self._pipe(["过节啦，节日快乐！|祝福", "过节啦，节日快乐！|同上"])
        out = pipe.handle_opening("节日祝福")
        assert len(out.candidates) == 1

    def test_empty_scenario_defaults(self):
        pipe = self._pipe(["在忙吗？|自然"])
        out = pipe.handle_opening("")
        assert len(out.candidates) == 1