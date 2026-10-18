# -*- coding: utf-8 -*-
"""T01162：基于现有 App 图标生成 1280×640 GitHub 社交预览图（Open Graph）。

源图：brand/_source/*_app_icon_design_flat_vector_st_1.png（1024×1024，白底居中图形）。
处理链路：
1) 去白底（flood fill 从四角扩散，容差可控）→ 透明 PNG；
2) 从去底后的实心像素取主色（不做全图平均，白底会稀释颜色）；
3) 1280×640 画布：主色深调对角渐变 + 柔光晕，左侧等比 logo，右侧两行文字；
4) 输出 PNG（严格 1280×640，logo 等比不拉伸）。

交付：本脚本即「设计源文件」——改 TITLE/SUBTITLE 或源图后重跑即可复现。
用法：python tools/gen_social_preview.py
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "brand", "_source")
OUT_DIR = os.path.join(ROOT, "brand")

W, H = 1280, 640
TITLE = "EmoReply"
SUBTITLE = "高情商聊天回复助手"

FONT_DIR = "C:/Windows/Fonts"
F_TITLE = os.path.join(FONT_DIR, "segoeuib.ttf")
F_CN = os.path.join(FONT_DIR, "msyhbd.ttc")
F_CN_REG = os.path.join(FONT_DIR, "msyh.ttc")

# 候选图标 → 输出文件名（brand 目录）；第 3 项为 True 者同时输出为默认 social-preview.png
# 2026-09-27 T01163：主标定稿为「气泡+爱心+星光（深蓝→青）」＝ cand-3，社交预览同步切到该稿，
# 保持仓库 OG 图与品牌主标一致；旧版（紫系）备份在 brand/_source/backup/social-preview-prev-cand-a-20260927.png。
# ⚠ 源图文件名已规范化（此前用生成脚本的 30 字符 slug，跨会话重跑会互相覆盖历史候选）。
CANDS = (
    ("cand-1-violet.png", "social-preview-cand-a.png", False),
    ("cand-2-magenta.png", "social-preview-cand-b.png", False),
    ("cand-3-navy-teal.png", "social-preview-cand-c.png", True),
)


def strip_white_bg(src, thresh=42, feather=0.8):
    """去白底：从四角 flood fill 白色连通区为透明，并轻微羽化边缘锯齿。"""
    im = Image.open(src).convert("RGBA")
    # floodfill 容差放大便于吃掉抗锯齿白边；用 ImageDraw.floodfill 从四角注入
    transparent = (0, 0, 0, 0)
    for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)):
        try:
            ImageDraw.floodfill(im, xy, transparent, thresh=thresh)
        except Exception:
            pass
    # ⚠ 不再做「全局近白像素一律置透明」——那会把图形内部的白色元素（对话气泡等）
    # 挖成空洞（实测 candB 内部空洞达 13.6%）。背景白只由上面的 flood fill 处理，
    # flood fill 天然只吃掉与画布外缘**连通**的区域，图形内部的白得以保留。
    if feather:
        alpha = im.split()[-1].filter(ImageFilter.GaussianBlur(feather))
        im.putalpha(alpha)
    return im


def dominant_color(logo):
    """取去底图标的主色：忽略透明像素，按频数取最常见的量化色。"""
    rgba = logo.copy()
    small = rgba.resize((96, 96), Image.LANCZOS)
    buckets = {}
    for r, g, b, a in small.getdata():
        if a < 200 or (r > 240 and g > 240 and b > 240):
            continue
        key = (r // 24, g // 24, b // 24)
        buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return (98, 84, 168)
    key = max(buckets.items(), key=lambda kv: kv[1] if kv[0][0] + kv[0][1] + kv[0][2] < 30 else kv[1] - 400)[0]
    r, g, b = [min(255, c * 24 + 12) for c in key]
    # 提饱和：拉开通道差异，避免灰扑扑的渐变背景
    mx, mn = max(r, g, b), min(r, g, b)
    if mx and mx - mn < 40:
        r, g, b = int(r * 1.05), int(g * 0.95), int(b * 1.12)
    return (min(255, r), min(255, g), min(255, b))


def scale(base, f):
    """主色按比例调亮/调暗。"""
    return tuple(max(0, min(255, int(c * f))) for c in base)


def gradient_bg(size, base):
    """对角渐变背景 + 右上柔光晕（T01827 重构：小图计算 + BICUBIC 放大）。

    旧版在全分辨率上逐像素计算 int 截断色值，放大看有水平条带；
    现改为 96×96 小图上完成渐变插值后 BICUBIC 放大，过渡天然连续。
    """
    w, h = size
    top = scale(base, 0.45)
    bottom = scale(base, 0.12)
    SW = SH = 96
    small = Image.new("RGB", (SW, SH))
    sp = small.load()
    for y in range(SH):
        ty = (y / (SH - 1)) ** 1.15
        row = [bottom[i] + (top[i] - bottom[i]) * ty for i in range(3)]
        for x in range(SW):
            k = 1.0 - ((x / (SW - 1)) * 0.55 + ty * 0.45) * 0.30
            sp[x, y] = tuple(max(0, min(255, int(c * k))) for c in row)
    bg = small.resize((w, h), Image.BICUBIC)

    # 右上柔光晕：L 蒙版 + 纯色 tint 合成（蒙版经高斯模糊，无硬边）
    glow_l = Image.new("L", (w // 4, h // 4), 0)
    gd = ImageDraw.Draw(glow_l)
    gx, gy, r = int(w * 0.86) // 4, int(h * 0.18) // 4, int(h * 0.95) // 4
    gd.ellipse([gx - r, gy - r, gx + r, gy + r], fill=105)
    glow_l = glow_l.filter(ImageFilter.GaussianBlur(40)).resize((w, h), Image.BICUBIC)
    tint = Image.new("RGB", (w, h), scale(base, 0.75))
    return Image.composite(tint, bg, glow_l)


def add_text(canvas, base):
    d = ImageDraw.Draw(canvas, "RGBA")
    accent = scale(base, 1.35)
    ft_title = ImageFont.truetype(F_TITLE, 82)
    ft_sub = ImageFont.truetype(F_CN, 40)
    tx = 560
    # 标题
    d.text((tx, 236), TITLE, font=ft_title, fill=(255, 255, 255, 255))
    # 标题下方强调短线（品牌色）
    d.rounded_rectangle([tx, 340, tx + 96, 348], radius=4, fill=accent + (255,))
    # 副标题（全图文字仅两行：标题 + 副标题，符合 GitHub 社交预览的简洁要求）
    d.text((tx, 372), SUBTITLE, font=ft_sub, fill=(226, 226, 232, 255))


def build(src_name, out_name):
    src = os.path.join(SRC_DIR, src_name)
    logo = strip_white_bg(src)
    base = dominant_color(logo)
    canvas = gradient_bg((W, H), base).convert("RGBA")

    # logo：等比缩放到高 360，左侧留出安全边距；不裁不变形
    target_h = 360
    ratio = logo.height / logo.width
    lw = int(target_h / ratio)
    logo_r = logo.resize((lw, target_h), Image.LANCZOS)
    lx, ly = 118, (H - target_h) // 2
    # 轻微投影，使 logo 从背景中浮起
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [lx - 30, ly - 10, lx + lw + 30, ly + target_h + 30], fill=(0, 0, 0, 90))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(45)))
    canvas.alpha_composite(logo_r, (lx, ly))

    add_text(canvas, base)
    out = os.path.join(OUT_DIR, out_name)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out, base


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for src_name, out_name, is_default in CANDS:
        if not os.path.exists(os.path.join(SRC_DIR, src_name)):
            print("[skip] missing source:", src_name)
            continue
        out, base = build(src_name, out_name)
        im = Image.open(out)
        print(f"[ok] {os.path.basename(out)} {im.size} base={base}")
        if is_default:
            main_out = os.path.join(OUT_DIR, "social-preview.png")
            im.convert("RGB").save(main_out, "PNG", optimize=True)
            print(f"[ok] social-preview.png (default) {Image.open(main_out).size}")


if __name__ == "__main__":
    main()
