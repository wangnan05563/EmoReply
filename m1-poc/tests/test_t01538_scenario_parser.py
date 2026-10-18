"""T01538：通知载荷 → 维度解析单测（微信/钉钉双平台覆盖）。

覆盖：
- 场景解析：工作/闲聊关键词命中、钉钉特有词、平局返回空串；
- 目的解析：安抚/拒绝/推进/闲聊关键词命中、平局返回空串；
- 无维度时不污染统计（返回空串）；
- 边界情况：空正文、纯表情、混合语言。
"""
from emoreply.scenario_parser import (parse_dimensions, parse_purpose,
                                      parse_scenario)


class TestParseScenario:
    def test_work_keywords(self):
        assert parse_scenario("明天开会讨论项目排期") == "工作"
        assert parse_scenario("周报还没交，deadline 是今天") == "工作"

    def test_casual_keywords(self):
        assert parse_scenario("周末去爬山吗？哈哈") == "闲聊"
        assert parse_scenario("吃了吗～最近怎么样") == "闲聊"

    def test_dingtalk_hints(self):
        # 钉钉特有词直接判工作
        assert parse_scenario("审批流卡在财务了", source="dingtalk") == "工作"
        assert parse_scenario("日程提醒：下午3点会议", source="dingtalk") == "工作"

    def test_wechat_no_special_hint(self):
        # 微信无特有场景词，靠关键词
        assert parse_scenario("红包已领取", source="wechat") == ""

    def test_tie_returns_empty(self):
        # 工作词和闲聊词数量相同 → 宁缺勿错
        assert parse_scenario("会议结束后我们去吃饭") == ""

    def test_empty_body(self):
        assert parse_scenario("") == ""
        assert parse_scenario(None) == ""  # type: ignore

    def test_emoji_only(self):
        assert parse_scenario("😂🤣😂") == ""


class TestParsePurpose:
    def test_appease(self):
        assert parse_purpose("别急，我马上处理") == "安抚"
        assert parse_purpose("不好意思，让你久等了") == "安抚"

    def test_reject(self):
        assert parse_purpose("今天没空，改天吧") == "拒绝"
        assert parse_purpose("不行，这个需求不能接") == "拒绝"

    def test_advance(self):
        assert parse_purpose("什么时候能确认一下？明天行吗") == "推进"
        assert parse_purpose("跟进一下项目进度") == "推进"

    def test_casual_purpose(self):
        assert parse_purpose("在吗？最近怎么样") == "闲聊"

    def test_tie_returns_empty(self):
        # 安抚词和推进词频次相同 → 空串（各1次）
        assert parse_purpose("别急，定一下") == ""

    def test_empty_body(self):
        assert parse_purpose("") == ""
        assert parse_purpose(None) == ""  # type: ignore


class TestParseDimensions:
    def test_wechat_work_advance(self):
        scenario, purpose = parse_dimensions(
            "明天开会确认项目排期，什么时候能定下来？", source="wechat"
        )
        assert scenario == "工作"
        assert purpose == "推进"

    def test_wechat_casual_chat(self):
        scenario, purpose = parse_dimensions(
            "周末去爬山吗？哈哈最近怎么样", source="wechat"
        )
        assert scenario == "闲聊"
        assert purpose == "闲聊"

    def test_dingtalk_work_appease(self):
        scenario, purpose = parse_dimensions(
            "审批流卡住了，别急我帮你催一下财务", source="dingtalk"
        )
        assert scenario == "工作"
        assert purpose == "安抚"

    def test_no_dimension_returns_empty_tuple(self):
        scenario, purpose = parse_dimensions("好的收到", source="wechat")
        assert scenario == ""
        assert purpose == ""

    def test_empty_body(self):
        scenario, purpose = parse_dimensions("", source="wechat")
        assert scenario == ""
        assert purpose == ""
