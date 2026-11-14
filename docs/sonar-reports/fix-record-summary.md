# EmoReply · SonarQube 问题修复记录摘要

- 日期：2026-11-14
- 项目：`D:\code\otherProjects\EmoReply`（分支 `main`，HEAD `832fce5`）
- 结果：**OPEN 81 → 7 → 0**，两轮修复、两轮重扫；未使用 `NOSONAR` 抑制
- 改动规模：19 个已跟踪文件修改，1 个新增文件（`android-app/app/proguard-rules.pro`），未 commit

## 1. 修复原则

1. **根因修复，不抑制**：所有问题按规则语义改代码；未新增任何 `NOSONAR` / 规则关闭。
2. **行为不变**：重构只做提取与等价改写；对外返回值、异常分支、RNG 消费顺序、UI 时序均保持原语义。
3. **测试兜底**：每轮修复后先重扫清零，再跑 Python 548 项、桌面冒烟、4 套 E2E；任一失败即回退排查。
4. **最小改动**：不顺手改无关代码；E2E 重新生成的截图已还原，保持 diff 只含修复。

## 2. 按文件修复明细（81 项）

| # | 文件 | 问题数 | 规则分布 | 修复动作 | 验证 |
|---|---:|---|---|---|---|
| 1 | `desktop-app/updater.js` | 16 | S6582×7、S2486×3、S3358×1、S7781×1、S6397×1、S1854×1、S1481×1、S3776×1 | 嵌套三元拆 `if`；`sanitizeName` 改 `replaceAll('\\','/')`；`pickAsset` 用 `release?.assets`；删未用变量；`check()` 去 try/catch，改 `res.json().catch(()=>null)`、`URL.canParse`、`String(e?.message||e||'')`；`download()` 拆 `prepareDownload()` + 模块级 `writeBody()`（复杂度 22→约 6）；headers 用 `res.headers?.get?.(...)`；`install()`/`streamOf` 同步简化；`info?.state` 合并空值判断 | `node --check`；UPDATER SMOKE 45 PASS；update-e2e 19/19 |
| 2 | `desktop-app/scripts/close-ask-e2e.js` | 14 | S2486×5、S6582×4、S7781×2、S3776×1、S1854×1、S7767×1 | `main()` 复杂度 39 拆为 `requireProfileArg` / `requireIsolatedProfile` / `runAskCases` / `quitViaCloseButton` / `runNativeCloseCase` / `sendWmClose` / `describeStillAlive` / `waitForPidExit`；5 处空 catch 改为引用异常并 `console.debug/warn`；空白归一改 `replaceAll`；删无用初值；`Math.trunc` 替代位运算取整 | close-ask E2E 11/11 |
| 3 | `m1-core/tests/test_t05727_baseline_eval.py` | 14 | S1244×14 | `== 0.0` / `== 1.0` 改 `pytest.approx(...)`；`_scene()` 跟随 `build_scene` 新签名 | pytest 548 passed（定向 47 passed） |
| 4 | `m1-core/scripts/gen_baseline_corpus.py` | 7 | S3776×3、S1172×2、S1481×1、S125×1 | 新增 `_time_row` / `_image_row` / `_noise_row` / `_message_row` / `_pick_nick` / `_has_other_message` / `_flatten_gt` / `_fill_rows` / `_emit_scene`；`render_scene()` 拆绘制子函数；`main()` 改 `itertools.product` 单层循环；删未用 `app` 参数与 `import` 残留；RNG 消费顺序逐项保持 | pytest 全量通过；重扫 0 |
| 5 | `desktop-app/scripts/updater.smoke.js` | 6 | S7780×3、S5850×1、S7755×1、S7785×1 | 路径字面量改 `String.raw`；正则分组显式化；`seen.at(-1)`；入口 `setTimeout(runAll, 0)`（CJS 无顶层 await） | UPDATER SMOKE 45 PASS |
| 6 | `desktop-app/scripts/update-e2e.js` | 5 | S7735×2、S3776×1、S3358×1、S2486×1 | 拆 `requireProfileEnv` / `runWizardCase` / `runToggleCases` / `classifyByTag` / `predictExpect` / `clickCheckAndWait` / `assertCheckConclusion` / `runGuardCases`；否定条件改正向；嵌套三元拆 `if`；catch 加 `console.warn` | update-e2e 19/19（真实 GitHub 往返） |
| 7 | `desktop-app/main.js` | 4 | S6582×4 | `e?.message` 逻辑或兜底、`lastCheck?.state !== 'newer'` 等可选链简化；**L277 `win && !win.isDestroyed()` 保持原样**（改可选链会改变空窗口语义，规则未点名） | UI E2E 51/51 |
| 8 | `desktop-app/renderer.js` | 3 | S3358×2、S7785×1 | 关闭提示两层三元改 `tipByMode` / `ariaByMode` 查表；更新元数据水合改 `async function hydrateUpdateMeta()` + `setTimeout(..., 0)`（经典 script 无顶层 await） | UI E2E 51/51；update-e2e 19/19 |
| 9 | `m1-core/tests/test_t05729_p1.py` | 2 | S1244×2 | 两处改 `pytest.approx(1.0)` / `pytest.approx(0.6)` | pytest 全量 |
| 10 | `android-app/app/build.gradle.kts` | 1 | S7204（VULNERABILITY） | `release` 开启 `isMinifyEnabled = true`，接入 `proguard-android-optimize.txt` + `proguard-rules.pro`；未开 `isShrinkResources`（降风险） | `gradle assembleRelease` BUILD SUCCESSFUL（R8 mapping 已产出） |
| 11 | `android-app/.../UpdateManager.kt` | 1 | S3776（16） | 抽 `deleteQuietly(file, why)`，三处删除失败日志统一，复杂度 16→约 13 | 同上 release 构建 |
| 12 | `desktop-app/index.html` | 1 | Web:S6819 | `#closeAsk` 由 `<div role="dialog" aria-modal>` 改原生 `<dialog aria-modal="true" aria-labelledby>`，CSS 复位 UA 默认样式；**不用 `showModal()`**（保持 z-index 75 低于控制条 80，仍由 renderer 控 `display`） | win-ctrl K6/K6b/K6c；close-ask L1–L5 全绿 |
| 13 | `desktop-app/scripts/win-ctrl-e2e.js` | 1 | S3776（19） | 拆 `assertPinPersisted` / `runPinCases` / `assertClosePersisted` / `runCloseCases` / `runWakeCase`；删除冗余 `if (PROFILE)` 分支 | win-ctrl 11/11 |
| 14 | `m1-core/emoreply/baseline.py` | 1 | S3776（49） | `evaluate()` 拆 `_Totals` 累加器 + `_parse_predictions` / `_match_pairs` / `_score_pairs` / `_count_unmatched` / `_evaluate_record` / `_build_metrics`；指标分母、缺图记全漏、时间行不计噪声等语义逐项对齐 | pytest 全量（含 T05727 基线评估） |
| 15 | `m1-core/emoreply/correction.py` | 1 | S125 | 改写 docstring，避免被识别为注释掉的代码 | pytest |
| 16 | `m1-core/emoreply/preprocess.py` | 1 | S3776（17） | `vote_screen_batches` 拆 `_flatten_batches` + `_cluster_variants`，主函数只排序取众数（复杂度约 3） | pytest |
| 17 | `m1-core/tests/test_t05730_correction_poc.py` | 1 | S7500 | 生成器抛错改内部 `def _raise(...)`，再 `monkeypatch.setattr` | pytest |
| 18 | `scripts/api_push.py` | 1 | S3776（30） | `main()` 拆 `_split_commit` / `_diff_pairs` / `_entry_for` / `_push_one`；父链与 tree 校验、optimistic lock 语义保持 | `py_compile`；scan 0 |
| 19 | `scripts/publish-release.py` | 1 | S3776（16） | 抽 `_refresh_existing_release()`，409/422 创建分支保留 | `py_compile`；scan 0 |
| 20 | `android-app/app/proguard-rules.pro` | 新增 | — | keep MediaPipe / ML Kit 全类，`-dontwarn` 对应包 | R8 构建成功 |

## 3. 第二轮残留 7 项与二次修复

| # | 规则 | 位置 | 原因 | 修复 |
|---|---|---|---|---|
| 1 | `javascript:S7785` | `renderer.js:1078` | `setTimeout(hydrateUpdateMeta, 0)` 前的直接调用被视为顶层 async 调用 | 改为 `setTimeout(hydrateUpdateMeta, 0)`，下一拍执行 |
| 2 | `javascript:S7785` | `updater.smoke.js:200` | 同上 | `setTimeout(runAll, 0)` |
| 3 | `javascript:S7781` | `updater.js:54` | `replaceAll(/\\/g,'/')` 的 pattern 可改字符串 | `replaceAll('\\','/')` |
| 4 | `javascript:S7721` | `updater.js:150` | `writeBody` 未捕获闭包却定义在 `createUpdater` 内 | 移至模块顶级作用域（`fs`/`Buffer`/`streamOf` 均为模块级） |
| 5 | `python:S3776` | `gen_baseline_corpus.py:164` | `build_scene` 复杂度 18 | 循环抽 `_fill_rows()` |
| 6 | `python:S1172` | 同上 | `app` 参数未使用（只影响渲染不影响 GT） | 签名改 `build_scene(chat, font_scale, seed)`，调用方同步 |
| 7 | `javascript:S6582` | `updater.js:140` | `!info` 与 `info.state` 可合并为可选链 | `info?.state !== 'newer'` |

> 修复过程小插曲：移动 `writeBody` 时一次编辑误删 `const repo = o.repo || REPO;`，已立即补回并 `node --check` + 45 项 smoke 验证。

## 4. 关键语义保护点

1. **RNG 顺序**：`gen_baseline_corpus.py` 的 `_pick_nick`（0.8 判定 → `rng.choice` → 0.4 前缀判定）与 `_fill_rows` 的消息循环顺序保持原实现；同一 seed 生成的语料不变。
2. **基线评估指标**：`noise_intrusion` 分母 `total_pred - time_lines`、`parse_applicable` 只统计含 SIDE_OTHER 的图、缺预测图记全漏、`round(x/y,4)` 分母为 0 返回 `None`，均逐项对齐。
3. **更新器状态契约**：`prepareDownload` 成功返回 `{state:'ok', ...}`，失败返回 `{state:'error'}`，测试按 `state` 断言，未改变。
4. **经典脚本无顶层 await**：renderer / smoke 用 `setTimeout(...,0)` 延后一拍，不引入 module 化。
5. **关闭弹层层级**：坚持 `<dialog>` + `display` 控制，不用 `showModal()`，避免 top-layer 盖住右上角控制条（z-index 75 vs 80）。

## 5. 未验证项与风险

| 项 | 状态 | 说明 |
|---|---|---|
| Android R8 运行时 | **未真机验证** | release 构建成功且产出 mapping，但 APK unsigned、本机无设备；MediaPipe / ML Kit 反射/JNI 路径需真机回归 |
| 安装包签名 | **未签名** | 仓库无 Windows 代码签名证书；正式分发前需补签 |
| 12 个安全热点 | **未评审** | `/api/hotspots/search` 403，无法取明细；质量门因此为 ERROR |
| 覆盖率接入 | **未接入** | `coverage=0` 是未上传报告，不是测试未跑 |
| Swift 分析 | **未覆盖** | 本机无 Swift 分析器 |
| 改动未提交 | **工作区** | 19 M + 1 新增；扫描基于快照叠加工作区验证 |

## 6. Git 状态

```text
 M android-app/app/build.gradle.kts
 M android-app/app/src/main/java/com/emoreply/poc/update/UpdateManager.kt
 M desktop-app/index.html
 M desktop-app/main.js
 M desktop-app/renderer.js
 M desktop-app/scripts/close-ask-e2e.js
 M desktop-app/scripts/update-e2e.js
 M desktop-app/scripts/updater.smoke.js
 M desktop-app/scripts/win-ctrl-e2e.js
 M desktop-app/updater.js
 M m1-core/emoreply/baseline.py
 M m1-core/emoreply/correction.py
 M m1-core/emoreply/preprocess.py
 M m1-core/scripts/gen_baseline_corpus.py
 M m1-core/tests/test_t05727_baseline_eval.py
 M m1-core/tests/test_t05729_p1.py
 M m1-core/tests/test_t05730_correction_poc.py
 M scripts/api_push.py
 M scripts/publish-release.py
?? android-app/app/proguard-rules.pro
?? docs/sonar-reports/
```

- 未执行 `git commit` / `push`；所有改动留在工作区，便于审阅后提交。
- E2E 运行重新生成的 5 张截图已还原，diff 不含无关变更。

## 7. 结论

- 81 个 OPEN（含 1 个安全漏洞、17 个 BUG、12 个 CRITICAL）全部按根因修复，两轮重扫后归零。
- 修复未引入新违规：质量门 `new_violations = 0`，全量 OPEN = 0。
- 全部测试通过；桌面生产构建与打包态 OCR 验证通过。
- 剩余风险集中在「未签名」「未真机验证 Android R8」「热点/覆盖率未接入」三类交付流程项，已在报告中列明。
