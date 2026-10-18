# T01164 品牌图标接入 Android/iOS 与安装包打包验证 —— 处理结果

> 处理时间：2026-09-27 ｜ 上游：T01163（品牌主标与全套资产）
> 结论：Android 已接入并编译通过；桌面端已实打包并像素级验证图标；iOS 资产已预置（本机无 Xcode App 工程，编译验证不可在本环境落地）

## 根因

T01163 交付了品牌资产，但三端均未使用：

- `android-app`：`res/` 下无任何 `mipmap-*`，`AndroidManifest.xml` 未声明 `android:icon` → 安装后是系统默认绿机器人图标。
- `desktop-app`：`package.json` 无 `build` 配置、未安装 electron-builder → 只能 `electron .` 源码运行，无安装包，任务栏为 Electron 默认图标（T01163 已加窗口 `icon`，但未覆盖 exe/安装包）。
- `ios-app`：仅有 SwiftPM 核心包（`Package.swift`，targets 只有 EmoReplyCore / 测试），**没有 Xcode App target 与 Assets.xcassets** → 图标无处可接。

## 影响范围

- Android：`android-app/app/src/main/res/` 新增 `mipmap-*`（10 文件）+ `drawable-*`（10 文件）+ `mipmap-anydpi-v26/`（2 XML）；`AndroidManifest.xml` +2 属性。
- 桌面端：`package.json`（build 配置 + dist 脚本 + electron-builder 依赖）、新增 `scripts/pkg-semver.js`、`assets/` 补 2 个图标、`index.html` 加 favicon link；产出 `desktop-app/dist/`（NSIS 安装包 / portable / win-unpacked，共 427 MB）。
- iOS：新增 `brand/ios/AppIcon.appiconset/`（15 PNG + Contents.json）；**未改动 ios-app**。
- 品牌资产：`brand/_tools/build_assets.py` 增加 iOS/favicon 生成与自适应图标修正；`brand/README.md` 同步。

## 修复（改动点）

### 1. Android 接入（已完成）

| 路径 | 内容 |
| --- | --- |
| `android-app/app/src/main/res/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_launcher.png`、`ic_launcher_round.png` | 方形/圆形启动器图标（48/72/96/144/192），API<26 兜底 |
| `android-app/app/src/main/res/drawable-{mdpi..xxxhdpi}/ic_launcher_foreground.png`、`ic_launcher_background.png` | 自适应图标图层：**108dp 画布**（108/162/216/324/432），前景图形占 62%（落在 72dp 安全区内），背景为满幅深蓝→青渐变 |
| `android-app/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml`、`ic_launcher_round.xml` | `<adaptive-icon>` 组合上述前后景 |
| `android-app/app/src/main/AndroidManifest.xml` | `android:icon="@mipmap/ic_launcher"`、`android:roundIcon="@mipmap/ic_launcher_round"` |

> 通知小图标：当前代码无通知（`grep setSmallIcon` 零命中），故未落地资源；后续加通知时请用 `brand/mono/emoreply-inverse-*.png`（系统会对小图标单色化，彩色图会变灰块），已写入 `brand/README.md`。

### 2. iOS 资产预置（本机不可编译）

`ios-app` 是 SwiftPM 包，没有 App target，因此**无法接入 AppIcon**（属本环境不可落地）。已按 Xcode 规范预置：

`brand/ios/AppIcon.appiconset/` —— 15 张 PNG（20/29/40/58/60/76/80/87/120/152/167/180/1024）+ `Contents.json`（universal + ios-marketing）。
关键点：iOS 会再套圆角遮罩且 App Store 拒绝含 alpha 的图标 → 脚本生成的是**满幅无 alpha 版本**（徽标放大到 100%，四角用同向渐变补齐，校验 `icon-1024.png` 为 RGB、角点 alpha=255）。建 Xcode App 工程时把该目录拖进 `Assets.xcassets` 即可。

### 3. 桌面端打包（已实打包验证）

- `desktop-app/package.json`：新增 `build` 配置（`appId=com.emoreply.desktop`、`productName=EmoReply`、`win.icon=assets/emoreply.ico`、`nsis.installerIcon/uninstallerIcon/installerHeaderIcon`、`mac.icon=assets/emoreply.icns`、`linux.icon=assets/emoreply-512.png`、target=nsis+portable、`files` 白名单含 `assets/**/*`），新增 `dist` / `dist:mac` 脚本，`devDependencies` 加 `electron-builder@^25.1.8`（已安装，334 包）。
- `desktop-app/scripts/pkg-semver.js`（新增）：**electron-builder 强制 version 为合法 semver，而本项目 patch 号是 `YYYYMMDD-NNN`（T01160 约定）** → 该脚本在打包期临时改写为 `0.1.0-YYYYMMDD-NNN`，打包结束（无论成败）还原原文件，两条约定同时成立。`npm run dist` 即走这条链路。
- `desktop-app/assets/`：`emoreply-512.png`、`favicon.ico`；`desktop-app/index.html` 加 `<link rel="icon">`。
- 产物：`dist/EmoReply-0.1.0-20260927-004-x64.exe`（NSIS 82.7 MB）、`dist/EmoReply-0.1.0-20260927-004-x64-portable.exe`（82.3 MB）、`dist/win-unpacked/EmoReply.exe`。

### 4. Web/PWA（顺带补齐）

`brand/favicon/`：`favicon.ico`（16/32/48）、`apple-touch-icon.png`（180）、`pwa-192.png`、`pwa-512.png`。

### 5. 资产脚本修正（T01163 遗留缺陷）

`brand/_tools/build_assets.py`：

- **自适应图标前景画布错误**：原按启动器尺寸（48–192）生成前景，作为 108dp 图层会被系统放大并顶出安全区 → 改为 108dp 画布（108/162/216/324/432），图形裁到自身 bbox 后按 62% 居中。
- **图形 mask 被徽标外发光污染**：AI 出图在徽标外缘有一圈淡色外发光，被「亮且低饱和」判据误判成图形，导致前景裁切 bbox 偏大、图形只占 41% → 新增 `_erode()` 对徽标 alpha 腐蚀 6px，只在徽标内部提取图形；修后前景占比 61–62%、反白版 bbox 干净。

## 验证（实测证据）

1. **Android 编译**：`JAVA_HOME=D:/android/jdk17/jdk-17.0.20.1+1 gradle assembleDebug --offline` → **BUILD SUCCESSFUL in 57s**（35 tasks）。
2. **APK 图标声明**：`aapt2 dump badging app-debug.apk` →
   `application-icon-120/160/240/320/480/640/65534 = res/mipmap-anydpi-v26/ic_launcher.xml`（全密度均指向自适应图标）。
3. **APK 资源**：`unzip -l` 确认 `res/mipmap-*/ic_launcher.png|ic_launcher_round.png`、`res/drawable-*/ic_launcher_foreground.png|ic_launcher_background.png`、`res/mipmap-anydpi-v26/ic_launcher*.xml` 全部打进包。
4. **自适应前景安全区**：前景内容占画布 61.1%（mdpi）/ 61.8%（xxxhdpi），中心偏移 0–2px（< 72dp 安全区 66.7%）。
5. **iOS 图标集**：解析 `Contents.json` 逐条校验 15 张图 —— 尺寸与 `size×scale` 完全一致、全部为 RGB（无 alpha），**0 问题**；ASCII 还原确认满幅渐变到边、图形居中未被裁。
6. **桌面端实打包**：`node scripts/pkg-semver.js --win --x64` → electron-builder 25.1.8 产出 NSIS + portable + win-unpacked，版本临时改写与还原均打印确认（`0.1.0-20260927-004` → `20260927-004`）。
7. **exe 图标像素级验证**（自写 `tmp/extract_exe_icon.py` 走 PE 资源表 RT_ICON）：
   - `dist/win-unpacked/EmoReply.exe`、NSIS 安装包、portable 包 **三者均内嵌 7 档图标 256/128/64/48/32/24/16**；
   - 抽出的 256×256 与 `brand/png/emoreply-256.png` **逐像素平均差 = 0.0（完全一致）** → 图标确为品牌图，非 Electron 默认图标。

⚠️ **未闭环项（需外部条件）**：

- **Android 真机外观**：`adb devices` 无设备（离线），未做真机安装与启动器外观确认；APK 已生成（`release/build/app/outputs/apk/debug/app-debug.apk`），连接设备后 `adb install -r` 即可目视核对（各厂商遮罩下气泡尾是否被裁）。
- **iOS 编译**：本机无 macOS/Xcode，且当前仓库无 App target，仅有预置资产。
- **安装包运行**：本会话未启动 GUI，安装包安装流程与开始菜单/任务栏显示需人工过目（源自主图标像素一致，风险很低）。

## 本次回写

- 新增/修改（Android）：`android-app/app/src/main/res/mipmap-*`、`drawable-*`、`mipmap-anydpi-v26/*`、`AndroidManifest.xml`。
- 新增/修改（桌面端）：`desktop-app/package.json`、`desktop-app/scripts/pkg-semver.js`、`desktop-app/assets/{emoreply-512.png,favicon.ico}`、`desktop-app/index.html`；产物 `desktop-app/dist/`（427 MB，可 `npm run dist` 重新生成）。
- 新增（品牌资产）：`brand/ios/AppIcon.appiconset/`、`brand/favicon/`、`brand/_tools/build_assets.py`（自适应修正 + iOS/favicon 生成）、`brand/README.md`（接入说明同步）。
- 结论文档：`docs/t01164-icon-integration-result.md`（本文档）。

## 下一步建议（可选）

1. 连接真机后 `adb install -r release/build/app/outputs/apk/debug/app-debug.apk`，确认启动器/设置页/最近任务里的图标外观（尤其圆形遮罩）。
2. 后续加通知时，用 `brand/mono/emoreply-inverse-*.png` 作 `setSmallIcon`，避免被单色化成灰块。
3. iOS：建 Xcode App 工程后拖入 `brand/ios/AppIcon.appiconset`，`xcodebuild` 校验无 "missing icon" 警告。
4. 如需正式发布：先做商标检索（第 9/42 类），再考虑代码签名（当前 exe 未签名，Windows 会报未知发布者）。
