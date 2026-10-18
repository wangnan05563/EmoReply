import SwiftUI
import UIKit

/// iOS 端呈现（FR-4.1/4.2 iOS 面）：无自动发送，点选候选复制到剪贴板。
/// 云端失败/未配置 → 本地模板兜底 + 明确提示（C24）。
struct ContentView: View {
    @State private var message: String = ""
    @State private var style: String = Classify.styles[1]
    @State private var candidates: [(text: String, rationale: String?)] = []
    @State private var notice: String = "粘贴对方消息后生成（点选候选即复制）"
    @State private var isGenerating = false
    @State private var copiedIndex: Int? = nil
    @State private var curContact: String = ""

    // RAG 知识库（决策③）管理区
    @State private var kbCategory = KnowledgeBase.categoryPersona
    @State private var kbContact = ""
    @State private var kbText = ""
    @State private var kbStatus = ""
    @State private var kbCount = 0
    @State private var kbEntries: [KnowledgeBase.Entry] = []

    // 配置仅存 UserDefaults（本机）；密钥不硬编码、不入仓库
    @AppStorage("cfg_baseUrl") private var baseUrl = "https://api.deepseek.com/v1"
    @AppStorage("cfg_model") private var model = "deepseek-chat"
    @AppStorage("cfg_apiKey") private var apiKey = ""
    @AppStorage("cfg_me") private var me = "我"

    /// RAG 知识库单例：Documents/kb.enc，密钥绑设备标识（identifierForVendor）
    private var knowledgeBase: KnowledgeBase {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let machineId = UIDevice.current.identifierForVendor?.uuidString ?? "unknown-device"
        return KnowledgeBase(fileURL: docs.appendingPathComponent("kb.enc"), machineId: machineId)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("对方消息") {
                    TextEditor(text: $message).frame(minHeight: 72)
                    TextField("当前联系人（可选，用于关系话术加权）", text: $curContact)
                    Picker("风格", selection: $style) {
                        ForEach(Classify.styles, id: \.self) { Text($0) }
                    }
                    Button(isGenerating ? "生成中…" : "生成回复") { generate() }
                        .disabled(isGenerating || message.trimmingCharacters(in: .whitespaces).isEmpty)
                }

                if !notice.isEmpty {
                    Section { Text(notice).font(.footnote).foregroundStyle(.secondary) }
                }

                if !candidates.isEmpty {
                    Section("候选（点选复制，自行粘贴发送）") {
                        ForEach(candidates.indices, id: \.self) { i in
                            let c = candidates[i]
                            Button {
                                UIPasteboard.general.string = c.text
                                copiedIndex = i
                                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                                    if copiedIndex == i { copiedIndex = nil }
                                }
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(copiedIndex == i ? "✓ 已复制到剪贴板" : c.text)
                                        .foregroundStyle(.primary)
                                    if let r = c.rationale {
                                        Text("为什么这样回：\(r)").font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }

                Section("RAG 知识库（人设 / 关系话术 / 平台礼仪；加密存本机）") {
                    Picker("分类", selection: $kbCategory) {
                        ForEach(KnowledgeBase.categories, id: \.self) {
                            Text(KnowledgeBase.categoryLabels[$0] ?? $0).tag($0)
                        }
                    }
                    if kbCategory == KnowledgeBase.categoryRelation {
                        TextField("联系人（关系话术必填）", text: $kbContact)
                    }
                    TextField("知识内容，如：老板周报口径以部门模板为准", text: $kbText)
                    HStack {
                        Button("保存条目") { saveKb() }
                        Spacer()
                        Text("共 \(kbCount) 条").font(.footnote).foregroundStyle(.secondary)
                        Button("清空", role: .destructive) { knowledgeBase.wipe(); refreshKb() }
                    }
                    if !kbStatus.isEmpty {
                        Text(kbStatus).font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(kbEntries, id: \.id) { e in
                        HStack {
                            Text("[\(KnowledgeBase.categoryLabels[e.category] ?? e.category)] \(e.text)")
                                .font(.caption)
                            Spacer()
                            Button("删除") { _ = knowledgeBase.remove(id: e.id); refreshKb() }
                                .font(.caption)
                        }
                    }
                }

                Section("设置（密钥仅存本机）") {
                    TextField("接口地址", text: $baseUrl).keyboardType(.URL).autocorrectionDisabled()
                    TextField("模型名", text: $model)
                    SecureField("API Key", text: $apiKey)
                    TextField("自称", text: $me)
                }
            }
            .navigationTitle("EmoReply")
            .onAppear { refreshKb() }
        }
    }

    private func refreshKb() {
        kbEntries = knowledgeBase.all()
        kbCount = kbEntries.count
    }

    private func saveKb() {
        do {
            _ = try knowledgeBase.add(category: kbCategory, text: kbText,
                                      contact: kbContact.trimmingCharacters(in: .whitespaces).isEmpty
                                          ? nil : kbContact.trimmingCharacters(in: .whitespaces))
            kbText = ""
            kbStatus = ""
            refreshKb()
        } catch {
            kbStatus = error.localizedDescription
        }
    }

    private func generate() {
        isGenerating = true
        notice = ""
        let current = EmoNotification(source: "manual", timestamp: Date().timeIntervalSince1970,
                                      body: message, sender: curContact.isEmpty ? "对方" : curContact,
                                      chat: curContact.isEmpty ? "对方" : curContact)
        // RAG（决策③）：生成前检索 top3 注入「参考资料」；无命中时不注入
        let contact = curContact.trimmingCharacters(in: .whitespaces)
        let knowledge = LexicalRetriever.search(kb: knowledgeBase, query: message,
                                                contact: contact.isEmpty ? nil : contact).map(\.text)
        let prompt = Compose.buildPrompt(current: current, history: [], me: me, style: style,
                                         knowledge: knowledge)
        let cfgReady = !baseUrl.isEmpty && !apiKey.isEmpty && !model.isEmpty

        Task {
            var list: [(String, String?)] = []
            var n: String? = nil
            if cfgReady {
                do {
                    let gen = CloudLlmGenerator(config: .init(baseUrl: baseUrl, apiKey: apiKey, model: model))
                    list = try await gen.generate(prompt).map { Sanitize.splitRationale($0) }
                } catch let e as CloudLlmGenerator.CloudLlmError {
                    n = e.userMessage
                } catch {
                    n = "云端生成失败，已降级为本地模板"
                }
            } else {
                n = "未配置云端生成，当前为本地模板（设置里填 Key 后启用）"
            }
            if list.isEmpty {
                list = StubGenerator.generate(prompt).map { ($0, nil as String?) }
            }
            await MainActor.run {
                candidates = list
                if let n = n { notice = n } else { notice = "云端生成完成" }
                isGenerating = false
            }
        }
    }
}
