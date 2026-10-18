package com.emoreply.poc.generator

import com.emoreply.poc.core.PromptBundle

/**
 * 概念验证期的确定性生成桩。
 *
 * 真机验收只考核「捕获 → 候选 → 注入」链路是否打通，生成质量不在 M1 考核范围内，
 * 因此不接云端 LLM——同时也避免把 API Key 带进验证包。
 */
object StubGenerator {

    fun generate(prompt: PromptBundle): List<String> {
        val current = prompt.current.substringAfter("：", "")
        return when {
            current.contains("？") || current.contains("?") -> listOf(
                "好的，我看一下，晚点回复你哈",
                "收到，我想想再回你～",
            )
            current.contains("谢谢") || current.contains("感谢") -> listOf("不客气～")
            else -> listOf(
                "收到，谢谢～",
                "好嘞，知道了",
            )
        }
    }
}