"""T01159 候选回复支持表情。

三处同构改动（py 事实源 → kt 镜像）：
1. compose.EMOJI_HINT：提示词注入「1-2 个、贴合语气、不堆砌」软约束；
2. sanitize.normalize_emoji：输出侧兜底——连续重复压成一个，总数超上限丢弃靠后的；
3. sanitize.strip_emoji：开关关闭时剥干净，保证「关=无表情」。

表情是正文的一部分：markdown/HTML 清洗（C3/C25）不得动它。
"""
import pytest

from emoreply.compose import EMOJI_HINT, build_prompt
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.sanitize import (count_emoji_units, normalize_emoji, split_rationale,
                               strip_emoji)


def _notice(body: str, sender: str = "小李") -> Notification:
    return Notification(source="manual", chat="c1", sender=sender, body=body, timestamp=1.0)


class TestPromptInjection:
    def test_default_prompt_has_emoji_hint(self):
        bundle = build_prompt(_notice("周末去爬山吗"), [])
        assert EMOJI_HINT.strip() in bundle.system
        assert "emoji" in bundle.system

    def test_emoji_off_has_no_hint(self):
        bundle = build_prompt(_notice("周末去爬山吗"), [], emoji=False)
        assert "emoji" not in bundle.system

    def test_emoji_off_matches_legacy_prompt(self):
        # 回归：关闭表情时提示词与旧版完全一致（不含表情约束）
        off = build_prompt(_notice("在吗"), [], emoji=False)
        assert "5)" not in off.system

    def test_hint_coexists_with_classification_extra(self):
        # 回归：分类信息（4) 消息类型…）与表情约束可共存
        bundle = build_prompt(_notice("下午几点开会？"), [], intent="问询", emotion="中性",
                              urgency="普通")
        assert "消息类型" in bundle.system
        assert "emoji" in bundle.system


class TestCleaningKeepsEmoji:
    def test_emoji_survives_candidate_parsing(self):
        text, rationale = split_rationale("好呀，周末一起去 😊|语气轻松")
        assert text == "好呀，周末一起去 😊"
        assert rationale == "语气轻松"

    def test_emoji_not_stripped_as_markdown(self):
        # 回归：* 与 _ 清洗不得吃掉表情（表情不含格式符，此用例锁定该行为）
        text, _ = split_rationale("太棒了 🎉|情绪共鸣")
        assert text.endswith("🎉")


class TestNormalizeEmoji:
    def test_single_emoji_unchanged(self):
        assert normalize_emoji("好呀 😊") == "好呀 😊"

    def test_two_emoji_unchanged(self):
        assert normalize_emoji("好呀 😊 等你 👍") == "好呀 😊 等你 👍"

    def test_repeated_run_collapsed(self):
        assert normalize_emoji("恭喜 🎉🎉🎉") == "恭喜 🎉"

    def test_spaced_repeat_collapsed(self):
        assert normalize_emoji("恭喜 🎉 🎉 🎉") == "恭喜 🎉"

    def test_over_cap_drops_later_ones(self):
        out = normalize_emoji("好 😊 棒 👍 走 🚀 冲 🎉", max_units=2)
        assert count_emoji_units(out) == 2
        assert out.startswith("好 😊 棒 👍")

    def test_no_emoji_text_unchanged(self):
        assert normalize_emoji("好的，那就这么定了") == "好的，那就这么定了"

    def test_zwj_sequence_counts_as_one(self):
        # 组合表情（👨‍👩‍👧）内部带 ZWJ，收敛时按 1 个单元计
        assert count_emoji_units("全家福 👨‍👩‍👧") == 1
        assert normalize_emoji("全家福 👨‍👩‍👧 真棒 😊") == "全家福 👨‍👩‍👧 真棒 😊"

    def test_empty_text(self):
        assert normalize_emoji("") == ""
        assert strip_emoji("") == ""


class TestStripEmoji:
    def test_strip_all(self):
        assert strip_emoji("好呀 😊 等你 👍") == "好呀 等你"

    def test_strip_keeps_text(self):
        assert strip_emoji("恭喜🎉🎉") == "恭喜"


class TestPipelineEmoji:
    def _pipeline(self, emoji=True, generator=None):
        gen = generator or (lambda p: ["好呀，一起去 😊🎉🎉|语气轻松"])
        return ReplyPipeline(gen, emoji=emoji)

    def test_pipeline_keeps_and_normalizes_emoji(self):
        out = self._pipeline().handle(_notice("周末去爬山吗？一起呀"), [])
        assert out.candidates, out.notice
        assert count_emoji_units(out.candidates[0].text) <= 2
        assert "😊" in out.candidates[0].text

    def test_pipeline_off_strips_emoji(self):
        out = self._pipeline(emoji=False).handle(_notice("周末去爬山吗？一起呀"), [])
        assert out.candidates, out.notice
        assert count_emoji_units(out.candidates[0].text) == 0


if __name__ == "__main__":
    pytest.main([__file__])
