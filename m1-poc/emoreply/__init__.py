"""M1 概念验证·平台无关核心链路参考实现。

覆盖 SRS 中不依赖 Android 运行时的部分：去重过滤（FR-1.4）、指向判定（FR-1.3）、
空载荷兜底（FR-1.5）、上下文与 token 预算（FR-3.1 / 附录 C-2 C16）、输出清洗
（附录 C C3/C25）、安全护栏（FR-3.5/6.4）。
"""
from .compose import PromptBundle, build_prompt, estimate_tokens
from .guardrail import NoticeThrottle, RiskAssessment, screen_candidate, screen_incoming
from .models import Candidate, Decision, Notification
from .pipeline import PipelineOutcome, ReplyPipeline
from .preprocess import decide, dedupe, is_directed_to_me, preprocess
from .sanitize import clean_candidate, strip_markdown, strip_markup

__all__ = [
    "PromptBundle",
    "build_prompt",
    "estimate_tokens",
    "NoticeThrottle",
    "RiskAssessment",
    "screen_candidate",
    "screen_incoming",
    "Candidate",
    "Decision",
    "Notification",
    "PipelineOutcome",
    "ReplyPipeline",
    "decide",
    "dedupe",
    "is_directed_to_me",
    "preprocess",
    "clean_candidate",
    "strip_markdown",
    "strip_markup",
]