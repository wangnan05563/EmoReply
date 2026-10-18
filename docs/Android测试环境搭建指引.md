# 本机 PC 搭建 Android 测试环境指引

> 目的：为 M1 概念验证（T00974）与真机验证缺口单（T00644）提供可执行的 Android 环境搭建与验证路径。
> 核查日期：2026-09-24　|　目标机：Windows 10 企业版 22H2（19045）

## 1. 本机现状核查（实测）

| 项 | 实测值 | 是否满足 | 说明 |
|---|---|---|---|
| 操作系统 | Windows 10 企业版 22H2（10.0.19045） | ✅ | Android Studio 与模拟器均支持 |
| CPU | i7-1165G7（4 核 8 线程） | ✅ | 支持 VT-x |
| 虚拟化 | `HypervisorPresent = True`（HvHost/vmcompute 运行中） | ✅ | 模拟器可用 WHPX 硬件加速，无需改 BIOS |
| 内存 | 63.7 GB | ✅ | 远超模拟器 4 GB 建议值 |
| GPU | Intel Iris Xe（共享显存） | ⚠️ 可用 | 可用，重负载渲染偏慢 |
| JDK | 仅 TRAE 内置 JRE 21（非独立安装） | ⚠️ | AGP 8.7+ 支持 JDK 21；仍建议独立装 JDK 17 |
| Android SDK | 无（`ANDROID_HOME` 空，无 `adb`） | ❌ | 需安装 |
| Android Studio | 未安装 | ❌ | 需安装 |
| **可用磁盘** | C 3.3 GB / D 0.8 GB / E 1.9 GB / F 5.5 GB | ❌ **硬阻塞** | 模拟器方案需 ≥20 GB，当前最大空闲 5.5 GB |

**结论：唯一阻塞是磁盘空间。** 虚拟化、内存、系统版本均已满足，无需任何 BIOS 改动。

因此给出两条路径——**路径 A 绕过磁盘问题且更贴近验收目标，优先推荐**。

---

## 2. 路径 A：真机 USB 调试（推荐，磁盘占用 ≈ 30 MB）

T00644 要求的 FR-1.1（通知捕获成功率）与 FR-4.2（IME 注入成功率）**本质上是真机指标**，
模拟器只能做流程预演。若手边有 Android 11+ 手机，此路径可直接闭环验收，且几乎不占磁盘。

### 2.1 安装（约 5 分钟）

1. 下载 **platform-tools**（仅含 adb/fastboot）：`https://developer.android.com/tools/releases/platform-tools`
   解压到 `D:\android\platform-tools`（约 30 MB）。
2. 加入 PATH（当前会话临时生效）：

```powershell
$env:Path += ";D:\android\platform-tools"
adb version
```

3. 手机端：设置 → 关于手机 → 连点「版本号」7 次开启开发者选项 → 开启 **USB 调试**；
   USB 连接后手机弹窗选「允许调试」。

```powershell
adb devices        # 期望看到 <序列号>  device
```

### 2.2 验证「通知监听捕获」（对应 FR-1.1）

| 步骤 | 操作 |
|---|---|
| ① 授权 | 安装被测 App 后：设置 → 通知 → 通知使用权 → 勾选被测 App（或 `adb shell cmd notification allow_listener <包名>/<服务类>`，Android 11+） |
| ② 开「显示详情」 | 微信/钉钉内开启消息详情通知（验收强前置，否则载荷为空 → 走 FR-1.5 兜底） |
| ③ 触发 | 用另一账号向被测机发消息，或用命令模拟：`adb shell cmd notification post -S bigtext -t "张三" tag1 "在吗"` |
| ④ 取证 | `adb logcat -s EmoReply:V`（按实现的 tag 调整），统计捕获条数与字段提取正确率 |

### 2.3 验证「IME 候选条注入」（对应 FR-4.2）

```powershell
# 列出全部输入法（含未启用的自定义 IME）
adb shell ime list -s -a

# 启用并切为默认输入法
adb shell ime enable com.example.emoreply/.EmoReplyIme
adb shell ime set    com.example.emoreply/.EmoReplyIme

# 回到系统输入法（避免调试时无法正常打字）
adb shell ime set com.android.inputmethod.latin/.LatinIME
```

切到自定义 IME 后，在任意输入框（如系统「设置」搜索框）输入，观察 AI 候选条；
点选候选统计注入/复制成功率（目标 ≥99%）。

### 2.4 验收命令清单（可直接存为脚本）

```powershell
adb devices
adb shell cmd notification allow_listener com.example.emoreply/.NotifyListener
adb shell ime list -s -a
adb shell ime set com.example.emoreply/.EmoReplyIme
adb logcat -c; adb logcat -s EmoReply:V
```

---

## 3. 路径 B：模拟器（需先腾出 ≥20 GB 磁盘）

适用于无真机、需可复现演示的场景。**执行前必须先清理磁盘**（当前 D 盘仅 0.8 GB 可用）。

### 3.1 磁盘预算（轻量 CLI 方案，省去 Android Studio 约 8 GB）

| 组件 | 下载 | 解压后 |
|---|---|---|
| command-line tools | 150 MB | 150 MB |
| platform-tools | 15 MB | 30 MB |
| emulator | 500 MB | 1.3 GB |
| system-image（android-31 google_apis x86_64） | 1.2 GB | 2.5 GB |
| AVD 用户数据 | — | 2–8 GB |
| **合计** | | **约 8–14 GB**（保守预留 20 GB） |

### 3.2 安装步骤

1. 下载 **command-line tools**：`https://developer.android.com/studio#command-line-tools-only`
   解压到 `D:\android\sdk\cmdline-tools\latest`。
2. 设环境变量（当前会话）：

```powershell
$env:ANDROID_HOME = "D:\android\sdk"
$env:Path += ";D:\android\sdk\cmdline-tools\latest\bin;D:\android\sdk\platform-tools;D:\android\sdk\emulator"
```

3. 安装组件并接受许可：

```powershell
sdkmanager "platform-tools" "emulator" "system-images;android-31;google_apis;x86_64"
sdkmanager --licenses
```

4. 创建并启动 AVD：

```powershell
avdmanager create avd -n emoreply31 -k "system-images;android-31;google_apis;x86_64" -d pixel_5
emulator -avd emoreply31 -gpu swiftshader_indirect   # Intel 核显建议软渲染更稳
adb devices
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

对应任务：环境搭建是 **T00644**（真机验证缺口单，状态 todo）的前置条件，本指引为其执行方案。

---

## 5. 风险与替代

| 风险 | 应对 |
|---|---|
| 磁盘不足（当前最大空闲 5.5 GB） | 优先走路径 A；或清理 ≥20 GB 后再走路径 B |
| Intel 核显模拟器渲染慢/黑屏 | `-gpu swiftshader_indirect` 软渲染 |
| 模拟器装不上微信 | 通知捕获指标改由真机取数，模拟器只做流程预演 |
| 内置 JRE 被 TRAE 更新影响 | 独立安装 JDK 17 并设 `JAVA_HOME` |