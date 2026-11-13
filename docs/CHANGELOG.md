# CHANGELOG

## 2026-11-13 — 端侧 LLM 纠错 POC：选型修正 + 评测骨架（T05730，T05725 阶段 4）

- **运行时选型修正（推翻 T05725 报告 §4.2 一处判断）**：MediaPipe GenAI 的 LLM Inference API **已原生支持多模态提示**（文本+图像输入，Gemma 3n E2B/E4B 变体，Android API 官方文档明确）——项目无需换 LiteRT/llama.cpp 运行时，升级 `tasks-genai`（现 0.10.35）+ 首次下载 Gemma 3n `.task` 即可；端侧基建与现有 `MediaPipeOnDeviceGenerator` 同栈。Gemma 3n E2B INT4 约 1.5GB 模型 / 6GB RAM 门槛 / 视觉编码+prefill 数秒的约束不变，「异步、用户显式触发、默认关闭」设计维持。
- **低置信纠错 POC 骨架（可复现）**：`m1-core/emoreply/correction.py`（事实源）——① 错误注入器 `inject_ocr_errors`：混淆对**全部来自项目内实测留痕**（昨→咋 时间行误读、形近字、图标残字），每行至多 1 处、可种子复现；② 纠错器协议 `TextCorrector`（行数契约：只改字不增删行）+ `NoopCorrector` 基线 + `OpenAICompatibleCorrector`（效果上限测量，可接云端/本地端点；行数破坏/网络失败回退直通的幻觉防护）。`scripts/eval_correction_poc.py`：注入 → 纠错 → `baseline.cer` 对比 → `correction-report.json`，含准入判定字段。
- POC 基线（本机无推理运行时，LLM 列空缺）：631 条 GT 消息注入 63 条（10% 场景）→ 注入后 CER **0.0189**、无误杀。**准入判定待端点接入**：复跑 `--endpoint` 得 LLM 纠错 CER 与误杀数，显著低于基线且无误杀才建端侧实装单，否则「不开」。
- 隐私红线（POC 阶段锁定）：云端端点仅允许喂合成语料；真实聊天内容的端侧实装走 MediaPipe GenAI，绝不经云端路径。
- 验证：新增 `test_t05730_correction_poc.py` 11 例；全量 pytest **548 passed** 零回归。
- 已知边界：图意摘要（范围第 2 项）无本机 VLM 运行时可测，效果与延迟待真机批（Gemma 3n `.task` 下载 + SM-G9980 实测）——人工评估流程：对同一批图片消息对比「有/无图意摘要」的候选可用率。

## 2026-11-13 — 读屏结构优化 P1：置信度分级贯通 + 行框几何动态分界（T05729，T05725 阶段 2）

- 范围裁剪（诚实标注）：四项 P1/P2 中本轮落地 **P1-1 置信度分级**与 **P1-3 行框几何替代硬编码**——两项均可离线验证；**P1-2 对比度增强（CLAHE）与 P2-4 UI 层级先验挂账**——评估报告自注「需真机验证不伤正常图」，无设备盲改渲染链路/裁剪窗口风险大于收益，待真机批随 T05727/T05728 验证同批补做。
- **置信度分级（P1-1）**：`OcrLine.confidence`（py 事实源，默认 1.0 向后兼容节点树路径）与 `OcrLineInfo.confidence`（kt，ML Kit `line.confidence` 贯通——此前该字段被丢弃未用）；`is_low_confidence`/`lowConfidence`（阈值 0.6，疑似行不静默删除）；行级诊断日志追加 `c=` 字段（回放脚本 txt 捕获止于引号，追加不破坏解析）；`collect_baseline_device.py` 解析 `c=` 进行框数据；`ocr:` 汇总日志新增 `lowconf=` 计数。
- **行框几何动态分界（P1-3）**：`infer_side_boundary`（py）+ `sideBoundaryPx`（kt 同构）——行框左缘排序聚簇，呈「显著双列」（恰 2 簇、每簇 ≥2 行、右簇左缘 ≥ 屏宽×0.2）时簇间隙中点即分界；单列/碎簇/证据不足一律回退 None，绝不比 T01143 固定比例（0.3125）更激进。`mine_at`/`mineAt` 双口径判侧；三处接线：OCR 路径 `judgeSides` 位置回退、节点树路径前缀标注、`fuseNodeSides` 节点侧别。
- 收益锚点（单测锁定）：右列短我方气泡左缘低于固定阈值 450 时，旧判法误判「对方」，动态分界修正（核心场景=短回复「嗯」「好」被反向）。
- 验证：m1-core 新增 `test_t05729_p1.py` 13 例；全量 pytest **537 passed** 零回归；Android `assembleDebug --offline` 编译通过；T05727 合成基线复跑六项指标**完全不变**（验收标准「无退化」达标）。
- 已知边界：动态分界的真机收益（机型/字号矩阵）与耗时增量（≈0，纯列表聚类）待真机以 `lastSideStats` 量化；CLAHE/UI 层级先验两项挂账见上。

## 2026-11-13 — 节点树×OCR 交叉验证判侧 + 跨屏变体投票（T05728，T05725 阶段 1）

- 背景：读屏此前是「节点树为空才走 OCR」的二选一，两个证据源从未交叉（见 [`评估报告`](聊天界面OCR优化与大模型引入评估报告.md) §5.1）。
- **节点树证据覆盖 OCR 判侧（P0-1）**：`m1-core/emoreply/preprocess.fuse_node_sides`（事实源）+ `EmoReplyReaderService.fuseNodeSides`（kt 同构，接入 `captureViaScreenshot`）。OCR 行中心落在节点树 TextView 框内且文本相似（LCS ≥0.6，OCR 错一字可容错）时，节点框左缘位置判定视为硬证据覆盖 OCR 侧别；无匹配行保持原判。微信节点树被保护 → 证据源为空 → 静默跳过；kt 侧额外保护 byColor 高置信颜色结论不被位置证据覆盖（T02222 判定层级）。`collectNodeLines` 与 `captureVisibleMessages` 同口径只读 text/bounds，无节点动作。
- **跨屏变体投票（P0-2）**：`preprocess.vote_screen_batches` + `voteScreenBatches` 同构。旧 `mergeScreenBatches` 按整行精确去重——滚动重叠区同一行被 OCR 读出两个变体（错字/空格漂移）时双行并存污染上下文；现把归一化相似 ≥0.8 的行聚簇取众数（并列取先出现者），时序按首现位置。`captureMultiScreen` 改接投票合并；kt 旧函数删除（py 版保留为 T01145 契约回归锚点，精确重复场景行为一致）。
- 验证：m1-core 新增 `test_t05728_fusion_vote.py` 12 例（硬证据覆盖/几何不匹配跳过/文本不相似不误配/左右缘比例判侧/OCR 错字容错/投票并簇取众数/时序首现/并列取先）；全量 pytest **524 passed** 零回归；Android `assembleDebug --offline` 编译通过（JDK17 + Gradle 8.9），APK 已产出。
- 已知边界：真机（SM-G9980 / Android 15 / 微信）回测未执行（设备离线）——融合在微信主场景为空路径，预期收益面是钉钉/QQ/短信等节点树可读应用；T05727 合成基线指标不变（融合属捕获层，回放器不触发），侧别收益待真机以 `lastSideStats` 与基准集量化。

## 2026-11-13 — 读屏 OCR 基准数据集与评测工具（T05727，T05725 阶段 0）

- 背景：项目此前无 OCR 基线指标，bad case 全靠用户反馈发现，改动的「提升」无法证伪（见 [`评估报告`](聊天界面OCR优化与大模型引入评估报告.md) §2.3）。本单补齐评测基建，真机采集部分待设备接入后补跑。
- `m1-core/emoreply/baseline.py`（事实源，可单测）：GT 语料 schema（行级 text/kind/side/msg 分组 + 行框几何，`msg` 把折行物理行归并为消息级比对粒度）、预测 JSONL schema、五项指标——消息级召回 / 侧别准确率 / 行级 CER（Levenshtein，去空白——tesseract 按字空格不扣分）/ 噪声误入率（分母剔除时间标记行）/ 对话可解析率（`split_dialogue_turns` 能出 current 的图占比）。
- `m1-core/scripts/gen_baseline_corpus.py`：合成语料生成器，96 张 1080×2340 PNG（App 微信/钉钉/QQ/短信 × 明暗主题 × 单聊/群聊 × 字号 1.0/1.3，种子可复现），含时间分隔行、群昵称行、噪声行（占位符/乱码/角标/通话提示）、折行消息、三种图片消息形态（高噪照片/银行绿表头/灰阶拍屏）。产物落 `m1-core/baseline/`（已入 `.gitignore`，可再生成不入库）。
- `m1-core/scripts/eval_ocr_baseline.py`：评测执行器。`--pipeline` 模式把 GT 行框当完美 OCR 输入回放读屏后处理链路（剔簇 → 折行合并 → 非文本噪声 → 群噪声 → 判侧 → 前缀，与 `captureViaScreenshot` 同序），测结构链路；`--predictions` 模式对真机采集的预测评分（CER/召回的真实数字）。产出 `report.json`。
- `m1-core/scripts/collect_baseline_device.py`：真机采集器——解析 adb logcat 的 `ocr: line` 行框与 IME `via` 标记，导出预测 JSONL 与节点树可读率 `readability.json`（决定 T05728 交叉验证收益面）。
- `m1-core/tests/test_t05727_baseline_eval.py`：23 例（CER 口径 / 五指标边界 / 语料 IO 往返与非法 kind / 生成器结构不变量 / 回放器时间行判定）。全量 `pytest` **512 passed** 零回归。
- **合成语料基线（pipeline-replay，完美 OCR 输入）**：召回 93.34%、侧别 99.32%、CER 0、噪声误入 27.10%、对话可解析 83.33%。两项泄漏均为真实链路行为而非语料 bug：① 140 条群昵称行——`strip_group_noise` 规则 3 只删「下一行以前 2 字开头」的前缀昵称，非前缀昵称泄漏（T01138 设计内边界）；② 79 条图内文字——白色字形污染环带取样被判成气泡证据、簇过滤失效（银行白底残留 T02305 已挂账；白字压花照片场景疑为真实失效模式，**待真机复证**）。此两项即基线锁定的改进空间。
- 生成器两轮自纠：气泡间距 28px < 行高导致时间行/昵称行与相邻消息被折行合并并轨（召回虚降 61%），改 `1.5×字号`；GT 几何按文本匹配回填使重复短语共用同一坐标，改 GT 行对象直挂 row 渲染时回填。
- 已知边界：合成语料的 CER 不代表真机识别水平（PIL 渲染 ≠ 真机字形/抗锯齿）；真机采集链路（collect 脚本）未实测（当前无设备连接）；频次/头像字形过滤未复刻进回放器。

## 2026-11-10 — SRS/概设实现状态标注：P2 未落地项与云端服务确认放弃

- 背景：对照 [`需求规格说明书`](需求规格说明书_高情商聊天回复助手.md) 逐条核查四端代码后，用户确认：P2 中未落地的增强项与云端服务相关需求**已放弃**（架构已改为端侧直连 OpenAI 兼容 LLM 接口，无自建云端服务）。
- [`需求规格说明书`](需求规格说明书_高情商聊天回复助手.md)：头部加实现状态声明；FR-3.4（用户微调）、FR-4.3 反馈上云部分、FR-5.1 多端同步部分、FR-5.3 可选上云部分逐条标注放弃；FR-6.1 补实现现状注（正文零持久化，严于 7 天上限）；附录 B 新增「实现状态补注」表（含放弃清单与现状表）；附录 C-3 的 C29 标注放弃。
- [`概要设计说明书`](概要设计说明书_高情商聊天回复助手.md)：头部加 2026-11-10 确认声明；§5.1 端↔云接口整节标注放弃（仅历史记录保留）；§10 中 C2/C15、C17 标注放弃。
- 明确**不在**放弃范围：FR-1.6 维持二期不实现的原决策；C31 测试连接已在 Android 端实现（`MainActivity.kt`）；FR-6.3 关闭云生成开关未实现但未放弃（暂缓）。

## 2026-11-09 — patch 号同日唯一性加固（T04726）

- 核验 `scripts/Gen-PatchVersion.ps1`：`YYYYMMDD-NNN` 格式、序号持久化在 `.build/patch-seq.json`、同日 +1 / 跨天重置、写回 `desktop-app/package.json` 的 version 并读回校验；`构建打包.bat` 与 `PC构建打包.cmd` 均在打包前调用。项目无根 `package.json`，发布配置 `version_file_path` 也指向 `desktop-app/package.json`，故写入位置为项目适配。
- 缺口（隔离沙盒实测）：并发启动 4 次构建时 3 次生成同一个 `20261109-001`、1 次写入失败；`.build/patch-seq.json` 损坏后会重置为 001，重用当天已用号。二者都违反「同日全局唯一」。
- 修复：新增跨进程锁 `.build/patch-seq.lock`（`FileShare.None` + 100ms×300 重试，进程退出由 OS 自动释放），把「读序号 → 写 version → 写序号」整体串行化；序号状态缺失/损坏时，从 package.json 当前 version 解析当天序号并 +1（兼容 `-Semver` 前缀），不再从 1 重来。
- 验证：隔离沙盒 5 组用例全过（顺序递增、跨天重置、JSON 合法性、4 并发唯一、状态损坏恢复）；`scripts/verify.ps1` 10 PASS / 0 FAIL。
- 已知边界：一键发布 `一键发布.bat`（`build-publish.ps1` 的 `build_pipeline`）不经过 patch 号生成步骤，版本号沿用上次构建值；如需「每次发布也唯一」，可在 `config.json` 的 build_pipeline 首步加入 `Gen-PatchVersion.ps1`，本次未改动（超出任务范围）。

## 2026-11-09 — 构建产物集中至 release（T04719）

- 桌面端 electron-builder 输出由 `desktop-app/dist` 改到 `release/desktop-installer`（`desktop-app/package.json` 的 `build.directories.output`，相对路径）；`setup-and-build-desktop.ps1` 从该目录搬运 NSIS 安装包到交付目录 `release/desktop`，工程内不再留 `dist/` 残留。
- `scripts/config.json`：一键发布的 2 处桌面资产目录改到 `release/desktop-installer`；新增 `release_cleanup` 配置段（默认 enabled / scope=all / 不清理缓存）。Android 资产仍取 `release/build/app/outputs/apk/debug`，与 `docs/品牌图标与接入.md` 的证据引用一致。
- 新增 `scripts/clean-release.ps1`：按 `-Scope desktop|android|all` 清理旧产物，默认保留 `release/toolchain`、`npm-cache`、`gradle-home`、`gradle-project-cache`、`kotlin` 等重建代价高的缓存；支持 `-PurgeCaches` / `-PurgeAll` / `-WhatIf`，删除前校验目标必须位于工程根内。
- 三个构建入口在构建前调用清理：一键发布 `build-publish.ps1`（scope=all）、`setup-and-build-desktop.ps1`（desktop）、`setup-and-build-android.ps1`（android，只清 APK outputs 以保留增量构建）。发布脚本另加「资产目录必须位于 `release/` 下」的校验。
- `release/` 与 `desktop-app/dist/` 均已在 `.gitignore`；发布说明 `release-notes-<VER>.md` 属人工输入文档，不放入一次性目录。
- 注意：`docs/品牌图标与接入.md` 引用的真机核对 APK 位于 `release/build/app/outputs/apk/debug/`，属一次性产物；执行 android/all 清理后需重新构建 Android 才会恢复。
- 验证：`scripts/verify.ps1` 脚本编码/语法校验、JSON 解析、路径引用一致性、临时目录清理演练（产物删除 + 缓存保留）。完整 `npm run dist` / Gradle 构建与 GitHub Release 发布属外部动作，未实跑，待后续按需执行。

## 2026-11-02 — 系统架构设计文档（T04074）

基于四端源码（`m1-poc` Python 事实源 / `android-app` Kotlin / `desktop-app` Electron JS / `ios-app` SwiftPM 库）整理 [`系统架构设计`](系统架构设计.md)：

- 章节：系统概述、总体架构、核心管线（跨端同构）、模块详细设计、数据流分析、技术选型、部署架构、安全与隐私、跨端一致性、附录。
- 含 8 张 Mermaid 图：总体架构、核心管线、Android 模块、桌面模块、Android 通知/读屏序列、桌面链路、部署架构。
- 严格对应现有实现，未假设不存在的功能；FR/C/T 编号均来自代码注释可追溯标记。
- `docs/` 文件数 10 → 11（10 Markdown + 1 docx）。

## 2026-10-31 — 工作空间清理（workspace-cleanup 技能，第 4 轮）

配置驱动（`cleanup-config.yaml`）。按用户选定「安全项 + 可重建产物」档执行，**保留**工具链缓存与依赖目录。

### 处置汇总（口径：logical bytes）

| 桶 | 项数 | 体积 | 明细 |
|---|---|---|---|
| delete | 1209 文件 | **1189.95MB** | `desktop-app/dist/` 534MB（NSIS/portable/win-unpacked，`npm run dist` 可重建）；`release/desktop/` 311MB（electron 解包产物）；`release/build/` 其余 873 文件 337MB（Android 构建中间物）；`.cleanup-quarantine/` 4 批次 5.6MB（2026-10-03 隔离，72h 窗口已于 10-06 到期）；`__pycache__`×3 + `.pytest_cache` 1.1MB；gradle/kotlin/npm 构建日志 4 个 |
| quarantine | 0 | — | 空桶原因：全部候选可再生，无可逆性因子命中，无需占用隔离窗口 |
| review→KEEP | — | — | `node_modules/`、`.env`、`.build/patch-seq.json`、`tmp/_mtask3`、`release/gradle-home`(1.10GB)/`toolchain`(129MB)/`gradle-project-cache`、`android-app/.gradle`、`.qoder-cn/`、`.idea/` |

### 证据保留
- `release/build/app/outputs/apk/debug/` 的 `app-debug.apk`(45.9MB) + `output-metadata.json` **原地保留**：`docs/品牌图标与接入.md` 引用该 APK 作真机核对证据。
- `release/` 属含糊目录名，按 README L230 与 `.gitignore` L12 的声明判定为 gradle 构建输出目录，非手写源码。

### 验证
- m1-poc pytest **489 passed**（`python -B -m pytest -q -p no:cacheprovider`）。
- 硬校验存在性 7/7；稳定性复扫无 `__pycache__`/`.pytest_cache` 复发。
- 数量守恒：desktop-app −534.4MB、release −648.9MB、m1-poc −1.1MB 与审计合计吻合，无未解释缺口。

### 审计与留痕
- `cleanup-logs/cleanup-targets.txt` 追加 round4 执行段（逐文件 size + sha256，≥1MB 用 `P:` 采样指纹）。
- 报告：[cleanup-report-20261031.md](../cleanup-logs/cleanup-report-20261031.md) 与同名 `.json`。

### 配置漂移（清理后已修复，2026-10-31）
- `git_integration.enabled` → `true`：仓库已于 2026-10-18 初始化；`auto_commit` 仍为 `false`，不自动提交。
- `ignored_audit.enabled` → `true`、`quarantine.gitignore` → `true`、`archive.auto_update_gitignore` → `true`：这三处的原因注释均为「无 git」，前提已失效。
- `platform.venv_python` / `verification.venv_python` → `F:/Program Files/Python3.14/python.exe`：原 3.13.12 无 pytest 模块，实测新解释器 pytest 9.1.1 跑 489 例通过。
- `verification.command` 的 `cd … &&` 改为 `Set-Location …;`：PowerShell 5 不支持 `&&`，原命令不可执行。
- 保留的保守项：`auto_add_inferred_to_gitignore`、`auto_commit`、`auto_update_precommit` 仍为 `false`（推断误判与自动提交的风险高于收益）。

## 2026-10-30 — docs 目录文档精简（人工整理）

按"保留规格/设计/PRD/环境类持久文档、删除一次性过程文档、相关文档合并"原则整理 `docs/`：

- **删除一次性过程文档（9 个）**：`t01055-*`×3（RAG 验证）、`t01081-fix-result`、`t01082-multiturn-result`、`t01099-tsf-proto`、`t01163-followup-icon-integration`、`t01339-device-recheck-checklist`、`t01362-sonar-fix-report`。
- **合并 8 → 2**：
  - `品牌图标与接入.md` ← `t01163-logo-result` + `t01164-icon-integration-result`（原 2 个源文件删除）。
  - `设计决策记录.md` ← `t01083-settings-redesign` + `t01097-multiplatform-ingress-assessment` + `t01343-readscreen-optimization` + `t01345-dingtalk-smart-reply-analysis` + `T02222-气泡颜色判方向`（原 5 个源文件删除）。
- **删除 36 张细粒度任务卡** `plan-cards/W001–W036.md`，仅保留 `plan-cards/INDEX.md` 作为模块级路线图概览（清理失效链接）。
- **删除 `sonar-reports/` 目录**（Sonar 增量修复报告，属过程快照）。
- **同步更新** `README.md`（索引去死链、补两合并文档）与 `plan-cards/INDEX.md`。

效果：`docs/` 由 **74 个文件 → 23 个**（精简约 69%）。

## 2026-10-03 — 工作空间清理（workspace-cleanup 技能，第 1 轮）

配置驱动（`cleanup-config.yaml`），无 git 仓库 → 非可再生目标一律隔离（S1 门）。

### 处置汇总（口径：logical bytes）

| 桶 | 项数 | 体积 | 明细 |
|---|---|---|---|
| delete | 158 文件 + 9 目录 | **623.90MB** | dist/ 578.39MB（77 文件，npm run dist 可重建）；chi_sim.traineddata 冗余副本 42.31MB（sha256 与 assets/ocr 正本一致）；chi_sim_int.traineddata 2.36MB（弃用 int8 模型，零引用，用户授权）；__pycache__ ×6（79 个 .pyc，m1-poc/tools/scripts/tmp）；.scannerwork/ |
| quarantine | 61 项 | 2.81MB | 运行日志 ×18（sonar-scan/e2e-run/electron-dev/pkg-exe/build-dist）、ocr_test*.png、e2e-profile/（测试 profile，数据类目录隔离不删）→ `.cleanup-quarantine/20261003-205530/`（含 manifest.json + restore.py，72h 恢复窗口） |
| review→KEEP | — | — | node_modules/（依赖）、brand/、docs/、perf/output、tmp/_mtask3、assets/、.workbuddy/ |

### 验证

- 冒烟 6 套 54 例全绿（含 OCR：证实 int8 删除无害）。
- m1-poc pytest **380 passed**（`python -B`，无字节码回写）。
- 硬校验 7 项存在性全部 OK（README / sonar 配置 / main.js / package.json / chi_sim 正本 / __init__.py / _mtask3）。
- 数量守恒：desktop-app −44.67MB 与两模型文件字节数精确吻合；tmp −0.07MB = sonar 日志；余项为 before 粗表 1 位小数舍入噪声，无未解释缺口。

### 审计与留痕

- `cleanup-logs/cleanup-targets.txt`：全部目标逐文件 sha256（>1MB 采样指纹 P: 标注）。
- 已知缺口如实补录：首轮执行器崩溃导致 traineddata 副本哈希未落盘，以删除前 sha256sum 证据 backfill（与正本同哈希）。
- 首轮扫描缺口（漏 scripts/tmp 的 pycache）由稳定性检查捕获并补删（2 文件 0.03MB），已入审计。

### 防复发

- 无 git，.gitignore 不适用；`cleanup-config.yaml` 已固化本轮全部模式（cache_dirs/runtime_logs/verified_duplicates），下轮直接复跑。
- 可再生产物（dist/）如需长期保持磁盘整洁，建议构建后手动清理或纳入周期性维护（本配置已覆盖）。

## 2026-10-03 — 清理第 2 轮（全模式复扫 + 散落脚本归位）

| 桶 | 项 | 说明 |
|---|---|---|
| delete | `m1-poc/.pytest_cache/`（5 文件） | pytest 缓存，可再生 |
| quarantine | `desktop-app/kb.smoke.js`（2945B） | **被取代的过期副本**：T01055 时代旧版（69 行/10 断言），现行版在 `desktop-app/scripts/kb.smoke.js`（112 行/6 例，实际在跑）。因文档 `docs/t01055-desktop-rag-result.md` 引用旧路径作证据，按可逆原则隔离而非删除（`.cleanup-quarantine/`，72h 窗口）；引用语义以现行版为准 |

验证：`node scripts/kb.smoke.js` 6/6 绿（归位后复跑）。全模式复扫其余项零残留（tmp 后缀/os 垃圾/coverage/pyc）。

## 2026-10-03 — 清理后一致性收尾（Sonar 口径 + 测试入口）

- `sonar-project.properties` 排除新增 `**/cleanup-logs/**`、`**/.cleanup-quarantine/**`：清理工具产物不参与质量扫描（否则审计 JSON/MD 被当源码扫出噪声）。
- `desktop-app/package.json` 注册标准入口：`npm test`（6 套冒烟串行，54 例）与 `npm test:e2e`（CDP 端到端）；实测 `npm test` exit 0。
- Sonar 复扫：21 → **13 OPEN**。新增 9 条中 8 条为当日新增代码（renderer 主题功能 S7764×3/S7761×2、ui-e2e S6582/S1481/S1854）已全部修复；剩余 13 = S7785×9（CommonJS 不适用）+ kotlin:S7204 / S107×2 / python:S107（T01362 有意保留）。修复后 E2E 51/51 复跑确认无回归。
- 本轮 E2E 测试 profile（`e2e-profile2`）已隔离，保持工作区零测试残留。

## 2026-10-03 — 文档资产补齐（导航索引 + README 现状对齐）

- 新建 `docs/README.md`：32 份文档按「规格与设计 / 优化迭代 PRD / 质量与测试报告 / 任务验证记录 / 环境与运维」五类索引，并固化命名约定（`t0xxxx` = MTask 任务号）与维护提示。
- `README.md` 对齐现状：pytest badge 260→380、删除已不存在的 `release/` 目录说明、补桌面端能力段（多模型并行/流式/本地 HTTP API/离线 OCR/明暗主题）、补 `npm test` / `npm run dist` / E2E 三步命令、补「已知限制」（安装包不入库、真机复核待办）、文档表改为指向索引。
- 链接自检：README 8 条 + docs/README 35 条内部链接全部可达（0 broken）。

## 2026-10-03 — 清理第 3 轮（周期性维护）

- delete：`.scannerwork/`（sonar 扫描重建）、`desktop-app/tmp/e2e-profile/`（**19 空目录 0 文件**，第 1 轮隔离移动的残留空树，执行前断言空）。
- quarantine：5 文件 44KB（sonar-scan5/6、sonar-issues6.json、e2e-run8、electron-dev5），批次 `20261003-215654`。
- **复发根因修复**：验证用的 `-B` 只挡字节码不挡 pytest cacheprovider → `.pytest_cache` 每跑必重建。配置固化 `pytest -q -p no:cacheprovider`，并把 `.pytest_cache` 补进 `stability_check.recurrence_patterns`。
- 验证：npm test 6 套绿 + pytest 380 passed + 存在性 9/9；残留复扫干净。隔离区累计 4 批次，10-06 起可一次性清理。

## 2026-10-03 — 桌面端安装包瘦身 + OCR 缓存缺陷修复

- **缺陷修复（影响已发布版本）**：`ocr.js` 未设 `cacheMethod`，而 tesseract.js 在加载语言数据后会把**解压后的模型回写**到 `cachePath`（缺省 = 进程 CWD）——首次使用截图取文时会尝试在 CWD 写出 42MB `chi_sim.traineddata`（权限不足则仅告警失败）。实测复现：assets/ocr 被回写出 44,366,093 字节副本。修复：`cacheMethod: 'none'`（`ocr.js` 与 `scripts/ocr.smoke.js` 同步）。
- **模型形态 A/B 实测**（同 commit，仅差模型形态）：

| 方案 | 安装包(NSIS) | 安装后 asar |
| --- | --- | --- |
| 未压缩 + 旧代码（有回写缺陷） | 113.4MB | 96.4MB |
| **未压缩 + cacheMethod:none（采纳）** | **111.8MB** | 96.4MB |
| .gz(gzip -9, 19MB) + cacheMethod:none | 116.5MB | **71.9MB** |

  结论：NSIS 的 LZMA 对未压缩模型压得比 gzip 更好，故**安装包最小的是未压缩形态**（较 9-01 基线还小 1.6MB，因删除了弃用的 chi_sim_int 2.4MB）；若更在意安装后磁盘占用可切 .gz（代价 +4.7MB）。`ocr.js` 保留 gz 自动探测（有 .gz 用 .gz，否则回落），切换零改码。
- 验证：OCR 中文识别 **confidence 95**（与改动前一致，gunzip 后 sha256 与正本相同）、`npm test` 6 套 54 例全绿、asar 内仅含正本模型且不再产生回写副本。
- 遗留优化点：asar 中 `tesseract.js-core` 的 **6 个 wasm 变体共 48.6MB**（simd/relaxedsimd/lstm × js/wasm），按运行时选择逻辑裁剪可再省数十 MB，但需先摸清选择规则，暂不贸然剪。

## 2026-10-03 — 桌面端安装包二次瘦身（tesseract wasm 变体裁剪）

- **裁剪依据（先摸清选择规则再动手）**：`tesseract.js/src/worker-script/index.js:42` 传给 `getCore` 的第一参是 **`lstmOnly` 布尔值**（并非 oem 数字），而 `getCore.js` 内部拿它与 `[OEM.DEFAULT=3, OEM.LSTM_ONLY=1]` 数组比对 —— 布尔值永不命中，故**实际总是走非 lstm 分支**。
  第一次尝试按「OEM=1 只用 lstm 变体」裁掉 6 个非 lstm 文件，OCR 冒烟立即报 `Cannot find module 'tesseract.js-core/tesseract-core-relaxedsimd'` 而失败 → 结论：node 端 6 组 `.js`+`.wasm`（simd/relaxedsimd/无 × lstm/非 lstm）**全部可能用到**。
- **真正可裁的只有 `*.wasm.js`**：这 6 个是 browser 端「wasm 内嵌为 base64 的单文件版」，node 端走 `fs.readFileSync(.wasm)` 从不加载 —— 共 **26.8MB**。
- **落地方式**：`package.json` `build.files` 增加 `"!node_modules/tesseract.js-core/*.wasm.js"`（不删 node_modules，避免被 `npm i` 还原、也避免开发态与打包态不一致）。

| 指标 | 裁剪前 | 裁剪后 |
| --- | --- | --- |
| asar | 96.4MB | **67.4MB** |
| NSIS 安装包 | 111.8MB | **99.6MB** |
| portable | 111.4MB | 99.2MB |

- **新增打包态回归 `desktop-app/scripts/ocr-asar-check.js`**：纯 node 冒烟跑的是 node_modules，**不覆盖 asar 内模块解析**（纯 node 也无法穿透 asar，只有 Electron 的 fs/require 补丁能读）。该脚本用 Electron 直接 `require('<app.asar>/ocr.js')` 对中文测试图做端到端识别。
  首次运行出现「未识别到关键词」误报 —— 实为 tesseract 中文 PSM=6 按字输出并以空格分隔（`"周 未 一 起 爬 山 好 吗"`），断言未剥空白所致；已修断言（`replace(/\s+/g,'')` 后匹配），**产品代码无问题**。
- 验证：asar 内 js-core 剩 12 文件（LICENSE/index.js/package.json + 6 组 .js/.wasm）；打包态 OCR **confidence 95** 两句全对；`npm test` 6 套 54 例全绿；生产 exe 冒烟 `/health` 200、`/generate` 2 候选 degraded=true、404 正确。

## 2026-10-07 — T01392/T01537/T01538/T01554「下一步建议」批量实施

按四张任务单回写末尾的「下一步建议」逐条处置，结论各异：T01392 建议早已闭环、T01554 建议本轮落地、T01537/T01538 建议因真机缺席仍阻塞。

### T01554 建议（可落地，本轮完成）：同构镜像到 m1-poc + 桌面端

源真值是 Android 侧 T01554（读屏乱码 + 理由列错位）的两个修复，此前只改了 kt，m1-poc 事实源与桌面端没有同步。**镜像过程中发现并修掉了两个真实缺陷**：

1. **启发式拆分会截断真实候选（修）**：kt 原实现的关键词表含弱词「说明」且**不要求关键词后跟冒号**，于是
   `好的，我说明一下情况` 会被砍成候选「好的，我」+ 理由「说明一下情况」——把整句拦腰截断，比原 bug（理由混入候选）更糟。
   三端统一收紧为：① 只留强标签词（理由/原因/解释/rationale/explanation），去掉口语动词「说明」；② 关键词后必须紧跟冒号。
   中文「理由：」多被既有 INLINE_RATIONALE 先行命中，故启发式的实际增量是**英文关键词**场景。
2. **桌面端 `TRAIL_PIPE` 正则移植失效（修）**：py 原版 `[\s|]+\Z` 照抄到 JS 变成 `/[\s|]+Z/`——JS 无 `\Z`，
   退化成字面字母 Z。后果是双向的：悬空管道符**没被剥掉**（T01131 在桌面端形同失效），
   且正常正文里的 " Z" 会被误删（`请把材料送到 Z 区前台` → `请把材料送到 区前台`）。改为 `/[\s|]+$/`。

落地范围（三端同构）：
- `m1-poc/emoreply/preprocess.py`：新增 `is_garbled_text`（四重判据：控制字符 / 有意义字符占比<70% / 连续3+非文字符号 / 纯符号行）并接入 `filter_non_text_noise`
- `m1-poc/emoreply/sanitize.py`：`split_rationale` 加错位交换 + `_heuristic_rationale_split`（收紧版）
- `m1-poc/tests/test_t01554_garbled_and_split.py`：新增 21 例，含 3 条误伤回归锁
- `desktop-app/sanitize.js`：同名两修复（错位交换 + `heuristicRationaleSplit` + TRAIL_PIPE 订正）
- `desktop-app/ocr.js`：新增 `isGarbledText` 并接入 `isNoiseLine`（与 Android/m1-poc 同构）
- `android-app/.../core/Sanitize.kt`：回修启发式弱词缺陷，与另两端对齐

验证：m1-poc **429 passed**（原 408 + 新增 21，零回归）；桌面端 `sanitize.smoke.js` 33→**44 例**、`ocr.smoke.js` +12 例、`npm test` 全绿 exit 0；Android `gradle :app:assembleDebug` **BUILD SUCCESSFUL**（16s）。

### T01392 建议 → 已闭环，无需再做
建议指向 T01393（流式 + 清洗最终态），而 T01393 状态已是 **done + verified=true**。代码复核确认落地：`main.js` 流式推原文（L240/365）、resolve 后 `sanitizeCandidates` 作用最终态（L268/368），`renderer.js` L228 用清洗后候选整体替换流式区。

### T01537 / T01538 建议 → 真机缺席，本轮未执行
两者建议的下游动作（T01539 真机复核 16 项、T01540 multiTone 延迟基线、通知链路低采纳场景复现）都依赖 Android 设备：**本机 `adb devices` 无设备接入**，且 T01539 执行纪律要求「会产生 LLM 调用/消耗云端配额的项先向用户确认」。未擅自推进。
