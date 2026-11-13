"""T05730：端侧 LLM 低置信行纠错 POC——评测执行器。

流程：读 T05727 语料 GT 消息 → 按置信场景注入实测误读对 → 纠错器跑纠错 →
用 baseline.cer 对比「注入后未纠错」与「注入后纠错」的行级 CER → report.json。

运行方式：
- 本机无模型（当前状态）：`python scripts/eval_correction_poc.py`
  只产出 Noop 基线（= 注入后 CER），LLM 列空缺并标注。
- 有任意 OpenAI 兼容端点（云端测效果上限 / 本地 Ollama 测端侧形态）：
  `python scripts/eval_correction_poc.py --endpoint http://127.0.0.1:11434/v1 --model qwen2.5:0.5b`

准入判读（T05725 报告 §4.4）：纠错 CER 显著低于基线且无误杀（正确行被改错）
→ 才值得建端侧实装单；否则 POC 结论「不开」，端侧 VLM 维持关闭。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emoreply.baseline import _gt_messages, cer, load_corpus  # noqa: E402
from emoreply.correction import (  # noqa: E402
    NoopCorrector, OpenAICompatibleCorrector, inject_ocr_errors,
)


def build_noisy_dataset(corpus, *, error_chance: float, seed: str):
    """GT 消息 → 注入误读后的（噪声行, 干净行, 上下文）三元组集合。

    每条消息独立注入；上下文取同一张图内**其他**消息文本（纠错器可利用的话题线索，
    模拟读屏「结合上下文猜字」的真实输入形态）。seed 锁定可复现。
    """
    rng = random.Random(seed)
    samples = []
    n_injected = 0
    for rec in corpus:
        msgs = _gt_messages(rec)
        others = [g.text for g in msgs]
        for j, g in enumerate(msgs):
            noisy = inject_ocr_errors(g.text, rng, chance=error_chance)
            if noisy != g.text:
                n_injected += 1
            context = others[:j] + others[j + 1:]
            samples.append({"image": rec.image, "clean": g.text, "noisy": noisy,
                            "context": context})
    return samples, n_injected


def score(samples, corrected):
    """平均 CER（对干净 GT）+ 误杀数（原本文本正确的行被改错）。"""
    cer_sum = 0.0
    harmed = 0
    for s, out in zip(samples, corrected):
        cer_sum += cer(s["clean"], out)
        if s["noisy"] == s["clean"] and out != s["clean"]:
            harmed += 1
    return cer_sum / len(samples), harmed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "baseline" / "corpus.jsonl"))
    ap.add_argument("--error-chance", type=float, default=0.4,
                    help="每条消息被注入误读的概率（注入量 = 模拟低置信行占比）")
    ap.add_argument("--seed", default="20261113")
    ap.add_argument("--endpoint", default=None, help="OpenAI 兼容端点（如 http://127.0.0.1:11434/v1）")
    ap.add_argument("--model", default="qwen2.5:0.5b")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    corpus = load_corpus(args.corpus)
    samples, n_injected = build_noisy_dataset(corpus, error_chance=args.error_chance,
                                              seed=args.seed)
    out_path = Path(args.out) if args.out else Path(args.corpus).parent / "correction-report.json"

    report = {
        "gt_messages": len(samples),
        "injected_messages": n_injected,
        "error_chance": args.error_chance,
        "correctors": {},
    }

    # 基线：不纠错（直通）
    noop_cer, noop_harm = score(samples, NoopCorrector().correct(
        [s["noisy"] for s in samples], [s["context"] for s in samples]))
    report["correctors"]["noop"] = {"cer": round(noop_cer, 4), "harmed": noop_harm}

    # LLM 纠错（可选：有端点才跑）
    if args.endpoint:
        llm = OpenAICompatibleCorrector(args.endpoint, args.model, args.api_key)
        # 逐样本调用（POC 量级可接受；批接口留给端侧实装阶段）
        corrected = [llm.correct([s["noisy"]], s["context"])[0] for s in samples]
        llm_cer, llm_harm = score(samples, corrected)
        report["correctors"]["openai_compatible"] = {
            "endpoint": args.endpoint, "model": args.model,
            "cer": round(llm_cer, 4), "harmed": llm_harm,
        }
        report["verdict"] = (
            "LLM 纠错 CER 显著低于基线且无误杀 → 可建端侧实装单"
            if llm_cer < noop_cer * 0.8 and llm_harm == 0
            else "收益不足或存在误杀 → POC 结论「不开」，端侧纠错维持关闭")
    else:
        report["verdict"] = ("本机无推理运行时：LLM 列空缺，仅产出注入基线；"
                             "待接入端点后复跑 --endpoint 得出准入判定")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
