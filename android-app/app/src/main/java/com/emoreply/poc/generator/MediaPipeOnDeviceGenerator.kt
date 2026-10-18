package com.emoreply.poc.generator

import android.content.Context
import android.util.Log
import com.emoreply.poc.core.PromptBundle
import com.google.mediapipe.tasks.genai.llminference.LlmInference
import com.google.mediapipe.tasks.genai.llminference.LlmInference.LlmInferenceOptions
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * 端侧离线 LLM 生成器（T01243 FR-8.1）。
 *
 * 用 MediaPipe LlmInference 加载一个 `.task` 端侧模型做纯本地推理：飞行/无网/隐私模式
 * 都能出候选，与云端 [CloudLlmGenerator] 走同一回调签名 `(PromptBundle) -> List<String>`，
 * 调用方可无损切换、配置缺失时回退云端/桩，链路其余部分完全不变。
 *
 * 关键取舍：
 * - **模型不随 APK 打包**：AAR 只带推理引擎 .so，模型 `.task` 由 [ensureDownloaded] 首次
 *   下载到应用私有目录，或由用户 adb push 到指定路径——正因如此才不因 ~1GB 模型把包体
 *   撑爆（FR-8.1 验收）。
 * - **单次推理返回一条候选**：端侧 LLM 逐 token 生成慢，不适合像云端那样一次出多条；POC
 *   以「断网能生成一次」为验收，多候选留给后续按需构造 diff 播送。
 * - **异常复用 [CloudLlmGenerator.CloudLlmException]**：让既有 catch（NotifyCaptureService /
 *   EmoReplyImeService）无需改动即可提示并降级，不新增分支。
 */
class MediaPipeOnDeviceGenerator private constructor(private val llm: LlmInference) {

    /**
     * 组装单条端侧 prompt：system 约束在前，随后是历史与当前消息，与云端 buildRequest
     * 保持同构（历史原样拼接，截断已由 Compose 完成）。
     */
    fun generate(prompt: PromptBundle): List<String> {
        val text = buildString {
            append(prompt.system).append('\n')
            if (prompt.history.isNotEmpty()) {
                append("历史对话：\n")
                prompt.history.forEach { append(it).append('\n') }
            }
            append("当前消息：\n").append(prompt.current)
        }
        val raw = try {
            llm.generateResponse(text)
        } catch (e: Exception) {
            Log.w(TAG, "端侧生成失败: ${e.message}")
            throw CloudLlmGenerator.CloudLlmException(0, "on-device: ${e.message}", "端侧模型生成失败，已降级")
        }
        val out = raw.trim()
        return if (out.isEmpty()) emptyList() else listOf(out)
    }

    fun close() = runCatching { llm.close() }

    companion object {
        private const val TAG = "EmoReply"
        /** 已加载实例缓存（按路径），避免每次管线重建都重载 ~1GB 模型。 */
        @Volatile private var cached: MediaPipeOnDeviceGenerator? = null
        @Volatile private var cachedFor: String? = null
        @Volatile private var loading = false
        private val lock = Any()

        /** 首次调用时加载模型；路径无效/加载失败返回 null（调用方回落云端/桩）。线程安全。 */
        fun create(context: Context, modelPath: String): MediaPipeOnDeviceGenerator? {
            if (!File(modelPath).isFile) return null
            cachedFor?.takeIf { it == modelPath }?.let { return cached }
            synchronized(lock) {
                cachedFor?.takeIf { it == modelPath }?.let { return cached }
                if (loading) return null // 并发加载保护：让请求回落，避免多线程挤 LLM
                loading = true
                return try {
                    val options = LlmInferenceOptions.builder()
                        .setModelPath(modelPath)
                        .setMaxTokens(512)
                        .build()
                    val llm = LlmInference.createFromOptions(
                        context.applicationContext,
                        options,
                    )
                    MediaPipeOnDeviceGenerator(llm)
                        .also { cached = it; cachedFor = modelPath }
                } catch (e: Exception) {
                    Log.w(TAG, "端侧模型加载失败: ${e.message}")
                    null
                } finally {
                    loading = false
                }
            }
        }

        fun clearCache() = synchronized(lock) {
            cached?.close(); cached = null; cachedFor = null
        }

        /**
         * FR-8.1 首下：把 [modelUrl] 下载到 [dir]，文件名取自 URL 末段——**不同模型落在不同文件**，
         * 不会像早期固定名那样第二次换模型时静默复用旧文件。已存在且非空则直接复用（幂等）。
         *
         * 失败一律返回 [DownloadOutcome.Failure] 且 reason 可直接展示给用户：把「404 不是直链」
         * 「401/403 需要登录鉴权」「返回的是网页不是模型文件」「网络不通」区分开。早期实现不查
         * `responseCode`，任何异常都糊成一句「下载失败或地址不可用」，用户无从判断该换链接还是换路径。
         */
        fun ensureDownloaded(
            dir: File,
            modelUrl: String,
            onProgress: (Long) -> Unit = {},
        ): DownloadOutcome {
            val url = modelUrl.trim()
            if (url.isBlank()) return DownloadOutcome.Failure("请先填写下载地址")
            val first = try {
                URI(url).toURL()
            } catch (e: Exception) {
                return DownloadOutcome.Failure("地址格式不正确：需填写完整的 http/https 链接")
            }
            if (first.protocol !in HTTP_PROTOCOLS) {
                return DownloadOutcome.Failure("仅支持 http/https 直链，当前协议：${first.protocol}")
            }
            val target = File(dir, fileNameOf(url))
            if (target.isFile && target.length() > 0) return DownloadOutcome.Success(target)
            if (!dir.exists() && !dir.mkdirs()) {
                return DownloadOutcome.Failure("无法创建下载目录：${dir.absolutePath}")
            }
            return try {
                download(first, target, onProgress)
            } catch (e: Exception) {
                target.delete()
                Log.w(TAG, "端侧模型下载异常: ${e.message}")
                DownloadOutcome.Failure("下载中断：${e.message ?: e.javaClass.simpleName}")
            }
        }

        /** 跟随重定向（含 http↔https，`HttpURLConnection` 默认不跟）完成下载并校验内容。 */
        private fun download(
            start: java.net.URL,
            target: File,
            onProgress: (Long) -> Unit,
        ): DownloadOutcome {
            var current = start
            var hops = 0
            var conn: HttpURLConnection? = null
            try {
                while (true) {
                    conn = (current.openConnection() as HttpURLConnection).apply {
                        requestMethod = "GET"
                        // 手动跟转：跨协议（http→https）默认不跟随，且要能把最终 40x 读出来判因
                        instanceFollowRedirects = false
                        connectTimeout = TimeUnit.SECONDS.toMillis(15).toInt()
                        // 端侧模型动辄 GB 级，读超时给足余量，避免正常慢速链路被误判为失败
                        readTimeout = TimeUnit.MINUTES.toMillis(2).toInt()
                        setRequestProperty("User-Agent", USER_AGENT)
                    }
                    val code = conn.responseCode
                    if (code in 300..399) {
                        val location = conn.getHeaderField("Location")
                        conn.disconnect()
                        conn = null
                        if (location.isNullOrBlank() || ++hops > MAX_REDIRECTS) {
                            return DownloadOutcome.Failure("地址重定向异常（次数过多或缺少跳转目标）")
                        }
                        current = URL(current, location)
                        continue
                    }
                    return readBody(conn, code, target, onProgress)
                }
            } finally {
                conn?.disconnect()
            }
        }

        /** 状态码判因 → 内容类型判因 → 落盘 → 体积兜底校验。 */
        private fun readBody(
            conn: HttpURLConnection,
            code: Int,
            target: File,
            onProgress: (Long) -> Unit,
        ): DownloadOutcome {
            if (code in listOf(401, 403)) {
                return DownloadOutcome.Failure(AUTH_REQUIRED_HINT)
            }
            if (code == 404 || code == 410) {
                return DownloadOutcome.Failure(
                    "地址不存在（HTTP $code）：这多半是网页或「下载」按钮的链接，不是文件直链。" +
                        "Kaggle 模型文件需登录才能取，建议在电脑上下载后 adb push（用「常用模型」按钮填的路径）"
                )
            }
            if (code !in 200..299) {
                return DownloadOutcome.Failure("服务端返回 HTTP $code，无法下载")
            }
            val contentType = conn.contentType.orEmpty().lowercase()
            if (contentType.startsWith("text/html") || contentType.startsWith("text/xml")) {
                return DownloadOutcome.Failure(
                    "该地址返回的是网页（$contentType）而非模型文件，通常是登录页或未授权的跳转。" +
                        "请改用文件直链，或在电脑上下载后 adb push"
                )
            }
            val declared = conn.contentLengthLong
            if (declared in 1 until MIN_MODEL_BYTES) {
                return DownloadOutcome.Failure(
                    "地址内容仅 ${declared}B，不像模型文件（Gemma 3 270M 约 300MB）。请确认链接指向 .task 文件本身"
                )
            }
            var written = 0L
            var reported = 0L
            conn.inputStream.use { input ->
                FileOutputStream(target).use { output ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n <= 0) break
                        output.write(buf, 0, n)
                        written += n
                        if (written - reported >= (2L shl 20)) {
                            reported = written
                            onProgress(written)
                        }
                    }
                    output.flush()
                }
            }
            if (written < MIN_MODEL_BYTES) {
                target.delete()
                return DownloadOutcome.Failure(
                    "实际下载 ${written / 1024}KB，远小于模型体积，判定为无效内容已删除。请改用文件直链或 adb push"
                )
            }
            return DownloadOutcome.Success(target)
        }

        /**
         * 从 URL 取落地文件名：只保留末段的合法字符，杜绝 `../` 穿越；取不到可用名字时回落固定名。
         * 内部分支/边界由 [fileNameOf] 的调用点覆盖（无 Android 测试源集，逻辑纯 JVM，可离线复核）。
         */
        internal fun fileNameOf(modelUrl: String): String {
            val last = modelUrl.trim().substringAfterLast('/').substringBefore('?').substringBefore('#')
            val name = last.filter { it.isLetterOrDigit() || it in "._-" }.take(80)
            return if (name.length >= 5 && name.contains('.')) name else "emoreply_model.task"
        }

        private const val MAX_REDIRECTS = 5
        private const val USER_AGENT = "EmoReply/1.0 (Android; LiteRT model fetch)"
        private val HTTP_PROTOCOLS = setOf("http", "https")

        /**
         * 模型文件体积下限（1MB）。端侧 LLM 权重最小的 Gemma 3 270M int8 也在百 MB 量级，
         * 用它兜住「200 但其实是登录页/错误页」这一类假成功——早期实现会把 HTML 存成
         * `.task` 并提示下载完成，随后模型加载才炸，排查成本极高。
         */
        private const val MIN_MODEL_BYTES = 1L shl 20

        private const val AUTH_REQUIRED_HINT =
            "该地址需要登录鉴权（HTTP 401/403）：Kaggle / HuggingFace 的模型文件没有匿名直链。" +
                "两条可行路径：① 在电脑上用 Kaggle CLI 或浏览器登录后下载，再点「常用模型」" +
                "按钮，按其状态行给出的 adb 命令把文件放进应用目录并回填路径；" +
                "② 换成你自己服务器上带凭据参数的文件直链（同一 Wi-Fi 下也可用 " +
                "`python -m http.server` 把本地文件暴露成 http 直链填这里）"
    }
}

/** [MediaPipeOnDeviceGenerator.ensureDownloaded] 的结果：失败原因可直接展示给用户。 */
sealed class DownloadOutcome {
    /** 模型文件已就位（新下载或复用已有同名文件）。 */
    data class Success(val file: File) : DownloadOutcome()

    /** 下载未成功，[reason] 为面向用户的中文原因（已区分 404 / 需鉴权 / 非模型内容 / 网络）。 */
    data class Failure(val reason: String) : DownloadOutcome()
}
