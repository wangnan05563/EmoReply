# -*- coding: utf-8 -*-
"""
SVG 子集光栅化校验：解析 brand/logo/*.svg 的 path/ellipse 定义（含 translate/scale），
贝塞尔与圆弧展平后用「画家算法 + 奇偶填充」渲染成 40x40 ASCII，用于与位图母版比对构图。

仅用于校验几何（不支持 mask/use 语义，按文件内图层顺序人工解释）：
  - logo      : 徽标底 → 尾 → 气泡 → 爱心 → 星光
  - mono      : 徽标底，图形区域镂空
  - inverse   : 白色图形，爱心镂空
"""
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.w3.org/2000/svg}"
ROOT = Path(__file__).resolve().parent.parent
GRID = 40


# ------------------------------------------------------------ 路径解析
def _tokens(d: str):
    return re.findall(r"[MmCcLlAaZzHhVvSsQqTt]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)


def _arc_points(x1, y1, rx, ry, phi, large, sweep, x2, y2, steps=48):
    """SVG 椭圆弧 → 折线（标准 endpoint → center 参数化）。"""
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    phi = math.radians(phi)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p, y1p = cosp * dx2 + sinp * dy2, -sinp * dx2 + cosp * dy2
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(num / den, 0.0)) * (1 if large != sweep else -1)
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (x1 + x2) / 2.0
    cy = sinp * cxp + cosp * cyp + (y1 + y2) / 2.0

    def ang(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / n)))
        return -a if (ux * vy - uy * vx) < 0 else a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi

    pts = []
    for i in range(1, steps + 1):
        th = th1 + dth * i / steps
        cth, sth = math.cos(th), math.sin(th)
        px = cosp * rx * cth - sinp * ry * sth + cx
        py = sinp * rx * cth + cosp * ry * sth + cy
        pts.append((px, py))
    return pts


def flatten(d: str, steps=48):
    """返回子路径列表 [[(x,y),...], ...]"""
    tk = _tokens(d)
    subs, cur = [], []
    i, x, y = 0, 0.0, 0.0
    sx, sy = 0.0, 0.0
    cmd = None
    while i < len(tk):
        if re.match(r"[A-Za-z]", tk[i]):
            cmd = tk[i]
            i += 1
            continue
        if cmd in ("M", "m"):
            x, y = float(tk[i]), float(tk[i + 1])
            i += 2
            if cur:
                subs.append(cur)
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            x, y = float(tk[i]), float(tk[i + 1])
            i += 2
            cur.append((x, y))
        elif cmd in ("H", "h"):
            x = float(tk[i])
            i += 1
            cur.append((x, y))
        elif cmd in ("V", "v"):
            y = float(tk[i])
            i += 1
            cur.append((x, y))
        elif cmd in ("C", "c"):
            p1 = (float(tk[i]), float(tk[i + 1]))
            p2 = (float(tk[i + 2]), float(tk[i + 3]))
            p3 = (float(tk[i + 4]), float(tk[i + 5]))
            i += 6
            x0, y0 = x, y
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                bx = mt ** 3 * x0 + 3 * mt ** 2 * t * p1[0] + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0]
                by = mt ** 3 * y0 + 3 * mt ** 2 * t * p1[1] + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1]
                cur.append((bx, by))
            x, y = p3
        elif cmd in ("A", "a"):
            rx, ry = float(tk[i]), float(tk[i + 1])
            phi = float(tk[i + 2])
            large, sweep = int(float(tk[i + 3])), int(float(tk[i + 4]))
            x2, y2 = float(tk[i + 5]), float(tk[i + 6])
            i += 7
            cur.extend(_arc_points(x, y, rx, ry, phi, large, sweep, x2, y2, steps))
            x, y = x2, y2
        elif cmd in ("Z", "z"):
            cur.append((sx, sy))
            subs.append(cur)
            cur = []
        else:
            raise ValueError(f"unsupported command: {cmd}")
    if cur:
        subs.append(cur)
    return subs


def apply_transform(subs, transform: str):
    if not transform:
        return subs
    m = re.search(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\)", transform)
    tx = ty = 0.0
    if m:
        tx, ty = float(m.group(1)), float(m.group(2))
    m = re.search(r"scale\(\s*([-\d.]+)", transform)
    s = float(m.group(1)) if m else 1.0
    return [[(tx + p[0] * s, ty + p[1] * s) for p in sub] for sub in subs]


def ellipse_subs(el, transform=""):
    cx, cy = float(el.get("cx")), float(el.get("cy"))
    rx, ry = float(el.get("rx")), float(el.get("ry"))
    d = (f"M{cx - rx} {cy} A{rx} {ry} 0 1 1 {cx + rx} {cy} "
         f"A{rx} {ry} 0 1 1 {cx - rx} {cy} Z")
    return apply_transform(flatten(d), transform)


def contains(subs, px, py) -> bool:
    """奇偶规则判定点是否在路径内（用于填充与 mask 裁剪）。"""
    inside = False
    for sub in subs:
        n = len(sub)
        for i in range(n):
            x1, y1 = sub[i]
            x2, y2 = sub[(i + 1) % n]
            if (y1 > py) != (y2 > py):
                xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                if px < xint:
                    inside = not inside
    return inside


# ------------------------------------------------------------ 图层装载
def load_layers(svg_path: Path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    layers = []  # (subs, fill)
    for el in root.iter():
        tag = el.tag.replace(NS, "")
        tr = el.get("transform", "")
        if tag == "path" and el.get("d"):
            layers.append((apply_transform(flatten(el.get("d")), el.get("transform", "")), el.get("fill", "")))
        elif tag == "ellipse":
            layers.append((ellipse_subs(el, ""), el.get("fill", "")))
        elif tag == "g" and tr.startswith("scale"):
            pass
    # 处理 <g transform="scale(0.25)">（组合标）
    for g in root.iter(f"{NS}g"):
        s = re.search(r"scale\(\s*([-\d.]+)", g.get("transform", "") or "")
        if not s:
            continue
        k = float(s.group(1))
        for el in g.iter():
            tag = el.tag.replace(NS, "")
            if tag == "path" and el.get("d"):
                layers.append(([[(p[0] * k, p[1] * k) for p in sub]
                                for sub in apply_transform(flatten(el.get("d")), el.get("transform", ""))],
                               el.get("fill", "")))
            elif tag == "ellipse":
                layers.append(([[(p[0] * k, p[1] * k) for p in sub] for sub in ellipse_subs(el, "")],
                               el.get("fill", "")))
    return layers


def render(layers, size=1024, grid=GRID):
    """画家算法：后绘制的图层覆盖先绘制的；返回字符网格。"""
    cell = size / grid
    out = []
    for gy in range(grid):
        row = ""
        py = (gy + 0.5) * cell
        for gx in range(grid):
            px = (gx + 0.5) * cell
            ch = "."
            for subs, fill in reversed(layers):
                if fill.startswith("url("):
                    continue
                if contains(subs, px, py):
                    f = fill.lower()
                    ch = {"#ffffff": "#", "#8db9d5": "H", "#c5fef5": "*"}.get(f, "o")
                    break
            else:
                pass
            # 徽标底（渐变）需单独判断：第一个 url() 图层
            if ch == ".":
                for subs, fill in layers:
                    if fill.startswith("url(") and contains(subs, px, py):
                        ch = "o"
                        break
            row += ch
        out.append(row)
    return out


def main():
    for name in ("emoreply-logo.svg", "emoreply-logo-mono.svg", "emoreply-logo-inverse.svg"):
        p = ROOT / "logo" / name
        layers = load_layers(p)
        print("=" * 20, name, f"({len(layers)} layers)")
        for row in render(layers):
            print(row)


if __name__ == "__main__":
    main()
