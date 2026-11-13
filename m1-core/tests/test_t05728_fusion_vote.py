"""T05728：节点树×OCR 交叉验证判侧与跨屏变体投票（m1-core 事实源单测）。

覆盖 fuse_node_sides（硬证据覆盖/无匹配保原判/文本不相似不误配/左右缘比例判侧）
与 vote_screen_batches（精确重复退化为 T01145 行为/OCR 变体并簇取众数/时序按首现）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emoreply.preprocess import (  # noqa: E402
    OcrLine, fuse_node_sides, vote_screen_batches,
)

W = 1440


def _node(left, right, top, bottom, text):
    return (left, right, top, bottom, text)


class TestFuseNodeSides:
    def test_node_evidence_overrides_ocr_side(self):
        """OCR 行中心落在节点框内且文本相似 → 采用节点位置判定（硬证据）。"""
        ln = OcrLine("明天上午十点开会", 900, 1300, 1000, 1040)
        node = _node(880, 1330, 980, 1060, "明天上午十点开会")
        assert fuse_node_sides([ln], ["对方"], [node], W) == ["我"]

    def test_no_match_keeps_original(self):
        ln = OcrLine("在吗？", 100, 400, 1000, 1040)
        assert fuse_node_sides([ln], ["对方"], [], W) == ["对方"]

    def test_center_outside_node_not_matched(self):
        """行中心不在节点框内：几何不匹配即跳过，不看文本。"""
        ln = OcrLine("在吗？", 100, 400, 1000, 1040)
        node = _node(880, 1330, 980, 1060, "在吗？")
        assert fuse_node_sides([ln], ["对方"], [node], W) == ["对方"]

    def test_dissimilar_text_not_matched(self):
        """中心在框内但文本不相似（相邻气泡同侧）：不误配。"""
        ln = OcrLine("明天上午十点开会", 900, 1300, 1000, 1040)
        node = _node(880, 1330, 980, 1060, "周末一起爬山好吗？")
        assert fuse_node_sides([ln], ["对方"], [node], W) == ["对方"]

    def test_node_left_edge_ratio_judges_side(self):
        """节点框左缘 ≥ 屏宽×0.3125 → 我；否则对方（与 kt isMine 同源）。"""
        ln1 = OcrLine("在吗？", 500, 700, 1000, 1040)    # 中心在 mine_node 内
        ln2 = OcrLine("在吗？", 150, 350, 2000, 2040)    # 中心在 other_node 内
        mine_node = _node(500, 750, 980, 1060, "在吗？")       # 500 ≥ 450 → 我
        other_node = _node(100, 400, 1980, 2060, "在吗？")     # 100 < 450 → 对方
        assert fuse_node_sides([ln1, ln2], ["对方", "我"], [mine_node, other_node], W) == ["我", "对方"]

    def test_ocr_error_tolerated_by_ratio(self):
        """节点文本与 OCR 文本相似（OCR 错一字）仍匹配——容错是本功能的价值之一。"""
        ln = OcrLine("明天上午十点开畬", 900, 1300, 1000, 1040)
        node = _node(400, 1350, 980, 1060, "明天上午十点开会")
        assert fuse_node_sides([ln], ["我"], [node], W) == ["对方"]


class TestVoteScreenBatches:
    def test_exact_duplicates_deduped(self):
        """精确重复（T01145 场景）行为不变：保留首现。"""
        batches = [["对方：你好", "我：你好"], ["对方：你好", "对方：在吗"]]
        assert vote_screen_batches(batches) == ["对方：你好", "我：你好", "对方：在吗"]

    def test_variant_merged_to_majority(self):
        """同一行被读出两个变体：取众数，不再双行并存。"""
        batches = [["对方：明天上午十点开会"],
                   ["对方：明天上午十点开畬", "对方：明天上午十点开会"]]
        assert vote_screen_batches(batches) == ["对方：明天上午十点开会"]

    def test_order_by_first_occurrence(self):
        batches = [["A", "B"], ["C", "B"]]
        assert vote_screen_batches(batches) == ["A", "B", "C"]

    def test_dissimilar_lines_both_kept(self):
        """相似度不足的两行是不同消息，都不丢。"""
        batches = [["对方：今天天气很好"], ["对方：明天上午十点开会"]]
        assert vote_screen_batches(batches) == ["对方：今天天气很好", "对方：明天上午十点开会"]

    def test_single_batch_same_as_exact_dedupe(self):
        batches = [["对方：你好", "", "我：你好", "对方：你好"]]
        assert vote_screen_batches([batches[0]]) == ["对方：你好", "我：你好"]

    def test_tie_takes_first_variant(self):
        """两变体各出现一次：取先出现者（时序稳定，不引入随机性）。"""
        batches = [["对方：明天上午十点开畬"], ["对方：明天上午十点开会"]]
        assert vote_screen_batches(batches) == ["对方：明天上午十点开畬"]
