package com.emoreply.poc.update

import android.content.Context
import android.util.Log
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection

/**
 * 应用内版本更新（T00881）。
 *
 * 版本源采用「JSON 清单 URL」方案：任意静态 HTTP 服务（对象存储 / 内网 httpd / GitHub Raw）
 * 托管一个 manifest.json 即可，格式：
 * ```json
 * {"versionName": "0.2.0", "versionCode": 2, "apkUrl": "https://.../app-release.apk", "notes": "更新说明"}
 * ```
 * versionCode 为比较基准（longVersionCode，单调递增），versionName/notes 仅展示。
 *
 * 流程：检查（启动自动 + 手动按钮）→ 发现新版本展示「立即更新」→ 下载（进度回调，
 * 断点不续传、失败可重试）→ FileProvider + ACTION_VIEW 自动拉起安装。
 * 所有异常折算为 [UpdateException]，userMessage 可直接展示——不崩溃、不静默失败。
 */
object UpdateManager {

    private const val TAG = "UpdateManager"

    /** 用户主动取消下载的异常码（区别于网络 0 / 清单 200 / HTTP 原始码）。 */
    const val CODE_CANCELLED = 1

    open class UpdateException(val code: Int, message: String, val userMessage: String) : Exception(message)

    /** 用户中途取消下载（T01838）：半成品 APK 已清理，状态保留可再次点击继续。 */
    class UpdateCancelledException :
        UpdateException(CODE_CANCELLED, "download cancelled by user", "已取消下载")

    data class Manifest(
        val versionCode: Long,
        val versionName: String,
        val apkUrl: String,
        val notes: String,
    )

    /** 当前版本信息。 */
    data class LocalVersion(val versionCode: Long, val versionName: String)

    fun localVersion(context: Context): LocalVersion {
        val pi = context.packageManager.getPackageInfo(context.packageName, 0)
        return LocalVersion(
            versionCode = if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else pi.versionCode.toLong(),
            versionName = pi.versionName.orEmpty(),
        )
    }

    /** 拉取远端清单并比较；无新版本返回 null。 */
    fun check(context: Context, manifestUrl: String): Manifest? {
        val remote = fetchManifest(manifestUrl)
        val local = localVersion(context)
        return if (remote.versionCode > local.versionCode) remote else null
    }

    private fun fetchManifest(manifestUrl: String): Manifest {
        val text = httpGet(manifestUrl) { conn ->
            conn.connectTimeout = 10_000
            conn.readTimeout = 10_000
        }
        val obj = try {
            JSONObject(text)
        } catch (e: Exception) {
            throw UpdateException(200, "bad manifest json: ${e.message}", "版本清单格式异常，请检查清单 URL")
        }
        val versionCode = obj.optLong("versionCode", 0L)
        val apkUrl = obj.optString("apkUrl").orEmpty()
        if (versionCode <= 0 || apkUrl.isBlank()) {
            throw UpdateException(200, "manifest missing versionCode/apkUrl", "版本清单缺少 versionCode 或 apkUrl")
        }
        return Manifest(
            versionCode = versionCode,
            versionName = obj.optString("versionName", ""),
            apkUrl = apkUrl,
            notes = obj.optString("notes", ""),
        )
    }

    /** 边拷贝边回调进度（0..100）：unknown 长度（total<=0）不回调，结束后由调用方补 100。
     *  isCancelled 在每个缓冲块边界检查（T01838），命中即抛 [UpdateCancelledException]。 */
    private fun copyWithProgress(input: java.io.InputStream, output: java.io.OutputStream,
                                 total: Long, onProgress: (Int) -> Unit,
                                 isCancelled: () -> Boolean = { false }) {
        val buf = ByteArray(64 * 1024)
        var read: Int
        var done = 0L
        var lastPct = -1
        while (input.read(buf).also { read = it } != -1) {
            if (isCancelled()) throw UpdateCancelledException()
            output.write(buf, 0, read)
            done += read
            if (total > 0) {
                val pct = (done * 100 / total).toInt()
                if (pct != lastPct) {
                    lastPct = pct
                    onProgress(pct)
                }
            }
        }
    }

    /** 下载 APK 到 app 缓存目录；onProgress(0..100) 在下载线程回调。
     *  cancelled（可选）置 true 可中途取消：抛 [UpdateCancelledException] 并清理半成品。 */
    fun download(apkUrl: String, target: File, onProgress: (Int) -> Unit,
                 cancelled: java.util.concurrent.atomic.AtomicBoolean? = null) {
        try {
            val conn = (java.net.URI(apkUrl).toURL().openConnection() as HttpURLConnection).apply {
                connectTimeout = 10_000
                readTimeout = 30_000
            }
            val total = conn.contentLengthLong
            conn.inputStream.use { input ->
                target.outputStream().use { output ->
                    copyWithProgress(input, output, total, onProgress) { cancelled?.get() == true }
                }
            }
            if (total <= 0) onProgress(100)
            conn.disconnect()
        } catch (e: UpdateCancelledException) {
            if (!target.delete()) Log.w(TAG, "半成品 APK 删除失败：${target.name}")
            throw e
        } catch (e: IOException) {
            if (!target.delete()) Log.w(TAG, "半成品 APK 删除失败：${target.name}")
            throw UpdateException(0, "download failed: ${e.message}", "下载失败：请检查网络后重试")
        }
    }

    /** 拉起系统安装器。未授予「安装未知应用」权限时先跳系统设置页。 */
    fun install(context: Context, apk: File) {
        if (Build.VERSION.SDK_INT >= 26 &&
            !context.packageManager.canRequestPackageInstalls()
        ) {
            context.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
            )
            return
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    fun cacheApkFile(context: Context): File = File(context.cacheDir, "update/app-update.apk")

    private fun httpGet(url: String, configure: (HttpURLConnection) -> Unit): String {
        var conn: HttpURLConnection? = null
        try {
            conn = java.net.URI(url).toURL().openConnection() as HttpURLConnection
            configure(conn)
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.readBytes()?.toString(Charsets.UTF_8).orEmpty()
            if (code !in 200..299) {
                throw UpdateException(code, "HTTP $code: ${text.take(200)}", "网络请求失败（HTTP $code），请稍后重试")
            }
            return text
        } catch (e: UpdateException) {
            throw e
        } catch (e: IOException) {
            throw UpdateException(0, "network error: ${e.message}", "网络异常，请检查网络后重试")
        } finally {
            conn?.disconnect()
        }
    }
}
