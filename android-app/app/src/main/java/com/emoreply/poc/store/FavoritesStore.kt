package com.emoreply.poc.store

import android.content.Context
import com.emoreply.poc.util.Crypto
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * T01262 FR-8.8：话术收藏夹（本地 AES-256-GCM 加密存储）。
 *
 * 候选长按「收藏」保存「话术 + 场景关键词」；生成时把同类场景的收藏话术注入 prompt
 * 作风格/角度参考（见 Compose.buildPrompt 的 favorites 参数）。与 TemplateStore 的差异：
 * 收藏话术要参与「同类场景优先」语义，故存关键词供场景匹配；且按 FR-8.8 要求整包加密
 * （Crypto 设备绑定密钥），拷贝文件读不出明文。取消收藏即时生效——存储按需重读，
 * 下次生成即不再注入。上限 50 条，超出按最旧淘汰。
 *
 * ⚠ 本环境无真机，收藏→生成注入的端到端链路未实测，仅编译通过 + 逻辑自洽。
 */
object FavoritesStore {

    private const val FILE = "favorites.dat"
    private const val LIMIT = 50

    data class Favorite(val text: String, val keyword: String, val savedAt: Long)

    @Volatile
    private var cached: List<Favorite>? = null

    private fun file(context: Context): File = File(context.filesDir, FILE)

    fun all(context: Context): List<Favorite> {
        cached?.let { return it }
        val key = Crypto.masterKey(context)
        val list = runCatching {
            val raw = file(context).takeIf { it.exists() }?.readText().orEmpty()
            if (raw.isEmpty()) return@runCatching emptyList<Favorite>()
            val arr = JSONArray(Crypto.decrypt(key, raw))
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Favorite(o.getString("text"), o.optString("keyword", ""), o.getLong("savedAt"))
            }
        }.getOrDefault(emptyList()) // 解密失败按空处理：不崩溃，收藏夹从零开始
        cached = list
        return list
    }

    /** 收藏（去重：文本相同不重复存）；返回是否新增。keyword=收藏时的筛选场景（空=通用）。 */
    fun save(context: Context, text: String, keyword: String): Boolean {
        val t = text.trim()
        if (t.isEmpty()) return false
        val current = all(context).toMutableList()
        if (current.any { it.text == t }) return false
        current.add(0, Favorite(t, keyword.trim(), System.currentTimeMillis()))
        while (current.size > LIMIT) current.removeAt(current.size - 1)
        persist(context, current)
        return true
    }

    /** 取消收藏（按文本精确匹配）；返回是否确有删除。 */
    fun remove(context: Context, text: String): Boolean {
        val t = text.trim()
        val current = all(context).filter { it.text != t }
        if (current.size == all(context).size) return false
        persist(context, current)
        return true
    }

    fun contains(context: Context, text: String): Boolean = all(context).any { it.text == text.trim() }

    fun clear(context: Context) {
        cached = emptyList()
        if (!file(context).delete()) android.util.Log.w("EmoReplyStore", "favorites: 缓存文件删除失败")
    }

    private fun persist(context: Context, list: List<Favorite>) {
        val arr = JSONArray()
        list.forEach { f ->
            arr.put(JSONObject().apply {
                put("text", f.text)
                put("keyword", f.keyword)
                put("savedAt", f.savedAt)
            })
        }
        file(context).writeText(Crypto.encrypt(Crypto.masterKey(context), arr.toString()))
        cached = list
    }
}
