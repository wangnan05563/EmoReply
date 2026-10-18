package com.emoreply.poc.ime

import android.content.BroadcastReceiver
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.inputmethodservice.InputMethodService
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.OvershootInterpolator
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.emoreply.poc.R
import com.emoreply.poc.capture.EmoReplyReaderService
import com.emoreply.poc.core.Adaptive
import com.emoreply.poc.core.PipelineOutcome
import com.emoreply.poc.core.ReplyPipeline
import com.emoreply.poc.core.Tone
import com.emoreply.poc.core.TextAction
import com.emoreply.poc.core.UxTheme
import com.emoreply.poc.core.buildTextActionPrompt
import com.emoreply.poc.core.expandCandidates
import com.emoreply.poc.core.splitRationale
import com.emoreply.poc.core.tagRationale
import com.emoreply.poc.core.uxTheme
import com.emoreply.poc.core.OPENING_SCENARIOS
import com.emoreply.poc.generator.CloudLlmGenerator
import com.emoreply.poc.generator.GeneratorSettings
import com.emoreply.poc.generator.MediaPipeOnDeviceGenerator
import com.emoreply.poc.generator.StubGenerator
import com.emoreply.poc.model.Candidate
import com.emoreply.poc.model.Notification as EmoNotification
import com.emoreply.poc.store.FeedbackStore
import com.emoreply.poc.store.FavoritesStore
import com.emoreply.poc.store.KnowledgeBase
import com.emoreply.poc.store.PersonaStore
import com.emoreply.poc.store.ReplyStore
import com.emoreply.poc.store.TemplateStore
import com.emoreply.poc.store.UxSettings

/**
 * EmoReply 输入法（FR-4.2）。
 *
 * T01082【验证失败】反馈处置后的形态：
 * - 候选纵向排列 + 滚动（反馈②）：候选不再并排挤压，逐条上下排列，超出高度（约 230dp）
 *   在 ScrollView 内滚动选择；
 * - 手动生成（反馈①）：微信前台时不产生系统通知（机制性限制，通知监听链路依赖系统通知），
 *   用户在微信内复制消息后，于本面板粘贴并点「生成候选」，走与通知链路完全相同的
 *   云端/桩管线（情绪识别/人设/知识库/护栏全保留）；连续生成自动累积本会话上下文（多轮）；
 * - 切换输入法（反馈③，T01105 拍板：自研键盘不做）：点「⌨ 输入法」展开已启用输入法
 *   列表，点击任一输入法直接切换（switchInputMethod），比系统选择器少一层；保留
 *   「系统选择器…」兜底（个别 ROM 限制直接切换时用）。注入候选后切回专业输入法继续编辑。
 *
 * 保留能力：点选注入 commitText、解释句 toast（FR-3.3）、采纳/忽略计数（FR-4.3）、
 * 长按候选另存为模板 + 模板条快速点选（T01082 步骤 6）。
 *
 * T01127 功能整合（简化固化）：「简化生成」固化为默认工作流（独立开关移除）；📋粘贴 与
 * 💬生成候选 并入 ✨「手动生成」（读剪贴板→填入→生成候选一体，长按 ✨ 展开面板手动编辑）；
 * 👁 读屏成功后自动生成候选。iconRow 7→5：✨ ⌨ ↩ 👁 🧹。
 *
 * T01128 布局精简：顶部汇总栏（状态行）移除；输入区改左三右一——左侧 75% 主输入框、
 * 右侧 25% 汇总展示（承接原顶部汇总栏的上下文/候选/读屏/生成状态文本）。
 *
 * T01129 读屏串行对话解析：读屏行带「我：/对方：」侧别标注（截图 OCR 路径按气泡右缘
 * 判定）；串行链路=读屏 → 解析成功才生成（readingScreen 防并发、解析失败明确提示）；
 * current 取最后一条对方发言，history 保留双方 sender，提示词渲染完整对话上下文。
 *
 * T01130 输入框美化：🖌 美化按钮（iconRow 6 图标）——LLM 润色输入框文本并回填，
 * 硬约束=语义不变/不增不删/语言一致/语气保留，空输入提示中止、失败保留原文。
 * T01131 候选区左三右一：左侧候选文本唯一可点击，右侧理由小字灰色不可点击（常驻展示，
 * 替代原点选 Toast 呈现）；无理由时候选独占整行。
 * T01132 文案精简：左侧输入框 hint 简化为「消息内容」；右侧汇总格默认态简化为「暂无待回复消息」。
 * T01133 输入法一键直切：⌨ 点击直接切到目标输入法（上次切过的 → 唯一的其他输入法），
 * 不再展开列表二次选择；多个候选且无记忆时展开一次并记住，之后同样一步直切。
 * T01138 输入区双栏：左侧 75% 区内再分左右均分两列（各 50%），按行从左到右顺序填充
 * （展示态双栏 / 获得焦点编辑时切回整宽 EditText）——数据源与生成逻辑零变化。
 */
class EmoReplyImeService : InputMethodService() {

    // T01128：顶部汇总栏（状态行）移除，汇总文本改由手动面板右侧格（输入框右 1/4）承接
    private var summaryView: TextView? = null
    private var candidateScroll: ScrollView? = null
    private var candidateCol: LinearLayout? = null
    private var templateRow: LinearLayout? = null
    private var manualPanel: LinearLayout? = null
    private var imePanel: LinearLayout? = null
    // T01536：inputRow 由构建期局部变量升为字段——精简模式可见性需在每次键盘显示重挂，
    // 旧实现只在 onCreateInputView 读一次标志，开关切 ON 后重开键盘/整屏扩展形态均不生效。
    private var inputRow: LinearLayout? = null
    private var manualInput: EditText? = null
    // T01112：最近一次 commit 注入的字符数（撤回一次有效；onStartInputView 重置防跨输入框误删）
    private var lastCommittedLen = 0
    // T01122：图标化 UI——一排无框图标（Remix Icon），生成中用透明度表达
    // T01127：粘贴/生成候选并入 ✨「手动生成」，生成中反馈落在 ✨ 上
    private var iconGeneratePanel: ImageView? = null
    // T01138：输入区双栏展示（展示态均分两栏 / 编辑态整宽 EditText）
    private var displayRow: LinearLayout? = null
    private var leftPane: TextView? = null
    private var rightPane: TextView? = null

    // T01135：IME 根视图（判高度是否遮挡聊天内容用）
    private var imeRootView: View? = null
    // T01130：输入框文字美化按钮（生成中半透明反馈）
    private var iconBeautify: ImageView? = null

    // T01158 FR-9.1：当前配色（亮/暗+AMOLED），键盘视图重绘时统一取用
    private var ux = UxTheme.light()
    private val iconButtons = mutableListOf<ImageView>()
    // T01158 FR-7.1：换一批——底部按钮容器 + 上次生成请求（重跑用）
    private var regenerateRow: LinearLayout? = null
    private var lastManualRequest: Pair<Pair<String, String>, List<Pair<String, String>>>? = null
    // T01260 FR-8.6：再互动建议——超 48h 未回复时显示「轻量回访」一键生成入口
    private var revisitRow: LinearLayout? = null

    private val mainHandler = Handler(Looper.getMainLooper())

    // 手动生成会话上下文：连续「生成候选」自动累积（与通知链路 history 同构，多轮）
    private val manualHistory = mutableListOf<EmoNotification>()

    // 最近一次手动生成结果：键盘重开时（ReplyStore 无待回复）仍可回看/点选
    @Volatile
    private var lastManualCandidates: List<Candidate>? = null
    @Volatile
    private var lastManualNotice: String? = null
    @Volatile
    private var generating = false
    // T01257 FR-7.6：最近一次候选呈现的来源（分应用分布用）与呈现时刻（「生成→采纳」耗时用）
    @Volatile
    private var lastShownSource: String? = null
    @Volatile
    private var lastShownAtMs: Long = 0L

    // 当前展示中的通知待回复（createdAt 标识）；adopted 标记是否已点选，用于忽略统计
    private var shownAt: Long? = null
    private var shownStyle: String? = null
    private var adopted = true

    // T01350：生成场景维度（关系/tone/风格）——生成时暂存、展示时同步，采纳/忽略时入三维统计
    private var lastGenDim: Triple<String, Int, String>? = null
    private var shownDim: Triple<String, Int, String>? = null

    // T01129：读屏进行中标志——串行防护，读屏未完成前重复点击不进入第二次读屏
    @Volatile
    private var readingScreen = false

    /** 禁用全屏抽取模式：手动面板的 EditText 不应把整个输入法顶成全屏编辑器。 */
    override fun onEvaluateFullscreenMode(): Boolean = false

    override fun onCreateInputView(): View {
        ux = uxTheme(this) // T01158 FR-9.1：构建前先定配色（暗色/AMOLED）
        iconButtons.clear() // 键盘视图可能重建，避免图标集无限增长
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ux.bg)
            setPadding(dp(12), dp(8), dp(12), dp(8))
        }
        // T01128：顶部汇总栏移除；汇总文本由手动面板内右侧格（输入框右 1/4）承接
        summaryView = TextView(this).apply {
            textSize = 10f
            setTextColor(ux.textSecondary)
            gravity = android.view.Gravity.CENTER_VERTICAL
        }

        // T01122：动作区改为一排无框图标（钉钉灵动回复风格，Remix Icon，MIT）——
        // 无边框、无文字标签、水平一排等间距；点击有缩放弹回 + 颜色过渡动画
        val iconRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        // T01249 FR-9.2：Smartbar 化——主行只放 ≤4 个高频图标，低频功能收进第二行（溢出区）：
        // 「···」按钮点击切换第二行可见性，全部功能入口仍可达。
        val overflowRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            // 默认收起；点击「···」再展开，避免低频图标常驻挤占主行空间
            visibility = View.GONE
        }
        fun addIcon(
            target: LinearLayout,
            res: Int,
            desc: String,
            weight: Float = 1f,
            onClick: () -> Unit,
        ): ImageView =
            iconButton(res, desc).apply {
                setOnClickListener { onClick() }
                target.addView(
                    this,
                    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, weight),
                )
            }

        // T01127：iconRow 7→5——📋粘贴、💬生成候选 并入 ✨「手动生成」（读剪贴板→填入→生成
        // 一体承载）；👁 读屏后自动生成候选，无需再点生成
        addIcon(iconRow, R.drawable.ic_gen_sparkling, "手动生成（读剪贴板并生成候选）") {
            // T01127：简化生成固化为默认——✨ 一键「读剪贴板→填入→生成候选」；长按展开面板手动编辑
            simplifiedGenerate()
        }.also {
            iconGeneratePanel = it
            it.setOnLongClickListener { openOpeningDialog(); true }
        }
        // 主行 4 个高频 + 「···」= 最高频入口；美化/清空属低频，收进溢出区第二行
        addIcon(iconRow, R.drawable.ic_gen_keyboard, "切换输入法") { toggleImePanel() }
        addIcon(iconRow, R.drawable.ic_gen_undo, "撤回上次注入") { undoLastCommit() }
        addIcon(iconRow, R.drawable.ic_gen_eye, "读屏并生成") { ensureManualPanel(); readFromScreen() }
        // T01249：「···」溢出入口——点击切换第二行（美化/清空）的可见性
        addIcon(iconRow, R.drawable.ic_gen_more, "更多功能") { toggleOverflowRow(overflowRow) }
        // 溢出区第二行：低频功能收纳于此，功能入口不丢
        // T01261 FR-8.7：长按「美化」弹出文本动作族菜单（改写/总结/翻译/扩写），点击仍为改写
        addIcon(overflowRow, R.drawable.ic_gen_brush, "美化输入框文字（长按可选改写/总结/翻译/扩写）") { beautifyInput() }
            .also { iconBeautify = it }
            .setOnLongClickListener { hapticFeedback(); showTextActionMenu(); true } // T01258：长按 Haptic
        addIcon(overflowRow, R.drawable.ic_gen_clear, "清空会话") {
            manualInput?.setText("")
        refreshInputPane(editing = false) // T01138：填入后按双栏展示态渲染
            manualHistory.clear()
            lastManualCandidates = null
            lastManualNotice = null
            Toast.makeText(this@EmoReplyImeService, "已清空（新会话）", Toast.LENGTH_SHORT).show()
        }
        // T01537：逐联系人语气调节入口——Smartbar 低频区，对话框内说明优先级（联系人级覆盖全局默认）
        addIcon(overflowRow, R.drawable.ic_gen_tone, "按联系人调节语气（联系人级覆盖全局默认）") { showContactToneDialog() }

        // 输入法面板（T01105）：已启用输入法一键直切 + 系统选择器兜底
        imePanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }

        // 手动生成面板（T01122：动作按钮已上移至图标行，面板只保留输入框）
        manualPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }.also { panel ->
            // T01128：输入区左三右一——左侧 75% 主输入框 + 右侧 25% 汇总展示（承接原顶部汇总栏）
            // T01138：左侧 75% 内再分双栏——展示态左右均分（各 50%、按行从左到右顺序填充），
            // 获得焦点进入编辑态时切回整宽 EditText（编辑交互与数据流零变化）
            val inputRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
            }
            this@EmoReplyImeService.inputRow = inputRow // T01536：挂字段供键盘重显时重挂精简可见性
            val inputCol = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            displayRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setBackgroundColor(ux.cardBg)
                setPadding(dp(8), dp(8), dp(8), dp(8))
                visibility = View.GONE
                // 点展示区即回到编辑态
                setOnClickListener { manualInput?.requestFocus() }
            }
            leftPane = TextView(this).apply {
                textSize = 12f
                setTextColor(ux.textPrimary)
                setLineSpacing(2f, 1f)
            }
            rightPane = TextView(this).apply {
                textSize = 12f
                setTextColor(ux.textPrimary)
                setLineSpacing(2f, 1f)
            }
            displayRow?.addView(leftPane, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            displayRow?.addView(
                rightPane,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginStart = dp(8) },
            )
            manualInput = EditText(this).apply {
                hint = "消息内容"
                textSize = 13f
                minLines = 2
                maxLines = 4
                setSingleLine(false)
                setBackgroundColor(ux.cardBg)
                setTextColor(ux.textPrimary)
                setHintTextColor(ux.textSecondary)
                setPadding(dp(10), dp(8), dp(10), dp(8))
                setOnFocusChangeListener { _, hasFocus -> refreshInputPane(hasFocus) }
            }
            inputCol.addView(displayRow)
            inputCol.addView(
                manualInput,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ),
            )
            // T01147：精简模式——隐藏消息内容框与汇总提示框（图标行+候选区保留；
            // 读屏/✨ 生成逻辑不受影响，仅不展示输入区）
            // T01536：可见性改由 applyCompactMode() 统一挂——构建期与每次键盘显示各重读一次，
            // 修复「设置里切了开关，键盘不重建就永远不生效」的旧缺陷
            applyCompactMode()
            inputRow.addView(
                inputCol,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 3f),
            )
            inputRow.addView(
                summaryView,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginStart = dp(6) },
            )
            panel.addView(
                inputRow,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(8) },
            )
        }

        // 反馈②：候选纵向 + 限高滚动（约 230dp，约 4 条可见）
        candidateCol = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        candidateScroll = object : ScrollView(this) {
            override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
                super.onMeasure(
                    widthMeasureSpec,
                    View.MeasureSpec.makeMeasureSpec(dp(230), View.MeasureSpec.AT_MOST),
                )
            }
        }.apply { visibility = View.GONE }
        candidateScroll?.addView(candidateCol)

        // T01158 FR-7.1：换一批入口（候选区尾部；无候选或无上次生成请求时隐藏）
        regenerateRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER
            visibility = View.GONE
            setPadding(0, dp(6), 0, 0)
            addView(pill("换一批 ↻").apply { setOnClickListener { regenerate() } })
        }

        // T01260 FR-8.6：再互动建议入口——仅当该聊天最近消息超 48h 未回复时可见，
        // 点击一键生成「轻量回访」（低压力重启话术）；正常时限内整行隐藏不提示
        revisitRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER
            visibility = View.GONE
            setPadding(0, dp(6), 0, 0)
            addView(pill("轻量回访 ↩").apply { setOnClickListener { revisitGenerate() } })
        }

        root.addView(iconRow)
        root.addView(overflowRow)
        root.addView(imePanel)
        root.addView(manualPanel)
        root.addView(candidateScroll)
        root.addView(regenerateRow)
        root.addView(revisitRow)
        // 模板条（「另存为模板」快速点选区）
        templateRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        root.addView(templateRow)
        // T01111 诊断：打印 root 实际落点与按钮屏幕坐标（定位触摸派发断点）
        root.setOnTouchListener { v, e ->
            val loc = IntArray(2).also { v.getLocationOnScreen(it) }
            Log.i(TAG_IME, "rootTouch action=${e.action} raw=(${e.rawX},${e.rawY}) viewAt=(${loc[0]},${loc[1]}) size=${v.width}x${v.height}")
            false
        }
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            root.post { dumpLayout(root) }
        }
        root.post { dumpLayout(root) }
        imeRootView = root // T01135：读屏遮挡判定用其高度
        return root
    }

    private fun dumpLayout(root: LinearLayout) {
        fun bounds(v: View): String {
            val l = IntArray(2).also { v.getLocationOnScreen(it) }
            return "[${l[0]},${l[1]} ${v.width}x${v.height}]"
        }
        val sb = StringBuilder("layout: root=${bounds(root)}")
        for (i in 0 until root.childCount) {
            val c = root.getChildAt(i)
            if (c.visibility == View.VISIBLE && c.width > 0) sb.append(" child$i(${c.javaClass.simpleName})=${bounds(c)}")
        }
        // T01128：图标行现在是 root 第 1 个 child（顶部状态行已移除）
        (root.getChildAt(0) as? ViewGroup)?.let { row ->
            for (i in 0 until row.childCount) {
                sb.append(" icon$i=${bounds(row.getChildAt(i))}")
            }
        }
        Log.i(TAG_IME, sb.toString())
    }

    // ===== T01122：钉钉灵动回复风格图标（无框/无文字/一排/点击动画）=====

    /** 无框纯图标按钮：24dp Remix 图标 + 触摸按下缩小变色、抬起 overshoot 弹回。 */
    private fun iconButton(res: Int, desc: String): ImageView {
        val iv = ImageView(this)
        iv.setImageResource(res)
        iv.contentDescription = desc
        iv.setPadding(dp(11), dp(11), dp(11), dp(11))
        iv.scaleType = ImageView.ScaleType.FIT_CENTER
        iv.setColorFilter(ux.iconNormal) // T01158 FR-9.1：图标色随主题（原常量仅亮色）
        iconButtons.add(iv)
        iv.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    iv.setColorFilter(ux.iconActive)
                    iv.animate().scaleX(0.78f).scaleY(0.78f).setDuration(80).start()
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    iv.setColorFilter(ux.iconNormal)
                    iv.animate().scaleX(1f).scaleY(1f).setDuration(180)
                        .setInterpolator(OvershootInterpolator(2.5f)).start()
                }
            }
            false // 不消费：保证 onClick 正常触发
        }
        return iv
    }

    /** T01249 FR-9.2：点击「···」切换溢出区第二行的可见性（美化/清空等低频图标）。 */
    private fun toggleOverflowRow(row: LinearLayout) {
        // T01263 FR-9.4：溢出区展开/收起加 200ms 过渡，避免第二行硬跳变
        val showing = row.visibility != View.VISIBLE
        if (showing) {
            row.visibility = View.VISIBLE
            animateReveal(row)
        } else {
            animateCollapse(row)
        }
    }

    /** 手动面板未展开时先展开（粘贴/读屏/生成/清空四个图标的体验联动）。 */
    private fun ensureManualPanel() {
        val panel = manualPanel ?: return
        if (panel.visibility != View.VISIBLE) toggleManualPanel()
    }

    // ===== T01258 FR-7.7：触觉反馈 =====

    /**
     * 触觉反馈（候选采纳 / 注入成功 / 长按触发三处调用）。
     * 依 haptic_level 映射反馈常量：0=关、1=轻（VIRTUAL_KEY）、2=中（CONTEXT_CLICK）；
     * haptic_enabled 关闭则整体跳过。⚠ 真机 Haptic 强度观感未实测（本环境无真机），
     * 仅按档位选常量，强度差异有待真机确认。
     */
    private fun hapticFeedback() {
        if (!UxSettings.hapticEnabled(this)) return
        val constant = when (UxSettings.hapticLevel(this)) {
            UxSettings.HAPTIC_OFF -> return
            UxSettings.HAPTIC_LIGHT -> HapticFeedbackConstants.VIRTUAL_KEY
            else -> HapticFeedbackConstants.CONTEXT_CLICK
        }
        imeRootView?.performHapticFeedback(constant)
    }

    // ===== T01263 FR-9.4：布局过渡动画（200ms alpha+translationY，避免硬跳变）=====
    // ⚠ 真机动画观感未实测（本环境无真机）：按规格做最小淡入/淡出过渡，不改任何尺寸逻辑。

    /** 展开类切换：淡入 + 轻微上移（200ms）。 */
    private fun animateReveal(v: View) {
        v.alpha = 0f
        v.translationY = dp(6).toFloat()
        v.animate().alpha(1f).translationY(0f).setDuration(200).start()
    }

    /** 收起类切换：淡出 + 轻微下移（200ms），动画结束后 GONE 并复位。 */
    private fun animateCollapse(v: View) {
        v.animate().alpha(0f).translationY(dp(6).toFloat()).setDuration(200)
            .withEndAction {
                v.visibility = View.GONE
                v.alpha = 1f
                v.translationY = 0f
            }
            .start()
    }

    // ===== T01127：手动生成（简化模式固化，原「粘贴/生成候选」按钮并入 ✨）=====

    /**
     * 手动生成（默认工作流）：剪贴板有文本 → 填入手动输入框并直接生成；
     * 剪贴板为空 → 友好降级（展开手动面板，走手动编辑路径，长按 ✨ 也可直达）。
     * 非文本内容由 coerceToText 兜底转文本；IME 属可信输入源无剪贴板权限障碍。
     */
    private fun simplifiedGenerate() {
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString()?.trim().orEmpty()
        if (text.isEmpty()) {
            Toast.makeText(this, "剪贴板为空，已展开手动面板（请复制消息后重试）", Toast.LENGTH_LONG).show()
            ensureManualPanel()
            return
        }
        ensureManualPanel()
        manualInput?.tag = null // 剪贴板内容不按读屏多行拆分
        manualInput?.setText(text)
        refreshInputPane(editing = false) // T01138：填入后按双栏展示态渲染
        manualInput?.setSelection(text.length)
        Toast.makeText(this, "已读取剪贴板，正在生成候选…", Toast.LENGTH_SHORT).show()
        generateManual()
    }

    override fun onStartInputView(info: EditorInfo?, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        lastCommittedLen = 0 // T01112：换输入框/重开键盘后旧注入不可撤回（防跨框误删）
        applyTheme() // T01158 FR-9.1：设置里改过暗色/AMOLED 后，重开键盘即生效
        applyCompactMode() // T01536：精简开关在 APP 进程写入后，键盘重显即生效（不再等视图重建）
        refresh()
    }

    /**
     * T01536：精简模式可见性重挂（原缺陷：标志只在 onCreateInputView 读一次）。
     * 开=输入区整行（消息内容框+双栏读屏+汇总提示格）GONE，图标行下直接候选区；
     * 关=恢复 VISIBLE。每次键盘显示重读 SharedPreferences，同进程读写即时一致。
     */
    private fun applyCompactMode() {
        val row = inputRow ?: return
        val compact = GeneratorSettings.isCompactMode(this)
        row.visibility = if (compact) View.GONE else View.VISIBLE
        Log.i(TAG_IME, "applyCompactMode compact=$compact inputRow=${if (row.visibility == View.VISIBLE) "VISIBLE" else "GONE"}")
    }

    private fun refresh() {
        val col = candidateCol ?: return
        val status = summaryView ?: return
        renderTemplates()

        val pending = ReplyStore.latest()
        if (pending == null) {
            markIgnoredIfNeeded()
            // 通知链路无待回复时，回看最近一次手动生成结果（键盘重开不丢失）
            val manual = lastManualCandidates
            if (manual != null && manualHistory.isNotEmpty()) {
                lastShownSource = "手动生成" // T01257 FR-7.6：分应用分布来源
                shownDim = lastGenDim // T01350：手动生成候选同步场景维度
                renderCandidates(col, manual)
                status.text = manualStatusText()
                return
            }
            status.text = "暂无待回复消息"
            candidateScroll?.visibility = View.GONE
            return
        }

        // 新的待回复替换旧的：旧的未点选 → 记「忽略」（FR-4.3）
        if (shownAt != null && pending.createdAt != shownAt) markIgnoredIfNeeded()
        if (pending.createdAt != shownAt) {
            shownAt = pending.createdAt
            shownStyle = pending.candidates.firstOrNull()?.style
            shownDim = null // T01350：通知链路暂无场景维度上下文，仅记全局统计
            adopted = false
        }
        // T01257 FR-7.6：通知链路的来源=聊天名（缺省回落到发送者）
        lastShownSource = pending.notification.chat?.takeIf { it.isNotBlank() }
            ?: pending.notification.sender
            ?: "未知"

        val who = pending.notification.sender ?: "对方"
        // T01260 FR-8.6：该聊天最近一条消息距 now 超 48h 未回复 → 汇总格提示 + 显示一键
        // 「轻量回访」入口；正常时限内不提示（revisitRow 保持 GONE）。
        val stale = pending.notification.timestamp > 0 &&
            System.currentTimeMillis() - pending.notification.timestamp > REVISIT_THRESHOLD_MS
        revisitRow?.visibility = if (stale) View.VISIBLE else View.GONE
        status.text = (if (pending.notice != null) {
            "风险提示（$who）：${pending.notice}"
        } else {
            "$who：${pending.notification.body.orEmpty()}"
        }) + if (stale) "｜上次消息已超 48h，可轻量回访" else ""

        renderCandidates(col, pending.candidates)
    }

    /**
     * 反馈②：候选逐条纵向排列，可滚动。
     * T01131：每条候选按左三右一横向划分——左侧候选文本（唯一可点击区，点选注入、
     * 长按另存模板）；右侧理由说明小字灰色、不可点击（FR-3.3 解释句由 toast 呈现
     * 演进为常驻展示；无理由时理由区不渲染，文本区独占整行）。
     */
    private fun renderCandidates(col: LinearLayout, candidates: List<Candidate>) {
        col.removeAllViews()
        candidateScroll?.visibility = if (candidates.isEmpty()) View.GONE else View.VISIBLE
        // T01257 FR-7.6：候选呈现时刻——点选采纳时据此算「生成→采纳」耗时（呈现即生成完成时刻）
        if (candidates.isNotEmpty()) lastShownAtMs = System.currentTimeMillis()
        // T01158 FR-7.1：有候选才显示「换一批」（无上次生成请求时不显示，点了也没意义）
        regenerateRow?.visibility =
            if (candidates.isNotEmpty() && lastManualRequest != null) View.VISIBLE else View.GONE
        candidates.forEach { candidate ->
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            val hasRationale = !candidate.rationale.isNullOrBlank()
            val item = TextView(this).apply {
                text = candidate.text
                textSize = 14f
                setTextColor(ux.accent) // T01158 FR-9.1：暗色下取可读的蓝（#7DA2FF）
                background = GradientDrawable().apply {
                    cornerRadius = dp(10).toFloat()
                    setColor(ux.cardBg)
                }
                setPadding(dp(14), dp(10), dp(14), dp(10))
                setOnClickListener {
                    adopted = true
                    // FR-4.3：点选即「采纳」（脱敏：只记计数与风格）
                    // T01257 FR-7.6：顺带记录分应用来源与「生成→采纳」耗时（零留存，不存正文）
                    val genMs = if (lastShownAtMs > 0) System.currentTimeMillis() - lastShownAtMs else null
                    FeedbackStore.record(
                        this@EmoReplyImeService, "采纳", candidate.style,
                        source = lastShownSource, genMs = genMs,
                        // T01350：场景维度入三维统计（通知链路 shownDim=null 仅记全局）
                        relation = shownDim?.first, tone = shownDim?.second,
                    )
                    hapticFeedback() // T01258 FR-7.7：候选采纳 Haptic
                    commit(candidate.text)
                }
                // 长按候选：T01262 FR-8.8 起由「另存为模板」扩展为「收藏/存模板/取消收藏」
                // 菜单——收藏话术本地加密存储，生成时同类场景优先注入 prompt 作参考
                setOnLongClickListener {
                    hapticFeedback() // T01258：长按 Haptic
                    showCandidateActions(candidate)
                    true
                }
            }
            row.addView(
                item,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 3f),
            )
            // T01291 用户反馈：恢复右侧纯理由栏、去掉复制按钮（T01158 的复制入口撤销）。
            // 回到 T01131 形态：左三右一，右侧理由灰字常驻不可点；无理由时候选独占整行。
            // T01250 FR-9.3：本条理由升级为分类标签——右侧坑垂直排列：上行标签（强调色、
            // 语义分类，一眼可辨），下行理由小字灰（保留原文细节）。无理由时跳过整块。
            if (hasRationale) {
                val tagText = tagRationale(candidate.rationale.orEmpty())
                val rightCol = LinearLayout(this).apply {
                    orientation = LinearLayout.VERTICAL
                    setPadding(dp(4), dp(2), dp(4), dp(4))
                }
                rightCol.addView(TextView(this).apply {
                    text = tagText
                    textSize = 11f
                    setTextColor(ux.accent) // T01158 FR-9.1：暗色下取可读的蓝（#7DA2FF）
                })
                rightCol.addView(TextView(this).apply {
                    text = candidate.rationale
                    textSize = 10f
                    setTextColor(ux.textSecondary)
                    setPadding(0, dp(2), 0, 0)
                })
                row.addView(
                    rightCol,
                    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f)
                        .apply { marginStart = dp(6) },
                )
            }
            col.addView(
                row,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(6) },
            )
        }
    }

    // ---- 手动生成（反馈①） ----

    private fun toggleManualPanel() {
        val panel = manualPanel ?: return
        Log.i(TAG_IME, "toggleManualPanel: clicked, showing=${panel.visibility != View.VISIBLE}")
        // T01263 FR-9.4：展开/收起加 200ms 过渡（淡入淡出+位移），避免面板硬跳变
        val showing = panel.visibility != View.VISIBLE
        if (showing) {
            panel.visibility = View.VISIBLE
            animateReveal(panel)
        } else {
            animateCollapse(panel)
        }
        // T01122：面板展开时手动生成图标保持激活色（toggle 态反馈）
        iconGeneratePanel?.setColorFilter(
            if (showing) ux.iconActive else ux.iconNormal,
        )
    }

    /**
     * T01111 读屏（pull 模式）：仅在本按钮被点击时读取一次当前聊天窗口可见文本。
     * T01124 扩展：节点树读不到内容（微信保护节点树）时自动 fallback 截图+端侧 OCR。
     * OCR 耗时 1-4 秒，全程后台线程，完成后回主线程填入；无模拟点击/滚动，不落盘不外传。
     */
    private fun readFromScreen() {
        // T01129：串行防护——读屏未完成（解析未成功）前禁止并发触发第二次读屏
        if (readingScreen) {
            Toast.makeText(this, "正在读屏，请稍候…", Toast.LENGTH_SHORT).show()
            return
        }
        Log.i(TAG_IME, "readFromScreen: clicked, serviceReady=${EmoReplyReaderService.isReady()}")
        summaryView?.text = "读屏：按钮已点击（服务就绪=${EmoReplyReaderService.isReady()}）"
        if (!EmoReplyReaderService.isReady()) {
            summaryView?.text = "读屏：服务未就绪，尝试自动恢复…"
            Thread {
                val recovered = recoverAccessibilityService()
                val ready = recovered && waitReaderReady(2500)
                mainHandler.post {
                    if (!ready) {
                        Toast.makeText(
                            this,
                            "读屏服务未就绪且自动恢复失败，正在打开系统设置…",
                            Toast.LENGTH_LONG,
                        ).show()
                        startActivity(
                            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    } else {
                        Log.i(TAG_IME, "readFromScreen: accessibility service recovered")
                        startScreenCapture()
                    }
                }
            }.start()
            return
        }
        startScreenCapture()
    }

    /**
     * T01149（用户反馈：点读屏跳辅助功能设置）：无障碍服务在进程被杀/重装后
     * 「已启用但未运行」，用户点读屏直接被甩到系统设置，体验差。
     * 自动恢复：持有 WRITE_SECURE_SETTINGS（adb 一次性授予）时，把本服务从
     * enabled 列表摘除再放回——强制系统 unbind→rebind，无需用户手动操作。
     * 无权限/失败返回 false，由调用方回落到跳设置的旧路径。
     */
    private fun recoverAccessibilityService(): Boolean {
        val cr = contentResolver
        val key = Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        return try {
            val cur = Settings.Secure.getString(cr, key) ?: ""
            Log.i(TAG_IME, "recover: enabled=[$cur]")
            // OneUI 会在服务进程死亡后自动把服务从 enabled 列表摘除（真机实测）——
            // 所以无论列表里还有没有自己，都强制写回完整列表触发系统 rebind
            val self = "com.emoreply.poc/.capture.EmoReplyReaderService"
            val restored = if (cur.contains("EmoReplyReaderService")) cur
            else (cur.split(":").filter { it.isNotBlank() } + self).joinToString(":")
            val without = restored.split(":").filterNot { it.contains("EmoReplyReaderService") }.joinToString(":")
            Settings.Secure.putString(cr, key, without)
            Thread.sleep(300)
            Settings.Secure.putString(cr, key, restored)
            true
        } catch (e: Exception) {
            Log.w(TAG_IME, "recover: failed", e)
            false
        }
    }

    /** 轮询等待读屏服务重绑完成（最多 [timeoutMs]）。 */
    private fun waitReaderReady(timeoutMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (EmoReplyReaderService.isReady()) return true
            Thread.sleep(200)
        }
        return EmoReplyReaderService.isReady()
    }

    /** 读屏主流程（服务就绪后调用）：收起遮挡 → 后台捕获 → 回填。 */
    private fun startScreenCapture() {
        // T01251 FR-7.4 等待期提示；T01312 补齐分屏阶段进度：采集端每完成一屏发
        // ACTION_READ_PROGRESS 广播，这里注册接收器把文案更新为「已读第 N/M 屏…」，
        // 采集结束（含失败路径）注销，避免 receiver 泄漏。
        summaryView?.text = "读屏：正在读取聊天内容…"
        readingScreen = true
        val progressReceiver = registerReadProgressReceiver()
        // T01135：IME 展开态（面板/候选占位）会遮挡聊天内容，导致 OCR 静默读不到。
        // 读屏前临时收起输入法，OCR 完成后自动恢复回到聊天框。
        val needRestore = isImeCovering()
        if (needRestore) {
            Log.i(TAG_IME, "readFromScreen: hide IME during capture")
            runCatching { requestHideSelf(0) }
        }
        Thread {
            if (needRestore) Thread.sleep(IME_HIDE_SETTLE_MS) // 等收起动画完成
            var messages = EmoReplyReaderService.captureVisibleMessages()
            var via = "节点树"
            if (messages.isEmpty()) {
                // T01343：读屏范围可配置（UxSettings.readScreens，默认 1 屏）——
                // 当前屏 + 上滑 N-1 屏；T01291 默认值由 2 屏简化为 1 屏保持不变
                messages = EmoReplyReaderService.captureMultiScreen(
                    extraScreens = UxSettings.readScreens(this@EmoReplyImeService) - 1,
                )
                via = "截图OCR"
            }
            val result = messages
            val source = via
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                readingScreen = false
                progressReceiver?.let { runCatching { unregisterReceiver(it) } }
                if (needRestore) restoreImeAfterRead()
                applyReadResult(result, source)
            }
        }.start()
    }

    /**
     * T01312：注册多屏采集进度接收器；返回 receiver 以便调用方在采集结束后注销。
     * Android 13+ 需显式指定 RECEIVER_NOT_EXPORTED（广播带包名、仅应用内传递）。
     */
    private fun registerReadProgressReceiver(): BroadcastReceiver? = runCatching {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val index = intent.getIntExtra(EmoReplyReaderService.EXTRA_READ_INDEX, 0)
                val total = intent.getIntExtra(EmoReplyReaderService.EXTRA_READ_TOTAL, 0)
                if (index <= 0 || total <= 0) return
                summaryView?.text = if (total > 1) "读屏：已读第 $index/$total 屏…"
                else "读屏：正在读取聊天内容…"
            }
        }
        val filter = IntentFilter(EmoReplyReaderService.ACTION_READ_PROGRESS)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(receiver, filter)
        }
        receiver
    }.onFailure { Log.w(TAG_IME, "register read-progress receiver failed", it) }.getOrNull()

    /** T01135：IME 是否高到会遮挡聊天内容（展开态判定阈值见 EmoReplyReaderService）。 */
    private fun isImeCovering(): Boolean =
        (imeRootView?.height ?: 0) >= EmoReplyReaderService.IME_COVER_WINDOW_PX

    /** T01135：读屏后恢复输入法（切回 EmoReply 并重新 attach 输入框视图）。 */
    private fun restoreImeAfterRead() {
        mainHandler.postDelayed({
            runCatching { requestShowSelf(0) }
        }, 120)
    }

    /** 读屏结果回填（主线程）。 */
    private fun applyReadResult(messages: List<String>, via: String) {
        if (messages.isEmpty()) {
            summaryView?.text = "读屏：未读到内容（确认聊天窗在前台且服务已开启）"
            Toast.makeText(
                this,
                "未读到聊天内容（请确认聊天窗口在前台，且已开启读屏服务）",
                Toast.LENGTH_LONG,
            ).show()
            return
        }
        manualInput?.tag = TAG_READ_SCREEN
        val text = messages.joinToString("\n")
        manualInput?.setText(text)
        refreshInputPane(editing = false) // T01138：填入后按双栏展示态渲染
        manualInput?.setSelection(text.length)
        // T01127：读屏成功即自动生成候选，无需再点「生成候选」
        // T02222：方向判定来源提示——气泡颜色判出的高置信；颜色不可用（深色主题、
        // 图片/语音气泡、非微信）时回退左缘位置判定，须让用户知道方向可能有误。
        val stats = EmoReplyReaderService.sideStats()
        val sideNote = when {
            stats == null || stats.judged == 0 -> ""
            stats.uncertain -> "，方向按位置判断（可能有误）"
            stats.byPosition > 0 -> "，其中 ${stats.byPosition} 条方向按位置判断"
            else -> ""
        }
        summaryView?.text = "读屏（$via）：已读取 ${messages.size} 条$sideNote，正在生成候选…"
        Toast.makeText(
            this,
            "已读取 ${messages.size} 条可见消息（$via），正在生成候选…",
            Toast.LENGTH_SHORT,
        ).show()
        generateManual()
    }

    /**
     * T01130「输入框美化」+ T01137 目标更正：美化对象首先是**宿主回复输入框**
     * （微信/钉钉当前正在打字的那条），而非本 IME 的手动面板输入框——
     * 用户在聊天框里打了字想润色，才是真实诉求。
     * 优先级：宿主编辑器有内容 → 美化并原地回填；否则回落到手动面板输入框；
     * 两者都空 → 提示中止。
     *
     * T01261 FR-8.7：泛化为文本动作族——点击仍为「改写」（原美化语义，BEAUTIFY_SYSTEM 保留），
     * 长按图标弹菜单可选总结/翻译/扩写，走完全相同的「读文本→调生成→清洗→回填」路径。
     */
    private fun beautifyInput() = runTextAction(TextAction.REWRITE)

    /** 文本动作的取材结果：宿主回复框（光标前后+选区）优先，空时回落手动面板输入框。 */
    private data class ActionTarget(
        val ic: android.view.inputmethod.InputConnection?,
        val useHost: Boolean,
        val text: String,
        val beforeLen: Int,
        val afterLen: Int,
    )

    private fun gatherActionTarget(): ActionTarget? {
        val ic = currentInputConnection
        // 宿主输入框全文 = 光标前文本 + 光标后文本（无选区场景；有选区时一并纳入）
        val before = runCatching { ic?.getTextBeforeCursor(MAX_BEAUTIFY_CHARS, 0)?.toString() }.getOrNull().orEmpty()
        val after = runCatching { ic?.getTextAfterCursor(MAX_BEAUTIFY_CHARS, 0)?.toString() }.getOrNull().orEmpty()
        val selected = runCatching { ic?.getSelectedText(0)?.toString() }.getOrNull().orEmpty()
        val hostText = (before + selected + after).trim()
        val manualText = manualInput?.text?.toString()?.trim().orEmpty()
        val useHost = ic != null && hostText.isNotBlank()
        val text = if (useHost) hostText else manualText
        if (text.isEmpty()) return null
        // 回填用：删除量=光标前后已读长度（选中文本位于两者之间，随删除一并带出）
        return ActionTarget(ic, useHost, text, before.length, after.length)
    }

    /** 动作结果回填：宿主回复框替换优先；替换失败/无宿主框时回落手动面板填入。 */
    private fun handleActionResult(action: TextAction, target: ActionTarget, result: String) {
        generating = false
        iconBeautify?.alpha = 1f
        if (result.isEmpty()) {
            summaryView?.text = "${action.label}失败（云端无返回），已保留原文"
            Toast.makeText(this, "${action.label}失败，已保留原文", Toast.LENGTH_SHORT).show()
            return
        }
        val done = target.useHost && replaceHostText(target.ic, target.beforeLen, target.afterLen, result)
        if (!done) {
            manualInput?.tag = null // 动作结果不按读屏多行拆分
            manualInput?.setText(result)
            refreshInputPane(editing = false) // T01138：填入后按双栏展示态渲染
            manualInput?.setSelection(result.length)
        }
        summaryView?.text = "已${action.label}"
        Toast.makeText(
            this,
            if (done) "已${action.label}（回复框原文已替换）" else "已${action.label}（原意不变）",
            Toast.LENGTH_SHORT,
        ).show()
    }

    /**
     * T01261 FR-8.7：文本动作族统一入口（改写/总结/翻译/扩写）。
     * 复用原美化路径：读宿主回复框（优先）或手动面板输入框 → 云端生成 → 清洗 → 回填；
     * 空文本提示中止；生成中防连点；失败保留原文（C24）。
     */
    private fun runTextAction(action: TextAction) {
        val target = gatherActionTarget()
        if (target == null) {
            // T01261：空文本提示中止（动作作用于输入框文本，无文本无意义）
            Toast.makeText(this, "请输入需要${action.label}的文字", Toast.LENGTH_SHORT).show()
            return
        }
        if (generating) {
            Toast.makeText(this, MSG_GENERATING, Toast.LENGTH_SHORT).show()
            return
        }
        val config = GeneratorSettings.load(this)
        if (!GeneratorSettings.isCloudReady(config)) {
            Toast.makeText(this, "云端模型未配置，无法${action.label}（见应用内设置）", Toast.LENGTH_LONG).show()
            return
        }
        generating = true
        iconBeautify?.alpha = 0.35f
        summaryView?.text = if (target.useHost) "正在${action.label}回复框文字…" else "正在${action.label}…"
        Thread {
            val result = try {
                CloudLlmGenerator(config.baseUrl, config.apiKey, config.model)
                    .generate(buildTextActionPrompt(target.text, action))
                    .firstOrNull()
                    ?.let { expandCandidates(it).firstOrNull() }
                    ?.let { splitRationale(it).first }
                    ?.trim()
                    .orEmpty()
            } catch (e: CloudLlmGenerator.CloudLlmException) {
                "" // C24 同款原则：失败保留原文，明确提示，不崩溃不静默
            }
            mainHandler.post { handleActionResult(action, target, result) }
        }.start()
    }

    /**
     * T01261 FR-8.7：长按「美化」图标 → 文本动作族菜单（改写/总结/翻译/扩写），
     * 选中后经 [runTextAction] 作用于输入框文本（空文本由 runTextAction 提示中止）。
     */
    private fun showTextActionMenu() {
        val labels = TextAction.entries.map { it.label }.toTypedArray()
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("文本动作")
            .setItems(labels) { _, which ->
                runTextAction(TextAction.entries[which])
            }
            .setNegativeButton("取消", null)
            .show()
    }

    /**
     * T01537：逐联系人语气调节对话框——默认值取该联系人的已存偏好或关系推档，
     * 保存即 setContactTone 写回；对话框内明确说明优先级（联系人级覆盖全局默认）。
     */
    private fun showContactToneDialog() {
        val persona = PersonaStore.get(this)
        val contact = lastContact ?: ""
        // 默认滑块值：若已有联系人偏好则用之，否则按当前全局态（AUTO→关系推档 / 手动→全局值）
        val defaultTone = if (contact.isNotBlank()) {
            persona.contactTone(contact) ?: effectiveTone(contact)
        } else {
            effectiveTone(null)
        }
        val globalDesc = if (UxSettings.toneAuto(this)) "自动（随关系推档）" else "${UxSettings.tone(this)}"
        val relationTone = if (contact.isNotBlank()) Tone.toneForRelation(persona.getRelation(contact)) else -1
        val savedTone = if (contact.isNotBlank()) persona.contactTone(contact) else null

        val pad = dp(16)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(8), dp(24), dp(8))
        }

        // 联系人名称输入框
        val etName = EditText(this).apply {
            hint = "联系人昵称（如王总、阿明）"
            setText(contact)
            textSize = 14f
            setTextColor(ux.textPrimary)
            setBackgroundColor(ux.cardBg)
        }
        val lp8 = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { setMargins(0, dp(8), 0, 0) }
        col.addView(etName, lp8)

        // 提示文本：优先级说明 + 当前状态
        val tvHint = TextView(this).apply {
            textSize = 12f
            setTextColor(ux.textSecondary)
            text = buildString {
                append("优先级：联系人级 > 全局滑杆 > 关系推档\n")
                append("当前全局设置：$globalDesc")
                if (relationTone >= 0) append(" | 该联系人关系推档：$relationTone")
                if (savedTone != null) append("\n已存偏好：$savedTone（将覆盖全局）")
            }
        }
        val lp8b = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { setMargins(0, dp(8), 0, dp(8)) }
        col.addView(tvHint, lp8b)

        // 滑块
        val slider = com.google.android.material.slider.Slider(this).apply {
            valueFrom = 0f
            valueTo = 100f
            stepSize = 1f
            value = defaultTone.toFloat()
        }
        val tvVal = TextView(this).apply {
            textSize = 12f
            setTextColor(ux.textPrimary)
            text = "当前值：${defaultTone}"
        }
        slider.addOnChangeListener { _, v, _ -> tvVal.text = "当前值：${v.toInt()}" }
        val lp4 = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { setMargins(0, dp(4), 0, 0) }
        col.addView(slider, lp4)
        col.addView(tvVal, lp4)

        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("按联系人调节语气")
            .setView(col)
            .setPositiveButton("保存并写回") { _, _ ->
                val name = etName.text.toString().trim()
                if (name.isEmpty()) {
                    Toast.makeText(this@EmoReplyImeService, "请输入联系人昵称", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                val v = slider.value.toInt()
                persona.setContactTone(name, v)
                lastContact = name
                Toast.makeText(
                    this@EmoReplyImeService,
                    "已将 $name 的语气设为 $v（联系人级优先于全局，下次对话自动生效）",
                    Toast.LENGTH_LONG,
                ).show()
            }
            .setNeutralButton("恢复全局/关系推档") { _, _ ->
                val name = etName.text.toString().trim()
                if (name.isEmpty()) {
                    Toast.makeText(this@EmoReplyImeService, "请输入联系人昵称", Toast.LENGTH_SHORT).show()
                    return@setNeutralButton
                }
                persona.setContactTone(name, null)
                Toast.makeText(
                    this@EmoReplyImeService,
                    "$name 已恢复为全局/关系推档",
                    Toast.LENGTH_SHORT,
                ).show()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    /** 用美化结果替换宿主输入框原文：先删旧（光标前后已读文本），再提交新文本。 */
    private fun replaceHostText(
        ic: android.view.inputmethod.InputConnection?,
        beforeLen: Int,
        afterLen: Int,
        result: String,
    ): Boolean {
        if (ic == null) return false
        return try {
            ic.beginBatchEdit()
            val removed = ic.deleteSurroundingText(beforeLen, afterLen)
            if (removed) ic.commitText(result, 1)
            removed
        } catch (e: Exception) {
            Log.w(TAG_IME, "beautify: host replace failed", e)
            false
        } finally {
            ic.endBatchEdit()
        }
    }

    /**
     * T01138：输入区双栏/单栏切换。
     * 编辑态（EditText 有焦点）→ 整宽 EditText；展示态（无焦点且为多行内容）→ 左右均分双栏，
     * 行按从左到右顺序依次填充（前半左栏、后半右栏），竖向占用减半、空间利用率提升。
     * 数据源恒为 manualInput（读屏/剪贴板填充），生成逻辑读其 text，零变化。
     */
    private fun refreshInputPane(editing: Boolean = manualInput?.isFocused == true) {
        val text = manualInput?.text?.toString()?.trim().orEmpty()
        val lines = if (text.isEmpty()) emptyList() else text.split('\n').map { it.trim() }.filter { it.isNotEmpty() }
        val dual = !editing && lines.size > 1
        Log.i(TAG_IME, "refreshInputPane editing=$editing lines=${lines.size} dual=$dual")
        if (dual) {
            val half = (lines.size + 1) / 2
            leftPane?.text = lines.take(half).joinToString("\n")
            rightPane?.text = lines.drop(half).joinToString("\n")
        }
        // T01263 FR-9.4：双栏/单栏（展示态↔编辑态）切换加 200ms 过渡；仅在实际切换时动画，
        // 状态未变时不动画，避免反复调用时无谓的重复淡入
        if (dual && displayRow?.visibility != View.VISIBLE) {
            displayRow?.visibility = View.VISIBLE
            manualInput?.visibility = View.GONE
            displayRow?.let { animateReveal(it) }
        } else if (!dual && manualInput?.visibility != View.VISIBLE) {
            displayRow?.visibility = View.GONE
            manualInput?.visibility = View.VISIBLE
            manualInput?.let { animateReveal(it) }
        }
    }

    private fun generateManual() {
        if (generating) {
            Toast.makeText(this, MSG_GENERATING, Toast.LENGTH_SHORT).show()
            return
        }
        val text = manualInput?.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) {
            Toast.makeText(this, "请先粘贴对方发来的消息", Toast.LENGTH_SHORT).show()
            return
        }
        // T01129：串行第一步——对话解析成功才进入生成，禁止提前处理。
        // 读屏内容按行拆分并解析侧别（我：/对方：）；解析失败给出明确提示而非空回复。
        val isReadScreen = manualInput?.tag == TAG_READ_SCREEN
        val lines = if (isReadScreen) {
            text.split('\n').map { it.trim() }.filter { it.isNotEmpty() }
        } else {
            listOf(text)
        }
        val (turns, current) = splitDialogueTurns(lines)
        if (current == null) {
            Toast.makeText(
                this,
                "未读到对方消息（全部为我方发言或内容为空），无法生成回复",
                Toast.LENGTH_LONG,
            ).show()
            summaryView?.text = "解析失败：无对方发言"
            return
        }
        generating = true
        iconGeneratePanel?.alpha = 0.35f // T01122：生成中图标半透明（T01127 起 feedback 落在 ✨）
        summaryView?.text = "正在生成候选…"
        Thread {
            val outcome = runManualPipeline(current, turns)
            mainHandler.post {
                generating = false
                iconGeneratePanel?.alpha = 1f
                showManualOutcome(outcome)
            }
        }.start()
    }

    /**
     * T01129：读屏多行文本侧别解析（与 m1-poc preprocess.split_dialogue_turns 同构）。
     * 「我：xxx」→ 我方发言；「对方：xxx」/无前缀 → 对方发言（向后兼容）。
     * current 取最后一条对方发言（我方发言不需要回复），其余按原始时序进 history；
     * 无对方发言时 current 返回 null（解析失败，调用方须明确提示、不得生成）。
     *
     * T01343 上下文优化（与 py 同构）：
     * - 时间分段：「时间：」标记行（捕获层把纯时间/日期行转换而来）开启新话题段，
     *   此前内容视为已完结话题整段丢弃，不回溯作 current——避免跨时段旧话题混入；
     * - history 上限：超过 [HISTORY_LIMIT] 条只保留最近 N 条（时序不变），双保险。
     */
    /** 按行解析侧别标注，并按「时间：」标记切话题段（标记开启新段，标记前视为已完结话题）。 */
    private fun parseSideLines(lines: List<String>): List<MutableList<Pair<String, String>>> {
        val segments = mutableListOf<MutableList<Pair<String, String>>>()
        segments.add(mutableListOf())
        for (raw in lines) {
            val t = raw.trim()
            if (t.isEmpty()) continue
            if (t.startsWith("时间：")) {
                segments.add(mutableListOf())
                continue
            }
            val parsed = when {
                t.startsWith("我：") || t.startsWith("我:") -> "我" to t.substringAfter(':').trim()
                t.startsWith("对方：") || t.startsWith("对方:") -> "对方" to t.substringAfter(':').trim()
                else -> "对方" to t
            }
            if (parsed.second.isNotEmpty()) segments.last().add(parsed)
        }
        return segments
    }

    /** 段内 current 解析：末行为我方发言时向前找最后一条对方发言（段内不跨段回溯）；无则 null。 */
    private fun resolveCurrentTurn(
        seg: List<Pair<String, String>>,
    ): Pair<List<Pair<String, String>>, Pair<String, String>>? {
        val last = seg.last()
        if (last.first == "对方") return seg.dropLast(1) to last
        val idx = seg.indexOfLast { it.first == "对方" }
        if (idx < 0) return null
        val hist = seg.toMutableList().apply { removeAt(idx) }
        return hist to seg[idx]
    }

    private fun splitDialogueTurns(
        lines: List<String>,
        historyLimit: Int? = HISTORY_LIMIT,
    ): Pair<List<Pair<String, String>>, Pair<String, String>?> {
        val segments = parseSideLines(lines)
        // 取最后一个非空段（时间标记后无内容时回落前段，保持向后兼容）
        val seg = segments.lastOrNull { it.isNotEmpty() } ?: return emptyList<Pair<String, String>>() to null
        val (history, current) = resolveCurrentTurn(seg)
            ?: return emptyList<Pair<String, String>>() to null
        return (if (historyLimit != null && history.size > historyLimit) {
            history.takeLast(historyLimit)
        } else history) to current
    }

    /** 与通知链路同一条管线（云端/桩 + 风险筛查 + 候选护栏）；manual=true 跳过意图闸。 */
    private fun runManualPipeline(
        current: Pair<String, String>,
        turns: List<Pair<String, String>>,
        exclude: List<String> = emptyList(), // T01158 FR-7.1：换一批排除项
    ): PipelineOutcome {
        // T01129：读屏内容已按侧别解析——current=最后一条对方发言，history 保留双方
        // 发言的 sender 标注（提示词渲染「我：/对方：」，生成感知完整对话上下文）
        val (curSender, curBody) = current
        val n = EmoNotification(
            source = "manual",
            timestamp = System.currentTimeMillis(),
            body = curBody,
            sender = curSender,
            chat = "手动生成",
            title = "手动生成",
        )
        val style = GeneratorSettings.load(this).style
        val history = manualHistory.toList() + turns.map { (sender, body) ->
            EmoNotification(
                source = "manual",
                timestamp = System.currentTimeMillis(),
                body = body,
                sender = sender,
                chat = "手动生成",
                title = "手动生成",
            )
        }
        // T01158：语气强度滑杆（FR-7.3）与表情开关（T01159）在此进管线
        // T01348：AUTO 档按「联系人手动覆盖 > 三维筛选关系 > persona 库关系」解析语气档位
        // T01350：采纳率闭环——低分维度（关系×tone桶×风格）自动降档；多风格开启时
        // 单值 tone 不生效，跳过 adapt
        val genRelation = currentRelation(curSender)
        val genStyle = style
        var tone = effectiveTone(curSender)
        if (!UxSettings.multiTone(this)) {
            tone = Adaptive.adaptTone(genRelation, tone, genStyle,
                FeedbackStore.dimStats(this), enabled = UxSettings.autoAdapt(this)).first ?: tone
        }
        // T01244 FR-8.2：三维筛选（关系/场景/目的）默认继承上次选择，进管线注入 prompt
        val scenario = UxSettings.scenario(this)
        val purpose = UxSettings.purpose(this)
        // T01262 FR-8.8：同类场景的收藏话术进管线（prompt 优先参考，禁止原样复制）
        val favorites = favoriteTexts()
        return try {
            (cloudPipeline(style) ?: stubPipeline(style))
                .handle(n, history, manual = true, tone = tone, exclude = exclude,
                    scenario = scenario, purpose = purpose, favorites = favorites,
                    multiTone = UxSettings.multiTone(this)) // T01349：多风格并行（默认关）
        } catch (e: CloudLlmGenerator.CloudLlmException) {
            // C24：明确提示 + 降级为本地模板，不崩溃、不静默失败
            stubPipeline(style)
                .handle(n, history, manual = true, tone = tone, exclude = exclude,
                    scenario = scenario, purpose = purpose, favorites = favorites,
                    multiTone = UxSettings.multiTone(this))
                .copy(notice = e.userMessage)
        }.also { outcome ->
            // T01158 FR-7.1：记住本次请求，供「换一批」重跑（会话上下文不变）
            lastManualRequest = current to turns
            // T01350：暂存本次生成场景维度（关系/tone/风格），采纳/忽略时入三维统计
            lastGenDim = Triple(genRelation, tone, genStyle)
            // T01537：记住当前会话联系人（用于语气调节对话框预填）
            lastContact = curSender?.takeIf { it.isNotBlank() && it != "我" && it != "对方" }
            // 生成过一条即纳入会话上下文（无论是否命中风险提示），支撑多轮
            manualHistory.add(n)
            if (manualHistory.size > MANUAL_HISTORY_LIMIT) manualHistory.removeAt(0)
            lastManualCandidates = outcome.candidates
            lastManualNotice = outcome.notice
        }
    }

    /**
     * T01348：当前会话的关系维度——三维筛选显式选择（T01244）> persona 库标注；
     * 两者皆空 → 空串（tone 映射按「陌生」处理）。
     */
    private fun currentRelation(sender: String?): String {
        val persona = PersonaStore.get(this)
        val named = sender?.takeIf { it.isNotBlank() && it != "我" && it != "对方" }
        return UxSettings.relation(this).ifBlank { named?.let { persona.getRelation(it) }.orEmpty() }
    }

    // T01537：当前会话联系人（用于语气调节对话框预填）
    private var lastContact: String? = null

    /**
     * T01537：有效语气解析——联系人级手动覆盖 > 全局滑杆（含手动值）> 关系推档。
     *
     * 读屏侧别解析的 sender 常是「对方/我」通用标注，拿不到真实昵称时联系人级
     * 覆盖与库内关系都不可用，此时回落用户显式选择的三维筛选关系（T01244），
     * 两者皆空 → 「陌生」→ 全局默认 50（与关闭 AUTO 行为一致，无回归）。
     */
    private fun effectiveTone(sender: String?): Int {
        val persona = PersonaStore.get(this)
        val named = sender?.takeIf { it.isNotBlank() && it != "我" && it != "对方" }
        val override = named?.let { persona.contactTone(it) }
        val g = UxSettings.tone(this)
        val relation = currentRelation(sender)
        val t = Tone.resolveTone(g, relation, override) ?: g
        Log.i(TAG_IME, "tone: contact=$named override=$override global=$g relation=$relation effective=$t")
        return t
    }

    /** T01243 FR-8.1：端侧优先（飞行/无网/隐私可用）→ 云端 → 均不可用返回 null 走桩。 */
    private fun cloudPipeline(style: String): ReplyPipeline? {
        val config = GeneratorSettings.load(this)
        // 端侧后端就绪则优先：离线也能出候选，无需任何网络/密钥
        if (GeneratorSettings.isOnDeviceReady(config)) {
            MediaPipeOnDeviceGenerator.create(this, config.modelPath)
                ?.let { gen ->
                    return ReplyPipeline(
                        generator = gen::generate, style = style,
                        persona = PersonaStore.get(this), kb = KnowledgeBase.get(this),
                        emoji = UxSettings.emojiEnabled(this),
                    )
                }
        }
        if (!GeneratorSettings.isCloudReady(config)) return null
        val generator = CloudLlmGenerator(config.baseUrl, config.apiKey, config.model)
        return ReplyPipeline(
            generator = generator::generate, style = style,
            persona = PersonaStore.get(this), kb = KnowledgeBase.get(this),
            emoji = UxSettings.emojiEnabled(this), // T01159
        )
    }

    private fun stubPipeline(style: String): ReplyPipeline =
        ReplyPipeline(
            generator = StubGenerator::generate, style = style,
            persona = PersonaStore.get(this), kb = KnowledgeBase.get(this),
            emoji = UxSettings.emojiEnabled(this), // T01159
        )

    private fun showManualOutcome(outcome: PipelineOutcome) {
        val col = candidateCol ?: return
        lastShownSource = "手动生成" // T01257 FR-7.6：分应用分布来源
        renderCandidates(col, outcome.candidates)
        summaryView?.text = manualStatusText()
    }

    private fun manualStatusText(): String {
        val notice = lastManualNotice
        val count = lastManualCandidates?.size ?: 0
        return buildString {
            append("手动生成（会话上下文 ${manualHistory.size} 条，候选 $count 条）")
            if (notice != null) append("｜提示：$notice")
        }
    }

    // ---- 输入法快捷切换（反馈③/T01105：自研键盘不做，补齐切换体验） ----

    /**
     * T01133：一键直切——点击即切到目标输入法，不再展开列表二次选择。
     * 目标优先级：上次切过的目标（仍在已启用列表）→ 唯一的其他输入法 →
     * 展开列表兜底（多个候选且无记忆时让用户选定一次，选定即被记住，之后同样一步直切）。
     */
    private fun toggleImePanel() {
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        val others = imm.enabledInputMethodList.filter { it.packageName != packageName }
        if (others.isEmpty()) {
            Toast.makeText(this, "暂无其他已启用输入法（请先在系统设置启用）", Toast.LENGTH_LONG).show()
            return
        }
        val remembered = imePrefs().getString(KEY_IME_TARGET, null)
        val target = others.firstOrNull { it.id == remembered } ?: others.singleOrNull()
        if (target == null) {
            renderImeList()
            imePanel?.visibility = View.VISIBLE
            return
        }
        switchImeTo(target.id)
    }

    /** 动态读取已启用输入法（排除本 IME），逐行一键直切；「系统选择器…」兜底。 */
    private fun renderImeList() {
        val panel = imePanel ?: return
        panel.removeAllViews()
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        val self = packageName
        val others = imm.enabledInputMethodList.filter { it.packageName != self }
        panel.addView(TextView(this).apply {
            text = if (others.isEmpty()) "暂无其他已启用输入法（先在系统设置启用）" else "一键切换到："
            textSize = 11f
            setTextColor(ux.textSecondary) // T01158 FR-9.1
            setPadding(dp(14), dp(4), dp(4), dp(2))
        })
        others.forEach { info ->
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(pill(info.loadLabel(packageManager).toString()).apply {
                setOnClickListener { switchImeTo(info.id) }
            })
            panel.addView(
                row,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(4) },
            )
        }
        panel.addView(pill("系统选择器…").apply {
            setOnClickListener { showImePicker() }
        }, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(4) })
    }

    /** IME 内直接切换到目标输入法（已启用即可，无需特殊权限）；失败兜底系统选择器。 */
    private fun switchImeTo(inputMethodId: String) {
        try {
            switchInputMethod(inputMethodId)
            // T01133：记住切走的目标，供下次「一键直切」
            imePrefs().edit().putString(KEY_IME_TARGET, inputMethodId).apply()
        } catch (e: Exception) {
            // 个别 ROM 对直切有限制 → 退回系统选择器，保证功能可用
            showImePicker()
        }
    }

    private fun imePrefs() = getSharedPreferences(PREFS_IME, MODE_PRIVATE)

    private fun showImePicker() {
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        imm.showInputMethodPicker()
    }

    /**
     * T01158 FR-7.1：换一批——保留会话上下文重跑管线，把当前这批候选作为排除项
     * （提示词软约束 + 输出侧相似度去重双重保证），生成中禁用（防连点重复请求）。
     * T01291：FR-7.2 复制按钮已按用户反馈撤销（恢复纯理由栏），换一批保留。
     */
    private fun regenerate() {
        if (generating) {
            Toast.makeText(this, MSG_GENERATING, Toast.LENGTH_SHORT).show()
            return
        }
        val req = lastManualRequest
        if (req == null) {
            Toast.makeText(this, "请先生成一次候选，再换一批", Toast.LENGTH_SHORT).show()
            return
        }
        val exclude = lastManualCandidates?.map { it.text } ?: emptyList()
        val (current, turns) = req
        generating = true
        iconGeneratePanel?.alpha = 0.35f
        summaryView?.text = "正在换一批（避开 ${exclude.size} 条旧候选）…"
        Thread {
            val outcome = runManualPipeline(current, turns, exclude = exclude)
            mainHandler.post {
                generating = false
                iconGeneratePanel?.alpha = 1f
                showManualOutcome(outcome)
                if (outcome.candidates.isEmpty()) {
                    Toast.makeText(this, "新候选与旧候选过于相似，请再换一次", Toast.LENGTH_SHORT).show()
                }
            }
        }.start()
    }

    /**
     * T01246 FR-8.4：✨ 长按 → 开场建议菜单。选场景（破冰/重连/节日祝福）后，
     * 直接在后台跑 handleOpening 并渲染候选（无对话上下文，从零开始）。
     * 生成中防连点；异常按 C24 降级为桩并提示。
     */
    private fun openOpeningDialog() {
        val items = OPENING_SCENARIOS.toTypedArray()
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("开场建议")
            .setItems(items) { _, which ->
                val scenario = items[which]
                if (generating) {
                    Toast.makeText(this, MSG_GENERATING, Toast.LENGTH_SHORT).show()
                    return@setItems
                }
                generating = true
                iconGeneratePanel?.alpha = 0.35f
                summaryView?.text = "正在生成「$scenario」开场话术…"
                Thread {
                    val outcome = try {
                        (cloudPipeline("亲切") ?: stubPipeline("亲切"))
                            .handleOpening(scenario)
                    } catch (e: CloudLlmGenerator.CloudLlmException) {
                        stubPipeline("亲切").handleOpening(scenario).copy(notice = e.userMessage)
                    }
                    mainHandler.post {
                        generating = false
                        iconGeneratePanel?.alpha = 1f
                        showManualOutcome(outcome)
                    }
                }.start()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    /**
     * T01158 FR-9.1：重新取配色并刷到已构建的视图。
     * 设置里切了暗色后，键盘不一定重建（service 常驻），重开面板时必须重刷一次，
     * 否则出现「上半屏暗色、下半屏亮色」的混合态。
     */
    private fun applyTheme() {
        ux = uxTheme(this)
        imeRootView?.setBackgroundColor(ux.bg)
        summaryView?.setTextColor(ux.textSecondary)
        leftPane?.setTextColor(ux.textPrimary)
        rightPane?.setTextColor(ux.textPrimary)
        displayRow?.setBackgroundColor(ux.cardBg)
        manualInput?.apply {
            setBackgroundColor(ux.cardBg)
            setTextColor(ux.textPrimary)
            setHintTextColor(ux.textSecondary)
        }
        iconButtons.forEach { it.setColorFilter(ux.iconNormal) }
    }

    // ---- 通知链路既有能力 ----

    /** 模板条展示（点击注入、长按删除），最多展示前 6 个。 */
    private fun renderTemplates() {
        val trow = templateRow ?: return
        trow.removeAllViews()
        val templates = TemplateStore.all(this)
        if (templates.isEmpty()) return
        val label = TextView(this).apply {
            text = "模板"
            textSize = 11f
            setTextColor(ux.textSecondary) // T01158 FR-9.1
            setPadding(dp(14), dp(8), dp(4), dp(8))
        }
        trow.addView(label)
        templates.take(6).forEach { t ->
            trow.addView(TextView(this).apply {
                text = t.text.take(10) + if (t.text.length > 10) "…" else ""
                textSize = 13f
                setTextColor(ux.accent) // T01158 FR-9.1：暗色下原绿 #059669 偏暗
                setPadding(dp(10), dp(8), dp(10), dp(8))
                setOnClickListener {
                    hapticFeedback() // T01258 FR-7.7：注入成功 Haptic
                    commit(t.text)
                }
                setOnLongClickListener {
                    TemplateStore.remove(this@EmoReplyImeService, t.id)
                    Toast.makeText(this@EmoReplyImeService, "模板已删除", Toast.LENGTH_SHORT).show()
                    renderTemplates()
                    true
                }
            })
        }
    }

    private fun markIgnoredIfNeeded() {
        if (!adopted && shownAt != null) {
            FeedbackStore.record(this, "忽略", shownStyle,
                relation = shownDim?.first, tone = shownDim?.second) // T01350：三维统计
            adopted = true
        }
        shownAt = null
    }

    // ===== T01262 FR-8.8：话术收藏夹（候选长按收藏 / 取消收藏 / 生成时优先注入） =====

    /**
     * 长按候选动作菜单：收藏话术 / 存为模板 / 取消收藏（已收藏时）/ 取消。
     * 收藏关键词取当前筛选场景（空=通用收藏，任何场景生成都可参考）；
     * 取消收藏即时生效——存储按需重读，下次生成不再注入该话术。
     */
    private fun showCandidateActions(candidate: Candidate) {
        val saved = FavoritesStore.contains(this, candidate.text)
        val items = mutableListOf("收藏话术（同类场景优先参考）", "存为模板")
        if (saved) items.add("取消收藏")
        items.add("取消")
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("候选操作")
            .setItems(items.toTypedArray()) { _, which ->
                when (which) {
                    0 -> {
                        val ok = FavoritesStore.save(this, candidate.text, UxSettings.scenario(this))
                        Toast.makeText(
                            this,
                            if (ok) "已收藏（生成时同类场景优先参考）" else "该话术已在收藏夹",
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                    1 -> {
                        val ok = TemplateStore.save(this, candidate.text)
                        Toast.makeText(
                            this,
                            if (ok) "已存为模板（长按模板可删除）" else "该模板已存在",
                            Toast.LENGTH_SHORT,
                        ).show()
                        if (ok) renderTemplates()
                    }
                    2 -> if (saved) {
                        FavoritesStore.remove(this, candidate.text)
                        Toast.makeText(this, "已取消收藏", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .show()
    }

    /** 与当前筛选场景匹配的收藏话术（keyword 空=通用，任何场景均参考）。 */
    private fun favoriteTexts(): List<String> {
        val kw = UxSettings.scenario(this)
        return FavoritesStore.all(this)
            .filter { it.keyword.isEmpty() || it.keyword == kw }
            .map { it.text }
    }

    // ===== T01260 FR-8.6：再互动建议（超 48h 未回复 → 一键「轻量回访」） =====

    /**
     * 一键「轻量回访」：复用开场建议的「重连」场景生成低压力重启话术。
     * 无当前消息上下文，从零生成；异常按 C24 降级为桩并提示。
     * ⚠ 真机验收未实测（本环境无真机）：仅保证编译通过 + 48h 判定逻辑自洽。
     */
    private fun revisitGenerate() {
        if (generating) {
            Toast.makeText(this, MSG_GENERATING, Toast.LENGTH_SHORT).show()
            return
        }
        generating = true
        iconGeneratePanel?.alpha = 0.35f
        summaryView?.text = "正在生成「轻量回访」话术…"
        Thread {
            val outcome = try {
                (cloudPipeline("亲切") ?: stubPipeline("亲切")).handleOpening("重连")
            } catch (e: CloudLlmGenerator.CloudLlmException) {
                stubPipeline("亲切").handleOpening("重连").copy(notice = e.userMessage)
            }
            mainHandler.post {
                generating = false
                iconGeneratePanel?.alpha = 1f
                showManualOutcome(outcome)
            }
        }.start()
    }

    private fun commit(text: String) {
        // T01081 二道防线：即使上游拆分遗漏（如存量候选），也只注入首个候选项的纯文本，
        // 剥离解释句——绝不把全部选项+场景解释一并发送
        val safe = expandCandidates(text)
            .firstOrNull()
            ?.let { splitRationale(it).first }
            ?.takeIf { it.isNotBlank() }
            ?: text
        currentInputConnection?.commitText(safe, 1)
        // T01112：记录注入长度供「↩ 撤回」一次有效回删
        lastCommittedLen = safe.length
    }

    /** T01112：撤回最近一次注入——deleteSurroundingText 回删（一次有效；键盘重开/切输入框重置防误删）。 */
    private fun undoLastCommit() {
        if (lastCommittedLen <= 0) {
            Toast.makeText(this, "没有可撤回的注入", Toast.LENGTH_SHORT).show()
            return
        }
        val ic = currentInputConnection
        if (ic == null || !ic.deleteSurroundingText(lastCommittedLen, 0)) {
            Toast.makeText(this, "撤回失败（输入框可能已失焦或内容已变动）", Toast.LENGTH_SHORT).show()
            lastCommittedLen = 0
            return
        }
        Toast.makeText(this, "已撤回上次注入（$lastCommittedLen 字）", Toast.LENGTH_SHORT).show()
        lastCommittedLen = 0
    }

    /** T01158 FR-9.1：色值可缺省——缺省即取当前主题（亮/暗）配色。 */
    private fun hex(color: Int): String = String.format("#%06X", 0xFFFFFF and color)

    private fun pill(text: String, bgHex: String? = null, fgHex: String? = null): TextView =
        TextView(this).apply {
            this.text = text
            textSize = 13f
            setTextColor(Color.parseColor(fgHex ?: hex(ux.chipFg)))
            background = GradientDrawable().apply {
                cornerRadius = dp(16).toFloat()
                setColor(Color.parseColor(bgHex ?: hex(ux.chipBg)))
            }
            setPadding(dp(14), dp(8), dp(14), dp(8))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { marginEnd = dp(8) }
        }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val MANUAL_HISTORY_LIMIT = 20

        /** 生成中防连点提示（多处共用同一文案）。 */
        private const val MSG_GENERATING = "正在生成中…"

        /** T01343：读屏 history 进 prompt 的条数上限（与 m1-poc preprocess.HISTORY_LIMIT 同构）。 */
        private const val HISTORY_LIMIT = 12
        private const val TAG_READ_SCREEN = "read_screen"
        private const val TAG_IME = "EmoReplyIme"
        // T01122 图标配色：常态灰 / 按下与激活态蓝（钉钉灵动回复风格）
        private const val COLOR_ICON_NORMAL = "#374151"
        private const val COLOR_ICON_ACTIVE = "#2563EB"
        // T01133：一键直切的目标输入法记忆
        private const val PREFS_IME = "ime_switch"
        private const val KEY_IME_TARGET = "last_target"
        // T01135：读屏前收起 IME 后等待动画落定的时间（ms）
        private const val IME_HIDE_SETTLE_MS = 450L
        // T01137：美化读取宿主输入框的上下文半径（字符数）
        private const val MAX_BEAUTIFY_CHARS = 2000
        // T01260 FR-8.6：再互动建议阈值——最近一条消息距 now 超 48h 未回复才提示
        private const val REVISIT_THRESHOLD_MS = 48L * 60L * 60L * 1000L
    }
}
