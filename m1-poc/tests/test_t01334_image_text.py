"""T01334：图片消息图内文字过滤（避免与气泡文字混淆）。

来源：T01145/T01143 观察项——图片消息（读书/视频封面）的图内文字被 OCR 读出，
坐标落在左侧，被当作「对方发言」混入生成 prompt。
两条路径两套信号：节点树用 ImageView bounds（硬证据），OCR 用行簇外围像素几何（T02305）。
"""
import pytest

from emoreply.preprocess import (
    OcrLine,
    Region,
    filter_image_text_clusters,
    filter_lines_in_regions,
)


def line(text, top, bottom=None, left=200, right=600, block=0):
    h = 40 if bottom is None else bottom - top
    return OcrLine(text, left, right, top, (bottom if bottom else top + h), block)


# ===== 节点树路径：图片区域矩形剔除 =====

class TestRegionFilter:
    def test_line_inside_image_region_dropped(self):
        region = Region(left=100, right=700, top=500, bottom=900)
        inside = line("图内文字", top=600, bottom=640)
        outside = line("气泡文字，你好", top=300, bottom=340)
        kept = filter_lines_in_regions([outside, inside], [region])
        assert kept == [outside]

    def test_no_region_keeps_everything(self):
        lines = [line("a", top=0), line("b", top=100)]
        assert filter_lines_in_regions(lines, []) == lines

    def test_region_edge_touch_not_dropped(self):
        """仅边缘相交（中心点在区域外）不剔除——防误杀。"""
        region = Region(left=100, right=700, top=500, bottom=900)
        # 行中心 y=520 落在区域内 → 剔除；用中心点判定而非相交判定
        l1 = line("x", top=505, bottom=535)   # cy=520 在区内
        l2 = line("y", top=460, bottom=499)   # cy=479 在区外
        kept = filter_lines_in_regions([l1, l2], [region])
        assert kept == [l2]


# ===== OCR 路径：图内文字行簇剔除（T02305 起为像素几何判据）=====
# 旧判据是「簇内无标点行占比 >= 0.8」，对银行/表单类截图（正文天生带冒号逗号）失效，
# 现判据只看簇外围环带：饱和且判不出气泡底色的样本占比 >= 0.5 → 整簇剔除。

W_IMG, H_IMG = 1080, 1920
PAGE = (237, 237, 237)      # 微信浅色页底
BUBBLE = (242, 242, 242)    # 对方白气泡
PHOTO_COLORS = ((200, 60, 90), (30, 30, 90), (0, 160, 93))


def photo_rgb(x, y):
    """x 100..700、y 500..800 是一块彩色图片内容，其余是页底。"""
    if 100 <= x < 700 and 500 <= y < 800:
        return PHOTO_COLORS[(y // 60) % 3]
    return PAGE


def bubble_rgb(x, y):
    """x 150..750、y 550..780 是一只白气泡（环带上取不到任何饱和色）。"""
    if 150 <= x < 750 and 550 <= y < 780:
        return BUBBLE
    return PAGE


class TestImageTextClusters:
    def test_dense_cluster_over_photo_dropped(self):
        """图片封面典型形态：3 行紧挨、四周是彩色内容 → 整簇剔除。"""
        cluster = [
            line("年度畅销书", top=600, bottom=640),
            line("限时五折", top=645, bottom=685),
            line("立即抢购", top=690, bottom=730),
        ]
        bubble = line("今天加班吗，要不要一起吃饭", top=300, bottom=340)
        kept = filter_image_text_clusters(cluster + [bubble],
                                          get_rgb=photo_rgb, width=W_IMG, height=H_IMG)
        assert kept == [bubble]

    def test_cluster_on_bubble_kept(self):
        """三行紧挨但环带全是气泡底色 → 是聊天连发，不是图内文字，保留。"""
        cluster = [
            line("在吗？", top=600, bottom=640),
            line("看到了吗？", top=645, bottom=685),
            line("回我一下！", top=690, bottom=730),
        ]
        assert filter_image_text_clusters(cluster, get_rgb=bubble_rgb,
                                          width=W_IMG, height=H_IMG) == cluster

    def test_two_lines_never_dropped(self):
        """少于 min_cluster(3) 行绝不剔除——防误杀短对话。"""
        two = [
            line("好的", top=600, bottom=640),
            line("收到", top=645, bottom=685),
        ]
        assert filter_image_text_clusters(two, get_rgb=photo_rgb,
                                          width=W_IMG, height=H_IMG) == two

    def test_loose_gap_not_a_cluster(self):
        """行间距 >= 行高 × 0.35 → 不构成簇（正常气泡间距）。"""
        lines = [
            line("第一行", top=600, bottom=640),
            line("第二行", top=680, bottom=720),   # gap 40，恰为行高×1.0
            line("第三行", top=760, bottom=800),
        ]
        assert filter_image_text_clusters(lines, get_rgb=photo_rgb,
                                          width=W_IMG, height=H_IMG) == lines

    def test_mixed_scene_only_cluster_removed(self):
        lines = [
            line("对方：明天几点集合", top=100, bottom=140),
            line("封面标题词", top=600, bottom=640),
            line("副标题词", top=645, bottom=685),
            line("促销词", top=690, bottom=730),
            line("我：八点吧，来得及吗", top=900, bottom=940),
        ]
        kept = filter_image_text_clusters(lines, get_rgb=photo_rgb,
                                          width=W_IMG, height=H_IMG)
        assert [l.text for l in kept] == ["对方：明天几点集合", "我：八点吧，来得及吗"]

    def test_no_pixels_keeps_everything(self):
        """拿不到像素时不做任何剔除：判据不许退回「按标点反推图内文字」。"""
        cluster = [
            line("年度畅销书", top=600, bottom=640),
            line("限时五折", top=645, bottom=685),
            line("立即抢购", top=690, bottom=730),
        ]
        assert filter_image_text_clusters(cluster) == cluster
