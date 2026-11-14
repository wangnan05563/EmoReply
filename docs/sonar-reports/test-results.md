# EmoReply · 测试执行结果

- 执行日期：2026-11-14
- 环境：Windows 10（本机）、Node `v22.23.2` / npm `10.9.8`、Electron `33.4.11`、Python `3.14`（`F:\Program Files\Python3.14\python.exe`）、pytest `9.1.1`
- 前置条件：**SonarQube 重扫 OPEN=0 之后**才执行全部测试（满足「测试前先确认代码质量问题已清零」约束）

## 1. 总结

| # | 测试套件 | 命令 | 结果 | 用例/断言 |
|---|---|---|---|---:|
| 1 | Python 全量单元/集成测试 | `python -m pytest -q`（工作目录 `m1-core`） | **通过** | **548 passed** |
| 2 | 桌面端冒烟（8 套） | `npm test`（`desktop-app`） | **通过（exit 0）** | UPDATER 45 项、TRAY-MENU 8 项，其余 6 套全绿 |
| 3 | UI 端到端（CDP） | `npm run test:e2e` | **通过** | **PASS 51 / FAIL 0** |
| 4 | 窗口控制 E2E | `node scripts/win-ctrl-e2e.js <profile>` | **通过** | **PASS 11 / FAIL 0** |
| 5 | 在线更新 E2E | `node scripts/update-e2e.js <profile>` | **通过** | **PASS 19 / FAIL 0** |
| 6 | 关闭交互 E2E | `node scripts/close-ask-e2e.js <profile>` | **通过** | **PASS 11 / FAIL 0** |
| 7 | 打包态 OCR 回归 | `npm run test:asar` | **通过** | `ASAR OCR CHECK PASSED` |
| 8 | Android release 构建校验 | `gradle assembleRelease` | **通过** | `BUILD SUCCESSFUL`（R8 已执行） |

> 合计：Python 548 用例、桌面冒烟 8 套、E2E 92 项断言（51+11+19+11）、打包态 OCR 1 项，全部通过，**无失败用例**。

## 2. Python 全量测试

```text
........................................................................ [ 13%]
........................................................................ [ 26%]
........................................................................ [ 39%]
........................................................................ [ 52%]
........................................................................ [ 65%]
........................................................................ [ 78%]
........................................................................ [ 91%]
............................................                             [100%]
548 passed in 20.89s
```

- 解释器：`F:\Program Files\Python3.14\python.exe`（PATH 中的 `python` 为沙箱运行时 3.13.18，无 pytest/Pillow，不能用于本项目测试）。
- 覆盖范围：`m1-core` 全部单元测试，含本次改动重点 `test_t05727_baseline_eval.py`、`test_t05729_p1.py`、`test_t05730_correction_poc.py`。
- 备注：README 旧口径为 489 项，实际收集到 548 项（仓库测试已扩充）。

## 3. 桌面端冒烟（`npm test`）

退出码 `0`，8 套冒烟全部通过。关键输出：

```text
===== UPDATER SMOKE: 45 PASS =====
===== TRAY-MENU SMOKE: 8 PASS =====
```

覆盖：generate / sanitize / llm / tone / kb / ocr / settings / profile-guard，以及更新器下载、安装、域名白名单、路径穿越清洗、托盘菜单项序与落点。

## 4. UI E2E（CDP）

启动方式（隔离 profile，窗口可见）：

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE
electron.exe . --remote-debugging-port=9222 `
  --user-data-dir=desktop-app\tmp\e2e-profile --force-show
npm run test:e2e
```

结果：

```text
===== E2E RESULT: PASS 51 / FAIL 0 =====
```

覆盖 A–J 全段：启动/向导、候选生成、键盘导航、设置（主题/缩放/强调色/持久化）、历史/知识库/隐私、截图取文/撤回/美化、剪贴板交付、多模型/端侧、输入边界（空消息、3000 字超长）。

## 5. 专项 E2E

### 5.1 窗口控制（`win-ctrl-e2e.js`）

```text
K1–K7 全部 PASS
===== T01566 E2E: PASS 11 / FAIL 0 =====
```

验证置顶/取消置顶、`winPin` 落盘、关闭询问弹层显隐（K6/K6b/K6c）、关闭策略落盘、托盘/二次启动唤回。其中 K6/K6b/K6c 是本次 `<dialog>` 改造的关键回归点。

### 5.2 在线更新（`update-e2e.js`）

```text
U0–U8c 全部 PASS
===== T03996 UPDATE E2E: PASS 19 / FAIL 0 =====
```

真实 GitHub 往返；本次结论 `U5b 真实结论=已是最新`；下载禁用、无残留安装包、未查出新版时拒绝下载/安装、两次被拒后页面仍可应答。

### 5.3 关闭交互（`close-ask-e2e.js`）

```text
L1–L7 + N1 全部 PASS
===== T03777 E2E: PASS 11 / FAIL 0 =====
```

覆盖 Esc 取消、仅本次隐藏、记住后不再询问、设置切「直接退出」、L7 真退出（target 消失）、N1 原生 `WM_CLOSE` 真退出（不退化成托盘僵尸态）。运行日志中的 `等待实例就绪：fetch failed` 是重启轮询期的正常输出，不是失败。

## 6. 打包态 OCR 回归（`npm run test:asar`）

在 `npm run dist` 产物 `dist/win-unpacked/resources/app.asar` 上执行：

```text
asar: ...\dist\win-unpacked\resources\app.asar
model: plain
elapsed: 1847ms  confidence: 95
lines: ["周 未 一 起 爬 山 好 吗", "明 天 上 午 开 会 记 得 带 材 料"]
ASAR OCR CHECK PASSED
```

证明 asar 打包后 `ocr.js`、`assets/ocr` 语言模型与 wasm 裁剪仍可用。

## 7. Android release 构建校验

- 命令：`gradle assembleRelease --no-daemon`（JDK 17、Gradle 8.9、SDK 35 / build-tools 34）
- 结果：`BUILD SUCCESSFUL in 6m 51s`，`minifyReleaseWithR8` 已执行，产出 `release/build/app/outputs/apk/release/app-release-unsigned.apk`（42,828,610 字节）与 `mapping.txt`（24,311,010 字节）。
- 说明：仓库无 release 签名配置，APK 为 **unsigned**；本次只验证 R8 + proguard 规则能完成构建，未做真机运行验证。

## 8. 执行过程中的环境问题与处置（非代码缺陷）

| 问题 | 现象 | 处置 |
|---|---|---|
| Python 缺 Pillow | 基线构建语料相关测试 `ModuleNotFoundError: PIL` | `pip install Pillow`（装到 13.0.0）后 548 全过 |
| asar OCR 测试图缺失 | `测试图不存在: desktop-app\tmp\ocr-asar-test.png` | 按仓库 `cleanup-config.yaml` 标注「PIL 现场生成，可再生」，用 Pillow 生成含「爬山」「开会」的中文图 |
| 首次 `npm run dist` 失败 | `remove ...app.asar: being used by another process` | 按仓库 T05736 方案改用唯一输出目录 `EMO_REPLY_EB_OUTPUT`，绕开被 Defender/索引服务持锁的旧目录；交付物复制到 `release\desktop` |
| Android `JAVA_HOME` 层级错误 | `JAVA_HOME is set to an invalid directory: E:\Android\jdk17` | 改为 `E:\Android\jdk17\jdk-17.0.20.1+1` 后构建成功 |

## 9. 结论

- 所有测试均在 OPEN=0 之后执行，**无失败、无跳过关键断言**。
- 本次重构涉及的更新器、窗口控制、关闭弹层、语料生成、基线评估均有对应测试覆盖并通过。
- 构建产物的打包态 OCR 回归通过，安装包可启动（见《构建结果》）。
