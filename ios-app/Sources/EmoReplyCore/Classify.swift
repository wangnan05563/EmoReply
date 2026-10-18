import Foundation

/// 意图与情感理解（FR-2.1 / 2.2 / 2.4）+ 风格枚举（FR-3.2）。
/// 与 m1-poc classify.py / android Classify.kt 同构的规则版基线。
public enum Classify {

    public static let intents = ["问询", "请求", "闲聊", "情绪倾诉", "通知", "冲突", "邀约"]
    public static let styles = ["正式", "亲切", "简短", "幽默"]

    // 顺序即优先级：强信号在前，通知类最靠前（FR-2.4 压误判的关键）
    static let intentRules: [(String, NSRegularExpression)] = [
        ("通知", try! NSRegularExpression(pattern:
            "验证码|校验码|取件码|快递|包裹|已签收|已发货|物流|账单|扣款|还款日|" +
            "会议提醒|日程|签到|打卡|系统通知|广告|优惠|促销|限时|余额不足|工单|" +
            "\\[(图片|文件|链接|名片)\\]")),
        ("冲突", try! NSRegularExpression(pattern: "你总是|你从来|不想再|分手|绝交|投诉|骗子|欺骗|敷衍|忍无可忍|太过分")),
        ("情绪倾诉", try! NSRegularExpression(pattern: "好累|累死|好难过|伤心|想哭|崩溃|烦死|压力大|委屈|焦虑|失眠|emo")),
        ("邀约", try! NSRegularExpression(pattern: "一起|约[个饭]|聚餐|吃饭|喝咖啡|喝奶茶|看电影|有空吗|出来玩|聚一聚")),
        ("请求", try! NSRegularExpression(pattern: "帮我|帮忙|麻烦你|拜托|能不能|可不可以|麻烦帮|劳驾|借[我个]")),
        ("问询", try! NSRegularExpression(pattern:
            "[??？]$|[??？]\\s*$|吗[??？]?$|呢[？?]?$|什么时候|几点|多少钱|多少个|" +
            "怎么|为什么|为啥|是不是|有没有|哪个|谁知道")),
    ]

    static let emotionRules: [(String, NSRegularExpression)] = [
        ("愤怒", try! NSRegularExpression(pattern: "气死|生气|无语|过分|差劲|忍无可忍|骗子|投诉|恶心|服了")),
        ("焦虑", try! NSRegularExpression(pattern: "着急|急死|担心|焦虑|来不及|怎么办|截止|延误|出事|糟了")),
        ("开心", try! NSRegularExpression(pattern: "太好了|哈哈|开心|棒|厉害|谢谢|感谢|喜欢|赞|顺利")),
    ]

    static let urgent = try! NSRegularExpression(pattern: "现在|马上|立刻|尽快|紧急|今天[之内]?必须|今晚|下班前|越快越好")

    // 强「无需回复」信号：即使意图规则漏判也拦下（压 FR-2.4 误判率）
    static let noReply = try! NSRegularExpression(pattern:
        "验证码|校验码|取件码|已签收|已发货|扣款成功|支付成功|到账|余额不足|" +
        "会议[已将]开始|日程提醒|系统自动|请勿回复|本条为系统消息|广告")

    static func matches(_ r: NSRegularExpression, _ s: String) -> Bool {
        r.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// FR-2.1：规则版消息分类。空文本归「通知」。
    public static func classifyIntent(_ text: String?) -> String {
        guard let text = text, !text.trimmingCharacters(in: .whitespaces).isEmpty else { return "通知" }
        for (intent, r) in intentRules where matches(r, text) { return intent }
        return "闲聊"
    }

    /// FR-2.2：返回 (情绪, 紧急度)，紧急度 ∈ {高, 普通}。
    public static func classifyEmotion(_ text: String?) -> (String, String) {
        let emotion = emotionRules.first { matches($0.1, text ?? "") }?.0 ?? "中性"
        let urgency = (text != nil && matches(urgent, text!)) ? "高" : "普通"
        return (emotion, urgency)
    }

    /// FR-2.4：判定是否需要回复。纯通知类 → false。
    public static func needsReply(_ text: String?, intent: String? = nil) -> Bool {
        if intent == "通知" { return false }
        if let text = text, matches(noReply, text) { return false }
        return true
    }
}
