# PC 端（EmoReply 桌面版）功能 × 体验 UI 全面测试报告

- 日期：2026-10-03
- 对象：`desktop-app/`（Electron 悬浮窗，主进程 main.js / 渲染 renderer.js / preload 上下文隔离）
- 方法：CDP（`--remote-debugging-port=9222`）驱动真实渲染进程，DOM 层断言 + 关键状态截图 + PIL 像素校验
- 脚本：`desktop-app/scripts/ui-e2e.js`（配置外置、可重复执行）
- 结果：**51 PASS / 0 FAIL**；冒烟 6 套 54 例同步回归全绿；截图 10 张全部通过像素校验

---

## 一、执行方式

```bash
# 1) 用独立 profile 启动（避免测试写脏真实用户配置：强调色/缩放/指令/历史开关）
cd desktop-app
env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe . \
  --remote-debugging-port=9222 --user-data-dir=tmp/e2e-profile

# 2) 跑 E2E（约 55s）
node scripts/ui-e2e.js
```

产出：逐条 PASS/FAIL 矩阵 + `docs/screenshots/e2e-*.png`（10 张）。

---

## 二、覆盖矩阵（A–J，51 项）

| 组 | 主题 | 项数 | 关键断言 |
|---|---|---|---|
| A | 首次运行向导 | 6 | 向导显示 / 选「自定义…」后模型名与接口地址输入框显形 / 空 Key 点测试给告警提示（`.status.bad`）/ 「稍后再说」关闭 / 刷新后不再弹 |
| B | 生成链路（未配 Key → 降级） | 6 | 产出候选 / notice 提示本地模板降级 / 候选卡结构（文本·role·tabIndex·aria·序号·三动作）/ 纯表情被判定拦截（压误判）/ 二次点击旁路强制生成 / 「换一条」重生成后恢复可交互 |
| C | 快捷指令栏 | 5 | 内置 5 条场景包渲染 / 未配云端给明确提示 / 自定义指令保存生效 / **追加不覆盖内置包** / 清理后内置包恢复 |
| D | 键盘导航（无障碍） | 3 | ArrowDown/ArrowUp 焦点移动正确 / 数字键 2 直选第二条并触发动作 |
| E | 设置与外观 | 11 | Key 掩码输入 / 强调色 6 档 + 切换 + 持久化 / 缩放 3 档 + 持久化 / **主题三档（浅·深·跟随系统）+ 深色生效 + 刷新保持 + 可切回** |
| F | 历史·知识库·隐私 | 3 | 知识库新增可见 / 计数标签更新 / **降级模板不写入历史（零留存口径）** |
| G | 其他入口 | 4 | 截图取文、撤回、美化按钮可用 / 语气四档可选 |
| H | 剪贴板交付链路 | 5 | 「📋 复制」写入候选正文 / 复制成功反馈 / 「撤回」恢复到覆盖前原文 / 撤回提示 / 美化未配云端有明确提示 |
| I | 多模型套件 / 端侧 | 4 | 两套模型保存落盘 / Ollama 一键预设行 / Ollama 连通检查有明确结论（本机未装 → 报错文案） |
| J | 输入边界 | 4 | 空消息给提示 / **空消息不产出候选且清空旧候选** / 3000 字超长消息完成生成 / 结束后按钮恢复 |

---

## 三、发现并已修复的问题（3 项）

### 1. 空消息仍会生成「无来源候选」（体验缺陷，已修）

`runGenerate` 缺少空态守卫（同类入口 `runCommand` / `regenerateWithTone` 都有）。剪贴板为空时点生成，会走降级模板凭空产出两条候选，用户看到与输入无关的回复。

修复（`renderer.js`）：

```js
if (!currentMessage) {
  $('notice').textContent = '请先「读剪贴板」获取对方消息，再生成回复';
  $('notice').classList.remove('ok');
  $('candidates').innerHTML = ''; // 与正常生成口径一致：新一轮清空旧候选
  return;
}
```

### 2. FR-9.1 暗色模式「账实不符」（已补实现）

T01413 只落地了主题令牌（`:root` 22 个语义变量），`index.html` 里仅一行注释「深色覆盖 data-theme 即可」——**无 `[data-theme]` 覆盖、无切换开关**，而 M-Opt1 里程碑把 FR-9.1 记为已验收。

补充实现：
- `index.html`：新增 `[data-theme='dark']` 令牌覆盖（同一套语义变量换深色取值，accent 族用 `color-mix` 与深色底混合，保持强调色联动）；设置区新增「主题」下拉（浅色 / 深色 / 跟随系统）。
- `renderer.js`：`applyTheme()` + `emoreply_theme` 持久化 + `prefers-color-scheme` 变更监听（仅 auto 档生效）。

验证：E8 断言 `data-theme=dark` 且 body 底色亮度 <60；截图 `e2e-10-theme-dark.png` 主色 `#151b27`（亮度 29）确认为深色，浅色截图主色 `#ffffff`。

### 3. 指令文本框「覆盖写」会永久顶掉内置场景包（测试口径修正 + 风险记录）

`#cfgCommands` 是全量列表语义，播种标志 `emoreply_commands_seeded` 使内置包只播种一次。测试中整体覆盖写 → 5 条内置包被永久替换且不再回填。

- 测试侧修正：改为「追加 + 事后还原」，并新增 C4（内置 5 条仍在）/ C5（清理后恢复）两条断言。
- 产品侧（建议，未改）：设置区可加「恢复内置指令包」按钮，降低误清空成本。

---

## 四、测试基建层面的两个坑（已修 / 已记录）

1. **窗口隐藏时 CDP 截图永久挂起**：`document.hidden === true` 时 `Page.captureScreenshot` 永不返回（实测 4 种参数组合全部 8s 超时；`Browser.setWindowBounds` 在 Electron 不可用）。已在 `shot()` 加可见性守卫 + 10s 超时 + 前置检查提示（重启实例即可恢复）。
2. **断言恒真导致假通过**：原 A2 用 `!!#wzBaseUrl`（元素始终存在）判定「进入第二步」，而向导实为单面板三步内联、根本没有 `#wzNext`；两张截图因此完全相同。已改为断言 `offsetParent`（真正可见）+ 真实交互（选「自定义…」触发）。

---

## 五、截图证据（`docs/screenshots/`）

| 文件 | 状态 | 校验要点 |
|---|---|---|
| e2e-01-wizard.png | 向导首屏 | 570×840，2864 色 |
| e2e-02-wizard-step2.png | 自定义模式（输入框显形） | 与 01 md5 不同（修正后切实切换） |
| e2e-03-candidates.png | 降级候选卡 | 主色 `#ffffff`，含强调色底 `#ebf1fd` |
| e2e-04-judge-bypass.png | 纯表情拦截提示 | — |
| e2e-05-cmdbar.png | 指令栏（含自定义） | 强调色 `#2563eb` 在位 |
| e2e-06-accent-purple.png | 紫强调色 | `#f3edfd`（accent 派生） |
| e2e-07-scale-large.png | 115% 缩放 | 4999 色（内容更满） |
| e2e-08-settings.png | 设置全展开 | — |
| e2e-09-history-kb.png | 历史/知识库 | — |
| e2e-10-theme-dark.png | 深色模式 | 主色 `#151b27`（亮度 29）确认深色 |

---

## 六、未覆盖 / 限制（需人工或真实条件）

- **云端真实生成路径**：需用户填 OpenAI 兼容 Key，本轮全部走本地降级模板（确定性、离线）。候选质量、流式打字效果、多模型并行对比的实感未验证。
- **粘贴回宿主（⏎ 回填）**：依赖 `SendKeys ^v` 合成与前台宿主窗口，自动化测试无法闭环，需真机手动验证（已在 MTask T01500 待办）。
- **截图取文（OCR）**：`ocr.smoke.js` 已覆盖离线识别（chi_sim best，置信度 94–95），但真实 `desktopCapturer` 截屏链路未在本轮点击验证。
- **视觉审美终审**：程序化只能校验尺寸/配色/亮度，排版观感仍需人工过目（已备截图，可直接看）。

---

## 七、回归结论

- `node --check` 全量通过（renderer.js / scripts/ui-e2e.js）。
- 冒烟 6 套 54 例全绿（generate 4 / sanitize 33 / llm 7 / tone 3 / kb 6 / ocr 1）。
- E2E 51 项全绿；修改仅涉及 `renderer.js`（空态守卫 + 主题应用）、`index.html`（深色令牌 + 主题下拉）、测试脚本 —— 未触碰主进程 IPC、生成管线与打包配置。
- **生产构建复验已完成**：`npm run dist` exit 0；asar 含全部新代码标记（data-theme / cfgTheme / emoreply_theme / applyTheme / 空态守卫文案）；打包 exe 单实例冒烟通过（`/health` 200、`/generate` 200 × 2 候选 degraded=true）。产物 `dist/EmoReply-0.1.0-20260930-004-x64.exe`（NSIS 113MB）+ portable。
- 注：打包 exe 冒烟必须在**单实例**下做——dev 实例同样监听 17777，双实例并存时会打到 dev 上造成假阳性。
