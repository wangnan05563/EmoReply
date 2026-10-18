"""RAG 人设知识库（决策③ / REQ-020）验收测试。

覆盖：加密静态不可读与往返、分类校验、词面检索相关性（bigram）与同联系人
加权、top_k、pipeline 注入与无命中不注入、EmbeddingRetriever 契约（离线模拟）。
"""
import json
import sqlite3  # noqa: F401  (仅 tmp 路径习惯对齐)

import pytest

from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.rag import (CATEGORY_PERSONA, CATEGORY_PLATFORM, CATEGORY_RELATION,
                          EmbeddingRetriever, KnowledgeBase, LexicalRetriever)


@pytest.fixture()
def kb(tmp_path):
    k = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="pw")
    yield k
    # 文件加密校验后自动清理由 tmp_path 承担


def notif(body, sender="对方", timestamp=1000):
    return Notification(source="wechat", timestamp=timestamp, body=body, sender=sender, chat=sender)


def seeded_kb(tmp_path):
    k = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="pw")
    k.add(CATEGORY_PLATFORM, "给老板发生日祝福，语气正式一点，别开过分玩笑")
    k.add(CATEGORY_PLATFORM, "微信里给长辈发长语音前，先发文字问一句方便吗")
    k.add(CATEGORY_PERSONA, "我养了两只猫，叫团子和年糕")
    k.add(CATEGORY_RELATION, "周报口径以部门模板为准，周五下班前发", contact="老板")
    return k


# ---- 存储层 ----

def test_kb_encrypted_at_rest(tmp_path):
    k = seeded_kb(tmp_path)
    raw = open(str(tmp_path / "kb.enc"), encoding="utf-8").read()
    assert "团子" not in raw and "周报" not in raw
    assert len(k) == 4


def test_kb_roundtrip_and_wrong_passphrase(tmp_path):
    seeded_kb(tmp_path)
    k2 = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="pw")
    assert len(k2) == 4
    k3 = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="bad")
    assert len(k3) == 0  # 口令不匹配 → 初始态，不崩溃


def test_kb_add_validation_and_remove(tmp_path):
    k = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="pw")
    with pytest.raises(ValueError):
        k.add("八卦", "不在枚举内")
    with pytest.raises(ValueError):
        k.add(CATEGORY_PERSONA, "   ")
    eid = k.add(CATEGORY_PERSONA, "第一条")
    assert k.remove(eid) is True
    assert k.remove(eid) is False
    assert len(k) == 0


def test_kb_relation_contact_only_for_relation(tmp_path):
    k = KnowledgeBase(str(tmp_path / "kb.enc"), passphrase="pw")
    k.add(CATEGORY_PERSONA, "个人事实不该绑联系人", contact="老板")
    assert k.entries()[0].contact is None  # 非 relation 类强制忽略 contact
    k.add(CATEGORY_RELATION, "老板专属话术", contact="老板")
    assert k.entries()[1].contact == "老板"


# ---- 词面检索 ----

def test_lexical_search_relevance(tmp_path):
    k = seeded_kb(tmp_path)
    hits = LexicalRetriever().search(k, "明天是我老板的生日，怎么回祝福合适", contact="老板")
    texts = [e.text for e in hits]
    # 相关性排序：bigram 高重叠的平台礼仪与同联系人话术应排在前两位
    assert "生日祝福" in texts[0]
    assert "周报" in texts[1]
    assert all("猫" not in texts[i] and "语音" not in texts[i] for i in (0, 1))


def test_lexical_search_no_match_returns_empty(tmp_path):
    k = seeded_kb(tmp_path)
    assert LexicalRetriever().search(k, "完全无关的查询词", contact="路人") == []


def test_lexical_search_category_filter_and_topk(tmp_path):
    k = seeded_kb(tmp_path)
    hits = LexicalRetriever().search(k, "语音", category=CATEGORY_PLATFORM, top_k=1)
    assert len(hits) == 1 and "语音" in hits[0].text


def test_lexical_same_contact_only_when_no_overlap(tmp_path):
    k = seeded_kb(tmp_path)
    hits = LexicalRetriever().search(k, "", contact="老板")  # 空查询仅剩联系人加权
    assert [e.text for e in hits] == ["周报口径以部门模板为准，周五下班前发"]


# ---- pipeline 注入 ----

def test_pipeline_injects_knowledge(tmp_path):
    k = seeded_kb(tmp_path)
    seen = {}

    def gen(prompt):
        seen["system"] = prompt.system
        return ["好的"]

    pipeline = ReplyPipeline(generator=gen, kb=k)
    pipeline.handle(notif("老板，明天他生日我要发什么？", sender="老板"), [])
    assert "参考资料" in seen["system"]
    assert "周报口径" in seen["system"]


def test_pipeline_no_hit_unchanged(tmp_path):
    k = seeded_kb(tmp_path)
    seen = {}
    pipeline = ReplyPipeline(generator=lambda p: seen.update(system=p.system) or ["好的"], kb=k)
    pipeline.handle(notif("完全无关的消息"), [])
    assert "参考资料" not in seen["system"]


def test_pipeline_without_kb_unchanged():
    seen = {}
    ReplyPipeline(generator=lambda p: seen.update(system=p.system) or ["好的"]).handle(notif("在吗"), [])
    assert "参考资料" not in seen["system"]


# ---- EmbeddingRetriever 契约（离线模拟 HTTP）----

def test_embedding_retriever_contract(tmp_path, monkeypatch):
    k = seeded_kb(tmp_path)
    r = EmbeddingRetriever("https://api.example.com/v1", "key")

    calls = {}

    def fake_embed(texts):
        calls["texts"] = list(texts)
        # 人造向量：查询与「周报」条目同向，其余正交
        def vec(t):
            if "周报" in t or "老板，明天" in t:
                return [1.0, 0.0]
            return [0.0, 1.0]
        return [vec(t) for t in texts]

    monkeypatch.setattr(r, "_embed", fake_embed)
    hits = r.search(k, "老板，明天怎么回他", contact="老板")
    assert calls["texts"][0].startswith("老板，明天")
    assert len(hits) >= 1
    assert "周报" in hits[0].text


def test_embedding_retriever_network_error(tmp_path, monkeypatch):
    k = seeded_kb(tmp_path)
    r = EmbeddingRetriever("https://api.example.com/v1", "key")

    def boom(texts):
        raise r.RetrievalError("embedding 调用失败")

    monkeypatch.setattr(r, "_embed", boom)
    with pytest.raises(r.RetrievalError):
        r.search(k, "任意查询", contact="老板")
