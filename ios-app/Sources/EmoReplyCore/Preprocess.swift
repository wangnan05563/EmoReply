import Foundation

/// 捕获层预处理：去重 → 指向判定 → 空载荷兜底（FR-1.3 / 1.4 / 1.5）。
/// 与 m1-poc preprocess.py 同构；全部纯函数。
public enum Preprocess {

    static let dedupeWindowSeconds = 2.0
    public static let fallbackHint = "上下文有限：请粘贴消息原文，或基于标题生成泛化建议"

    /// 归并重复通知（FR-1.4），输入需按 timestamp 升序。
    /// 判定键是「平台+会话+发送方」；窗口内仅当至少一方正文为空时才归并。
    public static func dedupe(_ notifications: [EmoNotification],
                              window: Double = dedupeWindowSeconds) -> [EmoNotification] {
        var kept: [EmoNotification] = []
        for n in notifications {
            var mergedIndex: Int? = nil
            for k in stride(from: kept.count - 1, through: 0, by: -1) {
                if n.timestamp - kept[k].timestamp > window { break }
                if (kept[k].source, kept[k].chat, kept[k].sender) != (n.source, n.chat, n.sender) { continue }
                if let kb = kept[k].body, let nb = n.body, kb != nb { continue }
                mergedIndex = k
                break
            }
            guard let idx = mergedIndex else { kept.append(n); continue }
            kept[idx].mentionsMe = kept[idx].mentionsMe || n.mentionsMe
            kept[idx].quotesMe = kept[idx].quotesMe || n.quotesMe
            if (kept[idx].body ?? "").isEmpty { kept[idx].body = n.body }
            if (kept[idx].title ?? "").isEmpty { kept[idx].title = n.title }
        }
        return kept
    }

    /// 判定消息是否指向当前用户（FR-1.3，首期仅硬规则）。
    public static func isDirectedToMe(_ n: EmoNotification) -> Bool {
        if !n.isGroup { return true }
        return n.mentionsMe || n.quotesMe
    }

    /// 对单条（已去重的）通知给出处置判定。
    public static func decide(_ n: EmoNotification) -> Decision {
        if n.isSystem {
            return Decision(action: .drop, reason: "系统消息，非用户间对话（FR-1.4）", notification: n, fallbackHint: nil)
        }
        if n.fromSelf {
            return Decision(action: .drop, reason: "自己发出的消息（FR-1.4）", notification: n, fallbackHint: nil)
        }
        if !isDirectedToMe(n) {
            return Decision(action: .drop, reason: "群聊未 @ 或引用当前用户（FR-1.3 硬规则）", notification: n, fallbackHint: nil)
        }
        if (n.body ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return Decision(action: .fallback, reason: "通知载荷为空，禁止强行生成（FR-1.5）", notification: n, fallbackHint: fallbackHint)
        }
        return Decision(action: .generate, reason: "指向当前用户且正文可用", notification: n, fallbackHint: nil)
    }

    /// 完整预处理链路：按时间排序 → 去重 → 逐条判定。
    public static func preprocess(_ notifications: [EmoNotification]) -> [Decision] {
        dedupe(notifications.sorted { $0.timestamp < $1.timestamp }).map { decide($0) }
    }
}
