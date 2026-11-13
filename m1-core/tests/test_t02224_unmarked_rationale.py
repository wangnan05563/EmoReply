# -*- coding: utf-8 -*-
"""T02224：根治「理由栏被识别成候选回复」——数量契约配对。

真机抓到的 LLM 原文（同一屏，两条 choice）：

    收到，麻烦直接把提示词发我，我看看具体场景再帮你优化 🙏
    语气简洁明了，直接推进下一步，避免无效寒暄，高效响应对方需求。
    你发我一份提示词吧，我看看怎么调整更合适 🙏
    侧重行动建议，以"我"为主语提出具体动作，既承接上文又明确告知对方需要提供什么。

模型把「内容|理由」的分隔符整个丢掉，候选与理由退化成**无标记的交替行**：
逐行拆 → 界面「候选 4 条」，点第 2 条会把分析语注入微信输入框（发送事故）。
T01126/T01136/T01554/T01720 一路都在扩「理由行」的词表，但词表只能认出带标签的行，
无标签时必然漏——这就是补丁反复复发的根因。

修复不再依赖词表，改用模型破坏不了的约束：候选条数 N 在请求时就定死了。
展开行数 > N 且全行无任何理由标记 → 按 (正文, 理由) 相邻配对，再按 N 截断。
"""
from emoreply.models import Notification
from emoreply.pipeline import ReplyPipeline
from emoreply.sanitize import expand_candidates, pair_unmarked_reasons, split_rationale

# 真机现场还原（结构与抓到的原文一致，措辞脱敏）
_RAW_ALTERNATING_4 = (
    "收到，麻烦直接把提示词发我，我看看具体场景再帮你优化 🙏\n"
    "语气简洁明了，直接推进下一步，避免无效寒暄，高效响应对方需求。\n"
    "你发我一份提示词吧，我看看怎么调整更合适 🙏\n"
    '侧重行动建议，以"我"为主语提出具体动作，既承接上文又明确告知对方需要提供什么。'
)
# 同屏另一条 choice：两条候选、无理由、空行分隔（真机同样抓到过）
_RAW_TWO_CANDIDATES = (
    "哈喽，你是想发一份相关的提示词给我看看吗？麻烦发文本过来，我帮你瞅瞅怎么优化更顺手 🙏\n"
    "\n"
    "收到啦，是不是想让我优化那份提示词呀？方便把具体内容贴一下吗，我看看怎么调整更自然 🙏"
)


def _notice(body: str = "南哥提的那份提示词要怎么改？") -> Notification:
    return Notification(source="manual", chat="c1", sender="小李", body=body, timestamp=1.0)


def _run(chunks, multi_tone=False, **kw):
    pipeline = ReplyPipeline(generator=lambda prompt: list(chunks), **kw)
    return pipeline.handle(_notice(), [], multi_tone=multi_tone).candidates


# ---- 主修复：无标记交替行按数量契约配对 ----

def test_unmarked_alternating_lines_pair():
    """真机现场：1 段 4 行（2 候选 + 2 理由）→ 2 条候选，理由各归其主。"""
    cands = _run([_RAW_ALTERNATING_4])
    assert len(cands) == 2, f"应只剩 2 条候选，实际 {len(cands)} 条"
    assert cands[0].text.startswith("收到，麻烦直接把提示词发我")
    assert cands[1].text.startswith("你发我一份提示词吧")
    # 理由不再作为独立候选出现，而是挂到对应候选上
    assert cands[0].rationale and cands[0].rationale.startswith("语气简洁明了")
    assert cands[1].rationale and cands[1].rationale.startswith("侧重行动建议")
    assert not any("避免无效寒暄" in c.text for c in cands)


def test_cross_chunk_alternating_pairs_too():
    """兼容服务按 n 拆成两段、每段「候选+理由」：同样只出 2 条候选。"""
    lines = _RAW_ALTERNATING_4.split("\n")
    cands = _run(["\n".join(lines[:2]), "\n".join(lines[2:])])
    assert len(cands) == 2
    assert cands[0].rationale is not None and cands[1].rationale is not None


def test_three_pairs_capped_at_expected():
    """模型多给了（3 候选 + 3 理由，N=2）→ 配对后按 N 截断，不再堆候选。"""
    raw = _RAW_ALTERNATING_4 + "\n那我先按这版改改看\n理由是降低改动成本，方便对方快速验证。"
    cands = _run([raw])
    assert len(cands) == 2


def test_english_unmarked_pair():
    # 请求 1 条时 2 行必然含一行非候选（鸽笼），配成 (正文, 理由)
    cands = _run(["Sure, send me the prompt\nExplains the next step clearly"], expected_candidates=1)
    assert len(cands) == 1
    assert cands[0].text == "Sure, send me the prompt"
    assert cands[0].rationale == "Explains the next step clearly"


# ---- 不越界：数量对不上 / 已有标记时一律不动，仍走词表路径 ----

def test_two_candidates_without_rationale_not_paired():
    """真机另一形态：2 行 = 2 候选、无理由（行数未超过 N）→ 不得配对吞掉一条。"""
    cands = _run([_RAW_TWO_CANDIDATES])
    assert len(cands) == 2
    assert all(c.rationale is None for c in cands)


def test_marked_lines_keep_lexical_path():
    """带 | 或「理由：」标记时按既有规则解析，数量契约不介入。"""
    cands = _run(["好呀周末一起去|答应得爽快\n下次吧|这周约满了"])
    assert [c.text for c in cands] == ["好呀周末一起去", "下次吧"]
    assert cands[1].rationale == "这周约满了"


def test_odd_line_count_left_untouched():
    """行数为奇无法对齐 → 宁可不配（保持旧行为），不做半截配对。"""
    out = pair_unmarked_reasons(["甲", "乙", "丙"], 2)
    assert out == ["甲", "乙", "丙"]


def test_expected_zero_disables_pairing():
    """N 未知（0）时行为与修复前完全一致。"""
    lines = expand_candidates(_RAW_ALTERNATING_4)
    assert len(lines) == 4
    assert pair_unmarked_reasons(lines, 0) == lines


def test_pairing_does_not_merge_duplicate_or_meta_text():
    # 配对只是补分隔符，清洗/元话语/去重闸仍由管线负责：元话语行被收进理由，不落成候选
    cands = _run(["收到，我看看\n请提供聊天记录这样我才能帮你回复"], expected_candidates=1)
    assert len(cands) == 1
    assert cands[0].rationale == "请提供聊天记录这样我才能帮你回复"


def test_field_name_prefix_stripped():
    """真机形态：模型把契约字段名「内容」当行首标签吐回来 → 剥净，不随候选注入聊天框。"""
    cands = _run(["内容：好的，我按这版先改改看|边界保持：改动成本最低"])
    assert len(cands) == 1
    assert cands[0].text == "好的，我按这版先改改看"
    assert cands[0].rationale == "边界保持：改动成本最低"


# ---- 误伤回归锁 ----

def test_t01554_misfire_regression_lock_still_holds():
    """T01554 锁：口语句子不被启发式砍断；本修复不得引入新的截断或吞候选。"""
    pairs = [split_rationale(c) for c in expand_candidates("好的，我说明一下情况\n下次再聊")]
    assert len(pairs) == 2
    assert pairs[0][0] == "好的，我说明一下情况"
    cands = _run(["好的，我说明一下情况\n下次再聊"], expected_candidates=1)
    assert len(cands) == 1
    assert cands[0].text == "好的，我说明一下情况"
    assert cands[0].rationale == "下次再聊"


def test_t01720_marked_continuation_still_merged():
    """T01720 锁：带「理由：」标签的续行照旧接回，不因此多出候选。"""
    raw = ("1. 哈哈被你发现了，那我下次一定早起等你😄\n理由：以自嘲化解调侃，拉近距离。\n"
           "2. 那咱定在10点半出发吧，刚好赶得上😂\n理由：直接给出可行的行动方案。\n"
           "3. 哈哈行，那你们先去，我下次一定不鸽\n理由：顺水推舟接受调侃，姿态放松。")
    cands = _run([raw], expected_candidates=3)
    assert len(cands) == 3
    assert cands[0].rationale.startswith("以自嘲化解调侃")
    assert all(not c.text.startswith("理由") for c in cands)


def test_multi_tone_pairs_with_three_variants():
    """多风格并行一次出 3 条 → 数量基准随档位数走（6 行配成 3 条并打上档位标签）。"""
    raw = "\n".join([
        "好的，我先按这版试下", "克制档表达，不额外加码",
        "行，那就这么定", "中性确认，给对方确定感",
        "太好了，那我马上改", "活泼回应，情绪外露",
    ])
    cands = _run([raw], multi_tone=True)
    assert len(cands) == 3
    assert [c.rationale.split("档")[0] + "档" for c in cands] == ["克制档", "中性档", "活泼档"]
