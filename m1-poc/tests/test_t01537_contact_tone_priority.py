"""T01537：逐联系人语气调节——覆盖优先级（联系人 > 全局 > 关系推档）（m1-poc 事实源）。

T01348 只让联系人覆盖在 AUTO 档生效；T01537 升级为联系人级压过一切全局值
（用户在 IME 按联系人手调后，不应被全局滑杆静默盖掉）。

覆盖：
- resolve_tone 新优先级：override vs 手动全局 / AUTO 全局 / None；
- ReplyPipeline 端到端：全局手动值时联系人覆盖生效、未覆盖联系人保持全局；
- 读写/回落/清除（回归 T01348 既有行为，防止优先级改造引入回归）。
"""
from emoreply.compose import TONE_DEFAULT, tone_hint
from emoreply.models import Notification
from emoreply.persona import PersonaStore
from emoreply.pipeline import ReplyPipeline
from emoreply.tone import (RELATION_TONE, TONE_AUTO, TONE_MAX, clamp_tone,
                           resolve_tone)


def _notice(body: str = "周末去爬山吗？一起呀", sender: str = "小李") -> Notification:
    return Notification(source="manual", chat="c1", sender=sender, body=body, timestamp=1.0)


class TestResolvePriority:
    """resolve_tone：联系人 > 全局 > 关系推档。"""

    def test_override_beats_manual_global(self):
        # 核心升级：全局是手动值 90，联系人偏好 20 → 取 20（旧语义会取 90）
        assert resolve_tone(90, "领导", override=20) == 20

    def test_override_beats_auto_global(self):
        assert resolve_tone(TONE_AUTO, "领导", override=80) == 80

    def test_manual_global_beats_relation(self):
        # 无联系人偏好 → 全局手动值优先于关系推档
        assert resolve_tone(45, "领导") == 45

    def test_auto_falls_back_to_relation(self):
        # 无联系人偏好 + AUTO → 关系推档（回落链最后一环）
        assert resolve_tone(TONE_AUTO, "领导") == RELATION_TONE["领导"]

    def test_none_no_injection(self):
        # None（调用方不注入语气行）旧行为不变，override 也不越权
        assert resolve_tone(None, "领导", override=20) is None

    def test_override_clamped(self):
        assert resolve_tone(TONE_AUTO, "朋友", override=500) == TONE_MAX
        assert resolve_tone(90, "领导", override=-5) == clamp_tone(-5)


class TestPipelineContactOverrideGlobal:
    def _capture(self, persona=None):
        seen = {}

        def gen(prompt):
            seen["system"] = prompt.system
            return ["好呀，周末一起去|语气轻松"]

        return ReplyPipeline(gen, persona=persona), seen

    def test_manual_global_overridden_by_contact(self, tmp_path):
        # 端到端：全局传手动 90，联系人「小李」偏好 20 → 提示词注入 20 的描述句
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小李", "领导")
        store.set_contact_tone("小李", 20)
        pipe, seen = self._capture(store)
        pipe.handle(_notice(sender="小李"), [], tone=90)
        assert tone_hint(20) in seen["system"]
        assert tone_hint(90) not in seen["system"]

    def test_uncovered_contact_keeps_manual_global(self, tmp_path):
        # 对照组：「小张」未设偏好 → 全局手动 90 原样生效（全局 > 关系推档）
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小张", "领导")
        pipe, seen = self._capture(store)
        pipe.handle(_notice(sender="小张"), [], tone=90)
        assert tone_hint(90) in seen["system"]

    def test_different_contacts_different_tone(self, tmp_path):
        # 验收场景镜像：同一消息对「领导(30)」与「朋友(80)」分别手调 → 语气各随其人
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_contact_tone("王总", 30)
        store.set_contact_tone("阿明", 80)
        for name, expect in (("王总", 30), ("阿明", 80)):
            pipe, seen = self._capture(store)
            pipe.handle(_notice(sender=name), [], tone=TONE_AUTO)
            assert tone_hint(expect) in seen["system"]

    def test_clear_override_falls_back(self, tmp_path):
        # 清除偏好（None）→ 回落 AUTO 关系推档，不再命中联系人级
        store = PersonaStore(str(tmp_path / "p.db"))
        store.set_relation("小李", "领导")
        store.set_contact_tone("小李", 80)
        store.set_contact_tone("小李", None)
        pipe, seen = self._capture(store)
        pipe.handle(_notice(sender="小李"), [], tone=TONE_AUTO)
        assert tone_hint(RELATION_TONE["领导"]) in seen["system"]
        assert tone_hint(80) not in seen["system"]
