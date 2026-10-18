package com.emoreply.poc.capture

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import com.google.mlkit.vision.text.TextRecognizer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/**
 * 读屏服务（T01111，pull 模式原型）。
 *
 * 合规边界（实现严守，见任务描述与 res/xml/reader_service_config.xml）：
 * 1. 仅在用户显式点击 IME「📖 读屏」按钮时调用 [captureVisibleMessages] 读取一次，
 *    无后台常驻收集——[onAccessibilityEvent] 刻意留空，不做任何事件内容收集；
 * 2. 无任何模拟点击/滚动/注入操作（AccessibilityNodeInfo.performAction 一律不调用）；
 * 3. 只读当前屏幕可见文本（微信一屏约 8-12 条），上限截取 20，不滚动补齐；
 * 4. 纯本地处理，不外传、不落盘（沿 FR-6.1/6.2 零留存设计）。
 *
 * 已知风险（转正前须真机验证）：微信用户协议禁止第三方读取聊天内容，账号风控风险
 * 低但非零；微信版本更新可能导致节点结构变化使解析失效（原型按通用 TextView 遍历，
 * 不绑定具体控件 id，抗版本漂移较好但过滤规则需持续维护）。
 */
class EmoReplyReaderService : AccessibilityService() {

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        runCatching { recognizer?.close() } // T01124：释放 ML Kit 识别器
        recognizer = null
        super.onDestroy()
    }

    /** pull 模式红线：事件回调不收集任何内容（订阅 typeWindowStateChanged 仅为能力声明）。 */
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    companion object {

        @Volatile
        private var instance: EmoReplyReaderService? = null

        /** 服务已在系统无障碍设置中启用且连接。 */
        fun isReady(): Boolean = instance != null

        // T01312 FR-7.4：多屏采集分屏进度广播。此前 captureMultiScreen 是同步循环，
        // IME 侧拿不到任何中间态，只能显示「正在读取…」这一句静态等待文案；现每完成
        // 一屏即发一次显式广播（带包名以适配 Android 8+ 的隐式广播限制），IME 侧注册
        // 接收器把文案更新为「已读第 N/M 屏…」，采集结束后注销。
        const val ACTION_READ_PROGRESS = "com.emoreply.poc.action.READ_PROGRESS"
        const val EXTRA_READ_INDEX = "index" // 1-based
        const val EXTRA_READ_TOTAL = "total"

        /** 上报分屏进度（index 从 1 开始）。服务未连接时静默失败，不影响采集主流程。 */
        private fun sendReadProgress(index: Int, total: Int) {
            val ctx = instance ?: return
            runCatching {
                Intent(ACTION_READ_PROGRESS).apply {
                    setPackage(ctx.packageName)
                    putExtra(EXTRA_READ_INDEX, index)
                    putExtra(EXTRA_READ_TOTAL, total)
                }.also { ctx.sendBroadcast(it) }
            }.onFailure { Log.w(TAG, "progress broadcast failed", it) }
        }

        private val TIME_ONLY = Regex("^([01]?\\d|2[0-3])[:：][0-5]\\d$")
        private val DATE_ONLY = Regex("^\\d{1,4}[./-]\\d{1,2}[./-]\\d{1,2}$")

        // T01112+ 噪声过滤：钉钉/短信实测记录的「已读回执/时间行/hint/占位符」四类噪声
        // 「昨」容错为「[昨咋]」：ML Kit 对时间分隔行的「昨」有误读为「咋」的实测案例
        private val TIME_EXTENDED = Regex(
            "^([昨咋]天|今天|前天|星期[一二三四五六日天]|周[一二三四五六日天]|\\d{1,2}月\\d{1,2}日|\\d{4}年\\d{1,2}月\\d{1,2}日)?" +
                "\\s*(上午|下午|中午|早上|晚上|凌晨)?\\s*([01]?\\d|2[0-3])[:：][0-5]\\d(:[0-5]\\d)?$",
        )
        private val DATE_CN = Regex(
            "^([昨咋]天|前天|今天|星期[一二三四五六日天]|周[一二三四五六日天]|" +
                "\\d{1,2}月\\d{1,2}日(\\s*周[一二三四五六日天])?|\\d{4}年\\d{1,2}月\\d{1,2}日)$",
        )

        /** 未读角标形态：99+ / 9+ 。纯数字短消息（如 666）刻意保留。 */
        private val BADGE_COUNT = Regex("^\\d{1,3}\\+$")

        /** 纯符号/图标字形/空白：含 Unicode 私有区（iconfont）、几何图形（□占位）、箭头/杂项符号。 */
        private val SYMBOL_ONLY = Regex(
            "^[\\s\\p{Punct}\\u3000-\\u303F\\uFF00-\\uFFEF\\uE000-\\uF8FF\\u2190-\\u21FF\\u2300-\\u23FF\\u25A0-\\u25FF\\u2600-\\u27BF]+$",
        )

        private val RECALL_NOTICE = Regex("^.{0,20}撤回了一条消息$")

        /**
         * 短文本内含时刻（≤8 字符）：时间分隔行的 OCR 误读兜底（如「昨天 22:31」误读为「B 22:31」）。
         * T02224 前缀收紧：原 `.{0,4}` 允许任意 4 字符，把右侧气泡「通话时长10:52」整行当成
         * 时间分隔行（实测反复出现的「理由栏被识别成候选回复」根因）。时间分隔行的前缀只会是
         * 日期词或图标误读残字，故中文前缀只放行日期词，其余前缀限定为非中文残字。
         */
        private val SHORT_TIME_LINE = Regex(
            "^([昨咋]天|前天|今天|星期[一二三四五六日天]|周[一二三四五六日天]|上午|下午|中午|早上|晚上|凌晨" +
                "|[^\\u4e00-\\u9fa5]{0,4})\\s*([01]?\\d|2[0-3])[:：][0-5]\\d(:[0-5]\\d)?$",
        )

        /** 固定 UI 文案：已读回执、系统分隔行、常见输入框 hint。 */
        private val FIXED_NOISE = setOf(
            "已读", "未读",
            "以上是新消息", "以下为新消息", "新消息",
            "对方正在输入…", "对方正在输入...", "正在输入…", "正在输入...",
            "输入信息", "输入消息", "发送消息", "请输入消息", "Type a message", "Enter message",
            // 平台营销卡片按钮文案（美团实测：与消息正文同为 TextView 且不可点、不可长按，
            // 无属性可区分，只能按固定文案滤除；这些短语作为真实聊天内容的概率极低）
            "点击拒收", "点此去评价", "服务评价", "进群领券", "立即进入粉丝群", "进群领取专属优惠券",
        )

        /** 通话类系统提示（居中灰字，非双方消息）：用户反馈「对方已取消」被当成对方消息
         *  进入上下文，LLM 误判为「对方撤回消息」而生成错向候选。
         *  实测形态：对方已取消 / 已取消 / 对方已拒绝 / 对方无应答 / 已挂断 / 通话时长 00:28 /
         *  邀请你语音通话。前导允许 ≤3 个非中文字符——气泡旁的通话图标会被 OCR 误读成单字母
         *  （实测「s对方已取消」）；限定非中文可避免误杀「订单已取消」这类真实聊天内容。 */
        private val CALL_SYSTEM_NOTICE = Regex(
            "^[^\\u4e00-\\u9fa5]{0,3}(对方已取消|已取消|对方已拒绝|已拒绝|对方无应答|无应答|" +
                "对方忙线中|对方忙|已挂断|通话时长.{0,12}|邀请你(语音|视频)通话|" +
                "(语音|视频)通话已取消|(语音|视频)通话中)$",
        )

        /** 内容级噪声判定（collect 已滤 EditText/hint 节点与头像占位符）。 */
        private fun isMessageText(v: String): Boolean = when {
            v.isBlank() -> false
            // 去掉数字/标点/空白后无任何文字 → 图标误读（如「0,」）或纯符号，滤除
            v.replace(Regex("[0-9\\s\\p{Punct}\\u3000-\\u303F\\uFF00-\\uFFEF]"), "").isEmpty() -> false
            v in FIXED_NOISE -> false
            TIME_ONLY.matches(v) || TIME_EXTENDED.matches(v) || DATE_ONLY.matches(v) || DATE_CN.matches(v) -> false
            SHORT_TIME_LINE.matches(v) -> false
            BADGE_COUNT.matches(v) -> false
            SYMBOL_ONLY.matches(v) -> false
            RECALL_NOTICE.matches(v) -> false
            CALL_SYSTEM_NOTICE.matches(v) -> false
            else -> true
        }

        /**
         * 读取当前微信聊天窗口可见的聊天文本（自上而下 = 时间正序）。
         * 过滤纯时间/日期行；上限 [maxCount] 条（取最下方的最新消息）。
         * 返回空列表 = 服务未连接 / 微信窗口不在前台 / 未读到内容，由调用方提示用户。
         *
         * 注意不能用 rootInActiveWindow：键盘弹出时活动窗口是 IME 自己的窗口，
         * 须遍历 getWindows() 按包名定位微信窗口（T01111 真机实测踩坑）。
         */
        fun captureVisibleMessages(maxCount: Int = 20): List<String> {
            val svc = instance
            Log.i(TAG, "capture: instance=${svc != null}")
            if (svc == null) return emptyList()
            lastSideStats = SideStats() // T02222：本次读屏的判侧统计从零累计
            val wins = runCatching { svc.windows }.getOrElse { emptyList() }
            Log.i(TAG, "capture: windows=${wins.size} pkgs=${wins.mapNotNull { it.root?.packageName }}")
            // 多平台窗口（T01111 扩展：验证短信/钉钉是否同样反制），全部遍历合并收集
            val roots = wins.mapNotNull { w -> w.root?.takeIf { it.packageName in TARGET_PKGS } }
            Log.i(TAG, "capture: wechatRoots=${roots.size}")
            if (roots.isEmpty()) return emptyList()
            val hits = mutableListOf<NodeLine>() // 屏幕 top / 气泡右缘 / 文本
            val imageRegions = mutableListOf<android.graphics.Rect>() // T01334：图片消息区域
            var visited = 0
            roots.forEach { root ->
                val before = hits.size
                collect(root, hits, onVisit = { visited++ }, regions = imageRegions)
                Log.i(
                    TAG,
                    "capture: root@${root.hashCode()} cls=${root.className} " +
                        "children=${root.childCount} visited=$visited hits=${hits.size - before}",
                )
            }
            Log.i(TAG, "capture: rawHits=${hits.size}")
            // 诊断：限量打印被过滤的噪声样本（真机核对过滤效果用）
            hits.asSequence()
                .map { it.text.trim() }
                .filter { !isMessageText(it) }
                .distinct()
                .take(8)
                .forEach { Log.i(TAG, "capture: filtered=\"$it\"") }
            // 标题栏区（top < 260px）：联系人名/组织架构副标题，非聊天消息
            val filteredTop = hits.count { it.top < TOPBAR_MAX_PX && isMessageText(it.text.trim()) }
            if (filteredTop > 0) Log.i(TAG, "capture: filtered topbar x$filteredTop")
            // 重复短文本（同文出现 >=3 次且 <=12 字）：钉钉发送者名标注/水印平铺层；
            // 上限 12 字是美团商家卡片「发送者名」实测所需（如「蜜雪冰城（大连商场店）」11 字）
            val freq = hits.groupingBy { it.text.trim() }.eachCount()
            // T01343：侧别比例判定的屏幕宽度基准（一次计算，序内复用）
            val sw = screenWidthPx()
            val result = hits.asSequence()
                .filter {
                    val v = it.text.trim()
                    if (it.top < TOPBAR_MAX_PX) return@filter false
                    // T01334：落在图片消息区域内的文本行=图内文字，剔除（非对方发言）
                    if (isInsideImage(it, imageRegions)) return@filter false
                    // T01343：时间行转「话题分段标记」保留（此前静默丢弃，浪费了唯一的话题边界信号）
                    // T02224：加「坐标居中」门槛，避免右侧气泡正文被吞成分隔行
                    if (isTimeMark(v, it.cx, sw)) return@filter true
                    isMessageText(v) && !(v.length <= 12 && (freq[v] ?: 0) >= 3)
                }
                .sortedBy { it.top }
                // 侧别前缀（与 OCR 路径 T01129 同构）：节点路径此前输出裸文本，生成层分不清
                // 「谁说的」，实测把自己发言当成对方发言、回复方向反了。
                .map {
                    val v = it.text.trim()
                    val prefix = when {
                        isTimeMark(v, it.cx, sw) -> TIME_MARK_PREFIX
                        isMine(it.left, sw) -> SPEAKER_ME_PREFIX
                        else -> SPEAKER_OTHER_PREFIX
                    }
                    prefix + v
                }
                .toList()
                .takeLast(maxCount)
            Log.i(TAG, "capture: result=${result.size}")
            // T02222：节点树路径没有截图像素，方向全部来自左缘位置判定 → 计入「存疑」
            lastSideStats = SideStats(judged = result.count { !it.startsWith(TIME_MARK_PREFIX) })
            return result
        }

        private val TARGET_PKGS = setOf(
            "com.tencent.mm",                // 微信（实测 8.0.78 节点树保护）
            "com.alibaba.android.rimet",     // 钉钉
            "com.samsung.android.messaging", // 三星短信
            "com.android.mms",               // 通用短信
            "com.tencent.wework",            // 企业微信
            "com.tencent.mobileqq",          // QQ
            "com.taobao.idlefish",           // 闲鱼（实测消息文本挂 content-desc 而非 text）
            "com.sankuai.meituan",           // 美团（实测 text 可读）
        )
        private const val TAG = "EmoReplyReader"

        /**
         * 深度优先收集所有带文本的节点；只读 text/content-desc，不触发任何节点动作。
         * 节点级过滤：
         * - EditText 整棵跳过（输入框 hint/占位符/用户草稿均非对方消息）；
         * - 带 hintText 的节点跳过（其 text 是 hint 提示而非消息）；
         * - 头像占位符：单字符且节点宽高均 < 80px（约 30dp）的小方块文本滤除，
         *   真实短消息（「哦」「好」）节点为整行气泡宽度，不会被误杀；
         * - content-desc 兜底（仅 text 为空时启用）须节点可点且可长按，见实现注释。
         */
        /** 节点路径的行记录：屏幕 top / bottom（图片区域判定）/ 气泡左缘右缘（判侧别）/ 文本。 */
        private data class NodeLine(
            val top: Int,
            val bottom: Int,
            val left: Int,
            val right: Int,
            val text: String,
        ) {
            val cx get() = (left + right) / 2
        }

        // ===== T01334：图片消息区域文字过滤（图内文字被 OCR/节点树读出会污染上下文）=====

        /** 图片消息控件类名：气泡内的大图（ImageView）。 */
        private val IMAGE_VIEW_CLASSES = setOf("android.widget.ImageView")

        /** 图片区域最小边长（px）：小于此值的是头像/小图标，不当图片消息处理。 */
        private const val IMAGE_REGION_MIN_PX = 200

        /** 图内文字行簇判据（OCR 路径，与 py 同构）：连续 >=3 行、行间空白 < 行高 ×
         *  0.35 构成簇；两条像素判据任一命中即整簇剔除：
         *  · 簇并集框四周环带上的图片内容样本占比 ≥ 0.5（[ringContentRatio]）；
         *  · 或整簇零气泡证据 + 环带是带纹理的中性灰（[clusterWithoutBubble]，抓灰阶拍屏）。
         *
         *  T02305：原第二判据是「簇内无标点行占比 ≥ 0.8」，而银行/表单类截图正文天生
         *  带冒号逗号（键值对排版），占比不达 0.8 → 整簇漏网 → 截图自己的品牌绿又被
         *  判成我说气泡，输出伪造「我：」。用文本内容反推「是否图内」在这类截图上先天
         *  不成立，判据只能来自像素。
         *
         *  必须在 mergeWrappedLines **之前**跑（真机 10-17 复测订正）：折行合并把 3 行并
         *  成 1 条高行，既凑不满 IMAGE_CLUSTER_MIN，行高又让间隙判据恒不成立。 */
        private const val IMAGE_CLUSTER_MIN = 3
        private const val IMAGE_CLUSTER_GAP_RATIO = 0.35f
        private val IMAGE_RING_OFFSETS = intArrayOf(12, 28, 48)
        private const val IMAGE_CONTENT_RATIO = 0.5f
        private const val IMAGE_GRAY_LUM_SPREAD_MIN = 40

        /** 环带上「图片内容」样本占比（饱和且判不出气泡底色，见 [BubbleSideJudge.isImageContent]）。 */
        private fun ringContentRatio(
            bmp: Bitmap,
            cluster: List<OcrLineInfo>,
            pageCache: MutableMap<Int, IntArray>,
        ): Float {
            val left = cluster.minOf { it.left }
            val right = cluster.maxOf { it.right }
            val top = cluster.minOf { it.top }
            val bottom = cluster.maxOf { it.bottom }
            var hits = 0
            var total = 0
            for (d in IMAGE_RING_OFFSETS) {
                for (f in floatArrayOf(0.25f, 0.5f, 0.75f)) {
                    val x = left + ((right - left) * f).toInt()
                    val y = top + ((bottom - top) * f).toInt()
                    for (pt in arrayOf(
                        intArrayOf(x, top - d), intArrayOf(x, bottom + d),
                        intArrayOf(left - d, y), intArrayOf(right + d, y),
                    )) {
                        val px = pt[0]
                        val py = pt[1]
                        if (px !in 0 until bmp.width || py !in 0 until bmp.height) continue
                        total++
                        val pages = pageCache.getOrPut(py / 16) {  // 页底沿 y 缓变，按 16px 分桶
                            BubbleSideJudge.pageColors(
                                { x2, y2 -> bmp.getPixel(x2, y2) }, py, bmp.width, bmp.height)
                        }
                        if (BubbleSideJudge.isImageContent(
                                bmp.getPixel(px, py), pages[0], pages[1])
                        ) hits++
                    }
                }
            }
            return if (total == 0) 0f else hits.toFloat() / total
        }

        /** 整簇「零气泡证据 + 环带是带纹理的中性灰」→ 图内文字（与 py
         *  `_cluster_without_bubble` 同构；T02305 A′′，真机 10-17 史文豪会话的拍屏截图）。
         *
         *  [ringContentRatio] 用「饱和色」把字形与页底挡在外面，代价是**灰阶拍屏照片**
         *  整块环带都被跨度下限放过（样本众数 (185,186,181)、单样本跨度仅 5）→ 占比 0
         *  → 漏网。两个条件缺一不可，各自挡掉一类误杀：
         *  · 每行 30 点环带绿/白票双 0 —— 真气泡（白/绿/深色）必然给得出票数；
         *  · 环带样本近中性但亮度极差 ≥ 40 —— 挡住「#F2F2F2 气泡坐在亮聊天背景图上」
         *    这种同样零票数却完全平坦的场景（实测平坦气泡极差 0~5、灰阶照片 49~108）。
         */
        private fun clusterWithoutBubble(bmp: Bitmap, cluster: List<OcrLineInfo>): Boolean {
            val getPixel = { x: Int, y: Int -> bmp.getPixel(x, y) }
            val anyVotes = cluster.any {
                val v = BubbleSideJudge.lineVotes(
                    getPixel, BubbleSideJudge.ProbeBox(it.left, it.right, it.top, it.bottom),
                    bmp.width, bmp.height)
                v[0] != 0 || v[1] != 0
            }
            if (anyVotes) return false
            val left = cluster.minOf { it.left }
            val right = cluster.maxOf { it.right }
            val top = cluster.minOf { it.top }
            val bottom = cluster.maxOf { it.bottom }
            val lums = ArrayList<Int>()
            for (d in IMAGE_RING_OFFSETS) {
                for (f in floatArrayOf(0.25f, 0.5f, 0.75f)) {
                    val x = left + ((right - left) * f).toInt()
                    val y = top + ((bottom - top) * f).toInt()
                    for (pt in arrayOf(
                        intArrayOf(x, top - d), intArrayOf(x, bottom + d),
                        intArrayOf(left - d, y), intArrayOf(right + d, y),
                    )) {
                        val px = pt[0]
                        val py = pt[1]
                        if (px !in 0 until bmp.width || py !in 0 until bmp.height) continue
                        val rgb = bmp.getPixel(px, py)
                        if (BubbleSideJudge.spanOf(rgb) >= BubbleSideJudge.CONTENT_MIN_SPAN) {
                            return false // 有饱和样本：属第一条判据的射程，不在此重复判
                        }
                        lums.add(BubbleSideJudge.lumOf(rgb))
                    }
                }
            }
            val hi = lums.maxOrNull() ?: return false
            val lo = lums.minOrNull() ?: return false
            return hi - lo >= IMAGE_GRAY_LUM_SPREAD_MIN
        }

        /** 节点树路径：文本行中心点落在图片消息区域内 → 剔除（硬证据，与 py 同构）。 */
        private fun isInsideImage(line: NodeLine, regions: List<android.graphics.Rect>): Boolean {
            if (regions.isEmpty()) return false
            val cx = (line.left + line.right) / 2
            val cy = (line.top + line.bottom) / 2
            return regions.any { it.left <= cx && cx <= it.right && it.top <= cy && cy <= it.bottom }
        }

        /** OCR 路径：剔除「图内文字」行簇（与 py filter_image_text_clusters 同构）。 */
        private fun filterImageTextClusters(
            bmp: Bitmap,
            lines: List<OcrLineInfo>,
        ): List<OcrLineInfo> {
            val ordered = lines.sortedBy { it.top }
            val out = mutableListOf<OcrLineInfo>()
            val pageCache = HashMap<Int, IntArray>()
            var i = 0
            while (i < ordered.size) {
                var j = i
                while (j + 1 < ordered.size) {
                    val gap = ordered[j + 1].top - ordered[j].bottom
                    val h = maxOf(ordered[j].height, ordered[j + 1].height, 1)
                    if (gap < h * IMAGE_CLUSTER_GAP_RATIO) j++ else break
                }
                if (j - i + 1 >= IMAGE_CLUSTER_MIN &&
                    (ringContentRatio(bmp, ordered.subList(i, j + 1), pageCache) >=
                        IMAGE_CONTENT_RATIO ||
                        clusterWithoutBubble(bmp, ordered.subList(i, j + 1)))
                ) {
                    i = j + 1 // 整簇判为图内文字，剔除
                    continue
                }
                out.add(ordered[i])
                i++
            }
            return out
        }

        // T01339 复验反馈：视频/图片等非纯文本不应纳入识别——OCR 噪声行过滤（与 py 同构）。
        private val MEDIA_PLACEHOLDER = Regex(
            """^\s*[【\[]\s*(图片|视频|动画表情|文件|语音|链接|名片|音乐|位置|红包|转账)\s*[】\]]\s*$""",
        )
        private val OVERLAY_BADGE = Regex("""^[\dA-Za-z一-鿿]{1,6}\s*[+＋]$""")
        private val EMOJI_RANGES = listOf(
            0x1F000..0x1FAFF, 0x2600..0x27BF, 0x2190..0x21FF, 0x2B00..0x2BFF,
        )

        private fun isEmoji(ch: Char): Boolean = EMOJI_RANGES.any { ch.code in it }

        private fun symbolNoiseRatio(text: String): Double {
            val chars = text.filterNot { it.isWhitespace() }
            if (chars.isEmpty()) return 0.0
            val noise = chars.count {
                // S* 类 = MATH_SYMBOL..OTHER_SYMBOL（25..28 连续），emoji 例外
                Character.getType(it) in Character.MATH_SYMBOL..Character.OTHER_SYMBOL && !isEmoji(it)
            }
            return noise.toDouble() / chars.length
        }

        /** 剔除媒体类噪声行：占位符 / 角标叠字 / 符号乱码（真实短消息不受影响）。 */
        private fun filterNonTextNoise(lines: List<OcrLineInfo>): List<OcrLineInfo> =
            lines.filter { ln ->
                val t = ln.text.trim()
                if (t.isEmpty() || MEDIA_PLACEHOLDER.matches(t) || OVERLAY_BADGE.matches(t)) {
                    false
                } else if (isGarbledText(t)) {
                    // T01554：乱码检测——不可打印字符/连续特殊符号/控制字符占比过高则丢弃
                    Log.d(TAG, "filterNonTextNoise: garbled text filtered: [$t]")
                    false
                } else {
                    symbolNoiseRatio(t) < 0.34
                }
            }

        /**
         * T01554：乱码文本检测——识别 OCR 误读产生的无效文本。
         *
         * 判据（任一命中即视为乱码）：
         * 1. 包含 Unicode 控制字符（C0/C1 区，排除常见空白 \t\n\r）；
         * 2. 不可打印字符占比 > 30%；
         * 3. 连续 3+ 个相同非字母数字字符（如「■■■」「???」）；
         * 4. 纯符号行（无汉字/字母/数字，且长度 > 2）。
         */
        private fun isGarbledText(text: String): Boolean {
            if (text.isBlank()) return true

            // 1. 控制字符检测（排除 \t \n \r）
            val controlChars = text.count { ch ->
                Character.isISOControl(ch) && ch != '\t' && ch != '\n' && ch != '\r'
            }
            if (controlChars > 0) return true

            // 2. 不可打印字符占比
            val printable = text.count { ch ->
                !Character.isISOControl(ch) && !Character.isWhitespace(ch) &&
                    (ch.isLetterOrDigit() || ch in '\u4e00'..'\u9fff' || isEmoji(ch))
            }
            val totalNonSpace = text.filterNot { it.isWhitespace() }.length
            if (totalNonSpace > 0 && printable.toDouble() / totalNonSpace < 0.7) return true

            // 3. 连续 3+ 个相同非字母数字字符
            val consecutivePattern = Regex("(.)\\1{2,}")
            if (consecutivePattern.containsMatchIn(text.replace("\\s".toRegex(), ""))) {
                // 进一步检查：如果重复字符是非字母数字的，视为乱码
                val repeats = consecutivePattern.findAll(text.replace("\\s".toRegex(), ""))
                if (repeats.any { match ->
                    val ch = match.value[0]
                    !ch.isLetterOrDigit() && !isEmoji(ch) && ch !in '\u4e00'..'\u9fff'
                }) {
                    return true
                }
            }

            // 4. 纯符号行（无汉字/字母/数字）
            val hasMeaningfulChar = text.any { ch ->
                ch.isLetterOrDigit() || ch in '\u4e00'..'\u9fff' || isEmoji(ch)
            }
            if (!hasMeaningfulChar && text.length > 2) return true

            return false
        }

        private fun collect(
            node: AccessibilityNodeInfo?,
            out: MutableList<NodeLine>,
            onVisit: (() -> Unit)? = null,
            regions: MutableList<android.graphics.Rect> = mutableListOf(),
        ) {
            if (node == null || out.size > MAX_NODES) return
            onVisit?.invoke()
            if (node.className?.toString() == "android.widget.EditText") return
            if (node.hintText?.isNotBlank() == true) return
            // T01334：图片消息节点（大图 ImageView）记录 bounds——其内部的图内文字
            // 会被当成对方发言混入上下文，后续按区域剔除。
            if (node.className?.toString() in IMAGE_VIEW_CLASSES) {
                val b = android.graphics.Rect()
                node.getBoundsInScreen(b)
                if (b.width() >= IMAGE_REGION_MIN_PX && b.height() >= IMAGE_REGION_MIN_PX) {
                    regions.add(b)
                }
            }
            // 部分应用（实测闲鱼）消息文本挂在 content-desc 而 text 为空；此类节点须同时
            // clickable 且 longClickable——闲鱼消息气泡可点可长按，而时间分隔行/已读回执/
            // 商品卡片/反诈横幅/头像等页面元素均不同时具备（SM-G9980 实测区分度 100%）。
            val t = node.text?.toString()?.takeIf { it.isNotBlank() }
                ?: node.contentDescription?.toString()?.takeIf {
                    it.isNotBlank() && node.isClickable && node.isLongClickable
                }
            if (t != null) {
                val bounds = android.graphics.Rect()
                node.getBoundsInScreen(bounds)
                if (t.trim().length == 1 && bounds.width() < 80 && bounds.height() < 80) {
                    return // 头像字形占位符
                }
                out.add(NodeLine(bounds.top, bounds.bottom, bounds.left, bounds.right, t))
            }
            for (i in 0 until node.childCount) {
                collect(node.getChild(i), out, onVisit, regions)
            }
        }

        private const val MAX_NODES = 200

        /** 屏幕顶部标题栏高度（px）：联系人名/组织架构副标题所在区，全滤。 */
        private const val TOPBAR_MAX_PX = 260

        /** OCR 线程要求：API 30+（takeScreenshot）且状态栏/标题栏已由裁剪处理。 */
        private const val MIN_OCR_API = 30

        /** 输入框功能行高度（px）：IME 窗口顶再往上让出这一段，滤掉「语音/+/表情」按钮文字。 */
        private const val IME_ROW_MARGIN_PX = 140

        /** T01129 侧别标注前缀（与 m1-poc preprocess.parse_speaker_line 同构，全角冒号）。 */
        const val SPEAKER_ME_PREFIX = "我："
        const val SPEAKER_OTHER_PREFIX = "对方："

        /** T01343：时间行转换的「话题分段标记」前缀（与 m1-poc preprocess.TIME_MARK_PREFIX 同构）。
         *  纯时间/日期行不再静默丢弃，而是转为「时间：xxx」标记行——它是读屏数据里
         *  唯一的话题边界信号，解析层（splitDialogueTurns）据此截断已完结话题。 */
        const val TIME_MARK_PREFIX = "时间："

        /** T01343：时间行识别（isMessageText 里各时间正则的并集，语义同步）。 */
        private fun isTimeLine(v: String): Boolean =
            TIME_ONLY.matches(v) || TIME_EXTENDED.matches(v) || DATE_ONLY.matches(v) ||
                DATE_CN.matches(v) || SHORT_TIME_LINE.matches(v)

        /**
         * 时间分隔行中心点相对屏宽的容偏比例。真机实测（1440 宽）：「10月10日19:11」
         * 中心 716.5、「昨天 13:20」误读行「BFF 13:20」中心 720.5，偏差 ≤3.5px（0.24%），
         * 而右侧气泡「通话时长10:52」中心 913.5（13.4%）——两者相差一个数量级。
         */
        private const val TIME_CHIP_CX_TOLERANCE_RATIO = 0.06f

        /**
         * T01343 预告、T02224 落地的收紧条件：文本像时间行 **且** 坐标居中才算话题分隔标记。
         * 微信/钉钉的时间分隔行始终是居中灰底胶囊，气泡正文（哪怕内容是「10:52」「通话时长
         * 10:52」）必然左/右对齐——只按文本判会把气泡吞成分隔行，方向标注随之丢失。
         */
        private fun isTimeMark(v: String, cx: Int, screenWidth: Int): Boolean =
            isTimeLine(v) && abs(cx - screenWidth / 2) <= screenWidth * TIME_CHIP_CX_TOLERANCE_RATIO

        /** T01135：聊天带最低可用高度（px）——低于此值 OCR 读不到内容，判定为被遮挡。 */
        private const val MIN_CHAT_BAND_PX = 300

        /** T01145：多屏读屏的滑动等待（ms）——上滑动画落定后再截图。 */
        private const val SWIPE_SETTLE_MS = 600L

        /** T01135：IME 窗口遮挡聊天内容的判定高度（px）——超过即认为 IME 处于展开态。 */
        const val IME_COVER_WINDOW_PX = 900

        /** T01143：侧别判定（用户反馈：群聊中对方消息被误标「我」）。
         *  通用规则=左对齐是他人、右对齐是本人（大部分聊天工具均如此）：
         *  气泡起点 left ≥ 屏宽 × [SELF_SIDE_LEFT_RATIO] → 本人，否则 → 他人。
         *  T01149 修正：移除 right>1140 辅助信号——真机实测群聊对方长气泡右缘可达
         *  1161（杨孟昭打卡消息），辅助信号反而把这类行误判为本人。
         *  T01150：OCR 截图路径此前仍按右缘 1140 标注（与本节判定不一致）——微信
         *  「下雨怕啥的…」对方长气泡右缘 1163 被误标「我」，候选方向随之反向，现同步。
         *  T01343：由绝对像素（450px）改为相对屏宽比例——450px 仅适配 1440 宽设备，
         *  换分辨率设备阈值即漂移（用户反馈「右侧我方发言被识别成对方」即此根因）。
         *  比例=450/1440≈0.3125，为 T01149 实测标定值的等比迁移，与 T01134 折行
         *  合并改相对比例同一思路：绝对像素随屏幕密度/字号/分辨率漂移。 */
        private fun isMine(left: Int, screenWidth: Int): Boolean =
            left >= screenWidth * SELF_SIDE_LEFT_RATIO

        /** 微信包名：气泡颜色判侧的适用门槛（其他平台气泡色各异，只走位置判定）。 */
        private const val WECHAT_PKG = "com.tencent.mm"

        /** T02222：单行判侧耗时红线（任务验收指标 ≤50ms）与整屏累计预算。 */
        private const val SIDE_BUDGET_MS = 50L
        private const val SIDE_TOTAL_BUDGET_MS = 300L

        /**
         * T02222：最近一次读屏的方向判定来源统计，供 IME 侧提示「方向存疑」。
         *
         * [byColor] 由气泡颜色决定（高置信）；其余按 T01143 左缘位置回退（深色主题、
         * 图片/语音气泡、取样落到字形或页底等），存在误判可能，须让用户看到。
         * [noEvidence] 是其中最可疑的一类：环带 30 点连一张气泡都没探到（图内文字的
         * 典型形态），T02305 起这类行禁止由位置回退断言「我」。
         */
        data class SideStats(
            val judged: Int = 0,
            val byColor: Int = 0,
            val noEvidence: Int = 0,
        ) {
            val byPosition: Int get() = judged - byColor

            /** 全部方向判定都由位置兜底时才提示——有一条颜色证据就不必打扰用户。 */
            val uncertain: Boolean get() = judged > 0 && byColor == 0

            operator fun plus(o: SideStats) =
                SideStats(judged + o.judged, byColor + o.byColor, noEvidence + o.noEvidence)
        }

        @Volatile
        private var lastSideStats: SideStats? = null

        /** 最近一次读屏的方向判定统计（多屏为各屏累计；无读屏记录时为 null）。 */
        fun sideStats(): SideStats? = lastSideStats

        /** 屏幕宽度（px）：侧别比例判定基准；服务未连接时回落 1440（标定设备）。 */
        private fun screenWidthPx(): Int =
            instance?.resources?.displayMetrics?.widthPixels ?: 1440

        /** T01143：本人气泡起点左缘比例（px/屏宽）——微信 1440 宽实测对方气泡 left≈250
         *  （0.174）、本人长气泡可向左延伸到 ~500（0.347），取中点 450/1440 留余量。 */
        private const val SELF_SIDE_LEFT_RATIO = 450f / 1440f

        /** 头像字形中心 x 上限（px）：对方头像区字形（单字+矮行+最左侧）才判为头像。 */
        private const val AVATAR_CX_MAX_PX = 200

        /** 换行拆行合并阈值（T01134）：判定用「行间空白 / 行高」的相对比例，不用绝对像素——
         *  绝对像素随屏幕密度与字号漂移（SM-G9980 density 3.5 实测折行空白约 30px，旧的 26px
         *  常量直接漏判，同一句话被拆成两条独立消息，下游 current 只取到半句）。
         *  同 block 是 ML Kit 已给出的「同一视觉块」信号，可放宽；跨 block 收紧以防误并相邻消息。
         *
         *  T01136：跨 block 阈值 0.6 → 0.8（TIM 实测同一句话跨 block 的 ratio=0.64 漏合并）；
         *  再加「水平对齐」信号：两行左缘（或右缘）近似对齐即判定为同一气泡的折行，
         *  此时按同 block 的宽松阈值处理——同气泡折行一定左右对齐，相邻消息则错位。 */
        private const val WRAP_MERGE_RATIO_SAME_BLOCK = 1.0f
        private const val WRAP_MERGE_RATIO_ALIGNED = 1.0f
        private const val WRAP_MERGE_RATIO_CROSS_BLOCK = 0.8f
        /** 「左右缘近似对齐」容差（px）：TIM 实测同气泡折行 l=252 vs 250（差 2px）。 */
        private const val ALIGN_TOLERANCE_PX = 24

        // ===== T01124：截图 OCR 路径（微信节点树被保护时的 fallback）=====
        // 引擎：ML Kit 中文识别 bundled 版（模型打进 APK，不依赖 GMS，国行设备可用）。
        // 边界：仅在用户点击读屏时执行一次；Bitmap 仅内存处理，识别完立即 recycle，
        // 不落盘、不外传（FR-6.1/6.2 零留存）。微信对像素级截图无从反制。

        @Volatile
        private var recognizer: TextRecognizer? = null
        private val recognizerLock = Any()

        private class OcrLineInfo(
            var text: String,
            var left: Int,
            var right: Int,
            var top: Int,
            var bottom: Int,
            val block: Int = 0,
        ) {
            val cx get() = (left + right) / 2
            val height get() = bottom - top
        }

        /**
         * 截图 + 端侧 OCR 读取当前屏幕聊天文本（节点树路径的 fallback）。
         * 调用方须在后台线程调用（截图等待 + OCR 识别共约 1-3 秒）。
         *
         * T01124 边界细化：
         * - 换行合并（T01134）：行间空白 < 行高 × 比例（同 block 1.0 / 跨 block 0.6）→ 合并为
         *   一条消息。用相对比例而非绝对像素，见 [WRAP_MERGE_RATIO_SAME_BLOCK] 注释；
         * - 气泡侧别：T01150 起统一用 [isMine]（左缘判定），与节点路径、m1-poc judge_side
         *   同构（旧的右缘阈值对长气泡失效，实测对方长气泡右缘 1163 被误判「我」）；
         *   T01129：不滤除，改为「我：」/「对方：」标注保留（生成层感知完整双方上下文）；
         * - 头像字形：单字符 && 行高<60 && 中心 x<[AVATAR_CX_MAX_PX]（最左头像区），
         *   不误杀对方单字消息（其气泡文本起于头像右侧 x≈160+）。
         */
        fun captureViaScreenshot(maxCount: Int = 20): List<String> {
            val svc = instance ?: return emptyList()
            if (Build.VERSION.SDK_INT < MIN_OCR_API) {
                Log.i(TAG, "ocr: api ${Build.VERSION.SDK_INT} < $MIN_OCR_API, unsupported")
                return emptyList()
            }
            Log.i(TAG, "ocr: start")
            val t0 = System.currentTimeMillis()
            val full = screenshotBitmap(svc) ?: return emptyList()
            val cropped = cropForOcr(svc, full)
            full.recycle() // crop 产出独立 bitmap，原图立即回收
            if (cropped == null) {
                Log.w(TAG, "ocr: aborted (IME covering the chat area)")
                return emptyList() // 由 IME 侧给出「请收起面板重试」的明确提示
            }
            val raw = runCatching { ocrLines(cropped) }
                .onFailure { Log.e(TAG, "ocr: recognize failed", it) }
                .getOrElse { emptyList() }
            // T01334：OCR 路径剔除「图内文字」行簇（节点树拿不到 ImageView，用环带像素判据）
            // T02305：判据从「无标点行占比」换成簇外围环带的图片内容占比——词法判据在
            // 银行/表单类截图上必然漏网，漏网行再被截图自身颜色判成「我：」。
            // T02305 真机复测补正：簇过滤必须在**折行合并之前**跑在原始行上。合并会把
            // 密集图内文字撑成少数高行（实测行距 32~58px > 行高×0.35），簇直接凑不起来，
            // 于是银行截图正文整批漏网。顺序：剔簇(raw) → 合并 → 剔噪。
            // T01339：再剔除媒体占位符/角标叠字/符号乱码行（视频图片非纯文本不纳入识别）
            val merged = filterNonTextNoise(
                mergeWrappedLines(filterImageTextClusters(cropped, raw)),
            )
            // T01343：侧别比例判定的屏幕宽度基准（一次计算，序内复用）
            val sw = screenWidthPx()
            // 诊断：行级明细（真机核对侧别/合并阈值用）
            merged.take(15).forEach {
                Log.i(TAG, "ocr: line t=${it.top} b=${it.bottom} l=${it.left} r=${it.right} \"${it.text.take(24)}\"")
            }
            // T01138 反馈：群聊噪声过滤（判侧前执行）——UI 悬浮条「N条新消息」
            // 被右侧坐标误判为「我：」、表情图标误读「(田+0)」、发送者昵称行被当独立消息
            val groupFiltered = filterGroupNoise(merged)
            // T02222：气泡颜色判侧（截图路径才有像素；仅微信）。[cropped] 因此
            // 延后到判侧结束才 recycle——判据本身只读像素，不改图。
            val sides = judgeSides(cropped, groupFiltered, sw)
            // T01129：全部保留并加侧别标注（自己行「我：」、对方行「对方：」）；
            // 频次过滤按侧别分组（同一文本对方发 3 次才滤，双方不互算）
            val freqMine = groupFiltered.filter { sides[it]!!.side == BubbleSideJudge.SIDE_MINE }
                .groupingBy { it.text }.eachCount()
            val freqOther = groupFiltered.filter { sides[it]!!.side != BubbleSideJudge.SIDE_MINE }
                .groupingBy { it.text }.eachCount()
            val result = groupFiltered.asSequence()
                // T01343：时间行转「话题分段标记」保留（与节点路径同构）
                // T02224：居中门槛——气泡里的「通话时长10:52」不是分隔行，不该抢走方向标注
                .filter { l -> isMessageText(l.text) || isTimeMark(l.text.trim(), l.cx, sw) }
                .filter { l ->
                    val mine = sides[l]!!.side == BubbleSideJudge.SIDE_MINE
                    val n = (if (mine) freqMine else freqOther)[l.text] ?: 0
                    !(l.text.length <= 10 && n >= 3)
                }
                .filter { l -> !(l.text.length == 1 && l.height < 60 && l.cx < AVATAR_CX_MAX_PX) } // 头像字形
                .map {
                    val v = it.text.trim()
                    val prefix = when {
                        isTimeMark(v, it.cx, sw) -> TIME_MARK_PREFIX
                        sides[it]!!.side == BubbleSideJudge.SIDE_MINE -> SPEAKER_ME_PREFIX
                        else -> SPEAKER_OTHER_PREFIX
                    }
                    prefix + v
                }
                .toList()
                .takeLast(maxCount)
            cropped.recycle()
            Log.i(TAG, "ocr: raw=${raw.size} merged=${merged.size} result=${result.size} cost=${System.currentTimeMillis() - t0}ms")
            return result
        }

        /**
         * T02222：对截图 OCR 的每行判侧（颜色主判据 + 位置回退），返回行 → 判侧结果。
         *
         * 同一行对象在频次统计与前缀标注里被多次引用，这里一次性算完存进 identity
         * map（[OcrLineInfo] 未覆写 equals/hashCode，天然按引用相等），既保证
         * 「像素只取一遍」，也让频次分组与最终标注口径一致。
         *
         * 耗时红线（任务要求单次判侧 ≤50ms）：逐行计时并打日志；整屏累计超过
         * [SIDE_TOTAL_BUDGET_MS] 后剩余行直接走位置回退，宁缺勿慢。
         */
        private fun judgeSides(
            bmp: Bitmap,
            lines: List<OcrLineInfo>,
            sw: Int,
        ): Map<OcrLineInfo, BubbleSideJudge.SideCall> {
            val t0 = System.currentTimeMillis()
            // 仅微信：其他平台气泡色各异（钉钉蓝、QQ 白/绿混合），颜色规则反而拉低准确率
            val colorAvailable = isWechatWindow()
            val sides = HashMap<OcrLineInfo, BubbleSideJudge.SideCall>(lines.size)
            var budgetLeft = SIDE_TOTAL_BUDGET_MS
            var slowest = 0L
            lines.forEach { l ->
                val begin = System.currentTimeMillis()
                val call = BubbleSideJudge.judge(
                    getPixel = { x, y -> bmp.getPixel(x, y) },
                    box = BubbleSideJudge.ProbeBox(l.left, l.right, l.top, l.bottom),
                    width = bmp.width,
                    height = bmp.height,
                    positionSide = if (isMine(l.left, sw)) BubbleSideJudge.SIDE_MINE
                    else BubbleSideJudge.SIDE_OTHER,
                    colorAvailable = colorAvailable && budgetLeft > 0,
                )
                val cost = System.currentTimeMillis() - begin
                budgetLeft -= cost
                slowest = maxOf(slowest, cost)
                if (cost > SIDE_BUDGET_MS) {
                    Log.w(TAG, "side: over budget cost=${cost}ms \"${l.text.take(16)}\"")
                }
                sides[l] = call
            }
            val dirLines = lines.filterNot { isTimeMark(it.text.trim(), it.cx, sw) }
            val byColor = dirLines.count { sides[it]!!.byColor }
            val noEvidence = dirLines.count {
                val c = sides[it]!!
                !c.byColor && c.greenVotes == 0 && c.otherVotes == 0
            }
            // 多屏读屏按屏累计（captureMultiScreen 开始时清零），单屏读屏即本次结果。
            lastSideStats = (lastSideStats ?: SideStats()) +
                SideStats(dirLines.size, byColor, noEvidence)
            Log.i(
                TAG,
                "side: wechat=$colorAvailable lines=${lines.size} color=$byColor " +
                    "position=${dirLines.size - byColor} noev=$noEvidence " +
                    "total=${System.currentTimeMillis() - t0}ms " +
                    "slowest=${slowest}ms",
            )
            return sides
        }

        /** 当前前台是否有微信应用窗（气泡颜色规则的适用门槛）。 */
        private fun isWechatWindow(): Boolean = runCatching {
            instance?.windows?.any { w -> w.root?.packageName == WECHAT_PKG } == true
        }.getOrDefault(false)

        /**
         * T01138 反馈：群聊读屏噪声过滤（纯文本规则，与 m1-poc preprocess.strip_group_noise 同构）。
         * 1) UI 悬浮条「N条新消息」——屏幕右侧，会被侧别判定误标为「我：」；
         * 2) 表情/图标误读行（整行括号包裹短内容，如「(田+0)」）；
         * 3) 发送者昵称行：群聊结构=昵称小字行+气泡正文，OCR 后昵称成独立行（对象错误）——
         *    特征=短行（≤12字）无标点且下一行以其前 2+ 字开头 → 删除昵称行。
         */
        private fun filterGroupNoise(lines: List<OcrLineInfo>): List<OcrLineInfo> {
            val out = mutableListOf<OcrLineInfo>()
            val newTexts = stripGroupNoiseTexts(lines.map { it.text })
            var idx = 0
            for (l in lines) {
                if (idx < newTexts.size && l.text == newTexts[idx]) {
                    out.add(l)
                    idx++
                }
            }
            return out
        }

        /** 群聊噪声过滤的文本判定（与 py 逐条对应）。 */
        private fun stripGroupNoiseTexts(raw: List<String>): List<String> {
            val newMsg = Regex("""\d+\s*条新消息""")
            val bracketIcon = Regex("""^\s*[（(].{0,6}[）)]\s*$""")
            val hasPunct = Regex("""[，。！？、：；,\.!?:;]""")
            val out = mutableListOf<String>()
            var i = 0
            while (i < raw.size) {
                val cur = raw[i].trim()
                val nxt = raw.getOrNull(i + 1)?.trim() ?: ""
                if (cur.isEmpty() || newMsg.containsMatchIn(cur) || bracketIcon.matches(cur)) {
                    i++
                    continue
                }
                if (cur.length <= 12 && nxt.isNotEmpty() && !hasPunct.containsMatchIn(cur) &&
                    nxt.startsWith(cur.take(2))
                ) {
                    i++
                    continue
                }
                out.add(cur)
                i++
            }
            return out
        }

        /**
         * T01145：多屏读屏——当前屏 + 上滑 [extraScreens] 次（每次截图 OCR），
         * 最后滑回原位，批次按时间先后合并去重（与 m1-poc merge_screen_batches 同构）。
         * 滑动用 AccessibilityService.dispatchGesture（仅用户点击读屏时执行，
         * 一次点击授权整个读屏序列）。调用方须在后台线程调用。
         * T01291 用户反馈简化：聊天默认停在最下方（最新消息），无需再向下翻找，
         * 默认只向上拉一屏（extraScreens 2→1），缩短读屏耗时。
         */
        fun captureMultiScreen(maxCount: Int = 20, extraScreens: Int = 1): List<String> {
            lastSideStats = SideStats() // T02222：多屏判侧统计按屏累计，本次从零开始
            val batches = mutableListOf<List<String>>()
            // T01312：总屏数=当前屏+上滑屏数；即使某次上滑失败也按计划屏数上报，
            // 保证 UI 上的分母稳定（失败那一屏不产生 index 递增，文案会停在上一屏）。
            val total = extraScreens + 1
            batches.add(captureViaScreenshot(maxCount = 60))
            sendReadProgress(1, total)
            repeat(extraScreens) {
                if (swipeUp()) {
                    Thread.sleep(SWIPE_SETTLE_MS)
                    batches.add(captureViaScreenshot(maxCount = 60))
                    sendReadProgress(batches.size, total)
                }
            }
            repeat(extraScreens) { // 滑回原位，恢复聊天滚动位置
                swipeDown()
                Thread.sleep(SWIPE_SETTLE_MS)
            }
            val merged = mergeScreenBatches(batches)
            Log.i(TAG, "ocr: multiscreen batches=${batches.size} merged=${merged.size}")
            return merged.takeLast(maxCount)
        }

        /** 上滑一屏（看更早的消息）；失败返回 false。 */
        private fun swipeUp(): Boolean =
            swipeGesture(
                startX = 720f, startY = 2400f, endX = 720f, endY = 900f, durationMs = 220L,
            )

        /** 下滑一屏（恢复原位）。 */
        private fun swipeDown(): Boolean =
            swipeGesture(
                startX = 720f, startY = 900f, endX = 720f, endY = 2400f, durationMs = 220L,
            )

        private fun swipeGesture(
            startX: Float, startY: Float, endX: Float, endY: Float, durationMs: Long,
        ): Boolean {
            val svc = instance ?: return false
            if (Build.VERSION.SDK_INT < 24) return false
            val path = android.graphics.Path().apply {
                moveTo(startX, startY)
                lineTo(endX, endY)
            }
            val gesture = android.accessibilityservice.GestureDescription.Builder()
                .addStroke(
                    android.accessibilityservice.GestureDescription.StrokeDescription(
                        path, 0, durationMs,
                    ),
                ).build()
            val latch = java.util.concurrent.CountDownLatch(1)
            var ok = false
            svc.dispatchGesture(gesture, object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(g: GestureDescription) {
                    ok = true
                    latch.countDown()
                }

                override fun onCancelled(g: GestureDescription) {
                    latch.countDown()
                }
            }, null)
            if (!latch.await(3, TimeUnit.SECONDS)) {
                Log.w(TAG, "dispatch 手势 3s 内未完成回调")
            }
            return ok
        }

        /** 批次合并去重：顺序拼接 + 整行去重（保留首次出现），与 py merge_screen_batches 同构。 */
        private fun mergeScreenBatches(batches: List<List<String>>): List<String> {
            val out = mutableListOf<String>()
            val seen = mutableSetOf<String>()
            for (batch in batches) {
                for (line in batch) {
                    val t = line.trim()
                    if (t.isNotEmpty() && seen.add(t)) out.add(t)
                }
            }
            return out
        }

        /** 换行拆行合并：行间空白 < 行高 × 比例 → 同一条消息的折行（T01134）。
         *  比例按是否同 block 取不同值（同 block 放宽、跨 block 收紧），理由见常量注释。
         *  [last] 记录上一「原始行」（算行间空白用），[out] 末元素是合并累积行（拼接文本/并边界用）。 */
        private fun mergeWrappedLines(lines: List<OcrLineInfo>): List<OcrLineInfo> {
            val out = mutableListOf<OcrLineInfo>()
            var last: OcrLineInfo? = null
            lines.sortedBy { it.top }.forEach { l ->
                val prev = last
                if (out.isNotEmpty() && prev != null) {
                    val gap = l.top - prev.bottom
                    val lineH = maxOf(l.height, prev.height, 1)
                    val ratio = when {
                        l.block == prev.block -> WRAP_MERGE_RATIO_SAME_BLOCK
                        isAligned(prev, l) -> WRAP_MERGE_RATIO_ALIGNED
                        else -> WRAP_MERGE_RATIO_CROSS_BLOCK
                    }
                    if (gap < lineH * ratio) {
                        val merged = out.last()
                        merged.text = joinWrapped(merged.text, l.text)
                        merged.left = minOf(merged.left, l.left)
                        merged.right = maxOf(merged.right, l.right)
                        merged.bottom = maxOf(merged.bottom, l.bottom)
                        last = l
                        return@forEach
                    }
                }
                out.add(OcrLineInfo(l.text, l.left, l.right, l.top, l.bottom, l.block))
                last = l
            }
            return out
        }

        /** T01136：同气泡折行的水平对齐信号——左缘或右缘近似对齐（<= [ALIGN_TOLERANCE_PX]）。 */
        private fun isAligned(prev: OcrLineInfo, next: OcrLineInfo): Boolean =
            kotlin.math.abs(prev.left - next.left) <= ALIGN_TOLERANCE_PX ||
                kotlin.math.abs(prev.right - next.right) <= ALIGN_TOLERANCE_PX

        /** 折行拼接：中文交界直接相连，ASCII 字母数字交界补一个空格（英文断词场景）。 */
        private fun joinWrapped(prev: String, nxt: String): String {
            if (prev.isEmpty()) return nxt
            if (nxt.isEmpty()) return prev
            val a = prev.last()
            val b = nxt.first()
            return if (a.code < 128 && a.isLetterOrDigit() && b.code < 128 && b.isLetterOrDigit()) {
                "$prev $nxt"
            } else {
                prev + nxt
            }
        }

        /** takeScreenshot 异步转同步；失败返回 null。 */
        private fun screenshotBitmap(svc: AccessibilityService): Bitmap? {
            val latch = CountDownLatch(1)
            var out: Bitmap? = null
            try {
                svc.takeScreenshot(
                    Display.DEFAULT_DISPLAY,
                    { r -> r.run() },
                    object : AccessibilityService.TakeScreenshotCallback {
                        override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                            try {
                                val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                                out = hw?.copy(Bitmap.Config.ARGB_8888, false)
                                hw?.recycle()
                            } catch (t: Throwable) {
                                Log.e(TAG, "ocr: wrap fail", t)
                            } finally {
                                result.hardwareBuffer.close()
                                latch.countDown()
                            }
                        }

                        override fun onFailure(errorCode: Int) {
                            Log.e(TAG, "ocr: screenshot error=$errorCode")
                            latch.countDown()
                        }
                    },
                )
            } catch (t: Throwable) {
                Log.e(TAG, "ocr: takeScreenshot throw", t)
                return null
            }
            if (!latch.await(5, TimeUnit.SECONDS)) {
                Log.e(TAG, "ocr: screenshot timeout")
                return null
            }
            return out
        }

        /**
         * 裁掉标题栏区与底部 IME/输入框功能区，只留聊天消息带。
         *
         * T01135：IME 展开态（手动面板 + 候选列表占位）会把聊天带压到几十像素，
         * OCR 读不到内容却静默返回空。裁剪高度低于 [MIN_CHAT_BAND_PX] 时直接返回
         * null，由调用方给出明确提示（而不是让用户白等一轮 OCR）。
         */
        private fun cropForOcr(svc: AccessibilityService, src: Bitmap): Bitmap? {
            val top = TOPBAR_MAX_PX
            val bottom = cropBottomPx(svc, src.height)
            val h = bottom - top
            if (h < MIN_CHAT_BAND_PX) {
                Log.w(TAG, "ocr: chat band too small h=$h (IME covering?)")
                return null
            }
            return Bitmap.createBitmap(src, 0, top, src.width, h)
        }

        /**
         * 裁剪下界：输入法上沿（IME 可见时）与底部系统栏上沿，取更靠上者。
         *
         * 读屏前 IME 被临时收起（T01135），输入法窗口随之从 [AccessibilityService.getWindows]
         * 消失——只按输入法定位时下界会落到整屏底部，把应用自身输入栏与系统导航栏一并裁进
         * OCR 区。导航栏图标会被 ML Kit 误读成文字（SM-G9980 实测三竖条「❙❙❙」→「三」、
         * 方框「□」→「()」），再经折行合并拼成「()三」，凭空多出一条「对方：()三」。
         *
         * 用窗口而非硬编码高度定位：导航栏在 SM-G9980 上是 Rect(0,3032-1440,3200)，
         * 换成手势导航/异形屏时高度不同，硬编码会失效。
         */
        private fun cropBottomPx(svc: AccessibilityService, srcHeight: Int): Int {
            var bottom = srcHeight
            runCatching {
                svc.windows.forEach { w ->
                    val r = Rect()
                    w.root?.getBoundsInScreen(r)
                    if (r.height() <= 0) return@forEach
                    if (w.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                        // 输入法面板自身的内容（读屏结果/候选列表）不得进 OCR 区
                        bottom = minOf(bottom, r.top - IME_ROW_MARGIN_PX)
                        return@forEach
                    }
                    // 底部系统栏：位于屏幕下四分之一且矮于屏高 1/6。状态栏（top=0）、
                    // 应用全屏窗（高=屏高）、悬浮球等均不满足，不会误判。
                    val isBottomBar = r.top >= srcHeight * 3 / 4 && r.height() <= srcHeight / 6
                    if (isBottomBar) bottom = minOf(bottom, r.top)
                }
            }
            Log.i(TAG, "ocr: crop bottom=$bottom screen=$srcHeight")
            return bottom
        }

        /** 复用进程内识别器（bundled 模型，无需下载）。 */
        private fun ensureRecognizer(): TextRecognizer =
            synchronized(recognizerLock) {
                recognizer
                    ?: TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
                        .also { recognizer = it }
            }

        /** ML Kit 整页识别，返回行级文本+坐标（识别器自带行聚合）。 */
        private fun ocrLines(bmp: Bitmap): List<OcrLineInfo> {
            val rec = ensureRecognizer()
            val latch = CountDownLatch(1)
            val out = mutableListOf<OcrLineInfo>()
            rec.process(InputImage.fromBitmap(bmp, 0))
                .addOnSuccessListener { visionText ->
                    visionText.textBlocks.forEachIndexed { bi, block ->
                        block.lines.forEach { line ->
                            val bb = line.boundingBox ?: return@forEach
                            // ML Kit 的 line 可能覆盖气泡内多个视觉折行（text 含 \n），直接拼接
                            val t = line.text.replace("\n", "").trim()
                            if (t.isNotBlank()) out.add(OcrLineInfo(t, bb.left, bb.right, bb.top, bb.bottom, bi))
                        }
                    }
                }
                .addOnFailureListener { Log.e(TAG, "ocr: mlkit fail", it) }
                .addOnCompleteListener { latch.countDown() }
            if (!latch.await(15, TimeUnit.SECONDS)) {
                Log.w(TAG, "ocr: mlkit 15s 内未完成，按已识别内容返回")
            }
            return out
        }
    }
}
