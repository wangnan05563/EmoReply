"""T01538：通知载荷 → 场景/目的维度解析（与 T01244 三维筛选同构）。

背景：T01350 采纳率闭环只在手动生成链路生效，通知链路因缺维度上下文而空转。
本模块从通知载荷（平台、会话名、消息文本）自动推断 scenario/purpose，复用
T01244 的枚举口径（SCENARIOS/PURPOSES），使两条链路共用同一套降档判定。

设计原则：
- 规则优先：关键词匹配 + 平台特征（微信/钉钉语义差异）；
- 宁缺勿错：解析不出时返回空串（不写入统计，留日志排查）；
- 可扩展：新增平台/场景只需扩展映射表，不改核心逻辑。
"""
from typing import Optional, Tuple

# 复用 T01244 枚举（硬编码，避免循环导入 compose.py）
SCENARIOS = ("", "闲聊", "工作")
PURPOSES = ("", "安抚", "拒绝", "推进", "闲聊")

# ===== 关键词映射表（小写匹配，覆盖常见表达）=====

# 场景关键词：从消息正文推断「闲聊」vs「工作」
SCENARIO_KEYWORDS = {
    "工作": [
        "会议", "汇报", "审批", "流程", "项目", "需求", "上线", "排期", "deadline",
        "ddl", "kpi", "okr", "周报", "日报", "考勤", "加班", "出差", "报销",
        "合同", "报价", "预算", "客户", "甲方", "乙方", "交付", "验收",
    ],
    "闲聊": [
        "吃饭", "周末", "爬山", "电影", "游戏", "旅游", "聚会", "生日", "结婚",
        "哈哈", "嘿嘿", "～", "早安", "晚安", "吃了吗", "在吗",
    ],
}

# 目的关键词：从消息正文推断用户意图
PURPOSE_KEYWORDS = {
    "安抚": ["别急", "没事", "放心", "理解", "抱歉", "不好意思", "辛苦", "谢谢"],
    "拒绝": ["不行", "不能", "不了", "没空", "不方便", "下次", "改天", "算了"],
    "推进": ["什么时候", "何时", "几点", "明天", "下周", "确认", "定一下", "安排", "跟进"],
    "闲聊": ["在吗", "吃了吗", "最近", "怎么样", "如何", "好吗", "吧", "呀", "哈"],
}

# 平台特征词（钉钉特有 vs 微信特有）
DINGTALK_HINTS = ["钉邮", "日程", "待办", "审批流", "考勤打卡", "日志"]
WECHAT_HINTS = ["朋友圈", "视频号", "小程序", "公众号", "红包", "转账"]


def parse_scenario(body: Optional[str], source: str = "") -> str:
    """从消息正文推断场景（闲聊/工作），未知返回空串。

    策略：
    1. 钉钉特有词 → 工作；
    2. 关键词计数：工作词 > 闲聊词 → 工作，反之闲聊；
    3. 平局或无命中 → 空串（宁缺勿错）。
    """
    if not body:
        return ""

    text = body.lower()

    # 钉钉特有词直接判工作
    if source == "dingtalk" and any(h in body for h in DINGTALK_HINTS):
        return "工作"

    # 关键词计数
    work_count = sum(1 for kw in SCENARIO_KEYWORDS["工作"] if kw in text)
    casual_count = sum(1 for kw in SCENARIO_KEYWORDS["闲聊"] if kw in text)

    if work_count > casual_count and work_count > 0:
        return "工作"
    if casual_count > work_count and casual_count > 0:
        return "闲聊"

    return ""


def parse_purpose(body: Optional[str]) -> str:
    """从消息正文推断目的（安抚/拒绝/推进/闲聊），未知返回空串。

    策略：最高频关键词对应的目的；平局或无命中 → 空串。
    """
    if not body:
        return ""

    text = body.lower()
    counts = {}
    for purpose, kws in PURPOSE_KEYWORDS.items():
        c = sum(1 for kw in kws if kw in text)
        if c > 0:
            counts[purpose] = c

    if not counts:
        return ""

    # 取最高频
    max_purpose = max(counts, key=counts.get)
    max_count = counts[max_purpose]

    # 平局检测：多个目的频次相同 → 返回空串
    tied = [p for p, c in counts.items() if c == max_count]
    if len(tied) > 1:
        return ""

    return max_purpose


def parse_dimensions(body: Optional[str], source: str = "") -> Tuple[str, str]:
    """一次性解析场景+目的，返回 (scenario, purpose)。

    调用方（NotifyCaptureService / pipeline）拿到后可直接传给 ReplyPipeline.handle()。
    两者皆空时调用方应跳过闭环统计（宁缺勿错）。

    会话名（原 chat 形参）已从签名移除：两个解析器都只看正文与平台，会话名从不
    参与判定（Kotlin 侧 ScenarioParser.parseDimensions 的同名形参同样是死参）。
    """
    scenario = parse_scenario(body, source)
    purpose = parse_purpose(body)
    return scenario, purpose
