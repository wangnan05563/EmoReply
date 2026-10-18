import Foundation

/// 候选回复的输出清洗（C3 markdown / C25 HTML / FR-3.3 解释句解析）。
/// 与 m1-poc sanitize.py / android Sanitize.kt 同构；只剥离格式符号、不改写文字。
public enum Sanitize {

    public static func stripMarkdown(_ text: String) -> String {
        var out = text
        // 代码围栏
        if let r = try? NSRegularExpression(pattern: "```[a-zA-Z0-9]*\\n?") {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "")
        }
        // 链接 [text](url) → text
        if let r = try? NSRegularExpression(pattern: "\\[([^\\]]*)\\]\\([^)]*\\)") {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "$1")
        }
        // 行内代码
        if let r = try? NSRegularExpression(pattern: "`([^`]*)`") {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "$1")
        }
        // 强调 **x** / *x* / __x__ / _x_
        if let r = try? NSRegularExpression(pattern: "(\\*{1,3}|_{1,3})(?=\\S)(.+?)(?<=\\S)\\1", options: [.dotMatchesLineSeparators]) {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "$2")
        }
        // 标题与列表标记
        if let r = try? NSRegularExpression(pattern: "^[ \\t]{0,3}#{1,6}[ \\t]*", options: [.anchorsMatchLines]) {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "")
        }
        if let r = try? NSRegularExpression(pattern: "^[ \\t]{0,3}(?:[-*+]|\\d+\\.)[ \\t]+", options: [.anchorsMatchLines]) {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "")
        }
        // 游离符号
        if let r = try? NSRegularExpression(pattern: "(?<!\\w)[*_~]{1,3}(?!\\w)") {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "")
        }
        return out
    }

    public static func stripMarkup(_ text: String) -> String {
        guard let r = try? NSRegularExpression(pattern: "</?[a-zA-Z][^>]*>") else { return text }
        return r.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "")
    }

    /// 注入输入框前的最终清洗。
    public static func cleanCandidate(_ text: String) -> String {
        var out = stripMarkup(stripMarkdown(text))
        if let r = try? NSRegularExpression(pattern: "\\n{3,}") {
            out = r.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: "\n\n")
        }
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// FR-3.3：解析「内容|理由」格式（按最后一个 | 切分），理由缺失返回 nil。
    public static func splitRationale(_ raw: String?) -> (String, String?) {
        let s = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return ("", nil) }
        if let idx = s.lastIndex(of: "|") {
            let text = String(s[s.startIndex..<idx]).trimmingCharacters(in: .whitespacesAndNewlines)
            let rationale = String(s[s.index(after: idx)...]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty && !rationale.isEmpty { return (cleanCandidate(text), rationale) }
        }
        return (cleanCandidate(s), nil)
    }

    // T01081：兼容服务不支持 n>1 时，LLM 常把多条候选挤在同一段输出——
    // 不拆开的话，用户点任一候选都会把全部选项连同解释一起发送（发送事故）。
    private static let numbered = try! NSRegularExpression(pattern: "(?:^|[\\s　])(?:选项|候选|方案)\\s*\\d+\\s*(?:[（(][^）)]*[）)])?\\s*[：:.．]")
    private static let linePrefix = try! NSRegularExpression(
        pattern: "^[ \\t　]*(?:(?:选项|候选|方案)\\s*\\d+\\s*(?:[（(][^）)]*[）)])?\\s*[：:.．]|[-*+•]|\\d+[.、)]|[（(]\\d+[)）])\\s*")

    /// 剥离单候选行的「选项N：/候选N:/1./-/•/（1）」前缀。
    private static func stripOptionPrefix(_ line: String) -> String {
        guard let r = try? linePrefix else { return line }
        let range = NSRange(line.startIndex..., in: line)
        return r.stringByReplacingMatches(in: line, range: range, withTemplate: "", limit: 1)
    }

    /// 把一条 LLM 输出拆成单候选行数组（只拆结构，不改写文字）。
    /// 多行逐行拆；单行含内联编号（「选项1：… 选项2：…」）按编号拆；其余原样单条。
    /// 与 m1-poc `sanitize.expand_candidates` 同构。
    public static func expandCandidates(_ raw: String?) -> [String] {
        let s = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return [] }
        let lines = s.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        if lines.count > 1 { return lines.map(stripOptionPrefix) }
        let range = NSRange(s.startIndex..., in: s)
        if numbered.firstMatch(in: s, range: range) != nil {
            let parts = numbered.stringByReplacingMatches(in: s, range: range, withTemplate: "\u{1}")
                .components(separatedBy: "\u{1}")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            if parts.count > 1 { return parts.map(stripOptionPrefix) }
        }
        return [stripOptionPrefix(s)]
    }
}
