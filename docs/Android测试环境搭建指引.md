# 本机 PC 搭建 Android 测试环境指引

> 目的：给出一条可执行的 Android 构建、安装与真机验证路径。
> 核查日期：2026-10-31（T03716）　|　目标机：Windows 10 企业版 22H2（19045）

## 1. 本机现状核查（实测）

| 项 | 实测值 | 是否满足 | 说明 |
|---|---|---|---|
| 操作系统 | Windows 10 企业版 22H2（10.0.19045） | ✅ | Android Studio 与模拟器均支持 |
| CPU | i7-1165G7（4 核 8 线程） | ✅ | 支持 VT-x |
| 内存 | 63.7 GB | ✅ | 远超模拟器 4 GB 建议值 |
| JDK | JDK 17：`E:\Android\jdk17\jdk-17.0.20.1+1` | ✅ | AGP 8.7+ 要求 JDK 17；已独立安装 |
| Gradle | 8.9：`E:\Android\gradle-8.9\bin\gradle.bat` | ✅ | 支持 `--offline`（依赖已缓存） |
| Android SDK | `E:\Android\sdk`（`android-app/local.properties` 的 `sdk.dir`） | ✅ | 含 platform-tools / build-tools |
| adb | `E:\Android\sdk\platform-tools\adb.exe` | ✅ | 不在 PATH，调用时用绝对路径 |
| 真机连接 | `adb devices` 输出为空 | ❌ **硬阻塞** | 无设备，依赖真机的验收无法执行 |
| 可用磁盘 | C 3.6 GB / D 2.9 GB | ❌ **硬阻塞** | 唯一可用模拟器镜像要求约 7.4 GB 数据分区，无法创建 |

**结论：构建链路已就绪，当前只剩两类阻塞——① 没有可连接的 Android 真机；② 模拟器镜像要求 7.4 GB 单盘空间，现有磁盘不满足。**

因此给出两条路径——**路径 A 绕过磁盘问题且更贴近验收目标，优先推荐**。

---

## 2. 路径 A：真机 USB 调试（推荐，磁盘占用 ≈ 30 MB）

SRS 的 FR-1.1（通知捕获成功率）与 FR-4.2（IME 注入成功率）**本质上是真机指标**，
模拟器只能做流程预演。若手边有 Android 11+ 手机，此路径可直接闭环验收，且几乎不占磁盘。

### 2.1 连接与确认（约 5 分钟）

1. 手机端：设置 → 关于手机 → 连点「版本号」7 次开启开发者选项 → 开启 **USB 调试**；
   USB 连接后手机弹窗选「允许调试」。
2. PC 端确认设备（adb 已随 SDK 安装，无需再下载 platform-tools）：

```powershell
& "E:\Android\sdk\platform-tools\adb.exe" devices   # 期望看到 <序列号>  device
```

### 2.2 验证「通知监听捕获」（对应 FR-1.1）

被测包名固定为 `com.emoreply.poc`，监听服务为
`com.emoreply.poc/com.emoreply.poc.capture.NotifyCaptureService`。

| 步骤 | 操作 |
|---|---|
| ① 授权 | 一键脚本会自动代授权；失败时在手机上：设置 → 通知 → 通知使用权 → 勾选 EmoReply |
| ② 开「显示详情」 | 微信/钉钉内开启消息详情通知（验收强前置，否则载荷为空 → 走通知兜底） |
| ③ 触发 | 用另一账号向被测机发消息，或模拟通知：`adb shell cmd notification post -S bigtext -t "张三" tag1 "在吗"` |
| ④ 取证 | `adb logcat -c` 后运行 `adb logcat`，再用 PowerShell `Select-String com.emoreply.poc` 过滤，统计捕获条数与字段提取正确率 |

> 无障碍读屏（`.capture.EmoReplyReaderService`）属于另一条取对话通道，需在
> 设置 → 无障碍 → EmoReply 中手动开启；通知链路验收不依赖它。

### 2.3 验证「IME 候选条注入」（对应 FR-4.2）

```powershell
$adb = "E:\Android\sdk\platform-tools\adb.exe"

# 列出全部输入法（含未启用的自定义 IME）
& $adb shell ime list -s -a

# 切到 EmoReply 输入法（PoC 输入法只有候选条、没有完整键盘）
& $adb shell ime set com.emoreply.poc/.ime.EmoReplyImeService

# 测完立即切回系统输入法，恢复本机正常打字
& $adb shell ime set com.android.inputmethod.latin/.LatinIME
```

切到自定义 IME 后，在任意输入框（如系统「设置」搜索框）输入，观察 AI 候选条；
点选候选统计注入/复制成功率（目标 ≥99%）。

### 2.4 一键脚本（推荐入口）

```bat
REM 环境准备 + 构建打包，并把 APK 复制到 release\EmoReply-poc-debug.apk
scripts\安卓构建打包.cmd

REM 已构建过、只想重装并跑一轮真机验证（默认不切输入法，-SetIme 才切）
scripts\安卓推送测试.cmd
```

- 构建产物：`release\build\app\outputs\apk\debug\app-debug.apk`（当前 versionCode 249401 / 0.1.0-poc）。
- 推送脚本默认读取 `release\EmoReply-poc-debug.apk`，所以**重装前先跑一次构建脚本**；
  也可用 `-ApkPath` 显式指定其他 APK。
- 脚本会依次完成：检测设备 → 安装 → 启动 `MainActivity` → 代授权通知监听 → 提示输入法切换命令。

### 2.5 验收命令清单（可直接存为脚本）

```powershell
$adb = "E:\Android\sdk\platform-tools\adb.exe"
& $adb devices
& $adb shell cmd notification allow_listener com.emoreply.poc/com.emoreply.poc.capture.NotifyCaptureService
& $adb shell ime list -s -a
& $adb shell ime set com.emoreply.poc/.ime.EmoReplyImeService
& $adb logcat -c; & $adb logcat | Select-String com.emoreply.poc
```

---

## 3. 路径 B：模拟器（需先腾出 ≥7.4 GB 单盘空间）

适用于无真机、需可复现演示的场景。**执行前必须先清理磁盘**：当前唯一可用镜像是
`system-images;android-37.0;google_apis_playstore;x86_64`，启动时固定要求约 7.4 GB 的
userdata 分区（显式改小 `disk.dataPartition.size` 仍报 need 7372.80 MB），
而本机 C/D 盘均不足。

### 3.1 磁盘预算（轻量 CLI 方案，省去 Android Studio 约 8 GB）

| 组件 | 下载 | 解压后 |
|---|---|---|
| command-line tools | 150 MB | 150 MB |
| platform-tools | 15 MB | 30 MB |
| emulator | 500 MB | 1.3 GB |
| system-image（android-37.0 google_apis_playstore x86_64） | 1.2 GB | 2.5 GB |
| AVD 用户数据 | — | **7.4 GB（该镜像固定下限）** |
| **合计** | | **约 12 GB**（建议单一盘符预留 15 GB 以上） |

### 3.2 安装步骤

1. 下载 **command-line tools**：`https://developer.android.com/studio#command-line-tools-only`
   解压到 `E:\Android\sdk\cmdline-tools\latest`（本机已装，可跳过）。
2. 设环境变量（当前会话）：

```powershell
$env:ANDROID_HOME = "E:\Android\sdk"
$env:Path += ";E:\Android\sdk\cmdline-tools\latest\bin;E:\Android\sdk\platform-tools;E:\Android\sdk\emulator"
```

3. 安装组件并接受许可：

```powershell
sdkmanager "platform-tools" "emulator" "system-images;android-37.0;google_apis_playstore;x86_64"
sdkmanager --licenses
```

4. 创建并启动 AVD：

```powershell
avdmanager create avd -n emoreply37 -k "system-images;android-37.0;google_apis_playstore;x86_64" -d pixel_5
emulator -avd emoreply37 -gpu swiftshader_indirect   # Intel 核显建议软渲染更稳
& "E:\Android\sdk\platform-tools\adb.exe" devices
```

### 3.3 模拟器上的验证差异（务必知悉）

- **通知监听**：模拟器无微信登录态，可用 `adb shell cmd notification post` 模拟通知验证链路；
  若需真实 IM，选带 Google Play 的镜像装微信——但**微信无 x86_64 原生包，装 arm 版会失败**，
  故真实 IM 场景仍以真机为准。
- **IME 注入**：模拟器可完整验证（`ime list/enable/set` 全支持），候选条需在真实输入框内观察。
- **结论**：模拟器适合验证「IME 注入」与「链路连通性」；「通知捕获成功率」指标仍应回真机取数。

---

## 4. 与验收准则 / 任务单的映射

| 验收准则 | 路径 A（真机） | 路径 B（模拟器） |
|---|---|---|
| FR-1.1 通知捕获成功率 ≥95% | ✅ 可闭环 | ⚠️ 仅链路预演，指标不可信 |
| FR-4.2 注入成功率 ≥99% | ✅ 可闭环 | ✅ 可闭环 |
| NFR 首次配置 <5 分钟 | ✅ 可实测 | ✅ 可实测 |

真机验证缺口按任务号在 MTask「aimsg三期」检索（原承载任务 T00644 等）。

---

## 5. 风险与替代

| 风险 | 应对 |
|---|---|
| 无真机连接（当前 `adb devices` 为空） | 借用 Android 11+ 真机走路径 A；或腾出 ≥7.4 GB 单盘后走路径 B |
| 磁盘不足（当前 C 3.6 GB / D 2.9 GB） | 优先走路径 A；或清理到单盘 ≥7.4 GB 后再走路径 B |
| Intel 核显模拟器渲染慢/黑屏 | `-gpu swiftshader_indirect` 软渲染 |
| 模拟器装不上微信 | 通知捕获指标改由真机取数，模拟器只做流程预演 |
| PoC 输入法没有完整键盘 | 验证完候选条立即 `ime set` 切回系统输入法 |
