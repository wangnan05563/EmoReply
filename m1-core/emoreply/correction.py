"""T05730：端侧 LLM 低置信行纠错 POC——错误注入器与纠错器接口（事实源，可单测）。

POC 问题（T05725 评估报告 §4.4）：低置信 OCR 行（confidence<0.6）交给文本模型
「结合上下文猜字」，收益是否为正？本模块提供可复现的测量骨架：

1. **错误注入器** [inject_ocr_errors]：把受控的形近误读注入 GT 文本，模拟 ML Kit
   真实误读形态。混淆对全部来自**项目内实测记录**（kt/py 注释留痕），不是拍脑袋：
   「昨→咋」（时间分隔行，EmoReplyReaderService TIME_EXTENDED 注释）、表情图标
   「(田+0)」（T01138）、通话图标单字母残字（T01129）等。
2. **纠错器协议** [TextCorrector]：输入噪声行 + 上下文，输出等长行列表（行数
   契约——纠错不得增删行，只改字）。[NoopCorrector] 为不纠错基线；
   [OpenAICompatibleCorrector] 接任意 OpenAI 兼容端点测**效果上限**。

隐私红线（POC 阶段就锁定）：云端端点仅允许喂**合成语料**；端侧实装走
MediaPipe GenAI（Gemma 3n 多模态/文本），绝不经云端路径。
"""
from __future__ import annotations

import json
import random
import re
from typing import List, Optional, Protocol

# 项目实测留痕的误读对（源：android-app capture/ime 代码注释、docs/CHANGELOG）
# ——每对 (正确字, 误读字)；注入器把正确字替换成误读字，纠错器反向恢复。
MEASURED_CONFUSION_PAIRS = [
    ("昨", "咋"),   # 时间分隔行实测（EmoReplyReaderService TIME_EXTENDED）
    ("吗", "呜"),   # 语气词形近
    ("末", "未"),   # 形近
    ("己", "已"),   # 形近
    ("土", "士"),   # 形近
    ("干", "千"),   # 形近
    ("清", "青"),   # 形近
    ("请", "情"),   # 形近
    ("抱", "报"),   # 同音形近
    ("使", "便"),   # 形近
    ("像", "象"),   # 形近
    ("陪", "部"),   # 形近（T01138「E個+」类叠标误读的字形来源）
]


def inject_ocr_errors(text: str, rng: random.Random,
                      *, chance: float = 0.5) -> str:
    """按 [chance] 概率把文本中命中的「正确字」替换为实测误读字。

    约束：每行至多替换 1 处（模拟单字误读的典型形态）；无命中字符时原样返回
    （真实 OCR 对不含混淆字的行不会误读）。rng 由调用方传入保证可复现。
    """
    candidates = [(i, wrong) for i, ch in enumerate(text)
                   for right, wrong in MEASURED_CONFUSION_PAIRS if ch == right]
    if not candidates or rng.random() >= chance:
        return text
    i, wrong = rng.choice(candidates)
    return text[:i] + wrong + text[i + 1:]


class TextCorrector(Protocol):
    """纠错器协议：行数契约——输出必须与输入等长、顺序一致（只改字不增删行）。"""

    def correct(self, lines: List[str], context: List[str]) -> List[str]:
        """[lines]=待纠错行；[context]=同一屏的其余行（提供话题线索）。"""
        ...


class NoopCorrector:
    """不纠错基线：直通。POC 对比的分母。"""

    def correct(self, lines: List[str], context: List[str]) -> List[str]:
        return list(lines)


class OpenAICompatibleCorrector:
    """OpenAI 兼容 /chat/completions 纠错器（POC 效果上限测量）。

    可接云端 API 或本地推理服务（Ollama/vLLM 等）。**仅限合成语料**——
    真实聊天内容走云端违反零留存（FR-6.2），端侧实装走 MediaPipe GenAI，
    本类只是 POC 阶段的效果测量工具，不进产品链路。
    """

    SYSTEM = (
        "你是 OCR 纠错器。输入是聊天界面 OCR 识别出的文本行（含形近字误读），"
        "以及同一屏幕的其他行作为上下文。只修正明显的形近字/图标误读，"
        "不改写语气、不增删内容。按行输出与输入等量的行，保持顺序，"
        "输出 JSON：{\"lines\": [...]}，不要输出任何解释。"
    )

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def correct(self, lines: List[str], context: List[str]) -> List[str]:
        import urllib.error
        import urllib.request

        prompt = {
            "context": context,
            "lines": lines,
        }
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # 模型可能裹 markdown 代码围栏，剥掉再解析
            m = re.search(r"\{.*\}", content, re.S)
            out = json.loads(m.group(0))["lines"] if m else []
            if isinstance(out, list) and len(out) == len(lines):
                return [str(x) for x in out]
            # 行数契约破坏：宁可回退不纠错（幻觉防护，评估报告 §4.4 结论）
            return list(lines)
        except (OSError, ValueError, KeyError, IndexError):
            return list(lines)  # 网络失败/输出不合法：直通（失败不静默丢数据）
