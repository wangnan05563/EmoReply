"""T01244 FR-8.2 三维筛选：关系×场景×目的的 prompt 约束注入与默认继承。

验收（PRD FR-8.2）：生成前置可选「对方关系 + 沟通目的」，作为 prompt 约束注入；
默认继承上次选择。缺省不注入（保持 M1 行为）；注入的约束文本含对应维度描述。
"""
import pytest

from emoreply.compose import build_filter_hint, build_prompt
from emoreply.models import Notification


def notif(body):
    return Notification(source="n", chat="c", sender="对方", body=body, timestamp=1.0)


class TestFilterHint:
    "FR-8.2 三维筛选提示词注入"

    def test_empty_returns_empty(self):
        assert build_filter_hint() == ""
        assert build_filter_hint(relation="", scenario="", purpose="") == ""

    def test_relation_injected(self):
        h = build_filter_hint(relation="领导")
        assert "对方关系：领导" in h
        assert "不越界" in h

    def test_scenario_injected(self):
        h = build_filter_hint(scenario="工作")
        assert "工作语境" in h

    def test_purpose_injected(self):
        h = build_filter_hint(purpose="拒绝")
        assert "礼貌表达拒绝" in h

    def test_three_dims_combined(self):
        h = build_filter_hint(relation="领导", scenario="工作", purpose="推进")
        assert "对方关系：领导" in h
        assert "工作语境" in h
        assert "推进事情落地" in h

    def test_relation_subsumed_by_intent_branch_keeps_working(self):
        """旧关系注入路径（intent 非空时）不受影响；且 relation 不因三维重复注入。"""
        p = build_prompt(notif("项目进度怎么样"), [], intent="询问", relation="领导", scenario="工作", purpose="推进")
        assert p.system.count("对方关系：领导") == 1
        assert "工作语境" in p.system
        assert "推进事情落地" in p.system

    def test_scenario_purpose_flow_into_system(self):
        p = build_prompt(notif("游戏要不要"), [], scenario="闲聊", purpose="拒绝", emoji=False)
        assert "对方在闲聊" in p.system
        assert "礼貌表达拒绝" in p.system

    def test_no_intent_three_dim_still_work(self):
        """intent 缺失时三维约束仍生效（与消息分类解耦）。"""
        p = build_prompt(notif("周末干嘛"), [], scenario="闲聊", purpose="闲聊")
        assert "对方在闲聊" in p.system


class TestPipelineFilter:
    "三维筛选经 pipeline 透传到提示词"

    def test_scenario_purpose_forwarded(self):
        captured = {}

        def gen(p):
            captured["system"] = p.system
            return ["好的，明白"]

        from emoreply.pipeline import ReplyPipeline
        pipe = ReplyPipeline(generator=gen)
        pipe.handle(notif("这个方案你再验证一下"), [], scenario="工作", purpose="推进")
        assert "工作语境" in captured["system"]
        assert "推进事情落地" in captured["system"]

    def test_default_empty_not_injected(self):
        captured = {}

        def gen(p):
            captured["system"] = p.system
            return ["好的"]

        from emoreply.pipeline import ReplyPipeline
        pipe = ReplyPipeline(generator=gen)
        pipe.handle(notif("晚上一起吃饭"), [])
        assert "场景：" not in captured["system"]
        assert "目的：" not in captured["system"]