# T01339 真机复验手册（设备上线后照单执行）

> 汇总范围：截至 2026-09-30，所有 done 但 verified=0 的真机待验项（原 4 项 + T01334/35/36/43/44 + T01348/49/50 新增）。
> 配套脚本：`scripts/device_recheck.py`（Phase 1 环境准备自动化 + Phase 2 逐项截图存证）。
> APK 基线：`release/build/app/outputs/apk/debug/app-debug.apk`，versionCode **246301**。

## Phase 0 前置检查

```bash
ADB=/d/android/Sdk/platform-tools/adb.exe
$ADB devices                 # 恰好 1 台设备在线
$ADB shell dumpsys input_method | grep mCurMethodId   # 记录当前输入法（排障基线）
$ADB shell dumpsys window | grep mCurrentFocus        # 确认设备空闲、非被真人使用
```

⚠️ 铁律：注入前见非测试触发的界面/消息突变 → 立即停手（设备可能正被真人使用）。

## Phase 1 环境准备（脚本自动化）

```bash
$ADB install -r release/build/app/outputs/apk/debug/app-debug.apk
# 授权：install -r 后 grant 必须重做
$ADB shell pm grant com.emoreply.poc android.permission.WRITE_SECURE_SETTINGS
# 无障碍服务写回（OneUI 进程死亡会摘除）
$ADB shell settings put secure enabled_accessibility_services \
  com.emoreply.poc/com.emoreply.poc.capture.EmoReplyReaderService
$ADB shell ime enable com.emoreply.poc/.ime.EmoReplyImeService
$ADB shell dumpsys package com.emoreply.poc | grep versionCode   # 期望 246301
```

排障预案（历史踩坑）：
- 唤不起键盘 → 先切系统键盘 tap 输入框得焦点 → 再 `ime set` 切回；
- 「隐形 IME 窗口僵死」→ `input keyevent 4` 收起后再 tap；
- 测试期间按钮全无反应 → 先查 `default_input_method` 是否被切到搜狗。

## Phase 2 逐项复验清单

### A. 原始 4 项（本单来源）

| # | 来源 | 验什么 | 预期 |
|---|------|--------|------|
| A1 | T01164 | 启动器/设置/最近任务三处图标外观 | 圆形遮罩下气泡尾完整不裁、爱心/星光可见 |
| A2 | T01292 | 设置页保存 API Key 后 | 输入框显示 `••••` 掩码；点眼睛图标可明文核对；再次保存掩码恢复 |
| A3 | T01145（T01291 简化 1 屏） | 群聊点 👁 | 汇总读屏内容（只读当前屏 1 次无翻动）；聊天滚动位置回到底部 |
| A4 | T01147 | 开启精简模式后弹键盘 | 图标行下直接是候选区（无输入框/汇总格）；开关持久化 |

### B. 读屏链路（2026-09-29 批次）

| # | 来源 | 验什么 | 预期 |
|---|------|--------|------|
| B1 | T01343-① | 右侧我方发言（气泡起点 ≥ 屏宽 31.25%） | 不再误判为对方；侧别标注正确 |
| B2 | T01343-② | 设置页读屏范围滑杆 1-3 | 调 2/3 时对应上滑次数，进度提示出现 |
| B3 | T01343-③④ | 含「时间」分隔的聊天记录 | 时间行转「时间：」标记；旧话题不混入 current |
| B4 | T01334 | 微信图片消息（图内有文字） | 节点树路径：图内文字不入上下文；OCR 路径：整簇剔除 |
| B5 | T01312 | 多屏读屏过程 | IME 显示「读屏 i/N」进度；单屏回落通用文案 |
| B6 | T01135 | 读屏触发瞬间 | IME 先收起 → 读屏 → 恢复；裁剪带 <300px 兜底 |
| B7 | T01152 | kill EmoReply 进程后点 👁 | 服务自动写回 enabled 列表完成读屏，无需手动开设置 |
| B8 | T01138 | 多条候选时输入区 | 展示态双栏均分、编辑态整宽 |

### C. 生成链路（T01348/49/50 新增，需两个 persona：领导 + 朋友）

| # | 来源 | 验什么 | 预期 |
|---|------|--------|------|
| C1 | T01348 | 同消息对「领导」与「朋友」生成（AUTO 开） | 领导语气明显克制（35 档）、朋友活泼（75 档） |
| C2 | T01348 | AUTO 开关 | 开=滑杆禁用显示「自动」；手动拖滑杆=退出 AUTO 且手动值**不被覆盖回 50** |
| C3 | T01348 | 老版本升级安装后打开联系人页 | persona.db v1→v2 迁移无损（旧关系标注还在） |
| C4 | T01349 | 多风格开关开 | 候选 rationale 前缀出现「克制档/中性档/活泼档」（需模型按序输出 3 条） |
| C5 | T01349 | **延迟实测**：开关各跑 N≥10 次 | 记录 P50/P95 对比数据回填 T01349（单请求方案 RTT 应不变，仅输出 token 增多） |
| C6 | T01350 | 同关系+同风格点忽略 ≥10 次（采纳率 <30%） | 后续生成语气自动降一档；第 9 次时不生效；开关关闭不降档 |
| C7 | T01350 | 通知链路反馈 | 仅记全局统计（无维度），不报错 |

### D. 其他

| # | 来源 | 验什么 | 预期 |
|---|------|--------|------|
| D1 | T01336 | 触发任一系统通知 | 小图标为纯白单色剪影（ic_stat_emoreply），无灰边 |
| D2 | T01344 | 设置页端侧模型预设 | 列表展示、路径自动填 /data/local/tmp/<file>、点跳转打开官方下载页 |
| D3 | T01335 | versionCode 链路 | dumpsys 确认 246301；后续发远程 versionCode≥246302 时 UpdateManager 能检出（需服务端配合） |
| D4 | T01147 | 三星短信/钉钉读屏对照 | 钉钉✅、三星短信✅（微信主动保护为已知限制） |

## 收尾

- 全部通过：本单标 done+verified；各来源单 verified 补 1。
- 任一异常：回原单补修 → 重新构建 → 本单项重跑。
- 截图证据统一存 `tmp/recheck/`（脚本自动命名）。
