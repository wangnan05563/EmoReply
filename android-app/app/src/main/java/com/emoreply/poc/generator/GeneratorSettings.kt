package com.emoreply.poc.generator

import android.content.Context
import android.content.SharedPreferences
import java.io.File

/**
 * 云端生成配置：密钥管理方案采用「用户运行时自填 + 本机 SharedPreferences 私有存储」。
 *
 * 决策依据：SRS 评审 F11 指出「密钥不出设备」存在歧义；在随包内置（违背密钥不入仓库约束）
 * 与网关代理（需后端，超出 M1 范围）均不可行时，自填是唯一满足「密钥不硬编码、不出设备」
 * 的方案。M2 若引入自有网关再演进。
 */
object GeneratorSettings {

    data class LlmConfig(
        val enabled: Boolean = false,
        val baseUrl: String = "",
        val apiKey: String = "",
        val model: String = "",
        val style: String = DEFAULT_STYLE,
        // T01243 FR-8.1：端侧离线后端开关与模型来源（.task 路径 + 首下 URL）
        val onDevice: Boolean = false,
        val modelPath: String = "",
        val modelUrl: String = "",
    )

    /** FR-3.2（REQ-009）：四种语气风格，可切换且互异。 */
    val STYLES = listOf("正式", "亲切", "简短", "幽默")
    const val DEFAULT_STYLE = "亲切"

    private const val PREFS = "generator_settings"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_API_KEY = "api_key"
    private const val KEY_MODEL = "model"
    private const val KEY_STYLE = "style"
    private const val KEY_ON_DEVICE = "on_device"
    private const val KEY_MODEL_PATH = "model_path"
    private const val KEY_MODEL_URL = "model_url"

    private const val DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    private const val DEFAULT_MODEL = "deepseek-chat"

    fun load(context: Context): LlmConfig {
        val p: SharedPreferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return LlmConfig(
            enabled = p.getBoolean(KEY_ENABLED, false),
            baseUrl = p.getString(KEY_BASE_URL, DEFAULT_BASE_URL).orEmpty(),
            apiKey = p.getString(KEY_API_KEY, "").orEmpty(),
            model = p.getString(KEY_MODEL, DEFAULT_MODEL).orEmpty(),
            style = p.getString(KEY_STYLE, DEFAULT_STYLE).orEmpty().ifBlank { DEFAULT_STYLE },
            onDevice = p.getBoolean(KEY_ON_DEVICE, false),
            modelPath = p.getString(KEY_MODEL_PATH, "").orEmpty(),
            modelUrl = p.getString(KEY_MODEL_URL, "").orEmpty(),
        )
    }

    fun save(context: Context, config: LlmConfig) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putBoolean(KEY_ENABLED, config.enabled)
            .putString(KEY_BASE_URL, config.baseUrl.trim())
            .putString(KEY_API_KEY, config.apiKey.trim())
            .putString(KEY_MODEL, config.model.trim())
            .putString(KEY_STYLE, config.style.trim())
            .putBoolean(KEY_ON_DEVICE, config.onDevice)
            .putString(KEY_MODEL_PATH, config.modelPath.trim())
            .putString(KEY_MODEL_URL, config.modelUrl.trim())
            .apply()
    }

    fun defaultBaseUrl(): String = DEFAULT_BASE_URL

    fun defaultModel(): String = DEFAULT_MODEL

    /** FR-6.3（REQ-017）：一键清除——恢复初始态（云端设置与风格全部重置）。 */
    fun clearAll(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    /** 配置完整（启用且 baseUrl / apiKey / model 均非空）才走云端，否则一律回退桩。 */
    fun isCloudReady(config: LlmConfig): Boolean =
        config.enabled && config.baseUrl.isNotBlank() && config.apiKey.isNotBlank() && config.model.isNotBlank()

    /** T01243 FR-8.1：端侧后端就绪 = 开关打开且 .task 模型文件真实存在。 */
    fun isOnDeviceReady(config: LlmConfig): Boolean =
        config.onDevice && config.modelPath.isNotBlank() && File(config.modelPath).isFile

    // ===== T01147：精简模式（隐藏 IME 的消息内容框与汇总提示框）=====

    private const val PREFS_IME_UI = "ime_ui_settings"
    private const val KEY_COMPACT = "compact_mode"

    /** 精简模式：键盘只留图标行+候选区，输入框/汇总格隐藏（读屏与 ✨ 生成不受影响）。 */
    fun isCompactMode(context: Context): Boolean =
        context.getSharedPreferences(PREFS_IME_UI, Context.MODE_PRIVATE)
            .getBoolean(KEY_COMPACT, false)

    fun setCompactMode(context: Context, compact: Boolean) {
        context.getSharedPreferences(PREFS_IME_UI, Context.MODE_PRIVATE).edit()
            .putBoolean(KEY_COMPACT, compact)
            .apply()
    }
}
