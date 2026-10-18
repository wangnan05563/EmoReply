@echo off
rem ============================================================
rem  EmoReply PoC - PC Desktop Build Launcher (T01101/T01359)
rem  Delegates to setup-and-build-desktop.ps1.
rem
rem  Build outputs and ALL build caches land in .\release
rem  (delete that folder to clean up completely).
rem
rem  Deliverables:
rem    release\desktop\EmoReply.exe          portable (green) build
rem    release\desktop\EmoReply-*-x64.exe    NSIS installer
rem  The installer step can be skipped: append -SkipInstaller.
rem
rem  NOTE: keep this file ASCII-only with CRLF line endings.
rem  cmd.exe parses batch files in the ANSI codepage (GBK on
rem  zh-CN systems); UTF-8 Chinese comments get mis-parsed into
rem  bogus commands (T01359 root cause).
rem
rem  Extra arguments are forwarded to the PowerShell script:
rem    this script -SkipBuild        env setup only
rem    this script -SkipInstaller    portable build only, no installer
rem    this script -Force            reinstall all dependencies
rem    this script -NodeRoot E:\nodejs
rem ============================================================

rem Step 1: patch number (yyyyMMdd-NNN) -> desktop-app\package.json "version",
rem must run BEFORE packing so every build gets a unique version.
set "PATCHPS1=%~dp0Gen-PatchVersion.ps1"
if not exist "%PATCHPS1%" set "PATCHPS1=%~dp0scripts\Gen-PatchVersion.ps1"
if not exist "%PATCHPS1%" (
    echo [ERROR] Gen-PatchVersion.ps1 not found next to this script or in .\scripts
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PATCHPS1%"
set PRC=%ERRORLEVEL%
if not "%PRC%"=="0" (
    echo [FAILED] patch version step failed, exit code %PRC%
    pause
    exit /b %PRC%
)

rem Locate the .ps1 next to this launcher, falling back to .\scripts.
rem A hard-coded relative level breaks whenever the launcher sits in a
rem different folder (e.g. scripts\scripts\...).
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
