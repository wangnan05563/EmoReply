# EmoReply iOS 端（T01053 · iOS 保留在首期范围）

与 m1-poc / android-app / desktop-app 四端同构的核心链路 Swift 移植 + iOS 呈现 App 壳。
**本目录交付源码与测试；构建需 macOS/Xcode（开发机无此环境，未编译验证）。**

## 结构

```
ios-app/
├── Package.swift                  # Swift Package（核心库 + XCTest）
├── Sources/EmoReplyCore/          # 平台无关核心（与 m1-poc 一一对应）
│   ├── Models.swift               # 通知/判定/候选（FR-1.2 字段对齐）
│   ├── Preprocess.swift           # 去重、指向判定、空载荷兜底（FR-1.3/1.4/1.5）
│   ├── Compose.swift              # 伪历史 + token 截断 + 提示词（C16 / FR-3.1 / REQ-006 注入）
│   ├── Sanitize.swift             # C3/C25 清洗 + FR-3.3 「内容|理由」解析
│   ├── Guardrail.swift            # FR-3.5/6.4 双侧护栏 + C6 节流
│   ├── Classify.swift             # FR-2.1/2.2/2.4 规则分类 + FR-3.2 风格枚举
│   ├── Pipeline.swift             # 编排（含 persona / RAG 知识库集成与禁语过滤）
│   ├── Persona.swift              # FR-5.1/5.2 加密存储（CryptoKit AES-GCM 单文件）
│   ├── Knowledge.swift            # RAG 知识库（决策③）：三类条目加密存储 + LexicalRetriever 词面检索
│   └── CloudLlmGenerator.swift    # FR-3.1 云端生成 + StubGenerator 兜底（C24）
├── Tests/EmoReplyCoreTests/       # XCTest（移植 m1-poc 66 项中的关键行为）
└── App/                           # SwiftUI 壳：粘贴 → 生成 → 复制（不自动发送）
    ├── EmoReplyApp.swift
    └── ContentView.swift
```

## 首次接入步骤（Mac 上约 5 分钟）

1. **核心回归**：`cd ios-app && swift test` —— 全部测试应通过（含加密静态不可读、禁语拦截、四风格互异等）。
2. **Xcode 包装**：`Xcode → File → New → Project → iOS App`（命名 EmoReply，Interface=SwiftUI），
   然后把 `App/EmoReplyApp.swift`、`App/ContentView.swift` 拖入工程，并把本仓库 `ios-app/`
   目录（含 Package.swift）拖入 → Xcode 会识别为本地 Swift Package，target 依赖 `EmoReplyCore` 即可。
3. **真机运行**：签名后安装；粘贴对方消息 → 生成 → 点选候选复制（**无自动发送**，FR-4.2）。
   iOS 端无系统级通知监听能力，消息经手动粘贴进入（与 SRS「独立 App 呈现」口径一致）。

## 设计要点

- 密钥 = SHA256(设备标识 ‖ 可选口令)，与 m1-poc persona.py / android Crypto.kt 同型；
  POC 用 UserDefaults 存配置，生产建议 Keychain + NSFileProtectionComplete。
- 提示词模板、风格枚举、「内容|理由」格式、降级文案与其余三端逐字同构。
- 分类（Classify）与禁语过滤已随核心移植；通知捕获在 iOS 由手动粘贴替代（系统限制）。
- **RAG 知识库（决策③）**：与 m1-poc rag.py / Android KnowledgeBase.kt / desktop kb.js 同构——
  三类条目（persona 个人事实 / relation 关系话术 / platform 平台礼仪）AES-GCM 加密单文件存储，
  relation 强制绑联系人；LexicalRetriever 按 CJK bigram 重叠打分（同联系人加权 0.15 次级信号，
  无重叠且非当前联系人不返回），Pipeline 生成前检索 top3 注入「参考资料」约束行。
  App 壳 ContentView 提供知识库管理区（增删/清空/计数）与「当前联系人」输入。
