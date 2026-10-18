import Foundation

/// M3 核心链路编排：预处理 → 分类 → 上下文/提示词 → 生成 → 清洗 → 护栏 → 候选。
/// 与 m1-poc pipeline.py / android Pipeline.kt 同构；生成由注入的闭包承担。
public struct PipelineOutcome {
    public let decision: Decision
    public var candidates: [Candidate] = []
    public var notice: String? = nil
    public var suppressed: Bool = false
}

public let noReplyNotice = "通知类消息，判定无需回复（FR-2.4）"

public final class ReplyPipeline {

    public typealias Generator = (Compose.PromptBundle) -> [String]

    let generator: Generator
    let throttle: Guardrail.NoticeThrottle
    /// 显式风格（nil = 允许人设 default_style 生效）
    let explicitStyle: String?
    let maxTokens: Int
    /// FR-5.1/5.2：人设与关系（可选，与 m1-poc persona.py 同构）
    public let persona: PersonaStore?
    /// RAG 知识库（决策③，可选，与 m1-poc rag.py 同构）
    public let kb: KnowledgeBase?

    public init(generator: @escaping Generator,
                throttle: Guardrail.NoticeThrottle = Guardrail.NoticeThrottle(),
                style: String? = nil,
                maxTokens: Int = Compose.defaultMaxTokens,
                persona: PersonaStore? = nil,
                kb: KnowledgeBase? = nil) {
        self.generator = generator
        self.throttle = throttle
        self.explicitStyle = style
        self.maxTokens = maxTokens
        self.persona = persona
        self.kb = kb
    }

    public func handle(_ current: EmoNotification, _ history: [EmoNotification], now: Double? = nil) -> PipelineOutcome {
        let decision = Preprocess.decide(current)
        switch decision.action {
        case .drop: return PipelineOutcome(decision: decision)
        case .fallback: return PipelineOutcome(decision: decision, notice: decision.fallbackHint)
        case .generate: break
        }

        // FR-6.4：命中风险时不生成迎合内容，改发风险提示（提示本身按 C6 节流）
        let risk = Guardrail.screenIncoming(current.body)
        if risk.risky {
            let stamp = now ?? current.timestamp
            if !throttle.allow("risk:\(risk.category ?? "")", now: stamp) {
                return PipelineOutcome(decision: decision, suppressed: true)
            }
            return PipelineOutcome(decision: decision, notice: risk.advice)
        }

        // FR-2.4（REQ-006）：纯通知类不生成候选，压「无需回复误判为需回复」
        let intent = Classify.classifyIntent(current.body)
        if !Classify.needsReply(current.body, intent: intent) {
            let stamp = now ?? current.timestamp
            if !throttle.allow("no-reply", now: stamp) {
                return PipelineOutcome(decision: decision, suppressed: true)
            }
            return PipelineOutcome(decision: decision, notice: noReplyNotice)
        }

        // FR-2.1/2.2 + FR-5.1/5.2：意图/情绪/人设/关系并入提示词
        let (emotion, urgency) = Classify.classifyEmotion(current.body)
        var me = "我", relation = "", style = explicitStyle ?? "亲切"
        var banned: [String] = []
        if let p = persona {
            let ctx = p.promptContext(current.sender ?? current.chat)
            me = ctx.me; relation = ctx.relation; banned = ctx.banned
            if explicitStyle == nil { style = ctx.style }
        }
        // RAG（决策③）：生成前按当前消息 + 联系人检索 top3，无命中时不注入（行为不变）
        let knowledge = kb.map {
            LexicalRetriever.search(kb: $0, query: current.body, contact: current.sender ?? current.chat)
        }.map { hits in hits.map(\.text) } ?? []
        let prompt = Compose.buildPrompt(current: current, history: history, me: me, style: style,
                                         maxTokens: maxTokens, intent: intent, emotion: emotion,
                                         urgency: urgency, relation: relation, knowledge: knowledge)

        var candidates: [Candidate] = []
        // T01081：单条输出可能含多条候选（服务不支持 n>1 时挤在同一段），
        // 先拆成单候选行再逐条解析——否则点任一候选会发送全部选项+解释
        for raw in generator(prompt) {
            for line in Sanitize.expandCandidates(raw) {
                let (text0, rationale) = Sanitize.splitRationale(line)
                if text0.isEmpty || Guardrail.screenCandidate(text0).risky { continue }
                if let p = persona, p.violatesBanned(text0, banned) { continue }
                candidates.append(Candidate(text: text0, style: style, rationale: rationale))
            }
        }
        return PipelineOutcome(decision: decision, candidates: candidates)
    }

    /// 批量入口：按时间排序去重后，逐条以其同会话历史为上下文处理。
    public func run(_ notifications: [EmoNotification]) -> [PipelineOutcome] {
        var byChat: [String: [EmoNotification]] = [:]
        var outcomes: [PipelineOutcome] = []
        for n in Preprocess.dedupe(notifications.sorted { $0.timestamp < $1.timestamp }) {
            let key = "\(n.source):\(n.chat ?? "")"
            let history = byChat[key] ?? []
            outcomes.append(handle(n, history))
            byChat[key] = history + [n]
        }
        return outcomes
    }
}
