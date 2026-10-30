package com.emoreply.poc.core

/**
 * 候选回复的输出清洗。
 *
 * SRS 附录 C 的两条实测约束：
 * - C3：LLM 输出的 markdown 格式符（**、*、链接）直接注入聊天框会变成噪声正文；
 * - C25：注入文本须剥离 HTML/标记，防标记污染。
 *
 * 清洗只剥离格式符号、不改写文字——改写会触发 C10 的语义丢失风险。
 */
private val MD_LINK = Regex("""\[([^\]]*)\]\([^)]*\)""")
private val EMPHASIS = Regex("""(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1""", RegexOption.DOT_MATCHES_ALL)
private val CODE_FENCE = Regex("""```[a-zA-Z0-9]*\n?""")
private val INLINE_CODE = Regex("""`([^`]*)`""")
private val HEADING = Regex("""^[ \t]{0,3}#{1,6}[ \t]*""", RegexOption.MULTILINE)
private val LIST_MARK = Regex("""^[ \t]{0,3}(?:[-*+]|\d+\.)[ \t]+""", RegexOption.MULTILINE)
private val HTML_TAG = Regex("""</?[a-zA-Z][^>]*>""")
private val STRAY_MARK = Regex("""(?<!\w)[*_~]{1,3}(?!\w)""")
private val BLANK_RUN = Regex("""\n{3,}""")
// T01131：尾部悬空管道符（LLM「内容|」理由缺失变体的残留），连同空白一并剥掉
private val TRAIL_PIPE = Regex("""[\s|]+\z""", RegexOption.DOT_MATCHES_ALL)

/** 剥离 markdown 格式符，保留文字内容（C3）。 */
fun stripMarkdown(text: String): String {
    var out = CODE_FENCE.replace(text, "")
    out = MD_LINK.replace(out) { it.groupValues[1] }
    out = INLINE_CODE.replace(out) { it.groupValues[1] }
    out = EMPHASIS.replace(out) { it.groupValues[2] }
    out = HEADING.replace(out, "")
    out = LIST_MARK.replace(out, "")
    out = STRAY_MARK.replace(out, "")
    return out
}

/** 剥离 HTML/标记（C25）。 */
fun stripMarkup(text: String): String = HTML_TAG.replace(text, "")

/** 注入输入框前的最终清洗。 */
fun cleanCandidate(text: String): String {
    var out = stripMarkup(stripMarkdown(text))
    out = BLANK_RUN.replace(out, "\n\n")
    out = TRAIL_PIPE.replace(out, "")
    return out.trim()
}

/**
 * FR-3.3：解析 LLM 候选行的「内容|理由」格式，理由缺失返回 null。
 * 与 m1-poc `sanitize.split_rationale` 同构：按最后一个 | 切分，
 * 内容本身含 | 时不受影响；清洗只作用于内容，理由仅作展示。
 *
 * T01554 增强：二次校验确保 rationale 不会混入 text——若 text 很短而
 * rationale 很长且含中文，疑似分隔符位置错误则交换两者；无 | 时尝试
 * 启发式检测「理由/rationale/explanation」关键词进行拆分。
 *
 * S3776 收敛：三条判据路径各抽为私有函数，返回 null 表示本路径不产出结果、
 * 落入下一条路径——与原 if 链的落空/提前 return 语义逐分支一致。
 */
fun splitRationale(raw: String?): Pair<String, String?> {
    val s = raw?.trim() ?: return "" to null
    if (s.isEmpty()) return "" to null
    splitOnLastPipe(s)?.let { return it }
    // T01126：无 | 时兜底「正文 理由：xxx」内联结构（LLM 忘带管道符的常见变体）
    inlineRationalePair(s)?.let { return it }
    keywordRationalePair(s)?.let { return it }
    return cleanCandidate(s) to null
}

/** 按最后一个 | 切分（原 if ("|" in s) 分支）；无 | 或 text 为空时返回 null 落入后续路径。 */
private fun splitOnLastPipe(s: String): Pair<String, String?>? {
    if ("|" !in s) return null
    val idx = s.lastIndexOf('|')
    val text = s.substring(0, idx).trim()
    val rationale = s.substring(idx + 1).trim()
    if (text.isNotEmpty() && rationale.isNotEmpty()) {
        // T01554：二次校验——若 rationale 看起来像候选文本（含汉字/完整句子），
        // 而 text 很短（≤3 字符），可能是分隔符位置错误，交换两者
        if (text.length <= 3 && rationale.length > 10 && rationale.any { it in '一'..'鿿' }) {
            // 疑似错位：短文本在前、长中文在后 → 交换
            return stripOptionPrefix(cleanCandidate(rationale)) to text
        }
        // 先剥 markdown 再剥标签前缀：前缀正则含 bullet [-*+•]，对未清洗文本会误剥 markdown 星号
        return stripOptionPrefix(cleanCandidate(text)) to rationale
    }
    if (text.isNotEmpty()) {
        // T01131：理由缺失的悬空管道符（「文本|」）——剥掉 | 只保留正文，
        // 不得把整段含 | 原文落入兜底当候选（真机实测残留「…旅行时光。|」）
        return stripOptionPrefix(cleanCandidate(text)) to null
    }
    return null
}

/** 「正文 理由：xxx」内联结构（T01126，原 INLINE_RATIONALE 分支）；不匹配或任一半空返回 null。 */
private fun inlineRationalePair(s: String): Pair<String, String?>? {
    val m = INLINE_RATIONALE.matchEntire(s) ?: return null
    val text = m.groupValues[1].trim()
    val rationale = m.groupValues[2].trim()
    if (text.isNotEmpty() && rationale.isNotEmpty()) {
        return stripOptionPrefix(cleanCandidate(text)) to rationale
    }
    return null
}

/**
 * 关键词启发式拆分（T01554，原 for 循环）；全部关键词不满足长度判据时返回 null。
 *
 * T01554：启发式检测——text 中是否混入了 rationale 内容。
 * 相对初版做了两点收紧（由 m1-poc 同构镜像时实测发现的误伤）：
 * 初版的「说明」弱词 + 不要求关键词后紧跟冒号，会把「好的，我说明一下情况」
 * 误砍成候选「好的，我」+理由「说明一下情况」——把完整句子拦腰截断，
 * 比「理由混入候选」这个原 bug 更糟。故：
 *   1) 只保留强标签词，去掉口语动词「说明」；
 *   2) 关键词后必须紧跟冒号（：/:），排除「说明一下」「说明书」等句中用法。
 * 注：中文「理由：」多数已被 INLINE_RATIONALE（T01126）先行命中，此处的实际
 * 增量是英文关键词（rationale/explanation）。返回值剥掉标签词前缀，
 * 与 INLINE_RATIONALE 的 group(2) 口径保持一致。
 */
private fun keywordRationalePair(s: String): Pair<String, String?>? {
    val rationaleKeywords = listOf("理由", "rationale", "explanation", "原因", "解释")
    for (keyword in rationaleKeywords) {
        val kwPattern = Regex(Regex.escape(keyword) + "\\s*[:：]", RegexOption.IGNORE_CASE)
        val m = kwPattern.find(s) ?: continue
        val kwIdx = m.range.first
        if (kwIdx <= 0) continue
        val tail = s.substring(kwIdx)
        val potentialText = s.substring(0, kwIdx).trimEnd(' ', '：', ':', '。', '.', '，', ',')
        // 长度判定用「含标签的原文」（判断后面是否真有一段解释）
        if (potentialText.isNotEmpty() && potentialText.length <= 20 && tail.length > 5) {
            val potentialRationale = s.substring(m.range.last + 1).trimStart(' ', '：', ':')
            return stripOptionPrefix(cleanCandidate(potentialText)) to potentialRationale
        }
    }
    return null
}

// T01081：兼容服务不支持 n>1 时，LLM 常把多条候选挤在同一段输出——
// 不拆开的话，用户点任一候选都会把全部选项连同解释一起注入聊天框（发送事故）。
private val NUMBERED = Regex("""(?:^|[\s　])(?:选项|候选|方案)\s*\d+\s*(?:[（(][^）)]*[）)])?\s*[：:.．]""")
private val LINE_PREFIX = Regex(
    """^[ \t　]*(?:(?:选项|候选|方案)\s*\d+\s*(?:[（(][^）)]*[）)])?\s*[：:.．]|[-*+•]|\d+[.、)]|[（(]\d+[)）])\s*""",
)

// T01126：LLM 把结构化标题混进候选（「## 候选回复」「理由：xxx」）——
// 标题行整行丢弃、标签前缀剥离，防止点选把标题带进输入框（快速点选场景）。
// T02224：「内容」也是标签词——提示词契约就叫「内容|理由」，模型会把字段名当行首标签吐回来
// （真机实测第 2 条候选是「内容：对方回复较为简短，直接点出未收到消息的事实…」），
// 不剥的话字段名跟着注入聊天框。长词在前保证「回复内容」优先于「内容」整体匹配。
private const val LABEL_WORDS = "(?:候选回复|参考回复|回复内容|内容|回复|候选|理由|原因|解释)"
private val LABEL_LINE = Regex("""^[ \t#*_>]*$LABEL_WORDS\s*[:：]?\s*[#*_\s]*$""")
private val LABEL_PREFIX = Regex("""^[ \t#*_]*$LABEL_WORDS\s*[:：]\s*""")
private val INLINE_RATIONALE = Regex("""^(.+?)\s*(?:理由|原因|解释)\s*[:：]\s*(.+)$""", RegexOption.DOT_MATCHES_ALL)

private fun stripOptionPrefix(line: String): String {
    var out = LABEL_PREFIX.replaceFirst(line, "")
    return LINE_PREFIX.replaceFirst(out, "")
}

/** 整行只是结构标签（「## 候选回复」「理由：」）→ 丢弃该行（T01126）。 */
private fun isLabelLine(line: String): Boolean = LABEL_LINE.matches(line.trim())

// 悬空管道符：行尾的 |（「内容|」）或行首的 |（「|理由」）。
// 全/半角空格一并容忍——LLM 常在管道符后补一个空格。
private val DANGLING_PIPE_TAIL = Regex("""\|[ \t\u3000]*$""")
private val LEADING_PIPE = Regex("""^[ \t\u3000]*\|""")

// T01720：LLM 把理由换行输出（「1. 候选正文」+ 换行「理由：xxx」续行）——
// 逐行拆会把续行剥掉「理由：」前缀后当独立候选（真机实测「候选 6 条」= 3 真候选 + 3 理由行）。
// 强特征 = 行首（允许 markdown/缩进/bullet 修饰）强标签词 + 冒号，与 T01554 启发式同词表；
// 常规正文不以此开头（「原因很简单：…」中 原因 后不是冒号，不命中）。
private val RATIONALE_LINE = Regex(
    """^[ \t#*_>]*(?:[-*+•][ \t\u3000]?)?(?:理由|原因|解释|rationale|explanation)\s*[:：]\s*(.+)$""",
    RegexOption.IGNORE_CASE,
)

/**
 * T01720：把「理由：xxx」续行接回上一条候选行，理由行自身不产出候选。
 * 常规补 | 交给 [splitRationale] 统一解析；上一行以悬空管道符收尾直接续接；
 * 上一行已含 |（已接过度理由）用「；」续接进理由段；无上一行则丢弃。
 * 与 m1-poc `sanitize._merge_rationale_lines` / desktop `mergeRationaleLines` 同构。
 */
private fun mergeRationaleLines(lines: List<String>): List<String> {
    val out = mutableListOf<String>()
    lines.forEach { ln ->
        val m = RATIONALE_LINE.find(ln)
        if (m != null) {
            val rationale = m.groupValues[1].trim()
            val prev = out.lastOrNull()
            if (prev != null && rationale.isNotEmpty()) {
                out[out.lastIndex] = when {
                    DANGLING_PIPE_TAIL.containsMatchIn(prev) -> prev + rationale
                    prev.contains('|') -> "$prev；$rationale"
                    else -> "$prev|$rationale"
                }
            }
        } else {
            out.add(ln)
        }
    }
    return out
}

/**
 * 把被换行拆开的「内容|理由」重新接回一行。
 *
 * LLM 会把理由换到下一行（「内容|」+换行+「理由」，或「内容」+换行+「|理由」），
 * 逐行拆会产出两条独立候选：前一条丢理由、后一条把理由当正文（真机实测候选 3
 * 无理由、候选 4 恰是候选 3 的理由），违反 FR-3.3「≥90% 候选附解释」。
 * 管道符是「这两行本属同一条」的显式标记，据此合并；无此标记时不做任何合并，
 * 以免误并两条真实候选。
 */
private fun joinDanglingPipe(lines: List<String>): List<String> {
    val out = mutableListOf<String>()
    lines.forEach { ln ->
        val prev = out.lastOrNull()
        if (prev != null && (DANGLING_PIPE_TAIL.containsMatchIn(prev) || LEADING_PIPE.containsMatchIn(ln))) {
            out[out.lastIndex] = prev + ln
        } else {
            out.add(ln)
        }
    }
    return out
}

/**
 * 把一条 LLM 输出拆成单候选行列表（只拆结构，不改写文字）。
 * - 多行输出：逐行拆（LLM 逐行列候选的常见格式）；
 * - 单行但含内联编号（「选项1：… 选项2：…」）：按编号拆；
 * - 其余：原样单条返回，交由 [splitRationale] 继续处理。
 * 与 m1-poc `sanitize.expand_candidates` 同构。
 */
fun expandCandidates(raw: String?): List<String> {
    val s = raw?.trim() ?: return emptyList()
    if (s.isEmpty()) return emptyList()
    // T01126：标题行（「## 候选回复」「理由：」）整行丢弃，不作为候选
    val lines = s.lines().map { it.trim() }.filter { it.isNotEmpty() && !isLabelLine(it) }
    val merged = mergeRationaleLines(lines) // T01720：理由续行接回上一条候选，不产出独立候选
    if (merged.size > 1) return joinDanglingPipe(merged).map { stripOptionPrefix(it) }
    if (NUMBERED.containsMatchIn(s)) {
        val parts = NUMBERED.split(s).map { it.trim() }.filter { it.isNotEmpty() }
        if (parts.size > 1) return parts.map { stripOptionPrefix(it) }
    }
    if (merged.size == 1) return listOf(stripOptionPrefix(merged[0]))
    // T01126：全部行都是结构标签（LLM 只输出标题没正文）→ 无候选
    return emptyList()
}

/** 行内是否已带理由结构标记：管道分隔、「… 理由：…」内联、「理由：」标签行。 */
private fun hasRationaleMark(line: String): Boolean =
    '|' in line || INLINE_RATIONALE.containsMatchIn(line) || RATIONALE_LINE.containsMatchIn(line)

/**
 * T02224 根治「理由栏被识别成候选回复」：用数量契约取代词表补丁。
 *
 * 真机现场（同一屏抓到的 LLM 原文）：模型把「内容|理由」的分隔符整个丢掉，候选与理由
 * 退化成无标记的交替纯文本行——4 行 = 2 候选 + 2 理由，界面出「候选 4 条」，
 * 用户点第 2 条会把「语气简洁明了，直接推进下一步」这类分析语注入微信输入框。
 * 历次修复（T01126/T01136/T01554/T01720）都在扩理由行的词表，但词表只能认出
 * **带标签**的理由行；无标签时它必然漏——这是补丁反复复发的根因。
 *
 * 这里改用一个模型破坏不了的约束：候选条数 N 在请求时就定死了（生成器 n / 提示词
 * 「两个候选」）。展开行数 > N 且全行无任何理由标记时，按鸽笼原理多出来的行不可能
 * 都是候选，只能是「正文, 理由」相邻成对——按序配对成「正文|理由」交回 [splitRationale]，
 * 再按 N 截断。条件不满足（N 未知、行数 ≤ N、行数为奇、已有标记）时原样返回，
 * 仍走词表路径：宁可不配，也不把两条真候选误吞成理由。
 */
fun pairUnmarkedReasons(lines: List<String>, expected: Int): List<String> {
    if (expected <= 0 || lines.size <= expected || lines.size % 2 != 0) return lines
    if (lines.any { hasRationaleMark(it) }) return lines
    return lines.chunked(2).take(expected).map { (text, reason) -> "$text|$reason" }
}
// ===== T01158 FR-7.1：换一批去重（与 m1-poc sanitize 同算法，结果一致）=====
private val PUNCT = Regex("""[\s　，。！？、；：,.!?;:~～\-—…"'()（）\[\]【】]""")

/** 比较用归一化：去表情、去标点空白、统一小写。 */
fun normalizeForCompare(text: String): String =
    PUNCT.replace(stripEmoji(text), "").lowercase()

/** LCS 比率相似度 = 2*LCS / (len(a)+len(b))（与 m1-poc sanitize.similarity 同算法）。 */
fun similarity(a: String, b: String): Float {
    val x = normalizeForCompare(a)
    val y = normalizeForCompare(b)
    if (x.isEmpty() && y.isEmpty()) return 1f
    if (x.isEmpty() || y.isEmpty()) return 0f
    var prev = IntArray(y.length + 1)
    for (ca in x) {
        val cur = IntArray(y.length + 1)
        for (j in 1..y.length) {
            cur[j] = if (ca == y[j - 1]) prev[j - 1] + 1 else maxOf(prev[j], cur[j - 1])
        }
        prev = cur
    }
    return 2f * prev[y.length] / (x.length + y.length)
}

/** 候选是否与已给候选重复（换一批过滤用，默认阈值与 py 侧一致）。 */
fun isDuplicate(text: String, exclude: List<String>, threshold: Float = 0.8f): Boolean {
    if (text.isEmpty()) return true
    return exclude.any { similarity(text, it) >= threshold }
}

/** 丢弃与 exclude 重复的候选；exclude 为空时原样返回。 */
fun <T> filterDuplicates(candidates: List<T>, exclude: List<String>, textOf: (T) -> String): List<T> {
    if (exclude.isEmpty()) return candidates
    return candidates.filter { !isDuplicate(textOf(it), exclude) }
}

// ===== T01159：表情（emoji）处理（与 m1-poc sanitize 同构）=====
// 表情是正文的一部分（不属 markdown/markup 噪声，C3/C25 清洗不得动它）；
// 但 LLM 常见「🎉🎉🎉」堆砌，注入聊天框会显得廉价——只做收敛，不改写文字。
private fun isEmojiBase(cp: Int): Boolean = when {
    cp in 0x1F000..0x1FAFF -> true // 表情主体（含补充符号与图形）
    cp in 0x1F1E6..0x1F1FF -> true // 区域指示符（国旗）
    cp in 0x2600..0x27BF -> true // 杂项符号与装饰符号
    cp in 0x2B00..0x2BFF -> true // 箭头与几何补充符号
    cp == 0x203C || cp == 0x2049 || cp == 0x2122 || cp == 0x2139 -> true
    cp in 0x2194..0x2199 || cp == 0x21A9 || cp == 0x21AA -> true
    cp == 0x231A || cp == 0x231B || cp == 0x2328 || cp == 0x23CF -> true
    cp in 0x23E9..0x23F3 || cp in 0x23F8..0x23FA -> true
    cp == 0x25AA || cp == 0x25AB || cp == 0x25B6 || cp == 0x25C0 -> true
    cp in 0x25FB..0x25FE -> true
    cp == 0x2934 || cp == 0x2935 || cp == 0x3030 || cp == 0x303D -> true
    cp == 0x3297 || cp == 0x3299 -> true
    else -> false
}

private fun isEmojiModifier(cp: Int): Boolean =
    cp == 0xFE0F || cp == 0x20E3 || cp in 0x1F3FB..0x1F3FF // 变体选择符/keycap/肤色

private fun isZwj(cp: Int): Boolean = cp == 0x200D

/**
 * 抽取表情单元（含修饰符与 ZWJ 组合序列，👨‍👩‍👧 计 1 个）。
 * 返回 [start, end) 字符索引区间——emoji 多为代理对，按 code point 遍历才不会切半。
 */
fun emojiUnits(text: String): List<IntRange> {
    val out = mutableListOf<IntRange>()
    var i = 0
    while (i < text.length) {
        val cp = text.codePointAt(i)
        if (!isEmojiBase(cp)) {
            i += Character.charCount(cp)
            continue
        }
        var j = i + Character.charCount(cp)
        while (j < text.length) {
            val c2 = text.codePointAt(j)
            val w2 = Character.charCount(c2)
            when {
                isEmojiModifier(c2) -> j += w2
                // ZWJ 后必须紧跟表情主体，否则（单独的 ZWJ）结束本单元
                isZwj(c2) && j + w2 < text.length && isEmojiBase(text.codePointAt(j + w2)) -> {
                    j += w2
                    val c3 = text.codePointAt(j)
                    j += Character.charCount(c3)
                    while (j < text.length && isEmojiModifier(text.codePointAt(j))) {
                        j += Character.charCount(text.codePointAt(j))
                    }
                }
                else -> break
            }
        }
        out.add(i until j)
        i = j
    }
    return out
}

/** 表情单元数（组合表情计 1 个）。 */
fun countEmojiUnits(text: String): Int = emojiUnits(text).size

private val SPACE_RUN = Regex("""[ \t\u3000]{2,}""")

/** 计算待丢弃的表情区间：重复（连同中间空白一起删，避免留下空洞）或超出配额的靠后表情。 */
private fun emojiDropRanges(text: String, units: List<IntRange>, maxUnits: Int): List<IntRange> {
    val drop = mutableListOf<IntRange>()
    var kept = 0
    var prevUnit: String? = null
    var prevEnd = -1
    for (r in units) {
        val seg = text.substring(r.first, r.last + 1)
        val between = if (prevEnd < 0) "" else text.substring(prevEnd, r.first)
        val repeat = prevUnit == seg && between.isBlank()
        if (repeat || kept >= maxUnits) {
            drop.add(if (repeat && prevEnd >= 0) prevEnd until (r.last + 1) else r)
        } else {
            kept++
            prevUnit = seg
        }
        prevEnd = r.last + 1
    }
    return drop
}

/**
 * 收敛表情：连续重复压成一个，总数超 maxUnits 时丢弃靠后的多余表情。
 * 只删表情本身，不动文字——与 [cleanCandidate] 同原则（不改写语义）。
 */
fun normalizeEmoji(text: String, maxUnits: Int = 2): String {
    if (text.isEmpty()) return text
    val units = emojiUnits(text)
    if (units.isEmpty()) return text
    val drop = emojiDropRanges(text, units, maxUnits)
    if (drop.isEmpty()) return text
    var out = text
    for (r in drop.sortedByDescending { it.first }) {
        out = out.substring(0, r.first) + out.substring(minOf(r.last + 1, out.length))
    }
    return SPACE_RUN.replace(out, " ").trim()
}

/** 去除全部表情（表情开关关闭时使用）。 */
fun stripEmoji(text: String): String {
    if (text.isEmpty()) return text
    val units = emojiUnits(text)
    if (units.isEmpty()) return text
    var out = text
    for (r in units.asReversed()) {
        out = out.substring(0, r.first) + out.substring(minOf(r.last + 1, out.length))
    }
    return SPACE_RUN.replace(out, " ").trim()
}

// ===== 元话语候选过滤（与 m1-poc sanitize.is_meta_candidate 同构）=====
private val META_REPLY = Regex(
    "(请[^，。；！?？]{0,15}(提供|补充|给出|发送给我|告诉我|描述|说明)" +
        "[^，。；！?？]{0,20}(聊天记录|上下文|最近(说)?的话|更多信息|详细内容|聊天内容)" +
        "|这样我才能帮你|才能帮你(起草|回复|生成)" +
        "|无法(生成|起草)(回复|候选)" +
        "|作为一个?(AI|ai|人工智能)助手?|作为一个?语言模型)",
)

/** 判定候选是否为 LLM 元话语（请求澄清/自我指称），命中则丢弃。 */
fun isMetaCandidate(text: String?): Boolean = text != null && META_REPLY.containsMatchIn(text)
