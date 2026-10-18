"""M1 核心链路验收测试——逐条对照 SRS 的功能需求验收准则。

不依赖 Android 运行时与云端 LLM（生成步骤用确定性桩），因此可在纯 Windows/Python
环境复现，作为「降级验证」的可信证据；真机侧只需替换 generator 与捕获层。
"""
from emoreply import (
    NoticeThrottle,
    ReplyPipeline,
    build_prompt,
    clean_candidate,
    dedupe,
    is_directed_to_me,
    preprocess,
    screen_incoming,
)
from emoreply.models import Notification


def note(**kw) -> Notification:
    base = {"source": "wechat", "timestamp": 0.0}
    base.update(kw)
    return Notification(**base)


class TestFr14DedupeAndFilter:
    """FR-1.4 验收：系统消息与自己消息过滤率 = 100%；重复通知去重率 = 100%。"""

    def test_system_and_self_messages_dropped(self):
        items = [
            note(is_system=True, sender="微信团队", body="你的验证码是 123456"),
            note(from_self=True, sender="我", body="好的"),
            note(sender="张三", chat="张三", body="在吗"),
        ]
        assert [d.action for d in preprocess(items)] == ["drop", "drop", "generate"]

    def test_identical_payload_merged(self):
        items = [
            note(timestamp=1.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=1.2, sender="张三", chat="张三", body="在吗"),
        ]
        assert len(dedupe(items)) == 1

    def test_summary_then_detail_merged_and_body_filled(self):
        """微信常先推摘要（无正文）再推详情，不能算成两条。"""
        items = [
            note(timestamp=1.0, sender="张三", chat="张三", title="张三"),
            note(timestamp=1.4, sender="张三", chat="张三", body="在吗"),
        ]
        merged = dedupe(items)
        assert len(merged) == 1
        assert merged[0].body == "在吗"

    def test_two_distinct_bodies_not_merged(self):
        """同一人快速连发的两条不同消息不能被吞掉。"""
        items = [
            note(timestamp=1.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=1.4, sender="张三", chat="张三", body="帮我看下这个"),
        ]
        assert len(dedupe(items)) == 2


class TestFr13Targeting:
    """FR-1.3 验收：私聊 100%；群聊 @/引用 ≥98%（首期仅硬规则，纯语义不考核）。"""

    def test_private_chat_always_directed(self):
        assert is_directed_to_me(note(is_group=False)) is True

    def test_group_mention_or_quote_directed(self):
        assert is_directed_to_me(note(is_group=True, mentions_me=True)) is True
        assert is_directed_to_me(note(is_group=True, quotes_me=True)) is True

    def test_group_without_signal_not_directed(self):
        assert is_directed_to_me(note(is_group=True, body="大家下午开会")) is False

    def test_group_accuracy_meets_acceptance(self):
        cases = []
        for i in range(50):
            if i % 2 == 0:
                cases.append((note(is_group=True, mentions_me=True, body=f"@{i} 看下"), True))
            else:
                cases.append((note(is_group=True, body=f"通知 {i}"), False))
        correct = sum(1 for n, expect in cases if is_directed_to_me(n) is expect)
        assert correct / len(cases) >= 0.98


class TestFr15Fallback:
    """FR-1.5 验收：空载荷场景 100% 触发兜底而非幻觉生成。"""

    def test_empty_payload_triggers_fallback(self):
        d = preprocess([note(sender="张三", body=None)])[0]
        assert d.action == "fallback"
        assert d.fallback_hint

    def test_whitespace_payload_triggers_fallback(self):
        assert preprocess([note(sender="张三", body="   ")])[0].action == "fallback"

    def test_fallback_never_invokes_generator(self):
        calls = []
        pipe = ReplyPipeline(generator=lambda p: calls.append(p) or ["不该出现"])
        out = pipe.run([note(sender="张三", body=None)])
        assert calls == []
        assert out[0].candidates == []
        assert out[0].notice


class TestGuardrail:
    """FR-3.5 / FR-6.4 验收：违规与诈骗类请求拒绝生成、给出风险提示。"""

    def test_otp_request_blocks_generation(self):
        calls = []
        pipe = ReplyPipeline(generator=lambda p: calls.append(p) or ["好的，我把验证码发你"])
        out = pipe.run([note(timestamp=1.0, sender="客服", body="把验证码发给我")])
        assert calls == []
        assert out[0].candidates == []
        assert "验证码" in out[0].notice

    def test_money_request_blocks_generation(self):
        calls = []
        pipe = ReplyPipeline(generator=lambda p: calls.append(p) or ["好的"])
        out = pipe.run([note(timestamp=1.0, sender="老板", body="帮我转账 5000 到这个账户")])
        assert calls == []
        assert out[0].notice

    def test_normal_messages_not_flagged(self):
        assert screen_incoming("我密码忘了怎么办").risky is False
        assert screen_incoming("晚上一起吃饭吗").risky is False

    def test_money_statement_not_flagged(self):
        """陈述句「我转账给你了」不是索取，不该被误拦。"""
        assert screen_incoming("我转账给你了，查收一下").risky is False

    def test_deceptive_candidate_filtered_out(self):
        pipe = ReplyPipeline(generator=lambda p: ["保证稳赚不赔，内部渠道带你飞", "好的，明天见"])
        out = pipe.run([note(timestamp=1.0, sender="小李", body="这个项目怎么样")])
        assert [c.text for c in out[0].candidates] == ["好的，明天见"]

    def test_repeated_risk_notice_throttled(self):
        """附录 C-2 C6：同一风险提示短时间内不重复弹。"""
        pipe = ReplyPipeline(generator=lambda p: [], throttle=NoticeThrottle(window=60.0))
        out = pipe.run([
            note(timestamp=1.0, sender="客服", body="把验证码发我"),
            note(timestamp=10.0, sender="客服", body="把验证码发我"),
        ])
        assert out[0].notice is not None
        assert out[1].suppressed is True
        assert out[1].notice is None


class TestAppendixCConstraints:
    """SRS 附录 C 的跨项目复用约束（C3 markdown 清洗 / C25 标记转义 / C16 token 预算）。"""

    def test_c3_markdown_stripped(self):
        assert clean_candidate("**好的**，我马上处理 [详情](http://example.com)") == "好的，我马上处理 详情"

    def test_c25_markup_stripped(self):
        assert clean_candidate("<p>收到</p>") == "收到"

    def test_c16_oldest_history_truncated_when_over_budget(self):
        history = [note(timestamp=i, sender="张三", chat="张三", body="旧消息" * 200) for i in range(5)]
        current = note(timestamp=99, sender="张三", chat="张三", body="在吗")
        bundle = build_prompt(current, history, max_tokens=600)
        assert bundle.truncated is True
        assert bundle.dropped_history > 0
        assert bundle.estimated_tokens <= 600

    def test_c16_within_budget_keeps_all_history(self):
        history = [note(timestamp=i, sender="张三", chat="张三", body="好的") for i in range(3)]
        current = note(timestamp=99, sender="张三", chat="张三", body="在吗")
        bundle = build_prompt(current, history, max_tokens=4096)
        assert bundle.truncated is False
        assert len(bundle.history) == 3


class TestEndToEnd:
    """端到端：捕获 → 去重 → 判定 → 上下文 → 生成 → 清洗 → 候选。"""

    def test_full_chain_actions(self):
        items = [
            note(timestamp=1.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=2.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=3.0, sender="张三", chat="张三", body="周末有空吗"),
            note(timestamp=4.0, sender="项目群", chat="项目群", is_group=True, body="大家下午开会"),
            note(timestamp=5.0, is_system=True, sender="微信团队", body="登录提醒"),
        ]
        pipe = ReplyPipeline(generator=lambda p: ["有空呀，周末一起？"])
        out = pipe.run(items)
        assert [o.decision.action for o in out] == ["generate", "generate", "drop", "drop"]
        assert len(out[1].candidates) == 1

    def test_same_chat_history_is_passed_downstream(self):
        seen = []
        pipe = ReplyPipeline(generator=lambda p: seen.append(p) or ["好的"])
        pipe.run([
            note(timestamp=1.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=3.0, sender="张三", chat="张三", body="周末有空吗"),
        ])
        assert seen[1].history == ["张三：在吗"]

    def test_cross_chat_history_isolated(self):
        seen = []
        pipe = ReplyPipeline(generator=lambda p: seen.append(p) or ["好的"])
        pipe.run([
            note(timestamp=1.0, sender="张三", chat="张三", body="在吗"),
            note(timestamp=3.0, sender="李四", chat="李四", body="有空吗"),
        ])
        assert seen[1].history == []