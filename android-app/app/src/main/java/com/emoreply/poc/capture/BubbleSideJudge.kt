package com.emoreply.poc.capture

/**
 * T02222：微信**气泡颜色**判侧（截图 OCR 路径的方向主判据）。
 *
 * 平台无关的事实源与回归网是 `m1-poc/emoreply/bubble_side.py`（14 条用例覆盖
 * 单聊/群聊/图片/语音/深色模式）；本对象的阈值、取样点位、投票规则与其逐条对应，
 * 改动必须两侧同步。设计理由（为什么必须扣页面基线、为什么深色主题要反着判）
 * 见 py 模块文档。
 *
 * 判据（色域匹配，非精确匹配）：
 * - 绿色系（#95EC69 / #A9EA7C 一类）→ 我说；
 * - 中性且比页面基线亮一档（#FFFFFF / #F2F2F2，深色主题下是「比 #111111 亮一档的
 *   #2C2C2C」）→ 对方说；
 * - 其余（字形、头像、表情、图片、气泡描边、页底本身）→ 该样本作废，不计票。
 *
 * 颜色判不出（非微信 / 深色读不出 / 取样全落在作废区）时由调用方回退
 * `EmoReplyReaderService.isMine` 位置判定，并把该行计入「方向存疑」。
 */
object BubbleSideJudge {

    const val SIDE_UNKNOWN = 0
    const val SIDE_MINE = 1
    const val SIDE_OTHER = 2

    // ===== 阈值（与 py bubble_side.py 同名常量一一对应）=====
    private const val GREEN_DELTA = 18           // g - max(r,b) ≥ 此值 → 绿（我说气泡）
    private const val GREEN_MIN_G = 90           // 绿通道绝对亮度下限，排掉暗部抗锯齿噪点
    private const val NEUTRAL_MAX_SPAN = 14      // max-min ≤ 此值 → 近中性（白/灰）
    private const val PAGE_EPS = 4               // 与页面基线通道差 ≤ 此值 → 就是页底，作废
    private const val LIGHT_MIN_CHANNEL = 225    // 浅色主题「白气泡」最低通道门槛
    private const val LIGHT_STEP_UP = 5          // 浅色：须比页底亮 ≥ 此值（#F2F2F2 vs #EDEDED 差 5）
    private const val LIGHT_PAGE_LUM = 80        // 页底明度 < 此值 → 深色主题分支
    private const val DARK_STEP_UP = 12          // 深色：气泡比页底亮 ≥ 此值
    private const val DARK_STEP_CAP = 110        // 深色：但不得亮过此值（否则是近白字形）
    private const val DARK_MAX_LUM = 210         // 深色：气泡绝对明度上限，同上
    private const val MIN_VOTES = 2              // 决定性样本数下限（单点不定，防偶发撞色）

    // ===== T02305 B：浅色主题下「我说绿」须贴近微信锚点 =====
    // 原判据「任意 g 主导色即绿」会把 App 品牌绿当成我说气泡：真机银行类截图（左侧
    // 对方气泡里的图片消息）的品牌绿表头 (0,160,93) 拿到 30/30 绿票 → 输出「我：客户
    // 账户名称…」，且 byColor=true 连「方向存疑」都不进。两个我说绿锚点是
    // (149,236,105)/(169,234,124)：明度都 ≥105、g≥234、g-r≤87；品牌深绿 r≈0、g≈160、
    // g-r≈160 四个方向全在锚点区间之外，故按这四个方向同时收口。
    // 深色主题不套本盒：我说气泡 #1AAB39=(26,171,57) 与常见品牌绿数值高度接近，加盒会
    // 把真气泡一起排掉（宁缺勿错），深色仍靠色相 + 「比页底亮一档」的相对判据兜底。
    private const val LIGHT_GREEN_MIN_G = 190
    private const val LIGHT_GREEN_MIN_R = 100   // 本盒的主力排他项：品牌绿 r≈0 直接出局
    private const val LIGHT_GREEN_MIN_B = 60
    private const val LIGHT_GREEN_GR_MAX = 130  // 锚点 g-r=87/65 + 抗锯齿余量

    /** 环带样本算「图片内容」的通道跨度下限（T02305 A，与 py 同名常量对应）。 */
    const val CONTENT_MIN_SPAN = 60

    /** 水平取样偏移（相对文本框左右缘）与纵向分位：微信气泡横内边距实测 20-50px。 */
    private val H_OFFSETS = intArrayOf(8, 20, 32)
    private val H_FRACS = doubleArrayOf(0.25, 0.5, 0.75)

    /** 垂直取样偏移（相对文本框上下缘）与横向分位：纵内边距实测 15-40px。 */
    private val V_OFFSETS = intArrayOf(8, 16)
    private val V_FRACS = doubleArrayOf(0.3, 0.5, 0.7)

    /** 一行像素判侧的返回：side 为 SIDE_*，[byColor]=false 表示颜色没判出来、走的回退。 */
    data class SideCall(val side: Int, val byColor: Boolean, val greenVotes: Int, val otherVotes: Int)

    /** 一行的文本框（截图坐标系）。 */
    data class ProbeBox(val left: Int, val right: Int, val top: Int, val bottom: Int)

    private fun red(color: Int) = (color shr 16) and 0xFF
    private fun green(color: Int) = (color shr 8) and 0xFF
    private fun blue(color: Int) = color and 0xFF
    private fun lum(color: Int) = (red(color) + green(color) + blue(color)) / 3
    private fun span(color: Int) = maxOf(red(color), green(color), blue(color)) -
        minOf(red(color), green(color), blue(color))

    /** 单像素 → SIDE_MINE / SIDE_OTHER / SIDE_UNKNOWN（=该样本不计票）。绿色优先。
     *
     * [pageLeft]/[pageRight] 是屏幕左、右两缘的页面基线：真机历史截图（2026-10-16
     * 核对 tmp/wadb）发现用户可设**聊天背景图**，页底是渐变色（左缘 (217,237,244)、
     * 右缘 (240,249,253)），单一基线会把另一侧页底误判成白气泡。
     */
    fun classifySample(color: Int, pageLeft: Int, pageRight: Int): Int {
        val lightTheme = minOf(lum(pageLeft), lum(pageRight)) >= LIGHT_PAGE_LUM
        val isGreen = isGreenPixel(color, lightTheme)
        if (span(color) > NEUTRAL_MAX_SPAN && !isGreen) return SIDE_UNKNOWN // 彩色：图片/表情/头像
        if (sameAsPage(color, pageLeft) || sameAsPage(color, pageRight)) return SIDE_UNKNOWN
        if (isGreen) return SIDE_MINE
        if (span(color) > NEUTRAL_MAX_SPAN) return SIDE_UNKNOWN
        val darkest = minOf(lum(pageLeft), lum(pageRight))
        val brightest = maxOf(lum(pageLeft), lum(pageRight))
        return if (darkest < LIGHT_PAGE_LUM) {
            // 深色主题：以最暗页底为基准，气泡亮一档但不得亮过上限（否则是近白字形）
            val step = lum(color) - darkest
            if (step in DARK_STEP_UP..DARK_STEP_CAP && lum(color) <= DARK_MAX_LUM) SIDE_OTHER
            else SIDE_UNKNOWN
        } else if (minOf(red(color), green(color), blue(color)) < LIGHT_MIN_CHANNEL) {
            SIDE_UNKNOWN
        } else if (lum(color) >= brightest + LIGHT_STEP_UP) {
            SIDE_OTHER
        } else {
            SIDE_UNKNOWN
        }
    }

    /** 色相判据（两主题共用）+ 浅色主题专属的锚点色域盒（T02305 B）。
     *
     * [lightTheme]=false 时只校色相——深色主题的我说绿 #1AAB39 与 App 品牌绿几乎同值，
     * 套盒会连真气泡一起排掉。
     */
    private fun isGreenPixel(color: Int, lightTheme: Boolean = true): Boolean {
        val r = red(color)
        val g = green(color)
        val b = blue(color)
        if (g - maxOf(r, b) < GREEN_DELTA || g < GREEN_MIN_G) return false
        if (!lightTheme) return true
        return g >= LIGHT_GREEN_MIN_G && r >= LIGHT_GREEN_MIN_R && b >= LIGHT_GREEN_MIN_B &&
            g - r <= LIGHT_GREEN_GR_MAX
    }

    /** 样本是否属于「图片内容」：饱和（跨度 ≥ [CONTENT_MIN_SPAN]）且判不出气泡底色。
     *  T02305 A 的环带判据按此计数——跨度下限挡掉近中性的页底/白气泡，锚点绿由
     *  [classifySample] 返回 SIDE_MINE 而排除在外，故真·多行「我」的消息不会被误杀。 */
    fun isImageContent(color: Int, pageLeft: Int, pageRight: Int): Boolean =
        span(color) >= CONTENT_MIN_SPAN &&
            classifySample(color, pageLeft, pageRight) == SIDE_UNKNOWN

    /** 通道跨度、明度（像素算术只此一份，供 T02305 A′′ 的灰阶环带判据复用）。 */
    fun spanOf(color: Int): Int = span(color)
    fun lumOf(color: Int): Int = lum(color)

    private fun sameAsPage(color: Int, page: Int): Boolean =
        kotlin.math.abs(red(color) - red(page)) <= PAGE_EPS &&
            kotlin.math.abs(green(color) - green(page)) <= PAGE_EPS &&
            kotlin.math.abs(blue(color) - blue(page)) <= PAGE_EPS

    /** 投票：绿优先（微信我说气泡的唯一强特征），其余需明显多于绿。 */
    fun decideSide(greenVotes: Int, otherVotes: Int): Int = when {
        greenVotes >= MIN_VOTES && greenVotes >= otherVotes -> SIDE_MINE
        otherVotes >= MIN_VOTES && otherVotes > greenVotes -> SIDE_OTHER
        else -> SIDE_UNKNOWN
    }

    /** 页面基线色（左缘、右缘各一个；聊天背景图可能带渐变，单一基线会误判）。 */
    fun pageColors(getPixel: (x: Int, y: Int) -> Int, y: Int, width: Int, height: Int): IntArray {
        val left = modalPage(getPixel, intArrayOf(2, 4), y, height)
        val right = modalPage(getPixel, intArrayOf(width - 3, width - 5), y, height)
        return intArrayOf(left, right)
    }

    /** 本侧两列在 [y] 附近的众数（并列取第一个）：避开偶发的头像/悬浮条像素。 */
    private fun modalPage(getPixel: (x: Int, y: Int) -> Int, xs: IntArray, y: Int,
                          height: Int): Int {
        val votes = HashMap<Int, Int>()
        var best = 0
        var bestCount = 0
        for (x in xs) {
            for (dy in intArrayOf(-40, 0, 40)) {
                val yy = (y + dy).coerceIn(0, height - 1)
                val c = getPixel(x, yy)
                val n = (votes[c] ?: 0) + 1
                votes[c] = n
                if (n > bestCount) {
                    bestCount = n
                    best = c
                }
            }
        }
        return best
    }

    /** 气泡内边距取样点（水平 6 列 × 3 行 + 垂直 4 行 × 3 列 = 30 点，越界丢弃）。 */
    fun probePoints(box: ProbeBox, width: Int, height: Int): List<IntArray> {
        val pts = ArrayList<IntArray>(30)
        val h = box.bottom - box.top
        val w = box.right - box.left
        for (d in H_OFFSETS) {
            for (f in H_FRACS) {
                val y = box.top + (h * f).toInt()
                pts.add(intArrayOf(box.left - d, y))
                pts.add(intArrayOf(box.right + d, y))
            }
        }
        for (d in V_OFFSETS) {
            for (f in V_FRACS) {
                val x = box.left + (w * f).toInt()
                pts.add(intArrayOf(x, box.top - d))
                pts.add(intArrayOf(x, box.bottom + d))
            }
        }
        return pts.filter { (x, y) -> x in 0 until width && y in 0 until height }
    }

    /**
     * 单行判侧：颜色优先，判不出时按 [positionSide]（T01143 左缘比例）回退。
     *
     * [colorAvailable]=false 用于非微信场景（钉钉/QQ/短信气泡色各异，颜色规则反而
     * 拉低准确率）——直接回退位置判定且不做任何像素取样。
     */
    fun judge(
        getPixel: (x: Int, y: Int) -> Int,
        box: ProbeBox,
        width: Int,
        height: Int,
        positionSide: Int,
        colorAvailable: Boolean,
    ): SideCall {
        if (!colorAvailable) return SideCall(positionSide, false, 0, 0)
        val (green, other) = lineVotes(getPixel, box, width, height)
        val side = decideSide(green, other)
        if (side != SIDE_UNKNOWN) return SideCall(side, true, green, other)
        // T02305 真机复测：30 个环带样本全部作废（绿/白票双 0）说明这行根本不在
        // 气泡上——拍屏截图内文字的灰底就是这个形态。此时位置回退同样不可信：
        // 图内文字居中排版，左缘越过 SELF_SIDE_LEFT_RATIO 就凭空造出「我：」。
        // 「我」是唯一危险的伪造方向（把对方内容伪装成用户发言并驱动回复），
        // 故零证据时只允许落到对方。与 py 事实源 judge_side_with_fallback 同构。
        if (green == 0 && other == 0) return SideCall(SIDE_OTHER, false, 0, 0)
        return SideCall(positionSide, false, green, other)
    }

    /** 一行环带取样的票数 `[green, other]`（与 py `judge_line` 的计票部分同构）。
     *  T02305 A′′ 要用「双 0」这个信号：零票数即零气泡证据，与「票数不足」不同。 */
    fun lineVotes(
        getPixel: (x: Int, y: Int) -> Int,
        box: ProbeBox,
        width: Int,
        height: Int,
    ): IntArray {
        val pages = pageColors(getPixel, (box.top + box.bottom) / 2, width, height)
        var green = 0
        var other = 0
        for ((x, y) in probePoints(box, width, height)) {
            when (classifySample(getPixel(x, y), pages[0], pages[1])) {
                SIDE_MINE -> green++
                SIDE_OTHER -> other++
                else -> Unit
            }
        }
        return intArrayOf(green, other)
    }
}
