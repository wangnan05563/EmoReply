"""T05727：合成聊天截图语料生成器（离线基线，零真机依赖）。

产出：`--out` 目录下
- `images/<name>.png`  渲染后的聊天截图（1080×2340，App×主题×聊天形态×字号 全矩阵）
- `corpus.jsonl`       ground truth（emoreply.baseline.CorpusRecord schema，
                       行级 text/kind/side/msg + 行框几何）

设计约束：
- 复刻 T02305 测试夹具的配色口径（微信 #95EC69/#F2F2F2/#EDEDED、深色 #1AAB39/
  #2C2C2C/#111111）；钉钉/QQ/短信用近似色——它们在真机链路上本就走纯位置判侧
  （bubble_side 仅对 com.tencent.mm 启用颜色规则），基线因此能测出位置判据对
  长我方气泡的真实缺陷（T01150 同源）。
- 图片消息渲染三种真机实测形态：彩色照片 / 银行表头（品牌绿）/ 灰阶拍屏——
  分别对应 T02305 环带判据的三条补抓路径。
- 折行只切 CJK 边界：GT 消息文本 = 物理行直接拼接，与链路 _join_wrapped 一致。
- 渲染引擎（PIL）≠ 真机渲染，字形/抗锯齿有差异：合成语料的 CER 不代表真机
  OCR 水平，只用于结构链路回归与门槛锁定；真机识别质量用 collect_baseline_device.py
  采集的真实截图补齐。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emoreply.baseline import (  # noqa: E402
    KIND_IMAGE_TEXT, KIND_MESSAGE, KIND_NOISE, KIND_TIME,
    SIDE_MINE, SIDE_NONE, SIDE_OTHER, CorpusRecord, GtLine,
)

W, H = 1080, 2340
BASE_FONT = 38
MARGIN_X = 36
BUBBLE_MAX_W = int(W * 0.7)
PAD_X, PAD_Y = 26, 20

# App × 主题 → (页底, 我方气泡, 对方气泡, 正文色, 次要文字色)
PALETTE = {
    "wechat": {
        "light": ((237, 237, 237), (149, 236, 105), (255, 255, 255), (10, 10, 10), (178, 178, 178)),
        "dark": ((17, 17, 17), (26, 171, 57), (44, 44, 44), (230, 230, 230), (122, 122, 122)),
    },
    "dingtalk": {
        "light": ((245, 246, 247), (214, 232, 255), (255, 255, 255), (20, 30, 40), (150, 158, 166)),
        "dark": ((30, 30, 30), (43, 76, 126), (51, 51, 51), (225, 230, 235), (120, 126, 132)),
    },
    "qq": {
        "light": ((242, 242, 242), (201, 221, 255), (255, 255, 255), (15, 20, 25), (160, 165, 170)),
        "dark": ((25, 25, 25), (44, 78, 128), (48, 48, 48), (222, 228, 233), (118, 124, 130)),
    },
    "sms": {
        "light": ((248, 248, 248), (215, 231, 245), (229, 229, 229), (10, 10, 10), (170, 170, 170)),
        "dark": ((18, 18, 18), (39, 75, 109), (51, 51, 51), (228, 228, 228), (125, 125, 125)),
    },
}

PHRASES = [
    "在吗？", "看到了吗？", "好的，没问题。", "明天上午十点开会，记得带资料。",
    "这份方案你先看看，有问题随时说。", "客户那边又改需求了，真是服了。",
    "周末一起爬山好吗？", "饭点了，去食堂还是点外卖？", "嗯，就这样吧。",
    "刚才说的那个文件我发你了，查收一下。", "会议室订好了，三楼 302。",
    "合同今天能盖章吗？财务那边催了。", "收到，我马上处理。",
    "你别急，我这就把报表统计出来发群里。", "晚点一起拼车回去？",
]
NICKNAMES = ["夏思铭妈妈", "王工", "李经理", "张敏", "产品-阿凯", "陈总"]
IMG_TEXTS = [
    ["客户账户名称:管理测试1", "客户账号:00724071200000002", "开户机构:交通路支行"],
    ["限时特惠 全场五折", "满 300 减 50", "活动截止 12 月 31 日"],
    ["余额查询", "当前余额 1,234.56 元", "最近交易:10 笔"],
]
NOISE_ROWS = ["[图片]", "[文件]", "■■■■■", "1+", "(田+0)", "对方已取消"]
TIME_ROWS = ["昨天 21:0{}", "今天 09:3{}", "星期三 14:1{}", "12:45"]


def _wrap_cjk(text: str, max_chars: int):
    """把长消息按 CJK 边界切成两条物理行（GT 拼接 = 直接相连）。"""
    if len(text) <= max_chars:
        return [text]
    cut = max(1, len(text) // 2)
    return [text[:cut], text[cut:]]


def _image_payload(rng, app):
    """图片消息三种真机形态之一：返回 (类型, 内容文本组)。"""
    kind = rng.choice(["photo", "bank", "gray"])
    return kind, rng.choice(IMG_TEXTS)


def build_scene(app: str, theme: str, chat: str, font_scale: float, seed: str):
    """生成一个场景的逻辑行序列（渲染 + GT 共用同一份几何）。

    GT 行对象直接挂在 row 上（`gt`/`nick_gt`），渲染时原地回填几何——不能按
    文本匹配回填：同场景重复短语会把多条 GT 行填成同一坐标，折行合并随即并轨
    （首轮回放实测：召回率虚降到 61%）。
    """
    rng = random.Random(seed)
    fs = int(BASE_FONT * font_scale)
    line_h = int(fs * 1.45)
    small_fs = int(fs * 0.75)
    rows = []          # 渲染用：dict(type, side, lines=[(text, font_px, centered)])
    msg_id = 0
    n_msg = rng.randint(6, 11)
    side = SIDE_OTHER if rng.random() < 0.6 else SIDE_MINE
    time_i = rng.randrange(0, len(TIME_ROWS))
    img_inserted = False

    def add_time_row():
        nonlocal rows, time_i
        text = TIME_ROWS[time_i % len(TIME_ROWS)].format(rng.randint(0, 5))
        time_i += 1
        rows.append({"type": "time", "side": SIDE_NONE,
                     "lines": [(text, small_fs, True)],
                     "gt": [GtLine(text, KIND_TIME, SIDE_NONE, -1)]})

    add_time_row()
    for k in range(n_msg):
        # 时间分隔行：每隔几条消息插一条（话题边界信号）
        if k and k % 4 == 0:
            add_time_row()
        # 图片消息：整场一条，多数在对方侧（真实分布：对方发截图/照片更常见）
        if not img_inserted and k == n_msg // 2:
            img_inserted = True
            kind, texts = _image_payload(rng, app)
            rows.append({"type": "image", "side": SIDE_OTHER,
                         "lines": [(t, small_fs, False) for t in texts],
                         "image_kind": kind,
                         "gt": [GtLine(t, KIND_IMAGE_TEXT, SIDE_NONE, -1) for t in texts]})
            continue
        # 噪声行：偶发混入（真机 OCR 会读出来，链路应滤掉）
        if rng.random() < 0.12:
            t = rng.choice(NOISE_ROWS)
            rows.append({"type": "noise", "side": SIDE_NONE,
                         "lines": [(t, fs, False)],
                         "gt": [GtLine(t, KIND_NOISE, SIDE_NONE, -1)]})
            continue

        text = rng.choice(PHRASES)
        phys = _wrap_cjk(text, max_chars=16 if font_scale < 1.15 else 13)
        # 侧别切换：随机连发 1~2 条后换人
        if k and rng.random() < 0.55:
            side = SIDE_OTHER if side == SIDE_MINE else SIDE_MINE
        msg_id += 1
        entry = {"type": "msg", "side": side, "lines": [(p, fs, False) for p in phys],
                 "gt": [GtLine(p, KIND_MESSAGE, side, msg_id) for p in phys]}
        # 群聊：对方消息上方加昵称小字行（OCR 会读成独立行，链路应滤/并）
        if chat == "group" and side == SIDE_OTHER and rng.random() < 0.8:
            nick = rng.choice(NICKNAMES)
            if rng.random() < 0.4 and text.startswith(nick[:2]):
                pass  # 昵称恰是正文前缀：留给 strip_group_noise 的「并」路径
            else:
                entry["nick"] = nick
                entry["nick_gt"] = GtLine(nick, KIND_NOISE, SIDE_NONE, -1)
        rows.append(entry)
    # 收尾保证有对方消息（current 存在，对话可解析才有意义）
    if not any(row.get("gt") and any(g.kind == KIND_MESSAGE and g.side == SIDE_OTHER
                                     for g in row["gt"]) for row in rows):
        msg_id += 1
        text = "晚上一起吃饭吗？"
        rows.append({"type": "msg", "side": SIDE_OTHER, "lines": [(text, fs, False)],
                     "gt": [GtLine(text, KIND_MESSAGE, SIDE_OTHER, msg_id)]})
    add_time_row()
    # GT 按视觉顺序平铺（昵称行在消息行之前，与渲染顺序一致）
    gt: list[GtLine] = []
    for row in rows:
        if row.get("nick_gt"):
            gt.append(row["nick_gt"])
        gt.extend(row["gt"])
    return rows, gt, fs, small_fs, line_h


def render_scene(app, theme, rows, fs, small_fs, line_h, font_path):
    """按逻辑行渲染 PNG，并把行框几何直接写进 row 上的 GT 行对象。"""
    page, mine_c, other_c, ink, dim = PALETTE[app][theme]
    im = Image.new("RGB", (W, H), page)
    draw = ImageDraw.Draw(im)
    font = ImageFont.truetype(font_path, fs)
    font_small = ImageFont.truetype(font_path, small_fs)
    y = 40
    # 气泡纵向间距必须大于行高×合并比例（同 block 1.0），否则相邻两条消息会被
    # 折行合并错误并轨（首轮回放实测：gap_y=28 < line_h=55.8，全部时间行/昵称行
    # 与下一条消息并成一行，召回率虚降到 61%）。真机间距 ≈ 10dp×密度 ≈ 1.5×字号。
    gap_y = int(1.5 * fs)
    rng = random.Random(f"img-{app}-{theme}-{y}")
    rng2 = random.Random(f"photo-{app}-{theme}-{y}")

    def text_w(t, f):
        return draw.textlength(t, font=f)

    def fill(g: GtLine, x0, x1, top, bot):
        g.left, g.right, g.top, g.bottom = int(x0), int(x1), int(top), int(bot)

    for row in rows:
        kind = row["type"]
        side = row["side"]
        g = row["gt"][0]
        if kind == "time":
            t, fpx, _ = row["lines"][0]
            f = font_small
            tw = text_w(t, f)
            x0 = (W - int(tw)) // 2
            bot = y + small_fs + 8
            draw.text((x0, y), t, font=f, fill=dim)
            fill(g, x0, x0 + int(tw), y, bot)
            y = bot + gap_y
            continue
        if kind == "noise":
            t, fpx, _ = row["lines"][0]
            f = font
            tw = int(text_w(t, f))
            side_is_other = rng.random() < 0.6
            x0 = MARGIN_X if side_is_other else W - MARGIN_X - tw
            bot = y + fs + 10
            draw.text((x0, y), t, font=f, fill=dim)
            fill(g, x0, x0 + tw, y, bot)
            y = bot + gap_y
            continue
        if kind == "image":
            iw, ih = 520, 380
            x0 = MARGIN_X + 20
            top = y
            bot = y + ih
            k = row["image_kind"]
            if k == "bank":
                draw.rectangle([x0, top, x0 + iw, bot], fill=(255, 255, 255))
                draw.rectangle([x0, top, x0 + iw, top + 120], fill=(0, 160, 93))
            elif k == "gray":
                for yy in range(top, bot, 4):
                    v = 140 + (x0 * 7 + yy * 13) % 90
                    draw.line([(x0, yy), (x0 + iw, yy)], fill=(v, v + 1, v - 3))
            else:  # photo：高噪彩色块（真机照片是花背景；平坦大色块会让环带取样
                # 命中白色字形被误判成气泡证据，属语料失真——首轮实测 46 行泄漏）
                for _ in range(240):
                    rx = rng2.randint(x0, x0 + iw - 24)
                    ry = rng2.randint(top, bot - 24)
                    c = (rng2.randint(40, 240), rng2.randint(20, 200), rng2.randint(30, 220))
                    draw.rectangle([rx, ry, rx + rng2.randint(12, 40),
                                    ry + rng2.randint(10, 30)], fill=c)
            # 图内文字：白色/深色小字，行距紧凑（行高 34、间距 6 → 密集簇特征）
            f = ImageFont.truetype(font_path, 34)
            ty = top + (140 if k == "bank" else 40)
            for gt_line, (t, _, _) in zip(row["gt"], row["lines"]):
                draw.text((x0 + 24, ty), t, font=f,
                          fill=(255, 255, 255) if k != "gray" else (30, 30, 30))
                tw = int(text_w(t, f))
                fill(gt_line, x0 + 24, x0 + 24 + tw, ty, ty + 40)
                ty += 46
            y = bot + gap_y
            continue
        # 普通消息（含昵称行与折行）
        if row.get("nick"):
            nf = font_small
            tw = int(text_w(row["nick"], nf))
            x0 = MARGIN_X + 20
            draw.text((x0, y), row["nick"], font=nf, fill=dim)
            fill(row["nick_gt"], x0, x0 + tw, y, y + small_fs + 6)
            # 昵称行与正文行的间距须大于合并比例阈值，否则昵称会被并进气泡正文
            # （真机昵称≈气泡上沿 8~11dp，加气泡内边距后 ≈ 0.6×行高 + 内边距）
            y += small_fs + 14 + int(0.6 * line_h)
        bubble_c = mine_c if side == SIDE_MINE else other_c
        f = font
        # 气泡宽 = 最长物理行 + 内边距；折行行左对齐同一起点
        max_tw = max(int(text_w(t, f)) for t, _, _ in row["lines"])
        bw = min(max_tw + PAD_X * 2, BUBBLE_MAX_W)
        bh = len(row["lines"]) * line_h + PAD_Y * 2
        if side == SIDE_MINE:
            bx0 = W - MARGIN_X - bw
        else:
            bx0 = MARGIN_X + 20
        by0 = y
        draw.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh], radius=16, fill=bubble_c)
        ty = by0 + PAD_Y
        for gt_line, (t, _, _) in zip(row["gt"], row["lines"]):
            tx0 = bx0 + PAD_X
            draw.text((tx0, ty), t, font=f, fill=ink)
            tw = int(text_w(t, f))
            fill(gt_line, tx0, tx0 + tw, ty, ty + fs + 8)
            ty += line_h
        y = by0 + bh + gap_y
    return im.crop((0, 0, W, min(H, y + 60)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "baseline"))
    ap.add_argument("--font", default=r"C:\Windows\Fonts\msyh.ttc")
    ap.add_argument("--seed", default="20261113")
    ap.add_argument("--per-scene", type=int, default=3,
                    help="每个组合（App×主题×形态×字号）生成的场景数")
    args = ap.parse_args()
    if not Path(args.font).exists():
        sys.exit(f"缺 CJK 字体：{args.font}（用 --font 指定，如 msyh.ttc / simhei.ttf）")

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    corpus_path = out / "corpus.jsonl"
    n = 0
    with open(corpus_path, "w", encoding="utf-8") as f:
        for app in PALETTE:
            for theme in ("light", "dark"):
                for chat in ("single", "group"):
                    for fscale in (1.0, 1.3):
                        for k in range(args.per_scene):
                            rows, gt, fs, sfs, lh = build_scene(
                                app, theme, chat, fscale, f"{args.seed}-{app}-{theme}-{chat}-{fscale}-{k}")
                            im = render_scene(app, theme, rows, fs, sfs, lh, args.font)
                            name = f"{app}_{theme}_{chat}_f{int(fscale*100)}_{k}.png"
                            im.save(out / "images" / name)
                            # 几何已由 render_scene 直接写进 GT 行对象，无需回填
                            rec = CorpusRecord(image=f"images/{name}", app=app,
                                               theme=theme, chat=chat,
                                               font_scale=fscale, lines=gt)
                            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
                            n += 1
    print(f"生成 {n} 张合成截图 → {out}")
    print(f"语料：{corpus_path}（{sum(1 for _ in open(corpus_path))} 行）")


if __name__ == "__main__":
    main()
