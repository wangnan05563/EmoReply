"""T05727：读屏 OCR 基准评测——语料 schema 与指标计算（事实源，与 scripts/ 评测工具配套）。

背景（T05725 评估报告 §2.3）：项目此前没有任何 OCR 基准集与量化指标，bad case
只能靠用户反馈发现，每次改动的「提升」无法证伪。本模块定义三样东西：

1. **语料 schema**：ground truth JSONL——每张截图一行记录（应用/主题/聊天形态/
   字号 + 物理行级标注）。行带 `msg` 分组号：同一条消息折行产生的多条物理行共享
   同一 msg 号，指标在**消息级**（折行合并后）比对，与 Android 读屏链路的输出
   粒度一致。
2. **预测 schema**：pipeline/真机输出的 JSONL——每张图一行 `{"image":...,
   "lines":["我：xxx", ...]}`（即读屏链路的最终标注行，时间行为「时间：xxx」）。
3. **指标**：消息级召回 / 侧别准确率 / 行级 CER / 噪声误入率 / 对话可解析率。

语料两类来源：
- 合成语料：`scripts/gen_baseline_corpus.py` 生成（可离线回归，零真机依赖）；
- 真机语料：`scripts/collect_baseline_device.py` 从 logcat 采集预测 +
  `adb screencap` 存图，人工标注 GT 后并入。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from .preprocess import TIME_MARK_PREFIX, parse_speaker_line, split_dialogue_turns

# ---- 行类别（GT 标注口径）----
KIND_MESSAGE = "message"        # 真实聊天气泡文本（含折行的多条物理行）
KIND_IMAGE_TEXT = "image_text"  # 图片消息内的文字（应被簇过滤剔除）
KIND_NOISE = "noise"            # UI 噪声：占位符/角标/乱码/昵称行/系统提示/悬浮条
KIND_TIME = "time"              # 时间/日期分隔行（应转「时间：」标记，非消息）

VALID_KINDS = (KIND_MESSAGE, KIND_IMAGE_TEXT, KIND_NOISE, KIND_TIME)

# ---- 侧别（GT 标注口径；与 bubble_side 的「我/对方」解耦，用英文枚举防标注手滑）----
SIDE_MINE = "mine"
SIDE_OTHER = "other"
SIDE_NONE = "none"  # 时间行/噪声行无侧别


@dataclass
class GtLine:
    """单条物理 OCR 行的 GT 标注（text=期望识别文本；几何=行框 px）。"""

    text: str
    kind: str
    side: str = SIDE_NONE
    msg: int = -1            # 消息分组号；非 message 行无意义（-1）
    left: int = 0
    right: int = 0
    top: int = 0
    bottom: int = 0
    block: int = 0


@dataclass
class CorpusRecord:
    """一张截图的 GT 记录。image 为相对语料根的路径。"""

    image: str
    app: str                 # wechat / dingtalk / qq / sms
    theme: str               # light / dark
    chat: str                # single / group
    font_scale: float        # 渲染字号倍率（覆盖字号漂移维度）
    lines: List[GtLine] = field(default_factory=list)


def load_corpus(path) -> List[CorpusRecord]:
    """读 GT 语料 JSONL；逐行校验 kind 合法性（错标注早暴露，不带病跑分）。"""
    records = []
    with open(path, encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            d = json.loads(raw)
            lines = []
            for ld in d["lines"]:
                if ld["kind"] not in VALID_KINDS:
                    raise ValueError(f"{d.get('image')} 行{ln}: 非法 kind={ld['kind']}")
                lines.append(GtLine(**ld))
            records.append(CorpusRecord(
                image=d["image"], app=d["app"], theme=d["theme"],
                chat=d["chat"], font_scale=d.get("font_scale", 1.0),
                lines=lines))
    return records


def save_predictions(path, predictions: List[Dict]) -> None:
    """写预测 JSONL：[{"image":..., "lines":["我：...", "时间：...", ...]}]。"""
    with open(path, "w", encoding="utf-8") as f:
        for p in predictions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def load_predictions(path) -> List[Dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
    return out


# ===== 指标计算 =====


def cer(ref: str, hyp: str) -> float:
    """字符错误率：Levenshtein 距离 / 参考长度（两侧先去全部空白）。

    去空白的理由：tesseract 中文 PSM=6 按字输出并以空格分隔（CHANGELOG 2026-10-16
    实测「周 未 一 起 爬 山 好 吗」），空格位置漂移不是识别错误，不该扣分。
    参考为空时按完全一致处理——空参考属语料 bug，返回 0 让它只在召回维度暴露
    （漏标行本就不算消息），不放大分母。
    """
    r = "".join((ref or "").split())
    h = "".join((hyp or "").split())
    if not r:
        return 0.0
    # 滚动数组 DP：消息级文本 ≤ 数十字，无需第三方库
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[len(h)] / len(r)


def _gt_messages(record: CorpusRecord) -> List[GtLine]:
    """把物理行按 msg 分组合并成消息级 GT（保序；文本直接拼接——合成语料
    折行只切在 CJK 边界，与链路 _join_wrapped 的拼接结果一致）。"""
    grouped: Dict[int, GtLine] = {}
    order: List[int] = []
    for ln in record.lines:
        if ln.kind != KIND_MESSAGE:
            continue
        g = grouped.get(ln.msg)
        if g is None:
            grouped[ln.msg] = GtLine(ln.text, ln.kind, ln.side, ln.msg)
            order.append(ln.msg)
        else:
            g.text += ln.text
    return [grouped[k] for k in order]


def _norm(s: str) -> str:
    """比对归一化：去空白——OCR 输出的空格位置漂移（如 tesseract 按字空格）
    不该扣 CER，语义等价。"""
    return "".join((s or "").split())


def evaluate(corpus: List[CorpusRecord],
             predictions: List[Dict],
             *,
             match_ratio: float = 0.75) -> Dict:
    """全量评测。预测按 image 字段与 GT 配对；缺图/多图计入缺口明细。

    指标口径：
    - message_recall      消息级召回 = 匹配上的 GT 消息 / GT 消息总数
    - side_accuracy       匹配消息中侧别标注正确的比例
    - line_cer            匹配消息的平均 CER（识别文本质量）
    - noise_intrusion     噪声误入率 = 未匹配任何 GT 消息的预测行 / 预测总行
    - dialogue_parse_rate 对话可解析率 = split_dialogue_turns 能给出 current 的图
                          / GT 含对方消息的图（端到端「能不能进入生成」的代理指标）
    """
    pred_by_image = {p.get("image"): p.get("lines") or [] for p in predictions}
    unmatched_images = sorted(r.image for r in corpus if r.image not in pred_by_image)

    total_gt = matched = side_ok = 0
    cer_sum = 0.0
    total_pred = pred_unmatched = time_lines = 0
    parse_applicable = parse_ok = 0

    for rec in corpus:
        gts = _gt_messages(rec)
        preds = pred_by_image.get(rec.image)
        if preds is None:
            total_gt += len(gts)          # 缺预测的图：GT 全记漏
            if any(g.side == SIDE_OTHER for g in gts):
                parse_applicable += 1
            continue
        used = [False] * len(preds)
        total_pred += len(preds)

        # 预测行一次性解析成 (侧别, 正文)：正文用于配对，侧别用于 side_accuracy。
        # 时间标记行是结构标记不是消息：不参与配对、不计噪声误入（单列计数）。
        parsed = []
        for p in preds:
            sender, body = parse_speaker_line(p)
            side = SIDE_MINE if sender == "我" else SIDE_OTHER
            is_time = p.strip().startswith(TIME_MARK_PREFIX)
            if is_time:
                time_lines += 1
            parsed.append((side, body, is_time))

        # 贪心配对：GT 按序取最优预测（SequenceMatcher 对归一化正文）
        pairs = []  # (gt, pred_index)
        for g in gts:
            best_i, best_r = -1, 0.0
            for i, (side, body, is_time) in enumerate(parsed):
                if used[i] or is_time:
                    continue
                r = SequenceMatcher(None, _norm(g.text), _norm(body)).ratio()
                if r > best_r:
                    best_i, best_r = i, r
            if best_i >= 0 and best_r >= match_ratio:
                used[best_i] = True
                pairs.append((g, best_i))

        total_gt += len(gts)
        matched += len(pairs)
        for g, i in pairs:
            cer_sum += cer(g.text, parsed[i][1])
            if parsed[i][0] == g.side:
                side_ok += 1
        pred_unmatched += sum(
            1 for i, u in enumerate(used) if not u and not parsed[i][2])

        # 对话可解析：GT 有对方消息才适用（没有则链路本就解析不出 current）
        if any(g.side == SIDE_OTHER for g in gts):
            parse_applicable += 1
            _, current = split_dialogue_turns(list(preds))
            if current is not None:
                parse_ok += 1

    extra_preds = sorted(set(pred_by_image) - {r.image for r in corpus})
    return {
        "images": len(corpus),
        "images_without_prediction": len(unmatched_images),
        "unmatched_images": unmatched_images,
        "extra_prediction_images": extra_preds,
        "gt_messages": total_gt,
        "matched_messages": matched,
        "message_recall": round(matched / total_gt, 4) if total_gt else None,
        "side_accuracy": round(side_ok / matched, 4) if matched else None,
        "line_cer": round(cer_sum / matched, 4) if matched else None,
        "pred_lines": total_pred,
        "time_mark_lines": time_lines,
        # 分母剔除时间标记行：它们是合法结构标记，摊进分母会虚低误入率
        "noise_intrusion": round(pred_unmatched / (total_pred - time_lines), 4)
        if total_pred > time_lines else None,
        "unmatched_pred_lines": pred_unmatched,
        "dialogue_parse_rate": round(parse_ok / parse_applicable, 4) if parse_applicable else None,
    }


def format_report(m: Dict) -> str:
    """评测指标 → 控制台可读报告（门槛数值一行一个，便于贴进提交/对比）。"""
    def pct(v):
        return f"{v * 100:.2f}%" if v is not None else "n/a"

    lines = [
        "== T05727 读屏 OCR 基线评测 ==",
        f"图片数             {m['images']}（缺预测 {m['images_without_prediction']}"
        f" / 多预测 {len(m['extra_prediction_images'])}）",
        f"消息级召回率       {pct(m['message_recall'])}   ({m['matched_messages']}/{m['gt_messages']})",
        f"侧别准确率         {pct(m['side_accuracy'])}",
        f"行级 CER           {m['line_cer'] if m['line_cer'] is not None else 'n/a'}",
        f"噪声误入率         {pct(m['noise_intrusion'])}   "
        f"({m.get('unmatched_pred_lines', 0)}/{m['pred_lines'] - m.get('time_mark_lines', 0)} 非时间预测行)",
        f"对话可解析率       {pct(m['dialogue_parse_rate'])}",
    ]
    if m["unmatched_images"]:
        lines.append("缺预测图片: " + ", ".join(m["unmatched_images"]))
    return "\n".join(lines)
