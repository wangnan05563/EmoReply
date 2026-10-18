# CHANGELOG

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
