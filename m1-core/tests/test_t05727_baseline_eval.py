"""T05727：读屏 OCR 基线评测——schema 与指标模块（emoreply.baseline）的单测。

覆盖：CER 数值口径 / evaluate 五项指标的边界（完美、错侧、漏消息、噪声误入、
时间行豁免）/ 语料 JSONL 往返与非法 kind 校验 / 生成器场景的结构不变量 /
回放器的时间行判定。全部离线，不依赖字体与图片渲染。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from emoreply.baseline import (  # noqa: E402
    KIND_MESSAGE, KIND_NOISE, KIND_TIME, SIDE_MINE, SIDE_NONE, SIDE_OTHER,
    CorpusRecord, GtLine, cer, evaluate, format_report, load_corpus, save_predictions,
)


def _rec(image, lines):
    return CorpusRecord(image=image, app="wechat", theme="light",
                        chat="single", font_scale=1.0, lines=lines)


def _msg(text, side, msg=1, **kw):
    return GtLine(text=text, kind=KIND_MESSAGE, side=side, msg=msg, **kw)


# ===== CER =====

class TestCer:
    def test_identical_zero(self):
        assert cer("你好吗", "你好吗") == 0.0

    def test_substitution(self):
        assert cer("你好吗", "你好吧") == pytest.approx(1 / 3)

    def test_deletion(self):
        assert cer("今天晚上一起吃饭吗", "今天晚上吃饭吗") == pytest.approx(2 / 9)

    def test_empty_reference_is_zero(self):
        """空参考属语料 bug，不在 CER 维度放大（见 baseline.cer docstring）。"""
        assert cer("", "任意") == 0.0

    def test_whitespace_insensitive(self):
        assert cer("今天晚上一起吃饭", "今天晚上 一起吃饭") == 0.0


# ===== evaluate 指标 =====

class TestEvaluate:
    def test_perfect_predictions(self):
        corpus = [_rec("a.png", [_msg("在吗？", SIDE_OTHER, 1),
                                 _msg("在的，怎么了", SIDE_MINE, 2)])]
        preds = [{"image": "a.png", "lines": ["对方：在吗？", "我：在的，怎么了"]}]
        m = evaluate(corpus, preds)
        assert m["message_recall"] == 1.0
        assert m["side_accuracy"] == 1.0
        assert m["line_cer"] == 0.0
        assert m["noise_intrusion"] == 0.0
        assert m["dialogue_parse_rate"] == 1.0

    def test_wrong_side_penalizes_side_accuracy_only(self):
        corpus = [_rec("a.png", [_msg("在吗？", SIDE_OTHER, 1)])]
        preds = [{"image": "a.png", "lines": ["我：在吗？"]}]
        m = evaluate(corpus, preds)
        assert m["message_recall"] == 1.0          # 文本匹配上了
        assert m["side_accuracy"] == 0.0           # 但方向反了

    def test_missing_message_penalizes_recall(self):
        corpus = [_rec("a.png", [_msg("第一条", SIDE_OTHER, 1),
                                 _msg("第二条", SIDE_MINE, 2)])]
        preds = [{"image": "a.png", "lines": ["对方：第一条"]}]
        m = evaluate(corpus, preds)
        assert m["message_recall"] == pytest.approx(0.5)

    def test_noise_line_counts_as_intrusion(self):
        corpus = [_rec("a.png", [_msg("在吗？", SIDE_OTHER, 1)])]
        preds = [{"image": "a.png", "lines": ["对方：在吗？", "对方：()三"]}]
        m = evaluate(corpus, preds)
        assert m["noise_intrusion"] == pytest.approx(0.5)

    def test_time_mark_lines_exempt_from_intrusion(self):
        """「时间：」行是结构标记不是消息，不该被算成噪声误入。"""
        corpus = [_rec("a.png", [
            GtLine(text="昨天 21:04", kind=KIND_TIME, side=SIDE_NONE),
            _msg("在吗？", SIDE_OTHER, 1),
        ])]
        preds = [{"image": "a.png", "lines": ["时间：昨天 21:04", "对方：在吗？"]}]
        m = evaluate(corpus, preds)
        assert m["noise_intrusion"] == 0.0
        assert m["message_recall"] == 1.0

    def test_wrapped_message_grouped_to_one(self):
        """同 msg 号的两条物理行 = 一条消息（折行合并后的比对粒度）。"""
        corpus = [_rec("a.png", [_msg("这份方案你先看", SIDE_OTHER, 1),
                                 _msg("看，有问题随时说。", SIDE_OTHER, 1)])]
        preds = [{"image": "a.png", "lines": ["对方：这份方案你先看看，有问题随时说。"]}]
        m = evaluate(corpus, preds)
        assert m["gt_messages"] == 1
        assert m["message_recall"] == 1.0
        assert m["line_cer"] == 0.0

    def test_missing_prediction_image_counts_all_gt_as_missed(self):
        corpus = [_rec("a.png", [_msg("在吗？", SIDE_OTHER, 1)]),
                  _rec("b.png", [_msg("在吗？", SIDE_OTHER, 1)])]
        preds = [{"image": "a.png", "lines": ["对方：在吗？"]}]
        m = evaluate(corpus, preds)
        assert m["message_recall"] == pytest.approx(0.5)
        assert m["unmatched_images"] == ["b.png"]

    def test_parse_rate_only_applicable_with_other_message(self):
        """GT 无对方消息的图不适用可解析指标（链路本就出不了 current）。"""
        corpus = [_rec("a.png", [_msg("嗯", SIDE_MINE, 1)])]
        preds = [{"image": "a.png", "lines": ["我：嗯"]}]
        m = evaluate(corpus, preds)
        assert m["dialogue_parse_rate"] is None

    def test_format_report_renders(self):
        corpus = [_rec("a.png", [_msg("在吗？", SIDE_OTHER, 1)])]
        preds = [{"image": "a.png", "lines": ["对方：在吗？"]}]
        text = format_report(evaluate(corpus, preds))
        assert "消息级召回率" in text and "100.00%" in text


# ===== 语料 IO =====

class TestCorpusIO:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "corpus.jsonl"
        rec = _rec("images/a.png", [_msg("在吗？", SIDE_OTHER, 1, left=100, right=300,
                                        top=50, bottom=90),
                                    GtLine(text="[图片]", kind=KIND_NOISE)])
        with open(path, "w", encoding="utf-8") as f:
            import dataclasses
            f.write(json.dumps(dataclasses.asdict(rec), ensure_ascii=False) + "\n")
        loaded = load_corpus(path)
        assert len(loaded) == 1
        assert loaded[0].lines[0].left == 100
        assert loaded[0].lines[1].kind == KIND_NOISE

    def test_invalid_kind_raises(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"image": "a.png", "app": "wechat", "theme": "light",
                                "chat": "single", "font_scale": 1.0,
                                "lines": [{"text": "x", "kind": "typo"}]},
                               ensure_ascii=False) + "\n")
        with pytest.raises(ValueError, match="非法 kind"):
            load_corpus(path)


# ===== 生成器场景不变量（不渲染，纯逻辑）=====

class TestBuildScene:
    def _scene(self, chat, font_scale=1.0, seed="t"):
        from gen_baseline_corpus import build_scene
        return build_scene("wechat", "light", chat, font_scale, seed)

    def test_scene_has_other_message(self):
        _, gt, *_ = self._scene("single")
        assert any(g.side == SIDE_OTHER for g in gt)

    def test_kinds_valid_and_sides_consistent_per_msg(self):
        _, gt, *_ = self._scene("group")
        by_msg = {}
        for g in gt:
            assert g.kind in ("message", "image_text", "noise", "time")
            if g.kind == "message":
                by_msg.setdefault(g.msg, g.side)
                assert by_msg[g.msg] == g.side   # 同一消息折行侧别必须一致

    def test_group_chat_has_nicknames_as_noise(self):
        _, gt, *_ = self._scene("group", seed="g1")
        assert any(g.kind == KIND_NOISE for g in gt)

    def test_large_font_wraps_more(self):
        """字号放大后同一条消息应切成更多物理行（折行维度生效）。"""
        _, gt_small, *_ = self._scene("single", 1.0, seed="w")
        _, gt_big, *_ = self._scene("single", 1.3, seed="w")
        msg_small = sum(1 for g in gt_small if g.kind == KIND_MESSAGE)
        msg_big = sum(1 for g in gt_big if g.kind == KIND_MESSAGE)
        assert msg_big > msg_small


# ===== 回放器时间行判定 =====

class TestIsTimeRow:
    def _line(self, text, width=1080):
        from eval_ocr_baseline import _is_time_row
        from emoreply.preprocess import OcrLine
        return _is_time_row(OcrLine(text, 400, 600, 100, 140), width)

    def test_centered_time_row_true(self):
        assert self._line("昨天 21:04")

    def test_left_aligned_not_time(self):
        from eval_ocr_baseline import _is_time_row
        from emoreply.preprocess import OcrLine
        assert not _is_time_row(OcrLine("昨天 21:04", 40, 240, 100, 140), 1080)

    def test_centered_non_time_false(self):
        assert not self._line("在吗？")
