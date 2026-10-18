import Foundation

// MARK: - 数据模型（与 m1-poc models.py / android Models.kt 同构，FR-1.2 字段对齐）

/// 一次系统通知（捕获层输出，只读，不触碰聊天数据库）。
public struct EmoNotification: Codable, Equatable {
    public var source: String
    public var timestamp: Double
    public var body: String?
    public var sender: String?
    public var chat: String?
    public var isGroup: Bool
    public var mentionsMe: Bool
    public var quotesMe: Bool
    public var fromSelf: Bool
    public var isSystem: Bool
    public var title: String?

    public init(source: String, timestamp: Double, body: String? = nil, sender: String? = nil,
                chat: String? = nil, isGroup: Bool = false, mentionsMe: Bool = false,
                quotesMe: Bool = false, fromSelf: Bool = false, isSystem: Bool = false,
                title: String? = nil) {
        self.source = source; self.timestamp = timestamp; self.body = body
        self.sender = sender; self.chat = chat; self.isGroup = isGroup
        self.mentionsMe = mentionsMe; self.quotesMe = quotesMe
        self.fromSelf = fromSelf; self.isSystem = isSystem; self.title = title
    }
}

public enum Action: String, Equatable { case generate, fallback, drop }

/// 预处理链路对单条通知的判定结果。
public struct Decision: Equatable {
    public let action: Action
    public let reason: String
    public let notification: EmoNotification
    public let fallbackHint: String?
}

/// 一条待注入输入框的候选回复。
public struct Candidate: Equatable {
    public let text: String
    public let style: String
    public let rationale: String?
}

/// 风险判定结果。
public struct RiskAssessment: Equatable {
    public let risky: Bool
    public let category: String?
    public let advice: String?
}
