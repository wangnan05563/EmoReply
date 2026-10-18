package com.emoreply.poc.store

import android.content.Context
import org.json.JSONArray
import java.io.File

/**
 * 回复模板库（T01082 步骤 6「另存为模板」）。
 *
 * 存储性质：模板是用户**主动保存**的回复文本（非被动采集），按最小收集原则
 * 只存文本本体与保存时间，普通 JSON 文件明文落盘、随「一键清除」（FR-6.3）删除。
 * 上限 20 条，超出按最旧淘汰。
 */
object TemplateStore {

    private const val FILE = "templates.json"
    private const val LIMIT = 20

    data class Template(val id: Long, val text: String, val savedAt: Long)

    @Volatile
    private var cached: List<Template>? = null

    private fun file(context: Context): File = File(context.filesDir, FILE)

    fun all(context: Context): List<Template> {
        cached?.let { return it }
        val list = runCatching {
            val arr = JSONArray(file(context).takeIf { it.exists() }?.readText() ?: "[]")
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Template(o.getLong("id"), o.getString("text"), o.getLong("savedAt"))
            }
        }.getOrDefault(emptyList())
        cached = list
        return list
    }

    /** 保存模板（去重：内容相同不重复存）；返回是否新增。 */
    fun save(context: Context, text: String): Boolean {
        val t = text.trim()
        if (t.isEmpty()) return false
        val current = all(context).toMutableList()
        if (current.any { it.text == t }) return false
        current.add(0, Template(System.currentTimeMillis(), t, System.currentTimeMillis()))
        while (current.size > LIMIT) current.removeAt(current.size - 1)
        persist(context, current)
        return true
    }

    fun remove(context: Context, id: Long) {
        val current = all(context).filter { it.id != id }
        persist(context, current)
    }

    fun clear(context: Context) {
        cached = emptyList()
        if (!file(context).delete()) android.util.Log.w("EmoReplyStore", "templates: 缓存文件删除失败")
    }

    private fun persist(context: Context, list: List<Template>) {
        val arr = JSONArray()
        list.forEach { t ->
            arr.put(org.json.JSONObject().apply {
                put("id", t.id)
                put("text", t.text)
                put("savedAt", t.savedAt)
            })
        }
        file(context).writeText(arr.toString())
        cached = list
    }
}
