"""T01136：OCR 折行合并（几何判定）的平台无关参考实现。

与其并列的 Android 实现见 `EmoReplyReaderService.mergeWrappedLines` —— 本模块是其
几何判定的事实源与回归网：IME/OCR 侧的细节（ML Kit block 编号）在这里用同样的
 `block` 字段表达，两侧判定规则保持一致。

判定规则：
- 行间空白 / 行高 < 比例 → 合并为同一条消息的折行；
- 比例按三档取值：同 block 1.0 / 左右缘近似对齐 1.0（跨 block 但对齐）/ 跨 block 0.8。

T01136 起因：TIM 聊天窗实测同一句话被 ML Kit 分到不同 block，ratio=0.64 高于旧的
跨 block 阈值 0.6 → 漏合并 → 下游 current 只取到半句。
"""
from dataclasses import dataclass
from typing import List, Optional

WRAP_MERGE_RATIO_SAME_BLOCK = 1.0
WRAP_MERGE_RATIO_ALIGNED = 1.0
WRAP_MERGE_RATIO_CROSS_BLOCK = 0.8
ALIGN_TOLERANCE_PX = 24


@dataclass
class LineBox:
    text: str
    left: int
    right: int
    top: int
    bottom: int
    block: int = 0

    @property
    def height(self) -> int:
        return self.bottom - self.top


def is_aligned(prev: LineBox, next_: LineBox) -> bool:
    """左右缘近似对齐（同一气泡的折行必对齐，相邻消息则错位）。"""
    return abs(prev.left - next_.left) <= ALIGN_TOLERANCE_PX or \
        abs(prev.right - next_.right) <= ALIGN_TOLERANCE_PX


def join_wrapped(prev: str, nxt: str) -> str:
    """折行拼接：中文交界直接相连，ASCII 字母数字交界补空格（英文断词）。"""
    if not prev:
        return nxt
    if not nxt:
        return prev
    a, b = prev[-1], nxt[0]
    if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
        return f"{prev} {nxt}"
    return prev + nxt


def merge_wrapped_lines(lines: List[LineBox]) -> List[LineBox]:
    """按「行间空白/行高」比例 + 对齐信号合并同一条消息的视觉折行。"""
    out: List[LineBox] = []
    last: Optional[LineBox] = None
    for line in sorted(lines, key=lambda b: b.top):
        if out and last is not None:
            gap = line.top - last.bottom
            line_h = max(line.height, last.height, 1)
            if line.block == last.block:
                ratio = WRAP_MERGE_RATIO_SAME_BLOCK
            elif is_aligned(last, line):
                ratio = WRAP_MERGE_RATIO_ALIGNED
            else:
                ratio = WRAP_MERGE_RATIO_CROSS_BLOCK
            if gap < line_h * ratio:
                merged = out[-1]
                merged.text = join_wrapped(merged.text, line.text)
                merged.left = min(merged.left, line.left)
                merged.right = max(merged.right, line.right)
                merged.bottom = max(merged.bottom, line.bottom)
                last = line
                continue
        out.append(LineBox(line.text, line.left, line.right, line.top, line.bottom, line.block))
        last = line
    return out


# ===== T01143：侧别判定（左对齐=他人，右对齐=本人）=====

SELF_SIDE_LEFT_PX = 450
SELF_SIDE_RIGHT_PX = 1140


def judge_side(left: int) -> str:
    """OCR 行侧别判定（与 kt EmoReplyReaderService.isMine 同构，返回「我」/「对方」）。

    气泡起点 left（>= 450 即从右半区开始 → 本人，微信 1440 宽实测对方气泡
    left≈250 恒定、本人长气泡可延伸到 ~500，取中点留余量）；否则 → 他人。

    T01149 修正：移除 right>1140 辅助信号（形参一并移除）——真机实测群聊对方长
    气泡右缘可达 1161（杨孟昭打卡消息 t=2159 l=237 r=1161），辅助信号反而误判为
    本人；「合并行 left 被噪声拉低」的场景由群聊噪声过滤（strip_group_noise）
    先行消化。
    """
    return "我" if left >= SELF_SIDE_LEFT_PX else "对方"
