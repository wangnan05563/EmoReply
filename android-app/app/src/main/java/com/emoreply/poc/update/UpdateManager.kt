package com.emoreply.poc.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 应用内版本更新（T00881 入口；T04015 路线A：对齐桌面内置 GitHub releases）。
 *
 * 版本源 = 代码常量 [REPO] + GitHub `releases/latest`，不读配置、不开放输入、不留测试后门
 * （更新包等于以用户身份执行的程序，能把源指向别处的开关就是攻击面）。守卫口径照搬桌面
 * desktop-app/updater.js：仅允许 GitHub 官方域名 [ALLOW_HOSTS]、文件名清洗 [sanitizeName]、
 * 下载字节数与响应 size 不符即删残留判失败。无发布/限流/异常折算为结构化 [CheckResult]，
 * 下载/安装异常折算为 [UpdateException]，userMessage 可直接展示——不崩溃、不静默失败。
 *
 * 流程：检查（启动自动 + 手动按钮）→ NEWER 展示「立即更新」→ 下载（进度回调、可取消、
 * size 校验）→ FileProvider + ACTION_VIEW 拉起系统安装器（系统安装界面即「安装前显式确认」，
 * 非静默更新）。版本号比较与桌面同口径：优先日期序号 YYYYMMDD-NNN，其次 semver。
 */
object UpdateManager {

    private const val TAG = "UpdateManager"

    /** 内置仓库（唯一事实源），与桌面端 updater.js 同址。 */
    const val REPO = "wangnan05563/EmoReply"

    /** 仅认 GitHub 官方域名：资产 URL 来自远端响应，不校验主机等于「服务器说下哪就下哪」。 */
    private val ALLOW_HOSTS = setOf(
        "api.github.com", "github.com", "raw.githubusercontent.com",
        "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    )

    /** 用户主动取消下载的异常码（区别于网络 0 / HTTP 原始码）。 */
    const val CODE_CANCELLED = 1

    open class UpdateException(val code: Int, message: String, val userMessage: String) : Exception(message)

    /** 用户中途取消下载（T01838）：半成品 APK 已清理，状态保留可再次点击继续。 */
    class UpdateCancelledException :
        UpdateException(CODE_CANCELLED, "download cancelled by user", "已取消下载")

    /** 检查结果状态（对齐桌面 updater.js check() 的 state 口径）。 */
    enum class UpdateState { NEWER, LATEST, NO_RELEASE, RATE_LIMIT, ERROR }

    /** 远端发布里的安卓安装包信息（资产 URL/文件名已经过白名单 + 清洗）。 */
    data class ReleaseInfo(
        val versionName: String,
        val apkUrl: String,
        val apkName: String,
        val size: Long,
        val notes: String,
        val pageUrl: String,
    )

    /** 结构化检查结果：state 决定界面文案，release 仅在 NEWER 时非空。 */
    data class CheckResult(
        val state: UpdateState,
        val message: String,
        val current: String,
        val latest: String = "",
        val release: ReleaseInfo? = null,
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

    // ---- 版本号比较（照搬桌面 updater.js versionKey/compareVersions）----
    // 仓库标签可能是 YYYYMMDD-NNN（桌面日期序号）或 semver；安卓 versionName 为 semver。
    // 优先日期序号，其次点分 semver；口径不同不硬比（返回 0），避免把 0.1.0 误判成比日期版更新。
    private val DATE_SEQ_RE = Regex("""(\d{8})-(\d{3})""")
    private val SEMVER_RE = Regex("""^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$""")
    private val NAME_RE = Regex("""^[\w][\w.\- ()+]*$""")
    private val META_EXT_RE = Regex("""\.(blockmap|yml|yaml|json)$""", RegexOption.IGNORE_CASE)

    private data class VersionKey(val kind: String, val a: Long, val b: Long, val c: Long = 0L)

    private fun versionKey(raw: String?): VersionKey? {
        val s = (raw ?: "").trim()
        DATE_SEQ_RE.find(s)?.let { m ->
            return VersionKey("date", m.groupValues[1].toLong(), m.groupValues[2].toLong())
        }
        SEMVER_RE.matchEntire(s)?.let { m ->
            return VersionKey("semver", m.groupValues[1].toLong(), m.groupValues[2].toLong(), m.groupValues[3].toLong())
        }
        return null
    }

    /** -1=older 小于 newer，0=同版本/口径不同，1=older 已最新；null=仓库版本号读不懂。 */
    private fun compareVersions(older: String?, newer: String?): Int? {
        val x = versionKey(older)
        val y = versionKey(newer) ?: return null
        if (x == null) return 1
        if (x.kind != y.kind) return 0
        val d = if (x.a != y.a) x.a - y.a else if (x.b != y.b) x.b - y.b else x.c - y.c
        return if (d < 0) -1 else if (d > 0) 1 else 0
    }

    /** 资产名来自远端响应：先取路径最后一段（两种分隔符都认），再剥前导点、清洗字符。
     *  顺序不能反——先剥斜杠会把 `../../evil.apk` 的前导点留下，落盘名仍不对。 */
    private fun sanitizeName(name: String?): String {
        val s = (name ?: "").replace('\\', '/')
        val base = s.substringAfterLast('/').trimStart('.')
        return if (NAME_RE.matches(base) && base.length <= 120) base else ""
    }

    private data class Asset(val name: String, val url: String, val size: Long)

    /** 选安卓安装包：只要 .apk，优先 release 命名，忽略 blockmap/元数据。 */
    private fun pickApkAsset(release: JSONObject): Asset? {
        val arr = release.optJSONArray("assets") ?: return null
        val apks = ArrayList<Asset>()
        for (i in 0 until arr.length()) {
            val a = arr.optJSONObject(i) ?: continue
            val url = a.optString("browser_download_url")
            if (url.isBlank()) continue
            val name = a.optString("name")
            if (META_EXT_RE.containsMatchIn(name)) continue
            if (!name.endsWith(".apk", ignoreCase = true)) continue
            apks.add(Asset(name, url, a.optLong("size", 0L)))
        }
        return apks.firstOrNull { it.name.contains("release", ignoreCase = true) } ?: apks.firstOrNull()
    }

    /** 查内置仓库最新发布版本；无发布/限流/异常均返回结构化状态，不抛。 */
    fun check(context: Context): CheckResult {
        val current = localVersion(context).versionName
        val apiUrl = "https://api.github.com/repos/$REPO/releases/latest"
        val resp = try {
            httpGetResponse(apiUrl) { conn ->
                conn.connectTimeout = 15_000
                conn.readTimeout = 15_000
                conn.setRequestProperty("User-Agent", "emoreply-android-updater")
                conn.setRequestProperty("Accept", "application/vnd.github+json")
            }
        } catch (e: IOException) {
            return CheckResult(UpdateState.ERROR, "连不上 GitHub（$REPO）：请检查网络后重试", current)
        }
        when (resp.code) {
            404 -> return CheckResult(
                UpdateState.NO_RELEASE,
                "内置仓库 $REPO 还没有发布任何版本，暂时没有可检测的线上更新。", current)
            403, 429 -> return CheckResult(
                UpdateState.RATE_LIMIT,
                "GitHub 接口调用过于频繁（未登录限流），请过一小时再试。", current)
        }
        if (resp.code !in 200..299) {
            return CheckResult(UpdateState.ERROR, "GitHub 返回异常状态 ${resp.code}，请稍后再试。", current)
        }
        val data = try {
            JSONObject(resp.body)
        } catch (e: Exception) {
            return CheckResult(UpdateState.ERROR, "GitHub 返回内容解析失败，请稍后再试。", current)
        }
        val latest = data.optString("tag_name").ifBlank { data.optString("name") }
        val pageUrl = data.optString("html_url").ifBlank { "https://github.com/$REPO/releases" }
        val cmp = compareVersions(current, latest)
            ?: return CheckResult(UpdateState.ERROR, "读不懂仓库版本号「$latest」，无法判断是否需要更新。", current, latest)
        if (cmp >= 0) {
            return CheckResult(UpdateState.LATEST, "已是最新版本（$current）。", current, latest)
        }
        val asset = pickApkAsset(data)
            ?: return CheckResult(UpdateState.ERROR, "发现新版本 $latest，但发布里没有可下载的安卓安装包（apk）。", current, latest)
        val name = sanitizeName(asset.name)
        val host = runCatching { URI(asset.url).host }.getOrNull()
        if (name.isBlank() || host == null || host !in ALLOW_HOSTS) {
            return CheckResult(UpdateState.ERROR, "发现新版本 $latest，但下载地址不在 GitHub 官方域名内，已拒绝。", current, latest)
        }
        val release = ReleaseInfo(latest, asset.url, name, asset.size, data.optString("body"), pageUrl)
        return CheckResult(UpdateState.NEWER, "发现新版本 $latest（当前 $current）。", current, latest, release)
    }

    /** 边拷贝边回调进度（0..100），返回已写字节数。unknown 长度（total<=0）不回调进度。
     *  isCancelled 在每个缓冲块边界检查（T01838），命中即抛 [UpdateCancelledException]。 */
    private fun copyWithProgress(input: InputStream, output: OutputStream,
                                 total: Long, onProgress: (Int) -> Unit,
                                 isCancelled: () -> Boolean = { false }): Long {
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
        return done
    }

    /** 下载 APK 到 app 缓存目录；onProgress(0..100) 在下载线程回调。
     *  expectedSize>0 时以下载字节数核对，不符即删残留判失败（照搬桌面 size 守卫）。
     *  cancelled（可选）置 true 可中途取消：抛 [UpdateCancelledException] 并清理半成品。 */
    fun download(apkUrl: String, target: File, expectedSize: Long, onProgress: (Int) -> Unit,
                 cancelled: AtomicBoolean? = null) {
        val host = runCatching { URI(apkUrl).host }.getOrNull()
        if (host == null || host !in ALLOW_HOSTS) {
            throw UpdateException(0, "unsafe download host: $host", "下载地址不在 GitHub 官方域名内，已拒绝")
        }
        try {
            val conn = (URI(apkUrl).toURL().openConnection() as HttpURLConnection).apply {
                connectTimeout = 10_000
                readTimeout = 30_000
                setRequestProperty("User-Agent", "emoreply-android-updater")
            }
            val code = conn.responseCode
            if (code !in 200..299) {
                conn.disconnect()
                throw UpdateException(code, "download HTTP $code", "下载失败（HTTP $code），请稍后重试")
            }
            val total = if (expectedSize > 0) expectedSize else conn.contentLengthLong
            var received = 0L
            conn.inputStream.use { input ->
                target.outputStream().use { output ->
                    received = copyWithProgress(input, output, total, onProgress) { cancelled?.get() == true }
                }
            }
            conn.disconnect()
            if (total > 0 && received != total) {
                if (!target.delete()) Log.w(TAG, "残留 APK 删除失败：${target.name}")
                throw UpdateException(0, "size mismatch: $received/$total",
                    "下载不完整（$received/$total 字节），已删除残留文件，请重试")
            }
            if (total <= 0) onProgress(100)
        } catch (e: UpdateCancelledException) {
            if (!target.delete()) Log.w(TAG, "半成品 APK 删除失败：${target.name}")
            throw e
        } catch (e: UpdateException) {
            throw e
        } catch (e: IOException) {
            if (!target.delete()) Log.w(TAG, "半成品 APK 删除失败：${target.name}")
            throw UpdateException(0, "download failed: ${e.message}", "下载失败：请检查网络后重试")
        }
    }

    /** 拉起系统安装器（系统安装界面即「安装前显式确认」）。未授予「安装未知应用」权限时先跳设置页。 */
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

    private data class HttpResponse(val code: Int, val body: String)

    private fun httpGetResponse(url: String, configure: (HttpURLConnection) -> Unit): HttpResponse {
        var conn: HttpURLConnection? = null
        try {
            conn = URI(url).toURL().openConnection() as HttpURLConnection
            configure(conn)
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.readBytes()?.toString(Charsets.UTF_8).orEmpty()
            return HttpResponse(code, text)
        } finally {
            conn?.disconnect()
        }
    }
}