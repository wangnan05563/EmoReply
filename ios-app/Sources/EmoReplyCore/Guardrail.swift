import Foundation

/// 安全护栏（FR-3.5 输出侧 / FR-6.4 入站侧）+ 提示节流（C6）。
/// 与 m1-poc guardrail.py / android Guardrail.kt 同构。
public enum Guardrail {

    static let sensitive = "(验证码|校验码|动态码|短信码|支付密码|登录密码|银行卡号|身份证号)"
    static let requestVerb = "(发|发给|给|告诉|提供|报一下|念一下|输入|填写|说一下|转|打)"
    static let moneyAsk = try! NSRegularExpression(
        pattern: "(帮我|替我|请你|麻烦你|帮忙|能不能|可以|赶紧|马上).{0,6}(转账|汇款|打款|付款|借钱|借款|扫码)" +
            "|(转账|汇款|打款|借钱|借款).{0,12}(账户|卡号|收款|二维码)")

    static let incomingRules: [(String, NSRegularExpression)] = [
        ("otp", try! NSRegularExpression(pattern: sensitive + ".{0,8}" + requestVerb + "|" + requestVerb + ".{0,8}" + sensitive)),
        ("money", moneyAsk),
        ("impersonation", try! NSRegularExpression(pattern: "(账号?异常|安全验证|涉嫌违规|点击.{0,4}链接|我是你(的)?(领导|老板|同事))")),
    ]

    static let advice: [String: String] = [
        "otp": "对方在索取验证码/证件信息，任何情况下都不要提供；建议电话核实身份。",
        "money": "对方在要求转账或付款，请先通过其他渠道确认身份，谨防冒充诈骗。",
        "impersonation": "该消息疑似冒充或诱导点击，请勿点击链接，建议通过官方渠道核实。",
    ]

    static let badOutput: [(String, NSRegularExpression)] = [
        ("deceptive", try! NSRegularExpression(pattern: "(保证|包)[^，。！!？\\s]{0,6}(赚|盈利|收益|回本)|稳赚不赔|内部渠道")),
        ("illegal", try! NSRegularExpression(pattern: "(刷单|代开|伪造|洗钱|绕过(风控|监管))")),
        ("abusive", try! NSRegularExpression(pattern: "(去死|滚|废物|垃圾东西)")),
        // T01082 验收 4：过度营销/客服套话拦截（「亲」仅称呼位命中，不误伤「亲人/母亲」）
        ("overselling", try! NSRegularExpression(pattern: "(?:^|[，,。！!？?\\s~～])亲[，,~～！!。]|宝贝|么么哒|亲亲[~～!！]|尊敬的用户")),
    ]

    static func matches(_ r: NSRegularExpression, _ s: String) -> Bool {
        r.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// 入站消息风险判定（FR-6.4）。
    public static func screenIncoming(_ text: String?) -> RiskAssessment {
        let t = text ?? ""
        for (category, r) in incomingRules where matches(r, t) {
            return RiskAssessment(risky: true, category: category, advice: advice[category])
        }
        return RiskAssessment(risky: false, category: nil, advice: nil)
    }

    /// 候选回复内容安全判定（FR-3.5）。
    public static func screenCandidate(_ text: String?) -> RiskAssessment {
        let t = text ?? ""
        for (category, r) in badOutput where matches(r, t) {
            return RiskAssessment(risky: true, category: category, advice: "该候选包含不当内容，已拦截。")
        }
        return RiskAssessment(risky: false, category: nil, advice: nil)
    }

    /// 同一提示的节流（C6：连续两次弹同一提示体验很差）。
    public final class NoticeThrottle {
        let windowMs: Double
        private var last: [String: Double] = [:]

        public init(windowMs: Double = 60_000) { self.windowMs = windowMs }

        public func allow(_ key: String, now: Double) -> Bool {
            if let prev = last[key], now - prev < windowMs { return false }
            last[key] = now
            return true
        }
    }
}
