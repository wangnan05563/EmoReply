import Foundation

/// 上下文拼装与提示词组装（FR-3.1 上下文策略 + 附录 C-2 C16）。
/// 与 m1-poc compose.py / android Compose.kt 同构。
public enum Compose {

    public static let defaultMaxTokens = 4096

    static let cjkRegex = try! NSRegularExpression(pattern: "[\\u3400-\\u9fff\\uf900-\\ufaff]")

    static let systemTemplate =
        "你是聊天回复助手，为「%@」起草一条可直接发送的回复。\n" +
        "约束：\n" +
        "1) 只依据给定上下文，不得编造对方未提及的事实；\n" +
        "2) 语气%@，不冒犯、不敷衍、不过度承诺；\n" +
        "3) 直接输出正文，不要使用 markdown 格式符。"

    /// 粗略估算 token：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token（安全边界，非精确计费）。
    public static func estimateTokens(_ text: String?) -> Int {
        guard let text = text, !text.isEmpty else { return 0 }
        let range = NSRange(text.startIndex..., in: text)
        let cjk = cjkRegex.numberOfMatches(in: text, range: range)
        return cjk + Int(ceil(Double(text.count - cjk) / 4.0))
    }

    public struct PromptBundle: Equatable {
        public let system: String
        public let history: [String]
        public let current: String
        public let style: String
        public let estimatedTokens: Int
        public let droppedHistory: Int
        public let truncated: Bool
    }

    static func render(_ n: EmoNotification) -> String {
        "\(n.sender ?? "对方")：\(n.body ?? "")"
    }

    /// 组装提示词并把伪历史压到 token 预算内；超预算从最旧开始丢弃（C16）。
    /// intent/emotion/urgency/relation 由 Classify/Persona 产出，非空时并入（REQ-006 / FR-5.2）。
    /// knowledge 由 RAG 检索产出（决策③），非空时并入「参考资料」；缺省不注入，保持 M1 行为不变。
    public static func buildPrompt(current: EmoNotification,
                                   history: [EmoNotification],
                                   me: String = "我",
                                   style: String = "亲切",
                                   maxTokens: Int = defaultMaxTokens,
                                   historyLimit: Int = 5,
                                   intent: String = "",
                                   emotion: String = "",
                                   urgency: String = "",
                                   relation: String = "",
                                   knowledge: [String] = []) -> PromptBundle {
        var system = String(format: systemTemplate, me, style)
        if !intent.isEmpty {
            var extra = "\n4) 消息类型：\(intent)。"
            if !emotion.isEmpty {
                extra += "对方情绪：\(emotion)"
                if !urgency.isEmpty { extra += "（紧急度：\(urgency)）" }
                extra += "。回应需贴合情绪与紧急度。"
            }
            if !relation.isEmpty { extra += "对方关系：\(relation)（语气贴合关系亲疏，不越界）。" }
            // RAG（决策③）：知识条目注入，声明不得据此编造；无命中时不注入
            if !knowledge.isEmpty {
                extra += "参考资料（已按相关性筛选；仅在与当前消息相关时参考，" +
                    "不得据此编造对方未提及的事实）：\(knowledge.joined(separator: "；"))。"
            }
            system += extra
        }
        let currentLine = render(current)
        let budget = maxTokens - estimateTokens(system) - estimateTokens(currentLine) - 512

        let lines = history.filter { ($0.body ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false }
            .map { render($0) }
            .suffix(historyLimit)

        var kept: [String] = []
        var used = 0
        for line in lines.reversed() {
            let cost = estimateTokens(line)
            if used + cost > budget { break }
            kept.insert(line, at: 0)
            used += cost
        }

        let dropped = lines.count - kept.count
        return PromptBundle(
            system: system,
            history: kept,
            current: currentLine,
            style: style,
            estimatedTokens: estimateTokens(system) + used + estimateTokens(currentLine),
            droppedHistory: dropped,
            truncated: dropped > 0,
        )
    }
}
