"""T01158 体验快赢（M-Opt1）的平台无关事实源。

覆盖四项中可在 Python 侧锁定的两处逻辑：
- FR-7.3 语气强度滑杆：连续强度 → 提示词描述句（与 4 档风格并存）；
- FR-7.1 候选换一批：已给候选进提示词排除项 + 输出侧相似度去重（硬保证）。

FR-7.2 一键复制与 FR-9.1 暗色模式属端侧交互/UI，无平台无关逻辑，落 Android 侧。
"""
import pytest

from emoreply.compose import TONE_DEFAULT, build_exclude_hint, build_prompt, tone_hint
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.sanitize import filter_duplicates, is_duplicate, similarity


def _notice(body: str = "周末去爬山吗？一起呀") -> Notification:
    return Notification(source="manual", chat="c1", sender="小李", body=body, timestamp=1.0)


class TestToneSlider:
    def test_bands_are_distinct(self):
        hints = {tone_hint(t) for t in (0, 25, 50, 75, 100)}
        assert len(hints) == 5  # 5 档描述互异——滑杆两端语气差异必须可感知

    def test_extremes_differ(self):
        assert tone_hint(0) != tone_hint(100)
        assert "克制" in tone_hint(0)
        assert "活泼" in tone_hint(100)

    def test_out_of_range_clamped(self):
        assert tone_hint(-30) == tone_hint(0)
        assert tone_hint(999) == tone_hint(100)
        assert tone_hint("50") == tone_hint(TONE_DEFAULT)

    def test_prompt_tone_default_absent(self):
        # 回归：不传 tone 时提示词不含强度描述（旧行为不变）
        assert "语气强度" not in build_prompt(_notice(), []).system

    def test_prompt_tone_injected(self):
        bundle = build_prompt(_notice(), [], tone=90)
        assert "语气强度" in bundle.system
        assert tone_hint(90) in bundle.system

    def test_tone_coexists_with_style(self):
        bundle = build_prompt(_notice(), [], style="正式", tone=10)
        assert "正式" in bundle.system
        assert "语气强度" in bundle.system


class TestRegenerateExclusion:
    def test_hint_empty_when_no_exclude(self):
        assert build_exclude_hint(None) == ""
        assert build_exclude_hint([]) == ""

    def test_hint_lists_candidates(self):
        hint = build_exclude_hint(["好呀一起去", "我可能有事"])
        assert "好呀一起去" in hint and "我可能有事" in hint
        assert "不得重复" in hint

    def test_hint_caps_items(self):
        hint = build_exclude_hint([f"候选{i}" for i in range(30)])
        assert hint.count(")") <= 12  # EXCLUDE_LIMIT=8：避免挤占 token

    def test_blank_items_ignored(self):
        assert build_exclude_hint([" ", ""]) == ""

    def test_prompt_exclude_injected(self):
        bundle = build_prompt(_notice(), [], exclude=["好呀一起去"])
        assert "换一批" in bundle.system
        assert "好呀一起去" in bundle.system


class TestDuplicateDetection:
    def test_identical_is_duplicate(self):
        assert is_duplicate("好呀，一起去", ["好呀，一起去"])
        assert is_duplicate("好呀，一起去", ["好呀，一起去"])

    def test_punct_and_emoji_ignored(self):
        # 表情/标点差异不算差异：用户点「换一批」期待看到不同说法
        assert is_duplicate("好呀，一起去 😊", ["好呀一起去~"])
        assert similarity("好呀，一起去 😊", "好呀一起去~") == pytest.approx(1.0)

    def test_different_text_not_duplicate(self):
        assert not is_duplicate("我这周加班，下次约", ["好呀，周末一起去爬山"])

    def test_empty_is_duplicate(self):
        assert is_duplicate("", ["任意"])

    def test_no_exclude_nothing_filtered(self):
        assert filter_duplicates(["a", "b"], []) == ["a", "b"]

    def test_filter_removes_duplicates(self):
        out = filter_duplicates(["好呀一起去", "我这周有事"], ["好呀，一起去！"])
        assert out == ["我这周有事"]


class TestPipelineRegenerate:
    def _pipeline(self, replies=None):
        gen = replies or (lambda p: ["好呀，周末一起去 😊|语气轻松", "我这周加班，下次再约|诚实说明"])
        return ReplyPipeline(gen)

    def test_first_batch_untouched(self):
        out = self._pipeline().handle(_notice(), [])
        assert [c.text for c in out.candidates] == ["好呀，周末一起去 😊", "我这周加班，下次再约"]

    def test_regenerate_drops_previous(self):
        # 换一批：LLM 仍返回旧候选（软约束失效）→ 输出侧去重兜底，不得重复出现
        out = self._pipeline().handle(
            _notice(), [], exclude=["好呀，周末一起去 😊"])
        texts = [c.text for c in out.candidates]
        assert "好呀，周末一起去 😊" not in texts
        assert "我这周加班，下次再约" in texts

    def test_regenerate_with_all_duplicates_yields_empty(self):
        out = self._pipeline().handle(
            _notice(), [], exclude=["好呀，周末一起去", "我这周加班，下次再约"])
        assert out.candidates == []
