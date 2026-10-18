package com.emoreply.poc.core

/**
 * T01348：关系感知 → 语气默认档位（与 m1-poc `emoreply/tone.py` 同构镜像）。
 *
 * 来源（T01345 竞品分析）：钉钉的高情商感知来自「先识别双方关系，再决定语气分布」。
 * 本项目 PersonaStore.relation 与 UxSettings.relation（三维筛选）的数据一直在库里，
 * 但 tone 是全局单值滑杆，不随对端关系变化——本文件把「关系 → 默认语气」补上。
 */
object Tone {

    /** AUTO 哨兵：任何 <= TONE_AUTO 的 tone 值视为「自动」（0 是合法手动值，不能复用）。 */
    const val TONE_AUTO = -1

    /** 归一化关系 → 默认语气档位（数值对齐任务单建议区间：上级/客户 30-40，同事 55-60，好友/家人 70-80）。 */
    val RELATION_TONE: Map<String, Int> = mapOf(
        "领导" to 35,   // 上级/客户/外部：克制、精简、避免感叹与过度承诺
        "客户" to 35,
        "同事" to 58,   // 平级：中性自然
        "朋友" to 75,   // 好友/家人/恋人：偏活泼
        "家人" to 75,
        "恋人" to 75,
        "陌生" to TONE_DEFAULT,  // 未标注 → 与全局默认一致（不因 AUTO 产生行为漂移）
    )

    /** 关系别名归一化（PersonaStore.RELATIONS 与 UxSettings.RELATIONS 两套口径 + 常见同义词）。 */
    private val RELATION_ALIASES: Map<String, String> = mapOf(
        "上级" to "领导",
        "老板" to "领导",
        "外部" to "客户",
        "平级" to "同事",
        "好友" to "朋友",
    )

    fun clampTone(value: Int): Int = TONE_MIN.coerceAtLeast(TONE_MAX.coerceAtMost(value))

    /** 关系别名归一化；空/null → 「陌生」。 */
    fun normalizeRelation(relation: String?): String {
        val r = relation?.trim().orEmpty()
        if (r.isEmpty()) return "陌生"
        return RELATION_ALIASES[r] ?: r
    }

    /** 归一化关系 → 默认语气档位；未收录关系按「陌生」处理。 */
    fun toneForRelation(relation: String?): Int =
        RELATION_TONE[normalizeRelation(relation)] ?: TONE_DEFAULT

    /**
     * tone 解析（T01537 优先级）：联系人级 > 全局 > 关系推档；null 旧行为不变。
     *
     * - tone == null → null（调用方不注入语气强度行，FR-7.3 旧行为）；
     * - [override] 非空 → 联系人级手动覆盖，优先于全局（无论 AUTO 还是手动值）；
     * - tone <= [TONE_AUTO] → 关系推档；
     * - 其余（0-100 或越界手动值）→ clamp 后返回。
     *
     * T01348 旧语义「override 仅在 AUTO 时生效」已由 T01537 升级为
     * 「联系人级覆盖一切全局值」（与 m1-poc `tone.py` resolve_tone 同构）。
     */
    fun resolveTone(tone: Int?, relation: String? = null, override: Int? = null): Int? {
        if (tone == null) return null
        if (override != null) return clampTone(override)
        if (tone <= TONE_AUTO) return toneForRelation(relation)
        return clampTone(tone)
    }
}
