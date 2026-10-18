import Foundation
import CryptoKit

/// 人设 / 联系人关系 / 历史摘要的本地加密存储（FR-5.1 / FR-5.2）。
///
/// 与 m1-poc persona.py 同构；iOS 上以 CryptoKit AES-GCM 加密单 JSON 文件落盘
/// （数据量小，无需 SQLite），密钥 = SHA256(设备标识 || 可选口令)。
/// 生产建议升级为 Keychain 保存密钥 + 文件保护（NSFileProtectionComplete）。
public final class PersonaStore {

    public struct Persona: Codable, Equatable {
        public var me: String = "我"
        public var catchphrases: [String] = []
        public var banned: [String] = []
        public var defaultStyle: String = "亲切"
    }

    public struct PromptContext {
        public let me: String
        public let relation: String
        public let style: String
        public let banned: [String]
    }

    struct HistoryEntry: Codable {
        var ts: Double
        var summary: String
    }

    struct StoreData: Codable {
        var persona: Persona = Persona()
        var contacts: [String: String] = [:]
        var history: [String: [HistoryEntry]] = [:]
    }

    public static let relations = ["家人", "好友", "同事", "客户", "陌生"]
    public static let defaultRelation = "陌生"

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
            self.data = StoreData()
        }
    }

    // ---- 持久化 ----

    private func persist() {
        guard let json = try? JSONEncoder().encode(data),
              let sealed = try? AES.GCM.seal(json, using: key) else { return }
        try? sealed.combined.write(to: fileURL)
    }

    // ---- FR-5.1 人设/偏好 ----

    public func setPersona(me: String? = nil, catchphrases: [String]? = nil,
                           banned: [String]? = nil, defaultStyle: String? = nil) {
        if let v = me { data.persona.me = v }
        if let v = catchphrases { data.persona.catchphrases = v }
        if let v = banned { data.persona.banned = v }
        if let v = defaultStyle { data.persona.defaultStyle = v }
        persist()
    }

    public func getPersona() -> Persona { data.persona }

    // ---- FR-5.2 联系人关系 ----

    public func setRelation(_ name: String, _ relation: String) {
        precondition(PersonaStore.relations.contains(relation), "非法关系枚举：\(relation)")
        data.contacts[name] = relation
        persist()
    }

    /// 未标注 → 默认「陌生」且不影响生成（FR-2.3 验收）。
    public func getRelation(_ name: String?) -> String {
        guard let name = name, !name.isEmpty else { return PersonaStore.defaultRelation }
        return data.contacts[name] ?? PersonaStore.defaultRelation
    }

    // ---- FR-5.2 历史摘要 ----

    public func addHistory(_ contact: String, _ summary: String) {
        let s = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return }
        var list = data.history[contact] ?? []
        list.append(HistoryEntry(ts: Date().timeIntervalSince1970, summary: s))
        data.history[contact] = list
        persist()
    }

    /// 最新优先、限量返回。
    public func history(_ contact: String, limit: Int = 5) -> [String] {
        let list = data.history[contact] ?? []
        return Array(list.suffix(limit).map { $0.summary }.reversed())
    }

    // ---- 聚合与过滤 ----

    public func promptContext(_ contact: String?) -> PromptContext {
        PromptContext(me: data.persona.me, relation: getRelation(contact),
                      style: data.persona.defaultStyle, banned: data.persona.banned)
    }

    /// FR-5.1 输出侧禁语过滤：候选含禁语 → 拦下。
    public func violatesBanned(_ text: String, _ banned: [String]) -> Bool {
        banned.contains { !$0.isEmpty && text.contains($0) }
    }

    // ---- FR-6.3 联动 ----

    public func wipe() {
        data = StoreData()
        persist()
        try? FileManager.default.removeItem(at: fileURL)
    }
}
