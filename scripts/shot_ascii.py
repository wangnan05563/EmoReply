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


def load_png_gray(path: Path):
    """Minimal PNG loader fallback when PIL is unavailable (RGB/RGBA 8-bit)."""
    data = path.read_bytes()
    pos = 8
    w = h = None
    idat = b""
    color_type = 6
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if ctype == b"IHDR":
            w, h, depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = w * ch
    prev = bytearray(stride)
    out = []
    i = 0
    for _ in range(h):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        row = []
        for x in range(w):
            px = line[x * ch:(x + 1) * ch]
            row.append((px[0] * 299 + px[1] * 587 + px[2] * 114) // 1000 if ch >= 3 else px[0])
        out.append(row)
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
