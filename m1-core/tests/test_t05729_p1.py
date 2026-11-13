"""T05729：读屏结构优化 P1——置信度分级与行框几何动态分界（事实源单测）。

覆盖：OcrLine.confidence 贯通与 is_low_confidence 判定；infer_side_boundary
双列推断（显著/不显著回退）；mine_at 动态/回退口径；fuse_node_sides 接入
动态分界后修正固定比例误判的短我方消息。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emoreply.preprocess import (  # noqa: E402
    LOW_CONFIDENCE_THRESHOLD, OcrLine, fuse_node_sides, infer_side_boundary,
    is_low_confidence, mine_at,
)

W = 1440


# ===== 置信度分级 =====

class TestConfidence:
    def test_default_confidence_full(self):
        """节点树路径无置信信号，默认 1.0 不误伤。"""
        assert OcrLine("x", 0, 1, 0, 1).confidence == 1.0

    def test_low_confidence_threshold(self):
        assert is_low_confidence(0.59)
        assert not is_low_confidence(0.60)      # 阈值含边界：0.6 不算疑似
        assert not is_low_confidence(None)      # 缺失不判疑似
        assert LOW_CONFIDENCE_THRESHOLD == 0.6


# ===== 动态列分界 =====

class TestInferSideBoundary:
    def test_two_column_distribution(self):
        """左簇 {100,120} + 右簇 {400,420}：分界 = 簇间隙中点 260。"""
        lefts = [100, 120, 400, 420]
        assert infer_side_boundary(lefts, W) == (120 + 400) // 2

    def test_single_column_returns_none(self):
        """全左（对方刷屏）：无右列证据 → 回退。"""
        assert infer_side_boundary([100, 120, 140, 160], W) is None

    def test_three_clusters_returns_none(self):
        """噪声行搅出的第三簇：证据不干净 → 回退。"""
        lefts = [100, 120, 400, 420, 700, 720]
        assert infer_side_boundary(lefts, W) is None

    def test_right_cluster_too_close_to_edge_returns_none(self):
        """「右簇」其实还在左半屏（< 0.2×屏宽）：不是右列。"""
        lefts = [100, 120, 250, 260]
        assert infer_side_boundary(lefts, W) is None

    def test_insufficient_lines_returns_none(self):
        assert infer_side_boundary([100, 400], W) is None   # 每簇仅 1 行
        assert infer_side_boundary([], W) is None

    def test_out_of_range_lefts_ignored(self):
        """越界左缘（OCR 偶发负值/超宽）剔除后仍可判定。"""
        assert infer_side_boundary([-5, 100, 120, 400, 420, 2000], W) == (120 + 400) // 2


class TestMineAt:
    def test_dynamic_boundary_wins(self):
        assert mine_at(400, 260, W)
        assert not mine_at(100, 260, W)

    def test_fallback_ratio_when_no_boundary(self):
        """无动态分界 → 固定 0.3125（T01143 口径不变）。"""
        assert not mine_at(400, None, W)         # 400 < 450
        assert mine_at(500, None, W)

    def test_custom_default_ratio(self):
        assert mine_at(300, None, W, default_ratio=0.2)


# ===== fuse_node_sides 接入动态分界 =====

class TestFuseWithDynamicBoundary:
    def test_short_mine_bubble_rescued_by_dynamic_boundary(self):
        """核心收益：右列短我方气泡左缘低于固定阈值 450，旧判法误判「对方」；
        节点框左缘呈显著双列（每簇 ≥2 行）→ 动态分界 260 修正为「我」。"""
        ln = OcrLine("嗯", 400, 440, 1000, 1040)
        nodes = [
            (100, 140, 100, 140, "在吗？"),          # 左簇（对方）
            (120, 160, 500, 540, "好的"),            # 左簇
            (400, 440, 980, 1060, "嗯"),             # 右簇（我方），含 OCR 行中心
            (420, 460, 1980, 2060, "嗯，知道了"),    # 右簇
        ]
        assert fuse_node_sides([ln], ["对方"], nodes, W) == ["我"]

    def test_no_double_column_keeps_fixed_ratio(self):
        """节点框无双列分布 → 固定口径不变（400 < 450 仍判对方）。"""
        ln = OcrLine("嗯", 400, 440, 1000, 1040)
        nodes = [(400, 440, 980, 1060, "嗯")]
        assert fuse_node_sides([ln], ["对方"], nodes, W) == ["对方"]
