# 品牌图标接入 Android/iOS 与安装包打包验证

来源：T01163（Logo 与多尺寸图标设计）

## 背景

T01163 已交付全套品牌资产 `D:/code/otherProjects/28_AIMsg/brand/`（矢量 4 份、PNG 8 档、ICO 7 档、ICNS、单色/反白、Android mipmap 五档、预览图、规范 README）。
桌面端已接入（`desktop-app/main.js` 的 BrowserWindow `icon` + `desktop-app/assets/emoreply.ico|icns`）；**Android / iOS 尚未接入，安装包打包配置也未落地**，需编译/真机验证后闭环。

## 工作项

1. **Android 接入**
   - 把 `brand/android/mipmap-{mdpi..xxxhdpi}/` 下 `ic_launcher.png`、`ic_launcher_round.png`、`ic_launcher_foreground.png`、`ic_launcher_background.png` 拷入 `android-app/app/src/main/res/` 同名目录；
   - 新增 `res/mipmap-anydpi-v26/ic_launcher.xml`（adaptive：foreground + background）与 `ic_launcher_round.xml`；
   - `AndroidManifest.xml` 的 `<application>` 增加 `android:icon="@mipmap/ic_launcher"`、`android:roundIcon="@mipmap/ic_launcher_round"`；
   - 通知栏小图标建议使用 `brand/mono/emoreply-inverse-*.png`（系统会对通知图标做单色化处理，彩色图标会变灰块）。
2. **iOS 接入**（本机无 macOS/Xcode，需在用户侧环境执行）
   - 用 `brand/png/emoreply-1024.png` 生成 `Assets.xcassets/AppIcon.appiconset/` 各尺寸 + `Contents.json`；
   - 确认 iOS 的圆角遮罩不会切掉气泡尾（必要时按 iOS 模板下调图形填充比，脚本 `fill_ratio` 可调）。
3. **桌面端打包配置**
   - 引入 electron-builder（或 NSIS）后配置 `build.win.icon = assets/emoreply.ico`、`build.mac.icon = assets/emoreply.icns`；
   - 产出安装包后确认：exe 在资源管理器/安装向导显示正确图标、开始菜单与任务栏显示正常、卸载程序图标正确。
4. **Web/PWA**（如有官网需求）：导出 `favicon.ico`（含 16/32/48）与 `apple-touch-icon` 180px。

## 验收标准

- Android：编译通过（`gradle assembleDebug --offline`），真机安装后启动器图标为新版、自适应图标在各厂商遮罩下气泡尾不被裁掉、通知图标不发灰块。
- 桌面：打包后的 exe 与安装包图标正确显示（需实机/虚拟机验证）。
- iOS：AppIcon 编译无 missing icon 警告，模拟器/真机显示正常（用户侧环境）。

## 备注

- 若视觉终审要求微调（星光位置、爱心比例、渐变），先改 `brand/logo/*.svg` 与 `_tools/build_assets.py` 常量并重跑脚本，再执行接入；位图族会自动同步重建。
- 商标检索（第 9/42 类）建议在对外正式发布前完成。
