package com.emoreply.poc.notify

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.emoreply.poc.R

/**
 * 通知统一入口（T01336）。
 *
 * 为什么必须走这里：Android 5.0 起（本项目 minSdk=26，全部受影响）系统会**单色化**
 * 通知小图标——彩色 PNG 会被压成一块无法辨认的灰块，这是最典型的通知图标坑。
 * 因此小图标一律使用 `R.drawable.ic_stat_emoreply`：由 `brand/mono/emoreply-inverse-*.png`
 * 生成的**纯白剪影 + alpha**（tools/gen_notif_icon.py，脚本内含纯白断言），
 * 系统会按前景色为其着色，永远不会出现灰块。
 *
 * 新增通知时请调用 [notify]，不要自己 `setSmallIcon`——资产选错是静默失败，
 * 编译期和运行时都不会报错，只有真机肉眼能看出来。
 */
object NotifChannels {

    /** 默认渠道（候选已生成 / 读屏完成等轻量提示）。 */
    const val CHANNEL_DEFAULT = "emoreply_default"

    /** 是否已获得发通知权限（Android 13+ 需运行时授予 POST_NOTIFICATIONS）。 */
    fun canPost(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED

    /** 建渠道（重复调用无副作用；渠道创建后其重要性由系统接管，改不得）。 */
    fun ensureDefaultChannel(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val mgr = context.getSystemService(NotificationManager::class.java) ?: return
        if (mgr.getNotificationChannel(CHANNEL_DEFAULT) != null) return
        val channel = NotificationChannel(
            CHANNEL_DEFAULT,
            context.getString(R.string.notif_channel_default_name),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply { description = context.getString(R.string.notif_channel_default_desc) }
        mgr.createNotificationChannel(channel)
    }

    /**
     * 发一条轻量通知。无权限或系统拒绝时静默返回 false，不抛异常打断调用方主流程。
     *
     * @param id 同一 id 会覆盖上一条（用于「读屏完成」这类可刷新的状态提示）。
     */
    fun notify(context: Context, id: Int, title: String, text: String): Boolean {
        if (!canPost(context)) return false
        return runCatching {
            ensureDefaultChannel(context)
            val notification = NotificationCompat.Builder(context, CHANNEL_DEFAULT)
                .setSmallIcon(R.drawable.ic_stat_emoreply) // T01336：单色白剪影，禁止换彩色图
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .build()
            NotificationManagerCompat.from(context).notify(id, notification)
            true
        }.getOrDefault(false)
    }
}
