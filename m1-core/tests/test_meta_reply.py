"""用户反馈（2026-09-27 截图）：第三条候选「请提供具体的聊天记录或对方最近说的话，
这样我才能帮你起草回复。」与输入内容无关——LLM 元话语/请求澄清被当候选正文。

修复：①提示词禁令（SYSTEM_TEMPLATE 约束 4）；②输出侧 is_meta_candidate 过滤。
"""
from emoreply.compose import SYSTEM_TEMPLATE, build_prompt
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.sanitize import is_meta_candidate


def _notice(body, sender="对方"):
    return Notification(source="manual", timestamp=1000.0, body=body,
                        sender=sender, chat="手动生成", title="手动生成")


def _stub(meta_texts):
    """桩生成器：返回含元话语的输出，验证过滤。"""

    def gen(prompt):
        return ["|".join(meta_texts)]

    return gen


META_SAMPLES = [
    # 真机截图样本（本单现象）
    "请提供具体的聊天记录或对方最近说的话，这样我才能帮你起草回复。",
    "请补充更多上下文信息。",
    "请发送给我更多的聊天内容。",
    "这样我才能帮你起草回复。",
    "抱歉，无法生成回复。",
    "作为一个AI助手，我无法了解具体情境。",
]


class TestMetaFilter:
    def test_screenshot_sample_detected(self):
        assert is_meta_candidate(
            "请提供具体的聊天记录或对方最近说的话，这样我才能帮你起草回复。")

    def test_all_meta_samples_detected(self):
        for s in META_SAMPLES:
            assert is_meta_candidate(s), s

    def test_normal_replies_not_killed(self):
        # 真实聊天回复不得误杀（「请告诉我地址」不含聊天上下文词）
        for s in ["请告诉我地址", "方便的时候发我一下", "那就不打扰你了，等你方便再聊",
                  "休息一下吧，趁这会儿可以做点自己的事"]:
            assert not is_meta_candidate(s), s

    def test_system_template_has_meta_ban(self):
        assert "元话语" in SYSTEM_TEMPLATE
        assert "请提供更多信息" in SYSTEM_TEMPLATE

    def test_pipeline_drops_meta_candidates(self):
        pipe = ReplyPipeline(generator=_stub(META_SAMPLES))
        outcome = pipe.handle(_notice("儿子睡着了"), [])
        assert outcome.candidates == []

    def test_pipeline_keeps_normal_candidate(self):
        pipe = ReplyPipeline(generator=_stub(["休息一下吧，趁这会儿可以做点自己的事"]))
        outcome = pipe.handle(_notice("儿子睡着了"), [])
        assert [c.text for c in outcome.candidates] == ["休息一下吧，趁这会儿可以做点自己的事"]

    def test_build_prompt_contains_meta_ban(self):
        bundle = build_prompt(_notice("在吗"), [])
        assert "元话语" in bundle.system
