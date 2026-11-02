# EmoReply 桌面端 POC（T01054 · 壳选型：Electron）

Windows 桌面形态先行：**悬浮窗 + 剪贴板交付**（FR-4.1 桌面面 / FR-4.2 不自动发送）。

## 运行

```powershell
cd D:\code\otherProjects\28_AIMsg\desktop-app
npm install          # 首次安装 electron（可用 --registry=https://registry.npmmirror.com 加速）
npm start
```

> 若在 WorkBuddy/其他 Electron 宿主的终端里启动，需先 `set ELECTRON_RUN_AS_NODE=` 清掉该变量，
> 否则 electron 会被当成纯 Node 运行（`require('electron').app` 为 undefined）。普通 cmd/资源管理器无此问题。

## 使用流程

1. 复制对方消息（任意聊天软件）→ 点「读剪贴板」
2. 选风格（正式/亲切/简短/幽默）→ 点「生成回复」
3. 点选候选 → 自动复制到剪贴板 → 自行粘贴发送（**全程无自动发送**）
4. 未配置 Key / 网络异常 / 402 → 自动降级本地模板并明确提示（附录 C24 同款约束）

## 配置

- 设置区（折叠）：接口地址 / 模型名 / API Key / 自称；**密钥仅存本机 localStorage，不入仓库**
- 默认 `https://api.deepseek.com/v1` + `deepseek-chat`（OpenAI 兼容，DeepSeek/通义/OpenAI 通用）

## 在线更新（T03996）

- 入口两处、实现一条：设置中心「在线更新」区块与托盘菜单「检查更新…」（后者只是唤回窗口并打开前者）
- 更新源是 `updater.js` 里的代码常量 `REPO`，界面与配置都不能改；下载地址只认 GitHub 官方域名，文件名做清洗
- 流程：`check()` 查 `releases/latest` → 有新版才允许 `download()` 到 `userData/updates/`（核对字节数，不完整即删）→ 用户在确认行点「确认安装并退出」才 `install()` 启动安装包并退出本程序
- 版本比较先归一日期序号（`YYYYMMDD-NNN`），再回落 semver；打包期 `scripts/pkg-semver.js` 的临时口径不影响判断
- 测试：`node scripts/updater.smoke.js`（纯逻辑，注入 fetch/spawn）+ `node scripts/tray-menu.smoke.js`（菜单项序与落点）
  + `node scripts/update-e2e.js <独立 profile>`（真实 GUI 点击 + 真实 GitHub 往返，用命中测试证明面板真的可见可点）
- 现状：仓库还没有任何 Release，所以真实环境点检查得到的是 `no_release`（「还没有发布任何版本」），发布第一个 Release 后同一入口即可用

## 与 Android 侧的关系

- 提示词模板、风格枚举、「内容|理由」解释句格式、降级文案与 m1-poc / android-app 同构
- 桌面端为独立交付面（不依赖通知监听），输入消息由剪贴板/后续手输补充
- 分类（Classify）与护栏（Guardrail）的 JS 移植属后续增强，当前 POC 未包含

## 已知边界（POC 尺度）

- 未打包安装器（electron-builder 后续接入）；`npm start` 开发态运行
- 无开机自启（托盘常驻、本地加密历史、关闭窗口时询问/记住去向均已实现）
- 密钥存 localStorage 明文（本机管理员可见）——与 Android 端 SharedPreferences 同级，M3 若引入同步再升级
