<div align="center">
  <img src="brand/png/emoreply-256.png" width="96" alt="EmoReply 图标" />

# EmoReply · 高情商聊天回复助手

读屏/剪贴板拿到对方的话 → 本地预处理 → 云端 LLM 生成可直接发送的候选回复 → 一键注入聊天输入框。
**Android 输入法 + Electron 桌面端 + Python 核心链路（概念验证阶段）**

![Android](https://img.shields.io/badge/Android-Kotlin-3DDC84) ![Desktop](https://img.shields.io/badge/Desktop-Electron-47848F) ![Core](https://img.shields.io/badge/Core-Python%203-3776AB) ![Tests](https://img.shields.io/badge/pytest-442%20passed-4B8BBE)

</div>

## 简介

聊天时最难的是「这句话怎么回才得体」。EmoReply 把这个动作压成一次点击：读取当前对话内容（剪贴板、手动粘贴或无障碍读屏），结合意图/情绪识别、人设与知识库检索组装提示词，生成多条带「为什么这样回」说明的候选，用户在输入法里点选即直接填入对方的聊天框。

核心取舍：**宁可不生成，也不生成迎合风险内容**——接入人称/风险护栏；**密钥不出设备**——云端模型地址与 API Key 由用户运行时自填，本机加密存储，仓库内不硬编码任何凭据。

## 特性

- **候选生成 + 一句话解释**：每条候选附「为什么这样回」（FR-3.3），可选多种侧重（情感共鸣 / 事实澄清 / 行动建议）
- **三种取对话方式**：剪贴板一键、手动粘贴输入、无障碍读屏（节点树；微信等保护节点树时自动回落**端侧 OCR**，ML Kit 中文，不依赖 GMS）
- **人物人设与关系**：自称、禁语、联系人关系注入提示词，输出侧再做禁语拦截
- **RAG 知识库**：按联系人加权检索人设/关系话术/平台礼仪，命中才注入
- **生成体验**：语气强度滑杆（理性 ↔ 活泼）、「换一批」去重重生成、候选一键复制、候代表情开关
- **键盘交互**：点选注入、**撤回上次注入**、清空会话、切换到系统输入法、模板另存与快速点选
- **暗色模式**：跟随系统 / 浅色 / 深色，含 AMOLED 纯黑（输入法面板与 App 同步）
- **护栏**：输入侧风险筛查（命中发风险提示而非迎合内容）、输出侧风险过滤、元话语拒答过滤
- **零留存**：候选项仅内存存续（ReplyStore），通知内容不外传、不落聊天记录

桌面端（Electron）额外具备：多模型并行对比生成、流式输出、快捷指令栏、**本地 HTTP API**（`127.0.0.1:17777`）、离线中文 OCR 取文（tesseract.js，模型随包分发）、窗口锚点吸附与位置记忆、明暗主题（浅色/深色/跟随系统）。

## 目录结构

```
android-app/    Android 端（输入法 IME、通知捕获、无障碍读屏 + OCR；Kotlin）
desktop-app/    Electron 桌面端（悬浮窗 + 剪贴板交付；含本地 HTTP API 与离线 OCR）
ios-app/        iOS 侧实现（需 macOS / Xcode 环境构建，本机无构建链路）
m1-poc/         平台无关核心链路的 Python 参考实现 + pytest 验收（事实源）
docs/           需求/设计/计划/报告与迭代 PRD（导航见 docs/README.md）
scripts/        Windows 一键脚本（构建打包、推送测试、环境搭建）
tools/          辅助脚本（品牌图生成、SVG→VectorDrawable 转换）
brand/          品牌资源（App 图标、GitHub 社交预览图）
perf/           桌面端性能压测脚手架（实际位于 desktop-app/perf，配置驱动）
cleanup-logs/   工作空间清理的审计与报告
```

> **双向演进约定**：新能力先在 `m1-poc` 落 Python 参考实现与测试，通过后再镜像到端侧 Kotlin/Swift 实现，保持两侧同构。

## 快速开始

### 桌面端（Electron）

```bash
cd desktop-app
npm install
npm start          # 启动悬浮窗
npm test           # 7 套冒烟（generate/sanitize/llm/tone/kb/ocr/settings，60+ 例）
npm run dist       # 生产构建（NSIS 安装包 + portable，约 100MB，产物在 dist/）
npm run test:asar  # 打包态 OCR 回归（需先 npm run dist，验 asar 内 OCR 可用）
```

桌面端还提供本地 HTTP 接口（默认 `127.0.0.1:17777`）：`GET /health`、`POST /generate`（body `{text, style}`），便于脚本化与性能压测。

### 端到端 UI 测试（CDP 驱动）

```bash
cd desktop-app
env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe . \
  --remote-debugging-port=9222 --user-data-dir=tmp/e2e-profile
# 另开终端
npm run test:e2e   # 51 项断言 + 截图（docs/screenshots/e2e-*.png）
```

### Android 端

环境：Windows + JDK 17 + Gradle 8.9（离线可用）。两种入口等价：

```bat
REM 一键：环境准备 + 构建打包（输出到 release\build\app\output\apk）
scripts\安卓构建打包.cmd

REM 已装好环境、只想重装 APK 并跑一轮真机验证
scripts\安卓推送测试.cmd
```

首次使用还需在系统设置里完成三步授权：**通知使用权** → **启用输入法** → **设为默认输入法**（应用首页可一键跳转对应设置页）。

### Python 核心链路（无真机也能验）

```bash
cd m1-poc
python -m pytest -q
```

## 配置

云端生成能力需要用户自填（应用内「设置」页填写，支持任意 OpenAI 兼容接口，如 DeepSeek / 通义 / OpenAI）：

| 配置项 | 说明 |
| --- | --- |
| 云端生成开关 | 关闭则全程走本地模板桩链路 |
| Base URL | OpenAI 兼容根地址（如 `https://api.deepseek.com/v1`），自动拼 `/chat/completions` |
| API Key | 仅存本机私有目录并加密，**不硬编码、不出设备** |
| 模型名 | 由所用服务提供 |

其余偏好（语气风格、语气强度、候代表情、暗色模式、AMOLED、精简键盘）同样保存在本机，可在设置页调整；配额/鉴权失败会明确提示并降级为本地模板，不静默失败。

## 核心链路

```
对话内容 → 预处理/去重 → 意图·情绪分类 → 上下文与提示词组装（人设+RAG）
        → 云端 LLM 生成 → 候选清洗（格式符/元话语/去重/表情收敛）→ 输出护栏
        → 输入法候选区展示（含理由）→ 点选注入聊天框 / 一键复制
```

- 意图与「是否真的需要回复」分开判定：**通知类消息默认不生成**（压打扰成本高于漏判成本）
- 伪历史按 token 预算从最旧开始截断，`system` 与当前消息永不丢弃
- 候选拆分与清洗有多层兜底（多候选挤在一段、忘带分隔符、标题标签混入、元话语拒答等）

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | **全部文档导航**（规格/设计/计划/调研/任务记录/质量报告） |
| `docs/需求规格说明书_高情商聊天回复助手.md` | SRS：范围、功能与非功能需求、验收口径 |
| `docs/概要设计说明书_高情商聊天回复助手.md` | 架构、模块划分、关键决策 |
| `docs/项目计划_EmoReply.md` | 里程碑与 WBS（含 `docs/plan-cards/` 任务卡） |
| `docs/optimization-prd-2026-09-27.md` 等 | 优化迭代 PRD / 概要设计 / 计划 / 跟踪矩阵 |
| `docs/t01360-pc-optimization-prd-2026-09-30.md` | 桌面端优化 PRD（FR-PC1~4） |
| `docs/pc-e2e-report-20261003.md` | 桌面端功能 × 体验 UI 全面测试报告 |
| `docs/t01362-sonar-fix-report-2026-09-30.md`、`docs/sonar-reports/` | 代码质量治理报告 |
| `docs/Android测试环境搭建指引.md` | 真机环境、ADB 与常见问题 |
| `docs/CHANGELOG.md` | 清理、构建与修复的时序记录 |

## 隐私与安全

- 只读取为生成回复所必需的内容；通知链路内容不入库、不外传，候选仅内存存续
- API Key 等敏感配置由用户自填并本机加密保存；**仓库内禁止提交任何密钥或含密钥的配置文件**
- 无障碍读屏为显式触发（点击读屏按钮读一次），无后台自动抓取

## 已知限制

- 概念验证阶段，未做服务端；联网能力仅限用户自填的云端 LLM 接口
- iOS 侧仅有工程代码，需 macOS 环境才能构建验证
- 部分 IM（如微信）主动保护无障碍节点树，故提供截图 + 端侧 OCR 回落路径；OCR 存在轻微识别波动
- 桌面端安装包**不入库**（产物可再生），需要时 `npm run dist` 约 2 分钟重建
- 云端生成质量、粘贴回宿主窗口兼容性仍需真机 + 真实 Key 复核（见 MTask 待办 T01500/T01502）

## 贡献

个人概念验证项目，暂不接受外部 PR；问题与使用反馈欢迎提 Issue。

开发约定（提交前请过一遍）：

- 新能力先落 `m1-poc`（Python 参考实现 + pytest 验收），通过后再镜像到 Kotlin / Electron 端，保持三端同构
- 提交前跑通 `m1-poc` 的 `python -m pytest -q` 与桌面端 `npm test`；渲染层改动需再过 `npm run test:e2e`
- 不提交任何密钥、安装包产物或真机截图等敏感内容

## 许可证

本仓库尚未包含 `LICENSE` 文件，版权归属以仓库所有者为准；在明确许可证前请视同「保留所有权利」。

## 致谢

- [Remix Icon](https://remixicon.com/)（MIT）：输入法图标行图标
- [Google ML Kit Text Recognition（Chinese）](https://developers.google.com/ml-kit/vision/text-recognition)：端侧 OCR
- 图标候选：[Agnes](https://www.agnes-ai.com/) 生成
