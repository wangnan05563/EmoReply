@echo off
rem ============================================================
rem  EmoReply - build & pack launcher with a unique patch version
rem  Step 1: generate patch number (yyyyMMdd-NNN) and write it into
rem          desktop-app\package.json -> "version"
rem  Step 2: run the desktop build (setup-and-build-desktop.ps1)
rem
rem  NOTE: keep this file ASCII-only with CRLF line endings.
rem  cmd.exe parses batch files in the ANSI codepage (GBK on
rem  zh-CN systems); UTF-8 Chinese comments get mis-parsed into
rem  bogus commands (T01359 root cause).
rem
rem  Extra arguments are forwarded to the build script:
rem    this script -SkipBuild        env setup only
rem    this script -SkipInstaller    portable build only, no installer
rem    this script -Force            reinstall all dependencies
rem    this script -NodeRoot E:\nodejs
rem ============================================================

rem --- Step 1: patch number (before packing, must succeed first) ---
set "PATCH=%~dp0Gen-PatchVersion.ps1"
if not exist "%PATCH%" set "PATCH=%~dp0scripts\Gen-PatchVersion.ps1"
if not exist "%PATCH%" (
    echo [ERROR] Gen-PatchVersion.ps1 not found next to this script or in .\scripts
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PATCH%"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo [FAILED] patch version step failed, exit code %RC%
    pause
    exit /b %RC%
)

rem --- Step 2: desktop build & pack ---
set "PS1=%~dp0setup-and-build-desktop.ps1"
if not exist "%PS1%" set "PS1=%~dp0scripts\setup-and-build-desktop.ps1"
if not exist "%PS1%" (
    echo [ERROR] setup-and-build-desktop.ps1 not found next to this script or in .\scripts
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" echo [FAILED] exit code %RC%
pause
exit /b %RC%
