"""T01143：群聊侧别判定——左对齐即他人、右对齐即本人（用户反馈修正）。"""
from emoreply.wrapmerge import judge_side


def test_group_other_message_left_anchored():
    # T01143 现象样本：刘居奇（左侧气泡）被判「我」——修正后应为对方
    assert judge_side(left=250) == "对方"


def test_other_long_bubble_right_edge_beyond_old_threshold():
    # T01149 样本：对方长气泡右缘可达 1161（杨孟昭打卡消息）——
    # 旧 right 辅助信号会把这类行误判「我」，纯 left 判定修正
    assert judge_side(left=237) == "对方"


def test_my_long_wrapped_line_right_union():
    # T01129 场景：合并行 left 被噪声拉低——纯 left 判定下随对方处理；
    # 该场景由群聊噪声过滤（strip_group_noise）在侧别标注前消化，见观察记录
    assert judge_side(left=255) == "对方"


def test_my_short_message():
    assert judge_side(left=1096) == "我"


def test_other_short_message():
    assert judge_side(left=250) == "对方"


def test_other_long_message_not_misjudged():
    # 对方长文本右缘可达 ~1100，但 left 恒在左侧
    assert judge_side(left=250) == "对方"


def test_wechat_other_long_bubble_ocr_path_sample():
    # T01150 真机样本：微信「下雨怕啥的，你下地库又不是上外头上车就到家。」对方长气泡
    # l=261 r=1163——OCR 路径旧右缘阈值（>1140）误标「我」，纯 left 判定修正
    assert judge_side(left=261) == "对方"


def test_boundary_left_threshold():
    assert judge_side(left=450) == "我"
    assert judge_side(left=449) == "对方"
