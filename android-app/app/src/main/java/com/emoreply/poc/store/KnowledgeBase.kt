package com.emoreply.poc.store

import android.content.Context
import com.emoreply.poc.util.Crypto
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom

/**
 * RAG 知识库（SRS 决策③ 2026-09-26 拍板：生成层补 RAG）。
 *
 * 与 m1-poc `emoreply/rag.py` 同构：三类条目（persona 个人事实 / relation 联系人话术 /
 * platform 平台礼仪），AES-256-GCM 加密单 JSON 文件落库（复用 [Crypto]）。
 * 检索为 [LexicalRetriever]（bigram 词面版，零依赖可离线）；语义版（/embeddings）留待云侧。
 */
class KnowledgeBase private constructor(context: Context, passphrase: String?) {

    data class Entry(
        val category: String,
        val text: String,
        val contact: String? = null,
        val id: String = newId(),
        val createdAt: Long = System.currentTimeMillis(),
    )

    private val key: ByteArray = Crypto.masterKey(context, passphrase)
    private val file: File = File(context.filesDir, "kb.enc")
    private val entries = mutableListOf<Entry>()

    init {
        if (file.exists()) {
            runCatching {
                val json = Crypto.decrypt(key, file.readText())
                val arr = JSONObject(json).optJSONArray("entries") ?: JSONArray()
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    entries.add(
                        Entry(
                            category = o.getString("category"),
                            text = o.getString("text"),
                            contact = if (o.isNull("contact")) null else o.getString("contact"),
                            id = o.getString("id"),
                            createdAt = o.optLong("createdAt", 0L),
                        )
                    )
                }
            }
            // 解密失败（口令不匹配/损坏）→ 初始态，不崩溃（与 PersonaStore 口径一致）
        }
    }

    @Synchronized
    private fun persist() {
        val arr = JSONArray()
        entries.forEach { e ->
            arr.put(JSONObject()
                .put("category", e.category)
                .put("text", e.text)
                .put("contact", e.contact ?: JSONObject.NULL)
                .put("id", e.id)
                .put("createdAt", e.createdAt))
        }
        file.writeText(Crypto.encrypt(key, JSONObject().put("entries", arr).toString()))
    }

    /** relation 类可绑联系人；其余强制忽略 contact（与 m1-poc 一致）。 */
    @Synchronized
    fun add(category: String, text: String, contact: String? = null): String {
        require(category in CATEGORIES) { "非法知识分类：$category" }
        val trimmed = text.trim()
        require(trimmed.isNotEmpty()) { "知识条目内容不能为空" }
        val entry = Entry(
            category = category,
            text = trimmed,
            contact = if (category == CATEGORY_RELATION) contact?.trim()?.ifEmpty { null } else null,
        )
        entries.add(entry)
        persist()
        return entry.id
    }

    @Synchronized
    fun remove(entryId: String): Boolean {
        val before = entries.size
        entries.removeAll { it.id == entryId }
        val changed = entries.size != before
        if (changed) persist()
        return changed
    }

    fun all(): List<Entry> = entries.toList()

    fun size(): Int = entries.size

    /** FR-6.3 联动：一键清除。 */
    @Synchronized
    fun wipe() {
        entries.clear()
        persist()
    }

    companion object {
        const val CATEGORY_PERSONA = "persona"
        const val CATEGORY_RELATION = "relation"
        const val CATEGORY_PLATFORM = "platform"
        val CATEGORIES = listOf(CATEGORY_PERSONA, CATEGORY_RELATION, CATEGORY_PLATFORM)
        val CATEGORY_LABELS = listOf("个人事实", "关系话术", "平台礼仪")

        private const val ID_LEN = 12

        fun newId(): String {
            val bytes = ByteArray(ID_LEN / 2)
            SecureRandom().nextBytes(bytes)
            return bytes.joinToString("") { "%02x".format(it) }
        }

        @Volatile
        private var instance: KnowledgeBase? = null

        fun get(context: Context, passphrase: String? = null): KnowledgeBase =
            instance ?: synchronized(this) {
                instance ?: KnowledgeBase(context.applicationContext, passphrase).also { instance = it }
            }
    }
}

/**
 * 词面检索（默认）：CJK bigram 重叠率 + 同联系人加权 0.15（次级信号）。
 * 无重叠且非当前联系人 → 不返回（防噪声注入）。与 m1-poc LexicalRetriever 同构。
 */
object LexicalRetriever {

    const val CONTACT_BOOST = 0.15
    private const val TOP_K = 3

    fun search(kb: KnowledgeBase, query: String?, contact: String?,
               category: String? = null, topK: Int = TOP_K): List<KnowledgeBase.Entry> {
        val q = bigrams(query ?: "")
        val scored = mutableListOf<Pair<Double, KnowledgeBase.Entry>>()
        for (e in kb.all()) {
            if (category != null && e.category != category) continue
            val sameContact = !e.contact.isNullOrBlank() && !contact.isNullOrBlank() && e.contact == contact
            val overlap = if (q.isEmpty()) 0.0 else q.intersect(bigrams(e.text)).size.toDouble() / q.size
            val score = overlap + (CONTACT_BOOST.takeIf { sameContact } ?: 0.0)
            if (score <= 0.0) continue
            scored.add(score to e)
        }
        return scored.sortedByDescending { it.first }.take(topK).map { it.second }
    }

    /** CJK 友好 bigram 集合：过滤标点/空白，保留单字（短查询兜底）。 */
    fun bigrams(text: String): Set<String> {
        val cleaned = text.filter { it.isLetterOrDigit() || it.code in 0x3400..0x9fff }
        if (cleaned.length < 2) return cleaned.map { it.toString() }.toSet()
        return cleaned.windowed(2).toSet() + cleaned.map { it.toString() }.toSet()
    }
}
