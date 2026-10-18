package com.emoreply.poc.core

/**
 * 意图与情感理解（REQ-006 / FR-2.1 / FR-2.2 / FR-2.4）。
 *
 * 与 m1-poc `emoreply/classify.py` 同构的规则版基线：只读通知场景下先用
 * 可解释硬规则压「通知类不回复」误判（FR-2.4），意图/情绪供提示词增强。
 */
object Classify {

    const val INTENT_INQUIRY = "问询"
    const val INTENT_REQUEST = "请求"
    const val INTENT_CHITCHAT = "闲聊"
    const val INTENT_VENT = "情绪倾诉"
    const val INTENT_NOTICE = "通知"
    const val INTENT_CONFLICT = "冲突"
    const val INTENT_INVITE = "邀约"

    // 顺序即优先级：强信号在前，通知类最靠前（FR-2.4 压误判的关键）
    private val INTENT_RULES: List<Pair<String, Regex>> = listOf(
        INTENT_NOTICE to Regex(
            "验证码|校验码|取件码|快递|包裹|已签收|已发货|物流|账单|扣款|还款日|" +
                "会议提醒|日程|签到|打卡|系统通知|广告|优惠|促销|限时|余额不足|工单|" +
                "\\[(图片|文件|链接|名片)\\]"
        ),
        INTENT_CONFLICT to Regex("你总是|你从来|不想再|分手|绝交|投诉|骗子|欺骗|敷衍|忍无可忍|太过分"),
        INTENT_VENT to Regex("好累|累死|好难过|伤心|想哭|崩溃|烦死|压力大|委屈|焦虑|失眠|emo"),
        INTENT_INVITE to Regex("一起|约[个饭]|聚餐|吃饭|喝咖啡|喝奶茶|看电影|有空吗|出来玩|聚一聚"),
        INTENT_REQUEST to Regex("帮我|帮忙|麻烦你|拜托|能不能|可不可以|麻烦帮|劳驾|借[我个]"),
        INTENT_INQUIRY to Regex(
            "[??？]$|[??？]\\s*$|吗[??？]?$|呢[？?]?$|什么时候|几点|多少钱|多少个|" +
                "怎么|为什么|为啥|是不是|有没有|哪个|谁知道"
        ),
    )

    private val EMOTION_RULES: List<Pair<String, Regex>> = listOf(
        "愤怒" to Regex("气死|生气|无语|过分|差劲|忍无可忍|骗子|投诉|恶心|服了"),
        "焦虑" to Regex("着急|急死|担心|焦虑|来不及|怎么办|截止|延误|出事|糟了"),
        "开心" to Regex("太好了|哈哈|开心|棒|厉害|谢谢|感谢|喜欢|赞|顺利"),
    )

    private val URGENT = Regex("现在|马上|立刻|尽快|紧急|今天[之内]?必须|今晚|下班前|越快越好")

    // 强「无需回复」信号：即使意图规则漏判也拦下（压 FR-2.4 误判率）
    private val NO_REPLY = Regex(
        "验证码|校验码|取件码|已签收|已发货|扣款成功|支付成功|到账|余额不足|" +
            "会议[已将]开始|日程提醒|系统自动|请勿回复|本条为系统消息|广告"
    )

    /** FR-2.1：规则版消息分类。空文本归「通知」。 */
    fun classifyIntent(text: String?): String {
        if (text.isNullOrBlank()) return INTENT_NOTICE
        for ((intent, pattern) in INTENT_RULES) {
            if (pattern.containsMatchIn(text)) return intent
        }
        return INTENT_CHITCHAT
    }

    /** FR-2.2：返回 (情绪, 紧急度)，紧急度 ∈ {高, 普通}。 */
    fun classifyEmotion(text: String?): Pair<String, String> {
        val emotion = EMOTION_RULES.firstOrNull { it.second.containsMatchIn(text ?: "") }?.first ?: "中性"
        val urgency = if (text != null && URGENT.containsMatchIn(text)) "高" else "普通"
        return emotion to urgency
    }

    /** FR-2.4：判定是否需要回复。纯通知类 → false。 */
    fun needsReply(text: String?, intent: String? = null): Boolean {
        if (intent == INTENT_NOTICE) return false
        if (text != null && NO_REPLY.containsMatchIn(text)) return false
        return true
    }
}
