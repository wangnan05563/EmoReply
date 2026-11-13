#Requires -Version 5.1
<#
.SYNOPSIS
    Remove build artifacts under the release directory before a build.
.DESCRIPTION
    Scope selects which artifacts are removed:
      desktop : release\desktop-installer, release\desktop, legacy desktop-app\dist
      android : release\build\app\outputs, release\EmoReply-poc-debug.apk
      all     : both groups (used by the one-click build + publish pipeline)
    Toolchains and caches (release\toolchain, npm-cache, gradle-home,
    gradle-project-cache, kotlin, downloads) are preserved by default:
    they are expensive to rebuild and are NOT build artifacts.
    -PurgeCaches removes those cache directories as well.
    -PurgeAll removes the whole release directory; the build scripts recreate
    it from scratch, so deleting release by hand is always safe.
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
        $targets += (Join-Path $relFull 'desktop-installer')
        $targets += (Join-Path $relFull 'desktop')
        $targets += (Join-Path $rootFull 'desktop-app\dist')
    }
    if ($Scope -in @('android', 'all')) {
        $targets += (Join-Path $relFull 'build\app\outputs')
        $targets += (Join-Path $relFull 'EmoReply-poc-debug.apk')
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
# large .asar) must not abort the whole one-click pipeline. Retry a few times, and
# if an item is still held, skip it (warn) so the build step can overwrite it.
function Remove-Resilient {
    param([string]$Path, [int]$Attempts = 4)
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            if ([System.IO.Directory]::Exists($Path)) {
                # Remove children individually so one locked child can't abort the batch.
                foreach ($c in [System.IO.Directory]::GetFileSystemEntries($Path)) {
                    Remove-Resilient -Path $c -Attempts $Attempts
                }
                [System.IO.Directory]::Delete($Path, $false)
            } else {
                [System.IO.File]::Delete($Path)
            }
            return $true
        } catch {
            if ($i -lt $Attempts) { Start-Sleep -Milliseconds 700 }
        }
    }
    Write-Warning ("[clean-release] SKIP (still in use after retries): " + $Path)
    return $false
}

# Move a target aside (rename) so the canonical path is freed for the next build
# even if a child file is held by another process (AV scanner, lingering
# node/electron). electron-builder's EnsureEmptyDir would otherwise fail on a
# still-locked file; renaming the PARENT directory sidesteps that because the
# lock is on a file handle (path-independent), not the directory entry. The
# moved-aside dir is then cleaned best-effort (locked items skipped, never
# fatal). If rename is unavailable we fall back to in-place resilient delete.
function Move-Aside-Resilient {
    param([string]$Path, [int]$Attempts = 4)
    for ($i = 1; $i -le $Attempts; $i++) {
        $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID
        $trash = $Path + '.trash-' + $stamp
        try {
            Move-Item -LiteralPath $Path -Destination $trash -Force -ErrorAction Stop
            Remove-Resilient -Path $trash -Attempts 2 | Out-Null
            return $true
        } catch {
            if ($i -lt $Attempts) { Start-Sleep -Milliseconds 700 }
        }
    }
    return $false
}

$removed = 0
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
