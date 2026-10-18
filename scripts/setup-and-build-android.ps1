<#
.SYNOPSIS
    EmoReply PoC —— Android 构建环境一键搭建 + APK 构建。

.DESCRIPTION
    逐项检查构建依赖（JDK / Gradle / SDK platform-tools / platform / build-tools / cmdline-tools），
    已安装的项直接跳过，缺失项优先复用本地离线包、否则从国内镜像下载，最后执行 Gradle assembleDebug。
    脚本幂等，可重复执行；同一台机器上第二次运行应当全程「已安装，跳过」。
    构建产物与全部构建缓存（依赖缓存、构建缓存、工程级缓存、Kotlin 会话数据）统一收敛在 release 目录，
    删除该目录即等于彻底清理，工程目录内不留任何构建残留。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup-and-build-android.ps1
    只搭环境不构建：  ... -SkipBuild
    强制重装全部依赖：... -Force
    换环境根目录：    ... -AndroidRoot E:\android
    换交付/缓存目录： ... -ReleaseDir E:\emoreply-release

.NOTES
    目标机：Windows 10 + PowerShell 5.1（脚本未使用 PS7 专属语法）。
#>
[CmdletBinding()]
param(
    # 环境安装根目录：其下平铺 jdk17 / gradle-8.9 / sdk / downloads
    [string]$AndroidRoot = 'D:\android',
    # 离线包目录，默认 <AndroidRoot>\downloads
    [string]$CacheDir,
    # 工程根目录，默认脚本所在目录的上一级
    [string]$ProjectRoot,
    # 交付物与构建缓存目录，默认 <ProjectRoot>\release；删除该目录即等于彻底清理
    [string]$ReleaseDir,
    # 忽略「已安装」判定，强制重新解压全部依赖
    [switch]$Force,
    # 只检查/搭建环境，不执行构建
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
# PS5 的进度条在大文件下载时逐块重绘，实测显著拖慢下载，且会污染构建日志
$ProgressPreference = 'SilentlyContinue'

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if (-not $CacheDir) { $CacheDir = Join-Path $AndroidRoot 'downloads' }
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $ProjectRoot 'release' }

$AppDir    = Join-Path $ProjectRoot 'android-app'
$Sdk       = Join-Path $AndroidRoot 'sdk'
$JdkDir    = Join-Path $AndroidRoot 'jdk17'
$JdkHome   = Join-Path $JdkDir 'jdk-17.0.20.1+1'
$GradleDir = Join-Path $AndroidRoot 'gradle-8.9'
$Staging   = Join-Path $AndroidRoot '.staging'
$AdbExe    = Join-Path $Sdk 'platform-tools\adb.exe'

# 构建相关的四类落点全部收敛到 release 之下，使「删掉 release」等价于「清理干净」：
#   gradle-home          —— 依赖缓存与构建缓存（GRADLE_USER_HOME）
#   gradle-project-cache —— 工程级 .gradle 元数据（--project-cache-dir）
#   kotlin               —— KGP 的 .kotlin 会话数据（kotlin.project.persistent.dir）
#   build                —— 编译产物（见 app/build.gradle.kts 的 buildDirectory）
$GradleHome   = Join-Path $ReleaseDir 'gradle-home'
$ProjectCache = Join-Path $ReleaseDir 'gradle-project-cache'
$KotlinDir    = Join-Path $ReleaseDir 'kotlin'
$BuiltApk     = Join-Path $ReleaseDir 'build\app\outputs\apk\debug\app-debug.apk'
# 交付物另置一份到 release 根下，避免取用时还要下钻到 build 深层目录
$ApkPath      = Join-Path $ReleaseDir 'EmoReply-poc-debug.apk'

# 镜像选择：本机实测直连 dl.google.com / repo.maven.apache.org 不稳定，
# 故统一走腾讯云与清华 TUNA；镜像路径与离线包文件名一一对应，便于人工预置包。
$SdkMirror    = 'https://mirrors.cloud.tencent.com/AndroidSDK'
$GradleMirror = 'https://mirrors.cloud.tencent.com/gradle'
$JdkMirror    = 'https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/windows'

# 依赖清单：顺序即安装顺序（Gradle 构建需要 JDK 先行）。
# Probe 一律指向组件内的可执行文件/关键产物，而不是目录——中断的解压会留下同名空目录，
# 只判目录存在会把残缺安装误判为「已装好」。
$Components = @(
    [pscustomobject]@{
        Name      = 'JDK 17 (Temurin 17.0.20.1+1)'
        Url       = "$JdkMirror/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip"
        ZipName   = 'jdk17.zip'
        ExtractTo = $JdkDir
        Inner     = 'jdk-17.0.20.1+1'
        RenameTo  = $null
        Probe     = Join-Path $JdkHome 'bin\java.exe'
    }
    [pscustomobject]@{
        Name      = 'Gradle 8.9'
        Url       = "$GradleMirror/gradle-8.9-bin.zip"
        ZipName   = 'gradle-8.9-bin.zip'
        ExtractTo = $AndroidRoot
        Inner     = 'gradle-8.9'
        RenameTo  = $null
        Probe     = Join-Path $GradleDir 'bin\gradle.bat'
    }
    [pscustomobject]@{
        Name      = 'Android SDK platform-tools (adb)'
        Url       = "$SdkMirror/platform-tools-latest-windows.zip"
        ZipName   = 'platform-tools.zip'
        ExtractTo = $Sdk
        Inner     = 'platform-tools'
        RenameTo  = $null
        Probe     = $AdbExe
    }
    [pscustomobject]@{
        Name      = 'Android SDK Platform 35 (android.jar)'
        Url       = "$SdkMirror/platform-35_r01.zip"
        ZipName   = 'platform-35.zip'
        ExtractTo = Join-Path $Sdk 'platforms'
        Inner     = 'android-35'
        RenameTo  = $null
        Probe     = Join-Path $Sdk 'platforms\android-35\android.jar'
    }
    [pscustomobject]@{
        Name      = 'Android SDK Build-Tools 34.0.0'
        Url       = "$SdkMirror/build-tools_r34-windows.zip"
        ZipName   = 'build-tools-34.zip'
        ExtractTo = Join-Path $Sdk 'build-tools'
        # 包内目录名是平台代号 android-14，而 AGP 按版本号目录 34.0.0 查找，必须改名
        Inner     = 'android-14'
        RenameTo  = '34.0.0'
        Probe     = Join-Path $Sdk 'build-tools\34.0.0\aapt2.exe'
    }
    [pscustomobject]@{
        Name      = 'Android SDK cmdline-tools (sdkmanager/avdmanager)'
        Url       = "$SdkMirror/commandlinetools-win-11076708_latest.zip"
        ZipName   = 'cmdline-tools.zip'
        ExtractTo = Join-Path $Sdk 'cmdline-tools'
        # 只有 latest 这一层布局才会被 sdkmanager 识别为当前版本
        Inner     = 'cmdline-tools'
        RenameTo  = 'latest'
        Probe     = Join-Path $Sdk 'cmdline-tools\latest\bin\sdkmanager.bat'
    }
)

function Get-OfflinePackage {
    param([string]$Url, [string]$ZipName)

    $local = Join-Path $CacheDir $ZipName
    # 离线包优先：downloads 目录里的包已在本机验证可用，命中即可完全断网搭建
    if (Test-Path $local) {
        Write-Host ("      · 复用离线包 {0}" -f $local) -ForegroundColor DarkGray
        return $local
    }

    Write-Host ("      · 下载 {0}" -f $Url) -ForegroundColor DarkGray
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    Invoke-WebRequest -Uri $Url -OutFile $local -UseBasicParsing
    return $local
}

function Expand-Package {
    param([string]$Zip, [string]$ExtractTo, [string]$Inner, [string]$RenameTo)

    New-Item -ItemType Directory -Force -Path $ExtractTo | Out-Null
    # 暂存目录刻意放在 AndroidRoot 下（与目标同盘）：Move-Item 才会走重命名，
    # 若用 $env:TEMP 则退化成跨盘整包复制，多花数分钟
    if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Staging)

    $src = Join-Path $Staging $Inner
    if (-not (Test-Path $src)) { throw "包内未找到目录 '$Inner'：$Zip" }

    $dstName = if ($RenameTo) { $RenameTo } else { $Inner }
    $dst = Join-Path $ExtractTo $dstName
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Move-Item -LiteralPath $src -Destination $dst
    Remove-Item $Staging -Recurse -Force
}

function Initialize-SdkLicenses {
    $licDir  = Join-Path $Sdk 'licenses'
    $licFile = Join-Path $licDir 'android-sdk-license'
    if (Test-Path $licFile) { return }

    New-Item -ItemType Directory -Force -Path $licDir | Out-Null
    # 缺许可文件时 AGP 会直接中断构建且提示不直观；这三行是 Google 公开的 android-sdk-license 哈希
    @(
        '24333f8a63b6825ea9c5514f83c2829b004d1fee'
        'd56f5187479451eabf01fb78af6dfcb131a6481e'
        '8933bad161af4178b1185d1a37fbf41ea5269c55'
    ) | Set-Content -Path $licFile -Encoding ASCII
}

function Initialize-LocalProperties {
    $file = Join-Path $AppDir 'local.properties'
    # Java properties 中 ':' 与 '\' 都是保留字符，SDK 路径必须逐字符转义后写入
    $escaped = $Sdk -replace '\\', '\\' -replace ':', '\:'
    $content = "sdk.dir=$escaped"

    if ((Test-Path $file) -and ((Get-Content $file -Raw).Trim() -eq $content)) { return }
    Set-Content -Path $file -Value $content -Encoding ASCII
}

function Invoke-ApkBuild {
    # JAVA_HOME 必须显式指向 JDK 17：本机 PATH 上是 TRAE 内置 JRE，版本漂移会让 AGP
    # 抛 "Unsupported class file major version"，表现为构建失败却看不出环境问题
    $env:JAVA_HOME = $JdkHome
    $env:ANDROID_HOME = $Sdk
    $env:ANDROID_SDK_ROOT = $Sdk
    # GRADLE_USER_HOME 只在本进程内覆盖：机器上的全局值供其它项目共享，改全局会波及它们；
    # 进程内指向 release，才能保证「删掉 release = 依赖与构建缓存一并清空」
    $env:GRADLE_USER_HOME = $GradleHome
    $env:Path = "$JdkHome\bin;$(Join-Path $Sdk 'platform-tools');$env:Path"

    New-Item -ItemType Directory -Force -Path $GradleHome, $ProjectCache, $KotlinDir | Out-Null

    # --no-daemon：脚本是一次性构建，不留下常驻 JVM 进程占内存。
    # 后两个参数把工程级 .gradle 与 KGP 的 .kotlin 一并赶进 release，
    # 否则它们会在 android-app 下重建，「缓存全在 release」就不成立。
    $gradleArgs = @(
        '-p', $AppDir,
        'assembleDebug',
        '--no-daemon',
        '--console=plain',
        "--project-cache-dir=$ProjectCache",
        "-Pkotlin.project.persistent.dir=$KotlinDir"
    )
    & (Join-Path $GradleDir 'bin\gradle.bat') @gradleArgs
    if ($LASTEXITCODE -ne 0) { throw "Gradle 构建失败（exit $LASTEXITCODE）" }
}

function Remove-LegacyCaches {
    # 早先的构建把缓存写在工程内；改到 release 后这些目录不再生成，但历史残留会让
    # 「缓存全在 release」不成立，故构建前清掉。幂等：无残留时直接跳过。
    foreach ($d in @(
        (Join-Path $AppDir '.gradle'),
        (Join-Path $AppDir '.kotlin'),
        (Join-Path $AppDir 'app\build')
    )) {
        if (Test-Path $d) {
            Remove-Item -LiteralPath $d -Recurse -Force
            Write-Host ("      · 清理工程内遗留缓存 {0}" -f $d) -ForegroundColor DarkGray
        }
    }
}

Write-Host ''
Write-Host 'EmoReply PoC · Android 构建环境检查' -ForegroundColor Cyan
Write-Host ("  环境根目录：{0}" -f $AndroidRoot)
Write-Host ("  工程目录  ：{0}" -f $AppDir)
Write-Host ("  离线包目录：{0}" -f $CacheDir)
Write-Host ("  交付/缓存 ：{0}" -f $ReleaseDir)
Write-Host ''

Add-Type -AssemblyName System.IO.Compression.FileSystem

$idx = 0
foreach ($c in $Components) {
    $idx++
    Write-Host ("[{0}/{1}] {2}" -f $idx, $Components.Count, $c.Name)

    if ((-not $Force) -and (Test-Path $c.Probe)) {
        Write-Host '      · 已安装，跳过' -ForegroundColor DarkGray
        continue
    }

    $zip = Get-OfflinePackage -Url $c.Url -ZipName $c.ZipName
    Expand-Package -Zip $zip -ExtractTo $c.ExtractTo -Inner $c.Inner -RenameTo $c.RenameTo

    if (-not (Test-Path $c.Probe)) { throw ("{0} 安装后校验失败，未找到 {1}" -f $c.Name, $c.Probe) }
    Write-Host '      · 完成' -ForegroundColor Green
}

Write-Host ''
# 编号取组件数 +1，增删依赖项时无需同步改这里
Write-Host ('[{0}/{1}] SDK 许可 / 工程 sdk.dir 配置' -f ($Components.Count + 1), ($Components.Count + 1))
Initialize-SdkLicenses
Initialize-LocalProperties
Write-Host '      · 完成' -ForegroundColor Green

if ($SkipBuild) {
    Write-Host ''
    Write-Host '已指定 -SkipBuild，跳过构建。' -ForegroundColor Yellow
    exit 0
}

Write-Host ''
Write-Host '构建 debug APK ...' -ForegroundColor Cyan
Remove-LegacyCaches
Invoke-ApkBuild

if (-not (Test-Path $BuiltApk)) { throw "构建结束但未找到 APK：$BuiltApk" }
Copy-Item -LiteralPath $BuiltApk -Destination $ApkPath -Force
$apk = Get-Item $ApkPath

Write-Host ''
Write-Host '构建成功' -ForegroundColor Green
Write-Host ("  交付物  ：{0}" -f $apk.FullName)
Write-Host ("  大小    ：{0:N2} MB" -f ($apk.Length / 1MB))
Write-Host ("  时间    ：{0}" -f $apk.LastWriteTime)
Write-Host ("  构建缓存：{0}" -f $ReleaseDir)
Write-Host ''
Write-Host '安装到真机（需先确认 adb devices 已授权）：'
Write-Host ("  & `"{0}`" install -r `"{1}`"" -f $AdbExe, $apk.FullName)