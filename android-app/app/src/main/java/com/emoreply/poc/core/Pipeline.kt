package com.emoreply.poc.core

import com.emoreply.poc.model.Action
import com.emoreply.poc.model.Candidate
import com.emoreply.poc.model.Decision
import com.emoreply.poc.model.Notification
import com.emoreply.poc.store.KnowledgeBase
import com.emoreply.poc.store.LexicalRetriever
import com.emoreply.poc.store.PersonaStore

const val NO_REPLY_NOTICE = "通知类消息，判定无需回复（FR-2.4）"

/**
 * M1 核心链路编排：捕获 → 预处理 → 上下文/提示词 → 生成 → 清洗 → 护栏 → 候选。
 *
 * 生成步骤由注入的 generator 回调承担：真机侧接云端 LLM，概念验证期用确定性桩跑通链路。
 * 这层解耦让「平台无关部分」可以脱离 Android 独立验证。
 */
typealias Generator = (PromptBundle) -> List<String>

data class PipelineOutcome(
    val decision: Decision,
    val candidates: List<Candidate> = emptyList(),
    val notice: String? = null,
    val suppressed: Boolean = false,
)

class ReplyPipeline(
    private val generator: Generator,
    private val throttle: NoticeThrottle = NoticeThrottle(),
    private val style: String = "亲切",
    private val maxTokens: Int = DEFAULT_MAX_TOKENS,
    private val persona: PersonaStore? = null,
    private val kb: KnowledgeBase? = null,
    private val emoji: Boolean = true, // T01159：false=提示词不提表情 + 输出侧剥掉表情
    /** T02224：本次生成请求的候选条数（数量契约），与生成器 n / 提示词措辞同源。 */
    private val expectedCandidates: Int = DEFAULT_CANDIDATES,
) {

    /**
     * manual=true（T01082 反馈①手动生成入口）：用户已显式要求回复，跳过指向性判定与
     * 「通知类无需回复」闸；风险筛查（FR-6.4）与候选护栏仍然生效，不因手动而豁免。
     */
    fun handle(
        current: Notification,
        history: List<Notification>,
        now: Long? = null,
        manual: Boolean = false,
        tone: Int? = null, // T01158 FR-7.3：语气强度滑杆
        exclude: List<String> = emptyList(), // T01158 FR-7.1：换一批（已给候选）
        scenario: String = "", // T01244 FR-8.2：三维筛选（场景）
        purpose: String = "",  // T01244 FR-8.2：三维筛选（目的）
        favorites: List<String> = emptyList(), // T01262 FR-8.8：收藏话术（同类场景优先参考）
        multiTone: Boolean = false, // T01349：多风格并行（默认关=旧行为）
    ): PipelineOutcome {
        val decision = if (manual) {
            Decision(Action.GENERATE, "手动触发（用户显式要求回复）", current)
        } else {
            decide(current)
        }
        when (decision.action) {
            Action.DROP -> return PipelineOutcome(decision)
            Action.FALLBACK -> return PipelineOutcome(decision, notice = decision.fallbackHint)
            Action.GENERATE -> Unit
        }

        // FR-6.4：命中风险时不生成迎合内容，改发风险提示（提示本身按 C6 节流）
        val risk = screenIncoming(current.body)
        if (risk.risky) {
            return throttledNotice(current, decision, now, "risk:${risk.category}", risk.advice)
        }

        // 意图判定：非 manual 时用于「通知类无需回复」闸，两种路径都并入提示词
        val intent = Classify.classifyIntent(current.body)
        // FR-2.4（REQ-006）：纯通知类不生成候选，压「无需回复误判为需回复」
        // （manual 时不适用——用户手动粘贴即显式要求生成，跳过该闸）
        if (!manual && !Classify.needsReply(current.body, intent)) {
            return throttledNotice(current, decision, now, "no-reply", NO_REPLY_NOTICE)
        }

        // FR-2.1/2.2（REQ-006）：意图与情绪并入提示词，增强生成的贴合度
        val (emotion, urgency) = Classify.classifyEmotion(current.body)
        // FR-5.1/5.2：人设（自称/禁语）与联系人关系（可选，与 m1-poc persona 同构）
        var me = "我"
        var relation = ""
        var banned: List<String> = emptyList()
        var lastTopic = "" // T01259 FR-8.5：上次聊到（跨会话记忆，来自最近历史摘要）
        persona?.let { p ->
            val ctx = p.promptContext(current.sender ?: current.chat)
            me = ctx.me
            relation = ctx.relation
            banned = ctx.banned
            lastTopic = ctx.lastTopic
        }
        // RAG（决策③）：知识检索注入——无命中则不注入，行为不变
        val knowledge = kb?.let {
            LexicalRetriever.search(it, current.body, contact = current.sender ?: current.chat)
        }?.map { e -> e.text } ?: emptyList()
        val prompt = buildPrompt(current, history, me = me, style = style, maxTokens = maxTokens,
            intent = intent, emotion = emotion, urgency = urgency, relation = relation,
            scenario = scenario, purpose = purpose,
            knowledge = knowledge, emoji = emoji, tone = tone,
            // T01349：多风格并行（单请求多语气，RTT 不变）；非空优先于单值 tone
            toneVariants = if (multiTone) MULTI_TONE_VARIANTS else null,
            exclude = exclude,
            lastTopic = lastTopic, favorites = favorites)
        val candidates = collectCandidates(prompt, banned, style, exclude,
            // T01349：多风格并行一次出 3 条，数量契约随之改为档位数
            expected = if (multiTone) MULTI_TONE_VARIANTS.size else expectedCandidates)
        // T01259 FR-8.5：生成后把首条候选写入该联系人历史（AES 加密落库、仅设备本地）。
        // 写入时机在生成完成后——同一次 handle 的 promptContext 先读、后写，不产生自指注入；
        // 下次会话生成时作为「上次聊到」注入。wipe() 即清空，验收「清空后记忆清零」。
        if (persona != null && candidates.isNotEmpty()) {
            val contact = current.sender ?: current.chat ?: ""
            if (contact.isNotBlank()) persona.addHistory(contact, candidates.first().text)
        }
        val finalCandidates = if (multiTone) applyMultiToneLabels(candidates) else candidates
        return PipelineOutcome(decision, candidates = finalCandidates)
    }

    /** 提示类出口：按 C6 节流，节流命中时只记 suppressed、不发重复提示。 */
    private fun throttledNotice(
        current: Notification,
        decision: Decision,
        now: Long?,
        key: String,
        notice: String?,
    ): PipelineOutcome {
        val stamp = now ?: current.timestamp
        if (!throttle.allow(key, stamp)) {
            return PipelineOutcome(decision, suppressed = true)
        }
        return PipelineOutcome(decision, notice = notice)
    }

    /**
     * 生成 → 逐行拆候选 → 清洗/护栏/禁语/去重（handle 与 handleOpening 共用）。
     * FR-7.1：已给候选先入去重集（提示词排除是软约束，输出侧才是硬保证）。
     * T02224：先跨 choice 汇总候选行再按数量契约配对理由行——兼容服务不支持 n>1 时
     * 模型会把「候选+理由」挤进同一段，也可能一段只出一条，两种形态都要能配对。
     */
    private fun collectCandidates(
        prompt: PromptBundle,
        banned: List<String>,
        style: String,
        exclude: List<String>,
        expected: Int,
    ): List<Candidate> {
        val seen = exclude.toMutableList()
        val lines = generator(prompt).flatMap { expandCandidates(it) }
        return pairUnmarkedReasons(lines, expected).mapNotNull { line ->
            val (text0, rationale) = splitRationale(line)
            // T01159：表情收敛（开启时防堆砌，关闭时剥干净）
            val text = if (emoji) normalizeEmoji(text0) else stripEmoji(text0)
            when {
                // 元话语候选（LLM 请求澄清/自我指称）与输入内容无关，直接丢弃
                text.isEmpty() || isMetaCandidate(text) || screenCandidate(text).risky -> null
                // FR-5.1：候选含禁语 → 输出侧拦下
                persona != null && persona.violatesBanned(text, banned) -> null
                // FR-7.1：与已给候选（含本批内前几条）重复 → 丢弃
                seen.isNotEmpty() && isDuplicate(text, seen) -> null
                else -> {
                    seen.add(text)
                    Candidate(text, style, rationale)
                }
            }
        // T02224：面板按 N 张卡片设计，超发一律截断——多出来的从来不是「更多选择」，
        // 而是漏网的理由行（真机实测「候选 6 条」= 3 候选 + 3 理由）
        }.let { if (expected > 0) it.take(expected) else it }
    }

    /** T01349：多风格并行——候选数与档位数严格一致时按序补语气标签（宁缺勿错）。 */
    private fun applyMultiToneLabels(candidates: List<Candidate>): List<Candidate> {
        if (candidates.size != MULTI_TONE_VARIANTS.size) return candidates
        return candidates.mapIndexed { i, c ->
            val label = MULTI_TONE_LABELS.getOrElse(i) { "档${i + 1}" }
            c.copy(rationale = if (c.rationale.isNullOrBlank()) "${label}档" else "${label}档：${c.rationale}")
        }
    }

    /**
     * T01246 FR-8.4：无对话上下文时的场景化开场建议。
     * 无 current 消息，只按场景生成开场；输出经 sanitize/护栏/去重，与普通候选同构。
     */
    fun handleOpening(scenario: String, exclude: List<String> = emptyList()): PipelineOutcome {
        val prompt = buildOpeningPrompt(scenario, style = style, maxTokens = maxTokens)
        val banned = persona?.promptContext("")?.banned ?: emptyList()
        val candidates = collectCandidates(prompt, banned, "亲切", exclude,
            expected = OPENING_CANDIDATES) // FR-8.4 开场提示词固定要 3 条
        return PipelineOutcome(
            // FR-8.4 开场无真实 incoming 通知——用合成空通知承载 Decision（reason 已标明来源）
            Decision(Action.GENERATE, "开场建议（FR-8.4）",
                Notification(source = "opening", timestamp = System.currentTimeMillis())),
            candidates = candidates,
        )
    }

    /** 批量入口：按时间排序去重后，逐条以其同会话历史为上下文处理。 */
    fun run(notifications: List<Notification>): List<PipelineOutcome> {
        val byChat = mutableMapOf<String, MutableList<Notification>>()
        val outcomes = mutableListOf<PipelineOutcome>()
        for (n in dedupe(notifications.sortedBy { it.timestamp })) {
            val key = "${n.source}:${n.chat}"
            val history = byChat.getOrPut(key) { mutableListOf() }
            outcomes.add(handle(n, history.toList()))
            history.add(n)
        }
        return outcomes
    }
}