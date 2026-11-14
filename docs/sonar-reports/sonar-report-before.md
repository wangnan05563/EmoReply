# EmoReply · SonarQube 扫描报告（修复前基线）

- 报告日期：2026-11-14
- 项目：`EmoReply`（SonarQube 项目 Key：`EmoReply`）
- 服务器：本机 SonarQube `26.1.0.118079`，`http://localhost:9000`
- 扫描器：`sonar-scanner 8.0.1.6346`（内置 JRE 21.0.9），Windows x64
- 扫描对象：`git archive HEAD` 导出的干净快照（415 个文件，不含 `.git` / `node_modules`），其中 Sonar 实际索引 159 个文件；文件相对路径与仓库 1:1 对应
- CE 任务：`81a81310-a173-4650-b027-d58a9cb62dbb`（SUCCESS）
- 原始证据：`docs/sonar-reports/scan-baseline.log`、`docs/sonar-reports/issues-baseline.json`

## 1. 结论摘要

| 指标 | 结果 |
|---|---|
| OPEN 问题总数 | **81** |
| BLOCKER | 0 |
| CRITICAL | **12** |
| MAJOR | 48 |
| MINOR | 21 |
| 类型分布 | CODE_SMELL 63 · BUG 17 · VULNERABILITY 1 |
| 修复前质量门 | 当时项目未配置硬性条件（Community Build 默认门为空），本次以 OPEN 数量为治理基线 |

## 2. 语言与范围

| 语言 | 代码行（ncloc） | 说明 |
|---|---:|---|
| Python | 7,843 | `m1-core`、`scripts` |
| Kotlin | 6,021 | `android-app` |
| JavaScript | 5,676 | `desktop-app` 主进程/渲染层/脚本 |
| Web（HTML/CSS） | 531 | `desktop-app/index.html` |
| Swift | 0 | 本机未安装 Swift 分析器，未覆盖 |
| **合计** | **20,071** | Sonar 实际索引 159 个文件 |

> 扫描排除了 `node_modules`、构建产物与二进制资源；快照方式用于规避 Windows 全仓遍历过慢与扫描器证书库枚举挂死两个环境问题（详见修复记录摘要）。

## 3. 严重度 × 类型矩阵

| 严重度 | CODE_SMELL | BUG | VULNERABILITY | 合计 |
|---|---:|---:|---:|---:|
| CRITICAL | 12 | 0 | 0 | 12 |
| MAJOR | 35 | 12 | 1 | 48 |
| MINOR | 16 | 5 | 0 | 21 |
| **合计** | **63** | **17** | **1** | **81** |

## 4. 规则分布（按数量降序）

| # | 规则 | 数量 | 类型 | 主要含义 |
|---|---|---:|---|---|
| 1 | `python:S1244` | 16 | BUG | 浮点数直接相等比较 |
| 2 | `javascript:S6582` | 15 | CODE_SMELL | 可用可选链简化空值判断 |
| 3 | `javascript:S2486` | 9 | CODE_SMELL | 异常被静默吞掉 |
| 4 | `python:S3776` | 7 | CODE_SMELL | 认知复杂度超阈值（>15） |
| 5 | `javascript:S3358` | 4 | CODE_SMELL | 嵌套三元表达式 |
| 6 | `javascript:S3776` | 4 | CODE_SMELL | 认知复杂度超阈值 |
| 7 | `javascript:S7781` | 3 | CODE_SMELL | `replace` 应使用 `replaceAll` |
| 8 | `javascript:S7780` | 3 | CODE_SMELL | 应使用 `String.raw` |
| 9 | `javascript:S7785` | 2 | CODE_SMELL | 顶层 async 调用可改顶层 await |
| 10 | `javascript:S1854` | 2 | CODE_SMELL | 赋值后未使用 |
| 11 | `javascript:S7735` | 2 | CODE_SMELL | 否定条件可正向表达 |
| 12 | `python:S125` | 2 | CODE_SMELL | 注释被当作注释掉的代码 |
| 13 | `python:S1172` | 2 | CODE_SMELL | 未使用的函数参数 |
| 14 | `kotlin:S7204` | 1 | **VULNERABILITY** | release 未开启混淆 |
| 15 | `kotlin:S3776` | 1 | CODE_SMELL | 认知复杂度超阈值 |
| 16 | `Web:S6819` | 1 | CODE_SMELL | 应使用原生语义元素 |
| 17 | `javascript:S7767` | 1 | CODE_SMELL | 冗余的 `undefined` 比较 |
| 18 | `javascript:S5850` | 1 | BUG | 正则分组优先级不明确 |
| 19 | `javascript:S7755` | 1 | CODE_SMELL | 访问末元素应使用 `at(-1)` |
| 20 | `javascript:S6397` | 1 | CODE_SMELL | 正则字符类可简化 |
| 21 | `javascript:S1481` | 1 | CODE_SMELL | 变量声明后未使用 |
| 22 | `python:S1481` | 1 | CODE_SMELL | 变量声明后未使用 |
| 23 | `python:S7500` | 1 | CODE_SMELL | 测试中生成器抛错写法不当 |

## 5. 文件分布

| 文件 | 问题数 | 主要规则 |
|---|---:|---|
| `desktop-app/updater.js` | 16 | S6582×7、S2486×3、S3358×1、S7781×1、S6397×1、S1854×1、S1481×1、S3776×1 |
| `desktop-app/scripts/close-ask-e2e.js` | 14 | S2486×5、S6582×4、S7781×2、S3776×1、S1854×1、S7767×1 |
| `m1-core/tests/test_t05727_baseline_eval.py` | 14 | S1244×14 |
| `m1-core/scripts/gen_baseline_corpus.py` | 7 | S3776×3、S1172×2、S1481×1、S125×1 |
| `desktop-app/scripts/updater.smoke.js` | 6 | S7780×3、S5850×1、S7755×1、S7785×1 |
| `desktop-app/scripts/update-e2e.js` | 5 | S7735×2、S3776×1、S3358×1、S2486×1 |
| `desktop-app/main.js` | 4 | S6582×4 |
| `desktop-app/renderer.js` | 3 | S3358×2、S7785×1 |
| `m1-core/tests/test_t05729_p1.py` | 2 | S1244 ×2 |
| `android-app/app/build.gradle.kts` | 1 | S7204 |
| `android-app/.../UpdateManager.kt` | 1 | S3776（复杂度 16） |
| `desktop-app/index.html` | 1 | Web:S6819 |
| `desktop-app/scripts/win-ctrl-e2e.js` | 1 | S3776（复杂度 19） |
| `m1-core/emoreply/baseline.py` | 1 | S3776（复杂度 49） |
| `m1-core/emoreply/preprocess.py` | 1 | S3776（复杂度 17） |
| `m1-core/emoreply/correction.py` | 1 | S125 |
| `m1-core/tests/test_t05730_correction_poc.py` | 1 | S7500 |
| `scripts/api_push.py` | 1 | S3776（复杂度 30） |
| `scripts/publish-release.py` | 1 | S3776（复杂度 16） |

## 6. CRITICAL 问题明细（12 项）

| # | 规则 | 文件:行 | 说明 | 复杂度 |
|---|---|---|---|---:|
| 1 | `kotlin:S3776` | `android-app/.../UpdateManager.kt:219` | `download()` 认知复杂度 16 | 16 |
| 2 | `javascript:S3776` | `desktop-app/scripts/close-ask-e2e.js:109` | `main()` 认知复杂度 39 | 39 |
| 3 | `javascript:S3776` | `desktop-app/scripts/update-e2e.js:92` | `main()` 认知复杂度 34 | 34 |
| 4 | `javascript:S3776` | `desktop-app/scripts/win-ctrl-e2e.js:53` | `main()` 认知复杂度 19 | 19 |
| 5 | `javascript:S3776` | `desktop-app/updater.js:137` | `download()` 认知复杂度 22 | 22 |
| 6 | `python:S3776` | `m1-core/emoreply/baseline.py:155` | `evaluate()` 认知复杂度 49 | 49 |
| 7 | `python:S3776` | `m1-core/emoreply/preprocess.py:800` | `vote_screen_batches()` 认知复杂度 17 | 17 |
| 8 | `python:S3776` | `m1-core/scripts/gen_baseline_corpus.py:95` | 场景构建函数认知复杂度 32 | 32 |
| 9 | `python:S3776` | `m1-core/scripts/gen_baseline_corpus.py:177` | `render_scene()` 认知复杂度 42 | 42 |
| 10 | `python:S3776` | `m1-core/scripts/gen_baseline_corpus.py:288` | 统计/主流程函数认知复杂度 16 | 16 |
| 11 | `python:S3776` | `scripts/api_push.py:65` | `main()` 认知复杂度 30 | 30 |
| 12 | `python:S3776` | `scripts/publish-release.py:426` | `_create_release()` 认知复杂度 16 | 16 |

## 7. 唯一安全漏洞（VULNERABILITY）

| 规则 | 文件:行 | 严重度 | 说明 |
|---|---|---|---|
| `kotlin:S7204` | `android-app/app/build.gradle.kts:26` | MAJOR | release 构建 `isMinifyEnabled = false`，未开启 R8 混淆，反编译可直接读取业务逻辑 |

> 该问题是本次扫描中唯一的安全类问题，修复方式是开启 release 混淆并补充 MediaPipe / ML Kit 的 keep 规则。

## 8. BUG 类问题（17 项）

| 规则 | 数量 | 位置 | 说明 |
|---|---:|---|---|
| `python:S1244` | 16 | `test_t05727_baseline_eval.py`（14）、`test_t05729_p1.py`（2） | 浮点结果用 `==` 精确比较，断言脆弱 |
| `javascript:S5850` | 1 | `desktop-app/scripts/updater.smoke.js:83` | 正则 `^(发现版本` + `发现新版本)` 的分组优先级不直观 |

## 9. 扫描口径与限制

1. **快照扫描**：为避免 Windows 证书库枚举挂死与全仓遍历过慢，使用 `git archive HEAD` 快照；`sonar.scm` 无 `.git`，日志出现 SCM provider 警告（仅警告，不影响分析）。
2. **Swift 未覆盖**：本机无 Swift 分析器，扫描不包含 Swift 代码。
3. **Hotspots API 受限**：`/api/hotspots/search` 对该 token 返回 403，修复前未取得安全热点清单；`/api/issues/search` 的 VULNERABILITY 仅 1 条（S7204）。
4. **无覆盖率数据**：基线未上传单元测试覆盖率，`coverage=0`，质量门中的覆盖率条件不参与本次问题治理判定。
