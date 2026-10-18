"""真机取证回放：把 logcat 里的 OCR 行框映射到设备截图，逐点打印判侧样本色。

目的：解释 T02305 复测为什么仍出现「我：客户账户名称…」——到底是 B 的锚点盒
放行了拍屏照片的绿灰底色，还是坐标映射没对齐。
"""
import sys
from PIL import Image

sys.path.insert(0, ".")
from emoreply.bubble_side import (  # noqa: E402
    LineBox, MINE, OTHER, classify_sample, page_colors_at, probe_points,
    decide_side,
)

SHOT = sys.argv[1] if len(sys.argv) > 1 else "../tmp/t02305_device/s11.png"
# logcat 的 ocr: line 坐标以截图裁剪区为原点，回放到整屏截图需补上这个偏移
# （用两条时间行「昨天11:42 / 昨天15:33」在截图里的实测位置标定，误差 ±7px）
OFFSET = int(sys.argv[2]) if len(sys.argv) > 2 else 0
im = Image.open(SHOT).convert("RGB")
W, H = im.size
px = im.load()


def get_rgb(x, y):
    x = max(0, min(W - 1, x))
    y = max(0, min(H - 1, y))
    return px[x, y]


# (log 行框, 期望文本) —— 取自 10-17 18:56:30 那次读屏的 ocr: line 日志
LINES = [
    ((899, 1189, 362, 826), "昨天15:33 2026/10/1田欢欢二七支"),
    ((1241, 1351, 381, 656), "0203100628767|河南承乾机械设备有限"),
    ((1519, 1610, 369, 664), "客户号: 0203643587199产品代号:单"),
    ((1642, 1668, 490, 745), "客户账户名称: 管理测試1"),
    ((1700, 1726, 468, 793), "客户账号:00724071200000002"),
    ((1758, 1828, 330, 784), "开户机构:交通路支行1600724070票据管理"),
    ((322, 462, 252, 1011), "恒生减员651人（真·对方白气泡）"),
    ((1943, 1978, 628, 812), "昨天16:59（时间行）"),
]

for (top, bottom, left, right), label in LINES:
    top, bottom = top + OFFSET, bottom + OFFSET
    box = LineBox(left, right, top, bottom)
    pl, pr = page_colors_at(get_rgb, (top + bottom) // 2, W, H)
    pts = probe_points(box, W, H)
    votes = {MINE: 0, OTHER: 0, None: 0}
    colors = []
    for x, y in pts:
        rgb = get_rgb(x, y)
        side = classify_sample(rgb, pl, pr)
        votes[side] = votes.get(side, 0) + 1
        colors.append(rgb)
    green = votes.get(MINE, 0)
    other = votes.get(OTHER, 0)
    print(f"--- {label}")
    print(f"    box=({top},{bottom},{left},{right}) page_l={pl} page_r={pr}")
    print(f"    samples={len(pts)} green={green} other={other} void={votes.get(None,0)}"
          f" decide={decide_side(green, other)}")
    uniq = {}
    for c in colors:
        uniq[c] = uniq.get(c, 0) + 1
    top5 = sorted(uniq.items(), key=lambda kv: -kv[1])[:6]
    print("    高频样本色: " + ", ".join(f"{c}x{n}" for c, n in top5))
