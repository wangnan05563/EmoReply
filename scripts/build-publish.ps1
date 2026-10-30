#Requires -Version 5.1
<#
.SYNOPSIS
    EmoReply one-click build + publish to GitHub Releases (config-driven, no hardcoding)
.DESCRIPTION
    Reads config.json (tooling/scripts/config.json):
      1) build_pipeline.steps: install deps, build desktop Electron (NSIS+portable), build Android APK
      2) locate assets: primary (desktop setup exe) for version+notes, plus additional (portable exe, android apk)
      3) derive version from github_release.version_file_path (desktop-app/package.json; repo has no root package.json)
      4) release notes: release-notes-<VER>.md (auto-generate placeholder if missing; repo has no git -> no git log)
      5) publish each asset via publish-release.py (token from GH_TOKEN / .env / git credential)
    All paths / versions / tokens are config-driven. This file contains ZERO project literals.
    Build steps run through a temp .cmd invoked by cmd.exe to keep native `&&` / `set` / `pushd` semantics
    (Windows PowerShell 5.1 does not support `&&`).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$RepoRoot
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if (-not (Test-Path $ConfigPath)) { Write-Error "config not found: $ConfigPath"; exit 1 }
$cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json

# ---- merge machine-local overrides (gitignored, lives next to the config) ----
function Merge-Config($base, $over) {
    foreach ($p in $over.PSObject.Properties) {
        $cur = $base.PSObject.Properties[$p.Name]
        if ($cur -and $cur.Value -is [pscustomobject] -and $p.Value -is [pscustomobject]) {
            Merge-Config $cur.Value $p.Value | Out-Null
        } else {
            $base | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force
        }
    }
    return $base
}
$localName = if ($cfg.runtime -and $cfg.runtime.local_config) { $cfg.runtime.local_config } else { 'config.local.json' }
$configDir = Split-Path -Parent (Resolve-Path $ConfigPath).Path
$localCfg = Join-Path $configDir $localName
if (Test-Path $localCfg) {
    $cfg = Merge-Config $cfg (Get-Content -Raw -Encoding UTF8 $localCfg | ConvertFrom-Json)
    Write-Host "[build-publish] local overrides: $localCfg"
}

$rel = $cfg.github_release
if (-not $rel -or $rel.enabled -ne $true) {
    Write-Host "[build-publish] github_release disabled, skip" -ForegroundColor Yellow
    exit 0
}

# ---- resolve python (config-driven, never hardcode managed path) ----
function Resolve-Python {
    $cands = if ($rel.python -and $rel.python.candidates) { $rel.python.candidates } else { @() }
    foreach ($c in $cands) {
        $p = [System.Environment]::ExpandEnvironmentVariables($c)
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}
$py = Resolve-Python
if (-not $py) { Write-Error "[build-publish] python not found (set github_release.python.candidates or install python)"; exit 1 }
Write-Host "[build-publish] python: $py"

# ---- runtime paths: config (env-expandable) -> environment -> PATH, never hardcoded ----
$javaHome = $null
if ($cfg.runtime -and $cfg.runtime.java_home) { $javaHome = [System.Environment]::ExpandEnvironmentVariables($cfg.runtime.java_home) }
if (-not $javaHome -or $javaHome -match '%') { $javaHome = $env:JAVA_HOME }
if (-not $javaHome -or -not (Test-Path (Join-Path $javaHome 'bin\java.exe'))) {
    Write-Error "[build-publish] JAVA_HOME unresolved: set runtime.java_home in $localName or the JAVA_HOME env var (expects <jdk>\bin\java.exe)"
    exit 1
}

$gradle = $null
$gradleCfg = if ($cfg.runtime -and $cfg.runtime.gradle) { [System.Environment]::ExpandEnvironmentVariables($cfg.runtime.gradle) } else { 'gradle' }
if ($gradleCfg -match '[\\/]') {
    if (Test-Path $gradleCfg) { $gradle = $gradleCfg }
} else {
    $cmd = Get-Command $gradleCfg -ErrorAction SilentlyContinue
    if ($cmd) { $gradle = $cmd.Source }
}
if (-not $gradle) {
    Write-Error "[build-publish] gradle not found: set runtime.gradle in $localName or put gradle on PATH"
    exit 1
}
Write-Host "[build-publish] java_home: $javaHome"
Write-Host "[build-publish] gradle: $gradle"

Push-Location $RepoRoot
try {
    # ---- 1) build pipeline (via temp .cmd so native cmd semantics apply) ----
    if ($cfg.build_pipeline -and $cfg.build_pipeline.steps) {
        $i = 0
        foreach ($step in $cfg.build_pipeline.steps) {
            $i++
            $cmd = $step.command
            if (-not $cmd) { continue }
            $cmd = $cmd -replace '<REPO_ROOT>', $RepoRoot
            $cmd = $cmd -replace '<JAVA_HOME>', $javaHome
            $cmd = $cmd -replace '<GRADLE>', $gradle
            Write-Host "[build-publish] [$i/$($cfg.build_pipeline.steps.Count)] $($step.name)" -ForegroundColor Cyan
            Write-Host "  > $cmd"
            $tmp = Join-Path $env:TEMP ("bp-step-" + $i + ".cmd")
            Set-Content -Path $tmp -Encoding ASCII -Value $cmd
            cmd /c $tmp
            Remove-Item $tmp -ErrorAction SilentlyContinue
            if ($LASTEXITCODE -ne 0) {
                $onFail = if ($step.on_fail) { $step.on_fail } else { 'stop' }
                if ($onFail -eq 'skip') { Write-Warning '  step failed, skip'; continue }
                if ($onFail -eq 'warn') { Write-Warning '  step failed, continue'; continue }
                Write-Error "[build-publish] step failed (exit $LASTEXITCODE): $($step.name)"
            }
        }
    } else {
        Write-Host '[build-publish] no build_pipeline.steps, skip build' -ForegroundColor Yellow
    }

    # ---- 2) derive version ----
    $ver = ''
    $vf = if ($rel.version_file_path) { Join-Path $RepoRoot $rel.version_file_path } else { Join-Path $RepoRoot 'package.json' }
    if (Test-Path $vf) {
        $vo = Get-Content -Raw -Encoding UTF8 $vf | ConvertFrom-Json
        if ($vo.version) { $ver = $vo.version }
    }
    if (-not $ver) { Write-Error ('[build-publish] cannot derive version from ' + $vf); exit 1 }
    Write-Host "[build-publish] version: $ver"

    # ---- 3) notes ----
    $notes = $null
    if ($rel.notes -and $rel.notes.path_template) {
        $notesPath = Join-Path $RepoRoot ($rel.notes.path_template -replace '<VER>', $ver)
        if (-not (Test-Path $notesPath)) {
            if ($rel.notes.auto_generate -ne $false) {
                $auto = Join-Path $RepoRoot (($rel.notes.path_template -replace '<VER>', $ver) -replace '\.md$', '.auto.md')
                Set-Content -Path $auto -Encoding UTF8 -Value ("# EmoReply $ver`n`n（自动生成的发布说明占位符；正式发布前请替换为正式说明，例如列出本次桌面端/安卓端变更。）`n")
                $notesPath = $auto
                Write-Host "[build-publish] auto-generated notes: $notesPath" -ForegroundColor Yellow
            } else {
                Write-Error ("[build-publish] notes file missing and auto_generate disabled: " + $notesPath); exit 1
            }
        }
        $notes = $notesPath
    } else {
        Write-Error '[build-publish] github_release.notes.path_template not configured'; exit 1
    }
    Write-Host "[build-publish] notes: $notes"

    # ---- 4) collect assets (primary + additional) ----
    $assets = @()
    function Add-Asset([string]$dir, [string]$glob, [string]$display, [string[]]$exclude) {
        $d = Join-Path $RepoRoot $dir
        if (-not (Test-Path $d)) { Write-Warning ("[build-publish] asset dir missing: " + $d); return }
        $glob = $glob -replace '<VER>', $ver
        $m = Get-ChildItem -Path $d -File | Where-Object { $_.Name -like $glob } | Where-Object {
            $n = $_.Name
            -not ($exclude | Where-Object { $n -like ("*" + $_ + "*") })
        } | Sort-Object LastWriteTime -Descending
        if (-not $m -or $m.Count -eq 0) { Write-Warning ("[build-publish] no asset matching '" + $glob + "' in " + $d); return }
        $f = $m[0].FullName
        $disp = if ($display) { $display -replace '<VER>', $ver } else { $m[0].Name }
        $script:assets += @{ path = $f; display = $disp }
        Write-Host ("[build-publish] asset: " + $f + "  (display: " + $disp + ")") -ForegroundColor Green
    }
    Add-Asset $rel.asset.dir $rel.asset.glob $rel.asset.display_name $rel.asset.exclude_patterns
    if ($rel.additional_assets) {
        foreach ($a in $rel.additional_assets) {
            Add-Asset $a.dir $a.glob $a.display_name $a.exclude_patterns
        }
    }
    if ($assets.Count -eq 0) { Write-Error '[build-publish] no assets located, abort'; exit 1 }

    # ---- 5) publish each asset ----
    $pub = Join-Path $RepoRoot $rel.publish_script
    if (-not (Test-Path $pub)) { Write-Error ("[build-publish] publish script missing: " + $pub); exit 1 }
    $env:GH_TAG = $ver
    if ($rel.name_template) { $env:GH_RELEASE_NAME = $rel.name_template -replace '<VER>', $ver }
    if ($rel.repo) { $env:GH_REPO = $rel.repo }
    if ($rel.draft) { $env:GH_DRAFT = '1' }
    if ($rel.prerelease) { $env:GH_PRERELEASE = '1' }
    if ($rel.dry_run) { $env:GH_DRY_RUN = '1' }
    Write-Host ("[build-publish] publishing {0} asset(s) (dry_run={1})..." -f $assets.Count, $rel.dry_run) -ForegroundColor Cyan
    foreach ($as in $assets) {
        Write-Host ("  -> " + $as.display) -ForegroundColor Cyan
        & $py $pub $as.path $notes $as.display
        if ($LASTEXITCODE -ne 0) { Write-Error ("[build-publish] publish failed (exit {0}): {1}" -f $LASTEXITCODE, $as.display); exit 1 }
    }
    Write-Host '[build-publish] publish done' -ForegroundColor Green
} finally {
    Pop-Location
}
exit 0
