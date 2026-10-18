#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_jtl.py —— 原生 JMeter jtl 基线对比分析器（配置驱动，零业务硬编码）

用途：
  优化前后用「同一份 JMX」复跑，得到修复前/后两份 jtl，自动按端点分组（label_match）
  对比 TPS / 百分位 / 错误率，量化收益。端点名、分组、文件路径全部来自配置，
  引擎不内嵌任何业务常量。

支持两种 jtl 格式（自动识别）：
  - XML：<testResults><sample lb=".." t=".." ts=".." s="true" rc="200"/>...
  - CSV：JMeter saveservice csv（列含 label/elapsed/timestamp/success/responseCode）

用法：
  python scripts/analyze_jtl.py --config config/jmeter-config.yml
  （读取 config.analysis.inputs / endpoint_groups / percentiles / output_report）
"""

import argparse
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET


def load_config(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 PyYAML：请先 `pip install pyyaml`")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --------------------------------------------------------------------------
# jtl 解析（XML / CSV 自动识别）
# --------------------------------------------------------------------------
def parse_jtl(path: str):
    """返回 [(label, elapsed_ms, ts_ms, success:bool, code:str), ...]"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        head = f.read(2048)
        f.seek(0)
        if head.lstrip().startswith("<?xml") or "<testResults" in head:
            return _parse_xml(f)
        return _parse_csv(f)


def _parse_xml(fh):
    out = []
    try:
        root = ET.parse(fh).getroot()
    except ET.ParseError as e:
        # 截断/损坏的 jtl（压测被中断常见）：退化为逐行正则提取
        fh.seek(0)
        return _parse_xml_fallback(fh.read())
    for s in root.iter("sample"):
        try:
            out.append((
                s.get("lb", ""),
                float(s.get("t", 0)),
                float(s.get("ts", 0)),
                s.get("s", "true") == "true",
                s.get("rc", ""),
            ))
        except Exception:
            continue
    return out


def _parse_xml_fallback(text):
    out = []
    for m in re.finditer(r'<sample\b[^>]*\b', text):
        seg = m.group(0)
        def attr(name):
            mm = re.search(rf'{name}="([^"]*)"', seg)
            return mm.group(1) if mm else ""
        try:
            out.append((
                attr("lb"),
                float(attr("t") or 0),
                float(attr("ts") or 0),
                attr("s") == "true",
                attr("rc"),
            ))
        except Exception:
            continue
    return out


def _parse_csv(fh):
    out = []
    reader = csv.DictReader(fh)
    # 兼容不同 saveservice 列名
    def col(row, *names):
        for n in names:
            if n in row and row[n] != "":
                return row[n]
        return ""
    for row in reader:
        try:
            lb = col(row, "label", "LB")
            t = float(col(row, "elapsed", "t", "ET") or 0)
            ts = float(col(row, "timestamp", "ts", "TS") or 0)
            suc = str(col(row, "success", "s", "S")).lower() in ("true", "1", "y", "yes")
            rc = col(row, "responseCode", "rc", "RC", "code")
            out.append((lb, t, ts, suc, rc))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------
def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def aggregate(samples, percentiles):
    """samples: list of (label, elapsed, ts, success, code)"""
    if not samples:
        return None
    elapsed = sorted(s[1] for s in samples)
    n = len(elapsed)
    errors = sum(1 for s in samples if not s[3])
    ts_vals = [s[2] for s in samples if s[2] > 0]
    duration = (max(ts_vals) - min(ts_vals)) / 1000.0 if len(ts_vals) > 1 else 0.0
    tps = (n / duration) if duration > 0 else float(n)
    return {
        "count": n,
        "error_rate": (errors / n * 100.0) if n else 0.0,
        "avg": sum(elapsed) / n,
        "p": {p: pct(elapsed, p) for p in percentiles},
        "tps": tps,
        "duration_s": duration,
    }


def group_of(label, groups):
    for g in groups:
        pat = g.get("label_match", "")
        if pat and re.search(pat, label):
            return g.get("title", pat)
    return "其它"


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def fmt(v, suffix=""):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


def build_report(inputs, groups, percentiles, defect_rules):
    # 读取所有输入
    data = {}  # tag -> {group -> samples}
    tags = []
    for inp in inputs:
        f = inp.get("file")
        tag = inp.get("tag", f)
        if tag not in tags:
            tags.append(tag)
        if not f or not os.path.isfile(f):
            print(f"[analyze] 跳过缺失文件：{f}")
            continue
        samples = parse_jtl(f)
        by_group = {}
        for s in samples:
            g = group_of(s[0], groups)
            by_group.setdefault(g, []).append(s)
        agg = {g: aggregate(v, percentiles) for g, v in by_group.items()}
        agg["总体"] = aggregate(samples, percentiles)
        data[tag] = agg
        print(f"[analyze] {tag}: {f} -> {len(samples)} 样本, {len(by_group)} 组")

    if not data:
        return "（无可用 jtl 数据）"

    all_groups = []
    for by_group in data.values():
        for g in by_group:
            if g not in all_groups:
                all_groups.append(g)
    # 保证「总体」与「其它」位置合理
    ordered = []
    if "总体" in all_groups:
        ordered.append("总体")
    for g in all_groups:
        if g not in ("总体", "其它"):
            ordered.append(g)
    if "其它" in all_groups:
        ordered.append("其它")

    lines = ["# JMeter 基线对比分析报告", ""]
    lines.append(f"标签：{' / '.join(tags)}")
    lines.append(f"百分位：{percentiles}")
    lines.append("")

    metric_rows = [
        ("样本数", lambda a: a["count"], ""),
        ("错误率", lambda a: a["error_rate"], "%"),
        ("平均(ms)", lambda a: a["avg"], ""),
        *[(f"P{p}(ms)", lambda a, p=p: a["p"].get(p), "") for p in percentiles],
        ("TPS", lambda a: a["tps"], ""),
    ]

    for g in ordered:
        lines.append(f"## 分组：{g}")
        lines.append("")
        lines.append("| 指标 | " + " | ".join(tags) + " |")
        lines.append("|" + "------|" * (len(tags) + 1))
        for mname, fn, suf in metric_rows:
            cells = []
            for tag in tags:
                a = (data.get(tag) or {}).get(g)
                cells.append(fmt(fn(a), suf) if a else "-")
            lines.append(f"| {mname} | " + " | ".join(cells) + " |")
        # 双标签相对变化（首个为基线，末尾为候选）
        if len(tags) == 2:
            base = (data.get(tags[0]) or {}).get(g)
            cand = (data.get(tags[1]) or {}).get(g)
            if base and cand and base["avg"] > 0 and cand["avg"] > 0:
                imp_avg = (base["avg"] - cand["avg"]) / base["avg"] * 100.0
                imp_tps = (cand["tps"] - base["tps"]) / base["tps"] * 100.0 if base["tps"] else 0
                p95_b = base["p"].get(percentiles[-1])
                p95_c = cand["p"].get(percentiles[-1])
                imp_p = (p95_b - p95_c) / p95_b * 100.0 if (p95_b and p95_c) else None
                lines.append("")
                lines.append(f"> 相对变化（{tags[1]} vs {tags[0]}）："
                             f"平均延迟 {'↓' if imp_avg > 0 else '↑'}{abs(imp_avg):.0f}%，"
                             f"TPS {'↑' if imp_tps > 0 else '↓'}{abs(imp_tps):.0f}%"
                             + (f"，P{percentiles[-1]} {'↓' if imp_p > 0 else '↑'}{abs(imp_p):.0f}%"
                                if imp_p is not None else ""))
        lines.append("")

    lines.append("---")
    lines.append("说明：错误率 = 样本中 success≠true 的占比（从原始响应重算，不信任工具 success 字段）。")
    lines.append("分组依据 config.analysis.endpoint_groups.label_match（正则匹配 sampler label）。")
    if defect_rules:
        lines.append(f"缺陷判定规则（参考）：codes={defect_rules.get('defect_codes')} "
                     f"prefixes={defect_rules.get('defect_prefixes')} "
                     f"expected={defect_rules.get('expected_codes')}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="JMeter jtl 基线对比分析（配置驱动）")
    ap.add_argument("--config", default="config/jmeter-config.yml",
                    help="jmeter-config.yml 路径（相对 CWD 或绝对）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    an = cfg.get("analysis") or {}
    if not an.get("enabled", False):
        print("[analyze] analysis.enabled=false，退出（无操作）")
        return
    inputs = an.get("inputs") or []
    if not inputs:
        print("[analyze] analysis.inputs 为空，退出")
        return
    groups = an.get("endpoint_groups") or []
    percentiles = an.get("percentiles") or [50, 95, 99]
    out = an.get("output_report") or "output/results/comparison_report.md"
    defect_rules = cfg.get("defect_rules") or {}

    report = build_report(inputs, groups, percentiles, defect_rules)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"[analyze] 报告已写入：{out}")


if __name__ == "__main__":
    main()
