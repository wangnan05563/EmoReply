"""T01350 采纳率闭环接回生成（m1-poc 事实源）。

覆盖：
- tone 桶化与维度键（tone 连续值按 20 分桶，关系归一化口径对齐 T01348）；
- adapt_tone 纯策略：样本 <10 不生效 / 采纳率 <30% 降 10 / 开关可关 / clamp≥0；
- FeedbackStore 三维统计（记录、读取、rate、wipe）；
- pipeline 端到端：低分场景语气行降档、样本不足不变、feedback=None 旧行为、
  multi_tone 时跳过 adapt。
"""
import pytest

from emoreply.compose import tone_hint
from emoreply.feedback import (LOW_RATE_THRESHOLD, MIN_SAMPLES, TONE_BUCKET_SIZE,
                               TONE_STEP_DOWN, FeedbackStore, adapt_tone, dim_key,
                               tone_bucket)
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline


def _notice(body: str = "周末去爬山吗？一起呀", sender: str = "小李") -> Notification:
    return Notification(source="manual", chat="c1", sender=sender, body=body, timestamp=1.0)


class TestToneBucket:
    def test_buckets(self):
        assert tone_bucket(0) == 0
        assert tone_bucket(20) == 1
        assert tone_bucket(50) == 2
        assert tone_bucket(99) == 4
        assert tone_bucket(100) == 4  # 边界收敛

    def test_bucket_size(self):
        assert TONE_BUCKET_SIZE == 20


class TestDimKey:
    def test_normalizes_relation(self):
        # 「上级」与「领导」同桶同键（与 T01348 归一化口径对齐）
        assert dim_key("上级", 35, "亲切") == dim_key("领导", 35, "亲切")

    def test_separates_tone_buckets(self):
        assert dim_key("领导", 35, "亲切") != dim_key("领导", 55, "亲切")

    def test_separates_style(self):
        assert dim_key("领导", 35, "亲切") != dim_key("领导", 35, "正式")


class TestAdaptTone:
    def _stats(self, adopted: int, ignored: int) -> dict:
        return {dim_key("领导", 40, "亲切"): {"adopted": adopted, "ignored": ignored}}

    def test_low_rate_steps_down(self):
        # 样本充足 + 采纳率 < 30% → 降 TONE_STEP_DOWN
        tone, down = adapt_tone("领导", 40, "亲切", self._stats(2, 8))
        assert down and tone == 40 - TONE_STEP_DOWN

    def test_insufficient_samples_noop(self):
        # 边界：样本量不足（<10）时策略不生效（验收核心）
        tone, down = adapt_tone("领导", 40, "亲切", self._stats(MIN_SAMPLES - 1, 0))
        assert not down and tone == 40

    def test_healthy_rate_noop(self):
        tone, down = adapt_tone("领导", 40, "亲切", self._stats(5, 5))
        assert not down and tone == 40

    def test_disabled_noop(self):
        tone, down = adapt_tone("领导", 40, "亲切", self._stats(0, 10), enabled=False)
        assert not down and tone == 40

    def test_unknown_dim_noop(self):
        tone, down = adapt_tone("领导", 40, "亲切", {})
        assert not down and tone == 40

    def test_none_tone_passthrough(self):
        assert adapt_tone("领导", None, "亲切", self._stats(0, 10)) == (None, False)

    def test_clamp_at_zero(self):
        # 已在最低档（桶 0）仍低分 → 降到 0，不为负
        stats = {"领导|0|亲切": {"adopted": 0, "ignored": 10}}
        tone, down = adapt_tone("领导", 5, "亲切", stats)
        assert down and tone == 0


class TestFeedbackStore:
    def test_record_and_rate(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.db"))
        assert store.rate("领导", 40, "亲切") is None
        for _ in range(3):
            store.record("领导", 40, "亲切", adopted=True)
        for _ in range(7):
            store.record("领导", 40, "亲切", adopted=False)
        assert store.rate("领导", 40, "亲切") == pytest.approx(0.3)
        # 同 tone 桶内任意值同键（41 与 45 同属 40-60 桶；40 属 40 桶边界值本身）
        assert store.rate("领导", 45, "亲切") == pytest.approx(0.3)
        assert store.rate("领导", 35, "亲切") is None  # 35 属上一桶，不同键

    def test_wipe(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.db"))
        store.record("领导", 40, "亲切", adopted=True)
        store.wipe()
        assert store.dim_stats() == {}

    def test_zero_retention(self, tmp_path):
        # 脱敏口径：库中只有维度键与计数，无正文
        store = FeedbackStore(str(tmp_path / "fb.db"))
        store.record("领导", 40, "亲切", adopted=True)
        blob = (tmp_path / "fb.db").read_bytes()
        assert "周末".encode("utf-8") not in blob  # 库内无正文（零留存）


class TestPipelineAdaptive:
    def _capture(self, feedback=None, auto_adapt=True, persona=None):
        seen = {}

        def gen(prompt):
            seen["system"] = prompt.system
            return ["好呀，周末一起去|语气轻松"]

        pipe = ReplyPipeline(gen, persona=persona, feedback=feedback, auto_adapt=auto_adapt)
        return pipe, seen

    def _low_rate_store(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.db"))
        for _ in range(2):
            store.record("领导", 40, "亲切", adopted=True)
        for _ in range(8):
            store.record("领导", 40, "亲切", adopted=False)
        return store

    def _leader_persona(self, tmp_path):
        from emoreply.persona import PersonaStore
        persona = PersonaStore(str(tmp_path / "p.db"))
        persona.set_relation("小李", "领导")
        return persona

    def test_low_rate_damps_tone(self, tmp_path):
        # 端到端：relation=领导 + 低分统计（样本 10、采纳率 20%）→ tone 40 降为 30，
        # 提示词语气行随数值跨档（中性自然 → 偏理性）
        persona = self._leader_persona(tmp_path)
        store = self._low_rate_store(tmp_path)
        pipe, seen = self._capture(feedback=store, persona=persona)
        pipe.handle(_notice(), [], tone=40)
        assert tone_hint(30) in seen["system"]
        assert tone_hint(40) not in seen["system"]

    def test_unrelated_dim_noop(self, tmp_path):
        # 无 persona → relation=「陌生」，与「领导」统计键不匹配 → 不生效（维度隔离）
        store = self._low_rate_store(tmp_path)
        pipe, seen = self._capture(feedback=store)
        pipe.handle(_notice(), [], tone=40)
        assert tone_hint(40) in seen["system"]

    def test_insufficient_samples_untouched(self, tmp_path):
        persona = self._leader_persona(tmp_path)
        store = FeedbackStore(str(tmp_path / "fb.db"))
        for _ in range(MIN_SAMPLES - 1):
            store.record("领导", 40, "亲切", adopted=False)
        pipe, seen = self._capture(feedback=store, persona=persona)
        pipe.handle(_notice(), [], tone=40)
        assert tone_hint(40) in seen["system"]

    def test_no_feedback_old_behavior(self, tmp_path):
        persona = self._leader_persona(tmp_path)
        pipe, seen = self._capture(persona=persona)
        pipe.handle(_notice(), [], tone=40)
        assert tone_hint(40) in seen["system"]

    def test_disabled_untouched(self, tmp_path):
        persona = self._leader_persona(tmp_path)
        store = self._low_rate_store(tmp_path)
        pipe, seen = self._capture(feedback=store, auto_adapt=False, persona=persona)
        pipe.handle(_notice(), [], tone=40)
        assert tone_hint(40) in seen["system"]

    def test_multi_tone_skips_adapt(self, tmp_path):
        persona = self._leader_persona(tmp_path)
        store = self._low_rate_store(tmp_path)
        pipe, seen = self._capture(feedback=store, persona=persona)
        pipe.handle(_notice(), [], tone=40, multi_tone=True)
        # 多风格下单值 tone 不注入，adapt 跳过（三档指令在）
        assert "多风格并行" in seen["system"]
        assert "\n语气强度：" not in seen["system"]

    def test_low_rate_with_auto_resolution(self, tmp_path):
        # T01348 AUTO 档与闭环叠加：AUTO 按关系解析出 35（领导），闭环再降一档
        persona = self._leader_persona(tmp_path)
        store = FeedbackStore(str(tmp_path / "fb.db"))
        for _ in range(1):
            store.record("领导", 35, "亲切", adopted=True)
        for _ in range(9):
            store.record("领导", 35, "亲切", adopted=False)
        pipe, seen = self._capture(feedback=store, persona=persona)
        from emoreply.tone import TONE_AUTO
        pipe.handle(_notice(), [], tone=TONE_AUTO)
        assert tone_hint(25) in seen["system"]
