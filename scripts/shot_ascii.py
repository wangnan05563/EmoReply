#!/usr/bin/env python3
"""Render a screenshot as an ASCII luminance map + row band sampler.

Why: this host cannot eyeball images, so visual questions ("is this a chat
screen? where is the input bar?") are answered programmatically.

Usage:
    python scripts/shot_ascii.py FILE.png [--cols 68] [--rows 92]
    python scripts/shot_ascii.py FILE.png --band 0.90 1.0   # sample bottom rows
"""

import argparse
import struct
import zlib
from pathlib import Path

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None

RAMP = " .:-=+*#%@"

CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 4: 2, 6: 4}


def _png_chunks(data: bytes):
    """切 PNG 数据块，返回 (宽, 高, color_type, 拼接后的 IDAT)。"""
    pos = 8
    w = h = None
    idat = b""
    color_type = 6
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if ctype == b"IHDR":
            w, h, _, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + ln
    return w, h, color_type, idat


def _paeth(a: int, b: int, c: int) -> int:
    """Paeth 预测器：取与 p 最接近的邻居（等价于原嵌套三元）。"""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _filter_sub(line: bytearray, ch: int, stride: int) -> None:
    """filter type 1 (Sub)。"""
    for x in range(ch, stride):
        line[x] = (line[x] + line[x - ch]) & 0xFF


def _filter_up(line: bytearray, prev: bytearray, stride: int) -> None:
    """filter type 2 (Up)。"""
    for x in range(stride):
        line[x] = (line[x] + prev[x]) & 0xFF


def _filter_avg(line: bytearray, prev: bytearray, ch: int, stride: int) -> None:
    """filter type 3 (Average)。"""
    for x in range(stride):
        a = line[x - ch] if x >= ch else 0
        line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF


def _filter_paeth(line: bytearray, prev: bytearray, ch: int, stride: int) -> None:
    """filter type 4 (Paeth)。"""
    for x in range(stride):
        a = line[x - ch] if x >= ch else 0
        c = prev[x - ch] if x >= ch else 0
        line[x] = (line[x] + _paeth(a, prev[x], c)) & 0xFF


def _apply_filter(f: int, line: bytearray, prev: bytearray, ch: int, stride: int) -> None:
    """按 filter type 还原一行扫描线（0 与未知类型不处理）。"""
    if f == 1:
        _filter_sub(line, ch, stride)
    elif f == 2:
        _filter_up(line, prev, stride)
    elif f == 3:
        _filter_avg(line, prev, ch, stride)
    elif f == 4:
        _filter_paeth(line, prev, ch, stride)


def _gray_row(line: bytearray, w: int, ch: int):
    """一行像素转灰度（RGB/RGBA 用 601 亮度系数，灰度直接取值）。"""
    row = []
    for x in range(w):
        px = line[x * ch:(x + 1) * ch]
        row.append((px[0] * 299 + px[1] * 587 + px[2] * 114) // 1000 if ch >= 3 else px[0])
    return row


def load_png_gray(path: Path):
    """Minimal PNG loader fallback when PIL is unavailable (RGB/RGBA 8-bit)."""
    w, h, color_type, idat = _png_chunks(path.read_bytes())
    raw = zlib.decompress(idat)
    ch = CHANNELS_BY_COLOR_TYPE[color_type]
    stride = w * ch
    prev = bytearray(stride)
    out = []
    i = 0
    for _ in range(h):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        _apply_filter(f, line, prev, ch, stride)
        out.append(_gray_row(line, w, ch))
        prev = line
    return w, h, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--cols", type=int, default=68)
    ap.add_argument("--rows", type=int, default=88)
    ap.add_argument("--band", nargs=2, type=float, default=None)
    args = ap.parse_args()

    path = Path(args.png)
    if Image:
        im = Image.open(path).convert("L")
        W, H = im.size
        im_small = im.resize((args.cols, args.rows))
        px = im_small.load()
        get = lambda x, y: px[x, y]  # noqa: E731
        im_color = Image.open(path).convert("RGB").load()
    else:
        W, H, gray = load_png_gray(path)
        im_color = None
        im_small = None

        def get(x, y):
            gy = min(H - 1, int(y * H / args.rows))
            gx = min(W - 1, int(x * W / args.cols))
            return gray[gy][gx]

    print(f"# {path}  {W}x{H}  ASCII map {args.cols}x{args.rows}")
    print("+" + "-" * args.cols + "+")
    for y in range(args.rows):
        line = "".join(RAMP[min(9, get(x, y) * 10 // 256)] for x in range(args.cols))
        # row index in device pixels
        dev_y = int(y * H / args.rows)
        print(f"|{line}| {dev_y:4d}")
    print("+" + "-" * args.cols + "+")

    if args.band:
        y0 = int(args.band[0] * H)
        y1 = int(args.band[1] * H)
        step = max(1, (y1 - y0) // 24)
        print(f"\n# band rows {y0}..{y1} (step {step}) centre-column colour samples")
        print("  y     x=0.10      x=0.30      x=0.50      x=0.70      x=0.90")
        for y in range(y0, y1, step):
            cells = []
            for fx in (0.10, 0.30, 0.50, 0.70, 0.90):
                x = min(W - 1, int(fx * W))
                if im_color:
                    cells.append("#%02x%02x%02x" % im_color[x, y])
                else:
                    cells.append("%3d" % get(int(fx * args.cols), int(y * args.rows / H)))
            print(f"{y:5d}  " + "  ".join(cells))


if __name__ == "__main__":
    main()
