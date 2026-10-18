"""T01348 关系感知 → 语气默认档位（m1-poc 事实源）。

覆盖：
- 关系别名归一化与映射表（tone.py）；
- resolve_tone 三级解析（手动值 / AUTO+联系人覆盖 / AUTO+关系映射 / None 旧行为）；
- PersonaStore 联系人级手动覆盖存储（含旧库缺列迁移）；
- ReplyPipeline 端到端：AUTO 时按关系注入对应语气强度描述句。
"""
import sqlite3

from emoreply.compose import TONE_DEFAULT, tone_hint
from emoreply.models import Notification
from emoreply.persona import PersonaStore
from emoreply.pipeline import ReplyPipeline
from emoreply.tone import (RELATION_TONE, TONE_AUTO, TONE_MAX, TONE_MIN,
                           clamp_tone, normalize_relation, resolve_tone,
                           tone_for_relation)


def _notice(body: str = "周末去爬山吗？一起呀", sender: str = "小李") -> Notification:
    return Notification(source="manual", chat="c1", sender=sender, body=body, timestamp=1.0)


class TestNormalizeRelation:
    def test_aliases(self):
        assert normalize_relation("上级") == "领导"
        assert normalize_relation("平级") == "同事"
        assert normalize_relation("外部") == "客户"
        assert normalize_relation("好友") == "朋友"

    def test_blank_defaults(self):
        assert normalize_relation("") == "陌生"
        assert normalize_relation(None) == "陌生"
        assert normalize_relation("  ") == "陌生"

    def test_canonical_passthrough(self):
        assert normalize_relation("领导") == "领导"
        assert normalize_relation("家人") == "家人"


class TestRelationToneMap:
    def test_bands_follow_task_ranges(self):
        # 上级/客户/外部→克制区间；同事→中性；好友/家人/恋人→偏活泼
        assert tone_for_relation("领导") == RELATION_TONE["领导"] <= 40
        assert tone_for_relation("客户") <= 40
        assert 50 <= tone_for_relation("同事") <= 60
        assert tone_for_relation("朋友") >= 70
        assert tone_for_relation("家人") >= 70
        assert tone_for_relation("恋人") >= 70

    def test_relation_tones_are_distinct_bands(self):
        # 上级与好友必须落在不同语气档位（提示词描述句互异）——验收核心
        assert tone_hint(tone_for_relation("领导")) != tone_hint(tone_for_relation("朋友"))

    def test_unknown_relation_falls_back_to_default(self):
        assert tone_for_relation("死敌") == TONE_DEFAULT
        assert tone_for_relation("") == TONE_DEFAULT


class TestResolveTone:
    def test_none_stays_none(self):
        # 旧行为：不传 tone → 提示词不含语气强度行
        assert resolve_tone(None, "领导") is None

    def test_manual_value_clamped(self):
        assert resolve_tone(90, "领导") == 90
        assert resolve_tone(999, "领导") == TONE_MAX
        assert resolve_tone(0, "领导") == 0  # 0 是合法手动值（最理性档），不得当 AUTO

    def test_auto_uses_override_first(self):
        # 联系人级手动覆盖优先于关系映射
        assert resolve_tone(TONE_AUTO, "领导", override=80) == 80

    def test_auto_uses_relation(self):
        assert resolve_tone(TONE_AUTO, "领导") == RELATION_TONE["领导"]
        assert resolve_tone(TONE_AUTO, "上级") == RELATION_TONE["领导"]  # 别名归一化

    def test_auto_unknown_relation_defaults(self):
        assert resolve_tone(TONE_AUTO, "") == TONE_DEFAULT
        assert resolve_tone(TONE_AUTO, None) == TONE_DEFAULT

    def test_any_negative_is_auto_sentinel(self):
        # AUTO=-1 是唯一合法哨兵；其余负值一并按 AUTO 处理（滑杆不会产生，防御性）
        assert resolve_tone(-30, "领导") == RELATION_TONE["领导"]


class TestPersonaContactTone:
    def test_set_and_clear(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        assert store.contact_tone("老板") is None
        store.set_contact_tone("老板", 40)
        assert store.contact_tone("老板") == 40
        store.set_contact_tone("老板", None)
        assert store.contact_tone("老板") is None

    def test_set_clamps(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_contact_tone("老板", 500)
        assert store.contact_tone("老板") == TONE_MAX

    def test_tone_does_not_touch_relation(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("老板", "领导")
        store.set_contact_tone("老板", 40)
        assert store.get_relation("老板") == "领导"

    def test_old_db_without_column_migrates(self, tmp_path):
        # 旧库 contacts 无 tone_manual 列 → 打开时自动补列，历史数据不丢
        db = str(tmp_path / "old.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE persona (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);"
            "CREATE TABLE contacts (name TEXT PRIMARY KEY, relation TEXT NOT NULL);"
        )
        conn.execute("INSERT INTO contacts VALUES('老板', '领导')")
        conn.commit()
        conn.close()
        store = PersonaStore(db)
        assert store.get_relation("老板") == "领导"
        assert store.contact_tone("老板") is None
        store.set_contact_tone("老板", 42)
        assert store.contact_tone("老板") == 42


class TestPipelineAutoTone:
    def _capture(self, persona=None):
        seen = {}

        def gen(prompt):
            seen["system"] = prompt.system
            return ["好呀，周末一起去|语气轻松"]

        return ReplyPipeline(gen, persona=persona), seen

    def test_auto_resolves_by_relation(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小李", "领导")
        pipe, seen = self._capture(store)
        pipe.handle(_notice(), [], tone=TONE_AUTO)
        assert tone_hint(RELATION_TONE["领导"]) in seen["system"]

    def test_auto_prefers_contact_override(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小李", "领导")
        store.set_contact_tone("小李", 80)
        pipe, seen = self._capture(store)
        pipe.handle(_notice(), [], tone=TONE_AUTO)
        assert tone_hint(80) in seen["system"]
        assert tone_hint(RELATION_TONE["领导"]) not in seen["system"]

    def test_auto_without_persona_falls_back_to_default(self):
        pipe, seen = self._capture()
        pipe.handle(_notice(), [], tone=TONE_AUTO)
        assert tone_hint(TONE_DEFAULT) in seen["system"]

    def test_manual_value_untouched(self, tmp_path):
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小李", "领导")
        pipe, seen = self._capture(store)
        pipe.handle(_notice(), [], tone=10)
        assert tone_hint(10) in seen["system"]

    def test_none_stays_old_behavior(self):
        pipe, seen = self._capture()
        pipe.handle(_notice(), [], tone=None)
        assert "语气强度" not in seen["system"]
