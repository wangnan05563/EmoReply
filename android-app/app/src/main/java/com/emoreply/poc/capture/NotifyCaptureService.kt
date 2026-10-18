package com.emoreply.poc.capture

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.emoreply.poc.core.ReplyPipeline
import com.emoreply.poc.core.ScenarioParser
import com.emoreply.poc.core.Tone
import com.emoreply.poc.generator.CloudLlmGenerator
import com.emoreply.poc.generator.GeneratorSettings
import com.emoreply.poc.generator.MediaPipeOnDeviceGenerator
import com.emoreply.poc.generator.StubGenerator
import com.emoreply.poc.store.FavoritesStore
import com.emoreply.poc.store.KnowledgeBase
import com.emoreply.poc.store.PersonaStore
import com.emoreply.poc.store.PendingReply
import com.emoreply.poc.store.ReplyStore
import com.emoreply.poc.store.UxSettings
import com.emoreply.poc.model.Notification as EmoNotification
import java.util.concurrent.Executors

/**
 * 通知捕获层（FR-1.1 / FR-1.2）。
 *
 * 只读取系统通知载荷，不触碰聊天数据库；解析产物映射为与 m1-poc 同构的
 * [EmoNotification]，因此可直接复用核心链路。
 *
 * 生成步骤（T01043）：配置了云端 LLM 时用 [CloudLlmGenerator]，否则回退 [StubGenerator]；
 * 云端调用在网络线程串行执行（onNotificationPosted 在主线程回调，禁止同步网络）。
 * 云端失败按附录 C24 明确提示并降级为本地模板——不崩溃、不静默失败。
 */
class NotifyCaptureService : NotificationListenerService() {

    private val history = mutableMapOf<String, MutableList<EmoNotification>>()

    // 单线程串行：保持与通知到达一致的顺序，history 读写无需额外加锁
    private val genExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "emo-reply-generator").apply { isDaemon = false }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val posted = sbn ?: return
        val n = parse(posted) ?: return
        genExecutor.execute { process(n) }
    }

    override fun onDestroy() {
        genExecutor.shutdown()
        super.onDestroy()
    }

    private fun process(n: EmoNotification) {
        val key = "${n.source}:${n.chat}"
        val past = history.getOrPut(key) { mutableListOf() }

        // FR-3.2（REQ-009）：风格来自用户设置（正式/亲切/简短/幽默），每次处理实时读取
        val style = GeneratorSettings.load(this).style
        val cloud = cloudPipeline(style)
        // T01262 FR-8.8：同类场景的收藏话术进管线（prompt 优先参考，禁止原样复制）
        val favorites = favoriteTexts()

        // T01538：从通知载荷解析场景/目的维度，使采纳率闭环覆盖通知链路
        val (scenario, purpose) = ScenarioParser.parseDimensions(n.body.orEmpty(), n.source, n.chat.orEmpty())
        // T01537：语气解析——联系人级 > 全局 > 关系推档（同手动链路）
        val persona = persona()
        val named = n.sender?.takeIf { it.isNotBlank() && it != "我" && it != "对方" }
        val contactOverride = named?.let { persona.contactTone(it) }
        val relation = UxSettings.relation(this).ifBlank { named?.let { persona.getRelation(it) }.orEmpty() }
        val tone = Tone.resolveTone(UxSettings.tone(this), relation, contactOverride) ?: UxSettings.tone(this)

        val outcome = try {
            (cloud ?: stubPipeline(style)).handle(
                n, past.toList(), manual = false, tone = tone,
                scenario = scenario, purpose = purpose, favorites = favorites,
            )
        } catch (e: CloudLlmGenerator.CloudLlmException) {
            // C24：明确提示 + 降级为本地模板；同会话历史仍由本条维护
            val fallback = stubPipeline(style).handle(
                n, past.toList(), manual = false, tone = tone,
                scenario = scenario, purpose = purpose, favorites = favorites,
            )
            fallback.copy(notice = e.userMessage).also {
                Log.w(TAG, "云端生成降级 code=${e.code}: ${e.message}")
            }
        }

        past.add(n)
        if (past.size > HISTORY_LIMIT) past.removeAt(0)

        when {
            outcome.candidates.isNotEmpty() ->
                ReplyStore.publish(PendingReply(n, outcome.candidates, outcome.notice))
            outcome.notice != null ->
                ReplyStore.publish(PendingReply(n, emptyList(), outcome.notice))
            else -> Unit
        }

        Log.i(
            TAG,
            "captured source=${n.source} chat=${n.chat} group=${n.isGroup} " +
                "action=${outcome.decision.action} candidates=${outcome.candidates.size} " +
                "notice=${outcome.notice} scenario=$scenario purpose=$purpose tone=$tone",
        )
    }

    /** T01243 FR-8.1：端侧优先（飞行/无网/隐私可用）→ 云端 → 均不可用返回 null 走桩。 */
    private fun cloudPipeline(style: String): ReplyPipeline? {
        val config = GeneratorSettings.load(this)
        // 端侧后端就绪则优先：离线也能出候选，无需任何网络/密钥
        if (GeneratorSettings.isOnDeviceReady(config)) {
            MediaPipeOnDeviceGenerator.create(this, config.modelPath)
                ?.let { gen ->
                    return ReplyPipeline(generator = gen::generate, style = style,
                        persona = persona(), kb = knowledgeBase(), emoji = UxSettings.emojiEnabled(this))
                }
        }
        if (!GeneratorSettings.isCloudReady(config)) return null
        val generator = CloudLlmGenerator(config.baseUrl, config.apiKey, config.model,
            emoji = UxSettings.emojiEnabled(this)) // T01159
        return ReplyPipeline(generator = generator::generate, style = style,
            persona = persona(), kb = knowledgeBase(), emoji = UxSettings.emojiEnabled(this))
    }

    private fun stubPipeline(style: String): ReplyPipeline =
        ReplyPipeline(generator = StubGenerator::generate, style = style,
            persona = persona(), kb = knowledgeBase(), emoji = UxSettings.emojiEnabled(this))

    /** FR-5.1/5.2：人设与关系存储（单例，加密落库）。 */
    private fun persona(): PersonaStore = PersonaStore.get(this)

    /** RAG 知识库（决策③，单例，加密落库）。 */
    private fun knowledgeBase(): KnowledgeBase = KnowledgeBase.get(this)

    /** T01262 FR-8.8：与当前筛选场景匹配的收藏话术（keyword 空=通用，任何场景均参考）。 */
    private fun favoriteTexts(): List<String> {
        val kw = UxSettings.scenario(this)
        return FavoritesStore.all(this)
            .filter { it.keyword.isEmpty() || it.keyword == kw }
            .map { it.text }
    }

    private fun parse(sbn: StatusBarNotification): EmoNotification? {
        // REQ-020（FR-1.1 二期）：多源接入扩展——企业微信/飞书/QQ/QQ邮箱；
        // 未知来源一律忽略（保守策略：宁漏勿错，避免误读无关 App 通知）
        val source = when (sbn.packageName) {
            PKG_WECHAT -> "wechat"
            PKG_DINGTALK -> "dingtalk"
            PKG_WEWORK -> "wework"
            PKG_LARK -> "lark"
            PKG_QQ -> "qq"
            PKG_QQMAIL -> "qqmail"
            else -> return null
        }

        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val raw = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
            ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()

        // 群聊时微信把正文写成「发送者: 内容」，据此拆出发送者并判定是否群聊
        val m = GROUP_PREFIX.find(raw.orEmpty())
        val sender = m?.groupValues?.get(1) ?: title
        val body = m?.groupValues?.get(2) ?: raw
        val isGroup = m != null

        return EmoNotification(
            source = source,
            timestamp = sbn.postTime,
            body = body,
            sender = sender,
            chat = title ?: sender,
            isGroup = isGroup,
            mentionsMe = isGroup && body?.contains("@") == true,
            title = title,
        )
    }

    companion object {
        private const val TAG = "EmoReply"
        private const val PKG_WECHAT = "com.tencent.mm"
        private const val PKG_DINGTALK = "com.alibaba.android.rimet"
        // REQ-020 二期平台扩展（FR-1.1：企业微信/飞书/QQ/邮件）
        private const val PKG_WEWORK = "com.tencent.wework"
        private const val PKG_LARK = "com.ss.android.lark"
        private const val PKG_QQ = "com.tencent.mobileqq"
        private const val PKG_QQMAIL = "com.tencent.androidqqmail"
        private const val HISTORY_LIMIT = 20
        private val GROUP_PREFIX = Regex("^(.{1,20}?)[:：]\\s*(.+)$", RegexOption.DOT_MATCHES_ALL)
    }
}
