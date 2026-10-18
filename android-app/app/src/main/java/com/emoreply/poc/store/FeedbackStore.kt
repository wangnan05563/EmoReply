package com.emoreply.poc.store

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject
import java.time.LocalDate

/**
 * 用户反馈记录（FR-4.3 / REQ-014）。
 *
 * 记录维度：采纳（点选候选注入）/ 忽略（候选被新消息替换未使用）；
 * 脱敏约束：**只记计数与元信息（时间戳 / 风格 / 是否提示类），绝不记录消息正文与候选内容**。
 * 默认仅本地存储，不上云（FR-4.3：反馈上云须授权且脱敏——M2 未接云端，天然满足）。
 * 采纳率作为「高情商」质量代理指标（目标 ≥40%）。
 *
 * T01257 FR-7.6 使用统计：在既有「采纳/忽略」之上叠加今日采纳（按日累计）、
 * 平均生成耗时（累计毫秒和/采样次数）与分应用分布（by_source）——依旧零留存：
 * 只存计数与毫秒，绝不存正文。
 */
object FeedbackStore {

    private const val PREFS = "feedback_stats"
    private const val KEY_ADOPTED = "adopted"
    private const val KEY_IGNORED = "ignored"
    private const val KEY_BY_STYLE = "by_style" // JSON: {"亲切": n, ...}
    private const val KEY_LAST_TS = "last_ts"
    // T01257 FR-7.6：今日采纳按日累计——存「日期 + 当日计数」，跨日自动归零重计
    private const val KEY_TODAY_DATE = "today_date" // "yyyy-MM-dd"
    private const val KEY_TODAY_ADOPTED = "today_adopted"
    // T01257 FR-7.6：平均生成耗时（累计毫秒和 + 采样次数），零留存
    private const val KEY_GEN_TOTAL_MS = "gen_total_ms"
    private const val KEY_GEN_SAMPLES = "gen_samples"
    // T01257 FR-7.6：分应用分布 JSON: {"微信": n, "手动生成": n, ...}
    private const val KEY_BY_SOURCE = "by_source"
    // T01350：三维采纳统计 JSON {"领导|2|亲切": {"a": n, "i": n}, ...}
    // （关系 × tone 桶 × 风格；零留存：只有维度键与计数，不存正文）
    private const val KEY_DIM_STATS = "dim_stats"

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * 记录一次反馈。type ∈ {采纳, 忽略}。
     * T01257 FR-7.6 扩展参数：source=候选来源（聊天名，分应用分布用）；
     * genMs=「生成→采纳」耗时毫秒（仅采纳时累计平均）。
     * T01350 扩展参数：relation/tone=生成时场景维度（供三维采纳统计；缺省只记全局）。
     */
    fun record(context: Context, type: String, style: String?, source: String? = null, genMs: Long? = null,
               relation: String? = null, tone: Int? = null) {
        val p = prefs(context)
        val adopted = type == "采纳"
        val byStyle = JSONObject(p.getString(KEY_BY_STYLE, "{}") ?: "{}")
        if (!style.isNullOrBlank()) {
            byStyle.put(style, byStyle.optInt(style, 0) + if (adopted) 1 else 0)
        }
        val editor = p.edit()
            .putInt(KEY_ADOPTED, p.getInt(KEY_ADOPTED, 0) + if (adopted) 1 else 0)
            .putInt(KEY_IGNORED, p.getInt(KEY_IGNORED, 0) + if (type == "忽略") 1 else 0)
            .putString(KEY_BY_STYLE, byStyle.toString())
            .putLong(KEY_LAST_TS, System.currentTimeMillis())
        if (adopted) recordAdoptedExtras(p, editor, source, genMs)
        recordDimStats(p, editor, adopted, relation, tone, style)
        editor.apply()
    }

    /** 采纳时的附加统计：今日计数（日期变更归零重计）+ 分应用分布 + 平均生成耗时。 */
    private fun recordAdoptedExtras(p: android.content.SharedPreferences,
                                    editor: android.content.SharedPreferences.Editor,
                                    source: String?, genMs: Long?) {
        // 今日采纳：日期变了当日计数归零重计（只存日期与计数，不存正文）
        val today = LocalDate.now().toString()
        val savedDate = p.getString(KEY_TODAY_DATE, null)
        val todayAdopted = if (savedDate == today) p.getInt(KEY_TODAY_ADOPTED, 0) else 0
        editor.putInt(KEY_TODAY_ADOPTED, todayAdopted + 1).putString(KEY_TODAY_DATE, today)
        // 分应用分布
        if (!source.isNullOrBlank()) {
            val bySource = JSONObject(p.getString(KEY_BY_SOURCE, "{}") ?: "{}")
            bySource.put(source, bySource.optInt(source, 0) + 1)
            editor.putString(KEY_BY_SOURCE, bySource.toString())
        }
        // 平均生成耗时：只累计毫秒与次数
        if (genMs != null && genMs > 0) {
            editor.putLong(KEY_GEN_TOTAL_MS, p.getLong(KEY_GEN_TOTAL_MS, 0) + genMs)
                .putLong(KEY_GEN_SAMPLES, p.getLong(KEY_GEN_SAMPLES, 0) + 1)
        }
    }

    /** T01350：三维采纳统计——relation/tone/style 三者齐备才入维度（零留存）。 */
    private fun recordDimStats(p: android.content.SharedPreferences,
                               editor: android.content.SharedPreferences.Editor,
                               adopted: Boolean, relation: String?, tone: Int?, style: String?) {
        if (relation == null || tone == null || style.isNullOrBlank()) return
        val dims = JSONObject(p.getString(KEY_DIM_STATS, "{}") ?: "{}")
        val key = com.emoreply.poc.core.Adaptive.dimKey(relation, tone, style)
        val entry = dims.optJSONObject(key) ?: JSONObject().put("a", 0).put("i", 0)
        entry.put(if (adopted) "a" else "i", entry.optInt(if (adopted) "a" else "i", 0) + 1)
        dims.put(key, entry)
        editor.putString(KEY_DIM_STATS, dims.toString())
    }

    /** T01350：读三维统计（key → (采纳, 忽略)），供 Adaptive.adaptTone 路由。 */
    fun dimStats(context: Context): Map<String, Pair<Int, Int>> {
        val o = runCatching {
            JSONObject(prefs(context).getString(KEY_DIM_STATS, "{}") ?: "{}")
        }.getOrElse { return emptyMap() }
        val out = mutableMapOf<String, Pair<Int, Int>>()
        val it = o.keys()
        while (it.hasNext()) {
            val k = it.next()
            val e = o.optJSONObject(k) ?: continue
            out[k] = e.optInt("a", 0) to e.optInt("i", 0)
        }
        return out
    }

    data class Stats(
        val adopted: Int,
        val ignored: Int,
        // T01257 FR-7.6：今日采纳数 / 平均生成耗时（null=尚无样本）/ 样本数 / 分应用分布
        val todayAdopted: Int = 0,
        val avgGenMs: Long? = null,
        val genSamples: Long = 0,
        val bySource: Map<String, Int> = emptyMap(),
    ) {
        /** 采纳率 = 采纳 / (采纳 + 忽略)；无反馈时返回 null。 */
        fun adoptionRate(): Double? {
            val total = adopted + ignored
            return if (total == 0) null else adopted.toDouble() / total
        }
    }

    fun stats(context: Context): Stats {
        val p = prefs(context)
        val samples = p.getLong(KEY_GEN_SAMPLES, 0)
        val totalMs = p.getLong(KEY_GEN_TOTAL_MS, 0)
        // 跨日读统计时今日计数归零（与记录侧同口径）
        val today = if (p.getString(KEY_TODAY_DATE, null) == LocalDate.now().toString())
            p.getInt(KEY_TODAY_ADOPTED, 0) else 0
        val bySource = runCatching {
            val o = JSONObject(p.getString(KEY_BY_SOURCE, "{}") ?: "{}")
            val m = mutableMapOf<String, Int>()
            val it = o.keys()
            while (it.hasNext()) {
                val k = it.next()
                m[k] = o.optInt(k, 0)
            }
            m
        }.getOrElse { emptyMap() }
        return Stats(
            adopted = p.getInt(KEY_ADOPTED, 0),
            ignored = p.getInt(KEY_IGNORED, 0),
            todayAdopted = today,
            avgGenMs = if (samples > 0) totalMs / samples else null,
            genSamples = samples,
            bySource = bySource,
        )
    }

    /** FR-6.3：一键清除，恢复初始态。 */
    fun clearAll(context: Context) {
        prefs(context).edit().clear().apply()
    }
}
