import XCTest
@testable import EmoReplyCore

/// 移植 m1-poc 验收测试的关键用例（24+30+12 项中的核心行为）。
/// 在 Mac 上 `swift test` 即可全量回归；iOS 真机侧另需 UI 实测。
final class EmoReplyCoreTests: XCTestCase {

    func notif(_ body: String?, sender: String? = "对方", timestamp: Double = 1000,
               isGroup: Bool = false, mentionsMe: Bool = false, quotesMe: Bool = false,
               isSystem: Bool = false, fromSelf: Bool = false) -> EmoNotification {
        EmoNotification(source: "wechat", timestamp: timestamp, body: body, sender: sender,
                        chat: sender, isGroup: isGroup, mentionsMe: mentionsMe, quotesMe: quotesMe,
                        fromSelf: fromSelf, isSystem: isSystem)
    }

    // ---- FR-1.4 去重 ----

    func testDedupeMergesEmptyBodyFollowUp() {
        let first = notif("你好呀", timestamp: 1000)
        let second = notif("", timestamp: 1001)
        let merged = Preprocess.dedupe([first, second])
        XCTAssertEqual(merged.count, 1)
        XCTAssertEqual(merged[0].body, "你好呀")
    }

    func testDedupeKeepsDistinctBodies() {
        let merged = Preprocess.dedupe([notif("第一条", timestamp: 1000), notif("第二条", timestamp: 1001)])
        XCTAssertEqual(merged.count, 2)
    }

    // ---- FR-1.3 指向判定 ----

    func testDirectedToMe() {
        XCTAssertTrue(Preprocess.isDirectedToMe(notif("私聊内容")))
        XCTAssertFalse(Preprocess.isDirectedToMe(notif("群聊闲聊", isGroup: true)))
        XCTAssertTrue(Preprocess.isDirectedToMe(notif("@我 的群消息", isGroup: true, mentionsMe: true)))
    }

    // ---- FR-1.4 过滤 + FR-1.5 兜底 ----

    func testDecideDropSystemAndSelf() {
        XCTAssertEqual(Preprocess.decide(notif("系统通知", isSystem: true)).action, .drop)
        XCTAssertEqual(Preprocess.decide(notif("我自己发的", fromSelf: true)).action, .drop)
    }

    func testDecideFallbackOnEmptyBody() {
        let d = Preprocess.decide(notif("   "))
        XCTAssertEqual(d.action, .fallback)
        XCTAssertNotNil(d.fallbackHint)
    }

    // ---- C16 token 截断：最旧优先丢弃 ----

    func testPromptTruncatesOldestFirst() {
        var history: [EmoNotification] = []
        for i in 0..<10 {
            history.append(notif(String(repeating: "长", count: 200), sender: "u\(i)", timestamp: Double(i)))
        }
        let bundle = Compose.buildPrompt(current: notif("当前消息"), history: history, maxTokens: 1200)
        XCTAssertTrue(bundle.truncated)
        XCTAssertEqual(bundle.droppedHistory, 5)   // historyLimit=5，超预算部分丢弃
        XCTAssertEqual(bundle.history.count, 1)    // 只留最近一条
    }

    // ---- C3/C25 清洗 ----

    func testSanitizeStripsMarkdownAndHtml() {
        XCTAssertEqual(Sanitize.cleanCandidate("**好的**，<p>收到</p>"), "好的，收到")
    }

    // ---- FR-3.3 解释句解析 ----

    func testSplitRationale() {
        let (t1, r1) = Sanitize.splitRationale("好的，马上看|让对方知道已处理，降低焦虑")
        XCTAssertEqual(t1, "好的，马上看")
        XCTAssertEqual(r1, "让对方知道已处理，降低焦虑")
        let (t2, r2) = Sanitize.splitRationale("收到，谢谢～")
        XCTAssertEqual(t2, "收到，谢谢～")
        XCTAssertNil(r2)
        let (t3, r3) = Sanitize.splitRationale("选 A|B 都行|理由放最后")
        XCTAssertEqual(t3, "选 A|B 都行")
        XCTAssertEqual(r3, "理由放最后")
    }

    // ---- FR-6.4 / FR-3.5 护栏 ----

    func testScreenIncomingRisk() {
        XCTAssertTrue(Guardrail.screenIncoming("把验证码发我一下").risky)
        XCTAssertTrue(Guardrail.screenIncoming("帮我转账到这个账户").risky)
        XCTAssertFalse(Guardrail.screenIncoming("明天下午两点开会").risky)
    }

    func testScreenCandidateRisk() {
        XCTAssertTrue(Guardrail.screenCandidate("保证赚，稳赚不赔").risky)
        XCTAssertFalse(Guardrail.screenCandidate("好的，收到").risky)
    }

    // ---- FR-2.1 / 2.2 / 2.4 分类 ----

    func testIntentClassification() {
        XCTAssertEqual(Classify.classifyIntent("你的快递已到，取件码 8-2-3001"), "通知")
        XCTAssertEqual(Classify.classifyIntent("下午几点开会？"), "问询")
        XCTAssertEqual(Classify.classifyIntent("最近好累，压力大"), "情绪倾诉")
        XCTAssertEqual(Classify.classifyIntent("你总是这样，太过分了"), "冲突")
        XCTAssertEqual(Classify.classifyIntent(""), "通知")
    }

    func testEmotionAndUrgency() {
        XCTAssertEqual(Classify.classifyEmotion("太好了，哈哈").0, "开心")
        XCTAssertEqual(Classify.classifyEmotion("急死了，来不及了").0, "焦虑")
        XCTAssertEqual(Classify.classifyEmotion("这个必须马上处理").1, "高")
        XCTAssertEqual(Classify.classifyEmotion("有空聊聊").1, "普通")
    }

    func testNeedsReply() {
        XCTAssertFalse(Classify.needsReply("验证码 283910，请勿泄露"))
        XCTAssertFalse(Classify.needsReply("您的包裹已签收"))
        XCTAssertTrue(Classify.needsReply("什么时候给我答复？"))
    }

    // ---- FR-3.2 四风格互异 ----

    func testFourStylesProduceDistinctPrompts() {
        let systems = Classify.styles.map {
            Compose.buildPrompt(current: notif("在吗"), style: $0).system
        }
        XCTAssertEqual(Set(systems).count, 4)
    }

    // ---- 链路集成 ----

    func testPipelineNoReplyNotice() {
        let pipeline = ReplyPipeline(generator: { _ in ["收到，谢谢～"] })
        let outcome = pipeline.handle(notif("验证码 283910，10 分钟内有效"), [])
        XCTAssertTrue(outcome.candidates.isEmpty)
        XCTAssertEqual(outcome.notice, noReplyNotice)
    }

    func testPipelineRiskNotice() {
        let pipeline = ReplyPipeline(generator: { _ in ["好的"] })
        let outcome = pipeline.handle(notif("把支付密码告诉我"), [])
        XCTAssertTrue(outcome.candidates.isEmpty)
        XCTAssertNotNil(outcome.notice)
    }

    func testPipelineCandidatesAndRationale() {
        let pipeline = ReplyPipeline(generator: { _ in ["好的，马上看|让对方知道已处理"] })
        let outcome = pipeline.handle(notif("那个文件急用，现在能发我吗？"), [])
        XCTAssertEqual(outcome.candidates.count, 1)
        XCTAssertEqual(outcome.candidates[0].rationale, "让对方知道已处理")
    }

    // ---- FR-5.1 / 5.2 PersonaStore ----

    func testPersonaEncryptedAtRestAndRoundtrip() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("persona.enc")
        let store = PersonaStore(fileURL: file, machineId: "test-device", passphrase: "pw")
        store.setPersona(me: "本小姐", banned: ["随便"], defaultStyle: "幽默")
        XCTAssertEqual(store.getPersona().me, "本小姐")
        // 静态不可读
        let raw = (try? Data(contentsOf: file)) ?? Data()
        XCTAssertFalse(String(data: raw, encoding: .utf8)?.contains("本小姐") ?? false)
        // 错口令 → 初始态而非明文
        let wrong = PersonaStore(fileURL: file, machineId: "test-device", passphrase: "wrong")
        XCTAssertEqual(wrong.getPersona().me, "我")
    }

    func testRelationDefaultAndEnum() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".enc")
        let store = PersonaStore(fileURL: dir, machineId: "d")
        XCTAssertEqual(store.getRelation("陌生人甲"), "陌生")
        XCTAssertEqual(store.getRelation(nil), "陌生")
        store.setRelation("老板", "同事")
        XCTAssertEqual(store.getRelation("老板"), "同事")
    }

    func testHistoryOrderAndWipe() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".enc")
        let store = PersonaStore(fileURL: dir, machineId: "d")
        for i in 0..<7 { store.addHistory("张三", "摘要\(i)") }
        XCTAssertEqual(store.history("张三", limit: 5), ["摘要6", "摘要5", "摘要4", "摘要3", "摘要2"])
        store.addHistory("李四", "   ")
        XCTAssertTrue(store.history("李四").isEmpty)
        store.setPersona(me: "X")
        store.wipe()
        XCTAssertEqual(store.getPersona().me, "我")
    }

    func testPipelinePersonaIntegration() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".enc")
        let store = PersonaStore(fileURL: dir, machineId: "d")
        store.setPersona(me: "本小姐", defaultStyle: "幽默", banned: ["随便"])
        store.setRelation("老板", "同事")

        var seenSystem = ""
        var seenStyle = ""
        let pipeline = ReplyPipeline(generator: { p -> [String] in
            seenSystem = p.system; seenStyle = p.style
            return ["随便说说啦", "好的，马上处理|先响应降低焦虑"]
        }, persona: store)
        let outcome = pipeline.handle(notif("这周报表发我一下", sender: "老板"), [])

        XCTAssertEqual(seenStyle, "幽默")               // 人设默认风格生效
        XCTAssertTrue(seenSystem.contains("本小姐"))     // 自称注入
        XCTAssertTrue(seenSystem.contains("对方关系：同事"))
        XCTAssertEqual(outcome.candidates.count, 1)     // 禁语候选被拦
        XCTAssertEqual(outcome.candidates[0].text, "好的，马上处理")
    }

    // ---- RAG 知识库（决策③ / 与 m1-poc test_rag.py 同构） ----

    private func tempKb() -> KnowledgeBase {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return KnowledgeBase(fileURL: dir.appendingPathComponent("kb.enc"), machineId: "test-device", passphrase: "pw")
    }

    func testKbAddRemoveAndPersist() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("kb.enc")
        let kb = KnowledgeBase(fileURL: file, machineId: "d", passphrase: "pw")
        let id1 = try kb.add(category: "persona", text: "我养了两只猫")
        try kb.add(category: "relation", text: "周报口径以部门模板为准", contact: "老板")
        try kb.add(category: "platform", text: "微信长语音前先发文字确认")
        XCTAssertEqual(kb.count, 3)
        XCTAssertEqual(kb.all()[1].contact, "老板")
        XCTAssertEqual(kb.all()[0].contact, nil)          // 非 relation 强制无联系人
        XCTAssertThrowsError(try kb.add(category: "bad", text: "x"))          // 非法分类
        XCTAssertThrowsError(try kb.add(category: "persona", text: "   "))    // 空文本
        // 加密静态不可读（密文为二进制，逐字节断言不含明文 UTF-8）
        let raw = (try? Data(contentsOf: file)) ?? Data()
        let plaintext = String(data: raw, encoding: .utf8)
        XCTAssertFalse(plaintext?.contains("两只猫") ?? false)
        // 重开实例读回；错口令 → 初始态而非明文
        XCTAssertEqual(KnowledgeBase(fileURL: file, machineId: "d", passphrase: "pw").count, 3)
        XCTAssertEqual(KnowledgeBase(fileURL: file, machineId: "d", passphrase: "wrong").count, 0)
        // 删除 + wipe
        XCTAssertTrue(kb.remove(id: id1))
        XCTAssertFalse(kb.remove(id: "nonexistent"))
        kb.wipe()
        XCTAssertEqual(kb.count, 0)
        XCTAssertEqual(KnowledgeBase(fileURL: file, machineId: "d", passphrase: "pw").count, 0)
    }

    func testLexicalSearchScoring() throws {
        let kb = tempKb()
        try kb.add(category: "persona", text: "我养了两只猫")
        try kb.add(category: "relation", text: "周报口径以部门模板为准", contact: "老板")
        try kb.add(category: "platform", text: "微信长语音前先发文字确认")
        // 词面命中
        let hit = LexicalRetriever.search(kb: kb, query: "养猫要注意什么", contact: nil)
        XCTAssertEqual(hit.count, 1)
        XCTAssertEqual(hit[0].text, "我养了两只猫")
        // 同联系人加权：弱词面 + 加权仍命中
        let boosted = LexicalRetriever.search(kb: kb, query: "老板问周报", contact: "老板")
        XCTAssertTrue(boosted.contains { $0.text.contains("周报") })
        // 无重叠且非当前联系人 → 不注入
        XCTAssertTrue(LexicalRetriever.search(kb: kb, query: "完全无关内容xyz", contact: "同事").isEmpty)
        // bigram：标点被过滤、单字兜底
        XCTAssertEqual(LexicalRetriever.bigrams("猫"), Set(["猫"]))
        XCTAssertFalse(LexicalRetriever.bigrams("你好，世界！").contains("，"))
        XCTAssertTrue(LexicalRetriever.bigrams("周报口径").contains("周报"))
    }

    func testLexicalSearchTopK() throws {
        let kb = tempKb()
        for i in 0..<5 { try kb.add(category: "platform", text: "平台条目测试\(i) 关于聊天礼仪和消息") }
        let hits = LexicalRetriever.search(kb: kb, query: "聊天礼仪和消息")
        XCTAssertEqual(hits.count, 3)  // top3 上限
    }

    // ---- T01081：多候选挤段拆分护栏 ----

    func testExpandCandidatesMultiline() {
        let parts = Sanitize.expandCandidates("好的，马上看|降低焦虑\n我看看再回你|争取时间")
        XCTAssertEqual(parts, ["好的，马上看|降低焦虑", "我看看再回你|争取时间"])
    }

    func testExpandCandidatesInlineNumbered() {
        let parts = Sanitize.expandCandidates("选项1：好的，马上看|理由一 选项2：我看看再回你|理由二")
        XCTAssertEqual(parts.count, 2)
        XCTAssertTrue(parts[0].hasPrefix("好的"))
        XCTAssertTrue(parts[1].hasPrefix("我看看"))
    }

    func testExpandCandidatesPlainSentenceNotSplit() {
        let raw = "收到，我下午三点前把周报发你，另外会议纪要我整理好也一并同步。"
        XCTAssertEqual(Sanitize.expandCandidates(raw), [raw])
    }

    func testPipelineGiantRawBecomesSeparateCandidates() {
        let giant = "选项1：好的，今天下班前发你|给出明确时限 选项2：我整理好就发，预计明天上午|争取质量优先"
        let pipeline = ReplyPipeline(generator: { _ in [giant] })
        let outcome = pipeline.handle(notif("这周周报什么时候发？", sender: "老板"), [])
        XCTAssertEqual(outcome.candidates.count, 2)
        for c in outcome.candidates {
            XCTAssertFalse(c.text.contains("选项"))
            XCTAssertFalse(c.text.contains("理由"))
            XCTAssertFalse(c.text.contains("|"))
        }
        XCTAssertEqual(outcome.candidates[0].rationale, "给出明确时限")
        XCTAssertEqual(outcome.candidates[1].rationale, "争取质量优先")
    }

    func testPipelineKbInjection() throws {
        let kb = tempKb()
        try kb.add(category: "relation", text: "周报口径以部门模板为准", contact: "老板")

        var seenSystem = ""
        let pipeline = ReplyPipeline(generator: { p -> [String] in
            seenSystem = p.system
            return ["好的，按模板发你|贴合既定口径"]
        }, kb: kb)
        _ = pipeline.handle(notif("这周周报怎么写？", sender: "老板"), [])
        // 检索命中 + 注入参考资料
        XCTAssertTrue(seenSystem.contains("参考资料"))
        XCTAssertTrue(seenSystem.contains("周报口径以部门模板为准"))
        XCTAssertTrue(seenSystem.contains("不得据此编造"))

        // 无命中 → 不注入，行为与既有一致
        var cleanSystem = ""
        let pipeline2 = ReplyPipeline(generator: { p -> [String] in
            cleanSystem = p.system
            return ["好的"]
        }, kb: kb)
        _ = pipeline2.handle(notif("晚上一起吃饭吗？", sender: "同事"), [])
        XCTAssertFalse(cleanSystem.contains("参考资料"))
    }
}
