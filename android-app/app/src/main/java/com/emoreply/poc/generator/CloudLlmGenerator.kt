package com.emoreply.poc.generator

import android.util.Log
import com.emoreply.poc.core.DEFAULT_CANDIDATES
import com.emoreply.poc.core.PromptBundle
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.util.concurrent.TimeUnit

/**
 * 云端 LLM 生成器（FR-3.1 真实生成质量评测用）。
 *
 * - 对接 OpenAI 兼容的 `POST {baseUrl}/chat/completions`（DeepSeek / 通义 / OpenAI 等均兼容），
 *   baseUrl / apiKey / model 全部来自运行时设置（[GeneratorSettings]），**绝不硬编码进仓库**。
 * - 实现 `Generator` 回调签名 `(PromptBundle) -> List<String>`，与 `ReplyPipeline` 保持解耦：
 *   配置缺失时可随时回退 `StubGenerator`，链路其余部分（Compose 截断 / Sanitize 清洗 /
 *   Guardrail 双侧护栏）完全不受影响。
 * - 异常一律抛 [CloudLlmException]，由调用方（NotifyCaptureService）按附录 C24 明确提示并降级，
 *   不崩溃、不静默失败。
 *
 * ⚠ 同步阻塞实现：调用方必须保证在非主线程执行（见 NotifyCaptureService 的生成线程）。
 */
class CloudLlmGenerator(
    private val baseUrl: String,
    private val apiKey: String,
    private val model: String,
    private val candidates: Int = DEFAULT_CANDIDATES, // T02224：与 Pipeline 数量契约同源
    private val emoji: Boolean = true, // T01159：false=不提示表情（与 system 侧一致）
) {

    /** 云端调用失败。code 为 HTTP 状态码（网络异常时为 0），userMessage 面向用户、可直接展示。 */
    class CloudLlmException(val code: Int, message: String, val userMessage: String) : Exception(message)

    fun generate(prompt: PromptBundle): List<String> {
        val endpoint = baseUrl.trimEnd('/') + "/chat/completions"
        val body = buildRequest(prompt)
        var conn: HttpURLConnection? = null
        try {
            conn = (java.net.URI(endpoint).toURL().openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = TimeUnit.SECONDS.toMillis(10).toInt()
                readTimeout = TimeUnit.SECONDS.toMillis(60).toInt()
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Authorization", "Bearer $apiKey")
            }
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }

            val httpCode = conn.responseCode
            val stream = if (httpCode in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.readBytes()?.toString(Charsets.UTF_8).orEmpty()
            if (httpCode !in 200..299) throw toException(httpCode, text)

            return parseChoices(text)
        } catch (e: CloudLlmException) {
            throw e
        } catch (e: IOException) {
            Log.w(TAG, "LLM 网络异常: ${e.message}")
            throw CloudLlmException(0, "network error: ${e.message}", "网络异常，无法连接生成服务")
        } finally {
            conn?.disconnect()
        }
    }

    /** system 与当前消息永不丢弃，伪历史按原样拼接——截断已由 Compose 完成，这里不重复处理。 */
    private fun buildRequest(prompt: PromptBundle): JSONObject {
        val userText = buildString {
            if (prompt.history.isNotEmpty()) {
                append("历史对话：\n")
                prompt.history.forEach { append(it).append('\n') }
            }
            append("当前消息：\n").append(prompt.current)
            // FR-3.3：每条候选附一句「为什么这样回」，按「内容|理由」输出，理由缺失时省略 | 部分
            // T01082：候选各有侧重（情感共鸣 / 事实澄清 / 行动建议），且不得使用过度营销套话
            // T01126：禁 markdown 标题/格式符与「候选回复/理由」标签词——用户逐条快速点选直接注入聊天框
            append("\n\n请给出候选回复；每条按「内容|理由」输出，理由用一句话说明情商点，可以省略。" +
                "两个候选各有侧重：一个侧重情感共鸣，另一个侧重事实澄清或行动建议。" +
                "语气克制得体，不得使用「亲」「宝贝」等过度营销套话，不得过度承诺。" +
                "每条候选单独一行纯文本，不要使用 markdown 标题、加粗、列表符号，" +
                "也不要输出「候选回复」「理由」这类小节标题或前缀。")
            // T01159：表情提示与 system 侧同源——关掉时两侧都不提，避免指令打架
            if (emoji) append("正文中可自然使用 1-2 个 emoji 表情，不得堆砌。")
        }
        return JSONObject().apply {
            put("model", model)
            put("temperature", 0.7)
            put("max_tokens", 1500)  // T01082：推理模型（reasoning_content）会占用大量 token，256 时正文为空
            put("n", candidates)
            put(
                "messages", JSONArray().apply {
                    put(JSONObject().put("role", "system").put("content", prompt.system))
                    put(JSONObject().put("role", "user").put("content", userText))
                }
            )
        }
    }

    private fun parseChoices(text: String): List<String> {
        val choices = JSONObject(text).optJSONArray("choices")
            ?: throw CloudLlmException(200, "response missing choices", "生成服务返回格式异常")
        val result = (0 until choices.length()).mapNotNull { i ->
            choices.getJSONObject(i).optJSONObject("message")?.optString("content")?.trim()
        }.filter { it.isNotEmpty() }
        if (result.isEmpty()) {
            throw CloudLlmException(200, "empty choices content", "生成服务未返回内容")
        }
        return result
    }

    /** 附录 C24：余额/配额错误（402）必须明确提示并降级，不得静默。 */
    private fun toException(code: Int, body: String): CloudLlmException {
        val detail = runCatching {
            JSONObject(body).optJSONObject("error")?.optString("message").orEmpty()
        }.getOrDefault("").ifEmpty { body.take(200) }
        val userMessage = when (code) {
            402 -> "云端生成配额不足（余额用尽），已降级为本地模板"
            401, 403 -> "API Key 无效或无权限，已降级为本地模板"
            429 -> "请求过于频繁（限流），已降级为本地模板"
            in 500..599 -> "生成服务暂不可用（$code），已降级为本地模板"
            else -> "云端生成失败（$code），已降级为本地模板"
        }
        Log.w(TAG, "LLM HTTP $code: $detail")
        return CloudLlmException(code, "HTTP $code: $detail", userMessage)
    }

    companion object {
        private const val TAG = "EmoReply"
    }
}
