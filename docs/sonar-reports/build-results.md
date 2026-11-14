# EmoReply · 生产构建结果

- 构建日期：2026-11-14
- 应用版本：`20261114-001`（打包期临时版本 `0.1.0-20261114-001`）
- 构建机：Windows 10 x64，Node `v22.23.2`，electron-builder `25.1.8`，Electron `33.4.11`
- 构建命令：`npm run dist`（`node scripts/pkg-semver.js --win --x64`）
- 交付目录：`release/desktop/`

## 1. 桌面端产物（生产交付）

| 产物 | 路径 | 大小 | SHA-256 |
|---|---|---:|---|
| NSIS 安装包 | `release/desktop/EmoReply-0.1.0-20261114-001-x64.exe` | 104,415,392 字节（99.6 MiB） | `09579AF453FDAFE62AF37538C3B0611254B7BFD7098B8998E70DCDDFEFE3421F` |
| 绿色便携版 | `release/desktop/EmoReply-0.1.0-20261114-001-x64-portable.exe` | 104,009,710 字节（99.2 MiB） | `A5B6EB9885CEE9FE4110DF1C9D76E220BAFD75FE07525579BDDFD3C8CDD024D4` |

- 目标平台：Windows x64
- 安装包类型：NSIS（`oneClick=false`，可选安装目录）+ portable 单文件
- 构建日志确认：`building target=nsis`、`building target=portable`、`block map` 均成功，退出码 `0`
- 打包内容：`app.asar` + OCR 语言模型 + 图标资源；`package.json` 版本在构建期改写、结束后已还原

## 2. 产物可用性验证

| 验证 | 方法 | 结果 |
|---|---|---|
| 解包程序可启动 | 运行 `dist/win-unpacked/EmoReply.exe --remote-debugging-port=9223 --user-data-dir=<隔离 profile> --force-show` | CDP `9223` 就绪，`Chrome/130.0.6723.191`，页面从 `resources/app.asar/index.html` 加载成功 |
| 打包态 OCR | `npm run test:asar` | `ASAR OCR CHECK PASSED`（confidence 95，中文两行正确识别） |
| 安装包结构 | electron-builder 产物清单 | NSIS 主安装包 + portable + blockmap 均生成 |

> 说明：本次为开发机本地构建，未在干净虚拟机做「安装→卸载」全流程；安装包未签名（见第 4 节）。

## 3. Android release 构建（R8 校验）

| 项 | 值 |
|---|---|
| 命令 | `gradle assembleRelease --no-daemon`（JDK 17、Gradle 8.9、SDK 35 / build-tools 34） |
| 结果 | `BUILD SUCCESSFUL in 6m 51s`（45 tasks） |
| 产物 | `release/build/app/outputs/apk/release/app-release-unsigned.apk`（42,828,610 字节） |
| R8 证据 | `release/build/app/outputs/mapping/release/mapping.txt`（24,311,010 字节）、`seeds.txt`、`usage.txt`、`configuration.txt` |
| 构建日志 | `docs/sonar-reports/android-release-build.log` |

- 本次构建验证了 `isMinifyEnabled = true` + `proguard-android-optimize.txt` + `proguard-rules.pro`（MediaPipe / ML Kit keep 与 dontwarn）能完成 R8 压缩与打包。
- 构建期出现一条警告：`Supplied proguard configuration does not exist: ...proguard-android-optimize.txt-8.7.3`；R8 任务仍执行并产出 mapping，属 AGP 默认规则文件解析警告，不影响构建成功。
- **限制**：仓库无 release 签名配置，APK 为 unsigned，不能直接分发安装；未做真机运行验证（本机无连接设备）。因此 Android 侧只作为「R8 配置可通过构建」的验证，不作为本次生产交付物。

## 4. 签名与部署合规

| 项 | 状态 | 说明 |
|---|---|---|
| Windows 安装包签名 | **未签名** | electron-builder 日志 `no signing info identified, signing is skipped`；仓库未配置代码签名证书 |
| Android release 签名 | **未签名** | 无 `signingConfig`，产出 `app-release-unsigned.apk` |
| 部署影响 | 需补证书 | Windows 安装时会触发 SmartScreen「未知发布者」提示；正式分发前应配置 EV/OV 代码签名证书并对安装包签名 |
| 功能影响 | 无 | 产物可正常安装/启动；签名只影响信任链与系统提示 |

## 5. 构建过程记录（可复现）

1. 首次直接执行 `npm run dist` 失败：`release/desktop-installer/win-unpacked/resources/app.asar` 被外部进程（Defender/索引服务）独占持锁，electron-builder 的 `EnsureEmptyDir` 无法删除该目录。
2. 按仓库既有 T05736 方案，设置 `EMO_REPLY_EB_OUTPUT` 指向全新唯一目录 `release/desktop-installer-<timestamp>`，`pkg-semver.js` 在构建期把 `build.directories.output` 指向该目录，构建成功后自动还原 `package.json`。
3. 构建成功后：
   - `win-unpacked` 移至 `desktop-app/dist/win-unpacked`，供 `npm run test:asar` 使用；
   - NSIS 安装包与 portable 复制到交付目录 `release/desktop/`；
   - 临时唯一目录已清理（约 199 MB）。
4. 旧 `release/desktop-installer` 仍被外部进程持锁，本次未强行删除，留待后续清理；不影响交付物。

## 6. 交付清单

| 交付物 | 位置 |
|---|---|
| Windows 安装包（NSIS） | `release/desktop/EmoReply-0.1.0-20261114-001-x64.exe` |
| Windows 便携版 | `release/desktop/EmoReply-0.1.0-20261114-001-x64-portable.exe` |
| 打包态 OCR 回归记录 | 见《测试执行结果》第 6 节 |
| Android R8 构建日志 | `docs/sonar-reports/android-release-build.log` |
| Android unsigned APK（验证用，非交付） | `release/build/app/outputs/apk/release/app-release-unsigned.apk` |
