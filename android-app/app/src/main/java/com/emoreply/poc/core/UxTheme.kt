package com.emoreply.poc.core

import android.content.Context
import android.graphics.Color
import com.emoreply.poc.store.UxSettings

/**
 * T01254 FR-10.2：主题预设（强调色 seed 表）。
 *
 * 每个预设 = id → (显示名, 强调色 seed)。在 light()/dark(amoled) 的基础 token 之上，
 * 仅覆盖 accent/iconActive 两个强调色 token（两者语义等价：主强调色 = 选中态图标色）。
 * 默认蓝与内置 accent（#2563EB）一致，未选其它预设时视觉零变化。
 */
val THEME_PRESETS: Map<String, Pair<String, Int>> = linkedMapOf(
    "default" to ("默认蓝" to Color.parseColor(DEFAULT_ACCENT_HEX)),
    "brand" to ("品牌青绿" to Color.parseColor("#14B8A6")),
    "nord" to ("Nord" to Color.parseColor("#88C0D0")),
    "monokai" to ("Monokai" to Color.parseColor("#AE81FF")),
    "rose" to ("玫红" to Color.parseColor("#F43F5E")),
    "warmOrange" to ("暖橙" to Color.parseColor("#F97316")),
    "purple" to ("紫" to Color.parseColor("#8B5CF6")),
)

/** 内置默认强调色（T01122 既有蓝；主题预设 default 与回落路径共用）。 */
const val DEFAULT_ACCENT_HEX = "#2563EB"

/**
 * T01158 FR-9.1：键盘面板配色（亮/暗两套，AMOLED 叠加纯黑）。
 *
 * IME 的界面在代码里构建（无 layout xml），无法靠 `values-night` 自动切换——
 * 因此把色值集中到一处，由 [uxTheme] 依据设置返回，视图构建与重绘都取这里的值。
 */
data class UxTheme(
    val bg: Int,
    val cardBg: Int,
    val textPrimary: Int,
    val textSecondary: Int,
    val accent: Int,
    val iconNormal: Int,
    val iconActive: Int,
    val chipBg: Int,
    val chipFg: Int,
) {
    companion object {
        /** 亮色主文本/图标色（T01122/T01128 既有配色，light() 内多处共用）。 */
        private val LIGHT_TEXT = Color.parseColor("#374151")

        /** 内置默认强调色（T01122 既有蓝；THEME_PRESETS default 与回落路径共用）。 */
        private val DEFAULT_ACCENT = Color.parseColor(DEFAULT_ACCENT_HEX)

        /** 亮色：沿用 T01122/T01128 既有配色（#F4F6FA 底 / 白卡片 / 蓝强调）。 */
        fun light() = UxTheme(
            bg = Color.parseColor("#F4F6FA"),
            cardBg = Color.WHITE,
            textPrimary = LIGHT_TEXT,
            textSecondary = Color.parseColor("#6B7280"),
            accent = DEFAULT_ACCENT,
            iconNormal = LIGHT_TEXT,
            iconActive = DEFAULT_ACCENT,
            chipBg = Color.parseColor("#E5E7EB"),
            chipFg = LIGHT_TEXT,
        )

        /** 暗色：底不发灰；AMOLED 档位下背景与卡片取纯黑/近黑。 */
        fun dark(amoled: Boolean) = UxTheme(
            bg = Color.parseColor(if (amoled) "#000000" else "#101418"),
            cardBg = Color.parseColor(if (amoled) "#0B0E12" else "#1C2128"),
            textPrimary = Color.parseColor("#E5E7EB"),
            textSecondary = Color.parseColor("#9CA3AF"),
            accent = Color.parseColor("#7DA2FF"),
            iconNormal = Color.parseColor("#9CA3AF"),
            iconActive = Color.parseColor("#7DA2FF"),
            chipBg = Color.parseColor(if (amoled) "#111827" else "#1F2937"),
            chipFg = Color.parseColor("#D1D5DB"),
        )
    }
}

/**
 * T01253 FR-10.1：Material You 动态取色——从壁纸提取强调色 seed。
 *
 * 仅 Android 12（API 31）及以上支持 [android.app.WallpaperManager.getWallpaperColors]；
 * API<31 或开关关闭、或取色异常时一律返回 null。⚠ Android 12/13 真机取色未实测——
 * 本机无 API 31 设备，此处只能保证编译通过 + 逻辑回落正确。
 */
fun dynamicAccent(context: Context): Int? {
    if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.S) return null
    if (!UxSettings.dynamicColor(context)) return null
    return runCatching {
        android.app.WallpaperManager.getInstance(context)
            .getWallpaperColors(android.app.WallpaperManager.FLAG_SYSTEM)
            ?.primaryColor?.toArgb()
    }.getOrNull()
}

/**
 * 按当前设置取配色（暗色 + AMOLED + 主题预设 + 动态取色 + 自定义强调色）。
 *
 * 优先级：动态取色取到壁纸 seed → 用自定义强调色（单色，亮/暗共用）→ 用选中预设的
 * 强调色 seed → 默认蓝。无论哪条路径，都只在 light()/dark(amoled) 基础上覆盖
 * accent/iconActive 两个 token，其余 token（bg/cardBg/text/iconNormal/chip）保持
 * FR-9.1 原有逻辑不变。候选选中态、图标激活态、理由标签、模板条全部取 ux.accent/
 * ux.iconActive，故自定义色一处生效、三处联动（FR-10.3 验收口径）。
 */
/** 强调色 seed 解析：动态取色 > 自定义色 > 选中预设 > 默认蓝（四级回落，均可能为空）。 */
private fun accentSeed(context: Context, custom: Int?): Int {
    val presetSeed = THEME_PRESETS[UxSettings.themePreset(context)]?.second
    return dynamicAccent(context)
        ?: custom
        ?: presetSeed
        ?: Color.parseColor(DEFAULT_ACCENT_HEX)
}

fun uxTheme(context: Context): UxTheme {
    val base = if (UxSettings.isDarkActive(context)) UxTheme.dark(UxSettings.amoled(context)) else UxTheme.light()
    // T01264 FR-10.3：自定义强调色（非法 hex 解析失败回落下一级）
    val custom = UxSettings.customAccent(context)
        .takeIf { it.isNotBlank() }
        ?.let { runCatching { Color.parseColor(it) }.getOrNull() }
    val seed = accentSeed(context, custom)
    return base.copy(accent = seed, iconActive = seed)
}

/**
 * T01250 FR-9.3：候选理由 → 4 类分类标签（候选卡片右侧常驻展示）。
 *
 * 关键词命中即归类；命中顺序即优先级（越靠前越早返回）。无命中回落到「情感共鸣」兜底，
 * 保证每条候选恰好有一个标签——理由文本本身仍完整保留在小字区，标签只做快速语义分类。
 */
fun tagRationale(rationale: String): String {
    val tag =
        when {
            rationale.contains("共情") || rationale.contains("安慰") ||
                rationale.contains("情绪") || rationale.contains("感受") ||
                rationale.contains("体谅") || rationale.contains("倾听") ||
                rationale.contains("理解") -> "情感共鸣"
            rationale.contains("建议") || rationale.contains("可以") ||
                rationale.contains("下一步") || rationale.contains("试着") ||
                rationale.contains("添加") || rationale.contains("记得") -> "行动建议"
            rationale.contains("边界") || rationale.contains("拒绝") ||
                rationale.contains("不") || rationale.contains("保持") ||
                rationale.contains("分寸") || rationale.contains("婉拒") -> "边界保持"
            rationale.contains("幽默") || rationale.contains("轻松") ||
                rationale.contains("调侃") || rationale.contains("化解") ||
                rationale.contains("玩笑") -> "幽默化解"
            else -> "情感共鸣"
        }
    return tag
}
