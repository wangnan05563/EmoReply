import SwiftUI

/// EmoReply iOS 端（T01053）：独立 App 呈现面（SRS 2.3：iOS 经独立 App/组件呈现）。
/// POC 交互面与桌面端同构：粘贴对方消息 → 选风格 → 云端生成 → 点选复制（不自动发送）。
@main
struct EmoReplyApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
