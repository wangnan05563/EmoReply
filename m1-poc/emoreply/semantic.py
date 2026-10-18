"""FR-1.6 纯语义「指向我」判定（M4 二期扩展，m1-poc 先行）。

SRS 验收：测试集准确率 ≥85%；「无需回复」误判为「需回复」≤5%
——压误判优先于召回（与 FR-2.4「误打扰比漏判更伤体验」同一口径）。

两级判定：
1. 规则信号层（默认，零依赖、离线可用）：
   - 显式称呼（用户人设名/别名表）→ 指向我
   - 第二人称代词（你/您/你们/你的）→ 指向我（群聊里第二人称几乎必有所指）
   - 其余（广播词、泛问求助、通知陈述）→ 默认「不指向」——宁漏勿扰
2. LLM 语义层（可选插拔）：有云端配置时启用；超时/失败/输出不可解析时
   回退规则层结果，绝不因语义层故障阻断主链路。

集成约定：preprocess.is_directed_to_me(n, semantic_judge=None)——
judge 为 None 时行为与首期完全一致（硬规则，零回归）。
"""
import json
import re
from typing import Callable, List, Optional, Protocol

from .models import Notification

# 第二人称代词：群聊无 @ 时最强的指向信号
_SECOND_PERSON = re.compile(r"[你您]")

# 广播/泛指词：出现且无第二人称时倾向「面向全体」而非「指向我」
_BROADCAST = re.compile(r"大家|各位|所有人|全部")

# 明确的「无需回复」通知语气（进一步压 FP，宁可漏掉误用于通知里的第二人称）
_NOTICE = re.compile(r"请准时|请查收|注意安全|自行|周知|截止时间|停发|升级维护|系统已恢复")


class SemanticVerdict:
    """单条消息的语义判定结果。"""

    def __init__(self, points_to_me: bool, confidence: float, reason: str):
        self.points_to_me = points_to_me
        self.confidence = confidence
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Verdict(to_me={self.points_to_me}, conf={self.confidence:.2f}, {self.reason})"


class SemanticJudge(Protocol):
    """语义判定器协议：给定通知，返回指向判定。"""

    def judge(self, n: Notification) -> SemanticVerdict: ...


class RuleSemanticJudge:
    """规则信号层：显式称呼 / 第二人称 / 广播排除，默认不指向。"""

    def __init__(self, my_names: Optional[List[str]] = None):
        # 人设名/别名表（去空、去重、转小写便于包含匹配）
        self.my_names = {x.strip().lower() for x in (my_names or []) if x and x.strip()}

    def judge(self, n: Notification) -> SemanticVerdict:
        body = (n.body or "").strip()
        if not body:
            return SemanticVerdict(False, 0.5, "空载荷，不判定")
        lowered = body.lower()

        # 1) 显式称呼（强信号）
        hit_name = next((x for x in self.my_names if x in lowered), None)
        if hit_name:
            return SemanticVerdict(True, 0.95, f"显式称呼「{hit_name}」")

        # 2) 第二人称代词（中强信号）；通知语气词出现时压回（压误判优先）
        if _SECOND_PERSON.search(body) and not _NOTICE.search(body):
            return SemanticVerdict(True, 0.7, "第二人称直接指称")

        # 3) 广播/泛指/通知 → 不指向
        if _BROADCAST.search(body) or _NOTICE.search(body):
            return SemanticVerdict(False, 0.8, "广播/通知语气，面向全体")

        # 4) 信号不足 → 默认不指向（宁漏勿扰）
        return SemanticVerdict(False, 0.5, "无指向信号，默认忽略")


class LlmSemanticJudge:
    """LLM 语义层：把指向判定交给云端模型；任何故障回退 fallback（规则层）。

    llm_call 可注入以便测试（签名：prompt -> 模型回复文本）。
    生产用法：
        LlmSemanticJudge(llm_call=http_chat, fallback=RuleSemanticJudge(["小王"]))
    """

    PROMPT_TEMPLATE = (
        "判断这条群聊消息是否指向「我」并需要我回复（无 @ 无引用）。\n"
        "消息：{body}\n"
        "只回答 YES 或 NO。YES 标准：消息明确对我说（称呼我/直接问我/需要我行动）。\n"
        "面向全体的广播、通知、泛问求助一律 NO。宁可 NO 不可误 YES。"
    )

    def __init__(self,
                 llm_call: Callable[[str], str],
                 fallback: SemanticJudge,
                 my_names: Optional[List[str]] = None):
        self.llm_call = llm_call
        self.fallback = fallback
        self.my_names = {x.strip().lower() for x in (my_names or []) if x and x.strip()}

    def judge(self, n: Notification) -> SemanticVerdict:
        body = (n.body or "").strip()
        if not body:
            return SemanticVerdict(False, 0.5, "空载荷，不判定")
        try:
            answer = (self.llm_call(self.PROMPT_TEMPLATE.format(body=body)) or "").strip().upper()
        except Exception:  # 网络失败/超时/解析异常 → 回退
            return self.fallback.judge(n)
        if "YES" in answer and "NO" not in answer:
            return SemanticVerdict(True, 0.9, "LLM 语义判定：指向我")
        if "NO" in answer:
            return SemanticVerdict(False, 0.9, "LLM 语义判定：不指向")
        return self.fallback.judge(n)  # 输出不可解析 → 回退


def build_seed_dataset(path: str) -> List[dict]:
    """加载种子测试集（jsonl：body/label/why；label: need_reply|ignore）。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                rows.append(json.loads(line))
    return rows


def evaluate_seed(judge: SemanticJudge, rows: List[dict],
                  wrap: Callable[[str], Notification]) -> dict:
    """在种子集上评估：acc 与 FP（ignore 误判为 need_reply 的比例）。"""
    total = len(rows)
    fp = 0
    correct = 0
    fp_cases: List[str] = []
    for r in rows:
        verdict = judge.judge(wrap(r["body"]))
        predicted = verdict.points_to_me
        expected = r["label"] == "need_reply"
        if predicted == expected:
            correct += 1
        elif predicted and not expected:
            fp += 1
            fp_cases.append(r["body"])
    return {
        "total": total,
        "accuracy": correct / total if total else 0.0,
        "false_positive_rate": fp / total if total else 0.0,
        "fp_cases": fp_cases,
    }
