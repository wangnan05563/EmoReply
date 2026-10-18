package com.emoreply.poc.store

import com.emoreply.poc.model.Candidate
import com.emoreply.poc.model.Notification

/** 最近一次需要回复的消息与其候选（捕获层写入、IME 读取）。 */
data class PendingReply(
    val notification: Notification,
    val candidates: List<Candidate>,
    val notice: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)

/**
 * 跨组件共享「待回复」状态。
 *
 * 捕获服务与 IME 同进程但不同线程，故用 volatile 保证可见性；不做持久化——
 * 概念验证只关心「当前这一条」，进程重启即清空（符合最小收集原则）。
 */
object ReplyStore {

    @Volatile
    private var current: PendingReply? = null

    fun publish(reply: PendingReply) {
        current = reply
    }

    fun latest(): PendingReply? = current

    fun clear() {
        current = null
    }
}