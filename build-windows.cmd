@echo off
setlocal

set "MSYS2_ROOT=%USERPROFILE%\msys64"
set "PROJECT_ROOT=%~dp0"

if not exist "%MSYS2_ROOT%\usr\bin\bash.exe" (
    echo MSYS2 was not found at "%MSYS2_ROOT%".
    exit /b 1
)
pushd "%PROJECT_ROOT%"
"%MSYS2_ROOT%\usr\bin\bash.exe" -c "export MSYSTEM=UCRT64; export CHERE_INVOKING=1; export PATH=/ucrt64/bin:/usr/bin:$PATH; cd src; mingw32-make MINGW_NATIVE=1 clean all"
set "BUILD_STATUS=%errorlevel%"
popd
if not "%BUILD_STATUS%"=="0" exit /b %BUILD_STATUS%

echo English Windows build completed: "%PROJECT_ROOT%bin\Windows\papp_GB.exe"
endlocal
