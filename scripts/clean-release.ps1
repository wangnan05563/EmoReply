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

$removed = 0
foreach ($p in $doomed) {
    if ($PSCmdlet.ShouldProcess($p, 'Delete')) {
        if ([System.IO.Directory]::Exists($p)) {
            [System.IO.Directory]::Delete($p, $true)
        } else {
            [System.IO.File]::Delete($p)
        }
        $removed++
        Write-Host ("[clean-release] removed: " + $p) -ForegroundColor DarkGray
    }
}
Write-Host ("[clean-release] scope={0} removed={1}/{2} kept=caches/toolchain" -f $Scope, $removed, $doomed.Count) -ForegroundColor Green
