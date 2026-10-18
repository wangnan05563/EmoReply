@echo off
rem ============================================================
rem  EmoReply PoC - Build Launcher
rem  Delegates to setup-and-build-android.ps1.
rem
rem  Build outputs and ALL build caches land in .\release
rem  (delete that folder to clean up completely).
rem
rem  Extra arguments are forwarded to the PowerShell script:
rem    compile.cmd -SkipBuild        env setup only
rem    compile.cmd -Force            reinstall all dependencies
rem    compile.cmd -AndroidRoot E:\android
rem ============================================================

rem Locate the .ps1 next to this launcher, falling back to .\scripts.
rem A hard-coded relative level breaks whenever the launcher sits in a
rem different folder (e.g. scripts\scripts\...).
set "PS1=%~dp0setup-and-build-android.ps1"
if not exist "%PS1%" set "PS1=%~dp0scripts\setup-and-build-android.ps1"
if not exist "%PS1%" (
    echo [ERROR] setup-and-build-android.ps1 not found next to this script or in .\scripts
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" echo [FAILED] exit code %RC%
pause
exit /b %RC%