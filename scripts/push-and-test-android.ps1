<#
.SYNOPSIS
    EmoReply PoC —— 一键推送到真机并启动测试。

.DESCRIPTION
    复用构建产物（release\EmoReply-poc-debug.apk），依次完成：
    检测设备 → 安装 APK → 启动主界面 → 授予通知监听权限 → 提示输入法切换命令。
    设备未连接、APK 缺失、多台设备等异常一律给出明确提示后退出，不做隐式重试。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\push-and-test-android.ps1
    只安装不启动：   ... -NoLaunch
    顺带切换输入法： ... -SetIme
    换环境根目录：   ... -AndroidRoot E:\android

.NOTES
    目标机：Windows 10 + PowerShell 5.1（脚本未使用 PS7 专属语法）。
#>
[CmdletBinding()]
param(
    # 环境安装根目录，其下含 sdk\platform-tools\adb.exe；默认取 ANDROID_ROOT 或工程内 local.properties
    [string]$AndroidRoot,
    # 待安装的 APK，默认 <ProjectRoot>\release\EmoReply-poc-debug.apk
    [string]$ApkPath,
    # 安装完成后不自动启动应用
    [switch]$NoLaunch,
    # 安装完成后把输入法切到 EmoReply。PoC 输入法只有候选条、没有完整键盘，
    # 默认不切以免用户失去正常输入能力；要验证候选条时再显式开启。
    [switch]$SetIme
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common-paths.ps1')
$AndroidRoot = Resolve-AndroidRoot -ProjectRoot $ProjectRoot -Override $AndroidRoot
if (-not $ApkPath) { $ApkPath = Join-Path $ProjectRoot 'release\EmoReply-poc-debug.apk' }

$AdbExe = Join-Path $AndroidRoot 'sdk\platform-tools\adb.exe'
$Package = 'com.emoreply.poc'
$Activity = "$Package/.MainActivity"
# 通知监听与输入法服务都是「需用户在系统设置里授权」的组件，这里用 adb 直接代授权，
# 省去每次刷机/重装后手工点选；组件名必须与 AndroidManifest 中的声明一致
$NotifyListener = "$Package/$Package.capture.NotifyCaptureService"
$ImeId = "$Package/.ime.EmoReplyImeService"

function Write-Step { param([string]$Text) Write-Host $Text -ForegroundColor Cyan }

Write-Host ''
Write-Host 'EmoReply PoC · 真机推送测试' -ForegroundColor Cyan
Write-Host ("  APK：{0}" -f $ApkPath)
Write-Host ("  adb：{0}" -f $AdbExe)
Write-Host ''

# 前置检查放在设备检测之前：这两个缺失属于「环境/产物问题」，提示要能和「手机没插好」区分开
# 这里用 Write-Host + exit 而不是 throw：双击运行时 throw 会打印一整段红色调用栈，
# 把「哪一步缺东西」的关键信息淹掉
if (-not (Test-Path $AdbExe)) {
    Write-Host ("未找到 adb：{0}" -f $AdbExe) -ForegroundColor Red
    Write-Host '  可用 -AndroidRoot 指定环境根目录（其下应有 sdk\platform-tools\adb.exe）'
    exit 1
}
if (-not (Test-Path $ApkPath)) {
    Write-Host ("未找到 APK：{0}" -f $ApkPath) -ForegroundColor Red
    Write-Host '  请先运行 编译构建.cmd 打包'
    exit 1
}

# 只认 state=device 的行：unauthorized / offline 同样会列在 devices 里，
# 若不过滤会把「未授权」误判为「可用设备」，后续 adb 命令才失败，提示不直观
$devices = @(
    & $AdbExe devices 2>&1 |
        Where-Object { $_ -match '^\S+\s+device\s*$' } |
        ForEach-Object { ($_ -split '\s+')[0] }
)

if ($devices.Count -eq 0) {
    Write-Host '未检测到已授权设备，请依次检查：' -ForegroundColor Yellow
    Write-Host '  · 手机已用 USB 线连接本机'
    Write-Host '  · 已开启「设置 → 开发者选项 → USB 调试」'
    Write-Host '  · 手机上弹出的「允许 USB 调试吗」对话框已点「允许」'
    # 云桌面/VDI 的 USB 是客户端重定向进来的：Windows 设备管理器里能看到设备节点，
    # 但 adb 从 USB 层枚举不到；实测拔插一次 USB 线重新触发重定向即可恢复，
    # 这条与上面「没插好」的提示要分开，否则会被误判成手机端设置问题
    Write-Host '  · 若为云桌面/VDI（USB 经客户端重定向）：拔掉 USB 线再插回，重新触发'
    Write-Host '    重定向后重跑本脚本'
    exit 1
}
if ($devices.Count -gt 1) {
    # 多台设备时 adb 会以「more than one device」中断，这里提前说明并列出序列号
    Write-Host ('检测到多台设备：{0}' -f ($devices -join ', ')) -ForegroundColor Yellow
    Write-Host '请只保留一台手机连接后重试。'
    exit 1
}

$serial = $devices[0]
Write-Host ("[1/4] 设备已连接：{0}" -f $serial) -ForegroundColor Green

Write-Step '[2/4] 安装 APK ...'
& $AdbExe -s $serial install -r $ApkPath
if ($LASTEXITCODE -ne 0) {
    Write-Host ("安装失败（exit {0}）" -f $LASTEXITCODE) -ForegroundColor Red
    exit 1
}
Write-Host '      · 安装成功' -ForegroundColor Green

if (-not $NoLaunch) {
    Write-Step '[3/4] 启动应用 ...'
    & $AdbExe -s $serial shell am start -n $Activity | Out-Null
    Write-Host '      · 已启动 MainActivity' -ForegroundColor Green
} else {
    Write-Host '[3/4] 已指定 -NoLaunch，跳过启动' -ForegroundColor DarkGray
}

Write-Step '[4/4] 授予通知监听权限 ...'
# cmd notification 需要 Android 11+；失败时不能报「完成」，否则用户会以为已授权
& $AdbExe -s $serial shell cmd notification allow_listener $NotifyListener | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host '      · 完成' -ForegroundColor Green
} else {
    Write-Host '      · 失败，请在「设置 → 通知 → 通知使用权」中手动勾选 EmoReply' -ForegroundColor Yellow
}

if ($SetIme) {
    & $AdbExe -s $serial shell ime set $ImeId
    Write-Host '      · 已切换到 EmoReply 输入法' -ForegroundColor Green
    Write-Host '        测完请切回原输入法（PoC 输入法没有完整键盘）' -ForegroundColor Yellow
} else {
    Write-Host '提示：要验证候选条时执行（测完立即切回原输入法）：' -ForegroundColor DarkGray
    Write-Host ("  & `"{0}`" -s {1} shell ime set {2}" -f $AdbExe, $serial, $ImeId) -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '推送完成' -ForegroundColor Green
Write-Host '查看应用日志：'
Write-Host ("  & `"{0}`" -s {1} logcat | Select-String {2}" -f $AdbExe, $serial, $Package)
Write-Host ''