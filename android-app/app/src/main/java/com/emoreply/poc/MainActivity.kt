package com.emoreply.poc

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CompoundButton
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.RadioGroup
import android.widget.Toast
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.emoreply.poc.core.PromptBundle
import com.emoreply.poc.core.THEME_PRESETS
import com.emoreply.poc.generator.CloudLlmGenerator
import com.emoreply.poc.generator.DownloadOutcome
import com.emoreply.poc.generator.GeneratorSettings
import com.emoreply.poc.generator.MediaPipeOnDeviceGenerator
import com.emoreply.poc.generator.OnDevicePresets
import com.emoreply.poc.store.FeedbackStore
import com.emoreply.poc.store.KnowledgeBase
import com.emoreply.poc.store.PersonaStore
import com.emoreply.poc.store.ReplyStore
import com.emoreply.poc.store.UxSettings
import com.emoreply.poc.update.UpdateManager
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** 授权引导页：把「通知使用权 → 启用输入法 → 切为默认」三步的可达性显式化。 */
class MainActivity : AppCompatActivity() {

    private lateinit var tvLast: TextView

    // 更新状态：pendingUpdate 非 null 表示已检查到新版本、待下载安装
    private val pendingUpdate = AtomicReference<UpdateManager.Manifest?>(null)
    private val updateExecutor = Executors.newSingleThreadExecutor()
    // T01838：下载中再次点击「更新」按钮 = 中途取消；取消后 pendingUpdate 保留可续
    private val downloadCancel = AtomicBoolean(false)
    @Volatile private var downloading = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyNightMode() // T01158 FR-9.1：暗色档位必须在 setContentView 前落到 Delegate
        setContentView(R.layout.activity_main)
        tvLast = findViewById(R.id.tvLast)
        applyAmoledBlack() // FR-9.1：AMOLED 纯黑窗口底色（仅深色生效）

        findViewById<Button>(R.id.btnNotifyAccess).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        findViewById<Button>(R.id.btnImeSettings).setOnClickListener {
            startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
        }
        findViewById<Button>(R.id.btnImePicker).setOnClickListener {
            (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
        }

        setupAboutCard()
        setupLlmSettings()
        setupUxSettings() // T01158：语气强度 / 表情 / 暗色 / AMOLED
        setupHapticSettings() // T01258 FR-7.7：触觉反馈开关 + 强度档位
        setupStyleSpinner()
        setupFilterSpinners() // T01244 FR-8.2：三维筛选（关系/场景/目的）默认继承
        setupFeedbackAndClear()
        setupPersonaSection()
        setupKnowledgeSection()
        setupUpdateSection()
        showOnboardingIfNeeded() // FR-7.5:全新安装首次启动才弹三步引导
    }

    override fun onResume() {
        super.onResume()
        renderConnectionCard()
        tvLast.text = renderLastCapture()
        renderLlmStatus()
        renderFeedbackStats()
    }

    override fun onDestroy() {
        updateExecutor.shutdownNow()
        super.onDestroy()
    }

    // ---------- T01158 体验设置（FR-7.3 语气强度 / T01159 表情 / FR-9.1 暗色） ----------

    /** 体验设置只做编排：按控件逐个接线，具体行为见各 setup 函数。 */
    private fun setupUxSettings() {
        setupToneControls()
        setupMultiToneSwitch()
        setupAutoAdaptSwitch()
        setupReadScreensSlider()
        setupEmojiSwitch()
        setupDarkModeControls()
        setupThemePresetSpinner()
        setupDynamicColorSwitch()
        // T01264 FR-10.3：自定义强调色（单色）——程序化插入「动态取色」下方
        setupCustomAccent()
    }

    /**
     * 语气强度滑杆（FR-7.3）：连续强度叠加在 4 档风格之上，与 [GeneratorSettings.STYLES]
     * 并存——风格定人设、强度定表达热度。拖动时不落盘，松手才写（避免频繁 IO）。
     */
    private fun setupToneControls() {
        val slider = findViewById<com.google.android.material.slider.Slider>(R.id.slTone)
        val value = findViewById<TextView>(R.id.tvToneValue)

        // T01348：按关系自动档——开=滑杆禁用（档位随联系人关系映射）；关=手动滑杆
        val swAuto = findViewById<com.google.android.material.switchmaterial.SwitchMaterial>(R.id.swToneAuto)
        val autoListener = { _: CompoundButton, checked: Boolean ->
            UxSettings.setToneAuto(this@MainActivity, checked)
            slider.isEnabled = !checked
            if (checked) {
                value.text = "自动"
                Toast.makeText(this@MainActivity, "语气将随联系人关系自动调节（上级克制、好友活泼）", Toast.LENGTH_SHORT).show()
            } else {
                val tone = UxSettings.tone(this@MainActivity)
                slider.value = tone.toFloat()
                value.text = tone.toString()
                Toast.makeText(this@MainActivity, "语气强度回到手动：$tone", Toast.LENGTH_SHORT).show()
            }
        }
        val auto = UxSettings.toneAuto(this)
        swAuto.isChecked = auto
        slider.isEnabled = !auto
        if (auto) value.text = "自动" else {
            val tone = UxSettings.tone(this)
            slider.value = tone.toFloat()
            value.text = tone.toString()
        }
        swAuto.setOnCheckedChangeListener(autoListener)

        // 手动滑杆：拖动不落盘，松手才写（避免频繁 IO）；写入即退出 AUTO——
        // 静默同步开关态（临时摘掉监听器），避免 setToneAuto(false) 把手动值覆盖回默认 50
        slider.addOnChangeListener { _, v, _ -> value.text = v.toInt().toString() }
        slider.addOnSliderTouchListener(object : com.google.android.material.slider.Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: com.google.android.material.slider.Slider) = Unit
            override fun onStopTrackingTouch(slider: com.google.android.material.slider.Slider) {
                UxSettings.setTone(this@MainActivity, slider.value.toInt())
                if (swAuto.isChecked) {
                    swAuto.setOnCheckedChangeListener(null)
                    swAuto.isChecked = false
                    swAuto.setOnCheckedChangeListener(autoListener)
                }
                Toast.makeText(this@MainActivity, "语气强度已保存：${slider.value.toInt()}", Toast.LENGTH_SHORT).show()
            }
        })
    }

    /** T01349：多风格并行候选开关（默认关=旧行为，便于灰度对比延迟增量）。 */
    private fun setupMultiToneSwitch() {
        val swMultiTone = findViewById<CompoundButton>(R.id.swMultiTone)
        swMultiTone.isChecked = UxSettings.multiTone(this)
        swMultiTone.setOnCheckedChangeListener { _, checked ->
            UxSettings.setMultiTone(this@MainActivity, checked)
            Toast.makeText(
                this@MainActivity,
                if (checked) "每次生成将同时给出克制/中性/活泼各一条候选"
                else "已恢复单一语气生成",
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    /** T01350：采纳率闭环开关（默认开；关闭后行为与闭环上线前一致）。 */
    private fun setupAutoAdaptSwitch() {
        val swAutoAdapt = findViewById<CompoundButton>(R.id.swAutoAdapt)
        swAutoAdapt.isChecked = UxSettings.autoAdapt(this)
        swAutoAdapt.setOnCheckedChangeListener { _, checked ->
            UxSettings.setAutoAdapt(this@MainActivity, checked)
            Toast.makeText(
                this@MainActivity,
                if (checked) "低分场景将自动下调语气（样本≥10 且采纳率<30% 才生效）"
                else "已关闭自动降档，语气不再随采纳率调整",
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    /** T01343：读屏范围（屏数 1-3，默认 1）——松手才落盘（避免频繁 IO）。 */
    private fun setupReadScreensSlider() {
        val slScreens = findViewById<com.google.android.material.slider.Slider>(R.id.slReadScreens)
        val tvScreens = findViewById<TextView>(R.id.tvReadScreensValue)
        val screens = UxSettings.readScreens(this)
        slScreens.value = screens.toFloat()
        tvScreens.text = screens.toString()
        slScreens.addOnChangeListener { _, v, _ -> tvScreens.text = v.toInt().toString() }
        slScreens.addOnSliderTouchListener(object : com.google.android.material.slider.Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: com.google.android.material.slider.Slider) = Unit
            override fun onStopTrackingTouch(slider: com.google.android.material.slider.Slider) {
                val n = slider.value.toInt()
                UxSettings.setReadScreens(this@MainActivity, n)
                Toast.makeText(this@MainActivity, "读屏范围已保存：${n} 屏", Toast.LENGTH_SHORT).show()
            }
        })
    }

    /** T01159：候代表情开关（关=提示词不提 + 输出侧剥除）。 */
    private fun setupEmojiSwitch() {
        val swEmoji = findViewById<CompoundButton>(R.id.swEmoji)
        swEmoji.isChecked = UxSettings.emojiEnabled(this)
        swEmoji.setOnCheckedChangeListener { _, checked ->
            UxSettings.setEmojiEnabled(this, checked)
            Toast.makeText(
                this,
                if (checked) "候选回复将带表情" else "候选回复不再带表情",
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    /** FR-9.1：暗色模式档位（跟随系统 / 浅色 / 深色）+ AMOLED 纯黑（仅深色档位生效）。 */
    private fun setupDarkModeControls() {
        val rg = findViewById<RadioGroup>(R.id.rgDark)
        rg.check(
            when (UxSettings.darkMode(this)) {
                UxSettings.DARK_ON -> R.id.rbDarkOn
                UxSettings.DARK_OFF -> R.id.rbDarkOff
                else -> R.id.rbDarkFollow
            },
        )
        rg.setOnCheckedChangeListener { _, checkedId ->
            val mode = when (checkedId) {
                R.id.rbDarkOn -> UxSettings.DARK_ON
                R.id.rbDarkOff -> UxSettings.DARK_OFF
                else -> UxSettings.DARK_FOLLOW
            }
            if (mode == UxSettings.darkMode(this)) return@setOnCheckedChangeListener
            UxSettings.setDarkMode(this, mode)
            applyNightMode()
            recreate() // 档位变更需重建 Activity 才能重走资源解析（Delegate 生效）
        }

        // FR-9.1：AMOLED 纯黑（仅深色档位生效，背景纯黑不发灰）
        val swAmoled = findViewById<CompoundButton>(R.id.swAmoled)
        swAmoled.isChecked = UxSettings.amoled(this)
        swAmoled.setOnCheckedChangeListener { _, checked ->
            UxSettings.setAmoled(this, checked)
            applyAmoledBlack()
            Toast.makeText(
                this,
                if (checked) "AMOLED 纯黑已开启" else "AMOLED 纯黑已关闭",
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    /** T01254 FR-10.2：主题预设（强调色 seed）——变更即落盘并重建刷新。 */
    private fun setupThemePresetSpinner() {
        val spPreset = findViewById<Spinner>(R.id.spThemePreset)
        val presetIds = THEME_PRESETS.keys.toList()
        val presetLabels = THEME_PRESETS.values.map { it.first }
        spPreset.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, presetLabels)
        spPreset.setSelection(presetIds.indexOf(UxSettings.themePreset(this)).coerceAtLeast(0))
        spPreset.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var first = true
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (first) { first = false; return } // 初始回填不触发保存（与 spStyle 口径一致）
                val preset = presetIds[pos]
                if (preset == UxSettings.themePreset(this@MainActivity)) return
                UxSettings.setThemePreset(this@MainActivity, preset)
                Toast.makeText(this@MainActivity, "主题预设：${presetLabels[pos]}", Toast.LENGTH_SHORT).show()
                recreate()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
    }

    /** T01253 FR-10.1：Material You 动态取色开关——变更即落盘并重建刷新。 */
    private fun setupDynamicColorSwitch() {
        val swDynamic = findViewById<CompoundButton>(R.id.swDynamicColor)
        swDynamic.isChecked = UxSettings.dynamicColor(this)
        swDynamic.setOnCheckedChangeListener { _, checked ->
            UxSettings.setDynamicColor(this, checked)
            Toast.makeText(
                this,
                if (checked) "动态取色已开启（Android 12+ 从壁纸取强调色）" else "动态取色已关闭（回落预设）",
                Toast.LENGTH_SHORT,
            ).show()
            recreate()
        }
    }

    /**
     * T01264 FR-10.3：自定义强调色选择——Spinner 列几个单色选项，选中即落盘并重建刷新。
     * 优先级：动态取色 > 自定义强调色 > 主题预设 > 默认（见 UxTheme.uxTheme）。
     * 自定义强调色驱动 IME 候选选中态/图标激活态/理由标签/模板条（均取 ux.accent/iconActive），
     * ⚠ 真机三处元素观感未实测（本环境无真机）：仅保证优先级链与持久化逻辑正确。
     */
    private fun setupCustomAccent() {
        val anchor = findViewById<CompoundButton>(R.id.swDynamicColor)
        val parent = anchor.parent as? LinearLayout ?: return
        val density = resources.displayMetrics.density
        val options = listOf(
            "跟随预设" to "",
            "默认蓝 #2563EB" to "#2563EB",
            "品牌青绿 #14B8A6" to "#14B8A6",
            "Nord #88C0D0" to "#88C0D0",
            "Monokai #AE81FF" to "#AE81FF",
            "玫红 #F43F5E" to "#F43F5E",
            "暖橙 #F97316" to "#F97316",
            "紫 #8B5CF6" to "#8B5CF6",
        )
        val current = UxSettings.customAccent(this)
        val title = TextView(this).apply {
            text = "自定义强调色（单色，优先于预设）"
            textSize = 12f
            setTextColor(Color.parseColor("#6B7280"))
            setPadding(0, (12 * density).toInt(), 0, 0)
        }
        val sp = Spinner(this)
        sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, options.map { it.first })
        sp.setSelection(options.indexOfFirst { it.second == current }.coerceAtLeast(0))
        sp.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var first = true
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (first) { first = false; return } // 初始回填不触发保存（与 spStyle 口径一致）
                val hex = options[pos].second
                if (hex == UxSettings.customAccent(this@MainActivity)) return
                UxSettings.setCustomAccent(this@MainActivity, hex)
                Toast.makeText(
                    this@MainActivity,
                    if (hex.isEmpty()) "已跟随预设" else "自定义强调色：${options[pos].first}",
                    Toast.LENGTH_SHORT,
                ).show()
                recreate()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        // 插入在「动态取色」开关之后（anchor 后第一/二个位置）
        parent.addView(title, parent.indexOfChild(anchor) + 1)
        parent.addView(sp, parent.indexOfChild(anchor) + 2)
    }

    /**
     * T01258 FR-7.7：触觉反馈开关 + 强度档位（0=关 1=轻 2=中）。
     * 程序化构建并插入「生成质量」卡（tvFeedback 之前），不新增 XML——
     * 与既有开关/spinner 同口径：开关关掉时强度下拉一并禁用；两处都关则 IME 侧整体跳过。
     */
    private fun setupHapticSettings() {
        val anchor = findViewById<TextView>(R.id.tvFeedback)
        val parent = anchor.parent as? LinearLayout ?: return
        val density = resources.displayMetrics.density
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setPadding(0, (8 * density).toInt(), 0, 0)
        }
        val sw = com.google.android.material.switchmaterial.SwitchMaterial(this).apply {
            text = "触觉反馈（候选采纳/注入/长按振动，强度可调）"
            isChecked = UxSettings.hapticEnabled(this@MainActivity)
        }
        val sp = Spinner(this)
        val labels = arrayOf("关闭", "轻", "中")
        sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        sp.setSelection(
            UxSettings.hapticLevel(this@MainActivity)
                .coerceIn(UxSettings.HAPTIC_OFF, UxSettings.HAPTIC_MEDIUM),
        )
        sp.isEnabled = sw.isChecked
        sw.setOnCheckedChangeListener { _, checked ->
            UxSettings.setHapticEnabled(this@MainActivity, checked)
            sp.isEnabled = checked
            Toast.makeText(
                this@MainActivity,
                if (checked) "触觉反馈已开启" else "触觉反馈已关闭",
                Toast.LENGTH_SHORT,
            ).show()
        }
        sp.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var first = true
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (first) { first = false; return } // 初始回填不触发保存（与 spStyle 口径一致）
                UxSettings.setHapticLevel(this@MainActivity, pos)
                Toast.makeText(this@MainActivity, "触觉强度：${labels[pos]}", Toast.LENGTH_SHORT).show()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        row.addView(sw, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(
            sp,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT),
        )
        parent.addView(row, parent.indexOfChild(anchor))
    }

    /**
     * T01244 FR-8.2：三维筛选（关系×场景×目的）下拉。首项均为空串=不筛选；
     * 选中后落盘 UxSettings（下次生成继承）。初始回填不触发保存（与 spStyle 一致）。
     */
    private fun setupFilterSpinners() {
        fun bind(
            spinnerId: Int,
            options: List<String>,
            current: String,
            onSelect: (String) -> Unit,
        ) {
            val sp = findViewById<Spinner>(spinnerId)
            val labels = options.map { if (it.isEmpty()) "不筛选" else it }
            sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
            sp.setSelection(options.indexOf(current).coerceAtLeast(0))
            sp.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                private var first = true
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                    if (first) { first = false; return }
                    onSelect(options[pos])
                }
                override fun onNothingSelected(parent: AdapterView<*>?) = Unit
            }
        }
        bind(R.id.spFilterRelation, UxSettings.RELATIONS, UxSettings.relation(this)) {
            saveFilter(relation = it)
        }
        bind(R.id.spFilterScenario, UxSettings.SCENARIOS, UxSettings.scenario(this)) {
            saveFilter(scenario = it)
        }
        bind(R.id.spFilterPurpose, UxSettings.PURPOSES, UxSettings.purpose(this)) {
            saveFilter(purpose = it)
        }
    }

    /** 任一维变更即把三维一并落盘（保持三维同一次选择的一致性）。 */
    private fun saveFilter(relation: String = UxSettings.relation(this),
                           scenario: String = UxSettings.scenario(this),
                           purpose: String = UxSettings.purpose(this)) {
        UxSettings.setFilter(this, relation, scenario, purpose)
        Toast.makeText(this, "三维筛选已保存", Toast.LENGTH_SHORT).show()
    }

    /** 把暗色档位落到 AppCompatDelegate（FOLLOW_SYSTEM 时系统切换自动跟随）。 */
    private fun applyNightMode() {
        val mode = when (UxSettings.darkMode(this)) {
            UxSettings.DARK_ON -> AppCompatDelegate.MODE_NIGHT_YES
            UxSettings.DARK_OFF -> AppCompatDelegate.MODE_NIGHT_NO
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
        if (AppCompatDelegate.getDefaultNightMode() != mode) AppCompatDelegate.setDefaultNightMode(mode)
    }

    /** AMOLED：深色下把窗口与内容根背景置纯黑（Material 默认夜间是深灰，非纯黑）。 */
    private fun applyAmoledBlack() {
        val black = ColorDrawable(Color.BLACK)
        when {
            UxSettings.amoled(this) && UxSettings.isDarkActive(this) -> {
                window.setBackgroundDrawable(black)
                findViewById<View>(android.R.id.content).setBackgroundColor(Color.BLACK)
            }
        }
    }

    // ---------- 连接状态卡（T01090：语义色 + 就绪折叠引导） ----------

    /** 全部就绪：圆点变绿、摘要一行、引导按钮折叠；有缺失：琥珀点、缺失项按钮展开。 */
    private fun renderConnectionCard() {
        val notifyOk = isNotificationAccessGranted()
        val imeOk = isImeEnabled()
        val defaultOk = isImeDefault()
        val dot = findViewById<View>(R.id.vConnDot)
        val summary = findViewById<TextView>(R.id.tvConnSummary)
        val missing = listOf(notifyOk, imeOk, defaultOk).count { !it }
        if (missing == 0) {
            dot.background.setTint(getColor(R.color.conn_ok))
            summary.text = getString(R.string.connection_ready_detail)
        } else {
            dot.background.setTint(getColor(R.color.conn_todo))
            summary.text = getString(R.string.connection_todo, missing)
        }
        findViewById<Button>(R.id.btnNotifyAccess).visibility = if (notifyOk) View.GONE else View.VISIBLE
        findViewById<Button>(R.id.btnImeSettings).visibility = if (imeOk) View.GONE else View.VISIBLE
        findViewById<Button>(R.id.btnImePicker).visibility = if (defaultOk) View.GONE else View.VISIBLE
    }

    /** 云端生成设置：密钥用户自填、仅存本机私有目录（T01043 密钥管理决策）。 */
    private fun setupLlmSettings() {
        val config = GeneratorSettings.load(this)
        // T01147：精简模式开关（键盘隐藏消息内容框与汇总提示框）
        val swCompact = findViewById<CompoundButton>(R.id.swCompactMode)
        swCompact.isChecked = GeneratorSettings.isCompactMode(this)
        swCompact.setOnCheckedChangeListener { _, checked ->
            GeneratorSettings.setCompactMode(this, checked)
            Toast.makeText(
                this,
                if (checked) "精简模式已开启：下次键盘弹出生效" else "精简模式已关闭：下次键盘弹出生效",
                Toast.LENGTH_SHORT,
            ).show()
        }
        findViewById<CompoundButton>(R.id.cbCloudEnabled).isChecked = config.enabled
        findViewById<EditText>(R.id.etBaseUrl).setText(config.baseUrl)
        findViewById<EditText>(R.id.etModel).setText(config.model)

        // T01243 FR-8.1：端侧离线后端。路径可直接被 btnSaveLlm 保存；「下载模型」则把
        // 已下载本地 .task 的绝对路径回填到 etModelPath，用户亦可 adb push 后手填路径。
        findViewById<CompoundButton>(R.id.swOnDevice).isChecked = config.onDevice
        findViewById<EditText>(R.id.etModelPath).setText(config.modelPath)
        findViewById<EditText>(R.id.etModelUrl).setText(config.modelUrl)
        findViewById<Button>(R.id.btnDownloadModel).setOnClickListener {
            val url = findViewById<EditText>(R.id.etModelUrl).text.toString().trim()
            if (url.isBlank()) {
                toast("请先填写可直连的 .task 模型下载地址（Gemma 需自备鉴权链接）")
                return@setOnClickListener
            }
            val btn = findViewById<Button>(R.id.btnDownloadModel)
            val status = findViewById<TextView>(R.id.tvOnDeviceStatus)
            btn.isEnabled = false
            status.text = "准备下载…"
            updateExecutor.execute {
                // 落地文件名由 URL 末段决定（不同模型互不覆盖），失败原因可直接展示
                val outcome = MediaPipeOnDeviceGenerator.ensureDownloaded(filesDir, url) { bytes ->
                    runOnUiThread { status.text = "下载中…已收 ${bytes / 1024 / 1024}MB" }
                }
                runOnUiThread {
                    btn.isEnabled = true
                    when (outcome) {
                        is DownloadOutcome.Success -> {
                            val file = outcome.file
                            findViewById<EditText>(R.id.etModelPath).setText(file.absolutePath)
                            findViewById<CompoundButton>(R.id.swOnDevice).isChecked = true
                            status.text = "下载完成：${file.length() / 1024 / 1024}MB，路径 ${file.absolutePath}"
                        }
                        is DownloadOutcome.Failure -> {
                            status.text = outcome.reason
                            toast(outcome.reason)
                        }
                    }
                }
            }
        }
        setupModelPresets()
        renderOnDeviceStatus()
        // T01292：已存密钥回显为掩码——真实值写入 EditText（inputType=textPassword 视觉转成
        // ••••），眼睛图标（password_toggle）点击即明文核对；不再用「（已设置）」占位提示。
        val etKey = findViewById<EditText>(R.id.etApiKey)
        if (config.apiKey.isNotBlank()) etKey.setText(config.apiKey)

        findViewById<Button>(R.id.btnSaveLlm).setOnClickListener {
            val current = GeneratorSettings.load(this)
            val typedKey = etKey.text.toString().trim()
            val next = current.copy(
                enabled = findViewById<CompoundButton>(R.id.cbCloudEnabled).isChecked,
                baseUrl = findViewById<EditText>(R.id.etBaseUrl).text.toString().trim()
                    .ifBlank { GeneratorSettings.defaultBaseUrl() },
                model = findViewById<EditText>(R.id.etModel).text.toString().trim()
                    .ifBlank { GeneratorSettings.defaultModel() },
                // 留空 = 沿用已保存的密钥，避免每次进页面都得重输
                apiKey = typedKey.ifBlank { current.apiKey },
                // T01243 FR-8.1：端侧离线后端设置一并保存
                onDevice = findViewById<CompoundButton>(R.id.swOnDevice).isChecked,
                modelPath = findViewById<EditText>(R.id.etModelPath).text.toString().trim(),
                modelUrl = findViewById<EditText>(R.id.etModelUrl).text.toString().trim(),
            )
            GeneratorSettings.save(this, next)
            // T01292：保存后保留掩码回显（••••），眼睛图标可明文核对；清空则无法满足需求
            etKey.setText(next.apiKey)
            toast(R.string.llm_status_saved)
            renderLlmStatus()
            renderOnDeviceStatus()
        }

        // T01091：连通性自检——用当前表单/已存配置发一次最小生成请求，回显模型名与耗时
        findViewById<Button>(R.id.btnTestConnection).setOnClickListener {
            val current = GeneratorSettings.load(this)
            val config = current.copy(
                enabled = findViewById<CompoundButton>(R.id.cbCloudEnabled).isChecked,
                baseUrl = findViewById<EditText>(R.id.etBaseUrl).text.toString().trim()
                    .ifBlank { GeneratorSettings.defaultBaseUrl() },
                model = findViewById<EditText>(R.id.etModel).text.toString().trim()
                    .ifBlank { GeneratorSettings.defaultModel() },
                apiKey = findViewById<EditText>(R.id.etApiKey).text.toString().trim()
                    .ifBlank { current.apiKey },
            )
            if (config.apiKey.isBlank()) {
                toast(R.string.llm_test_need_key)
                return@setOnClickListener
            }
            val btn = findViewById<Button>(R.id.btnTestConnection)
            btn.isEnabled = false
            btn.text = getString(R.string.llm_test_running)
            updateExecutor.execute {
                val start = System.currentTimeMillis()
                val result = runCatching {
                    // 最小请求：candidates=1，正文极短，只为验证连通性/鉴权
                    CloudLlmGenerator(config.baseUrl, config.apiKey, config.model, candidates = 1)
                        .generate(PromptBundle(system = "你是连通性测试。", current = "回复：OK"))
                }
                val elapsed = System.currentTimeMillis() - start
                runOnUiThread {
                    btn.isEnabled = true
                    btn.text = getString(R.string.llm_test_connection)
                    result.fold(
                        onSuccess = {
                            renderLlmStatus()
                            findViewById<TextView>(R.id.tvLlmStatus).text =
                                getString(R.string.llm_test_ok, config.model, "${elapsed}ms")
                        },
                        onFailure = { e ->
                            val msg = (e as? CloudLlmGenerator.CloudLlmException)?.userMessage
                                ?: (e.message ?: "未知错误")
                            findViewById<TextView>(R.id.tvLlmStatus).text =
                                getString(R.string.update_failed, msg)
                        },
                    )
                }
            }
        }
    }

    private fun renderLlmStatus() {
        val config = GeneratorSettings.load(this)
        val tv = findViewById<TextView>(R.id.tvLlmStatus)
        tv.text = if (GeneratorSettings.isCloudReady(config)) {
            getString(R.string.llm_status_on, config.baseUrl, config.model)
        } else {
            getString(R.string.llm_status_off)
        }
    }

    /**
     * T01344：端侧模型预制选项——为每个常用模型生成一个按钮，点击即自动填好路径，
     * 并弹出该模型在 PC 端的放置命令（run-as + adb push 三条，可长按复制）。
     * 「打开官方下载页」按钮跳到对应 Kaggle 页面取文件与版本。
     *
     * 路径预置在应用私有 cache 目录而非 `/data/local/tmp`：后者是官方 Linux 脚本的
     * 目录，Android 应用受 SELinux 沙箱限制读不到它，回填了也永远「文件缺失」（详见
     * [OnDevicePresets] 的 KDoc）。刻意**不把模型直链硬编码进代码**：端侧模型 1-3GB，
     * 直链失效的代价是用户白等几十分钟。
     */
    private fun setupModelPresets() {
        val container = findViewById<LinearLayout>(R.id.llModelPresets)
        val etPath = findViewById<EditText>(R.id.etModelPath)
        val status = findViewById<TextView>(R.id.tvOnDeviceStatus)
        OnDevicePresets.PRESETS.forEach { preset ->
            val btn = com.google.android.material.button.MaterialButton(
                this,
                null,
                com.google.android.material.R.attr.materialButtonOutlinedStyle,
            ).apply {
                text = preset.label
                textSize = 11f
                minHeight = 0
                minimumHeight = 0
                setPadding(8, 8, 8, 8)
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginEnd = 4 }
            }
            btn.setOnClickListener {
                etPath.setText(OnDevicePresets.pathOf(preset))
                etPath.setSelection(etPath.text.length)
                status.text = "${preset.fileName}：${preset.hint}"
                showPushCommandsDialog(preset)
            }
            container.addView(btn)
        }
        findViewById<Button>(R.id.btnModelSource).setOnClickListener {
            val preset = OnDevicePresets.match(etPath.text.toString())
                ?: OnDevicePresets.PRESETS.first()
            runCatching {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(preset.sourcePage)))
            }.onFailure {
                toast("无法打开下载页：${preset.sourcePage}")
            }
        }
    }

    /**
     * 回填路径后紧接着展示 PC 端放置命令：用户之前反馈「按钮无作用」，根因是只填了
     * 路径却没告知文件该如何放到位。对话框内命令为可选中文本，长按即可复制整段。
     */
    private fun showPushCommandsDialog(preset: OnDevicePresets.ModelPreset) {
        val tv = TextView(this).apply {
            text = OnDevicePresets.pushCommands(preset.fileName)
            setTextIsSelectable(true)
            textSize = 12f
            typeface = android.graphics.Typeface.MONOSPACE
            val p = (8 * resources.displayMetrics.density).toInt()
            setPadding(p, p, p, p)
        }
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("把 ${preset.fileName} 放进手机")
            .setMessage(
                "① 先在电脑上从官方页下载好该文件（Kaggle 需登录，无匿名直链）；" +
                    "② 手机用 USB 连电脑开 adb；③ 长按下方命令复制、把文件路径改成实际位置后逐条执行；" +
                    "④ 回到本页点该模型按钮回填路径即可。"
            )
            .setView(tv)
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    /** T01243 FR-8.1：端侧可用性回显（开关 + 模型文件是否就位）。 */
    private fun renderOnDeviceStatus() {
        val config = GeneratorSettings.load(this)
        findViewById<TextView>(R.id.tvOnDeviceStatus).text = when {
            !config.onDevice -> "端侧离线未启用（需开启开关并放好模型）"
            GeneratorSettings.isOnDeviceReady(config) -> "端侧就绪：${config.modelPath}"
            else -> "端侧模型文件缺失：${config.modelPath.ifBlank { "（未填写路径）" }}"
        }
    }

    // ---------- 回复风格（FR-3.2 / REQ-009） ----------

    private fun setupStyleSpinner() {
        val sp = findViewById<Spinner>(R.id.spStyle)
        sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, GeneratorSettings.STYLES)
        val current = GeneratorSettings.load(this).style
        sp.setSelection(GeneratorSettings.STYLES.indexOf(current).coerceAtLeast(0))
        sp.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var first = true
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (first) { first = false; return } // 初始回填不触发保存
                val style = GeneratorSettings.STYLES[pos]
                GeneratorSettings.save(this@MainActivity, GeneratorSettings.load(this@MainActivity).copy(style = style))
                toast(getString(R.string.style_saved, style))
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
    }

    // ---------- 反馈统计 + 一键清除（FR-4.3 / FR-6.3） ----------

    private fun setupFeedbackAndClear() {
        findViewById<Button>(R.id.btnClearData).setOnClickListener {
            // T01087（P1）：清除动作不可逆——先经确认对话框，避免误触一次性抹掉
            // API Key / 人设 / 知识库 / 模板等全部本地数据
            com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
                .setTitle(R.string.clear_confirm_title)
                .setMessage(R.string.clear_confirm_msg)
                .setPositiveButton(R.string.clear_confirm_yes) { _, _ -> clearAllLocalData() }
                .setNegativeButton(R.string.clear_confirm_no, null)
                .show()
        }
        renderFeedbackStats()
    }

    /** FR-6.3：恢复初始态——反馈统计、云端设置、待回复缓存、人设关系、知识库、模板全部清空。 */
    private fun clearAllLocalData() {
        FeedbackStore.clearAll(this)
        GeneratorSettings.clearAll(this)
        PersonaStore.get(this).wipe()
        KnowledgeBase.get(this).wipe()
        com.emoreply.poc.store.TemplateStore.clear(this)
        ReplyStore.clear()
        setupLlmSettings()
        setupPersonaSection()
        renderKbStatus()
        renderLlmStatus()
        renderFeedbackStats()
        com.google.android.material.snackbar.Snackbar.make(
            findViewById(android.R.id.content), R.string.clear_data_done,
            com.google.android.material.snackbar.Snackbar.LENGTH_SHORT,
        ).show()
    }

    // ---------- RAG 知识库（决策③ / REQ-020，本机加密存储） ----------

    private fun setupKnowledgeSection() {
        val sp = findViewById<Spinner>(R.id.spKbCategory)
        sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            KnowledgeBase.CATEGORY_LABELS)
        findViewById<Button>(R.id.btnKbSave).setOnClickListener {
            val text = findViewById<EditText>(R.id.etKbText).text.toString().trim()
            if (text.isEmpty()) {
                toast(R.string.kb_need_text)
                return@setOnClickListener
            }
            val category = KnowledgeBase.CATEGORIES[sp.selectedItemPosition]
            val contact = findViewById<EditText>(R.id.etKbContact).text.toString().trim()
            KnowledgeBase.get(this).add(category, text, contact.ifEmpty { null })
            findViewById<EditText>(R.id.etKbText).text.clear()
            findViewById<EditText>(R.id.etKbContact).text.clear()
            toast(R.string.kb_saved)
            renderKbStatus()
        }
        renderKbStatus()
    }

    private fun renderKbStatus() {
        findViewById<TextView>(R.id.tvKbStatus).text =
            getString(R.string.kb_status, KnowledgeBase.get(this).size())
    }

    // ---------- 人设与关系（FR-5.1 / FR-5.2，AES-256-GCM 本地加密存储） ----------

    private fun setupPersonaSection() {
        val persona = PersonaStore.get(this)
        val cur = persona.getPersona()
        findViewById<EditText>(R.id.etPersonaMe).setText(cur.me)
        findViewById<EditText>(R.id.etPersonaBanned).setText(cur.banned.joinToString("，"))

        findViewById<Button>(R.id.btnPersonaSave).setOnClickListener {
            val me = findViewById<EditText>(R.id.etPersonaMe).text.toString().trim().ifBlank { "我" }
            val banned = findViewById<EditText>(R.id.etPersonaBanned).text.toString()
                .split("，", ",", "、").map { it.trim() }.filter { it.isNotEmpty() }
            persona.setPersona(me = me, banned = banned)
            toast(R.string.persona_saved)
        }

        val sp = findViewById<Spinner>(R.id.spRelation)
        val relations = PersonaStore.RELATIONS.split(",")
        sp.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, relations)
        findViewById<Button>(R.id.btnRelationSave).setOnClickListener {
            val name = findViewById<EditText>(R.id.etContactName).text.toString().trim()
            if (name.isEmpty()) {
                toast(R.string.relation_need_name)
                return@setOnClickListener
            }
            persona.setRelation(name, relations[sp.selectedItemPosition])
            toast(getString(R.string.relation_saved, name, relations[sp.selectedItemPosition]))
        }
    }

    private fun renderFeedbackStats() {
        val tv = findViewById<TextView>(R.id.tvFeedback)
        val s = FeedbackStore.stats(this)
        val rate = s.adoptionRate()
        tv.text = if (rate == null) {
            getString(R.string.feedback_empty)
        } else {
            // T01257 FR-7.6：使用统计——今日/累计采纳、采纳率、分应用分布、平均生成耗时
            val sourceSummary = if (s.bySource.isEmpty()) "—" else
                s.bySource.entries.sortedByDescending { it.value }
                    .joinToString("、") { "${it.key} ${it.value}" }
            val avg = s.avgGenMs?.let { "${it}ms" } ?: "—"
            buildString {
                append("今日采纳 ${s.todayAdopted}｜累计 采纳 ${s.adopted} / 忽略 ${s.ignored}（采纳率 ${"%.0f".format(rate * 100)}%）\n")
                append("分应用：$sourceSummary\n")
                append("平均生成耗时：$avg（样本 ${s.genSamples} 次）")
            }
        }
    }

    // ---------- 版本更新（T00881） ----------

    private fun setupUpdateSection() {
        val etUrl = findViewById<EditText>(R.id.etUpdateUrl)
        // 回显上次填过的清单地址，便于启动自动检查
        val saved = getPreferences(Context.MODE_PRIVATE).getString(KEY_UPDATE_URL, null)
        if (!saved.isNullOrBlank()) etUrl.setText(saved)

        findViewById<Button>(R.id.btnCheckUpdate).setOnClickListener {
            val url = etUrl.text.toString().trim()
            if (url.isBlank()) {
                toastUpdateStatus(getString(R.string.update_failed, "请先填写版本清单 URL"))
                return@setOnClickListener
            }
            getPreferences(Context.MODE_PRIVATE).edit().putString(KEY_UPDATE_URL, url).apply()
            checkUpdate(url)
        }
        findViewById<Button>(R.id.btnDoUpdate).setOnClickListener {
            if (downloading) {
                // T01838：下载中点击按钮 = 中途取消；pendingUpdate 保留，取消后可再次点击继续
                downloadCancel.set(true)
                return@setOnClickListener
            }
            val manifest = pendingUpdate.get() ?: return@setOnClickListener
            startDownload(manifest)
        }
        // 启动自动检查（仅当有已保存的清单地址）
        if (!saved.isNullOrBlank()) checkUpdate(saved)
    }

    private fun checkUpdate(manifestUrl: String) {
        val btn = findViewById<Button>(R.id.btnDoUpdate)
        btn.isEnabled = false
        pendingUpdate.set(null)
        toastUpdateStatus(getString(R.string.update_checking))
        updateExecutor.execute {
            try {
                val found = UpdateManager.check(this, manifestUrl)
                runOnUiThread {
                    if (found != null) {
                        pendingUpdate.set(found)
                        btn.text = getString(R.string.update_do, found.versionName)
                        btn.isEnabled = true
                        val local = UpdateManager.localVersion(this)
                        toastUpdateStatus(
                            "${found.versionName}（当前 ${local.versionName}）\n${found.notes}"
                        )
                    } else {
                        val local = UpdateManager.localVersion(this)
                        toastUpdateStatus(getString(R.string.update_latest, local.versionName))
                    }
                }
            } catch (e: UpdateManager.UpdateException) {
                runOnUiThread { toastUpdateStatus(getString(R.string.update_failed, e.userMessage)) }
            }
        }
    }

    private fun startDownload(manifest: UpdateManager.Manifest) {
        val btn = findViewById<Button>(R.id.btnDoUpdate)
        val btnCheck = findViewById<Button>(R.id.btnCheckUpdate)
        val pb = findViewById<ProgressBar>(R.id.pbUpdate)
        downloading = true
        downloadCancel.set(false)
        // T01838：下载中按钮切「取消下载」（点击即取消）；检测按钮禁用防下载中被重复检查
        btn.text = getString(R.string.update_cancel)
        btn.isEnabled = true
        btnCheck.isEnabled = false
        pb.visibility = View.VISIBLE
        pb.progress = 0
        toastUpdateStatus(getString(R.string.update_downloading, 0))
        val apk = UpdateManager.cacheApkFile(this)
        apk.parentFile?.mkdirs()
        updateExecutor.execute {
            try {
                UpdateManager.download(manifest.apkUrl, apk, { pct ->
                    runOnUiThread {
                        pb.progress = pct
                        toastUpdateStatus(getString(R.string.update_downloading, pct))
                    }
                }, downloadCancel)
                runOnUiThread {
                    downloading = false
                    pb.visibility = View.GONE
                    toastUpdateStatus(getString(R.string.update_download_done))
                    setBtnUpdateIdle(btn, manifest)
                    btnCheck.isEnabled = true
                    UpdateManager.install(this, apk)
                }
            } catch (_: UpdateManager.UpdateCancelledException) {
                runOnUiThread {
                    downloading = false
                    pb.visibility = View.GONE
                    toastUpdateStatus(getString(R.string.update_cancelled))
                    setBtnUpdateIdle(btn, manifest)
                    btnCheck.isEnabled = true
                }
            } catch (e: UpdateManager.UpdateException) {
                runOnUiThread {
                    downloading = false
                    pb.visibility = View.GONE
                    toastUpdateStatus(getString(R.string.update_failed, e.userMessage))
                    // 允许重新点击继续（重新下载）
                    setBtnUpdateIdle(btn, manifest)
                    btnCheck.isEnabled = true
                }
            }
        }
    }

    /** 按钮回到「发现新版本 X，立即更新」待命态（pendingUpdate 保留，可再次点击）。 */
    private fun setBtnUpdateIdle(btn: Button, manifest: UpdateManager.Manifest) {
        btn.text = getString(R.string.update_do, manifest.versionName)
        btn.isEnabled = true
    }

    private fun toastUpdateStatus(text: String) {
        findViewById<TextView>(R.id.tvUpdateStatus).text = text
    }

    // ---------- FR-7.5 首次引导向导 ----------

    /** 尚未完成引导才弹窗；已完成直接进入主界面（持久化于 UxSettings.ime_ui_settings）。 */
    private fun showOnboardingIfNeeded() {
        if (!UxSettings.onboardingDone(this)) showOnboardingFlow(0)
    }

    /** 三步引导：①启用键盘 ②授权通知 ③第一次生成。每步给直达入口，可任意点「跳过」。 */
    private fun showOnboardingFlow(step: Int) {
        val titles = arrayOf(
            "① 启用 EmoReply 键盘",
            "② 授权通知／无障碍权限",
            "③ 完成第一次生成",
        )
        val bodies = arrayOf(
            "打开系统输入法设置，把 EmoReply 加入「已启用键盘」列表，\n启用后即可在任意输入框呼出。",
            "允许 EmoReply 读取通知（并视需开启无障碍），\n收到消息时键盘才会自动弹出回复候选。",
            "配置完成后，收到新消息会自动生成候选回复；\n也可回到本页用「测试连接」先验证云端生成链路。",
        )
        val isLast = step == titles.lastIndex
        MaterialAlertDialogBuilder(this)
            .setTitle(titles[step])
            .setMessage(bodies[step])
            .setCancelable(false) // 强制走「下一步/完成」或「跳过」，保证被标记掉
            .setPositiveButton(if (isLast) "完成" else "下一步") { _, _ ->
                if (isLast) skipOnboarding() else showOnboardingFlow(step + 1)
            }
            .setNegativeButton("跳过") { _, _ -> skipOnboarding() }
            .apply {
                // 前两步提供直达系统设置的入口；第三步信息完备，仅留「完成」
                when (step) {
                    0 -> setNeutralButton("去启用") { _, _ ->
                        startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
                    }
                    1 -> setNeutralButton("去授权") { _, _ ->
                        startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                    }
                }
            }
            .show()
    }

    /** 完成或跳过后落盘标记，重启不再弹。 */
    private fun skipOnboarding() = UxSettings.setOnboardingDone(this, true)

    companion object {
        private const val KEY_UPDATE_URL = "update_manifest_url"
    }

    /** T01092：保存类操作统一 Snackbar 反馈（原 Toast）。 */
    private fun toast(resId: Int) {
        com.google.android.material.snackbar.Snackbar.make(
            findViewById(android.R.id.content), resId,
            com.google.android.material.snackbar.Snackbar.LENGTH_SHORT,
        ).show()
    }

    /** T01092：带格式化参数的 Snackbar 反馈。 */
    private fun toast(text: String) {
        com.google.android.material.snackbar.Snackbar.make(
            findViewById(android.R.id.content), text,
            com.google.android.material.snackbar.Snackbar.LENGTH_SHORT,
        ).show()
    }

    // ---------- 关于与更新卡（T01093：默认折叠 + 版本号外显） ----------

    private fun setupAboutCard() {
        val version = runCatching {
            packageManager.getPackageInfo(packageName, 0).versionName
        }.getOrNull().orEmpty()
        findViewById<TextView>(R.id.tvVersion).text = version
        val header = findViewById<View>(R.id.llAboutHeader)
        val section = findViewById<View>(R.id.llUpdateSection)
        val arrow = findViewById<TextView>(R.id.tvAboutArrow)
        header.setOnClickListener {
            val expanded = section.visibility == View.VISIBLE
            section.visibility = if (expanded) View.GONE else View.VISIBLE
            arrow.text = getString(if (expanded) R.string.about_arrow_down else R.string.about_arrow_up)
        }
    }

    private fun renderLastCapture(): String {
        val pending = ReplyStore.latest() ?: return getString(R.string.last_none)
        val who = pending.notification.sender ?: "对方"
        val body = pending.notification.body.orEmpty()
        val candidates = pending.candidates.joinToString(" / ") { it.text }
        val notice = pending.notice.orEmpty()
        return getString(R.string.last_template, who, body, candidates, notice)
    }

    private fun isNotificationAccessGranted(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
            ?: return false
        return enabled.split(":").any {
            ComponentName.unflattenFromString(it)?.packageName == packageName
        }
    }

    private fun isImeEnabled(): Boolean {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        return imm.enabledInputMethodList.any { it.packageName == packageName }
    }

    private fun isImeDefault(): Boolean {
        val current = Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
        return current?.startsWith(packageName) == true
    }
}