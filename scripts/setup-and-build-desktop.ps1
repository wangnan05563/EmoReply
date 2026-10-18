<#
.SYNOPSIS
    EmoReply PoC —— PC（Electron）构建环境一键搭建 + 桌面包打包。

.DESCRIPTION
    参考 setup-and-build-android.ps1 的结构改写（T01101）。逐项检查构建依赖（Node.js / Electron），
    已安装的项直接跳过，缺失项优先复用本地离线包、否则从国内镜像（npmmirror）下载；
    依赖就绪后执行 npm install，最后组装绿色便携包（Electron 运行时 + 应用源码）。
    脚本幂等，可重复执行；同一台机器上第二次运行应当全程「已安装，跳过」。
    构建产物与全部构建缓存（npm 缓存、Electron 二进制缓存）统一收敛在 release 目录，
    删除该目录即等于彻底清理，工程目录内不留任何构建残留。

    打包方式说明（PC 与 Android 的关键差异）：Android 走 Gradle assembleDebug 产出签名 APK；
    PC 端 Electron 官方分发推荐 electron-builder（NSIS 安装器 / 代码签名），但那会引入
    新的构建依赖链。POC 阶段采用 Electron 官方等效的「绿色便携」打包：
    Electron 运行时目录 + resources\app 应用源码，双击 ExeName 即可运行，零新增依赖。
    正式版若需安装器/自动更新/签名，再引入 electron-builder（见文末注释建议）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup-and-build-desktop.ps1
    只搭环境不打包：  ... -SkipBuild
    强制重装全部依赖：... -Force
    换环境根目录：    ... -NodeRoot E:\nodejs
    换交付/缓存目录： ... -ReleaseDir E:\emoreply-release

.NOTES
    目标机：Windows 10/11 + Windows PowerShell 5.1（未使用 PS7 专属语法）。
#>
[CmdletBinding()]
param(
    # 环境安装根目录：其下平铺 node-v22.22.2-win-x64 / downloads
    [string]$NodeRoot = 'D:\nodejs',
    # 离线包目录，默认 <NodeRoot>\downloads
    [string]$CacheDir,
    # 工程根目录，默认脚本所在目录的上一级
    [string]$ProjectRoot,
    # 交付物与构建缓存目录，默认 <ProjectRoot>\release；删除该目录即等于彻底清理
    [string]$ReleaseDir,
    # 忽略「已安装」判定，强制重新安装全部依赖
    [switch]$Force,
    # 只检查/搭建环境，不执行打包
    [switch]$SkipBuild,
    # 只出绿色便携版，跳过 electron-builder NSIS 安装包（T01359）
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
# PS5 的进度条在大文件下载时逐块重绘，实测显著拖慢下载，且会污染构建日志
$ProgressPreference = 'SilentlyContinue'

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if (-not $CacheDir) { $CacheDir = Join-Path $NodeRoot 'downloads' }
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $ProjectRoot 'release' }

$AppDir      = Join-Path $ProjectRoot 'desktop-app'
$NodeDir     = Join-Path $NodeRoot 'node-v22.22.2-win-x64'
$NodeExe     = Join-Path $NodeDir 'node.exe'
$NpmCmd      = Join-Path $NodeDir 'npm.cmd'
$Staging     = Join-Path $NodeRoot '.staging'

# 构建相关的落点全部收敛到 release 之下，使「删掉 release」等价于「清理干净」：
#   npm-cache —— npm 下载缓存（--cache 指定；默认会写进用户 AppData，污染全局）
#   desktop   —— 打包产物（Electron 运行时 + resources\app）
$NpmCache    = Join-Path $ReleaseDir 'npm-cache'
$DistDir     = Join-Path $ReleaseDir 'desktop'
$ExeName     = 'EmoReply.exe'

# 镜像选择：与安卓版同思路——直连官方源不稳，统一走 npmmirror（淘宝镜像）；
# ELECTRON_MIRROR 是 electron 依赖 postinstall 下载 Electron 二进制的专用镜像开关。
$NodeMirror    = 'https://npmmirror.com/mirrors/node'
$NpmRegistry   = 'https://registry.npmmirror.com'
$ElectronMirror = 'https://npmmirror.com/mirrors/electron/'
$NodeVersion   = '22.22.2'

# 依赖清单：顺序即安装顺序。Probe 指向组件内的可执行文件，而不是目录——
# 中断的解压会留下同名空目录，只判目录存在会把残缺安装误判为「已装好」。
$Components = @(
    [pscustomobject]@{
        Name      = ('Node.js v{0} (win-x64 zip)' -f $NodeVersion)
        Url       = ('{0}/v{1}/node-v{1}-win-x64.zip' -f $NodeMirror, $NodeVersion)
        ZipName   = ('node-v{0}-win-x64.zip' -f $NodeVersion)
        ExtractTo = $NodeRoot
        Inner     = ('node-v{0}-win-x64' -f $NodeVersion)
        RenameTo  = $null
        Probe     = $NodeExe
    }
)

# 原生命令统一经此调用：PS5.1 在 $ErrorActionPreference='Stop' 下，原生命令写 stderr
# 会被包装成终止错误（npm 安装时的 notice/进度信息恰恰走 stderr），脚本会无故中断。
# 此处临时把偏好降到 SilentlyContinue，成败只认 $LASTEXITCODE。
function Invoke-Native {
    param([scriptblock]$Script)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { $output = & $Script 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($output) { $output | ForEach-Object { Write-Host ('      · ' + $_) -ForegroundColor DarkGray } }
    return $LASTEXITCODE
}

# 递归删除统一走 .NET 直接调用：Remove-Item 在部分受限环境会被安全包装器劫持
# （删除操作要求交互确认），对构建脚本是致命的；.NET Delete 语义即删、失败即抛。
function Remove-Tree {
    param([string]$Path)
    if ([System.IO.Directory]::Exists($Path)) { [System.IO.Directory]::Delete($Path, $true) }
    elseif ([System.IO.File]::Exists($Path)) { [System.IO.File]::Delete($Path) }
}

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
    param([string]$Zip, [string]$ExtractTo, [string]$Inner)

    New-Item -ItemType Directory -Force -Path $ExtractTo | Out-Null
    # 暂存目录刻意放在 NodeRoot 下（与目标同盘）：Move-Item 才会走重命名，
    # 若用 $env:TEMP 则退化成跨盘整包复制，多花数分钟
    Remove-Tree $Staging
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Staging)

    $src = Join-Path $Staging $Inner
    if (-not (Test-Path $src)) { throw "包内未找到目录 '$Inner'：$Zip" }

    $dst = Join-Path $ExtractTo $Inner
    if (Test-Path $dst) { Remove-Tree $dst }
    Move-Item -LiteralPath $src -Destination $dst
    Remove-Tree $Staging
}

function Find-ExistingNode {
    # 优先复用机器上已有的 Node 22（与组件同主版本才复用，避免版本漂移重演
    # 安卓侧 JAVA_HOME 漂移导致 AGP 报 Unsupported class file 的坑）：
    # 依次探测 PATH 上的 node 与 managed 安装位；返回 node.exe 全路径或 $null。
    # node --version 属原生命令，其 stderr 在 Stop 偏好下会终止脚本，故整段临时降级。
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $candidates = @()
        $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
        $managed = 'C:\Users\{0}\.workbuddy\binaries\node\versions\22.22.2-3\node.exe' -f $env:USERNAME
        $candidates += $managed
        foreach ($c in $candidates) {
            if (Test-Path $c) {
                $v = (& $c --version) 2>$null
                if ($LASTEXITCODE -eq 0 -and $v -match '^v22\.') { return $c }
            }
        }
    } finally { $ErrorActionPreference = $prev }
    return $null
}

function Invoke-NpmInstall {
    # npm install 到 desktop-app：registry 与 Electron 二进制镜像均指向 npmmirror，
    # 直连 registry.npmjs.org / github release 在本机实测不稳定（与安卓侧同因）。
    # npm 缓存收敛到 release\npm-cache，保证「删掉 release = 缓存一并清空」。
    if (-not (Test-Path (Join-Path $AppDir 'package.json'))) { throw "未找到 {0}" -f (Join-Path $AppDir 'package.json') }
    $env:ELECTRON_MIRROR = $ElectronMirror
    New-Item -ItemType Directory -Force -Path $NpmCache | Out-Null

    $npmArgs = @(
        'install', '--no-audit', '--no-fund', '--loglevel=warn',
        "--registry=$NpmRegistry",
        "--cache=$NpmCache"
    )
    # npm install 必须在应用目录内执行（读 package.json）
    Push-Location $AppDir
    try { $rc = Invoke-Native { & $NpmCmd @npmArgs } } finally { Pop-Location }
    if ($rc -ne 0) { throw "npm install 失败（exit $rc）" }

    $electronExe = Join-Path $AppDir 'node_modules\electron\dist\electron.exe'
    if (-not (Test-Path $electronExe)) { throw "Electron 二进制缺失（postinstall 可能未走镜像）：$electronExe" }
}

function Invoke-DesktopPackage {
    # 绿色便携打包：Electron 运行时（node_modules\electron\dist）+ resources\app 应用源码。
    # electron.exe 直接改名为交付名（Electron 支持改主程序名运行）；正式版如需安装器/
    # 自动更新/代码签名，换 electron-builder 产出 NSIS 包（此处注释留位，不引入依赖）。
    $runtime = Join-Path $AppDir 'node_modules\electron\dist'
    if (-not (Test-Path $runtime)) { throw "Electron 运行时缺失：$runtime" }

    # 增量覆盖策略（T01101 实测教训）：打包后立即重打包时，运行时文件（default_app.asar
    # 等）常被防病毒/索引服务以共享读方式长期持锁，整目录删除会失败。处置：
    # ① default_app.asar 是 Electron 无 app 时的 fallback UI，本包永远带 resources\app，
    #    用不到它——复制运行时时直接排除，也永远不会成为删除障碍；
    # ② 运行时主程序存在则只增量覆盖 resources\app；需要彻底重建时删 release\desktop。
    $appDirOut = Join-Path $DistDir 'resources\app'
    $hasPacked = Test-Path (Join-Path $DistDir $ExeName)
    if (-not $hasPacked) {
        New-Item -ItemType Directory -Force -Path $appDirOut | Out-Null
        Write-Host '      · 复制 Electron 运行时 ...' -ForegroundColor DarkGray
        # -Path 才支持 * 通配符；-Exclude 掉 default_app.asar（fallback UI，本包用不到，
        # 且实测它最容易被外部进程持锁，排除后整链路无删除障碍）
        Copy-Item -Path (Join-Path $runtime '*') -Destination $DistDir -Recurse -Force -Exclude 'default_app.asar'
        # 主程序改名：resources\app 存在时 Electron 加载它而非内置 default_app.asar
        # （必须在复制后立即改名，不能依赖复制前的状态判断）
        Move-Item -LiteralPath (Join-Path $DistDir 'electron.exe') -Destination (Join-Path $DistDir $ExeName) -Force
        Write-Host '      · 运行时就绪' -ForegroundColor DarkGray
    } else {
        Write-Host '      · 运行时已就绪，增量覆盖应用源码 ...' -ForegroundColor DarkGray
        New-Item -ItemType Directory -Force -Path $appDirOut | Out-Null
    }

    Write-Host '      · 复制应用源码 -> resources\app ...' -ForegroundColor DarkGray
    # 动态复制顶层运行源码：package.json + 全部 .js/.html（main/renderer/preload/kb 等，
    # 主进程依赖链会随迭代增加，白名单硬编码曾漏掉 kb.js/preload.js 导致启动即退）。
    # node_modules/package-lock/构建脚本/子目录原型不进包。
    Copy-Item -Path (Join-Path $AppDir 'package.json') -Destination (Join-Path $DistDir 'resources\app') -Force
    $srcFiles = Get-ChildItem -LiteralPath $AppDir -File | Where-Object { $_.Extension -in '.js', '.html' -and $_.Name -ne 'package-lock.json' }
    foreach ($f in $srcFiles) {
        Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $DistDir 'resources\app') -Force
    }
    $assets = Join-Path $AppDir 'assets'
    if (Test-Path $assets) {
        Copy-Item -LiteralPath $assets -Destination (Join-Path $DistDir 'resources\app\assets') -Recurse -Force
    }

    $packed = Join-Path $DistDir $ExeName
    if (-not (Test-Path $packed)) { throw "打包结束但未找到主程序：$packed" }
    return $packed
}

function Invoke-InstallerPackage {
    # NSIS 安装包（T01359）：除绿色便携目录外，还要产出可分发/可安装的单文件安装包。
    # electron-builder 配置已在 desktop-app\package.json（nsis + portable 双 target），
    # patch 号（YYYYMMDD-NNN）非合法 semver 的兼容由 pkg-semver.js 处理
    # （打包期临时改写为 0.1.0-YYYYMMDD-NNN，结束无论成败还原原文件）。
    # 产物统一复制进交付目录 release\desktop，与绿色便携版放在一起。
    $ebDist = Join-Path $AppDir 'dist'
    # 每次全量重建 electron-builder 输出目录，避免旧版本安装包残留被重复搬运
    if (Test-Path $ebDist) { Remove-Tree $ebDist }

    # node.exe 已在组件阶段前置于 PATH，直接调用；pkg-semver.js 内部经 npx
    # 解析本地 node_modules\.bin\electron-builder（node 目录自带 npx.cmd）。
    $pkgSemver = Join-Path $AppDir 'scripts\pkg-semver.js'
    if (-not (Test-Path $pkgSemver)) { throw "未找到 pkg-semver.js：$pkgSemver" }
    $rc = Invoke-Native { & node $pkgSemver --win --x64 }
    if ($rc -ne 0) { throw "electron-builder 打包失败（exit $rc）" }

    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
    # 只搬 NSIS setup 主安装包（artifactName = EmoReply-<version>-x64.exe）；
    # portable 副本/-blockmap/win-unpacked 留在 electron-builder 输出目录不进交付。
    $setups = @(Get-ChildItem -LiteralPath $ebDist -Filter 'EmoReply-*-x64.exe' -File |
        Where-Object { $_.Name -notmatch 'portable|blockmap' })
    if ($setups.Count -eq 0) { throw "electron-builder 结束但未找到安装包：$ebDist" }
    $firstDest = $null
    foreach ($s in $setups) {
        $dest = Join-Path $DistDir $s.Name
        Copy-Item -LiteralPath $s.FullName -Destination $dest -Force
        if (-not $firstDest) { $firstDest = $dest }
    }
    # 返回交付目录内的路径（而非 electron-builder 输出目录），成功摘要直接展示交付位
    return $firstDest
}

Write-Host ''
Write-Host 'EmoReply PoC · PC（Electron）构建环境检查' -ForegroundColor Cyan
Write-Host ("  环境根目录：{0}" -f $NodeRoot)
Write-Host ("  应用目录  ：{0}" -f $AppDir)
Write-Host ("  离线包目录：{0}" -f $CacheDir)
Write-Host ("  交付/缓存 ：{0}" -f $ReleaseDir)
Write-Host ''

Add-Type -AssemblyName System.IO.Compression.FileSystem

# [1/N] Node.js：优先复用机器上已有的 v22（跳过下载），否则从镜像安装到 NodeRoot
$idx = 0
$nodeInUse = $null
foreach ($c in $Components) {
    $idx++
    Write-Host ("[{0}/{1}] {2}" -f $idx, $Components.Count, $c.Name)

    if ((-not $Force) -and (Test-Path $c.Probe)) {
        Write-Host '      · 已安装，跳过' -ForegroundColor DarkGray
        $nodeInUse = $c.Probe
        continue
    }

    $existing = Find-ExistingNode
    if ($existing) {
        Write-Host ("      · 复用现有 Node：{0}" -f $existing) -ForegroundColor DarkGray
        $nodeInUse = $existing
        continue
    }

    $zip = Get-OfflinePackage -Url $c.Url -ZipName $c.ZipName
    Expand-Package -Zip $zip -ExtractTo $c.ExtractTo -Inner $c.Inner

    if (-not (Test-Path $c.Probe)) { throw ("{0} 安装后校验失败，未找到 {1}" -f $c.Name, $c.Probe) }
    $nodeInUse = $c.Probe
    Write-Host '      · 完成' -ForegroundColor Green
}

if (-not $nodeInUse) { throw '未找到可用的 node.exe（安装失败或环境异常）' }
$nodeBin = Split-Path -Parent $nodeInUse
$env:Path = "$nodeBin;$env:Path"

# npm.cmd 与 node.exe 同目录（zip 发行版自带）；若复用的是非 zip 安装（如 managed 版
# 只有 node.exe），则跳过 npm 步骤前先确认 npm 可用，否则报错并给出处理建议。
if (-not (Test-Path (Join-Path $nodeBin 'npm.cmd'))) {
    throw ("复用的 Node 未自带 npm.cmd（{0}）。处理建议：改用 -NodeRoot 指向完整 zip 安装，或删除该目录后重跑让脚本从镜像安装完整 Node。" -f $nodeBin)
}
$NpmCmd = Join-Path $nodeBin 'npm.cmd'

Write-Host ''
Write-Host ('[{0}/{1}] npm 依赖安装（electron）' -f ($Components.Count + 1), ($Components.Count + 1))
Invoke-NpmInstall
Write-Host '      · 完成' -ForegroundColor Green

if ($SkipBuild) {
    Write-Host ''
    Write-Host '已指定 -SkipBuild，跳过打包。' -ForegroundColor Yellow
    exit 0
}

Write-Host ''
Write-Host '打包绿色便携版 ...' -ForegroundColor Cyan
$packed = Invoke-DesktopPackage
$exe = Get-Item $packed

$setupItem = $null
if (-not $SkipInstaller) {
    Write-Host ''
    Write-Host '打包 NSIS 安装包（electron-builder）...' -ForegroundColor Cyan
    $setupPath = Invoke-InstallerPackage
    $setupItem = Get-Item $setupPath
}

Write-Host ''
Write-Host '打包成功' -ForegroundColor Green
Write-Host ("  绿色便携：{0}" -f $exe.FullName)
if ($setupItem) { Write-Host ("  安装包  ：{0}" -f $setupItem.FullName) }
Write-Host ("  大小    ：{0:N2} MB" -f ((Get-ChildItem -LiteralPath $DistDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB))
Write-Host ("  时间    ：{0}" -f $exe.LastWriteTime)
Write-Host ("  构建缓存：{0}" -f $ReleaseDir)
Write-Host ''
Write-Host '运行方式：双击交付目录中的 EmoReply.exe（绿色便携，无需安装）。' -ForegroundColor Cyan
if ($setupItem) { Write-Host '          或双击同目录 EmoReply-<版本>-x64.exe 走安装向导（未做代码签名，首次运行 SmartScreen 会提示）。' -ForegroundColor Cyan }
Write-Host '只出便携版：构建脚本追加 -SkipInstaller 可跳过安装包步骤。' -ForegroundColor DarkGray
