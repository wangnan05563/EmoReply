@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM EmoReply one-click build + publish to GitHub Releases
REM Pure ASCII entry. All Chinese output is delegated to build-publish.ps1.
REM Configure everything in config.json (no hardcoded project name/path/version).
REM
REM Preflight checks (adapted from AirMirror scripts one-click publish bat):
REM   0) Load GH_TOKEN / GITHUB_TOKEN from repo-root .env (environment wins).
REM      publish-release.py reads GH_TOKEN only, so GITHUB_TOKEN is mapped to it.
REM   1) git available (git credential is the last token fallback).
REM   2) PowerShell available (needed to run build-publish.ps1).
REM   3) gh CLI optional (REST via token is used when absent).
REM   4) config file present.
REM   5) python available (runs publish-release.py; config candidates are the fallback).
REM
REM Usage:
REM   double-click one-click publish.bat        (build + publish)
REM   one-click publish.bat <extra args>        (extra args passed through to build-publish.ps1)
REM   set NO_PAUSE=1 before running             (skip the pause on exit)
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"
if not defined CONFIG_PATH set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "ENV_FILE=%REPO_ROOT%\.env"
chcp 65001 >nul
echo [publish] project root: %REPO_ROOT%
echo [publish] config: %CONFIG_PATH%

REM --- 0. load token from .env (environment already set wins) ---
if not defined GH_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i /b "GH_TOKEN=" "%ENV_FILE%" 2^>nul') do set "GH_TOKEN=%%b"
)
if not defined GH_TOKEN if not defined GITHUB_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i /b "GITHUB_TOKEN=" "%ENV_FILE%" 2^>nul') do set "GITHUB_TOKEN=%%b"
)
REM publish-release.py reads GH_TOKEN only; map GITHUB_TOKEN -> GH_TOKEN.
if not defined GH_TOKEN if defined GITHUB_TOKEN set "GH_TOKEN=!GITHUB_TOKEN!"
if defined GH_TOKEN set "GH_TOKEN=!GH_TOKEN:"=!"
if defined GITHUB_TOKEN set "GITHUB_TOKEN=!GITHUB_TOKEN:"=!"
if defined GH_TOKEN (echo [OK]   GitHub token loaded from environment or .env) else (
    echo [WARN] No GH_TOKEN/GITHUB_TOKEN in environment or .env; publish may fail or hang on git credential helper.
)

REM --- 1. git ---
where git >nul 2>nul
if errorlevel 1 (
    echo [FAIL] git not found. Install Git for Windows and add it to PATH.
    goto :fail
)
for /f "tokens=*" %%v in ('git --version 2^>nul') do echo [OK]   %%v

REM --- 2. PowerShell ---
where powershell >nul 2>nul
if errorlevel 1 (
    echo [FAIL] PowerShell not found; cannot run build-publish.ps1.
    goto :fail
)
echo [OK]   PowerShell available

REM --- 3. gh CLI (optional) ---
where gh >nul 2>nul
if not errorlevel 1 (
    echo [OK]   gh CLI available
) else (
    echo [OK]   gh CLI absent; GitHub REST via token will be used.
)

REM --- 4. config file ---
if not exist "%CONFIG_PATH%" (
    echo [FAIL] config file not found: %CONFIG_PATH%
    goto :fail
)
echo [OK]   config file: %CONFIG_PATH%

REM --- 5. python (runs publish-release.py; config candidates are the fallback) ---
where python >nul 2>nul
if not errorlevel 1 (
    echo [OK]   python available on PATH
) else (
    where python3 >nul 2>nul
    if not errorlevel 1 (
        echo [OK]   python3 available on PATH
    ) else (
        echo [WARN] python not on PATH; build-publish.ps1 will try github_release.python.candidates.
    )
)

set "EXTRA=%*"
echo.
echo [INFO] call: powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-publish.ps1" -ConfigPath "%CONFIG_PATH%" -RepoRoot "%REPO_ROOT%" !EXTRA!
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-publish.ps1" -ConfigPath "%CONFIG_PATH%" -RepoRoot "%REPO_ROOT%" !EXTRA!
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo [FAIL] publish exited with code %CODE%. See PowerShell output above.
    goto :fail
)
echo.
echo [DONE] Build and publish finished. Release link is in the PowerShell output above.
if not defined NO_PAUSE pause
endlocal
exit /b 0

:fail
if not defined NO_PAUSE pause
endlocal
exit /b 1
