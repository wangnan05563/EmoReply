# T01391 · M1.1 需求与架构评审（aimsg三期 · 桌面端 P0 主链路）

> 日期：2026-09-30 · 状态：评审完成 · 结论：**方案定稿，进入实施**
> 输入：`docs/t01360-pc-optimization-prd-2026-09-30.md`（FR-PC1~PC4）、desktop-app 现状盘点（716 行 POC：main.js 223 / renderer.js 201 / kb.js 163）

## 1. 现状盘点（评审输入）

| 能力 | 现状 | 差距 |
|---|---|---|
| LLM 调用 | `callLlm` 非流式 HTTPS + `n:2`，401/402/429 降级模板 | 需 stream:true + SSE 解析 |
| 候选拆分 | `expandCandidates`（选项前缀/bullet 剥离）+ `splitRationale`（lastIndexOf 管道） | 缺 markdown 剥离、元话语过滤、悬空管道、标签行丢弃——且**无先后顺序保证** |
| 快捷键 | 无 | 全新（main 进程 globalShortcut） |
| 回填 | 仅「点击复制到剪贴板」 | 缺粘贴回宿主、重生成、美化回填 |
| 向导 | 无 | 首次运行三步向导 + 连接测试 |

## 2. 候选清洗顺序铁律（FR-PC1，与 m1-poc sanitize.py 逐条对齐）

**先剥 markdown，再剥标签前缀**——原因：`_LINE_BULLET_PREFIX` 含 `[-*+•]`，对未清洗文本会误剥 markdown 星号（真机实测教训，T01126 固化）。

实施管线（新模块 `desktop-app/sanitize.js`，Node 零依赖，与 sanitize.py 逐函数同构）：

```
expandCandidates(raw)                    # 拆单候选（标签行整行丢弃、悬空管道接回）
  └─ per candidate → splitRationale      # 「内容|理由」+ 内联「理由：」兜底
       └─ cleanCandidate(text)           # stripMarkup(stripMarkdown) → 空行收敛 → 悬空管道符 [\s|]+$ 剥离
       └─ stripOptionPrefix              # 标签前缀 → 选项前缀 → bullet 前缀
  └─ isMetaCandidate(text) 命中丢弃      # 元话语（请提供聊天记录/作为AI助手…）
  └─ normalizeEmoji(text, 2)             # 表情收敛（正文一部分，清洗不动语义）
```

关键顺序约束：`splitRationale` 内部对内容侧才做 `_stripOption_prefix(cleanCandidate(text))`——
**cleanCandidate 在前、前缀剥离在后**，但两者的 markdown 剥离都先于任何 bullet 匹配。

## 3. Stream 方案（FR-PC2-1）

- **主进程**：`callLlmStream(config, system, user, onDelta)` —— 复用现有 https 请求，`stream: true`；
  手写 SSE 解析（按 `\n\n` 分帧、`data: ` 前缀、`[DONE]` 终止），`choices[0].delta.content` 累积全文；
  每 delta 经 `win.webContents.send('llm:chunk', { seq, text })` 推给渲染进程。
- **渲染进程**：流式占位卡逐字追加 + 光标动画（CSS blink）；`llm:generate` Promise resolve 后
  用**清洗后的最终候选列表整体替换**流式区（清洗只作用于最终态，流式过程展示原文）。
- **降级**：未配置 Key / 非 2xx / SSE 中断 → 本地模板一次性返回（整体渲染，无光标）；
  429/401/402 提示沿用现有 userMessage 映射。
- **非流式兼容**：部分 OpenAI 兼容端不支持 stream → 收到非 SSE 响应（首帧非 `data:`）时整体解析回退，
  与现有 `finalizeLlmResponse` 同路径。

## 4. 快捷键与回填（FR-PC3-1 / FR-PC4-1）

- 快捷键：`globalShortcut.register`，默认 `Alt+Shift+E` 呼出/隐藏悬浮窗（`toggle` 语义，二次触发恢复）；
  「复制后热键直达生成」= 复制动作发生时悬浮窗隐藏状态下按热键 → 唤起 + 自动读剪贴板 + 生成（同 Android ✨ 一体化口径）。
  注册失败（占用）→ 设置页展示可改键位（M3 再做自定义，M1 固定默认值 + 失败提示）。
- 回填三动作：候选卡 = 复制（现有）| **粘贴回宿主**（win.hide() → 延时 150ms → 主进程合成 `Ctrl+V` 到焦点窗口）| **重生成**（exclude 当前候选重跑）；美化回填 = 美化输入框选区，无选区取剪贴板原文。
  ⚠ 合成 Ctrl+V 依赖目标窗口焦点，win.hide() 后需留焦点切换窗口（150ms 实测定值）。

## 5. 风险与决策记录

1. **n:2 与流式**：OpenAI 兼容端 stream+n>1 组合兼容性差 → 改为**单次请求单候选 ×2 并行请求**？决策：M1 保持 `n:2` 非流式语义降级为「stream 单选 + 第二候选取 n:1 二连发」——**简化：M1 流式只出 1 条主候选 + 降级模板补位**，n:2 并行对比归 M2.1（多模型并行）。PRD FR-PC2-1 验收核心是「逐字渲染」，单候选满足。
2. **清洗位置**：清洗在主进程（llm:generate 返回前）做，渲染进程只显示——保证剪贴板/回填两条出口都吃到清洗后文本。
3. **顺序铁律回归**：sanitize.js 移植后用 node 冒烟测试锁住「markdown 未剥时 bullet 正则不误剥」用例（`**好呀**` → `好呀` 非 `*好呀`）。

## 6. 实施顺序（M1 剩余）

T01392 清洗管线（sanitize.js + 接线 + 冒烟）→ T01393 流式 → T01394 快捷键 → T01395 回填 → T01396 向导。
