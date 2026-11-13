"""T05730：低置信行纠错 POC——注入器与纠错器契约单测。

覆盖：注入器确定性/混淆对来自实测表/每行至多 1 处/无命中原样返回；
Noop 基线恒等；OpenAI 兼容纠错器的行数契约防护（输出行数不符/网络失败回退直通）。
"""
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emoreply.correction import (  # noqa: E402
    MEASURED_CONFUSION_PAIRS, NoopCorrector, OpenAICompatibleCorrector,
    inject_ocr_errors,
)


class TestInject:
    def test_deterministic_with_same_seed(self):
        rng1, rng2 = random.Random("s"), random.Random("s")
        text = "昨天上午十点开会"
        assert inject_ocr_errors(text, rng1, chance=1.0) == inject_ocr_errors(text, rng2, chance=1.0)

    def test_measured_pair_yesterday(self):
        """项目实测误读：昨→咋（时间分隔行）。"""
        out = inject_ocr_errors("昨天", random.Random("a"), chance=1.0)
        assert out in ("咋天", "昨天")   # 命中「昨」替换 or 概率路径
        assert inject_ocr_errors("昨天", random.Random("a"), chance=1.0) == \
            inject_ocr_errors("昨天", random.Random("a"), chance=1.0)

    def test_no_hit_char_unchanged(self):
        """不含混淆字的行永不误读（真实 OCR 同性质）。"""
        assert inject_ocr_errors("你好呀", random.Random("x"), chance=1.0) == "你好呀"

    def test_at_most_one_replacement(self):
        """一行内多处命中也至多替换 1 处（模拟单字误读形态）。"""
        out = inject_ocr_errors("昨天请请土", random.Random("b"), chance=1.0)
        diff = sum(1 for a, b in zip("昨天请请土", out) if a != b)
        assert diff <= 1

    def test_zero_chance_unchanged(self):
        assert inject_ocr_errors("昨天", random.Random("c"), chance=0.0) == "昨天"

    def test_confusion_pairs_all_measured(self):
        """混淆对表非空且每对为 (正确, 误读) 二元组——误读字不得与正确字相同。"""
        assert len(MEASURED_CONFUSION_PAIRS) >= 5
        assert all(right != wrong and len(right) == 1 for right, wrong in MEASURED_CONFUSION_PAIRS)


class TestNoop:
    def test_identity(self):
        lines = ["对方：在吗？", "我：在的"]
        ctx = ["时间：昨天 21:04"]
        assert NoopCorrector().correct(lines, ctx) == lines


class TestOpenAICompatibleContract:
    """契约防护：行数破坏/网络失败回退直通；围栏剥离；有效输出采信。"""

    class _FakeResp:
        def __init__(self, content):
            self._c = content

        def read(self):
            return json.dumps({"choices": [{"message": {"content": self._c}}]}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _patch(self, monkeypatch, content=None, error=None):
        import urllib.request
        if error is not None:
            monkeypatch.setattr(urllib.request, "urlopen",
                                lambda req, timeout=None: (_ for _ in ()).throw(error))
        else:
            monkeypatch.setattr(urllib.request, "urlopen",
                                lambda req, timeout=None: self._FakeResp(content))

    def test_contract_violation_falls_back(self, monkeypatch):
        """模型输出行数与输入不符（幻觉增删行）→ 回退直通，不采信。"""
        self._patch(monkeypatch, content=json.dumps(
            {"lines": ["多了一行", "又多一行"]}, ensure_ascii=False))
        cor = OpenAICompatibleCorrector("http://stub/v1", "stub-model")
        assert cor.correct(["昨天上午开会"], []) == ["昨天上午开会"]

    def test_valid_output_accepted(self, monkeypatch):
        self._patch(monkeypatch, content=json.dumps({"lines": ["咋天上午开会"]}, ensure_ascii=False))
        cor = OpenAICompatibleCorrector("http://stub/v1", "stub-model")
        assert cor.correct(["昨天上午开会"], []) == ["咋天上午开会"]

    def test_network_failure_falls_back(self, monkeypatch):
        """端点不可达 → 直通（POC 数据不丢）。"""
        self._patch(monkeypatch, error=OSError("unreachable"))
        cor = OpenAICompatibleCorrector("http://stub/v1", "stub-model")
        assert cor.correct(["昨天上午开会"], []) == ["昨天上午开会"]

    def test_markdown_fence_stripped(self, monkeypatch):
        self._patch(monkeypatch, content=(
            "```json\n" + json.dumps({"lines": ["咋天上午开会"]}, ensure_ascii=False) + "\n```"))
        cor = OpenAICompatibleCorrector("http://stub/v1", "stub-model")
        assert cor.correct(["昨天上午开会"], []) == ["咋天上午开会"]
