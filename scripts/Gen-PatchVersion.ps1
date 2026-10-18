<#
.SYNOPSIS
    Generate a unique patch number (yyyyMMdd-NNN) and write it into package.json version.

.DESCRIPTION
    Runs BEFORE packing/build so every build gets a unique patch version:
      - date part  : 8 digits, yyyyMMdd (no separators)
      - sequence   : starts at 1 within one day, increments per run, resets to 1
                     when the day changes (persisted in .build/patch-seq.json)
      - version    : written back as "yyyyMMdd-NNN" (for example 20260927-001)

    T01335: also bumps the Android versionCode in app/build.gradle.kts.
    UpdateManager compares manifest versionCode against the installed
    longVersionCode (remote > local), so a hardcoded versionCode=1 makes the
    update check never fire. Android versionCode must be an Int32, so it is
    derived as (days since 2020-01-01) * 100 + seq -- small, strictly
    increasing, and safe for a single Int (246301 on 2026-09-29).
    If the file already holds a HIGHER versionCode, that value is kept and
    incremented by 1 instead (never let it go backwards).

    The write keeps the rest of package.json untouched (only the "version"
    field value is replaced), then reads the file back and verifies.

    NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 parses a .ps1
    without a UTF-8 BOM using the system ANSI codepage, so Chinese comments
    or output would break into garbage tokens on this machine.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Gen-PatchVersion.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File Gen-PatchVersion.ps1 -Today 20260928
#>
param(
    [string]$Root = "",
    [string]$PackagePath = "",
    [string]$SeqFile = "",
    [string]$Today = "",
    [switch]$Semver,
    [string]$SemverBase = "",
    # T01335: Android build.gradle.kts to bump versionCode in (empty = default path,
    # pass -NoAndroid to skip the Android step entirely).
    [string]$GradlePath = "",
    [switch]$NoAndroid
)

$ErrorActionPreference = "Stop"

# --- resolve paths (script normally lives in <root>\scripts) ---
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = Split-Path -Parent $scriptDir }
if ([string]::IsNullOrWhiteSpace($PackagePath)) {
    $PackagePath = Join-Path (Join-Path $Root "desktop-app") "package.json"
}
if ([string]::IsNullOrWhiteSpace($SeqFile)) {
    $SeqFile = Join-Path (Join-Path $Root ".build") "patch-seq.json"
}
if ([string]::IsNullOrWhiteSpace($Today)) {
    $Today = (Get-Date).ToString("yyyyMMdd")
}
if ([string]::IsNullOrWhiteSpace($GradlePath)) {
    $GradlePath = Join-Path (Join-Path (Join-Path $Root "android-app") "app") "build.gradle.kts"
}

if (-not (Test-Path $PackagePath)) {
    Write-Host "[PatchVersion][ERROR] package.json not found: $PackagePath"
    exit 1
}

# --- sequence: same day -> +1 ; new day -> reset to 1 ---
$seq = 1
if (Test-Path $SeqFile) {
    try {
        $state = Get-Content -Raw -Encoding UTF8 $SeqFile | ConvertFrom-Json
        if ($state.date -eq $Today) {
            $prev = [int]$state.seq
            if ($prev -gt 0) { $seq = $prev + 1 }
        }
    } catch {
        Write-Host "[PatchVersion][WARN] seq file unreadable, restart from 1: $SeqFile"
        $seq = 1
    }
}

$patch = "{0}-{1}" -f $Today, ("{0:D3}" -f $seq)

# --- write version back, keeping the file layout intact ---
# Keep whatever encoding the file already uses: a UTF-8 BOM must survive the
# rewrite, otherwise the first key turns into \ufeff and JSON parsing breaks.
$bytes = [System.IO.File]::ReadAllBytes($PackagePath)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
if ($hasBom) {
    $enc = New-Object System.Text.UTF8Encoding($true)
} else {
    $enc = New-Object System.Text.UTF8Encoding($false)
}
$text = [System.IO.File]::ReadAllText($PackagePath, $enc)
$pattern = '"version"\s*:\s*"[^"]*"'
$match = [regex]::Match($text, $pattern)
if (-not $match.Success) {
    Write-Host "[PatchVersion][ERROR] no version field in $PackagePath"
    exit 1
}

# Default output is the strict "yyyyMMdd-NNN" form required by the task.
# NOTE: that form is NOT valid semver, so npm / electron-builder would reject it.
# Pass -Semver to emit "<base>-yyyyMMdd-NNN" (a valid semver prerelease) instead.
$versionValue = $patch
if ($Semver) {
    $base = $SemverBase
    if ([string]::IsNullOrWhiteSpace($base)) {
        $baseMatch = [regex]::Match($match.Value, '(\d+)\.(\d+)\.(\d+)')
        if ($baseMatch.Success) { $base = $baseMatch.Value } else { $base = "0.1.0" }
    }
    $versionValue = "$base-$patch"
}

$newText = $text.Remove($match.Index, $match.Length).Insert(
    $match.Index, '"version": "' + $versionValue + '"')
[System.IO.File]::WriteAllText($PackagePath, $newText, $enc)

# --- verify by parsing the JSON back ---
$verify = Get-Content -Raw -Encoding UTF8 $PackagePath | ConvertFrom-Json
if ($verify.version -ne $versionValue) {
    Write-Host "[PatchVersion][ERROR] verify failed: got $($verify.version), expect $versionValue"
    exit 1
}

# --- persist sequence only after the write succeeded ---
$seqDir = Split-Path -Parent $SeqFile
if (-not (Test-Path $seqDir)) { New-Item -ItemType Directory -Path $seqDir | Out-Null }
$stateJson = '{"date":"' + $Today + '","seq":' + $seq + '}'
[System.IO.File]::WriteAllText($SeqFile, $stateJson, $enc)

Write-Host "[PatchVersion] $patch -> $PackagePath"
Write-Host "[PatchVersion] verify OK: version = $($verify.version)"

# --- T01335: bump Android versionCode (must be a monotonic Int32) ---
# UpdateManager fires only when manifest versionCode > installed longVersionCode,
# so a hardcoded 1 silently disables every update prompt.
if (-not $NoAndroid) {
    if (-not (Test-Path $GradlePath)) {
        Write-Host "[PatchVersion][WARN] gradle file not found, skip versionCode bump: $GradlePath"
    } else {
        $ktsBytes = [System.IO.File]::ReadAllBytes($GradlePath)
        $ktsBom = ($ktsBytes.Length -ge 3 -and $ktsBytes[0] -eq 0xEF -and $ktsBytes[1] -eq 0xBB -and $ktsBytes[2] -eq 0xBF)
        if ($ktsBom) { $ktsEnc = New-Object System.Text.UTF8Encoding($true) }
        else { $ktsEnc = New-Object System.Text.UTF8Encoding($false) }
        $ktsText = [System.IO.File]::ReadAllText($GradlePath, $ktsEnc)
        $vcMatch = [regex]::Match($ktsText, 'versionCode\s*=\s*(\d+)')
        if (-not $vcMatch.Success) {
            Write-Host "[PatchVersion][ERROR] no versionCode field in $GradlePath"
            exit 1
        }
        $curVc = [long]$vcMatch.Groups[1].Value
        $epoch = [datetime]"2020-01-01"
        $dayPart = [datetime]::ParseExact($Today, "yyyyMMdd", $null)
        $candidate = ([long]($dayPart - $epoch).Days * 100) + $seq
        $newVc = $candidate
        if ($newVc -le $curVc) { $newVc = $curVc + 1 }  # never go backwards
        if ($newVc -gt 2147483647) {
            Write-Host "[PatchVersion][ERROR] versionCode overflows Int32: $newVc"
            exit 1
        }
        $ktsNew = $ktsText.Remove($vcMatch.Index, $vcMatch.Length).Insert(
            $vcMatch.Index, "versionCode = $newVc")
        [System.IO.File]::WriteAllText($GradlePath, $ktsNew, $ktsEnc)
        $vcVerify = [regex]::Match([System.IO.File]::ReadAllText($GradlePath, $ktsEnc), 'versionCode\s*=\s*(\d+)')
        if (-not $vcVerify.Success -or [long]$vcVerify.Groups[1].Value -ne $newVc) {
            Write-Host "[PatchVersion][ERROR] versionCode verify failed, expect $newVc"
            exit 1
        }
        Write-Host "[PatchVersion] Android versionCode $curVc -> $newVc -> $GradlePath"
    }
}
exit 0
