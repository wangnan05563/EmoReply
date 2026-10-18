"""T01245 FR-8.3 防诈骗风险提示：威胁/恐吓类识别 + 候选置顶提醒 + 误报控制。

验收（PRD FR-8.3）：转账/借钱/威胁/验证码五类样本全部触发风险提醒；正常消息零误报；
不产出顺从诈骗话术的候选。护栏杆 + 拒绝生成的结构已由既有测试覆盖（otp/money），
本文件补充补充用 FR-8.3 增加的新增「威胁」类与回归断言。
"""
import pytest

from emoreply.guardrail import screen_incoming
from emoreply.pipeline import ReplyPipeline
from emoreply.models import Notification


def note(**kw):
    base = {"source": "n", "chat": "c1", "sender": "对方", "body": "", "timestamp": 1.0}
    base.update(kw)
    return Notification(**base)


class TestThreat:
    """FR-8.3 新增：威胁/恐吓类诈骗识别。"""

    @pytest.mark.parametrize("body", [
        "再不转账后果自负",          # 恐吓施压
        "不配合调查就冻结你的账户",   # 假冒公检法 + 冻结
        "你再不还钱就报警抓你",      # 恐吓 + 威胁报警
        "你涉嫌洗钱，警察马上上门找你", # 通缉/上门
        "法院传票已发，不来出庭就坐牢", # 传票 + 坐牢
    ])
    def test_threat_flagged(self, body):
        r = screen_incoming(body)
        assert r.risky is True
        assert r.category == "threat"

    @pytest.mark.parametrize("body", [
        "晚上一起吃饭吗",           # 正常邀约
        "我明天要去法院办个证明",     # 正常陈述（非施压）
        "放心，不会被冻结的",        # 正常安抚（冻结需接账户/资金才判）
        "你叫我配合他们调查",        # 陈述他人之言
    ])
    def test_threat_no_false_positive(self, body):
        assert screen_incoming(body).risky is False


    @pytest.mark.parametrize("body", [
        "把验证码发给我",           # FR-6.4 otp 仍生效
        "帮我转账 5000 到这个账户",    # FR-6.4 money 仍生效
        "我是你领导，账户异常需要处理",  # impersonation
    ])
    def test_existing_categories_still_work(self, body):
        assert screen_incoming(body).risky is True

    def test_normal_account_freeze_statement_not_flagged(self):
        """「不会被冻结」是正常安抚，不是威胁。"""
        assert screen_incoming("放心，不会被冻结的").risky is False

    def test_threat_blocks_generation_with_notice(self):
        calls = []

        def gen(p):
            calls.append(p)
            return ["好的，我马上转给你"]

        pipe = ReplyPipeline(generator=gen)
        out = pipe.run([note(body="再不转账后果自负")])
        assert calls == []
        assert out[0].candidates == []
        assert out[0].notice
        assert "威胁" in out[0].notice or "诈骗" in out[0].notice