"""M1 核心链路编排：捕获 → 预处理 → 上下文/提示词 → 生成 → 清洗 → 护栏 → 候选。

生成步骤由注入的 generator 回调承担：真机侧接云端 LLM，本环境用确定性桩跑通链路。
这层解耦让「平台无关部分」可以脱离 Android 独立验证——即本环境可达的降级验证等级。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .classify import classify_emotion, classify_intent, needs_reply
from .compose import (MULTI_TONE_LABELS, MULTI_TONE_VARIANTS, OPENING_CANDIDATES, OPENING_SCENARIOS,
                      DEFAULT_CANDIDATES, PromptBundle,
                      build_opening_prompt, build_prompt, build_exclude_hint, tone_hint)
from .feedback import adapt_tone  # T01350：采纳率闭环（低分场景自动降档）
from .guardrail import NoticeThrottle, screen_candidate, screen_incoming
from .models import Candidate, Decision, Notification
from .preprocess import decide, dedupe
from .rag import LexicalRetriever
from .sanitize import (expand_candidates, filter_duplicates, is_duplicate, is_meta_candidate,
                       normalize_emoji, pair_unmarked_reasons, split_rationale, strip_emoji)
from .tone import TONE_AUTO, resolve_tone  # T01348：关系感知 → 语气默认档位

NO_REPLY_NOTICE = "通知类消息，判定无需回复（FR-2.4）"

Generator = Callable[[PromptBundle], List[str]]


@dataclass
class PipelineOutcome:
    decision: Decision
    candidates: List[Candidate] = field(default_factory=list)
    notice: Optional[str] = None
    suppressed: bool = False


class ReplyPipeline:
    """M1 概念验证的核心链路（不含 Android 捕获与 IME 注入）。

    persona（可选）：PersonaStore 实例（FR-5.1/5.2）。提供时——
    自称/关系/默认风格并入提示词，禁语进入输出侧过滤；不提供则行为与 M1 完全一致。
    kb（可选）：KnowledgeBase（RAG，决策③）；retriever 缺省 LexicalRetriever，
    可换 EmbeddingRetriever（语义版）。无命中条目时不注入，行为不变。
    """

    def __init__(self,
                 generator: Generator,
                 throttle: Optional[NoticeThrottle] = None,
                 style: Optional[str] = None,
                 max_tokens: int = 4096,
                 persona=None,
                 kb=None,
                 retriever=None,
                 emoji: bool = True,
                 feedback=None,          # T01350：采纳率闭环；传 None 则闭环关闭
                 auto_adapt: bool = True,  # T01350：低分场景自动降档开关（用户可关）
                 expected_candidates: int = DEFAULT_CANDIDATES,  # T02224：数量契约，与生成器 n 同源
                 ) -> None:
        self.generator = generator
        self.throttle = throttle or NoticeThrottle()
        self.explicit_style = style  # None 表示未显式指定，允许人设 default_style 生效
        self.max_tokens = max_tokens
        self.persona = persona
        self.kb = kb
        self.retriever = retriever
        self.emoji = emoji  # T01159：False 时提示词不提表情、输出侧剥掉表情
        self.feedback = feedback          # T01350：采纳率闭环；None 表示不接回
        self.auto_adapt = auto_adapt      # T01350：低分场景自动降档开关
        self.expected_candidates = expected_candidates  # T02224：理由配对的数量基准

    def _throttled_notice(self, current: Notification, decision: Decision,
                          now: Optional[float], key: str, notice: str) -> PipelineOutcome:
        """提示类出口：按 C6 节流，节流命中时只记 suppressed、不发重复提示。"""
        stamp = current.timestamp if now is None else now
        if not self.throttle.allow(key, stamp):
            return PipelineOutcome(decision, suppressed=True)
        return PipelineOutcome(decision, notice=notice)

    def _retrieve_knowledge(self, current: Notification) -> List[str]:
        """RAG（决策③）：知识检索注入——无命中则不注入，行为不变。"""
        if self.kb is None:
            return []
        retriever = self.retriever or LexicalRetriever()
        return [e.text for e in retriever.search(
            self.kb, query=current.body or "", contact=current.sender or current.chat, top_k=3)]

    def _resolve_context(self, current: Notification, tone: Optional[int],
                         multi_tone: bool) -> tuple:
        """人设（自称/关系/禁语/风格）+ 语气档位 + 采纳闭环 + 知识检索，一次解析齐。"""
        me, relation, banned = "我", "", []
        style = self.explicit_style or "亲切"
        if self.persona is not None:
            ctx = self.persona.prompt_context(current.sender or current.chat)
            me, relation, banned = ctx["me"], ctx["relation"], ctx["banned"]
            if self.explicit_style is None:
                style = ctx["style"]
        # T01348：AUTO 时按「联系人覆盖 > 关系映射」解析；
        # T01537 升级：联系人级覆盖 > 全局（手动值同样被覆盖）> 关系推档；
        # tone=None（不注入语气强度行）旧行为不变
        if tone is not None:
            contact = current.sender or current.chat
            override = self.persona.contact_tone(contact) if self.persona is not None else None
            tone = resolve_tone(tone, relation, override)
        # T01350：采纳率闭环——低分维度（关系×tone桶×风格，样本充足且采纳率<30%）
        # 自动下调语气一档；多风格并行时单值 tone 不生效，跳过 adapt
        if self.feedback is not None and not multi_tone:
            tone, _ = adapt_tone(relation, tone, style, self.feedback.dim_stats(),
                                 enabled=self.auto_adapt)
        knowledge = self._retrieve_knowledge(current)
        return me, relation, banned, style, tone, knowledge

    def _candidate_from_line(self, line: str, banned: List[str], style: str,
                             seen: List[str]) -> Optional[Candidate]:
        """单候选行 → 清洗/护栏/禁语/去重后的一条候选；不合规返回 None。

        - T01159：表情收敛（开启时防堆砌，关闭时剥干净）；
        - 元话语候选（LLM 请求澄清/自我指称）与输入内容无关，直接丢弃；
        - FR-5.1：候选含禁语 → 输出侧拦下；
        - FR-7.1：与已给候选（含本批内前面几条）重复 → 丢弃。
        """
        text, rationale = split_rationale(line)
        text = normalize_emoji(text) if self.emoji else strip_emoji(text)
        if not text or is_meta_candidate(text) or screen_candidate(text).risky:
            return None
        if self.persona is not None and self.persona.violates_banned(text, banned):
            return None
        if seen and is_duplicate(text, seen):
            return None
        seen.append(text)
        return Candidate(text=text, style=style, rationale=rationale)

    def _collect_candidates(self, prompt: PromptBundle, banned: List[str],
                            style: str, exclude: Optional[List[str]],
                            expected: Optional[int] = None) -> List[Candidate]:
        """生成 → 逐行拆候选 → 数量契约配对理由行 → 清洗/护栏/禁语/去重。

        T02224：先跨 choice 汇总候选行再配对——兼容服务可能把「候选+理由」挤进
        同一段，也可能一段只出一条，两种形态都要能认出无标记的理由行。
        """
        # FR-7.1：换一批——先按文本去重（提示词排除是软约束，输出侧兜底才是硬保证）
        seen: List[str] = list(exclude or [])
        lines: List[str] = []
        for raw in self.generator(prompt):
            # T01081：单条输出可能含多条候选（服务不支持 n>1 时 LLM 挤在同一段），
            # 先拆成单候选行再逐条解析——否则点任一候选会注入全部选项+解释
            lines.extend(expand_candidates(raw))
        base = self.expected_candidates if expected is None else expected
        candidates: List[Candidate] = []
        for line in pair_unmarked_reasons(lines, base):
            cand = self._candidate_from_line(line, banned, style, seen)
            if cand is not None:
                candidates.append(cand)
        # T02224：面板按 N 张卡片设计，超发一律截断——多出来的从来不是「更多选择」，
        # 而是漏网的理由行（真机实测「候选 6 条」= 3 候选 + 3 理由）
        return candidates[:base] if base > 0 else candidates

    @staticmethod
    def _apply_multi_tone_labels(candidates: List[Candidate]) -> None:
        """T01349：多风格并行——候选数与档位数严格一致时按序补语气标签（宁缺勿错，
        条数对不上说明模型没按序出，不打标避免误导）；去重已由 is_duplicate 覆盖。"""
        if len(candidates) != len(MULTI_TONE_VARIANTS):
            return
        for i, cand in enumerate(candidates):
            label = MULTI_TONE_LABELS[i] if i < len(MULTI_TONE_LABELS) else f"档{i + 1}"
            cand.rationale = f"{label}档" + (f"：{cand.rationale}" if cand.rationale else "")

    def handle(self,
               current: Notification,
               history: List[Notification],
               now: Optional[float] = None,
               tone: Optional[int] = None,
               exclude: Optional[List[str]] = None,
               scenario: Optional[str] = None,   # T01244 FR-8.2：三维筛选（场景）
               purpose: Optional[str] = None,    # T01244 FR-8.2：三维筛选（目的）
               multi_tone: bool = False) -> PipelineOutcome:   # T01349：多风格并行，默认关闭即旧行为
        decision = decide(current)
        gate = self._gate_outcome(current, decision, now)
        if gate is not None:
            return gate
        # FR-2.1/2.2（REQ-006）：意图与情绪并入提示词，增强生成的贴合度
        # （闸门放行后重算一次 intent——classify_intent 为纯函数，无副作用）
        intent = classify_intent(current.body)
        emotion, urgency = classify_emotion(current.body)
        me, relation, banned, style, tone, knowledge = self._resolve_context(current, tone,
                                                                             multi_tone)
        prompt = build_prompt(current, history, me=me, style=style, max_tokens=self.max_tokens,
                              intent=intent, emotion=emotion, urgency=urgency, relation=relation,
                              knowledge=knowledge, emoji=self.emoji,
                              tone=tone, tone_variants=MULTI_TONE_VARIANTS if multi_tone else None,
                              exclude=exclude,
                              scenario=scenario or "", purpose=purpose or "")
        candidates = self._collect_candidates(prompt, banned, style, exclude,
            # T01349：多风格并行一次出 3 条，数量契约随之改为档位数
            expected=len(MULTI_TONE_VARIANTS) if multi_tone else None)
        if multi_tone:
            self._apply_multi_tone_labels(candidates)
        return PipelineOutcome(decision, candidates=candidates)

    def _gate_outcome(self, current: Notification, decision: Decision,
                      now: Optional[float]) -> Optional[PipelineOutcome]:
        """生成前置闸门：drop/fallback/风险提示/纯通知四类出口；放行返回 None。

        - FR-6.4：命中风险时不生成迎合内容，改发风险提示（提示本身按 C6 节流）；
        - FR-2.4（REQ-006）：纯通知类不生成候选，压「无需回复误判为需回复」。
        """
        if decision.action == "drop":
            return PipelineOutcome(decision)
        if decision.action == "fallback":
            return PipelineOutcome(decision, notice=decision.fallback_hint)
        risk = screen_incoming(current.body or "")
        if risk.risky:
            return self._throttled_notice(current, decision, now,
                                          f"risk:{risk.category}", risk.advice)
        if not needs_reply(current.body, classify_intent(current.body)):
            return self._throttled_notice(current, decision, now, "no-reply", NO_REPLY_NOTICE)
        return None

    def handle_opening(self, scenario: str,
                       exclude: Optional[List[str]] = None) -> PipelineOutcome:
        """T01246 FR-8.4：无对话上下文时的场景化开场建议。

        与 handle 不同：无 current 消息，只按场景生成开场话术。输出经 sanitize/
        护栏/去重，与普通候选完全同构。decision 固定为手动生成的 GENERATE。
        """
        prompt = build_opening_prompt(scenario, style=self.explicit_style or "亲切",
                                      max_tokens=self.max_tokens)
        banned = self.persona.prompt_context("").get("banned", []) if self.persona else []
        candidates = self._collect_candidates(prompt, banned, "亲切", exclude,
                                              expected=OPENING_CANDIDATES)  # FR-8.4 开场固定 3 条
        return PipelineOutcome(decision=Decision("generate", "开场建议（FR-8.4）", None),
                               candidates=candidates)

    def run(self, notifications: List[Notification]) -> List[PipelineOutcome]:
        """批量入口：按时间排序去重后，逐条以其同会话历史为上下文处理。"""
        by_chat: Dict[str, List[Notification]] = {}
        outcomes: List[PipelineOutcome] = []
        for n in dedupe(sorted(notifications, key=lambda x: x.timestamp)):
            key = f"{n.source}:{n.chat}"
            history = by_chat.setdefault(key, [])
            outcomes.append(self.handle(n, list(history)))
            history.append(n)
        return outcomes