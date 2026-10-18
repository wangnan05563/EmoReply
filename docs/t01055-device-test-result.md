# [PRD] 4 M4二期扩展 —— Android 真机全面测试（SM-G9980 / Galaxy S21 Ultra）

## 测试环境

- 设备：Samsung SM-G9980（Android 真机，USB 调试），serial R5CR716VVEM
- 包：com.emoreply.poc 0.1.0-poc（debug，16:22 新鲜构建含全部 M4 改动，产物 release/build/app/outputs/apk/debug/app-debug.apk 3.29MB）
- 安装：`adb install -r` Success；`am start -W` 启动正常（WARM 120ms）

## 实测通过项（截图/状态为证）

1. **启动与稳定性**：全程零崩溃（logcat crash buffer 无记录、无 FATAL EXCEPTION）。
2. **授权状态**：通知监听「已授权」、EMOREPLY 输入法「已启用」（tvStatus 确认；`cmd notification allow_listener` 生效）。
3. **人设保存（FR-5.1）**：自称 Ben + 禁语 sorry 保存成功，UI 回显正确。
4. **RAG 知识库（决策③）**：
   - 分类 Spinner 三项切换正常（个人事实/关系话术/平台礼仪）；
   - 关系话术（绑联系人）+ 平台礼仪两条录入保存成功，计数「知识条目：2 条」；
   - 加密落盘：`run-as` 取 files/kb.enc（85B），内容为 `iv.ciphertext` hex 密文（与 m1-poc crypto.py 格式一致），无明文泄漏。
5. **一键清除（FR-6.3 联动）**：点击后知识条目归 0、人设恢复默认「我」、禁语清空、反馈统计清空——全部联动正确（截图 after-clear.png）。
6. **云端设置 + 持久化**：填入 agnes-ai 配置并勾选启用 → tvLlmStatus「云端生成：已启用（https://apihub.agnes-ai.com/v1 / agnes-2.5-flash）」；force-stop 重启后配置与启用状态保持。
7. **云端连通性（PC 端同 Key 复测）**：服务可达，但 **API Key 认证失败（Invalid token）**——该 Key 当前不可用，云端生成将走 401 降级本地模板路径（C24，app 内置该降级文案）。

## 发现的问题

1. **[阻断云端] API Key 无效**：PC 端直连 agnes-ai /chat/completions 返回 `Invalid token`。需用户更换有效 Key 后重测云端生成与降级文案。
2. **[输入体验，非缺陷] adb input text 不支持中文**，且 OneUI 键盘弹出导致 EditText 布局位移——自动化输入曾错位打进 etModel（已清理恢复）。人工使用不受影响。
3. **[平台特性] 三星 Freecess 冻结**：app 切后台即被冻结（uiautomator 偶发抓不到树），测试期需保持前台；不影响真实使用（通知触发会解冻）。

## 未实测项（verified:false，待条件）

1. **端到端通知→捕获→生成→IME 交付**：NotifyCaptureService 仅映射六源包名（wechat/dingtalk/wework/lark/qq/qqmail），adb 无法伪造包名发通知，需真实微信消息触发（用另一设备发一条含「？」的微信消息即测；候选应出现在通知/输入法面板，云端 Key 失效时走本地模板+降级提示）。
2. 输入法实际切换为默认（tvStatus「输入法为默认：否」，需人工点「③切换输入法」）。
3. 云端生成真实响应（等有效 Key）。

## 本次回写

无新增单。
