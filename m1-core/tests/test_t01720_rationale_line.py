"""T01720：理由换行续行被当独立候选的修复。

真机现场（截图 OCR 复核）：LLM 把理由换行输出（「1. 候选正文」+ 换行「理由：xxx」），
expand_candidates 逐行拆分后理由行被 _strip_option_prefix 剥掉前缀成为独立候选——
「候选 6 条」= 3 真候选 + 3 理由行，且全部 rationale=None。

修复：expand_candidates 内 _merge_rationale_lines 把「理由：xxx」续行接回上一条
候选行（补 | 交给 split_rationale 统一解析）；无上一行则丢弃。
"""
from emoreply.sanitize import expand_candidates, split_rationale

# T01720 真机现场还原（截图同款对话：早餐/出发时间）
_RAW_NUMBERED_WITH_RATIONALE_LINES = """1. 哈哈被你发现了，那我下次一定早起等你😄
理由：以自嘲化解对方“二货”的调侃，拉近距离并表达未来愿意配合的态度。
2. 那咱定在10点半出发吧，刚好能赶上11点前，你负责导航我负责认路😂
理由：避开情绪调侃，直接给出具体可行的行动方案，确保行程顺利。
3. 哈哈行，那你们先去，我下次一定不鸽
理由：顺水推舟接受调侃，同时给出下次的承诺，姿态放松。"""


def _pairs(raw):
    """expand → split 全链路，返回 (text, rationale) 对列表。"""
    return [split_rationale(c) for c in expand_candidates(raw)]


# ---- 主修复：理由续行接回上一条候选 ----
def test_numbered_lines_with_rationale_continuation():
    pairs = _pairs(_RAW_NUMBERED_WITH_RATIONALE_LINES)
    assert len(pairs) == 3, f"应为 3 条候选，实际 {len(pairs)} 条：{pairs}"
    texts = [t for t, _ in pairs]
    assert texts[0] == "哈哈被你发现了，那我下次一定早起等你😄"
    assert texts[1] == "那咱定在10点半出发吧，刚好能赶上11点前，你负责导航我负责认路😂"
    assert texts[2] == "哈哈行，那你们先去，我下次一定不鸽"
    # 理由归位（不含「理由：」标签，也不作为候选正文出现）
    assert pairs[0][1] == "以自嘲化解对方“二货”的调侃，拉近距离并表达未来愿意配合的态度。"
    assert pairs[1][1] == "避开情绪调侃，直接给出具体可行的行动方案，确保行程顺利。"
    assert pairs[2][1] == "顺水推舟接受调侃，同时给出下次的承诺，姿态放松。"


def test_bullet_rationale_continuation():
    raw = "1. 周末我组织一局\n- 理由：主动揽局显得有担当\n2. 那我带饮料\n- 理由：小成本高回报"
    pairs = _pairs(raw)
    assert len(pairs) == 2
    assert pairs[0] == ("周末我组织一局", "主动揽局显得有担当")
    assert pairs[1] == ("那我带饮料", "小成本高回报")


def test_rationale_line_without_numbered_candidates():
    raw = "好啊那说定了\n理由：干脆利落地答应，给对方确定感"
    pairs = _pairs(raw)
    assert pairs == [("好啊那说定了", "干脆利落地答应，给对方确定感")]


def test_dangling_pipe_then_rationale_line():
    # 「正文|」+ 换行「理由：xxx」→ 不重复补管道符，直接续接
    raw = "好呀周末一起去|\n理由：答应得爽快"
    pairs = _pairs(raw)
    assert pairs == [("好呀周末一起去", "答应得爽快")]


def test_two_rationale_lines_for_same_candidate():
    # 极端变体：一个候选挂两条理由续行 → 第二条以「；」续接进理由段
    raw = "好呀周末一起去\n理由：答应得爽快\n原因：对方等这句话很久了"
    pairs = _pairs(raw)
    assert len(pairs) == 1
    assert pairs[0][0] == "好呀周末一起去"
    assert pairs[0][1] == "答应得爽快；对方等这句话很久了"


def test_leading_rationale_line_dropped():
    # 理由行在最前（无上一行）→ 丢弃，不产出「理由只剩内容」的幽灵候选
    raw = "理由：没有候选只有这句\n1. 真候选在这里"
    pairs = _pairs(raw)
    assert pairs == [("真候选在这里", None)]


def test_all_rationale_lines_no_candidates():
    raw = "理由：只有理由\n原因：没有候选"
    assert expand_candidates(raw) == []


def test_english_rationale_continuation():
    raw = "Sounds good, see you at 10:30\nRationale: confirms plan with a concrete time"
    pairs = _pairs(raw)
    assert len(pairs) == 1
    assert pairs[0][0] == "Sounds good, see you at 10:30"
    assert pairs[0][1] == "confirms plan with a concrete time"


# ---- 误伤回归锁：常规正文/既有路径不受影响 ----
def test_plain_body_starting_with_cause_word_not_merged():
    # 「原因」后不是紧跟冒号 → 不是理由续行，是正常候选
    raw = "1. 原因很简单，我睡过头了\n2. 下次一定早起"
    pairs = _pairs(raw)
    assert len(pairs) == 2
    assert pairs[0][0] == "原因很简单，我睡过头了"
    assert pairs[1][0] == "下次一定早起"


def test_normal_pipe_format_unaffected():
    raw = "1. 好呀一起去|答应得爽快\n2. 下次吧|这周约满了"
    pairs = _pairs(raw)
    assert pairs == [("好呀一起去", "答应得爽快"), ("下次吧", "这周约满了")]


def test_single_line_inline_rationale_unaffected():
    # T01126 内联结构（T01554 收紧后口径）：单行不走 expand 合并，由 split_rationale 处理
    t, r = split_rationale("好的，这个周末一定到 理由：给对方确定感")
    assert t == "好的，这个周末一定到"
    assert r == "给对方确定感"


def test_t01554_misfire_regression_lock_still_holds():
    # T01554 误伤锁：口语句子不被启发式砍断；本修复不得引入新截断
    pairs = _pairs("好的，我说明一下情况\n下次再聊")
    assert len(pairs) == 2
    assert pairs[0][0] == "好的，我说明一下情况"
    assert pairs[1][0] == "下次再聊"


def test_multiline_pipe_format_unaffected():
    # 既有「内容|理由」逐行格式 + 悬空管道合并（T01136 场景）不回归
    raw = "好呀一起|爽快\n好呀一起去|"
    pairs = _pairs(raw)
    assert pairs[0] == ("好呀一起", "爽快")
    assert pairs[1][0] == "好呀一起去"
