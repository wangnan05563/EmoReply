"""安全护栏（FR-3.5 输出侧 / FR-6.4 入站侧）+ 提示节流（附录 C-2 C6）。

入站侧：对方索取验证码/密码/钱财、疑似冒充或诱导时，拒绝协助生成迎合内容，
       改为向用户给出风险提示（FR-6.4）。
输出侧：候选文本出现违法/欺骗性承诺时拦截（FR-3.5）。
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 敏感对象与索取动词需同时出现，避免「我密码忘了」这类正常叙述被误伤
_SENSITIVE = r"(验证码|校验码|动态码|短信码|支付密码|登录密码|银行卡号|身份证号)"
_REQUEST_VERB = r"(发|发给|给|告诉|提供|报一下|念一下|输入|填写|说一下|转|打)"

# 钱财类看「请求语气」或「明确收款指向」：单纯出现"转账"不能判定，
# 否则「我转账给你了」这种陈述也会被拦，误报的打扰成本比漏报更高（附录 C-2 C6）。
_MONEY_ASK = re.compile(
    r"(帮我|替我|请你|麻烦你|帮忙|能不能|可以|赶紧|马上).{0,6}(转账|汇款|打款|付款|借钱|借款|扫码)"
    r"|(转账|汇款|打款|借钱|借款).{0,12}(账户|卡号|收款|二维码)"
)

# T01245 FR-8.3：威胁/恐吓类——假冒公检法/通缉抓人/恐吓施压。这类不索取具体财物，
# 属高压力诈骗话术，需置顶提醒；单独成类便于与「请求索取」分流（避免误伤正常请求）。
# 「冻结」单独出现太宽（「不会被冻结」是正常安抚），必须接账户/资金/钱才判威胁。
_THREAT = re.compile(
    r"(?:不(?:转|配合|照做).{0,6}(?:后果|就|别怪)|告(?:发|诉)"
    r"|抓(?:你|捕)|坐牢|拘留|报警抓|起诉|法院传票|通缉|警察.{0,6}(?:上门|找.{0,2}你|抓你)"
    r"|(?:账户|资金|钱).{0,4}冻结|冻结.{0,4}(?:账户|资金|钱))"
)

_RULES: List[Tuple[str, re.Pattern]] = [
    ("otp", re.compile(_SENSITIVE + r".{0,8}" + _REQUEST_VERB + r"|" + _REQUEST_VERB + r".{0,8}" + _SENSITIVE)),
    ("money", _MONEY_ASK),
    ("impersonation", re.compile(r"(账号?异常|安全验证|涉嫌违规|点击.{0,4}链接|我是你(的)?(领导|老板|同事))")),
    ("threat", _THREAT),
]

_ADVICE: Dict[str, str] = {
    "otp": "对方在索取验证码/证件信息，任何情况下都不要提供；建议电话核实身份。",
    "money": "对方在要求转账或付款，请先通过其他渠道确认身份，谨防冒充诈骗。",
    "impersonation": "该消息疑似冒充或诱导点击，请勿点击链接，建议通过官方渠道核实。",
    "threat": "对方在威胁或恐吓施压，疑似诈骗；请勿因恐惧配合转账或提供信息，建议报警或向官方渠道核实。",
}

# 输出侧：不代写任何承诺收益、规避监管或人身攻击的内容
# T01082 验收 4：过度营销/客服套话不得出现在候选中。
# 「亲」仅在称呼位（行首/标点后跟逗号或语气符）命中，避免误伤「母亲」「亲人」等正常词；
# 「宝贝」独立成词命中——本产品输出的是替用户起草的回复，称对方「宝贝」极不专业，直接拦。
_OVERSELLING = re.compile(
    r"(?:^|[，,。！!？?\s~～])亲[，,~～！!。]|宝贝|么么哒|亲亲[~～!！]|尊敬的用户"
)

_BAD_OUTPUT: List[Tuple[str, re.Pattern]] = [
    ("deceptive", re.compile(r"(保证|包)[^，。！!？\s]{0,6}(赚|盈利|收益|回本)|稳赚不赔|内部渠道")),
    ("illegal", re.compile(r"(刷单|代开|伪造|洗钱|绕过(风控|监管))")),
    ("abusive", re.compile(r"(去死|滚|废物|垃圾东西)")),
    ("overselling", _OVERSELLING),
]


@dataclass
class RiskAssessment:
    risky: bool
    category: Optional[str] = None
    advice: Optional[str] = None


def screen_incoming(text: str) -> RiskAssessment:
    """入站消息风险判定（FR-6.4）。"""
    for category, pattern in _RULES:
        if pattern.search(text or ""):
            return RiskAssessment(True, category, _ADVICE[category])
    return RiskAssessment(False)


def screen_candidate(text: str) -> RiskAssessment:
    """候选回复内容安全判定（FR-3.5）。"""
    for category, pattern in _BAD_OUTPUT:
        if pattern.search(text or ""):
            return RiskAssessment(True, category, "该候选包含不当内容，已拦截。")
    return RiskAssessment(False)


class NoticeThrottle:
    """同一提示的节流（C6：连续两次弹同一提示体验很差）。

    护栏命中与风险提示都走这里——误打扰是本产品的核心体验风险。
    """

    def __init__(self, window: float = 60.0) -> None:
        self.window = window
        self._last: Dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < self.window:
            return False
        self._last[key] = now
        return True