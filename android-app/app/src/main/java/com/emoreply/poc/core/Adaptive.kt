package com.emoreply.poc.core

/**
 * T01350：采纳率闭环接回生成——推理侧路由（与 m1-poc `emoreply/feedback.py` 同构镜像）。
 *
 * 三方 LLM 无训练闭环下的等价替代（不改模型，改输入）：按「关系 × tone 桶 × 风格」
 * 维度统计采纳率（FeedbackStore），某维度样本充足且采纳率低于阈值 → 该场景本次
 * 生成自动下调语气一档——低分场景不去赌模型当前语气。
 *
 * TemplateStore 盘点结论（T01345 依赖确认）：模板是用户主动保存的裸文本，无场景/
 * 关系标签、无质量元数据、新装 0 条——直接当低分场景兜底候选会「降级反而更差」，
 * 故本版路由仅做降档；模板参考注入走既有 T01262 favorites 机制。
 */
object Adaptive {

    /** 维度样本量下限：不足不生效（防少量样本误判，验收边界）。 */
    const val MIN_SAMPLES = 10

    /** 采纳率低于该值视为「低分场景」。 */
    const val LOW_RATE_THRESHOLD = 0.30

    /** 降档步长（滑杆 0-100，一档≈两个 5 步进）。 */
    const val TONE_STEP_DOWN = 10

    /** tone 连续值 → 5 桶（0-20/21-40/.../81-100），避免连续值做 key 碎片化。 */
    const val TONE_BUCKET_SIZE = 20

    fun toneBucket(tone: Int): Int = (tone / TONE_BUCKET_SIZE).coerceIn(0, 4)

    /** 统计维度键：关系|tone桶|风格（relation 先经 Tone 归一化，口径对齐 T01348）。 */
    fun dimKey(relation: String?, tone: Int, style: String?): String =
        "${Tone.normalizeRelation(relation)}|${toneBucket(tone)}|${style.orEmpty()}"

    /**
     * 低分场景自动降档。返回 (生效 tone, 是否降档)。
     *
     * - enabled=false / tone=null / 样本 < [MIN_SAMPLES] → 原样返回；
     * - 采纳率 < [LOW_RATE_THRESHOLD] → max(0, tone - [TONE_STEP_DOWN])。
     */
    fun adaptTone(
        relation: String?,
        tone: Int?,
        style: String?,
        stats: Map<String, Pair<Int, Int>>, // key -> (adopted, ignored)
        enabled: Boolean = true,
    ): Pair<Int?, Boolean> {
        if (!enabled || tone == null) return tone to false
        val (adopted, ignored) = stats[dimKey(relation, tone, style)] ?: return tone to false
        val total = adopted + ignored
        if (total < MIN_SAMPLES) return tone to false
        if (adopted.toDouble() / total >= LOW_RATE_THRESHOLD) return tone to false
        return (tone - TONE_STEP_DOWN).coerceAtLeast(0) to true
    }
}
