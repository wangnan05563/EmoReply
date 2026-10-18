import Foundation

/// 云端 LLM 生成（FR-3.1，OpenAI 兼容 /chat/completions）。
/// 与 android CloudLlmGenerator 同构：密钥运行时传入，不硬编码；错误折算为
/// CloudLlmError（userMessage 面向用户，可直接展示，C24）。async/await。
public final class CloudLlmGenerator {

    public struct Config {
        public var baseUrl: String
        public var apiKey: String
        public var model: String
        public init(baseUrl: String, apiKey: String, model: String) {
            self.baseUrl = baseUrl; self.apiKey = apiKey; self.model = model
        }
    }

    public struct CloudLlmError: Error {
        public let code: Int
        public let message: String
        public let userMessage: String
    }

    let config: Config

    public init(config: Config) { self.config = config }

    public func generate(_ prompt: Compose.PromptBundle, candidates: Int = 2) async throws -> [String] {
        var base = config.baseUrl
        while base.hasSuffix("/") { base.removeLast() }
        guard let url = URL(string: base + "/chat/completions") else {
            throw CloudLlmError(code: 0, message: "bad baseUrl", userMessage: "接口地址格式错误，请检查设置")
        }

        var userText = ""
        if !prompt.history.isEmpty {
            userText += "历史对话：\n" + prompt.history.joined(separator: "\n") + "\n"
        }
        userText += "当前消息：\n\(prompt.current)"
        // FR-3.3：每条候选按「内容|理由」输出，理由可省略
        userText += "\n\n请给出候选回复；每条按「内容|理由」输出，理由用一句话说明情商点，可以省略。"

        var request = URLRequest(url: url, timeoutInterval: 60)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        let body: [String: Any] = [
            "model": config.model,
            "temperature": 0.7,
            "max_tokens": 1500,  // T01082：推理模型（reasoning_content）会占用大量 token，256 时正文为空
            "n": candidates,
            "messages": [
                ["role": "system", "content": prompt.system],
                ["role": "user", "content": userText],
            ],
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw CloudLlmError(code: 0, message: "\(error)", userMessage: "网络异常，无法连接生成服务")
        }
        let http = response as? HTTPURLResponse
        let code = http?.statusCode ?? 0
        let text = String(data: data, encoding: .utf8) ?? ""
        guard (200...299).contains(code) else { throw Self.toError(code: code, body: text) }

        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = obj["choices"] as? [[String: Any]] else {
            throw CloudLlmError(code: 200, message: "response missing choices", userMessage: "生成服务返回格式异常")
        }
        let out = choices.compactMap { c -> String? in
            guard let m = c["message"] as? [String: Any], let content = m["content"] as? String else { return nil }
            let t = content.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.isEmpty ? nil : t
        }
        if out.isEmpty {
            throw CloudLlmError(code: 200, message: "empty choices content", userMessage: "生成服务未返回内容")
        }
        return out
    }

    /// 附录 C24：余额/配额（402）等错误必须明确提示并降级，不得静默。
    static func toError(code: Int, body: String) -> CloudLlmError {
        var detail = ""
        if let obj = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any],
           let err = obj["error"] as? [String: Any], let msg = err["message"] as? String {
            detail = msg
        } else {
            detail = String(body.prefix(200))
        }
        let userMessage: String
        switch code {
        case 402: userMessage = "云端生成配额不足（余额用尽），已降级为本地模板"
        case 401, 403: userMessage = "API Key 无效或无权限，已降级为本地模板"
        case 429: userMessage = "请求过于频繁（限流），已降级为本地模板"
        case 500...599: userMessage = "生成服务暂不可用（\(code)），已降级为本地模板"
        default: userMessage = "云端生成失败（\(code)），已降级为本地模板"
        }
        return CloudLlmError(code: code, message: "HTTP \(code): \(detail)", userMessage: userMessage)
    }
}

/// 本地模板兜底（与 android StubGenerator 同款口径）。
public enum StubGenerator {
    public static func generate(_ prompt: Compose.PromptBundle) -> [String] {
        let current = prompt.current.split(separator: "：", maxSplits: 1).last.map(String.init) ?? ""
        if current.contains("？") || current.contains("?") {
            return ["好的，我看一下，晚点回复你哈", "收到，我想想再回你～"]
        }
        if current.contains("谢谢") || current.contains("感谢") { return ["不客气～"] }
        return ["收到，谢谢～", "好嘞，知道了"]
    }
}
