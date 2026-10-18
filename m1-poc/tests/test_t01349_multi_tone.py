"""T01349 多风格并行候选（m1-poc 事实源）。

覆盖：
- tone_variants_hint 指令组装（三档互异、顺序锁定）；
- build_prompt：tone_variants 优先于单值 tone；缺省旧行为不变；
- pipeline.handle multi_tone：默认关（无指令）；开=注入指令 + 候选按序打语气标签
  （条数与档位数严格一致才打标）+ 去重兜底 + 与 AUTO 档叠加。
延迟验收（P50/P95 对比）属端侧实测，待设备在线补测；本文件锁定平台无关逻辑。
"""
from emoreply.compose import (MULTI_TONE_LABELS, MULTI_TONE_VARIANTS, build_prompt,
                              tone_hint, tone_variants_hint)
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.tone import TONE_AUTO


def _notice(body: str = "周末去爬山吗？一起呀") -> Notification:
    return Notification(source="manual", chat="c1", sender="小李", body=body, timestamp=1.0)


class TestToneVariantsHint:
    def test_lists_all_bands_distinct(self):
        hint = tone_variants_hint(MULTI_TONE_VARIANTS)
        for label in MULTI_TONE_LABELS:
            assert f"{label}档" in hint
        # 三档描述必须互异——否则「跨语气分布」无从谈起
        assert len({tone_hint(t) for t in MULTI_TONE_VARIANTS}) == len(MULTI_TONE_VARIANTS)

    def test_order_locked(self):
        hint = tone_variants_hint(MULTI_TONE_VARIANTS)
        assert "顺序与上列一致" in hint and "不得打乱" in hint

    def test_more_tones_than_labels_gets_generic_label(self):
        hint = tone_variants_hint([20, 40, 60, 80])
        assert "档4" in hint


class TestBuildPromptVariants:
    def test_default_absent(self):
        # 旧行为：不传 variants/tone → 无任何语气强度描述
        assert "语气强度" not in build_prompt(_notice(), []).system

    def test_single_tone_old_behavior(self):
        assert tone_hint(90) in build_prompt(_notice(), [], tone=90).system

    def test_variants_override_single_tone(self):
        # variants 优先：单值注入行「\n语气强度：」不得出现（避免两套语气指令打架；
        # 注意 tone_hint(80) 与 tone_hint(90) 同档描述，不能用描述句判重）
        bundle = build_prompt(_notice(), [], tone=90, tone_variants=MULTI_TONE_VARIANTS)
        assert "\n语气强度：" not in bundle.system
        assert "多风格并行" in bundle.system
        for t in MULTI_TONE_VARIANTS:
            assert tone_hint(t) in bundle.system


class TestPipelineMultiTone:
    def _capture(self, replies):
        seen = {}

        def gen(prompt):
            seen["system"] = prompt.system
            return replies

        return ReplyPipeline(gen), seen

    def test_off_is_old_behavior(self):
        # 默认关：system 无多风格指令，rationale 无档位前缀
        pipe, seen = self._capture(["好呀一起去|理由A"])
        out = pipe.handle(_notice(), [])
        assert "多风格并行" not in seen["system"]
        assert out.candidates[0].rationale == "理由A"

    def test_on_injects_instruction_and_tags(self):
        replies = ["这周加班，下次再约|遗憾推辞", "看情况吧|中性模糊", "好呀冲！|热情答应"]
        pipe, seen = self._capture(replies)
        out = pipe.handle(_notice(), [], multi_tone=True)
        assert "多风格并行" in seen["system"]
        labels = [c.rationale.split("：")[0] for c in out.candidates]
        assert labels == [f"{l}档" for l in MULTI_TONE_LABELS]

    def test_tag_keeps_original_rationale(self):
        replies = ["A|原有理由", "B|", "C|另一理由"]
        pipe, _ = self._capture(replies)
        out = pipe.handle(_notice(), [], multi_tone=True)
        assert out.candidates[0].rationale == "克制档：原有理由"
        assert out.candidates[1].rationale == "中性档"  # 无原理由不留悬空冒号
        assert out.candidates[2].rationale == "活泼档：另一理由"

    def test_count_mismatch_no_tags(self):
        # 模型只出 2 条（<3 档）→ 不打标（宁缺勿错，避免张冠李戴）
        replies = ["这周加班，下次再约|推辞", "好呀冲！|答应"]
        pipe, _ = self._capture(replies)
        out = pipe.handle(_notice(), [], multi_tone=True)
        assert [c.rationale for c in out.candidates] == ["推辞", "答应"]

    def test_duplicates_still_filtered(self):
        # 多档下模型挤同义候选 → is_duplicate 兜底仍在
        replies = ["好呀一起去|语气1", "好呀一起去！|语气2", "好呀一起去~|语气3"]
        pipe, _ = self._capture(replies)
        out = pipe.handle(_notice(), [], multi_tone=True)
        assert len(out.candidates) == 1

    def test_auto_tone_coexists(self):
        # AUTO 档 + 多风格：指令在；AUTO 解析出的单值描述不出现（variants 优先）
        pipe, seen = self._capture(["A|", "B|", "C|"])
        pipe.handle(_notice(), [], tone=TONE_AUTO, multi_tone=True)
        assert "多风格并行" in seen["system"]
        assert "语气强度：" not in seen["system"]
