@echo off
rem ============================================================
rem  EmoReply PoC - Push & Test Launcher
rem  Delegates to push-and-test-android.ps1.
rem
rem  Plug in the phone (USB debugging enabled) and double-click.
rem
rem  Extra arguments are forwarded to the PowerShell script:
rem    push.cmd -NoLaunch      install only, do not start the app
rem    push.cmd -SetIme        also switch the IME to EmoReply
rem    push.cmd -AndroidRoot E:\android
rem ============================================================

rem Locate the .ps1 next to this launcher, falling back to .\scripts.
rem A hard-coded relative level breaks whenever the launcher sits in a
rem different folder (e.g. scripts\scripts\...).
set "PS1=%~dp0push-and-test-android.ps1"
if not exist "%PS1%" set "PS1=%~dp0scripts\push-and-test-android.ps1"
if not exist "%PS1%" (
    echo [ERROR] push-and-test-android.ps1 not found next to this script or in .\scripts
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" echo [FAILED] exit code %RC%
pause
exit /b %RC%