package com.emoreply.poc.core

import com.emoreply.poc.model.Notification
import kotlin.math.ceil

const val DEFAULT_MAX_TOKENS = 4096

// T02224：候选条数是请求时就定死的「数量契约」——生成器 n、提示词措辞与输出侧
// 理由配对（Sanitize.pairUnmarkedReasons）三处必须同源，否则配对会算错。
const val DEFAULT_CANDIDATES = 2
const val OPENING_CANDIDATES = 3

private val CJK = Regex("[\\u3400-\\u9fff\\uf900-\\ufaff]")

private const val SYSTEM_TEMPLATE =
    "你是聊天回复助手，为「%s」起草一条可直接发送的回复。\n" +
        "约束：\n" +
        "1) 只依据给定上下文，不得编造对方未提及的事实；\n" +
        "2) 语气%s，不冒犯、不敷衍、不过度承诺；\n" +
        "3) 直接输出正文，不要使用 markdown 格式符；\n" +
        "4) 即使上下文不完整或混乱，也基于已有信息给出最合适的回复——" +
        "严禁输出「请提供更多信息/聊天记录」之类请求澄清的元话语。"

// ===== T01159：候选表情（默认开启，与 m1-poc compose.EMOJI_HINT 同构）=====
// 只给「1-2 个、贴合语气、不堆砌」的软约束——硬编码插表情会破坏语义（C10），
// 交给模型按语气使用；输出侧由 Sanitize.normalizeEmoji 兜底防堆砌。
const val EMOJI_HINT = (
    "\n5) 正文中可自然使用 1-2 个 emoji 表情（如 😊👍🙏），贴合当前语气与情绪；" +
        "不得堆砌连续表情，也不得用表情替代必要文字。"
    )

// ===== T01158 FR-7.3：语气强度（连续滑杆：理性 ↔ 活泼，与 m1-poc compose 同构）=====
// 4 档风格（FR-3.2）之上叠加连续强度：风格定「基本人设」，强度定「表达热度」。
const val TONE_MIN = 0
const val TONE_MAX = 100
const val TONE_DEFAULT = 50

private val TONE_BANDS = listOf(
    20 to "语气极克制、偏理性：措辞精简，避免感叹号与情绪化表达",
    40 to "语气偏理性：稳重得体，少用夸张表达",
    60 to "语气中性自然：亲切但不过火",
    80 to "语气偏活泼：可用轻松措辞与适度感叹",
    101 to "语气活泼热情：可适度使用感叹与轻松表达，但仍不得过度承诺",
)

/** 语气强度 → 提示词描述句（越界收敛到 [0,100]）。 */
fun toneHint(tone: Int): String {
    val t = tone.coerceIn(TONE_MIN, TONE_MAX)
    return TONE_BANDS.first { t < it.first }.second
}

// ===== T01349：多风格并行候选（单请求多语气，不增加 RTT；与 m1-poc compose 同构）=====
// 三档变体取 tone 五档带中三个互异描述区（35/58/80 分别命中「偏理性/中性自然/偏活泼」），
// 约束模型单请求内跨语气分布，RTT 不变——IME 场景对延迟极敏感，禁止并发多请求。
val MULTI_TONE_VARIANTS = listOf(35, 58, 80)
val MULTI_TONE_LABELS = listOf("克制", "中性", "活泼")

/** T01349：多风格并行指令：逐档列出语气描述，要求按序各出一条（顺序即档位顺序）。 */
fun toneVariantsHint(tones: List<Int>): String {
    val parts = tones.mapIndexed { i, t ->
        val label = MULTI_TONE_LABELS.getOrElse(i) { "档${i + 1}" }
        "${i + 1}) ${label}档（${toneHint(t)}）"
    }
    return "\n语气强度（多风格并行）：本次必须按以下语气从上到下各生成一条候选，" +
        "顺序与上列一致，不得打乱，不得合并成同一语气：\n" + parts.joinToString("\n")
}

// FR-7.1 换一批：注入的排除项上限（过多会挤占 token 且稀释约束）
const val EXCLUDE_LIMIT = 8
private const val EXCLUDE_SNIPPET = 60

/** FR-7.1：已给候选的排除提示（换一批时不得重复）。 */
fun buildExcludeHint(exclude: List<String>): String {
    val items = exclude.map { it.trim() }.filter { it.isNotEmpty() }
    if (items.isEmpty()) return ""
    val lines = items.take(EXCLUDE_LIMIT)
        .mapIndexed { i, e -> "${i + 1}) ${e.take(EXCLUDE_SNIPPET)}" }
        .joinToString("；")
    return "\n换一批：以下候选已给出过，必须换角度或换措辞，不得重复：$lines。"
}

// ===== T01244 FR-8.2：三维筛选（关系×场景×目的）约束注入（与 m1-poc compose 同构）=====
// 三维都是生成前约束，非空才注入；relation 交由调用方按 intent/emotion 分支去重。
private val SCENARIO_HINTS = mapOf(
    "闲聊" to "对方在闲聊，回话要轻松自然，不必刻意推进",
    "工作" to "工作语境，回话要专业得体、就事论事",
)
private val PURPOSE_HINTS = mapOf(
    "安抚" to "目的是安抚对方情绪，先共情再给建议",
    "拒绝" to "目的是礼貌表达拒绝或边界，语气温和但立场清晰",
    "推进" to "目的是推进事情落地，回话要给出明确下一步",
    "闲聊" to "目的是延续对话，不必追求结果",
)

/** FR-8.2：三维筛选注入提示词（全空返回空串）。 */
fun buildFilterHint(relation: String = "", scenario: String = "", purpose: String = ""): String {
    val parts = buildList {
        if (relation.isNotEmpty()) add("对方关系：$relation（语气贴合关系亲疏，不越界）")
        if (scenario.isNotEmpty()) add("场景：${SCENARIO_HINTS[scenario] ?: scenario}")
        if (purpose.isNotEmpty()) add("目的：${PURPOSE_HINTS[purpose] ?: purpose}")
    }
    if (parts.isEmpty()) return ""
    return "\n4) " + parts.joinToString("；") + "。"
}

// ===== T01246 FR-8.4：场景化开场建议（无对话上下文时选场景生成开场话术）=====
val OPENING_SCENARIOS = listOf("破冰", "重连", "节日祝福")
private val OPENING_HINTS = mapOf(
    "破冰" to "对方是初次或久未交流，回话要自然不刻意、降低社交压力，给出轻松的话题切入点",
    "重连" to "与久未联系的人重建联系，回话要真诚承接过去、不留尴尬，自然推进",
    "节日祝福" to "节日祝福语境，回话要应景得体、不套路化，可带适度温度",
)

/** FR-8.4：场景化开场提示词束（无 current/history，场景约束入 system，期望 3 条候选）。 */
fun buildOpeningPrompt(scenario: String, style: String = "亲切", maxTokens: Int = DEFAULT_MAX_TOKENS): PromptBundle {
    val sc = if (OPENING_HINTS.containsKey(scenario)) scenario else "破冰"
    val hint = OPENING_HINTS[sc] ?: OPENING_HINTS.getValue("破冰")
    val system = (
        "你是聊天回复助手，为「我」起草一条可直接发送的**开场**话术（对方主动场合），约束：\n" +
            "1) 场景：$hint；\n" +
            "2) 语气$style，不冒犯、不敷衍、不过度承诺；\n" +
            "3) 直接输出正文，不要使用 markdown 格式符；\n" +
            "4) 给出 3 条风格各异、互不重复的开场候选，每条按「内容|理由」输出，" +
            "理由用一句话说明情商点，可以省略。"
        )
    val budget = maxTokens - estimateTokens(system) - 512
    return PromptBundle(
        system = system,
        history = emptyList(),
        current = "",
        style = style,
        estimatedTokens = estimateTokens(system),
        droppedHistory = 0,
        truncated = budget < 0,
    )
}

/**
 * 粗略估算 token 数：CJK 按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。
 *
 * 刻意不引入 tokenizer 依赖——移动端要的是「不超窗」的安全边界，不是精确计费。
 */
fun estimateTokens(text: String?): Int {
    if (text.isNullOrEmpty()) return 0
    val cjk = CJK.findAll(text).count()
    return cjk + ceil((text.length - cjk) / 4.0).toInt()
}

data class PromptBundle(
    val system: String,
    val history: List<String> = emptyList(),
    val current: String = "",
    val style: String = "亲切",
    val estimatedTokens: Int = 0,
    val droppedHistory: Int = 0,
    val truncated: Boolean = false,
)

private fun render(n: Notification): String = "${n.sender ?: "对方"}：${n.body ?: ""}"

/**
 * 组装提示词，并把伪历史压到 token 预算内。
 *
 * history 需按时间升序且不含 current；超预算时从**最旧**开始丢弃——
 * 越近的对话对语气判断越关键，system 与当前消息则永不丢弃。
 */
/** REQ-006（FR-2.1/2.2 → FR-3.1）：分类信息非空时并入提示词增强；缺省不注入，保持 M1 行为。 */
private fun intentSection(
    intent: String,
    emotion: String,
    urgency: String,
    relation: String,
    knowledge: List<String>,
): String {
    if (intent.isEmpty()) return ""
    var extra = "\n4) 消息类型：$intent。"
    if (emotion.isNotEmpty()) {
        extra += "对方情绪：$emotion"
        if (urgency.isNotEmpty()) extra += "（紧急度：$urgency）"
        extra += "。回应需贴合情绪与紧急度。"
    }
    if (relation.isNotEmpty()) extra += "对方关系：$relation（语气贴合关系亲疏，不越界）。"
    // RAG（决策③）：知识条目注入，声明不得据此编造；无命中时不注入
    if (knowledge.isNotEmpty()) {
        extra += "参考资料（已按相关性筛选；仅在与当前消息相关时参考，" +
            "不得据此编造对方未提及的事实）：${knowledge.joinToString("；")}。"
    }
    return extra
}

/** 伪历史压预算：超预算从最旧开始丢弃，返回 (保留行, 丢弃条数, 已用 token)。 */
private fun historyWindow(
    history: List<Notification>,
    historyLimit: Int,
    budget: Int,
): Triple<List<String>, Int, Int> {
    val lines = history.filter { !it.body.isNullOrBlank() }
        .map { render(it) }
        .takeLast(historyLimit)
    val kept = ArrayDeque<String>()
    var used = 0
    for (line in lines.asReversed()) {
        val cost = estimateTokens(line)
        if (used + cost > budget) break
        kept.addFirst(line)
        used += cost
    }
    return Triple(kept.toList(), lines.size - kept.size, used)
}

/**
 * 一次生成请求的全部可选上下文（默认值即 M1 行为）。
 *
 * S107 收敛：这些字段原本摊在 [buildPrompt] 的 19 个形参上，聚合成对象只为
 * 收拢形参个数；字段名、默认值、注入顺序一字未改——与 m1-poc compose.PromptContext
 * 逐字段同名（lastTopic/favorites 为安卓端 FR-8.5/8.8 增量，py 侧无对应请求路径）。
 */
data class PromptContext(
    val me: String = "我",
    val style: String = "亲切",
    val maxTokens: Int = DEFAULT_MAX_TOKENS,
    val historyLimit: Int = 5,
    val intent: String = "",
    val emotion: String = "",
    val urgency: String = "",
    val relation: String = "",
    val scenario: String = "", // T01244 FR-8.2：三维筛选（场景）
    val purpose: String = "",  // T01244 FR-8.2：三维筛选（目的）
    val knowledge: List<String> = emptyList(),
    val emoji: Boolean = true,
    val tone: Int? = null, // T01158 FR-7.3：null=沿用旧行为（仅风格生效）
    val toneVariants: List<Int>? = null, // T01349：多风格并行（非空优先于单值 tone）
    val exclude: List<String> = emptyList(), // T01158 FR-7.1：换一批排除项
    val lastTopic: String = "", // T01259 FR-8.5：上次聊到（跨会话记忆，仅话题参考）
    val favorites: List<String> = emptyList(), // T01262 FR-8.8：收藏话术（同类场景优先参考）
)

fun buildPrompt(
    current: Notification,
    history: List<Notification>,
    ctx: PromptContext = PromptContext(),
): PromptBundle {
    var system = String.format(SYSTEM_TEMPLATE, ctx.me, ctx.style)
    system += intentSection(ctx.intent, ctx.emotion, ctx.urgency, ctx.relation, ctx.knowledge)
    // T01244 FR-8.2：三维筛选独立注入。relation 已由 intent 分支注入（第 139 行，
    // 只要 intent 非空即注入），此处跳过避免重复；场景/目的恒由这里补。
    val relationHint = if (ctx.intent.isEmpty()) ctx.relation else ""
    val filterHint = buildFilterHint(relationHint, ctx.scenario, ctx.purpose)
    if (filterHint.isNotEmpty()) system += filterHint
    // T01259 FR-8.5：上次聊到——只注入真实历史摘要，且明确「不得据此编造」，防止跨会话记忆被幻觉放大
    if (ctx.lastTopic.isNotBlank()) {
        system += "\n4) 上次聊到：${ctx.lastTopic}（作为话题衔接参考，不得据此编造对方未提及的事实）。"
    }
    // T01262 FR-8.8：收藏话术（同类场景优先）——仅作语气/角度参考，禁止原样复制
    if (ctx.favorites.isNotEmpty()) {
        system += "\n4) 收藏话术（同类场景优先参考其语气与角度，禁止原样复制）：${ctx.favorites.joinToString("；")}。"
    }
    // T01159：表情约束（emoji=false 时不注入，输出侧再剥掉，保证「关=无表情」）
    if (ctx.emoji) system += EMOJI_HINT
    // T01158 FR-7.3：语气强度
    // T01349：toneVariants 非空时优先（多风格并行一次出多档），单值 tone 忽略
    if (!ctx.toneVariants.isNullOrEmpty()) {
        system += toneVariantsHint(ctx.toneVariants)
    } else if (ctx.tone != null) {
        system += "\n语气强度：${toneHint(ctx.tone)}"
    }
    // T01158 FR-7.1：换一批排除项
    val excludeHint = buildExcludeHint(ctx.exclude)
    if (excludeHint.isNotEmpty()) system += excludeHint
    val currentLine = render(current)
    // 预留回复本身的空间，避免「输入刚好压线、输出直接撞顶」
    val budget = ctx.maxTokens - estimateTokens(system) - estimateTokens(currentLine) - 512

    val (kept, dropped, used) = historyWindow(history, ctx.historyLimit, budget)
    return PromptBundle(
        system = system,
        history = kept,
        current = currentLine,
        style = ctx.style,
        estimatedTokens = estimateTokens(system) + used + estimateTokens(currentLine),
        droppedHistory = dropped,
        truncated = dropped > 0,
    )
}
// ===== T01130：输入框文字美化（与 m1-poc compose.BEAUTIFY_SYSTEM 同构）=====
const val BEAUTIFY_SYSTEM = (
    "你是文字润色助手。请对用户给出的文本做美化，严格遵守：\n"
    + "1) 保持核心语义不变，不得改变文本语言（中文美化中文，英文美化英文）；\n"
    + "2) 只优化句式结构与措辞，使表达更自然流畅、更得体；\n"
    + "3) 不添加原文未包含的信息，不删减原文关键信息；\n"
    + "4) 保留原有语气倾向（正式/随意、热情/冷静）；\n"
    + "5) 若原文已足够清晰流畅，原样输出即可，不做无意义改动；\n"
    + "6) 直接输出美化后的文本，不要任何解释或前后缀，不要使用 markdown 格式符。"
)

/** 构造输入框文字美化的提示词束（current=原文，无对话上下文）。 */
fun buildBeautifyPrompt(text: String, maxTokens: Int = DEFAULT_MAX_TOKENS): PromptBundle {
    val budget = maxTokens - estimateTokens(BEAUTIFY_SYSTEM) - estimateTokens(text) - 512
    return PromptBundle(
        system = BEAUTIFY_SYSTEM,
        history = emptyList(),
        current = text,
        style = "自然",
        estimatedTokens = estimateTokens(BEAUTIFY_SYSTEM) + estimateTokens(text),
        droppedHistory = 0,
        truncated = budget < 0,
    )
}

// ===== T01261 FR-8.7：快捷动作面板——文本动作族（美化扩展）=====
// 长按「美化」图标弹出动作菜单，动作作用于输入框文本；每种动作映射独立 system prompt，
// 改写（REWRITE）复用 BEAUTIFY_SYSTEM（原美化语义，BEAUTIFY_SYSTEM 保留不动）。
enum class TextAction(val label: String) {
    REWRITE("改写"), SUMMARY("总结"), TRANSLATE("翻译"), EXPAND("扩写"),
}

private val SUMMARY_SYSTEM = (
    "你是文本精炼助手。请把用户给出的文本总结为要点，严格遵守：\n"
    + "1) 提取核心要点，删去冗余铺垫，使表达更精炼；\n"
    + "2) 保留原文关键信息与事实，不添加原文未包含的内容；\n"
    + "3) 保持原有语言（中文总结中文，英文总结英文），保留原有语气倾向；\n"
    + "4) 若原文已足够精炼，原样输出即可；\n"
    + "5) 直接输出总结后的文本，不要任何解释或前后缀，不要使用 markdown 格式符。"
)

private val TRANSLATE_SYSTEM = (
    "你是翻译助手。请把用户给出的文本翻译成当前使用语言（中文语境默认译为中文），严格遵守：\n"
    + "1) 不转换文体：保持原文的正式/随意风格与语气，不做改写润色；\n"
    + "2) 保持核心语义不变，不添加、不删减原文关键信息；\n"
    + "3) 若原文已是目标语言，原样输出即可；\n"
    + "4) 直接输出译文，不要任何解释或前后缀，不要使用 markdown 格式符。"
)

private val EXPAND_SYSTEM = (
    "你是文字扩写助手。请把用户给出的文本扩写得更完整、更充实，严格遵守：\n"
    + "1) 围绕原文主旨展开，补充合理的背景与细节，使表达更完整流畅；\n"
    + "2) 不改变核心语义与立场，不偏离原文主旨；\n"
    + "3) 保持原有语言与语气倾向，不添加原文明确反对的信息；\n"
    + "4) 直接输出扩写后的文本，不要任何解释或前后缀，不要使用 markdown 格式符。"
)

/** T01261 FR-8.7：构造文本动作（改写/总结/翻译/扩写）的提示词束；改写复用 BEAUTIFY_SYSTEM。 */
fun buildTextActionPrompt(text: String, action: TextAction, maxTokens: Int = DEFAULT_MAX_TOKENS): PromptBundle {
    val system = when (action) {
        TextAction.REWRITE -> BEAUTIFY_SYSTEM
        TextAction.SUMMARY -> SUMMARY_SYSTEM
        TextAction.TRANSLATE -> TRANSLATE_SYSTEM
        TextAction.EXPAND -> EXPAND_SYSTEM
    }
    val budget = maxTokens - estimateTokens(system) - estimateTokens(text) - 512
    return PromptBundle(
        system = system,
        history = emptyList(),
        current = text,
        style = "自然",
        estimatedTokens = estimateTokens(system) + estimateTokens(text),
        droppedHistory = 0,
        truncated = budget < 0,
    )
}
