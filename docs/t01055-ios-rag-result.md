# [PRD] 4 M4二期扩展 —— RAG 端侧接线：iOS 移植（第四端补齐）

## 本阶段交付（接桌面端 JS 阶段；至此 RAG 人设库四端同构完成）

1. **`Sources/EmoReplyCore/Knowledge.swift`（新增，与 rag.py / KnowledgeBase.kt / kb.js 同构）**：
   - `KnowledgeBase`：CryptoKit AES-GCM 加密单 JSON 文件（与 PersonaStore 同型，密钥=SHA256(machineId‖口令)）；三类条目 persona/relation/platform，relation 强制绑联系人；增删查 + wipe 联动 FR-6.3；口令不匹配/损坏 → 初始态不崩溃。
   - `LexicalRetriever`：CJK bigram 词面检索（单字兜底），同联系人加权 0.15，无重叠且非当前联系人不返回，top3 注入——与 Python/Kotlin/JS 打分口径逐行一致。
2. **Compose.swift**：`buildPrompt` 增 `knowledge` 参数，注入「参考资料（…不得据此编造对方未提及的事实）」约束行（在 intent 块内，与 Android/Python 同构）；缺省不注入，行为不变。
3. **Pipeline.swift**：增 `kb: KnowledgeBase?` 可选参数；生成前按当前消息 + sender/chat 检索 top3 注入；无命中时不注入。
4. **App/ContentView.swift**：新增「RAG 知识库」管理 Section（分类 Picker / 关系话术联系人 / 内容 / 保存 / 删除 / 清空 / 计数）+ 主区「当前联系人」输入；`generate()` 走检索注入。
5. **Tests**：新增 4 个 RAG 测试（增删查/持久化/静态不可读/错口令初始态、检索打分与联系人加权与 bigram 口径、top3 上限、Pipeline 注入集成与无命中不注入）。

## 顺手修复（既有隐患）

- `testPersonaEncryptedAtRestAndRoundtrip` 的「静态不可读」断言原用 `String(contentsOf: .utf8)` 读二进制密文，非法 UTF-8 会抛错导致测试失败；改为 `Data` 读取后按 UTF-8 解码断言不含明文（新 RAG 测试同款写法）。

## 验证（诚实标注）

- 括号/引号平衡静态自查 5 文件全过 ✅（Python 脚本剔除字符串与注释后统计）。
- 检索打分逻辑与 desktop kb.js（10 组冒烟断言全过）、m1-poc rag.py（pytest 覆盖）逐行对齐；密钥派生与 PersonaStore 同型（该类既有测试移植自已验证用例）。
- **verified:false：本机 Windows 无 Swift 编译链路，`swift test` 未实跑**。Mac 侧验证步骤：`cd ios-app && swift test`（新增 4 个 RAG 测试应全过）；真机抽查：知识库区添加关系话术 → 主区填当前联系人 → 粘贴含关键词消息生成 → 观察候选贴合知识条目。
- 移植中自查发现并修正 3 处 Swift 语法问题（Substring→String 显式转换、Set 与 Array 比较类型、Character 拼接）。

## 剩余工作（本单保持 todo）

1. FR-1.6 语义指向判定：需标注测试集；
2. 邮件协议接入：待授权方案拍板；
3. Mac 侧 `swift test` + Xcode 构建、Android 真机、桌面 GUI 人工抽查。

## 本次回写

无新增单。
