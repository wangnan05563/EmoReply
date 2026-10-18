package com.emoreply.poc.store

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.emoreply.poc.util.Crypto
import org.json.JSONArray
import org.json.JSONObject

/**
 * 人设 / 联系人关系 / 历史摘要的本地加密存储（FR-5.1 / FR-5.2）。
 *
 * 与 m1-poc `emoreply/persona.py` 同构：SQLite 三表（persona/contacts/history），
 * 敏感字段 AES-256-GCM 加密落库（见 [Crypto]），库文件拷走读不出明文。
 * 关系默认「陌生」，未标注不影响生成（FR-2.3）。
 */
class PersonaStore private constructor(context: Context, passphrase: String?) :
    SQLiteOpenHelper(context, DB_NAME, null, DB_VERSION) {

    private val key: ByteArray = Crypto.masterKey(context, passphrase)

    class Persona(
        val me: String = "我",
        val catchphrases: List<String> = emptyList(),
        val banned: List<String> = emptyList(),
        val defaultStyle: String = "亲切",
    )

    class PromptContext(
        val me: String,
        val relation: String,
        val style: String,
        val banned: List<String>,
        // T01259 FR-8.5：上次聊到（最近一条历史摘要；无历史为空串=不注入）
        val lastTopic: String = "",
    )

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS persona (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)")
        db.execSQL("CREATE TABLE IF NOT EXISTS contacts (name TEXT PRIMARY KEY, relation TEXT NOT NULL, tone_manual INTEGER)")
        db.execSQL("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, contact TEXT NOT NULL, summary TEXT NOT NULL, created_at REAL NOT NULL)")
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_history_contact ON history(contact, id)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // T01348：v2 contacts 加 tone_manual（联系人级语气手动覆盖，与 m1-poc 同构）
        if (oldVersion < 2) {
            db.execSQL("ALTER TABLE contacts ADD COLUMN tone_manual INTEGER")
        }
    }

    // ---- FR-5.1 人设/偏好（整包加密，部分更新） ----

    @Synchronized
    fun setPersona(me: String? = null, catchphrases: List<String>? = null,
                   banned: List<String>? = null, defaultStyle: String? = null) {
        val cur = getPersona()
        val next = Persona(
            me = me ?: cur.me,
            catchphrases = catchphrases ?: cur.catchphrases,
            banned = banned ?: cur.banned,
            defaultStyle = defaultStyle ?: cur.defaultStyle,
        )
        val json = JSONObject()
            .put("me", next.me)
            .put("catchphrases", JSONArray(next.catchphrases))
            .put("banned", JSONArray(next.banned))
            .put("default_style", next.defaultStyle)
            .toString()
        writableDatabase.execSQL(
            "INSERT INTO persona(id, data) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            arrayOf(Crypto.encrypt(key, json)),
        )
    }

    fun getPersona(): Persona {
        readableDatabase.rawQuery("SELECT data FROM persona WHERE id = 1", null).use { c ->
            if (!c.moveToFirst()) return Persona()
            return try {
                val o = JSONObject(Crypto.decrypt(key, c.getString(0)))
                Persona(
                    me = o.optString("me", "我"),
                    catchphrases = o.optJSONArray("catchphrases")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList(),
                    banned = o.optJSONArray("banned")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList(),
                    defaultStyle = o.optString("default_style", "亲切"),
                )
            } catch (e: Exception) {
                Persona() // 解密失败按初始态处理：不崩溃（密钥随 ANDROID_ID，正常不会发生）
            }
        }
    }

    // ---- FR-5.2 联系人关系 ----

    @Synchronized
    fun setRelation(name: String, relation: String) {
        require(relation in RELATIONS) { "非法关系枚举：$relation" }
        writableDatabase.execSQL(
            "INSERT INTO contacts(name, relation) VALUES(?, ?) ON CONFLICT(name) DO UPDATE SET relation=excluded.relation",
            arrayOf(name, relation),
        )
    }

    fun getRelation(name: String?): String {
        if (name.isNullOrBlank()) return DEFAULT_RELATION
        readableDatabase.rawQuery("SELECT relation FROM contacts WHERE name = ?", arrayOf(name)).use { c ->
            return if (c.moveToFirst()) c.getString(0) else DEFAULT_RELATION
        }
    }

    // ---- T01348：联系人级语气手动覆盖（AUTO 档时优先于关系映射）----

    /** 记录/清除该联系人的手动语气值（null=清除，回到关系映射）。 */
    @Synchronized
    fun setContactTone(name: String, tone: Int?) {
        val v = tone?.let { com.emoreply.poc.core.Tone.clampTone(it) }
        writableDatabase.execSQL(
            "INSERT INTO contacts(name, relation, tone_manual) VALUES(?, ?, ?) " +
                "ON CONFLICT(name) DO UPDATE SET tone_manual=excluded.tone_manual",
            arrayOf(name, getRelation(name), v),
        )
    }

    /** 该联系人的手动语气值；未设置/未知名 → null。 */
    fun contactTone(name: String?): Int? {
        if (name.isNullOrBlank()) return null
        readableDatabase.rawQuery(
            "SELECT tone_manual FROM contacts WHERE name = ?", arrayOf(name),
        ).use { c ->
            if (!c.moveToFirst()) return null
            return if (c.isNull(0)) null else c.getInt(0)
        }
    }

    // ---- FR-5.2 历史摘要（加密存储） ----

    @Synchronized
    fun addHistory(contact: String, summary: String) {
        if (summary.isBlank()) return
        writableDatabase.execSQL(
            "INSERT INTO history(contact, summary, created_at) VALUES(?, ?, ?)",
            arrayOf(contact, Crypto.encrypt(key, summary.trim()), System.currentTimeMillis().toDouble()),
        )
    }

    fun history(contact: String, limit: Int = 5): List<String> {
        val out = mutableListOf<String>()
        readableDatabase.rawQuery(
            "SELECT summary FROM history WHERE contact = ? ORDER BY id DESC LIMIT ?",
            arrayOf(contact, limit.toString()),
        ).use { c ->
            while (c.moveToNext()) {
                runCatching { out.add(Crypto.decrypt(key, c.getString(0))) }
            }
        }
        return out
    }

    // ---- 聚合与过滤 ----

    fun promptContext(contact: String?): PromptContext {
        val p = getPersona()
        return PromptContext(
            me = p.me,
            relation = getRelation(contact),
            style = p.defaultStyle,
            banned = p.banned,
            // T01259 FR-8.5：最近一条历史摘要作「上次聊到」（history 按 id DESC，最新在前）
            lastTopic = history(contact ?: "", limit = 1).firstOrNull().orEmpty(),
        )
    }

    fun violatesBanned(text: String, banned: List<String>): Boolean = banned.any { it.isNotEmpty() && it in text }

    // ---- FR-6.3 联动 ----

    @Synchronized
    fun wipe() {
        writableDatabase.execSQL("DELETE FROM persona")
        writableDatabase.execSQL("DELETE FROM contacts")
        writableDatabase.execSQL("DELETE FROM history")
    }

    companion object {
        // T01348：扩展「朋友/恋人/领导」（与 tone 关系映射、UxSettings.RELATIONS 口径对齐），
        // 旧值（家人/好友/同事/客户/陌生）全部保留，旧库数据无需迁移
        const val RELATIONS = "家人,朋友,好友,恋人,同事,领导,客户,陌生"
        const val DEFAULT_RELATION = "陌生"
        private const val DB_NAME = "persona.db"
        private const val DB_VERSION = 2

        @Volatile
        private var instance: PersonaStore? = null

        fun get(context: Context, passphrase: String? = null): PersonaStore =
            instance ?: synchronized(this) {
                instance ?: PersonaStore(context.applicationContext, passphrase).also { instance = it }
            }
    }
}
