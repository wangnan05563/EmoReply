"""用户反馈（2026-09-27 截图）：群消息读屏解析出现对象错误。

真机样本（微信群「2024级阳光二班」，读屏结果）：
    我：公23条新消息          ← UI 悬浮条「23条新消息」+ OCR 误读，被侧别误判为「我」
    对方：夏思铭妈妈          ← 发送者昵称行被当成消息
    对方：夏思铭三年级蓄力计划,坚持阅读第25天,...
    对方：刘居奇              ← 昵称「刘居奇」（正文本身以同名开头，双份）
    对方：刘居奇三年级蓄力计划,...
    我：(田+0)                ← 表情图标 OCR 误读
"""
from emoreply.preprocess import strip_group_noise

SAMPLE = [
    "公23条新消息",
    "夏思铭妈妈",
    "夏思铭三年级蓄力计划,坚持阅读第25天,开启主动阅读PK赛",
    "夏思铭妈妈",
    "夏思铭三年级蓄力计划,坚持阅读第26天,开启主动阅读PK赛",
    "刘居奇妈妈",
    "刘居奇",
    "刘居奇三年级蓄力计划,坚持阅读第26天,开启主动阅读PK赛",
    "(田+0)",
]


def test_new_message_badge_dropped():
    out = strip_group_noise(SAMPLE)
    assert not any("条新消息" in l for l in out)


def test_bracket_icon_dropped():
    out = strip_group_noise(SAMPLE)
    assert not any(l.startswith("(") and l.endswith(")") for l in out)


def test_nickname_lines_merged():
    out = strip_group_noise(SAMPLE)
    # 昵称行（夏思铭妈妈/刘居奇妈妈/刘居奇）全部消失，正文保留
    assert "夏思铭妈妈" not in out
    assert "刘居奇妈妈" not in out
    assert "刘居奇" not in out
    assert any(l.startswith("夏思铭三年级蓄力计划") for l in out)
    assert any(l.startswith("刘居奇三年级蓄力计划") for l in out)


def test_full_sample():
    out = strip_group_noise(SAMPLE)
    assert out == [
        "夏思铭三年级蓄力计划,坚持阅读第25天,开启主动阅读PK赛",
        "夏思铭三年级蓄力计划,坚持阅读第26天,开启主动阅读PK赛",
        "刘居奇三年级蓄力计划,坚持阅读第26天,开启主动阅读PK赛",
    ]


def test_normal_short_messages_not_killed():
    # 短消息在昵称合并规则下不得误杀（下一行非其前缀）
    lines = ["儿子睡着了", "去旅游呀", "你是真人吗", "大哥"]
    assert strip_group_noise(lines) == lines


def test_normal_reply_with_punct_not_treated_as_nickname():
    # 含标点的短行不判昵称（「请告诉我地址，谢谢」下一行「请…」也不匹配前缀）
    lines = ["请告诉我地址", "请回复我"]
    assert strip_group_noise(lines) == ["请告诉我地址", "请回复我"]


def test_empty_and_mixed():
    assert strip_group_noise([]) == []
    assert strip_group_noise(["23条新消息", "儿子睡着了"]) == ["儿子睡着了"]
