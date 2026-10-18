# EmoReply 同类产品调研与优化需求梳理

> 调研日期：2026-09-27 · 范围：GitHub 开源同类 / 国内封闭竞品 / 商业化产品 / 输入法主题标杆
> 结论先行：**EmoReply 的核心壁垒是「输入法内零切换闭环」（读屏/粘贴 → 候选 → 原地注入）+ 零留存隐私**。竞品验证了大量可借鉴能力，所有扩展建议都围绕「让这个闭环更快、更准、更好看」，而不是变成另一个聊天 App。

---

## 一、竞品全景

### A. GitHub 开源同类

| 产品 | 形态 | 核心亮点（对 EmoReply 的启示） |
|------|------|------|
| **Auto-Reply-Android**（it5prasoon） | 通知监听自动回复 + LLM | 与 EmoReply 同源路线（NotificationListener）。已验证：**Groq（免费）/OpenAI 双 provider**、**防诈骗保护**（转账/威胁/OTP 拒答，语言无关）、**统计面板**（今日/累计/分应用）、**可配置回复延迟**、30 分钟对话上下文会话、VIP 联系人规则、10 个人设 prompt 模板 |
| **Sanna**（sannabotdev） | 语音优先全权助手 | **SOUL 人设文件**（用户可编辑助手人格）+ **结构化长期记忆注入 prompt** + SKILL.md 技能扩展 + 后台子代理（通知到达→评估→行动）。EmoReply 的人设库可向「SOUL 文件+记忆」演进 |
| **ai-keyboard-app**（mgldezveer） | 全键盘 + ChatGPT | **Quick Actions**（改写/总结/翻译/邮件一键动作）+ **AI 聊天面板内嵌键盘** + 自定义主题/暗色模式。EmoReply 的 🖌 美化是 Quick Actions 的特例 |

### B. 国内封闭竞品（直系——「高情商回复」赛道）

| 产品 | 核心亮点 |
|------|------|
| **AI高情商聊天输入法**（应用宝） | 粘贴→多条风格方案 / **表达优化**（EmoReply 已有=🖌）/ **场景化开场建议**（破冰/重连/日常）/ **截图识别理解**（EmoReply 已有=读屏）/ **语气强度自由调节**（连续滑杆而非档位） |
| **回复神器** | **40 万+话术库**（分类检索+每日更新）/ **关系×语气×目的三维筛选**（温柔安抚/幽默调侃/正式客气）/ **模拟对话练习**（自定义对方年龄情绪身份）/ 素材库（朋友圈文案/表情包） |
| **AI帮聊** | **潜台词解读**（「呵呵」是敷衍还是调侃）/ 性格定制（学习用户说话习惯）/ 场景化祝福 / **话术陷阱识别** |
| **回哒** | 截屏-分析-回复三步极简 / 话术模板库 / **回复逻辑教学**（教用户「先感谢+关联对方」的高情商公式——工具+教育双价值） |

### C. 商业化产品（定价与隐私形态参考）

| 产品 | 模式 | 关键情报 |
|------|------|------|
| **AI Text Reply**（greenrobot） | **Gemma 4 端侧视觉模型，100% 离线** | 模型 ~2.4GB 下载一次永久离线；$4.99/年；无账号无广告；后台处理完成通知提醒。**「截图分析+端侧模型」是 2026 的隐私最优解** |
| **Screenshot-to-Reply 类**（Flirtist/Wingman 等） | 订阅制（$4.99/周 或 $14.99/月） | **AI 教练人设可选**（ Bold Charmer vs Smooth Talker）/ **Vibe Check**（分析对方是否真感兴趣）/ **跨应用记忆**（避免对不同人重复同一句话）/ **Anti-Ghosting 提醒**（48h 未回→低压力重启话术）/ 3 候选+Regenerate |

### D. 输入法主题/UI 标杆（开源实现成熟可抄）

- **FlorisBoard**：Material You 动态取色（壁纸联动）+ Smartbar（undo/redo/剪贴板/emoji/溢出菜单第二行）+ Addon 主题市场（Catppuccin 包）
- **fcitx5-android**：9 内置主题 + Monet 动态生成（22 个颜色属性映射系统色彩 token）+ 主题预览
- **CleverKeys**：18+ 主题 + **DIY 主题编辑器** + 100+ 布局 + 无限剪贴板历史（IME 天然有剪贴板权限）
- **Urik**：Material You + WCAG AA 无障碍对比度 + 触觉强度滑杆

---

## 二、功能矩阵：竞品已验证 vs EmoReply 现状

| 能力 | 竞品证据 | EmoReply | 差距 |
|------|---------|----------|------|
| 消息捕获 | 通知监听（AutoReply）/截图（全体） | ✅ 通知+读屏+多屏 | 持平领先 |
| 候选生成+理由 | 3 候选+Regenerate（普遍） | ✅ 3-5 条+理由 | **缺「换一批」** |
| 人设/风格 | 人设模板×10 / 教练人设 / SOUL | ✅ RAG 人设库+4 风格 | 缺语气强度连续调节 |
| 表达优化 | 普遍 | ✅ 🖌 美化 | 可扩展为快捷动作族 |
| 侧别/上下文理解 | Screenshot-to-reply 主打 | ✅ 读屏侧别+多屏 | 持平领先 |
| 端侧离线模型 | Gemma 4（greenrobot）/ Urik | ❌ 仅云端 | **重要差距** |
| 防诈骗防护 | AutoReply | ❌ | 差距 |
| 统计面板 | AutoReply | ❌ | 差距 |
| 主题系统 | FlorisBoard/fcitx5 | ❌ 固定配色 | **差距最大** |
| 暗色模式 | ai-keyboard 等 | ❌ | 差距 |
| 一键复制 | greenrobot | ❌ 仅 IME 注入 | 差距（非 IME 场景不可用） |
| 自动发送 | AutoReply | ❌（设计取向：人工采纳） | 战略差异，维持 |

---

## 三、优化需求清单（按用户四维度组织）

### A. 客户体验提升

| # | 需求 | 说明 | 来源 | 优先级 |
|---|------|------|------|--------|
| A1 | **候选「换一批」（Regenerate）** | 不满意时一键重生成，保留会话上下文不变 | 全行业标配 | **P0** |
| A2 | **候选一键复制** | 候选右侧加复制按钮——非 IME 宿主场景（如浏览器）也能用 | greenrobot | **P0** |
| A3 | **语气强度滑杆** | 从「4 档风格」升级为连续调节（理性克制 ↔ 轻松自然） | AI高情商输入法 | **P0** |
| A4 | 读屏过程反馈升级 | 「已读第 2/3 屏…」进度提示；多屏读屏 3s 等待期体验 | 自查 | P1 |
| A5 | 首次引导向导 | 3 步 onboarding：启用键盘→授权通知/无障碍→引导完成第一次生成 | FlorisBoard | P1 |
| A6 | 统计面板 | 今日/累计采纳数、省时估算、分应用分布 | AutoReply | P1 |
| A7 | 触觉反馈 | 候选采纳/注入成功 Haptic（强度可调） | Urik | P2 |

### B. 功能扩展

| # | 需求 | 说明 | 来源 | 优先级 |
|---|------|------|------|--------|
| B1 | **端侧离线模型试点** | Gemma 3n/4（MediaPipe LLM Inference API）做离线兜底+隐私模式；云端不可用/飞行场景全功能可用 | greenrobot/Urik | **P1 重磅** |
| B2 | **关系×场景×目的三维筛选** | 生成前可设「对方关系（领导/客户/恋人）+沟通目的（安抚/拒绝/推进）」，注入 prompt | 回复神器 | **P1** |
| B3 | **防诈骗与风险提示** | 转账/借钱/威胁/OTP 类消息 → 候选置顶风险提醒+拒答式候选（「What money? Wrong person」式） | AutoReply | P1 |
| B4 | 场景化开场建议 | 破冰/重连/节日祝福——✨ 长按菜单新增「开场」模式（无对话上下文时引导输入场景） | AI高情商/回哒 | P1 |
| B5 | 联系人记忆深化 | 人设库已有联系人加权 → 扩展「上次聊到 X」跨会话记忆（本地加密存储，符合零留存边界需评审） | Sanna/Wingman | P2 |
| B6 | 再互动建议 | 会话超 48h 未回 → 汇总格提示「低压力重启话术」 | Wingman | P2 |
| B7 | 快捷动作面板 | 🖌 美化扩展为改写/总结/翻译/扩展一族（长按 🖌 弹菜单） | ai-keyboard-app | P2 |
| B8 | 话术收藏夹 | 用户把好用的候选收藏，下次同类场景优先展示 | 回复神器 | P2 |

### C. UI 升级

| # | 需求 | 说明 | 来源 | 优先级 |
|---|------|------|------|--------|
| C1 | **暗色模式** | 跟随系统深浅色；AMOLED 纯黑主题 | 全行业 | **P0** |
| C2 | **图标行 Smartbar 化** | iconRow → 主图标+「···」溢出菜单第二行（低频功能收纳，腾出空间） | FlorisBoard | P1 |
| C3 | 候选卡片标签化 | 理由文字 → 分类标签（情感共鸣/行动建议/边界保持），扫读效率提升 | Flirtist Vibe | P1 |
| C4 | 双栏/精简模式联动打磨 | 布局切换时的过渡动画与记忆 | 自查 | P2 |

### D. 主题升级

| # | 需求 | 说明 | 来源 | 优先级 |
|---|------|------|------|--------|
| D1 | **Material You 动态取色** | Android 12+ 从壁纸取色生成键盘配色（fcitx5/FlorisBoard 成熟方案可直接参考实现） | fcitx5-android | **P1** |
| D2 | 主题预设包 | 浅色/深色/AMOLED 黑/Nord/Monokai 等 6-9 个预设 | fcitx5-android | P1 |
| D3 | 自定义强调色 | 候选选中态/激活态颜色可调（先做单色自定义，主题编辑器后置） | CleverKeys | P2 |
| D4 | DIY 主题编辑器 | 完整颜色自定义+预览+导入导出 | CleverKeys | P3 远期 |

---

## 四、优先级路线建议

- **P0（体验短板，1-2 周量级）**：A1 换一批、A2 一键复制、A3 语气强度滑杆、C1 暗色模式
- **P1（差异化扩展，3-4 周量级）**：B1 端侧模型试点、B2 三维筛选、B3 防诈骗、B4 场景开场、C2 Smartbar、C3 标签化、D1 Material You、D2 主题预设
- **P2（长期能力）**：A6 统计、B5 记忆、B6 再互动、B7 快捷动作、B8 收藏、D3 自定义色

**战略提醒**：
1. **「自动发送」不做**——AutoReply 的全自动路线与 EmoReply「人工采纳」定位冲突，后者是隐私与安全的护城河（SRS FR-6）；
2. **全键盘输入维持不做**（已有拍板）——但 B7 快捷动作、C2 Smartbar 属于「AI 工具条」范畴，与拍板不冲突；
3. **端侧模型（B1）是与全体竞品拉开差距的最大机会**：greenrobot 证明了「完全离线+订阅」的商业模式可行，EmoReply 的 ML Kit OCR 已验证端侧 AI 工程链路，Gemma 3n 端侧推理是自然延伸。

---

## 五、竞品链接

- Auto-Reply-Android: https://github.com/it5prasoon/Auto-Reply-Android
- Sanna: https://github.com/sannabotdev/sannabotapp
- ai-keyboard-app: https://github.com/mgldezveer/ai-keyboard-app
- AI Keyboard: https://github.com/roonakyadav/ai-keyboard
- FlorisBoard: https://github.com/florisboard/florisboard
- fcitx5-android 主题系统: https://deepwiki.com/mzbgf/fcitx5-android/7.3
- CleverKeys: https://f-droid.org/eu/packages/tribixbite.cleverkeys/
- Urik: https://github.com/ElizabethWega/Urik
- AI Text Reply (greenrobot): https://aitextreply.greenrobot.com/
- AI高情商聊天输入法: https://sj.qq.com/appdetail/com.qnaibhjp.yovqwp
