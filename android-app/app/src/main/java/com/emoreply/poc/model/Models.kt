package com.emoreply.poc.model

/**
 * 一次系统通知（捕获层输出，只读，不触碰聊天数据库）。
 * 字段对齐 SRS FR-1.2，与 m1-poc/emoreply/models.py 保持同构。
 */
data class Notification(
    val source: String,
    val timestamp: Long,
    val body: String? = null,
    val sender: String? = null,
    val chat: String? = null,
    val isGroup: Boolean = false,
    val mentionsMe: Boolean = false,
    val quotesMe: Boolean = false,
    val fromSelf: Boolean = false,
    val isSystem: Boolean = false,
    val title: String? = null,
)

enum class Action { GENERATE, FALLBACK, DROP }

/** 预处理链路对单条通知的判定结果。 */
data class Decision(
    val action: Action,
    val reason: String,
    val notification: Notification,
    val fallbackHint: String? = null,
)

/** 一条待注入输入框的候选回复。 */
data class Candidate(
    val text: String,
    val style: String,
    val rationale: String? = null,
)

/** 风险判定结果。 */
data class RiskAssessment(
    val risky: Boolean,
    val category: String? = null,
    val advice: String? = null,
)