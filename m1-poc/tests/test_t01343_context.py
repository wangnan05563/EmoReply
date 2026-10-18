"""T01343：读屏上下文优化——history 条数上限 + 时间行分段。

用户反馈（2026-09-29）：
1. 上下文过多时已完结话题（前一小时）与当前话题生成混杂；
2. 读屏是否应把消息时间考虑进来。
方案：split_dialogue_turns 识别捕获层的「时间：」标记行（话题边界），
并对 history 做条数上限截断。时间行评估结论=有限纳入（仅作分段边界信号，
不做排序/绝对时间推理——OCR 时间行无日期年份粒度，多了不可靠）。
"""
import pytest

from emoreply.preprocess import (
    HISTORY_LIMIT,
    TIME_MARK_PREFIX,
    is_time_mark_line,
    split_dialogue_turns,
)


def sender_body(seq):
    return [(s, b) for s, b in seq]


# ===== 时间分段：标记行截断已完结话题 =====

class TestTimeMarkSegmentation:
    def test_mark_line_drops_previous_topic(self):
        """时间标记前的旧话题（含对方发言）整段丢弃，不回溯当 current。"""
        lines = [
            "对方：上午的方案你看完了吗",
            "我：看完了，回头说",
            "时间：下午 3:21",
            "对方：那现在定一下会议室",
        ]
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "那现在定一下会议室")
        # 旧话题两行必须被时间边界清掉
        assert ("对方", "上午的方案你看完了吗") not in history
        assert ("我", "看完了，回头说") not in history
        assert history == []

    def test_current_search_stops_at_mark(self):
        """标记后只有我方发言时不得回溯前段找 current（应解析失败）。"""
        lines = [
            "对方：中午吃什么",
            "我：随便",
            "时间：14:02",
            "我：我先出去了",
        ]
        history, current = split_dialogue_turns(lines)
        assert current is None
        assert history == []

    def test_multiple_marks_keep_last_segment(self):
        """多时间标记：current 与 history 均只取最后一个标记之后的段内内容。"""
        lines = [
            "对方：早上好",
            "时间：上午 9:00",
            "对方：文档发我了",
            "我：收到",
            "时间：下午 2:10",
            "对方：帮我看个 bug",
        ]
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "帮我看个 bug")
        # 「文档发我了/收到」隔着时间标记，属上一话题段，不进 history
        assert history == []

    def test_mark_tail_falls_back_to_previous_segment(self):
        """标记在末尾（防御性：真实读屏时序不会出现）——回落最后一个非空段。"""
        lines = [
            "对方：在吗",
            "时间：18:00",
        ]
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "在吗")
        assert history == []

    def test_no_mark_backcompat(self):
        """无标记行时行为与旧版完全一致（向后兼容）。"""
        lines = ["对方：a", "我：b", "对方：c", "我：d", "对方：e"]
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "e")
        assert sender_body(history) == [("对方", "a"), ("我", "b"),
                                        ("对方", "c"), ("我", "d")]

    def test_is_time_mark_line(self):
        assert is_time_mark_line("时间：下午 3:21")
        assert is_time_mark_line("时间：14:02")
        assert not is_time_mark_line("对方：时间：14:02 的时间戳不对")
        assert not is_time_mark_line("3:21 开会别迟到")
        assert not is_time_mark_line("")
        assert not is_time_mark_line(None)

    def test_time_prefix_constant(self):
        assert TIME_MARK_PREFIX == "时间："


# ===== history 条数上限 =====

class TestHistoryLimit:
    def test_default_limit_trims_old_turns(self):
        n = HISTORY_LIMIT + 3
        lines = [f"对方：消息{i}" for i in range(n)] + ["我：嗯"]
        lines.append("对方：最新这条")
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "最新这条")
        assert len(history) == HISTORY_LIMIT
        # 保留的是最近的 N 条（时序不变）：16 条 history 截尾 12 → 从「消息4」起
        assert history[0] == ("对方", f"消息{4}")
        assert history[-1] == ("我", "嗯")

    def test_within_limit_no_trim(self):
        lines = [f"对方：m{i}" for i in range(5)]
        lines.append("对方：最后")
        history, current = split_dialogue_turns(lines)
        assert len(history) == 5
        assert current == ("对方", "最后")

    def test_limit_none_disables(self):
        n = HISTORY_LIMIT + 5
        lines = [f"对方：m{i}" for i in range(n)]
        history, current = split_dialogue_turns(lines, history_limit=None)
        assert len(history) == n - 1  # 旧语义：末条为 current，其余全 history
        assert current == ("对方", f"m{n - 1}")

    def test_limit_applies_after_segmentation(self):
        """先分段再截断：截断只在当前话题段内进行。"""
        lines = ["对方：旧话题"] + [f"我：旧{i}" for i in range(20)]
        lines += ["时间：16:00", "对方：新话题开始了"]
        history, current = split_dialogue_turns(lines)
        assert current == ("对方", "新话题开始了")
        assert history == []
