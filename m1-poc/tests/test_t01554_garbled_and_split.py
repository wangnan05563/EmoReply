"""T01554 同构镜像：乱码行过滤 + 候选拆分增强。

源真值为 Android 侧两个 bug 修复：
1. `EmoReplyReaderService.isGarbledText` —— OCR 偶发乱码行未被符号占比阈值拦住；
2. `Sanitize.splitRationale` —— 理由列偶尔显示在候选列（分隔符错位 / 关键词缺失）。

本模块把两侧逻辑镜像回 m1-poc 事实源，并对 kt 原实现的启发式规则做收紧
（详见 sanitize._heuristic_rationale_split 注释），用例含误伤回归锁。
"""
import pytest

from emoreply.preprocess import OcrLine, filter_non_text_noise, is_garbled_text
from emoreply.sanitize import split_rationale


def line(text):
    return OcrLine(text=text, left=0, right=100, top=0, bottom=20)


# ---- 判据 1：控制字符 ----
def test_control_char_is_garbled():
    assert is_garbled_text("你好\u0007世界")
    assert is_garbled_text("abc\u0000def")


def test_common_whitespace_not_garbled():
    # \t \n \r 属正常空白，不应判乱码
    assert not is_garbled_text("你好\t可以的")


# ---- 判据 2：有意义字符占比 < 70% ----
def test_low_meaningful_ratio_is_garbled():
    assert is_garbled_text("■□▲△■□▲△")


def test_normal_text_high_ratio_kept():
    assert not is_garbled_text("周末一起去爬山好吗")
    assert not is_garbled_text("好的👌收到")


# ---- 判据 3：连续 3+ 个相同非文字符号 ----
def test_consecutive_symbols_is_garbled():
    assert is_garbled_text("???")
    assert is_garbled_text("■■■")
    assert is_garbled_text("！！！")


def test_cjk_repeat_not_garbled():
    # 判据 3 排除汉字重复：中文叠字是合法表达
    assert not is_garbled_text("好好好")


# ---- 判据 4：纯符号行 ----
def test_pure_symbol_line_is_garbled():
    assert is_garbled_text("※◎※◎")
    assert not is_garbled_text("好")  # 长度 ≤2 不触发本判据


def test_blank_is_garbled():
    assert is_garbled_text("")
    assert is_garbled_text("   ")


def test_filter_non_text_noise_drops_garbled():
    src = [line("周末一起去爬山好吗"), line("???",), line("■■■"), line("明天下午三点开会")]
    kept = filter_non_text_noise(src)
    kept_texts = [x.text for x in kept]
    assert kept_texts == ["周末一起去爬山好吗", "明天下午三点开会"]


def test_filter_non_text_noise_keeps_media_placeholder_semantics():
    # 占位符/角标仍由既有判据剔除，行为不变
    kept = filter_non_text_noise([line("[图片]"), line("周末一起爬山好吗")])
    assert [x.text for x in kept] == ["周末一起爬山好吗"]


# ---- split_rationale：T01554 分隔符错位检测 ----
def test_misplaced_pipe_swaps_text_and_rationale():
    # 「|」位置错误：短确认词在前、长中文解释在后 → 交换
    text, rationale = split_rationale("好|今天天气不错，我们去爬山吧")
    assert text == "今天天气不错，我们去爬山吧"
    assert rationale == "好"


def test_normal_pipe_not_swapped():
    text, rationale = split_rationale("今天天气不错，我们去爬山吧|语气轻松，回应邀请")
    assert text == "今天天气不错，我们去爬山吧"
    assert rationale == "语气轻松，回应邀请"


def test_long_text_short_rationale_not_swapped():
    # text > 3 字符：不满足错位条件
    text, rationale = split_rationale("好的我看看|短")
    assert text == "好的我看看"


# ---- split_rationale：T01554 启发式关键词拆分 ----
def test_heuristic_split_on_rationale_keyword():
    # 拆分后 rationale 不带「理由：」标签（与 INLINE_RATIONALE 的 group(2) 口径一致，
    # UI 展示理由时不必再剥一次标签词）
    text, rationale = split_rationale("好的理由：这个建议很合理")
    assert text == "好的"
    assert rationale == "这个建议很合理"


def test_heuristic_split_accepts_english_keyword():
    # 英文标签是启发式相对 INLINE_RATIONALE 的实际增量（INLINE 只认中文三词）。
    # 返回值剥掉标签词，与中文 group(2) 口径一致。
    text, rationale = split_rationale("好的 rationale: 语气轻松")
    assert text == "好的"
    assert rationale == "语气轻松"


# ---- 误伤回归锁（对 kt 原实现的加固点）----
def test_no_false_split_on_verb_shuoming():
    """「说明」作口语动词时必须整句保留——kt 原实现的弱词会把它拦腰截断。"""
    s = "好的，我说明一下情况"
    text, rationale = split_rationale(s)
    assert text == s, f"真实候选被误拆: {text!r} / {rationale!r}"
    assert rationale is None


def test_no_false_split_on_noun_shuomingshu():
    s = "请把说明书递给我"
    text, rationale = split_rationale(s)
    assert text == s
    assert rationale is None


def test_chinese_colon_case_goes_through_inline_path():
    """中文「理由：」由既有的 INLINE_RATIONALE 先行处理，不走启发式。

    INLINE_RATIONALE（T01126）的 `(.+?)` 不限前缀长度，因此任何含「理由：」的中文串
    都会在此命中；启发式拆分的实际增量是**英文关键词**（rationale/explanation）场景。
    本用例固化这一分层行为，避免后续误以为启发式会改写 INLINE 的结果。
    """
    s = "我们今天下午三点左右到公司附近找你商量理由：路上可能有点堵车，你别着急等我"
    text, rationale = split_rationale(s)
    assert text == "我们今天下午三点左右到公司附近找你商量"
    assert rationale == "路上可能有点堵车，你别着急等我"


def test_no_split_when_keyword_not_followed_by_colon():
    # 加固点 2：「理由」后无冒号 → 视为正文用词，整句保留
    s = "你这个理由我接受不了"
    text, rationale = split_rationale(s)
    assert text == s
    assert rationale is None


# ---- 既有行为回归 ----
def test_inline_rationale_still_works():
    text, rationale = split_rationale("好的 理由：语气轻松")
    assert text == "好的"
    assert rationale == "语气轻松"


def test_dangling_pipe_still_stripped():
    text, rationale = split_rationale("厦门的海很蓝，那年在岛上的旅行时光。|")
    assert not text.endswith("|")
    assert rationale is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
