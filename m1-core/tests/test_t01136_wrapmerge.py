"""T01136：读屏 OCR 折行合并现象验收（几何判定）。

样本取自真机实测（TIM 聊天窗 SM-G9980 / 1440x3200）：同一句话被 ML Kit 分到
不同 textBlock，行间空白 32 / 行高 50 = 0.64 —— 旧跨 block 阈值 0.6 漏判。
"""
from emoreply.wrapmerge import (
    WRAP_MERGE_RATIO_CROSS_BLOCK,
    LineBox,
    merge_wrapped_lines,
)

# T01136 真机样本行1 t=714 b=764 l=252 r=1133 / 行2 t=796 b=846 l=250 r=770
TIM_LINE1 = LineBox("Hello,我已被你创建戌功,等你进一", 252, 1133, 714, 764, block=0)
TIM_LINE2 = LineBox("步开发部署我的灵魂。", 250, 770, 796, 846, block=1)


def test_threshold_relaxed_from_strict_value():
    assert WRAP_MERGE_RATIO_CROSS_BLOCK >= 0.8


def test_cross_block_aligned_lines_merged():
    """跨 block 但左右缘对齐 → 合并（本单核心现象）。"""
    out = merge_wrapped_lines([TIM_LINE1, TIM_LINE2])
    assert len(out) == 1
    assert out[0].text == "Hello,我已被你创建戌功,等你进一步开发部署我的灵魂。"
    # 合并后边界取并集（侧别判定用右缘，需取最大值）
    assert out[0].right == 1133


def test_ratio_below_even_stricter_cross_block_merges():
    """即使不算对齐信号，放宽后的 0.8 也能覆盖 0.64 的场景。"""
    a = LineBox("前半句", 300, 900, 100, 150, block=0)
    b = LineBox("后半句", 700, 1000, 182, 232, block=1)  # 左右缘均不对齐
    out = merge_wrapped_lines([a, b])
    assert len(out) == 1  # gap 32，小于 50×0.8 即 40


def test_adjacent_messages_not_merged():
    """相邻消息（间距远 + 不对齐）不得误并。"""
    a = LineBox("你好呀", 250, 900, 100, 150, block=0)
    b = LineBox("在吗", 700, 1000, 260, 310, block=1)  # gap 110，远超 50×0.8
    out = merge_wrapped_lines([a, b])
    assert len(out) == 2


def test_same_block_unchanged():
    a = LineBox("第一行", 250, 900, 100, 150, block=0)
    b = LineBox("第二行", 250, 880, 165, 215, block=0)
    assert len(merge_wrapped_lines([a, b])) == 1


def test_english_word_boundary_gets_space():
    # 英文在词边界折行 → 补空格（既有的 join_wrapped 契约；词内硬断不在本单范围）
    a = LineBox("Hello", 250, 900, 100, 150, block=0)
    b = LineBox("world again", 250, 900, 165, 215, block=0)
    out = merge_wrapped_lines([a, b])
    assert out[0].text == "Hello world again"


def test_cjk_join_without_space():
    a = LineBox("前半句内容", 250, 900, 100, 150, block=0)
    b = LineBox("后半句内容", 250, 900, 165, 215, block=0)
    out = merge_wrapped_lines([a, b])
    assert out[0].text == "前半句内容后半句内容"


def test_sorted_by_top_before_merging():
    # 输入顺序颠倒不影响结果（按 top 排序）
    out = merge_wrapped_lines([TIM_LINE2, TIM_LINE1])
    assert len(out) == 1
    assert out[0].text.startswith("Hello")
