#Requires -Version 5.1
<#
.SYNOPSIS
    Remove build artifacts under the release directory before a build.
.DESCRIPTION
    Scope selects which artifacts are removed:
      desktop : release\desktop-installer* (含临时唯一目录), release\desktop, 历史 desktop-app\dist
      android : release\build\app\outputs, release\EmoReply-<版本>-poc-debug.apk（旧名一并清理），
                以及工程内历史缓存 android-app\app\build / .gradle / .kotlin
      all     : both groups + Python 本地缓存（m1-core 的 __pycache__ / .pytest_cache）
    Toolchains and per-project caches still under release (release\toolchain,
    gradle-project-cache, kotlin, downloads) are preserved by default: they are
    expensive to rebuild and are NOT build artifacts. Shared Gradle/npm caches
    live outside release (GRADLE_USER_HOME / npm_config_cache env vars, e.g.
    D:\code\Data_Gradle / D:\code\Data_npm\npm-cache) and are never touched here.
    -PurgeCaches removes the release-local cache directories as well.
    -PurgeAll removes the whole release directory; the build scripts recreate
    it from scratch, so deleting release by hand is always safe (shared caches
    outside release are unaffected).
    Every target is verified to live inside the project root before deletion.
    All paths are derived from the project root; no absolute path is hardcoded.
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\clean-release.ps1 -Scope all -WhatIf
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\clean-release.ps1 -Scope desktop
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ProjectRoot,
    [string]$ReleaseDir,
    [ValidateSet('desktop', 'android', 'all')][string]$Scope = 'all',
    [switch]$PurgeCaches,
    [switch]$PurgeAll
)

$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $ProjectRoot 'release' }

$rootFull = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd([char[]]@('\', '/'))
$relFull = [System.IO.Path]::GetFullPath($ReleaseDir).TrimEnd([char[]]@('\', '/'))
$rootPrefix = $rootFull + [System.IO.Path]::DirectorySeparatorChar

$targets = @()
if ($PurgeAll) {
    $targets += $relFull
} else {
    if ($Scope -in @('desktop', 'all')) {
        # 注意：desktop-installer 系列（含 desktop-installer / desktop-installer.build /
        # desktop-installer-<时间戳>-<PID>）统一在下方 desktop-installer* 循环里 best-effort 处理，
        # 不在此显式列出，避免与循环重复处理。
        $targets += (Join-Path $relFull 'desktop')
        $targets += (Join-Path $rootFull 'desktop-app\dist')
    }
    if ($Scope -in @('android', 'all')) {
        $targets += (Join-Path $relFull 'build\app\outputs')
        # 交付物名带版本号（EmoReply-<版本>-poc-debug.apk），按模式清理所有旧版本
        foreach ($f in Get-ChildItem -LiteralPath $relFull -Filter 'EmoReply-*-poc-debug.apk' -File -ErrorAction SilentlyContinue) {
            $targets += $f.FullName
        }
        # 兼容旧的无版本号命名（历史残留，幂等）
        $targets += (Join-Path $relFull 'EmoReply-poc-debug.apk')
        # 工程内历史缓存：早先版本的 Gradle/Kotlin 缓存曾写在 android-app 下，
        # 现在统一由构建参数赶进 release，这里只清理可能的历史残留（可再生，非交付物）
        $targets += (Join-Path $rootFull 'android-app\app\build')
        $targets += (Join-Path $rootFull 'android-app\.gradle')
        $targets += (Join-Path $rootFull 'android-app\.kotlin')
    }
    if ($Scope -eq 'all') {
        # Python 本地缓存：跑 pytest / 评测脚本时生成，可再生成，不属于交付物
        $targets += (Join-Path $rootFull 'm1-core\.pytest_cache')
        foreach ($d in Get-ChildItem -LiteralPath (Join-Path $rootFull 'm1-core') -Directory -Recurse -Filter '__pycache__' -ErrorAction SilentlyContinue) {
            $targets += $d.FullName
        }
        # iOS SwiftPM 构建中间物（swift build/test 默认会写 .build；README 已改为 --scratch-path 指向 release）
        $targets += (Join-Path $rootFull 'ios-app\.build')
    }
    if ($PurgeCaches) {
        foreach ($n in @('toolchain', 'npm-cache', 'gradle-home', 'gradle-project-cache', 'kotlin', 'downloads')) {
            $targets += (Join-Path $relFull $n)
        }
    }
    # A build must never start from a missing release root.
    if (-not (Test-Path -LiteralPath $relFull)) {
        New-Item -ItemType Directory -Path $relFull -Force | Out-Null
        Write-Host ("[clean-release] created: " + $relFull) -ForegroundColor DarkGray
    }
}

# Resolve first, delete second: nothing is removed until every path passed the guard.
$doomed = @()
foreach ($t in $targets) {
    $full = [System.IO.Path]::GetFullPath($t)
    if (-not $full.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ("Refuse to delete outside project root: " + $full)
    }
    if (Test-Path -LiteralPath $full) { $doomed += $full }
}

# Resilient delete: a single in-use item (e.g. an AV scanner briefly locking a
# large .asar) must not abort the whole one-click pipeline. Delete best-effort and
# skip (warn) anything still held — WITHOUT sleep-retry: a permanently held file
# (Defender real-time scan on app.asar) will never clear by waiting, and retrying
# used to stall the build for minutes (T05736). Freed paths are handled by the
# move-aside path; skipped items are cleaned on a later run when the lock lifts.
function Remove-Resilient {
    param([string]$Path)
    try {
        if ([System.IO.Directory]::Exists($Path)) {
            # Remove children individually so one locked child can't abort the batch.
            foreach ($c in [System.IO.Directory]::GetFileSystemEntries($Path)) {
                Remove-Resilient -Path $c
            }
            [System.IO.Directory]::Delete($Path, $false)
        } else {
            [System.IO.File]::Delete($Path)
        }
        return $true
    } catch {
        Write-Warning ("[clean-release] SKIP (in use): " + $Path)
        return $false
    }
}

# Move a target aside (rename) so the canonical path is freed for the next build
# even if a child file is held by another process (AV scanner, lingering
# node/electron). electron-builder's EnsureEmptyDir would otherwise fail on a
# still-locked file; renaming the PARENT directory sidesteps that because the
# lock is on a file handle (path-independent), not the directory entry. The
# moved-aside dir is then cleaned best-effort (locked items skipped, never
# fatal). If rename is unavailable we fall back to in-place resilient delete.
function Move-Aside-Resilient {
    param([string]$Path, [int]$Attempts = 8)
    for ($i = 1; $i -le $Attempts; $i++) {
        $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
        $trash = $Path + '.trash-' + $stamp
        try {
            Move-Item -LiteralPath $Path -Destination $trash -Force -ErrorAction Stop
            Remove-Resilient -Path $trash | Out-Null
            return $true
        } catch {
            if ($i -lt $Attempts) { Start-Sleep -Milliseconds 1000 }
        }
    }
    return $false
}

# Best-effort cleanup of stale *.trash-* dirs left by prior move-aside when a
# child file (e.g. app.asar) was still held by AV/indexer. Non-fatal: locked
# items are skipped, so a dir may linger until the handle is released and a
# later run finishes the job. Keeps the release dir from accumulating orphans.
function Clear-StaleTrash {
    param([string]$Root)
    foreach ($t in Get-ChildItem -LiteralPath $Root -Directory -Filter '*.trash-*' -ErrorAction SilentlyContinue) {
        Remove-Resilient -Path $t.FullName | Out-Null
    }
}

$removed = 0
# Clean stale *.trash-* leftovers and ALL desktop-installer* dirs (canonical
# desktop-installer / electron-builder 临时 desktop-installer.build / 每轮唯一输出
# desktop-installer-<时间戳>-<PID>）from previous runs first (best-effort). 若某子文件
# （如 app.asar）仍被防病毒/索引服务持锁，挪开/跳过即可，绝不致命；锁释放后由后续运行收尾。
Clear-StaleTrash -Root $relFull
foreach ($d in Get-ChildItem -LiteralPath $relFull -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'desktop-installer*' }) {
    if (-not (Move-Aside-Resilient -Path $d.FullName)) { Remove-Resilient -Path $d.FullName }
}
foreach ($p in $doomed) {
    if ($PSCmdlet.ShouldProcess($p, 'Clean')) {
        if ([System.IO.Directory]::Exists($p)) {
            # Prefer move-aside: frees the canonical path even under a lock.
            $ok = Move-Aside-Resilient -Path $p
            if (-not $ok) {
                # Fallback: delete in place, skipping anything still in use.
                Remove-Resilient -Path $p
            }
        } else {
            Remove-Resilient -Path $p
        }
        if (-not (Test-Path -LiteralPath $p)) {
            $removed++
            Write-Host ("[clean-release] cleaned: " + $p) -ForegroundColor DarkGray
        } else {
            Write-Host ("[clean-release] partially cleaned (some items in use, build will overwrite): " + $p) -ForegroundColor Yellow
        }
    }
}
Write-Host ("[clean-release] scope={0} cleaned={1}/{2} kept=caches/toolchain" -f $Scope, $removed, $doomed.Count) -ForegroundColor Green
