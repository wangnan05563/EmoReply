"""个性化与记忆（REQ 映射 FR-5.1 / FR-5.2）——本地加密存储设施。

范围口径（对照 SRS）：
- FR-5.1 用户人设/偏好：自称、口头禅、禁语、默认风格。多端同步属 P2 二期，
  本设施先落单机版（冲突策略随同步工程再补）。
- FR-5.2 联系人关系与历史摘要：默认本地存储、**加密**；历史来源受限于只读
  捕获，不完整时不臆造——本设施只存「用户主动提供/系统摘要」的内容。

加密设计（与 MTask 端同类方案）：AES-256-GCM，主密钥 = SHA256(机器指纹 ||
可选口令)。敏感字段（人设、关系、历史摘要）以 `iv.tag.ciphertext` 落库，
库文件直接拷走读不出明文。关系默认「陌生」，未标注不影响生成（FR-2.3）。
"""
import json
import os
import sqlite3
from typing import List, Optional

from . import crypto
from .models import Notification

# FR-2.3：关系枚举，未标注默认「陌生」且不影响生成。
# T01348：扩展「朋友/恋人/领导」（与 tone 关系映射、UxSettings.RELATIONS 口径对齐），
# 旧值（家人/好友/同事/客户/陌生）全部保留，旧库数据无需迁移。
RELATIONS = ("家人", "朋友", "好友", "恋人", "同事", "领导", "客户", "陌生")
DEFAULT_RELATION = "陌生"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    name TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    tone_manual INTEGER
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_contact ON history(contact, id);
"""


class PersonaStore:
    """人设 / 联系人关系 / 历史摘要的本地加密存储（单文件 SQLite）。"""

    def __init__(self, db_path: str, passphrase: Optional[str] = None) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._key = crypto.derive_key(passphrase)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """旧库升级：contacts 缺 tone_manual 列（T01348）则补齐。"""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(contacts)")}
        if "tone_manual" not in cols:
            self._conn.execute("ALTER TABLE contacts ADD COLUMN tone_manual INTEGER")

    def close(self) -> None:
        self._conn.close()

    # ---- 加解密原语（共用 crypto 模块）----

    def _encrypt(self, plain: str) -> str:
        return crypto.encrypt(self._key, plain)

    def _decrypt(self, payload: str) -> str:
        return crypto.decrypt(self._key, payload)

    # ---- FR-5.1 人设/偏好 ----

    def set_persona(self, me: Optional[str] = None, catchphrases: Optional[List[str]] = None,
                    banned: Optional[List[str]] = None, default_style: Optional[str] = None) -> None:
        """部分更新人设；None 字段保持原值。整包加密落库。"""
        current = self.get_persona()
        data = {
            "me": me if me is not None else current.get("me", "我"),
            "catchphrases": catchphrases if catchphrases is not None else current.get("catchphrases", []),
            "banned": banned if banned is not None else current.get("banned", []),
            "default_style": default_style if default_style is not None else current.get("default_style", "亲切"),
        }
        self._conn.execute(
            "INSERT INTO persona(id, data) VALUES(1, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (self._encrypt(json.dumps(data, ensure_ascii=False)),),
        )
        self._conn.commit()

    def get_persona(self) -> dict:
        row = self._conn.execute("SELECT data FROM persona WHERE id = 1").fetchone()
        if row is None:
            return {"me": "我", "catchphrases": [], "banned": [], "default_style": "亲切"}
        return json.loads(self._decrypt(row[0]))

    # ---- FR-5.2 联系人关系 ----

    def set_relation(self, name: str, relation: str) -> None:
        if relation not in RELATIONS:
            raise ValueError(f"非法关系枚举：{relation}（允许：{RELATIONS}）")
        self._conn.execute(
            "INSERT INTO contacts(name, relation) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET relation=excluded.relation",
            (name, relation),
        )
        self._conn.commit()

    def get_relation(self, name: Optional[str]) -> str:
        """未标注 → 默认「陌生」且不影响生成（FR-2.3 验收）。"""
        if not name:
            return DEFAULT_RELATION
        row = self._conn.execute("SELECT relation FROM contacts WHERE name = ?", (name,)).fetchone()
        return row[0] if row else DEFAULT_RELATION

    # ---- T01348：联系人级语气手动覆盖（AUTO 档时优先于关系映射）----

    def set_contact_tone(self, name: str, tone: Optional[int]) -> None:
        """记录/清除该联系人的手动语气值（None=清除，回到关系映射）。

        传入值即刻收敛到 [0,100]；联系人不存在时仅记录 tone（relation 保持默认）。
        """
        from .tone import clamp_tone
        v = clamp_tone(tone) if tone is not None else None
        self._conn.execute(
            "INSERT INTO contacts(name, relation, tone_manual) VALUES(?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET tone_manual=excluded.tone_manual",
            (name, self.get_relation(name), v),
        )
        self._conn.commit()

    def contact_tone(self, name: Optional[str]) -> Optional[int]:
        """该联系人的手动语气值；未设置/未知名 → None。"""
        if not name:
            return None
        row = self._conn.execute(
            "SELECT tone_manual FROM contacts WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    # ---- FR-5.2 历史摘要（加密存储） ----

    def add_history(self, contact: str, summary: str) -> None:
        import time
        if not summary.strip():
            return
        self._conn.execute(
            "INSERT INTO history(contact, summary, created_at) VALUES(?, ?, ?)",
            (contact, self._encrypt(summary.strip()), time.time()),
        )
        self._conn.commit()

    def history(self, contact: str, limit: int = 5) -> List[str]:
        rows = self._conn.execute(
            "SELECT summary FROM history WHERE contact = ? ORDER BY id DESC LIMIT ?",
            (contact, limit),
        ).fetchall()
        return [self._decrypt(r[0]) for r in rows]

    # ---- 生成上下文聚合 ----

    def prompt_context(self, contact: Optional[str]) -> dict:
        """给 compose/pipeline 用的一次性上下文：自称 / 关系 / 默认风格 / 禁语。"""
        persona = self.get_persona()
        return {
            "me": persona.get("me", "我"),
            "relation": self.get_relation(contact),
            "style": persona.get("default_style", "亲切"),
            "banned": persona.get("banned", []),
        }

    @staticmethod
    def violates_banned(text: str, banned: List[str]) -> bool:
        """输出侧禁语过滤（FR-5.1）：候选含禁语 → 拦下，不注入。"""
        return any(w and w in text for w in banned)

    # ---- FR-6.3 联动：一键清除 ----

    def wipe(self) -> None:
        self._conn.executescript("DELETE FROM persona; DELETE FROM contacts; DELETE FROM history;")
        self._conn.commit()
