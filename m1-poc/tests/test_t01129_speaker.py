"""T01129：读屏 AI 回复——串行解析完整对话上下文（侧别感知）。

验收点：
1. 侧别前缀解析（我：/对方：，全半角冒号兼容，无前缀向后兼容为对方）；
2. split_dialogue_turns 时序保持、current=最后一条对方发言；
3. 末行为我方发言时 current 回溯到最后一条对方发言；
4. 全部为我方发言（或空）→ current=None（解析失败，调用方不得进入生成）；
5. 上下文感知：我方发言以「我：xxx」渲染进提示词（完整双方上下文）。
"""
import pytest

from emoreply.compose import build_prompt
from emoreply.models import Notification
from emoreply.preprocess import (
    SPEAKER_ME,
    SPEAKER_OTHER,
    parse_speaker_line,
    split_dialogue_turns,
)


def _notice(body: str, sender: str = "对方") -> Notification:
    return Notification(
        source="manual", timestamp=1_000.0, body=body, sender=sender,
        chat="手动生成", title="手动生成",
    )


class TestParseSpeakerLine:
    def test_me_prefix_fullwidth(self):
        assert parse_speaker_line("我：儿子睡着了") == (SPEAKER_ME, "儿子睡着了")

    def test_me_prefix_halfwidth(self):
        assert parse_speaker_line("我: hi") == (SPEAKER_ME, "hi")

    def test_other_prefix(self):
        assert parse_speaker_line("对方：去旅游呀") == (SPEAKER_OTHER, "去旅游呀")

    def test_no_prefix_defaults_to_other(self):
        # 向后兼容：无标注行视为对方发言
        assert parse_speaker_line("去旅游呀") == (SPEAKER_OTHER, "去旅游呀")

    def test_blank_line(self):
        assert parse_speaker_line("   ") == ("", "")

    def test_none_like_empty(self):
        assert parse_speaker_line("") == ("", "")

    def test_prefix_only_no_body(self):
        assert parse_speaker_line("我：") == (SPEAKER_ME, "")


class TestSplitDialogueTurns:
    def test_sequential_ordering(self):
        lines = ["对方：去旅游呀", "我：好呀", "对方：儿子睡着了"]
        history, current = split_dialogue_turns(lines)
        assert current == (SPEAKER_OTHER, "儿子睡着了")
        assert history == [
            (SPEAKER_OTHER, "去旅游呀"),
            (SPEAKER_ME, "好呀"),
        ]

    def test_current_is_last_other_message(self):
        # 末行是我方发言：current 回溯到最后一条对方发言，其后我方发言留 history
        lines = ["对方：快来", "我：稍等", "我：马上到"]
        history, current = split_dialogue_turns(lines)
        assert current == (SPEAKER_OTHER, "快来")
        assert history == [(SPEAKER_ME, "稍等"), (SPEAKER_ME, "马上到")]

    def test_all_mine_no_current(self):
        # 全部为我方发言 → 无待回复对象（解析失败，不得进入生成）
        history, current = split_dialogue_turns(["我：好", "我：嗯"])
        assert current is None
        assert history == []

    def test_empty_input_no_current(self):
        history, current = split_dialogue_turns([])
        assert current is None
        assert history == []

    def test_blank_lines_skipped(self):
        lines = ["对方：a", "  ", "对方：b"]
        history, current = split_dialogue_turns(lines)
        assert current == (SPEAKER_OTHER, "b")
        assert history == [(SPEAKER_OTHER, "a")]


class TestContextAwarePrompt:
    def test_my_utterance_rendered_into_prompt(self):
        # 完整上下文感知：我方发言以「我：xxx」进 history，LLM 可见双方对话
        history = [_notice("好呀", sender=SPEAKER_ME), _notice("儿子睡着了")]
        bundle = build_prompt(_notice("去旅游呀"), history)
        assert "我：好呀" in bundle.history
        assert "对方：儿子睡着了" in bundle.history
        assert bundle.current == "对方：去旅游呀"

    def test_parse_to_prompt_pipeline(self):
        # 端到端：读屏标注行 → 解析 → 通知构造 → 提示词含双方发言
        lines = ["对方：去旅游呀", "我：好呀", "对方：儿子睡着了"]
        history, current = split_dialogue_turns(lines)
        assert current is not None
        hist = [_notice(b, sender=s) for s, b in history]
        cur = _notice(current[1], sender=current[0])
        bundle = build_prompt(cur, hist)
        assert "我：好呀" in bundle.history
        assert bundle.current == "对方：儿子睡着了"
