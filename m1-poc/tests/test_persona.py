"""FR-5.1 / FR-5.2（关系记忆设施）验收测试。

加密存储的正确性与「静态不可读」、关系默认「陌生」不影响生成、
人设（自称/禁语/默认风格）与 pipeline 的集成、一键清除。
"""
import sqlite3

import pytest

from emoreply.models import Notification
from emoreply.persona import PersonaStore
from emoreply.pipeline import ReplyPipeline


@pytest.fixture()
def store(tmp_path):
    s = PersonaStore(str(tmp_path / "persona.db"), passphrase="test-passphrase")
    yield s
    s.close()


def _notif(body, sender="老板", **kw):
    kw.setdefault("source", "wechat")
    kw.setdefault("timestamp", 1000)
    return Notification(body=body, sender=sender, **kw)


def _stub(replies):
    return lambda prompt: list(replies)


# ---- 加密存储：静态不可读 ----

def test_persona_encrypted_at_rest(store, tmp_path):
    store.set_persona(me="本小姐", catchphrases=["绝了"], banned=["随便"], default_style="幽默")
    raw = sqlite3.connect(str(tmp_path / "persona.db")).execute("SELECT data FROM persona").fetchone()[0]
    assert "本小姐" not in raw and "绝了" not in raw


def test_history_encrypted_at_rest(store, tmp_path):
    store.add_history("老板", "老板周五要季度汇报，勿扰")
    raw = sqlite3.connect(str(tmp_path / "persona.db")).execute(
        "SELECT summary FROM history").fetchone()[0]
    assert "季度汇报" not in raw


def test_roundtrip_and_isolation(tmp_path):
    a = PersonaStore(str(tmp_path / "a.db"), passphrase="k1")
    b = PersonaStore(str(tmp_path / "b.db"), passphrase="k2")
    a.set_persona(me="小王")
    assert a.get_persona()["me"] == "小王"
    b.set_persona(me="小张")
    assert b.get_persona()["me"] == "小张"
    assert a.get_persona()["me"] == "小王"  # 不同口令互不可见
    a.close(); b.close()


def test_wrong_passphrase_fails(tmp_path):
    s1 = PersonaStore(str(tmp_path / "p.db"), passphrase="right")
    s1.set_persona(me="秘密自称")
    s1.close()
    s2 = PersonaStore(str(tmp_path / "p.db"), passphrase="wrong")
    with pytest.raises(ValueError, match="解密失败"):
        s2.get_persona()
    s2.close()


# ---- 关系：默认「陌生」且不影响生成 ----

def test_relation_default_and_enum(store):
    assert store.get_relation("陌生人甲") == "陌生"
    assert store.get_relation(None) == "陌生"
    store.set_relation("老板", "同事")
    assert store.get_relation("老板") == "同事"
    with pytest.raises(ValueError):
        store.set_relation("老板", "死敌")


def test_history_order_and_limit(store):
    for i in range(7):
        store.add_history("张三", f"摘要{i}")
    got = store.history("张三", limit=5)
    assert got == [f"摘要{i}" for i in range(6, 1, -1)]  # 最新在前


def test_add_history_ignores_blank(store):
    store.add_history("李四", "   ")
    assert store.history("李四") == []


# ---- pipeline 集成 ----

def test_pipeline_injects_persona_and_relation(store):
    store.set_persona(me="本小姐", default_style="幽默")
    store.set_relation("老板", "同事")
    seen = {}

    def gen(prompt):
        seen["system"] = prompt.system
        seen["style"] = prompt.style
        return ["好的，马上处理"]

    pipeline = ReplyPipeline(generator=gen, persona=store)
    outcome = pipeline.handle(_notif("这周报表发我一下"), [])
    assert outcome.candidates[0].style == "幽默"      # 人设默认风格生效
    assert seen["style"] == "幽默"
    assert "本小姐" in seen["system"]                  # 自称注入
    assert "对方关系：同事" in seen["system"]           # 关系注入


def test_pipeline_explicit_style_overrides_persona(store):
    store.set_persona(default_style="幽默")
    seen = {}

    def gen(prompt):
        seen["style"] = prompt.style
        return ["ok"]

    pipeline = ReplyPipeline(generator=gen, persona=store, style="正式")
    outcome = pipeline.handle(_notif("在吗"), [])
    assert outcome.candidates[0].style == "正式"
    assert seen["style"] == "正式"  # 显式 style 优先于人设默认


def test_pipeline_filters_banned_candidates(store):
    store.set_persona(banned=["随便"])
    pipeline = ReplyPipeline(generator=lambda p: ["随便说说啦", "好的，我确认一下细节"], persona=store)
    outcome = pipeline.handle(_notif("方案定了吗？"), [])
    texts = [c.text for c in outcome.candidates]
    assert "随便说说啦" not in texts
    assert "好的，我确认一下细节" in texts


def test_pipeline_without_persona_unchanged():
    """不传 persona 时与既有行为一致（回归保护）。"""
    pipeline = ReplyPipeline(generator=lambda p: ["收到"])
    outcome = pipeline.handle(_notif("在吗"), [])
    assert outcome.candidates[0].text == "收到"


# ---- 一键清除联动 ----

def test_wipe_restores_initial(store):
    store.set_persona(me="本小姐")
    store.set_relation("老板", "同事")
    store.add_history("老板", "摘要")
    store.wipe()
    assert store.get_persona()["me"] == "我"
    assert store.get_relation("老板") == "陌生"
    assert store.history("老板") == []
