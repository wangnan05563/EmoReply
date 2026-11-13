"""T05727：读屏 OCR 基线评测执行器——两种模式：

1. `--pipeline`（离线回归）：把语料 GT 的**物理行框**当作完美 OCR 输入，回放
   m1-core 读屏后处理链路（图内文字簇剔除 → 折行合并 → 非文本噪声 → 群噪声 →
   判侧 → 前缀标注，与 Android OCR 路径同序），测的是**结构链路**本身。
   注意：不跑真实 OCR 引擎，行框无几何/识别误差，频次与头像字形过滤未复刻
   （合成语料不含该两类场景）——CER 恒为 0 是预期，不是缺陷。
2. `--predictions <file>`（真机/外部预测）：对 collect_baseline_device.py 采集或
   人工整理的预测 JSONL 评分——这才是 CER/召回的真实数字。

产出：`<out>/report.json` + 控制台报告。报告即 T05725 路线图的阶段门槛基线。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emoreply.baseline import (  # noqa: E402
    KIND_TIME, evaluate, format_report, load_corpus, load_predictions,
    save_predictions,
)
from emoreply.bubble_side import LineBox, judge_side_with_fallback  # noqa: E402
from emoreply.preprocess import (  # noqa: E402
    OcrLine, TIME_MARK_PREFIX, filter_image_text_clusters,
    filter_non_text_noise, merge_wrapped_lines, strip_group_noise,
)

# kt isTimeMark 同构近似：居中 + 时刻特征（真实判定在捕获层，此处只服务回放）
_TIME_TEXT = re.compile(r"^(昨天|今天|星期[一二三四五六日天])?\s*\d{1,2}:\d{2}")
_CENTER_BAND = (0.30, 0.70)   # 行中心落在屏宽此区间才可能是时间行


def _is_time_row(ln: OcrLine, width: int) -> bool:
    cx = (ln.left + ln.right) / 2
    lo, hi = width * _CENTER_BAND[0], width * _CENTER_BAND[1]
    return lo <= cx <= hi and bool(_TIME_TEXT.search(ln.text.strip()))


def pipeline_replay(rec, images_dir: Path) -> list:
    """把单张语料图回放读屏后处理链路，返回带前缀的最终标注行。

    链路顺序 = EmoReplyReaderService.captureViaScreenshot（T01334 复测定序）：
    剔簇 → 合并 → 剔非文本噪声 → 群噪声 → 判侧 → 时间/侧别前缀。
    """
    im = Image.open(images_dir / rec.image).convert("RGB")
    W, H = im.size
    px = im.load()

    def get_rgb(x, y):
        return px[max(0, min(W - 1, x)), max(0, min(H - 1, y))]

    # 完美 OCR：GT 物理行框原样进链路（含噪声/图内文字行——引擎会读出来）
    raw = [OcrLine(l.text, l.left, l.right, l.top, l.bottom, l.block) for l in rec.lines]
    after = filter_image_text_clusters(raw, get_rgb=get_rgb, width=W, height=H)
    merged = merge_wrapped_lines(after)
    kept = filter_non_text_noise(merged)
    texts = strip_group_noise([l.text for l in kept])
    # strip_group_noise 输出字符串：按多重集映射回几何（同文多行按出现次数弹出）
    remaining = []
    pool = list(kept)
    for t in texts:
        for i, ln in enumerate(pool):
            if ln.text == t:
                remaining.append(pool.pop(i))
                break

    color_available = rec.app == "wechat"   # bubble_side 颜色规则仅微信启用
    out = []
    for ln in remaining:
        if _is_time_row(ln, W):
            out.append(TIME_MARK_PREFIX + ln.text.strip())
            continue
        call = judge_side_with_fallback(
            get_rgb, LineBox(ln.left, ln.right, ln.top, ln.bottom), W, H,
            color_available=color_available)
        out.append(("我：" if call.side == "我" else "对方：") + ln.text.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "baseline" / "corpus.jsonl"))
    ap.add_argument("--images", default=None, help="图片根目录（默认 = 语料所在目录）")
    ap.add_argument("--predictions", default=None, help="外部预测 JSONL（真机采集）；不传则走 --pipeline 回放")
    ap.add_argument("--out", default=None, help="report.json 输出路径（默认 = 语料目录）")
    args = ap.parse_args()

    corpus = load_corpus(args.corpus)
    images_dir = Path(args.images) if args.images else Path(args.corpus).parent
    out_path = Path(args.out) if args.out else Path(args.corpus).parent / "report.json"

    if args.predictions:
        predictions = load_predictions(args.predictions)
        mode = "external"
    else:
        predictions = [{"image": rec.image, "lines": pipeline_replay(rec, images_dir)}
                       for rec in corpus]
        mode = "pipeline-replay"
        save_predictions(out_path.parent / "predictions.jsonl", predictions)

    metrics = evaluate(corpus, predictions)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, **metrics}, f, ensure_ascii=False, indent=2)
    print(format_report(metrics))
    print(f"\nmode={mode}  report → {out_path}")


if __name__ == "__main__":
    main()
