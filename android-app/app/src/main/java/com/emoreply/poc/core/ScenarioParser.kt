package com.emoreply.poc.core

/**
 * T01538：通知载荷 → 场景/目的维度解析（与 m1-poc `emoreply/scenario_parser.py` 同构镜像）。
 *
 * 背景：T01350 采纳率闭环只在手动生成链路生效，通知链路因缺维度上下文而空转。
 * 本模块从通知载荷（平台、会话名、消息文本）自动推断 scenario/purpose，复用
 * T01244 的枚举口径（SCENARIOS/PURPOSES），使两条链路共用同一套降档判定。
 */
object ScenarioParser {

    // ===== 关键词映射表（小写匹配，覆盖常见表达）=====

    private val SCENARIO_WORK_KEYWORDS = listOf(
        "会议", "汇报", "审批", "流程", "项目", "需求", "上线", "排期", "deadline",
        "ddl", "kpi", "okr", "周报", "日报", "考勤", "加班", "出差", "报销",
        "合同", "报价", "预算", "客户", "甲方", "乙方", "交付", "验收",
    )

    private val SCENARIO_CASUAL_KEYWORDS = listOf(
        "吃饭", "周末", "爬山", "电影", "游戏", "旅游", "聚会", "生日", "结婚",
        "哈哈", "嘿嘿", "～", "早安", "晚安", "吃了吗", "在吗",
    )

    private val PURPOSE_APPEASE = listOf("别急", "没事", "放心", "理解", "抱歉", "不好意思", "辛苦", "谢谢")
    private val PURPOSE_REJECT = listOf("不行", "不能", "不了", "没空", "不方便", "下次", "改天", "算了")
    private val PURPOSE_ADVANCE = listOf("什么时候", "何时", "几点", "明天", "下周", "确认", "定一下", "安排", "跟进")
    private val PURPOSE_CASUAL = listOf("在吗", "吃了吗", "最近", "怎么样", "如何", "好吗", "吧", "呀", "哈")

    private val DINGTALK_HINTS = listOf("钉邮", "日程", "待办", "审批流", "考勤打卡", "日志")

    /**
     * 从消息正文推断场景（闲聊/工作），未知返回空串。
     *
     * 策略：
     * 1. 钉钉特有词 → 工作；
     * 2. 关键词计数：工作词 > 闲聊词 → 工作，反之闲聊；
     * 3. 平局或无命中 → 空串（宁缺勿错）。
     */
    fun parseScenario(body: String, source: String = ""): String {
        if (body.isBlank()) return ""

        // 钉钉特有词直接判工作
        if (source == "dingtalk" && DINGTALK_HINTS.any { it in body }) {
            return "工作"
        }

        val workCount = SCENARIO_WORK_KEYWORDS.count { it in body }
        val casualCount = SCENARIO_CASUAL_KEYWORDS.count { it in body }

        return when {
            workCount > casualCount && workCount > 0 -> "工作"
            casualCount > workCount && casualCount > 0 -> "闲聊"
            else -> ""
        }
    }

    /**
     * 从消息正文推断目的（安抚/拒绝/推进/闲聊），未知返回空串。
     *
     * 策略：最高频关键词对应的目的；平局或无命中 → 空串。
     */
    fun parsePurpose(body: String): String {
        if (body.isBlank()) return ""

        val counts = mapOf(
            "安抚" to PURPOSE_APPEASE.count { it in body },
            "拒绝" to PURPOSE_REJECT.count { it in body },
            "推进" to PURPOSE_ADVANCE.count { it in body },
            "闲聊" to PURPOSE_CASUAL.count { it in body },
        ).filterValues { it > 0 }

        if (counts.isEmpty()) return ""

        val maxCount = counts.values.maxOrNull() ?: 0
        val topPurposes = counts.filter { it.value == maxCount }

        // 平局检测：多个目的频次相同 → 返回空串
        return if (topPurposes.size == 1) topPurposes.keys.first() else ""
    }

    /**
     * 一次性解析场景+目的，返回 Pair(scenario, purpose)。
     *
     * 调用方（NotifyCaptureService / pipeline）拿到后可直接传给 ReplyPipeline.handle()。
     * 两者皆空时调用方应跳过闭环统计（宁缺勿错）。
     */
    fun parseDimensions(body: String, source: String = "", chat: String = ""): Pair<String, String> {
        val scenario = parseScenario(body, source)
        val purpose = parsePurpose(body)
        return Pair(scenario, purpose)
    }
}
