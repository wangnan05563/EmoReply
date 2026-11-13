"""T05727：真机读屏语料采集——从 adb logcat 提取 OCR 行框与节点树可读率。

采集流程（人工配对，与 replay_t02305_device.py 同一约定）：
1. 手机进入目标聊天界面（微信/钉钉/QQ/短信 × 单聊/群聊 × 明暗主题 × 字号轮换）；
2. 用 EmoReply 触发一次读屏（节点树或截图 OCR 路径均可）；
3. 读屏前后各执行一次整屏截图：
   `adb exec-out screencap -p > shots/<序号>.png`
   （「ocr: line」坐标以裁剪区为原点，回放整屏需标定 y 偏移，参照 replay 脚本）；
4. 采集完一批后运行本脚本导出。

用法：
  python scripts/collect_baseline_device.py [--out baseline/device]

产出：
- `predictions.jsonl`：OCR 会话的行框 → 文本（评测用预测；GT 需人工对截图标注后
  并入 corpus.jsonl 的真机分区）；
- `readability.json`：节点树可读率统计（via=节点树 会话数 / 总会话数）——
  决定 T05728「节点树×OCR 交叉验证」的收益面。

注意：logcat 里的 IME 侧 `via` 标记（EmoReplyImeService）是当前唯一的路径信号；
按应用细分的可读率需要 reader 日志补包名，当前版本只出总量（诚实边界，写进
readability.json 的 note 字段）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# EmoReplyReaderService 的行级诊断日志：ocr: line t=.. b=.. l=.. r=.. "文本"
LINE_RE = re.compile(
    r'(?P<ts>\d{2}-\d{2} (?P<clock>[\d:.]+)).*EmoReplyReader.*ocr: line '
    r't=(?P<t>-?\d+) b=(?P<b>-?\d+) l=(?P<l>-?\d+) r=(?P<r>-?\d+) "(?P<txt>.*)"')
START_RE = re.compile(r'(?P<ts>\d{2}-\d{2} (?P<clock>[\d:.]+)).*EmoReplyReader.*ocr: start')
# IME 侧读屏路径标记（T01124）：via = 节点树 / 截图OCR
VIA_RE = re.compile(r'(?P<ts>\d{2}-\d{2} (?P<clock>[\d:.]+)).*EmoReplyIme.*via = (?P<via>\S+)')


def logcat_dump(adb: str) -> str:
    r = subprocess.run([adb, "logcat", "-d"], capture_output=True, text=True,
                       errors="replace", timeout=60)
    if r.returncode != 0:
        sys.exit(f"adb logcat 失败：{r.stderr[:300]}")
    return r.stdout


def parse(log: str):
    """按时间序扫描：ocr: start 开启一个 OCR 会话，其后的 line 归属该会话。"""
    sessions, cur, via_counts = [], None, Counter()
    for ln in log.splitlines():
        m = START_RE.search(ln)
        if m:
            cur = {"clock": m["clock"], "lines": []}
            sessions.append(cur)
            continue
        m = LINE_RE.search(ln)
        if m and cur is not None:
            cur["lines"].append({
                "text": m["txt"], "left": int(m["l"]), "right": int(m["r"]),
                "top": int(m["t"]), "bottom": int(m["b"]),
            })
            continue
        m = VIA_RE.search(ln)
        if m:
            via_counts[m["via"]] += 1
    return [s for s in sessions if s["lines"]], via_counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adb", default="adb")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "baseline" / "device"))
    args = ap.parse_args()

    sessions, via_counts = parse(logcat_dump(args.adb))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pred_path = out / "predictions.jsonl"
    with open(pred_path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sessions):
            # 真机 OCR 路径输出未经侧别标注（前缀在 IME 层拼接后才落日志），
            # 这里保留裸文本 + 行框：侧别由人工对照截图标注，或回放链路补判。
            f.write(json.dumps({
                "image": f"shots/session-{i:03d}-{s['clock'].replace(':', '')}.png",
                "clock": s["clock"],
                "lines": [l["text"] for l in s["lines"]],
                "boxes": s["lines"],
            }, ensure_ascii=False) + "\n")

    total = sum(via_counts.values())
    node = via_counts.get("节点树", 0)
    readability = {
        "sessions_via": dict(via_counts),
        "node_tree_ratio": round(node / total, 4) if total else None,
        "note": "总量口径；按应用细分需 reader 日志补包名，当前 logcat 无此信息",
    }
    with open(out / "readability.json", "w", encoding="utf-8") as f:
        json.dump(readability, f, ensure_ascii=False, indent=2)

    print(f"OCR 会话 {len(sessions)} 个 → {pred_path}")
    for i, s in enumerate(sessions):
        print(f"  session-{i:03d} [{s['clock']}] {len(s['lines'])} 行")
    print(f"读屏路径统计：{dict(via_counts)}  节点树可读率={readability['node_tree_ratio']}")
    if not sessions:
        print("（未捕获到读屏会话：请先在真机上触发 EmoReply 读屏，再运行本脚本）")


if __name__ == "__main__":
    main()
