# 共享路径解析：工程内路径一律相对脚本位置推导，机器级覆盖走环境变量，不写死盘符。
# 用法（各脚本顶部）： . (Join-Path $PSScriptRoot 'common-paths.ps1')

function Get-ProjectRoot {
    param([string]$ScriptRoot)
    return (Split-Path -Parent $ScriptRoot)
}

# android-app/local.properties 是工程内唯一的 SDK 位置事实源（已 gitignore，随机器走）
function Get-AndroidSdkFromLocalProperties {
    param([string]$ProjectRoot)
    $lp = Join-Path (Join-Path $ProjectRoot 'android-app') 'local.properties'
    if (-not (Test-Path $lp)) { return $null }
    $line = Get-Content -Path $lp -Encoding UTF8 |
        Where-Object { $_ -match '^\s*sdk\.dir\s*=' } |
        Select-Object -First 1
    if (-not $line) { return $null }
    # .properties 转义：\\ -> \ ，\: -> :
    return ((($line -split '=', 2)[1]).Trim() -replace '\\\\', '\' -replace '\\:', ':')
}

# Android 环境根目录：其下平铺 sdk / jdk17 / gradle-8.9
function Resolve-AndroidRoot {
    param([string]$ProjectRoot, [string]$Override)
    if ($Override) { return $Override }
    if ($env:ANDROID_ROOT) { return $env:ANDROID_ROOT }
    $sdk = Get-AndroidSdkFromLocalProperties $ProjectRoot
    if ($sdk) { return (Split-Path -Parent $sdk) }
    foreach ($v in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
        if ($v) { return (Split-Path -Parent $v) }
    }
    throw ("无法确定 Android 环境根目录：请设置 ANDROID_ROOT，或用 -AndroidRoot 指定（其下应有 sdk\platform-tools\adb.exe）。工程根：{0}" -f $ProjectRoot)
}

# Node 环境根目录：机器级覆盖优先；否则落在工程的 release 目录下（与 Android 产物同约定，已 gitignore）
function Resolve-NodeRoot {
    param([string]$ProjectRoot, [string]$Override)
    if ($Override) { return $Override }
    if ($env:NODE_ROOT) { return $env:NODE_ROOT }
    return (Join-Path $ProjectRoot 'release\toolchain\node')
}
