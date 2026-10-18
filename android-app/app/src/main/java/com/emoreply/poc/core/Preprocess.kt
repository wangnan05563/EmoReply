package com.emoreply.poc.core

import com.emoreply.poc.model.Action
import com.emoreply.poc.model.Decision
import com.emoreply.poc.model.Notification

/** 重复推送通常紧随其后；窗口压到秒级，避免把同一人快速连发的两条消息误吞。 */
const val DEDUPE_WINDOW_MS = 2_000L

const val FALLBACK_HINT = "上下文有限：请粘贴消息原文，或基于标题生成泛化建议"

/** 窗口内找 n 的归并目标下标：同「平台+会话+发送方」且至少一方正文为空；无则 -1。 */
private fun mergeTargetIndex(n: Notification, kept: List<Notification>, window: Long): Int {
    for (i in kept.indices.reversed()) {
        val k = kept[i]
        if (n.timestamp - k.timestamp > window) break
        if (k.source != n.source || k.chat != n.chat || k.sender != n.sender) continue
        if (!k.body.isNullOrEmpty() && !n.body.isNullOrEmpty() && k.body != n.body) continue
        return i
    }
    return -1
}

/** 把重复通知 n 的信号并入归并目标（提及/引用取或、补空正文与标题）。 */
private fun absorb(target: Notification, n: Notification): Notification = target.copy(
    mentionsMe = target.mentionsMe || n.mentionsMe,
    quotesMe = target.quotesMe || n.quotesMe,
    body = if (target.body.isNullOrEmpty()) n.body else target.body,
    title = if (target.title.isNullOrEmpty()) n.title else target.title,
)

/**
 * 归并重复通知（FR-1.4），输入需按 timestamp 升序。
 *
 * 判定键是「平台+会话+发送方」而非整条载荷：微信对同一条消息可能先推摘要
 * （正文为空）再推详情，只比正文会把一条消息算成两条。窗口内仅当「至少一方
 * 正文为空」时才归并——双方都有正文且不同时，那就是两条真实消息。
 */
fun dedupe(items: List<Notification>, window: Long = DEDUPE_WINDOW_MS): List<Notification> {
    val kept = mutableListOf<Notification>()
    for (n in items) {
        val idx = mergeTargetIndex(n, kept, window)
        if (idx < 0) {
            kept.add(n)
        } else {
            kept[idx] = absorb(kept[idx], n)
        }
    }
    return kept
}

/**
 * 判定消息是否指向当前用户（FR-1.3，首期仅硬规则）。
 *
 * 私聊一律视为指向；群聊只看 @提及 与 引用/回复。纯语义判定按 SRS FR-1.6
 * 降级为二期 P2——只读通知下上下文有限，误判「需要回复」的打扰成本高于漏判。
 */
fun isDirectedToMe(n: Notification): Boolean = !n.isGroup || n.mentionsMe || n.quotesMe

/** 对单条（已去重的）通知给出处置判定。 */
fun decide(n: Notification): Decision {
    if (n.isSystem) return Decision(Action.DROP, "系统消息，非用户间对话（FR-1.4）", n)
    if (n.fromSelf) return Decision(Action.DROP, "自己发出的消息（FR-1.4）", n)
    if (!isDirectedToMe(n)) return Decision(Action.DROP, "群聊未 @ 或引用当前用户（FR-1.3 硬规则）", n)
    if (n.body.isNullOrBlank()) {
        // FR-1.5：空载荷一律走兜底，禁止用模板冒充真实生成（附录 C-2 C20）
        return Decision(Action.FALLBACK, "通知载荷为空，禁止强行生成（FR-1.5）", n, FALLBACK_HINT)
    }
    return Decision(Action.GENERATE, "指向当前用户且正文可用", n)
}

/** 完整预处理链路：按时间排序 → 去重 → 逐条判定。 */
fun preprocess(items: List<Notification>): List<Decision> =
    dedupe(items.sortedBy { it.timestamp }).map { decide(it) }