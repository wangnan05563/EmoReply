@echo off
rem T01099 TSF probe build (manual env, bypass broken vcvars64)
set "MSVC=C:\Program Files (x86)\Microsoft Visual Studio\2017\BuildTools\VC\Tools\MSVC\14.16.27023"
set "SDK=C:\Program Files (x86)\Windows Kits\10"
set "SDKV=10.0.19041.0"
set "PATH=%MSVC%\bin\Hostx64\x64;%SDK%\bin\%SDKV%\x64;%PATH%"
set "INCLUDE=%MSVC%\include;%SDK%\Include\%SDKV%\ucrt;%SDK%\Include\%SDKV%\um;%SDK%\Include\%SDKV%\shared"
set "LIB=%MSVC%\lib\x64;%SDK%\Lib\%SDKV%\ucrt\x64;%SDK%\Lib\%SDKV%\um\x64"
cl /nologo /utf-8 /EHsc /W3 tsf_probe.cpp /Fe:tsf_probe.exe ole32.lib user32.lib
