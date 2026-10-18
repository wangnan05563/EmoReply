"""离线回放 T02305 新链路（A′′ 簇过滤 + C 零证据不落「我」）到真机截图。

设备暂时连不上时用的**指示性**验证：logcat 里打印的是折行合并**之后**的行框，
所以这里按真机原始行的行高（44px）把高框还原成子行，再喂给新链路。真实结论仍以
装上新 APK 后的真机读屏为准。

用法：python scripts/replay_t02305_newchain.py <截图.png> <logcat.txt> <日志时间前缀> <y偏移>
"""
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emoreply.bubble_side import LineBox, judge_side_with_fallback  # noqa: E402
from emoreply.preprocess import (  # noqa: E402
    OcrLine, filter_image_text_clusters, filter_non_text_noise,
    merge_wrapped_lines,
)

RAW_LINE_H = 44  # 真机原始 OCR 行高档位（银行截图正文实测 40~48px）

LINE_RE = re.compile(
    r'(?P<ts>[\d-]+ (?P<clock>[\d:.]+)).*EmoReplyReader.*ocr: line '
    r't=(?P<t>-?\d+) b=(?P<b>-?\d+) l=(?P<l>-?\d+) r=(?P<r>-?\d+) "(?P<txt>.*)"')


def log_lines(path, clock, offset):
    out = []
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE_RE.search(ln)
        if m and m["clock"].startswith(clock):
            out.append(OcrLine(m["txt"], int(m["l"]) , int(m["r"]),
                               int(m["t"]) + offset, int(m["b"]) + offset, 0))
    return out


def decompose(merged):
    """把合并后的高框拆回原始子行（左右缘沿用并集框——指示性近似的来源）。"""
    out = []
    for ln in merged:
        n = max(1, round(ln.height / RAW_LINE_H))
        pitch = ln.height / n
        for k in range(n):
            out.append(OcrLine(ln.text if k == 0 else f"{ln.text[:8]}…{k}",
                               ln.left, ln.right,
                               int(ln.top + k * pitch),
                               int(ln.top + (k + 1) * pitch - 4), ln.block))
    return out


def main():
    shot, log, clock, offset = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    im = Image.open(shot).convert("RGB")
    W, H = im.size
    px = im.load()

    def get_rgb(x, y):
        return px[max(0, min(W - 1, x)), max(0, min(H - 1, y))]

    merged = log_lines(log, clock, offset)
    raw = decompose(merged)
    print(f"日志行 {len(merged)} 条 → 还原原始行 {len(raw)} 条（{clock}，y+{offset}）")

    after_cluster = filter_image_text_clusters(raw, get_rgb=get_rgb, width=W, height=H)
    dropped = [l for l in raw if l not in after_cluster]
    print(f"\n== 簇过滤剔除 {len(dropped)} 行 ==")
    for l in dropped:
        print(f"   t={l.top} l={l.left} \"{l.text[:26]}\"")
    kept = filter_non_text_noise(merge_wrapped_lines(after_cluster))
    print(f"== 合并+噪声过滤后剩 {len(after_cluster)} → {len(kept)} 行 ==")
    print("\n== 最终上下文（判侧后）==")
    for l in kept:
        call = judge_side_with_fallback(get_rgb, LineBox(l.left, l.right, l.top, l.bottom),
                                        W, H)
        prefix = "我：" if call.side == "mine" else "对方："
        flag = "" if call.by_color else "  [存疑 g=%d o=%d]" % (call.green_votes,
                                                                call.other_votes)
        print(f"   {prefix}{l.text[:26]}{flag}")


if __name__ == "__main__":
    main()
