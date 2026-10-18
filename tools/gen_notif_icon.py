"""Generate Android notification small icons from the brand mono (white) silhouette.

Why this exists
---------------
From Android 5.0 (and mandatory on our minSdk=26) the system **tints** the
notification small icon to a single colour. A full-colour icon is flattened to
an unreadable grey/white blob - the classic "grey block" bug.

The correct source asset is therefore a **white silhouette on transparent
background**: `brand/mono/emoreply-inverse-*.png` (verified: all opaque pixels
are exactly (255,255,255); only alpha varies).

Sizing
------
Notification small icon = 24dp overall, content must live inside a 22dp safe
box (~2dp optical padding). We render each density at its 24dp size and inset
the silhouette so it never touches the edge.

Outputs
-------
  android-app/app/src/main/res/drawable-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_stat_emoreply.png
  android-app/app/src/main/res/drawable/ic_stat_emoreply.png        (default = xhdpi)
"""
from __future__ import annotations

import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "brand", "mono", "emoreply-inverse-512.png")
RES = os.path.join(ROOT, "android-app", "app", "src", "main", "res")

# density -> (canvas px, content px) ; 24dp canvas, ~22dp content
DENSITIES = {
    "mdpi": (24, 22),
    "hdpi": (36, 33),
    "xhdpi": (48, 44),
    "xxhdpi": (72, 66),
    "xxxhdpi": (96, 88),
}
NAME = "ic_stat_emoreply.png"


def build(src: Image.Image, canvas: int, content: int) -> Image.Image:
    """Downscale the silhouette to `content`, centre it on a `canvas` canvas.

    Only the **alpha** channel is resampled and the RGB is re-filled with pure
    white. Resizing RGBA directly (what PIL does by default) does not
    premultiply, so semi-transparent edge pixels come out as dark grey fringes
    - harmless for a logo, fatal here because the system tints the icon: grey
    pixels stay grey and the silhouette looks dirty at status-bar size.
    """
    # NOTE: do not use `paste(..., mask=...)` here - PIL's mask blending is not
    # premultiplied, so a white silhouette on a transparent canvas comes out as
    # grey (rgb = 255 * alpha). Build the canvas-sized alpha plane instead.
    alpha = src.getchannel("A").resize((content, content), Image.LANCZOS)
    canvas_alpha = Image.new("L", (canvas, canvas), 0)
    off = (canvas - content) // 2
    canvas_alpha.paste(alpha, (off, off))
    out = Image.new("RGBA", (canvas, canvas), (255, 255, 255, 0))
    out.putalpha(canvas_alpha)
    return out


def verify(path: str, canvas: int) -> None:
    """Assert the produced icon is white-only + has optical padding."""
    im = Image.open(path).convert("RGBA")
    assert im.size == (canvas, canvas), f"size {im.size} != {(canvas, canvas)}"
    px = im.load()
    non_white = 0
    opaque = 0
    min_x, min_y, max_x, max_y = canvas, canvas, -1, -1
    for y in range(canvas):
        for x in range(canvas):
            r, g, b, a = px[x, y]
            if a > 16:
                opaque += 1
                # every visible pixel must be pure white (tintable silhouette)
                if not (r == 255 and g == 255 and b == 255):
                    non_white += 1
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    assert opaque > 0, f"{path}: no visible pixels"
    assert non_white == 0, f"{path}: {non_white} non-white pixels (would tint badly)"
    # optical padding: silhouette must not bleed to the canvas edge
    assert min_x >= 1 and min_y >= 1, f"{path}: silhouette touches top/left edge"
    assert max_x <= canvas - 2 and max_y <= canvas - 2, f"{path}: silhouette touches bottom/right edge"
    print(
        f"  ok {os.path.basename(os.path.dirname(path)):10s} {canvas}x{canvas} "
        f"opaque={opaque:5d} bbox=({min_x},{min_y})-({max_x},{max_y})"
    )


def main() -> None:
    src = Image.open(SRC).convert("RGBA")
    print(f"source: {SRC} {src.size}")
    for density, (canvas, content) in DENSITIES.items():
        d = os.path.join(RES, f"drawable-{density}")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, NAME)
        build(src, canvas, content).save(p, "PNG")
        verify(p, canvas)
        if density == "xhdpi":
            dflt = os.path.join(RES, "drawable")
            os.makedirs(dflt, exist_ok=True)
            build(src, canvas, content).save(os.path.join(dflt, NAME), "PNG")
            print("  ok drawable     (default copy = xhdpi)")


if __name__ == "__main__":
    main()
