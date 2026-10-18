import Foundation
import CryptoKit

/// RAG 人设知识库（SRS 决策③ 2026-09-26 拍板：生成层补 RAG）。
/// 与 m1-poc rag.py / Android KnowledgeBase.kt / desktop kb.js 同构。
///
/// 条目三类：
/// - persona：用户个人事实与偏好（如「我养了两只猫」）
/// - relation：按联系人的关系话术（如「老板：周报口径以部门模板为准」）
/// - platform：平台礼仪（如「微信长语音前先发文字确认」）
///
/// 存储：CryptoKit AES-GCM 加密单 JSON 文件（与 PersonaStore 同型），
/// 密钥 = SHA256(设备标识 || 可选口令)，端侧绑定。
public final class KnowledgeBase {

    public static let categoryPersona = "persona"
    public static let categoryRelation = "relation"
    public static let categoryPlatform = "platform"
    public static let categories = [categoryPersona, categoryRelation, categoryPlatform]
    public static let categoryLabels = ["persona": "个人事实", "relation": "关系话术", "platform": "平台礼仪"]

    public struct Entry: Codable, Equatable {
        public let category: String
        public let text: String
        public let contact: String?   // relation 类可绑定联系人；其余为 nil
        public let id: String
        public let createdAt: Double
    }

    struct StoreData: Codable {
        var entries: [Entry] = []
    }

    let fileURL: URL
    let key: SymmetricKey
    private var data: StoreData

    public init(fileURL: URL, machineId: String, passphrase: String? = nil) {
        self.fileURL = fileURL
        let seed = (passphrase?.isEmpty == false) ? "\(machineId)|\(passphrase!)" : machineId
        self.key = SymmetricKey(data: SHA256.hash(data: Data(seed.utf8)))
        if let raw = try? Data(contentsOf: fileURL),
           let sealed = try? AES.GCM.SealedBox(combined: raw),
           let decrypted = try? AES.GCM.open(sealed, using: key),
           let decoded = try? JSONDecoder().decode(StoreData.self, from: decrypted) {
            self.data = decoded
        } else {
            self.data = StoreData()  // 口令不匹配/损坏 → 初始态，不崩溃（与 PersonaStore 口径一致）
        }
    }

    // ---- 持久化 ----

    private func persist() {
        guard let json = try? JSONEncoder().encode(data),
              let sealed = try? AES.GCM.seal(json, using: key) else { return }
        try? sealed.combined.write(to: fileURL)
    }

    // ---- 条目增删查 ----

    @discardableResult
    public func add(category: String, text: String, contact: String? = nil) throws -> String {
        guard KnowledgeBase.categories.contains(category) else {
            throw NSError(domain: "KnowledgeBase", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "非法知识分类：\(category)"])
        }
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else {
            throw NSError(domain: "KnowledgeBase", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "知识条目内容不能为空"])
        }
        let entry = Entry(category: category, text: t,
                          contact: category == KnowledgeBase.categoryRelation ? contact : nil,
                          id: String(UUID().uuidString.prefix(12).lowercased()),
                          createdAt: Date().timeIntervalSince1970)
        data.entries.append(entry)
        persist()
        return entry.id
    }

    @discardableResult
    public func remove(id: String) -> Bool {
        let before = data.entries.count
        data.entries.removeAll { $0.id == id }
        let changed = data.entries.count != before
        if changed { persist() }
        return changed
    }

    public func all() -> [Entry] { data.entries }

    public var count: Int { data.entries.count }

    /// FR-6.3 联动：一键清除。
    public func wipe() {
        data = StoreData()
        persist()
        try? FileManager.default.removeItem(at: fileURL)
    }
}

/// 词面检索（默认）：CJK bigram 重叠率 + 同联系人加权 0.15（次级信号）。
/// 无重叠且非当前联系人 → 不返回（防噪声注入）。与 m1-poc LexicalRetriever 同构。
public enum LexicalRetriever {

    public static let contactBoost = 0.15
    static let topK = 3

    public static func search(kb: KnowledgeBase, query: String?, contact: String?,
                              category: String? = nil, topK: Int = LexicalRetriever.topK) -> [KnowledgeBase.Entry] {
        let q = bigrams(query ?? "")
        var scored: [(Double, KnowledgeBase.Entry)] = []
        for e in kb.all() {
            if let category = category, e.category != category { continue }
            let sameContact = (e.contact?.isEmpty == false) && (contact?.isEmpty == false) && e.contact == contact
            let overlap = q.isEmpty ? 0.0 : Double(q.intersection(bigrams(e.text)).count) / Double(q.count)
            let score = overlap + (sameContact ? contactBoost : 0)
            if score <= 0 { continue }  // 无重叠且非当前联系人 → 不注入，避免噪声
            scored.append((score, e))
        }
        return scored.sorted { $0.0 > $1.0 }.prefix(topK).map { $0.1 }
    }

    /// CJK 友好 bigram 集合：过滤标点/空白，保留单字（短查询兜底）。
    /// Character.isLetter/isNumber 对汉字/数字为 true，与 Python isalnum / Kotlin isLetterOrDigit 同口径。
    static func bigrams(_ text: String) -> Set<String> {
        let arr = Array(text.filter { $0.isLetter || $0.isNumber })
        let chars = arr.map { String($0) }
        if arr.count < 2 { return Set(chars) }
        var grams = Set(chars)
        for i in 0..<(arr.count - 1) { grams.insert(String(arr[i]) + String(arr[i + 1])) }
        return grams
    }
}
