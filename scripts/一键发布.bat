@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM EmoReply one-click build + publish to GitHub Releases
REM Pure ASCII entry. All Chinese output is delegated to build-publish.ps1.
REM Configure everything in config.json (no hardcoded project name/path/version).
REM Reference: 18_comparePakage tooling scripts one-click publish bat (one-click publish)
REM ============================================================
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"
if not defined CONFIG_PATH set "CONFIG_PATH=%SCRIPT_DIR%config.json"
chcp 65001 >nul
echo [publish] project root: %REPO_ROOT%
echo [publish] config: %CONFIG_PATH%
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-publish.ps1" -ConfigPath "%CONFIG_PATH%" -RepoRoot "%REPO_ROOT%"
if errorlevel 1 (
  echo [publish] FAILED - see output above
  exit /b 1
)
echo.
echo [publish] DONE.
endlocal
