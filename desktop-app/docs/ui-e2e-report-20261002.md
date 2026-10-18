# PC 端功能与体验 UI 全面测试报告（2026-10-02）

## 一、测试方式与环境

- **工具链**：CDP（Chrome DevTools Protocol）端到端测试——`scripts/ui-e2e.js`（dev 启动 Electron + `--remote-debugging-port=9222`，Node22 内置 WebSocket 直连，Runtime.evaluate DOM 层断言 + Page.captureScreenshot 状态截图 + PIL 像素校验）。脚本已沉淀为可复用回归资产。
- **环境**：dev 模式（未配置云端 Key → 全链路走降级模板路径，离线确定性）；29 项断言（A 向导 4 / B 生成链路 6 / C 指令栏 3 / D 键盘导航 3 / E 设置 6 / F 历史知识库隐私 3 / G 其他 4）。

## 二、结果总览：**PASS 29 / FAIL 0**（修复 1 个 P0 缺陷后）

像素校验：向导页 vs 主界面差异 79.6%、强调色切换差异 50.2%、缩放切换差异 52.7%（均显著生效）；主界面背景色 (244,246,250)=#f4f6fa 与主题令牌一致。

## 三、发现并修复的真实缺陷（P0）

**指令栏从未渲染（T01399 功能性缺陷，本次测试的核心发现）**

- 现象：主界面指令栏恒为空（内置 5 条场景包不出现）、设置页指令 textarea 恒空。
- 根因：`fillCommandsTextarea()` 在 renderer.js:378 顶层初始化时被调用，而其依赖的 `CMDS_KEY`/`CMDS_SEEDED` 常量在 line 466 才 `const` 声明——**TDZ（暂时性死区）ReferenceError 导致顶层初始化在 378 处中断**。
- 连锁影响：指令播种永不执行；且 C2 之前点「生成回复」实际依赖的中断后代码路径未初始化（首轮 E2E 生成链路全败的直接原因之一）。
- 修复：三个常量提升至文件顶部声明区（renderer.js line 5-18，含注释说明）。修复后：播种 5 条 ✓、reload 零异常 ✓、指令栏/自定义指令/生成全链路 ✓。
- 潜伏期推断：该 TDZ 自 T01399 引入指令栏起即存在（此前冒烟只覆盖纯函数与主进程，UI 层无自动化覆盖）——本次 E2E 管线填补了这一盲区。

## 四、连带发现（低危，已随修复缓解或记录）

1. **设置页指令 textarea 保存=整表覆盖**：若 textarea 未被正确回填（TDZ 中断期的状态），保存会静默清空指令列表（测试中实际触发，已从源码 BUILTIN_COMMANDS 恢复用户数据）。TDZ 修复后 textarea 回填正常，意外触发面已消除；建议后续把「保存空列表」加确认提示（不阻塞）。
2. **测试脚本键名错误暴露的口径问题**：向导持久化键为 `emoreply_wizard_done`（非 wiz_done）——旧截图脚本 ui-shots.js 清的是错误键，其「向导截图」实为主界面（已由本次 E2E 修正，向导显示/跳过/持久化全部验证通过）。

## 五、29 项断言矩阵（全部 PASS）

| 分组 | 覆盖点 |
|---|---|
| A 启动/向导 | 未完成弹向导、第二步服务配置、跳过后隐藏、持久化不再弹 |
| B 生成链路 | 降级产出候选、notice 降级提示、候选卡结构（文本/role/tabIndex/aria-label/序号/复制·回填·换一条三动作）、纯表情本地判定拦截（压误判）、二次点击旁路强制生成、「换一条」exclude 重生成 |
| C 指令栏 | 内置 5 条场景包渲染、未配置云端明确提示、自定义指令保存后入栏（测试后已清理不残留） |
| D 键盘导航 | ArrowDown/ArrowUp 聚焦移动、数字键 2 直选候选触发动作 |
| E 设置 | Key 掩码（type=password）、强调色 6 档色板+css 变量切换+持久化、界面缩放 1.15 生效+持久化 |
| F 历史知识库隐私 | 知识库增删+计数、**隐私口径：降级模板不写入历史**（即使显式开启） |
| G 其他 | 截图取文/撤回/美化按钮可用、语气四档 |

## 六、截图佐证（desktop-app/docs/screenshots/）

e2e-01-wizard（向导）、02-wizard-step2、03-candidates（候选卡+来源徽标+语气标签）、04-judge-bypass（判定拦截提示）、05-cmdbar（指令栏）、06-accent-purple（紫色强调色）、07-scale-large（1.15 缩放）、08-settings（设置全展开）、09-history-kb（历史+知识库）。

## 七、遗留事项

1. **生产构建待重打**：renderer.js TDZ 修复后，dist 内 NSIS/portable 仍是旧代码；**因磁盘空间不足（D: 仅剩 ~700MB）暂缓重打**，磁盘清理后执行 `npm run dist` + 打包冒烟即可（约 5 分钟）。
2. ⚠ **磁盘告警**：C: 剩 523MB（100%）、D: 剩 0（清理项目产物后 ~700MB）。D: 大头为 IDE/工具链数据（Data_Trae 25GB、JetBrains 16.7GB、otherProjects 15.7GB、Data_UV 8GB、Data_WorkBuddy 7.5GB 等）——建议在各自 IDE 内清理缓存，或由用户决策清理项；本环境已清理本项目可再生成的构建产物（win-unpacked 188MB + 压测原始样本）。
