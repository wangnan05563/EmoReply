"""RAG 人设知识库（SRS 决策③ 2026-09-26 拍板：生成层补 RAG）。

知识条目三类（对齐决策③：用户人设库 / 关系话术 / 平台礼仪向量库）：
- persona：用户个人事实与偏好（如「我养了两只猫」「回避薪资话题」）
- relation：按联系人的关系话术（如「老板：周报口径以部门模板为准」）
- platform：平台礼仪（如「微信长语音前先发文字确认」）

检索双实现：
- LexicalRetriever（默认，零依赖可离线）：CJK bigram 重叠打分；同联系人条目
  恒定加权（关系话术对当前会话天然相关）。
- EmbeddingRetriever（可选，语义版）：OpenAI 兼容 /embeddings + 余弦相似度，
  需网络与密钥；接口与词面版一致，调用方可无感切换。

注入：top-k 条目经 compose 并入 system 提示词「参考资料」，声明不得据此编造。
存储：AES-256-GCM 加密单文件 JSON（与人设库同型，密钥=SHA256(机器指纹||口令)）。
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from urllib import request as urlrequest

from . import crypto

CATEGORY_PERSONA = "persona"
CATEGORY_RELATION = "relation"
CATEGORY_PLATFORM = "platform"
CATEGORIES = (CATEGORY_PERSONA, CATEGORY_RELATION, CATEGORY_PLATFORM)


@dataclass
class KnowledgeEntry:
    category: str
    text: str
    contact: Optional[str] = None   # relation 类可绑定联系人；其余为 None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)


class KnowledgeBase:
    """加密知识库（单文件 JSON，条目增删查）。"""

    def __init__(self, db_path: str, passphrase: Optional[str] = None) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._key = crypto.derive_key(passphrase)
        self._entries: List[KnowledgeEntry] = []
        if os.path.exists(db_path):
            try:
                payload = crypto.decrypt(self._key, open(db_path, encoding="utf-8").read())
                self._entries = [KnowledgeEntry(**e) for e in json.loads(payload).get("entries", [])]
            except (ValueError, TypeError):  # json.JSONDecodeError 是 ValueError 子类
                self._entries = []  # 口令不匹配/损坏 → 初始态，不崩溃（与 PersonaStore 口径一致）

    def _persist(self) -> None:
        payload = json.dumps({"entries": [e.__dict__ for e in self._entries]}, ensure_ascii=False)
        with open(self.db_path, "w", encoding="utf-8") as f:
            f.write(crypto.encrypt(self._key, payload))

    def add(self, category: str, text: str, contact: Optional[str] = None) -> str:
        if category not in CATEGORIES:
            raise ValueError(f"非法知识分类：{category}（允许：{CATEGORIES}）")
        text = (text or "").strip()
        if not text:
            raise ValueError("知识条目内容不能为空")
        entry = KnowledgeEntry(category=category, text=text,
                               contact=(contact or None) if category == CATEGORY_RELATION else None)
        self._entries.append(entry)
        self._persist()
        return entry.id

    def remove(self, entry_id: str) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.id != entry_id]
        changed = len(self._entries) != before
        if changed:
            self._persist()
        return changed

    def entries(self) -> List[KnowledgeEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def wipe(self) -> None:
        """FR-6.3 联动：一键清除。"""
        self._entries = []
        self._persist()


def _bigrams(text: str) -> set:
    """CJK 友好的 bigram 集合：过滤标点/空白，单字也保留（短查询兜底）。"""
    cleaned = [ch for ch in text if ch.isalnum() or "\u3400" <= ch <= "\u9fff"]
    if len(cleaned) < 2:
        return set(cleaned)
    return {"".join(cleaned[i:i + 2]) for i in range(len(cleaned) - 1)} | set(cleaned)


class LexicalRetriever:
    """词面检索（默认）：bigram 重叠率 + 同联系人加权，零依赖可离线。"""

    CONTACT_BOOST = 0.15

    def search(self, kb: KnowledgeBase, query: str, contact: Optional[str] = None,
               category: Optional[str] = None, top_k: int = 3) -> List[KnowledgeEntry]:
        q = _bigrams(query or "")
        scored: List[tuple] = []
        for e in kb.entries():
            if category and e.category != category:
                continue
            same_contact = bool(e.contact and contact and e.contact == contact)
            overlap = len(q & _bigrams(e.text)) / max(1, len(q)) if q else 0.0
            score = overlap + (self.CONTACT_BOOST if same_contact else 0.0)
            if score <= 0:
                continue  # 无重叠且非当前联系人 → 不注入，避免噪声
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]


class EmbeddingRetriever:
    """语义检索（可选）：OpenAI 兼容 /embeddings + 余弦相似度。

    与 CloudLlmGenerator 同款配置口径（baseUrl/apiKey）；查询与条目各向量化一次
    后内存比对。网络失败抛 RetrievalError，由调用方降级（回落词面检索或不注入）。
    """

    def __init__(self, base_url: str, api_key: str, model: str = "text-embedding-3-small",
                 timeout: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    class RetrievalError(Exception):
        pass

    def _embed(self, texts: List[str]) -> List[List[float]]:
        endpoint = self.base_url + "/embeddings"
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urlrequest.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {self.api_key}"})
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            vectors = [item["embedding"] for item in sorted(obj["data"], key=lambda x: x["index"])]
            if len(vectors) != len(texts):
                raise self.RetrievalError("embedding 数量不匹配")
            return vectors
        except self.RetrievalError:
            raise
        except Exception as e:
            raise self.RetrievalError(f"embedding 调用失败：{e}") from e

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def search(self, kb: KnowledgeBase, query: str, contact: Optional[str] = None,
               category: Optional[str] = None, top_k: int = 3) -> List[KnowledgeEntry]:
        pool = [e for e in kb.entries() if not category or e.category == category]
        if not pool or not (query or "").strip():
            return [e for e in pool if e.contact and contact and e.contact == contact][:top_k]
        vectors = self._embed([query] + [e.text for e in pool])
        q = vectors[0]
        scored = []
        for e, v in zip(pool, vectors[1:]):
            score = self._cosine(q, v)
            if e.contact and contact and e.contact == contact:
                score += 0.15
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for s, e in scored[:top_k] if s > 0.05]
