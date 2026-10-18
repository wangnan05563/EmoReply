"""T01131 反馈修复：候选尾部「|」分隔符残留清洗。

根因：LLM「内容|理由」输出理由缺失时（「文本|」），原 split_rationale 的
| 分支要求两侧均非空，整段含 | 落入兜底被当候选正文返回（真机实测
「太好了，好好享受这段旅行时光。|」）。修复=悬空管道符剥离 + 尾部 | 清洗。
"""
import pytest

from emoreply.sanitize import clean_candidate, expand_candidates, split_rationale


class TestDanglingPipe:
    def test_text_with_trailing_pipe_no_rationale(self):
        assert split_rationale("太好了，好好享受这段旅行时光。|") == ("太好了，好好享受这段旅行时光。", None)

    def test_bare_pipe_is_empty(self):
        assert split_rationale("|") == ("", None)

    def test_pipe_with_spaces_only(self):
        assert split_rationale("好的呀 |   ") == ("好的呀", None)

    def test_clean_candidate_strips_trailing_pipes(self):
        assert clean_candidate("文本。|") == "文本。"
        assert clean_candidate("文本。||") == "文本。"
        assert clean_candidate("文本。 | ") == "文本。"

    def test_normal_pipe_pair_unchanged(self):
        # 回归：正常「内容|理由」不受影响
        text, rationale = split_rationale("好的呀|语气亲近")
        assert text == "好的呀"
        assert rationale == "语气亲近"

    def test_expand_pipeline_no_pipe_residue(self):
        # 端到端链路：expand_candidates 拆分（契约不含清洗）→ split_rationale 清洗，
        # pipeline/commit 实际消费的组合行为无残留
        for raw in expand_candidates("太好了，好好享受这段旅行时光。|"):
            text, _ = split_rationale(raw)
            assert text == "太好了，好好享受这段旅行时光。"

    def test_expand_pipeline_bare_pipe_dropped(self):
        # 近空候选「|」清洗后为空文本，pipeline 按空文本跳过（不产出候选）
        for raw in expand_candidates("|"):
            text, _ = split_rationale(raw)
            assert text == ""

    def test_middle_pipe_still_splits(self):
        # 内容本身含 | 的正当分隔不受影响（rsplit 取最后一个）
        text, rationale = split_rationale("a|b|理由")
        assert text == "a|b"
        assert rationale == "理由"
