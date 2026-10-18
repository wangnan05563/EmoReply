"""量一刀真机像素：图内行 vs 真气泡行的「环带纹理度」能不能分开。

背景：T02305 想用「整簇零气泡证据」剔除图内行，但 test_bubble_over_chat_background_image_kept
指出 #F2F2F2 气泡坐在亮聊天背景图上时同样零证据——只靠票数会吞消息。
本脚本在真机截图上同时量两类行的环带样本，看「样本是否平坦」能否做第二道区分。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emoreply.bubble_side import LineBox, judge_line, page_colors_at  # noqa: E402
from PIL import Image  # noqa: E402

SHOT = Path(__file__).resolve().parents[2] / "tmp/t02305_device/s12.png"
OFFSET = 275  # logcat 坐标 → 整屏截图（两条时间行标定，误差 ±7px）
OFFSETS = (12, 28, 48)

im = Image.open(SHOT).convert("RGB")
W, H = im.size
px = im.load()
def get_rgb(x, y):
    return px[max(0, min(W - 1, x)), max(0, min(H - 1, y))]

CASES = [
    ("图内·客户账户名称", (1642, 1668, 490, 745)),
    ("图内·客户账号", (1700, 1726, 468, 793)),
    ("图内·开户机构", (1758, 1828, 330, 784)),
    ("图内·客户号行", (1519, 1610, 369, 664)),
    ("图内·承乾机械", (1241, 1351, 381, 656)),
    ("真气泡·恒生减员", (322, 462, 252, 1011)),
    ("时间片·昨天16:59", (1943, 1978, 628, 812)),
    ("时间片·昨天15:33", (899, 934, 628, 812)),
]

for label, (t, b, l, r) in CASES:
    t, b = t + OFFSET, b + OFFSET
    box = LineBox(l, r, t, b)
    samples = []
    for d in OFFSETS:
        for f in (0.25, 0.5, 0.75):
            x = l + int((r - l) * f)
            y = t + int((b - t) * f)
            for p in ((x, t - d), (x, b + d), (l - d, y), (r + d, y)):
                if 0 <= p[0] < W and 0 <= p[1] < H:
                    samples.append(get_rgb(*p))
    lums = [sum(c) / 3 for c in samples]
    spread = [max(c) - min(c) for c in samples]
    ch_range = [max(c[i] for c in samples) - min(c[i] for c in samples) for i in range(3)]
    _, g, o = judge_line(get_rgb, box, W, H)
    print(f"{label:20s} n={len(samples)} 亮度极差={max(lums)-min(lums):6.1f} "
          f"跨度中位={sorted(spread)[len(spread)//2]:3d} 通道极差={ch_range} "
          f"绿={g} 白={o}")
