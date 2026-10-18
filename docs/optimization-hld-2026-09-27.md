# EmoReply 优化迭代概要设计（HLD 简版）

## 模块映射

| 模块 | 代码位置 | 设计要点 |
|------|---------|---------|
| M1 体验快赢 | 候选区/生成循环/输入法配色（EmoReplyImeService、CloudLlmGenerator）：换一批=复用会话 prompt 重调；一键复制=ClipboardManager；语气滑杆=style 数值化注入；暗色=DayNight 资源 | FR-7.1/7.2/7.3/9.1 |
| M2 智能增强 | 生成质量与安全（pipeline 诈骗检测、CloudLlmGenerator prompt 注入、MediaPipe LLM 端侧后端 LlmBackend 抽象）：三维筛选=弹层选参注入；端侧=LlmInferenceSession 同接口 | FR-8.1/8.2/8.3/8.4 |
| M3 界面升级 | 布局与主题（iconRow、候选适配器、ThemeManager 新建）：Smartbar=主行+OverflowPopup；标签=splitRationale 分类映射；Monet=动态取色映射色槽 | FR-9.2/9.3/10.1/10.2 |
| M4 支撑体验 | 读屏进度回调与首次引导（readFromScreen、MainActivity）：进度=批次回调；引导=标记+三步页 | FR-7.4/7.5 |
| M5 长期能力 | 统计/记忆/动作族/收藏（复用既有加密与存储体系）：统计=仅计数；记忆=kb.enc 扩展；收藏=加密本地 | FR-7.6/7.7/8.5/8.6/8.7/8.8/9.4/10.3/10.4 |

## 技术决策

1. LLM 后端抽象：`LlmBackend` 接口统一 CloudLlmGenerator 与端侧 MediaPipe 后端；
2. 主题系统沿用 IME 现有动态构建 UI（代码建 view），颜色集中到 ThemeTokens 单例；
3. 零留存边界：统计仅计数、记忆加密、收藏加密，均本地。

## 风险登记

| 端侧模型包体积与机型兼容 | 🟠 高 | 试点开关默认关，白名单机型先行 |
| OneUI IME 显示异常复发 | 🟠 高 | 部署脚本固化 grant+rebind；观察项单跟踪 |
| 群聊误判回归 | 🟡 中 | py 事实源测试护栏，阈值改动必须带样本回归 |
