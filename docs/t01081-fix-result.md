# T01081 修复：微信自动回复选项带场景解释发送 Bug

## 根因分析

发送链路为：`CloudLlmGenerator`（请求 `n:2` 两候选）→ `parseChoices` 按 choices 数组拆分 → `splitRationale` 按「内容|理由」切分 → IME 点击 `commitText(candidate.text)` 注入微信输入框。

**Bug 触发路径**：许多 OpenAI 兼容服务不支持 `n>1` 参数——只返回 1 个 choice，且把两条候选连同解释挤在同一段 content 里（如「选项1：好的，马上看|理由一 选项2：我看看再回你|理由二」）。此时：

1. `parseChoices` 按 choices 拆分得到 **1 条巨型 raw**（含全部选项+解释）；
2. `splitRationale` 只按**最后一个** `|` 切一次，切完 text 仍是整段（全部选项+解释都在里面）；
3. 候选条上只有这一条巨型候选——**无论用户点哪个「选项」，commit 的都是同一段完整文本**，与 Bug 现象完全吻合。

## 修复方案（分层防御，四端同构）

### Fix 1（核心）：Sanitize 新增多候选拆分护栏 `expand_candidates / expandCandidates`

- **多行输出**：逐行拆（LLM 逐行列候选的常见格式），每行剥「选项N：/候选N:/1./-/•/（1）」前缀；
- **单行含内联编号**（「选项1：… 选项2：…」）：按编号正则拆分；
- **普通长句**：不误拆（无编号单行原样返回）；
- 只拆结构、不改写文字，不破坏既有 C3/C25 清洗。

改动点：`m1-poc/emoreply/sanitize.py`（新增 `expand_candidates` + `_NUMBERED`/`_LINE_PREFIX` 正则）；Android `core/Sanitize.kt`、iOS `Sanitize.swift`、desktop `renderer.js` 同构实现。

### Fix 2：Pipeline 候选产出改 flatMap

`generator(prompt)` 的每条 raw 先 `flatMap(expandCandidates)` 再逐行 `splitRationale`——单条巨型输出拆成独立候选，每条候选携带自己的解释句（解释仅展示、不注入）。

改动点：`m1-poc/emoreply/pipeline.py`、Android `core/Pipeline.kt`、iOS `Pipeline.swift`、desktop `renderer.js`（候选渲染循环）。

### Fix 3（兜底）：Android IME `commit()` 二道防线

`EmoReplyImeService.commit()` 注入前再过一次 `expandCandidates + splitRationale`，只取**首个候选项的纯文本**——即使上游拆分遗漏或存在存量候选，也绝不把全部选项+解释一并发送。

## 期望行为达成

1. ✅ 仅将用户选中的那条回复的**纯文本内容**注入输入框（pipeline 拆分 + IME 兜底双层保证）；
2. ✅ 场景解释进入 `Candidate.rationale`，仅 toast 展示（FR-3.3 展示逻辑未变），不随消息发送；
3. ✅ 两条候选拆分后各自独立——点哪条发哪条。

## 验证

- **m1-poc**：新增 `tests/test_t01081_expand.py` 9 用例（多行拆/内联编号拆/前缀剥离/普通长句不误拆/空输入/端到端巨型 raw → 2 条独立候选且 text 无「选项/理由/|」）**9 passed**；全量回归 **88 passed** ✅
- **desktop**：renderer.js `node --check` 语法过；expandCandidates 逻辑 node 单测 5 组断言通过 ✅
- **Android**：`gradle assembleDebug` **BUILD SUCCESSFUL** ✅，修复版 APK 已装真机（SM-G9980）并重绑定通知监听
- **iOS**：同构修复 + 4 个新 XCTest 用例（多行/内联编号/不误拆/端到端），括号平衡自查过；`swift test` 待 Mac 侧执行（verified:false）
- **真机端到端**：需用户重发微信消息确认（Key 失效时走本地模板路径，本地模板本就单条干净，无此 Bug）

## 约束确认

- 消息读取、候选生成、解释展示逻辑均未改变；
- 与现有框架兼容：expand 是纯字符串处理，无新依赖、无权限变化。
