package com.emoreply.poc.store

import android.content.Context
import android.content.res.Configuration
import com.emoreply.poc.core.TONE_DEFAULT
import com.emoreply.poc.core.TONE_MAX
import com.emoreply.poc.core.TONE_MIN

/**
 * T01158 体验设置（M-Opt1）：语气强度 / 表情开关 / 暗色模式 / AMOLED 纯黑。
 *
 * 落在独立 prefs 文件（与精简模式同文件 `ime_ui_settings`）——输入法与 APP 都要读：
 * IME 是独立进程组件，不能用 Activity 级内存态，必须落到 SharedPreferences。
 * ⚠ IME 与 APP 同进程（默认），MODE_PRIVATE 即可；跨进程读不到时回落到默认值。
 */
object UxSettings {

    /** 暗色模式：0=跟随系统（默认）1=始终浅色 2=始终暗色 */
    const val DARK_FOLLOW = 0
    const val DARK_OFF = 1
    const val DARK_ON = 2

    private const val PREFS = "ime_ui_settings"
    private const val KEY_TONE = "tone"
    private const val KEY_EMOJI = "emoji_enabled"
    private const val KEY_DARK = "dark_mode"
    private const val KEY_AMOLED = "amoled_black"
    // T01254 FR-10.2：主题预设 id（默认蓝 / Nord / Monokai ...），持久化即实现「重启保持」
    private const val KEY_THEME_PRESET = "theme_preset"
    // T01253 FR-10.1：Material You 动态取色开关（默认关）
    private const val KEY_DYNAMIC_COLOR = "dynamic_color"
    // T01244 FR-8.2：三维筛选（关系/场景/目的）——默认继承上次选择
    private const val KEY_RELATION = "filter_relation"
    private const val KEY_SCENARIO = "filter_scenario"
    private const val KEY_PURPOSE = "filter_purpose"
    // FR-7.5：首次引导已完成标记——默认 false，完成/跳过即置 true，重启不再弹
    private const val KEY_ONBOARDING_DONE = "onboarding_done"
    // T01258 FR-7.7：触觉反馈——总开关（默认开）+ 强度档位（0=关 1=轻 2=中）
    private const val KEY_HAPTIC_ENABLED = "haptic_enabled"
    private const val KEY_HAPTIC_LEVEL = "haptic_level"
    // T01264 FR-10.3：自定义强调色（单色 hex，如 #FF6600；空=未设置，回落主题预设）
    private const val KEY_CUSTOM_ACCENT = "custom_accent"

    // T01258 FR-7.7：触觉反馈强度档位
    const val HAPTIC_OFF = 0
    const val HAPTIC_LIGHT = 1
    const val HAPTIC_MEDIUM = 2

    // T01343：读屏范围（屏数）——默认 1 屏，可调最多 3 屏（上滑次数=屏数-1）
    const val READ_SCREENS_MIN = 1
    const val READ_SCREENS_MAX = 3
    const val READ_SCREENS_DEFAULT = 1

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** FR-7.3：语气强度 0（理性）↔ 100（活泼），默认 50。 */
    fun tone(context: Context): Int = prefs(context).getInt(KEY_TONE, TONE_DEFAULT)

    /**
     * T01348：写入语气强度。允许 [TONE_AUTO]（按关系自动档）；0-100 之外的值收敛。
     * 注意不能把非法值一律 coerce 到 [TONE_MIN]——那会把 AUTO 哨兵静默变成手动 0。
     */
    fun setTone(context: Context, tone: Int) {
        val v = if (tone <= com.emoreply.poc.core.Tone.TONE_AUTO) {
            com.emoreply.poc.core.Tone.TONE_AUTO
        } else {
            tone.coerceIn(TONE_MIN, TONE_MAX)
        }
        prefs(context).edit().putInt(KEY_TONE, v).apply()
    }

    /** T01348：当前是否处于「按关系自动」档。 */
    fun toneAuto(context: Context): Boolean =
        tone(context) <= com.emoreply.poc.core.Tone.TONE_AUTO

    /** T01348：切「按关系自动」开关——开=存 AUTO 哨兵；关=回落全局默认 50。 */
    fun setToneAuto(context: Context, on: Boolean) {
        setTone(context, if (on) com.emoreply.poc.core.Tone.TONE_AUTO else TONE_DEFAULT)
    }

    // ===== T01349：多风格并行候选（单请求多语气，默认关=旧行为便于灰度对比）=====

    private const val KEY_MULTI_TONE = "multi_tone"

    /** T01349：多风格并行开关（开=一次请求按克制/中性/活泼三档各出一条，RTT 不变）。 */
    fun multiTone(context: Context): Boolean = prefs(context).getBoolean(KEY_MULTI_TONE, false)

    fun setMultiTone(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_MULTI_TONE, enabled).apply()
    }

    // ===== T01350：采纳率闭环（低分场景自动降档，默认开=发挥闭环价值；样本<10 自动不生效）=====

    private const val KEY_AUTO_ADAPT = "auto_adapt"

    /** T01350：低分场景自动降档开关（默认开；关闭后行为与闭环上线前一致）。 */
    fun autoAdapt(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_ADAPT, true)

    fun setAutoAdapt(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_AUTO_ADAPT, enabled).apply()
    }

    /** T01159：候选是否带表情（默认开）。 */
    fun emojiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_EMOJI, true)

    fun setEmojiEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_EMOJI, enabled).apply()
    }

    /** FR-9.1：暗色模式档位（默认跟随系统）。 */
    fun darkMode(context: Context): Int = prefs(context).getInt(KEY_DARK, DARK_FOLLOW)

    fun setDarkMode(context: Context, mode: Int) {
        prefs(context).edit().putInt(KEY_DARK, mode).apply()
    }

    /** FR-9.1：AMOLED 纯黑（仅暗色生效，背景纯 #000000 不发灰）。 */
    fun amoled(context: Context): Boolean = prefs(context).getBoolean(KEY_AMOLED, false)

    fun setAmoled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_AMOLED, enabled).apply()
    }

    /** T01254 FR-10.2：主题预设 id（对应 [com.emoreply.poc.core.THEME_PRESETS] 的键），默认 default。 */
    fun themePreset(context: Context): String =
        prefs(context).getString(KEY_THEME_PRESET, "default").orEmpty()

    fun setThemePreset(context: Context, id: String) {
        prefs(context).edit().putString(KEY_THEME_PRESET, id).apply()
    }

    /** T01253 FR-10.1：Material You 动态取色开关（默认关）。 */
    fun dynamicColor(context: Context): Boolean = prefs(context).getBoolean(KEY_DYNAMIC_COLOR, false)

    fun setDynamicColor(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_DYNAMIC_COLOR, enabled).apply()
    }

    // ===== T01264 FR-10.3：自定义强调色（单色）=====

    /** 自定义强调色 hex（如 #FF6600）；空串=未设置（回落主题预设 seed）。 */
    fun customAccent(context: Context): String =
        prefs(context).getString(KEY_CUSTOM_ACCENT, "").orEmpty()

    /** 设置自定义强调色；传 null/空串=清除自定义值（跟随预设）。 */
    fun setCustomAccent(context: Context, hex: String?) {
        prefs(context).edit().putString(KEY_CUSTOM_ACCENT, hex?.trim().orEmpty()).apply()
    }

    /**
     * 当前是否应按暗色渲染。
     *
     * 跟随系统档位读 Configuration.uiMode——输入法 service 与应用同进程同配置，
     * 与 APP 侧 AppCompatDelegate 的判定口径一致（MODE_NIGHT_FOLLOW_SYSTEM）。
     */
    fun isDarkActive(context: Context): Boolean = when (darkMode(context)) {
        DARK_ON -> true
        DARK_OFF -> false
        else -> (context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES
    }

    // ===== T01244 FR-8.2：三维筛选（关系/场景/目的）默认继承上次选择 =====

    val RELATIONS = listOf("", "领导", "客户", "恋人", "朋友")
    val SCENARIOS = listOf("", "闲聊", "工作")
    val PURPOSES = listOf("", "安抚", "拒绝", "推进", "闲聊")

    fun relation(context: Context): String = prefs(context).getString(KEY_RELATION, "").orEmpty()

    fun scenario(context: Context): String = prefs(context).getString(KEY_SCENARIO, "").orEmpty()

    fun purpose(context: Context): String = prefs(context).getString(KEY_PURPOSE, "").orEmpty()

    fun setFilter(context: Context, relation: String, scenario: String, purpose: String) {
        prefs(context).edit()
            .putString(KEY_RELATION, relation)
            .putString(KEY_SCENARIO, scenario)
            .putString(KEY_PURPOSE, purpose)
            .apply()
    }

    // ===== FR-7.5：首次引导向导完成标记 =====

    /** 是否已完成/跳过首次引导（全新安装默认 false → 启动弹出三步向导）。 */
    fun onboardingDone(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ONBOARDING_DONE, false)

    /** 完成或跳过引导即落盘，重启不再弹。 */
    fun setOnboardingDone(context: Context, done: Boolean) {
        prefs(context).edit().putBoolean(KEY_ONBOARDING_DONE, done).apply()
    }

    // ===== T01258 FR-7.7：触觉反馈（总开关 + 强度档位）=====

    /** 触觉反馈总开关（默认开）；关闭后 IME 三处反馈点整体跳过。 */
    fun hapticEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_HAPTIC_ENABLED, true)

    fun setHapticEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_HAPTIC_ENABLED, enabled).apply()
    }

    /** 触觉强度档位（0=关 1=轻 2=中），默认轻。 */
    fun hapticLevel(context: Context): Int =
        prefs(context).getInt(KEY_HAPTIC_LEVEL, HAPTIC_LIGHT).coerceIn(HAPTIC_OFF, HAPTIC_MEDIUM)

    fun setHapticLevel(context: Context, level: Int) {
        prefs(context).edit().putInt(KEY_HAPTIC_LEVEL, level.coerceIn(HAPTIC_OFF, HAPTIC_MEDIUM)).apply()
    }

    // ===== T01343：读屏范围（屏数）=====

    private const val KEY_READ_SCREENS = "read_screens"

    /** 读屏屏数（1-3，默认 1）：点 👁 读取当前屏 + 上滑 N-1 屏。 */
    fun readScreens(context: Context): Int =
        prefs(context).getInt(KEY_READ_SCREENS, READ_SCREENS_DEFAULT)
            .coerceIn(READ_SCREENS_MIN, READ_SCREENS_MAX)

    fun setReadScreens(context: Context, screens: Int) {
        prefs(context).edit()
            .putInt(KEY_READ_SCREENS, screens.coerceIn(READ_SCREENS_MIN, READ_SCREENS_MAX))
            .apply()
    }
}
