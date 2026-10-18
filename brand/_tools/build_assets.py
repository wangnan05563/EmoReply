# -*- coding: utf-8 -*-
"""
EmoReply 品牌资产构建脚本

输入：_source/ 下 AI 生成的 1024x1024 候选图（白底）
输出：
  logo/     矢量 SVG（主标 / 单色 / 反白 / 横向组合）
  png/      16/32/48/64/128/256/512/1024 透明背景 PNG
  ico/      Windows 多尺寸 ICO
  icns/     macOS ICNS（Electron 打包备用）
  mono/     单色 / 反白变体 PNG
  android/  可直接拷贝的 mipmap 图标
  preview/  应用场景预览图

背景去背策略：从画布四边做近白色 flood fill，再对边界 3px 做亮度反演 alpha + 去白边（un-premultiply）。
"""
import os
import struct
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
# 定稿源图（气泡+爱心+星光，深蓝→青）。换稿指向 _source/cand-1-violet.png 或 cand-2-magenta.png 后重跑即可
SRC = ROOT / "_source" / "cand-3-navy-teal.png"

OUT_PNG = ROOT / "png"
OUT_ICO = ROOT / "ico"
OUT_ICNS = ROOT / "icns"
OUT_MONO = ROOT / "mono"
OUT_ANDROID = ROOT / "android"
OUT_PREVIEW = ROOT / "preview"
for d in (OUT_PNG, OUT_ICO, OUT_ICNS, OUT_MONO, OUT_ANDROID, OUT_PREVIEW):
    d.mkdir(parents=True, exist_ok=True)

RESAMPLE = Image.Resampling.LANCZOS
BRAND_NAVY = (8, 39, 94)      # #08275E
BRAND_TEAL = (33, 175, 175)   # #21AFAF


# ---------------------------------------------------------------- 背景去除
def strip_white_bg(im: Image.Image) -> Image.Image:
    """白底 → 透明，边界做去白边处理。"""
    im = im.convert("RGB")
    arr = np.asarray(im).astype(np.float32)          # H,W,3
    lum = arr.mean(axis=2)
    sat = arr.max(axis=2) - arr.min(axis=2)
    bg_like = (lum > 224) & (sat < 26)

    h, w = lum.shape
    visited = np.zeros((h, w), dtype=bool)
    dq: deque = deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg_like[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg_like[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))

    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and bg_like[ny, nx]:
                visited[ny, nx] = True
                dq.append((ny, nx))

    # 边界带：dilate 3 次
    band = visited.copy()
    for _ in range(3):
        nb = band.copy()
        nb[1:, :] |= band[:-1, :]
        nb[:-1, :] |= band[1:, :]
        nb[:, 1:] |= band[:, :-1]
        nb[:, :-1] |= band[:, 1:]
        band = nb
    band &= ~visited

    alpha = np.where(visited, 0.0, 1.0)
    # 边界带按亮度反推覆盖率（去白边）
    a_band = np.clip((250.0 - lum) / 34.0, 0.0, 1.0)
    alpha[band] = np.clip(a_band[band], 0.08, 1.0)

    a = alpha[..., None]
    rgb = np.where(a > 0.02, np.clip((arr - (1.0 - a) * 255.0) / np.maximum(a, 0.02), 0, 255), arr)
    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def make_master(src: Path, size: int = 1024, fill_ratio: float = 0.88) -> Image.Image:
    img = strip_white_bg(Image.open(src))
    bbox = img.split()[3].getbbox()
    img = img.crop(bbox)
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    target = int(size * fill_ratio)
    canvas = canvas.resize((target, target), RESAMPLE)
    master = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    master.paste(canvas, ((size - target) // 2, (size - target) // 2))
    return master


# ---------------------------------------------------------------- 位图族
def write_pngs(master: Image.Image) -> None:
    for s in (1024, 512, 256, 128, 64, 48, 32, 16):
        master.resize((s, s), RESAMPLE).save(OUT_PNG / f"emoreply-{s}.png")


def write_ico(master: Image.Image) -> None:
    """手工写 ICO 目录 + PNG 条目（Lanczos 缩放，Windows Vista+ / electron-builder 通用）。"""
    import io
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = []
    for s in sizes:
        buf = io.BytesIO()
        master.resize((s, s), RESAMPLE).save(buf, format="PNG")
        blobs.append(buf.getvalue())

    n = len(sizes)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries, datas = b"", b""
    for s, blob in zip(sizes, blobs):
        dim = s if s < 256 else 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        datas += blob
    (OUT_ICO / "emoreply.ico").write_bytes(header + entries + datas)


def write_icns(master: Image.Image) -> None:
    """PNG 版 ICNS（ic07/ic08/ic09/ic10），Electron 打 macOS 包备用。"""
    entries = b""
    for tag, s in (("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024)):
        import io
        buf = io.BytesIO()
        master.resize((s, s), RESAMPLE).save(buf, format="PNG")
        data = buf.getvalue()
        entries += tag.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    blob = b"icns" + struct.pack(">I", len(entries) + 8) + entries
    (OUT_ICNS / "emoreply.icns").write_bytes(blob)


# ---------------------------------------------------------------- 单色变体
def _erode(mask: np.ndarray, k: int) -> np.ndarray:
    """4 邻域腐蚀 k 次（用于剥掉徽标边缘的外发光环，避免污染图形 mask）。"""
    m = mask
    for _ in range(k):
        n = m.copy()
        n[1:, :] = np.minimum(n[1:, :], m[:-1, :])
        n[:-1, :] = np.minimum(n[:-1, :], m[1:, :])
        n[:, 1:] = np.minimum(n[:, 1:], m[:, :-1])
        n[:, :-1] = np.minimum(n[:, :-1], m[:, 1:])
        m = n
    return m


def _masks(master: Image.Image):
    """返回 (badge_alpha, glyph_alpha, heart_alpha)，取值 0..1 浮点。"""
    a = np.asarray(master).astype(np.float32)
    alpha = a[..., 3] / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = (r + g + b) / 3.0
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    # ⚠ AI 图在徽标外缘有一圈淡色外发光，会被「亮且低饱和」判据误判成图形 →
    #   先把徽标 alpha 腐蚀 6px，只在徽标内部区域提取图形（自适应前景裁切依赖该 bbox）
    inner = _erode((alpha > 0.9).astype(np.float32), 6)
    # 亮且低饱和（白气泡 / 淡青星光 / 浅蓝爱心）
    glyph = np.clip((lum - 168.0) / 52.0, 0.0, 1.0) * (sat <= 110.0) * (b >= r - 8)
    glyph = glyph * inner
    # 爱心专用色 #8DB9D5 ± 容差
    heart = ((np.abs(r - 141) < 34) & (np.abs(g - 185) < 34) & (np.abs(b - 213) < 34)).astype(np.float32)
    heart = heart * inner
    return alpha, glyph, heart


def write_mono(master: Image.Image) -> None:
    alpha, glyph, heart = _masks(master)

    # 单色版：实心徽标 + 图形镂空（currentColor #08275E），用于单色打印 / 深色底
    solid_a = np.clip(alpha - glyph * alpha, 0, 1)
    solid = np.zeros((*solid_a.shape, 4), np.uint8)
    solid[..., 0], solid[..., 1], solid[..., 2] = BRAND_NAVY
    solid[..., 3] = (solid_a * 255).astype(np.uint8)
    mono = Image.fromarray(solid, "RGBA")

    # 反白版：白色图形（浅蓝爱心镂空），用于深色背景
    inv_a = np.clip(glyph - heart * 0.85, 0, 1)
    inv = np.zeros((*inv_a.shape, 4), np.uint8)
    inv[..., 0] = inv[..., 1] = inv[..., 2] = 255
    inv[..., 3] = (inv_a * 255).astype(np.uint8)
    inv_img = Image.fromarray(inv, "RGBA")

    for name, img in (("emoreply-mono", mono), ("emoreply-inverse", inv_img)):
        for s in (512, 256, 128, 64, 32):
            img.resize((s, s), RESAMPLE).save(OUT_MONO / f"{name}-{s}.png")


# ---------------------------------------------------------------- Android
def write_android(master: Image.Image) -> None:
    alpha, glyph, heart = _masks(master)
    # 自适应前景：白色图形（爱心镂空）
    fg_a = np.clip(glyph - heart * 0.85, 0, 1)
    fg = np.zeros((*fg_a.shape, 4), np.uint8)
    fg[..., 0] = fg[..., 1] = fg[..., 2] = 255
    fg[..., 3] = (fg_a * 255).astype(np.uint8)
    fg_img = Image.fromarray(fg, "RGBA")

    # 自适应背景：竖向渐变（深蓝 → 青）
    bg = Image.new("RGB", (1024, 1024))
    d = ImageDraw.Draw(bg)
    for y in range(1024):
        t = y / 1023.0
        d.line([(0, y), (1024, y)],
               fill=tuple(int(BRAND_NAVY[i] + (BRAND_TEAL[i] - BRAND_NAVY[i]) * t) for i in range(3)))

    # 传统方形/圆形启动器图标（48dp 基准）
    densities = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for name, px in densities.items():
        d_out = OUT_ANDROID / f"mipmap-{name}"
        d_out.mkdir(parents=True, exist_ok=True)
        master.resize((px, px), RESAMPLE).save(d_out / "ic_launcher.png")
        master.resize((px, px), RESAMPLE).save(d_out / "ic_launcher_round.png")

    # 自适应图标（Android 8+）：图层画布 108dp，图形置于 72dp 安全区 → 取 62% 居中
    adaptive = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
    for name, px in adaptive.items():
        d_out = OUT_ANDROID / f"drawable-{name}"
        d_out.mkdir(parents=True, exist_ok=True)
        # 先裁到图形自身 bbox，再整体缩放到 62%（= 内容占画布 62%，落在 72dp 安全区内）
        # 用 alpha>128 的实心区取 bbox（含柔边的 mask 会让 bbox 偏大、缩放后图形偏小）
        solid_a = fg_img.split()[3].point(lambda v: 255 if v > 128 else 0)
        glyph = fg_img.crop(solid_a.getbbox())
        w, h = glyph.size
        side = max(w, h)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(glyph, ((side - w) // 2, (side - h) // 2))
        inner = int(px * 0.62)
        fg_canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        fg_canvas.paste(square.resize((inner, inner), RESAMPLE),
                        ((px - inner) // 2, (px - inner) // 2))
        fg_canvas.save(d_out / "ic_launcher_foreground.png")
        bg.resize((px, px), RESAMPLE).save(d_out / "ic_launcher_background.png")
    # Play 商店 512
    (OUT_ANDROID / "store").mkdir(parents=True, exist_ok=True)
    master.resize((512, 512), RESAMPLE).save(OUT_ANDROID / "store" / "ic_launcher-playstore.png")


# ---------------------------------------------------------------- iOS
def _ios_full_bleed(master: Image.Image) -> Image.Image:
    """iOS AppIcon 必须不透明且满幅（系统再套圆角遮罩）：把徽标放大到满幅，四角用同向渐变补齐。"""
    side = int(master.width / 0.88)          # 母版徽标占 88% → 放大到 100%
    big = master.resize((side, side), RESAMPLE)
    canvas = Image.new("RGB", (master.width, master.height))
    d = ImageDraw.Draw(canvas)
    for y in range(master.height):
        t = y / (master.height - 1.0)
        d.line([(0, y), (master.width, y)],
               fill=tuple(int(BRAND_NAVY[i] + (BRAND_TEAL[i] - BRAND_NAVY[i]) * t) for i in range(3)))
    off = (side - master.width) // 2
    src = big.crop((off, off, off + master.width, off + master.height))
    canvas.paste(src, (0, 0), src)          # mask 必须与源同尺寸，故用 src 自身的 alpha
    return canvas


IOS_ICONS = [
    # (文件名, 像素) —— iPhone / iPad / App Store 全套
    ("icon-20.png", 20), ("icon-20@2x.png", 40), ("icon-20@3x.png", 60),
    ("icon-29.png", 29), ("icon-29@2x.png", 58), ("icon-29@3x.png", 87),
    ("icon-40.png", 40), ("icon-40@2x.png", 80), ("icon-40@3x.png", 120),
    ("icon-60@2x.png", 120), ("icon-60@3x.png", 180),
    ("icon-76.png", 76), ("icon-76@2x.png", 152),
    ("icon-83.5@2x.png", 167),
    ("icon-1024.png", 1024),
]

IOS_CONTENTS = """{
  "images": [
    {"idiom": "universal", "platform": "ios", "size": "20x20", "scale": "1x", "filename": "icon-20.png"},
    {"idiom": "universal", "platform": "ios", "size": "20x20", "scale": "2x", "filename": "icon-20@2x.png"},
    {"idiom": "universal", "platform": "ios", "size": "20x20", "scale": "3x", "filename": "icon-20@3x.png"},
    {"idiom": "universal", "platform": "ios", "size": "29x29", "scale": "1x", "filename": "icon-29.png"},
    {"idiom": "universal", "platform": "ios", "size": "29x29", "scale": "2x", "filename": "icon-29@2x.png"},
    {"idiom": "universal", "platform": "ios", "size": "29x29", "scale": "3x", "filename": "icon-29@3x.png"},
    {"idiom": "universal", "platform": "ios", "size": "40x40", "scale": "1x", "filename": "icon-40.png"},
    {"idiom": "universal", "platform": "ios", "size": "40x40", "scale": "2x", "filename": "icon-40@2x.png"},
    {"idiom": "universal", "platform": "ios", "size": "40x40", "scale": "3x", "filename": "icon-40@3x.png"},
    {"idiom": "universal", "platform": "ios", "size": "60x60", "scale": "2x", "filename": "icon-60@2x.png"},
    {"idiom": "universal", "platform": "ios", "size": "60x60", "scale": "3x", "filename": "icon-60@3x.png"},
    {"idiom": "universal", "platform": "ios", "size": "76x76", "scale": "1x", "filename": "icon-76.png"},
    {"idiom": "universal", "platform": "ios", "size": "76x76", "scale": "2x", "filename": "icon-76@2x.png"},
    {"idiom": "universal", "platform": "ios", "size": "83.5x83.5", "scale": "2x", "filename": "icon-83.5@2x.png"},
    {"idiom": "ios-marketing", "size": "1024x1024", "scale": "1x", "filename": "icon-1024.png"}
  ],
  "info": {"author": "xcode", "version": 1}
}
"""


def write_ios(master: Image.Image) -> None:
    d_out = ROOT / "ios" / "AppIcon.appiconset"
    d_out.mkdir(parents=True, exist_ok=True)
    full = _ios_full_bleed(master)
    for name, px in IOS_ICONS:
        full.resize((px, px), RESAMPLE).save(d_out / name)
    (d_out / "Contents.json").write_text(IOS_CONTENTS, encoding="utf-8")


# ---------------------------------------------------------------- Web / PWA
def write_favicon(master: Image.Image) -> None:
    import io
    d_out = ROOT / "favicon"
    d_out.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48]
    blobs = []
    for s in sizes:
        buf = io.BytesIO()
        master.resize((s, s), RESAMPLE).save(buf, format="PNG")
        blobs.append(buf.getvalue())
    n = len(sizes)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries, datas = b"", b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        datas += blob
    (d_out / "favicon.ico").write_bytes(header + entries + datas)
    master.resize((180, 180), RESAMPLE).save(d_out / "apple-touch-icon.png")
    master.resize((192, 192), RESAMPLE).save(d_out / "pwa-192.png")
    master.resize((512, 512), RESAMPLE).save(d_out / "pwa-512.png")


# ---------------------------------------------------------------- 组合标
def write_lockup(master: Image.Image) -> None:
    font_path = None
    for c in ("C:/Windows/Fonts/SegoeUI-Bold.ttf", "C:/Windows/Fonts/seguisb.ttf",
              "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(c).exists():
            font_path = c
            break
    if not font_path:
        return
    H = 256
    icon = master.resize((H, H), RESAMPLE)
    text = "EmoReply"
    font = ImageFont.truetype(font_path, 128)
    canvas_w, canvas_h = 1100, H
    for variant, fg, bg in (("emoreply-lockup.png", BRAND_NAVY, None),
                            ("emoreply-lockup-white.png", (255, 255, 255), None)):
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0) if bg is None else bg)
        img.paste(icon, (0, 0), icon)
        d = ImageDraw.Draw(img)
        d.text((H + 56, (H - 128) // 2 - 6), text, font=font, fill=fg)
        img.save(OUT_PREVIEW / variant)


# ---------------------------------------------------------------- 预览图
def write_preview(master: Image.Image) -> None:
    """尺寸接触表 + 场景预览（浅色/深色底）。"""
    W, H = 1500, 980
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    font = None
    for c in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        if Path(c).exists():
            font = ImageFont.truetype(c, 22) if c.endswith(".ttc") else ImageFont.truetype(c, 22)
            break

    # 顶部大图标
    canvas.paste(master.resize((260, 260), RESAMPLE), (60, 60), master.resize((260, 260), RESAMPLE))
    if font:
        d.text((360, 90), "EmoReply  ·  高情商聊天回复助手", font=font, fill=(20, 24, 33))
        d.text((360, 130), "主色 #08275E → #21AFAF", font=font, fill=(90, 98, 110))

    # 尺寸接触表（浅底 / 深底）
    sizes = [16, 24, 32, 48, 64, 128, 256]
    x0, y0 = 60, 360
    d.rectangle([x0 - 20, y0 - 50, W - 40, y0 + 200], fill=(246, 248, 250))
    x = x0
    for s in sizes:
        im = master.resize((s, s), RESAMPLE)
        canvas.paste(im, (x, y0 + (120 - s) // 2), im)
        if font:
            d.text((x, y0 + 130), f"{s}px", font=font, fill=(70, 78, 90))
        x += max(120, s + 60)
    # 深色底
    d.rectangle([x0 - 20, y0 + 230, W - 40, y0 + 480], fill=(24, 28, 36))
    x = x0
    for s in sizes:
        im = master.resize((s, s), RESAMPLE)
        canvas.paste(im, (x, y0 + 300), im)
        x += max(120, s + 60)
    if font:
        d.text((x0, y0 + 200), "浅色底 / 深色底 实际渲染（16–256px）", font=font, fill=(120, 128, 140))

    # 单色 / 反白 / Android 自适应
    mono = Image.open(OUT_MONO / "emoreply-mono-128.png")
    inv = Image.open(OUT_MONO / "emoreply-inverse-128.png")
    canvas.paste(mono, (60, 700), mono)
    canvas.paste(inv, (220, 700), inv)
    if font:
        d.text((60, 840), "单色版（#08275E 实心镂空）", font=font, fill=(20, 24, 33))
        d.text((220, 840), "反白版（白色图形）", font=font, fill=(20, 24, 33))
        d.text((60, 890), "最小建议使用尺寸：24px（16px 仅在 ICO/托盘等强制场景使用）", font=font, fill=(150, 20, 20))

    canvas.save(OUT_PREVIEW / "preview-sizes.png")

    # 场景预览：任务栏 / 桌面 / 托盘 简易示意
    scene = Image.new("RGB", (1200, 600), (238, 241, 245))
    sd = ImageDraw.Draw(scene)
    ico32 = master.resize((32, 32), RESAMPLE)
    ico64 = master.resize((64, 64), RESAMPLE)
    ico128 = master.resize((128, 128), RESAMPLE)
    # 桌面
    for i in range(3):
        scene.paste(ico64, (80 + i * 140, 120), ico64)
        sd.rectangle([70 + i * 140, 190, 150 + i * 140, 206], fill=(200, 205, 212))
    # 任务栏
    sd.rectangle([0, 520, 1200, 600], fill=(32, 36, 44))
    scene.paste(ico32, (60, 534), ico32)
    scene.paste(ico32, (110, 534), ico32)
    if font:
        sd.text((60, 80), "桌面快捷方式 / 安装包 / 系统托盘", font=font, fill=(60, 66, 76))
        sd.text((60, 300), "安装包封面 512px", font=font, fill=(60, 66, 76))
    scene.paste(ico128, (80, 340), ico128)
    scene.save(OUT_PREVIEW / "preview-scenes.png")


def main() -> None:
    master = make_master(SRC)
    master.save(ROOT / "_source" / "master-1024.png")
    write_pngs(master)
    write_ico(master)
    write_icns(master)
    write_mono(master)
    write_android(master)
    write_ios(master)
    write_favicon(master)
    write_lockup(master)
    write_preview(master)
    print("[OK] 品牌资产生成完成 ->", ROOT)


if __name__ == "__main__":
    main()
