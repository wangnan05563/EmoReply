# T01082 多轮对话高情商回复生成与编辑 —— 方案与实现

## 一、目标

在 EmoReply 现有链路（通知捕获 → 分类 → 上下文/提示词 → 生成 → 清洗 → 护栏 → 候选 → 输入法注入）上，补齐「多轮对话上下文理解 + 高情商回复生成 + 人工可编辑」的完整能力与验收证据，支撑新媒体/客服多轮对话场景。

## 二、输入（与现有实现的映射）

| 需求输入 | 现有实现 | 状态 |
|---|---|---|
| 对话上下文历史（消息序列/发送者/时间戳） | `Notification`（source/timestamp/body/sender/chat）+ 调用方累积 history（`NotifyCaptureService.history` / `run()` byChat） | ✅ 已有 |
| 最新诉求文本 | `current`（提示词「当前消息」永不丢弃，C16） | ✅ 已有 |
| 回复风格标签 | `GeneratorSettings.style`（正式/亲切/简短/幽默；专业型≈正式、安抚型≈亲切、幽默型=幽默，POC 沿用现有四标签） | ✅ 已有 |
| 已选历史回复片段拼接输入框 | IME `commitText(text, 1)`——插入光标处（默认末尾），不覆盖已输入内容 | ✅ 平台能力 |

## 三、约束（逐条落实）

1. **基于上下文推断意图，不孤立解析单条**：伪历史按会话累积传入 `Compose.buildPrompt`，token 超预算从最旧丢弃、system 与 current 永不丢（C16）。新增验证：`test_five_turn_context_kept`（见六）。
2. **情绪优先**：`Classify.classifyEmotion`（开心/愤怒/焦虑/悲伤+紧急度）先于语气注入提示词「对方情绪：…回应需贴合情绪与紧急度」（FR-2.2）。
3. **信息完整 / 表达克制**：
   - 提示词指令新增「两个候选各有侧重：一个侧重情感共鸣，另一个侧重事实澄清或行动建议；语气克制得体，不得使用『亲』『宝贝』等过度营销套话，不得过度承诺」（`CloudLlmGenerator.buildRequest`）；
   - 护栏新增 `overselling` 规则：称呼位「亲，」/「宝贝」/「么么哒」/「尊敬的用户」等客服套话直接拦截（`guardrail.py` / `Guardrail.kt` / `Guardrail.swift` 三端同构）；
   - `deceptive` 规则放宽为「保证…（≤6字间隔）赚/盈利/收益/回本」，覆盖「我保证你能赚回来」类过度承诺，且不误伤「保证按顺序检查」。
4. **逐字编辑**：候选经 `commitText` 注入微信输入框后即为普通文本，光标可任意定位、支持全选/粘贴/删除/追加（输入法平台能力）。
5. **点选拼接不覆盖**：`commitText(text, 1)` 在当前光标位置插入，光标落末尾（position=1）。
6. **简体中文**：提示词与全部输出约定简体中文（现有模板即简体）。
7. **响应延迟**：本地模板路径即时（<100ms）；云端路径受服务端影响，兜底策略——连接超时 10s / 读超时 60s，任一失败即降级本地模板并明确提示（C24），保证「有回复、不卡死」。3 秒硬指标依赖云端服务性能，POC 以降级机制保证体验下限（见六·未竟事项）。

## 四、步骤（链路对应）

1. 加载上下文/角色关系：history 按「source:chat」分桶累积 + `PersonaStore.promptContext`（关系/自称/禁语）+ RAG 知识库检索注入（决策③）；
2. 意图/情绪解析：`Classify.classifyIntent`（通知/问询/请求/情绪倾诉/冲突…）+ `classifyEmotion`，非空并入提示词（REQ-006）；通知类直接不生成（FR-2.4 压误判）；
3. 生成 2-3 候选：`n:2` + 侧重指令；兼容服务不支持 n>1 时由 `expandCandidates` 拆分护栏兜底（T01081，防巨型候选）；
4. 候选展示：IME 候选条（每条独立 TextView）；
5. 点选填入：`commitText` + 解释句仅 toast 展示（FR-3.3），`FeedbackStore` 记采纳（脱敏）；
6. 编辑/发送/另存模板：编辑与发送为微信原生行为；**另存为模板为新增能力，POC 未实现**（见六·未竟事项，建议 PersonaStore 增加 `templates` 表 + IME 长按候选菜单）。

## 五、期望输出

- 本文档（五节结构，缺失字段已标注推断依据：风格标签映射关系、3s 指标的降级兜底口径）；
- 代码增量：`guardrail.py`/`Guardrail.kt`/`Guardrail.swift`（overselling + deceptive 放宽）、`CloudLlmGenerator.buildRequest`（侧重指令）、`tests/test_t01082_multiturn.py`（5 用例）；
- 全部改动经 m1-poc 全量回归 **93 passed**、Android `assembleDebug` **BUILD SUCCESSFUL**、修复版已装真机。

## 六、验收标准对照

| 验收项 | 结果 | 证据 |
|---|---|---|
| 1. 连续 5 轮正确关联首轮上下文 | ✅ | `test_five_turn_context_kept`：5 轮历史全保留、升序、首轮在最前、current 正确 |
| 2. 点选内容为纯文本无隐藏格式 | ✅ | `test_candidate_text_plain_and_editable`：cleanCandidate 剥离 markdown/HTML；rationale 独立 |
| 3. 编辑后发送与输入框一致 | ✅（结构保证） | `commitText` 注入即普通文本，无二次转换环节 |
| 4. 不出现「亲」「宝贝」等营销用语 | ✅ | `overselling` 护栏 + 提示词禁令；`test_overselling_blocked` / `test_normal_words_not_blocked`（「亲人/母亲」不误伤） |
| 5. 响应 ≤3s | ⚠️ 部分 | 本地模板即时；云端依赖服务性能，超时降级兜底（verified:false，需云端 Key 有效后实测） |

## 未竟事项（标注依据）

1. **另存为模板**：新增交互能力，涉及 IME 菜单与新存储表，建议单独立单；
2. **3s 硬指标**：当前 agnes-ai Key 认证失败（Invalid token），云端路径待有效 Key 实测；
3. **desktop 端护栏**：桌面端无 Guardrail 体系（纯复制场景），overselling 拦截待补；
4. **候选侧重侧重验证**：提示词指令的实际遵循度需云端联调观察（LLM 行为，非代码可单测）。
