"""T01145：读屏扩展多屏——批次合并去重（与 kt mergeScreenBatches 同构）。"""
from emoreply.preprocess import merge_screen_batches


def test_overlapping_batches_dedup():
    # 上滑后底部与上一屏顶部重叠（夏思铭第26天在两批都出现）
    b1 = ["对方：夏思铭三年级蓄力计划,坚持阅读第25天",
          "对方：夏思铭三年级蓄力计划,坚持阅读第26天"]
    b2 = ["对方：夏思铭三年级蓄力计划,坚持阅读第26天",
          "对方：刘居奇三年级蓄力计划,坚持阅读第26天"]
    assert merge_screen_batches([b1, b2]) == [
        "对方：夏思铭三年级蓄力计划,坚持阅读第25天",
        "对方：夏思铭三年级蓄力计划,坚持阅读第26天",
        "对方：刘居奇三年级蓄力计划,坚持阅读第26天",
    ]


def test_three_batches_order_preserved():
    b1 = ["对方：a", "我：b"]
    b2 = ["我：b", "对方：c"]
    b3 = ["对方：c", "我：d"]
    assert merge_screen_batches([b1, b2, b3]) == ["对方：a", "我：b", "对方：c", "我：d"]


def test_empty_batches():
    assert merge_screen_batches([[], [], ["对方：x"]]) == ["对方：x"]
    assert merge_screen_batches([]) == []


def test_duplicates_within_batch_also_dedup():
    b1 = ["对方：同文", "对方：同文"]
    assert merge_screen_batches([b1]) == ["对方：同文"]
