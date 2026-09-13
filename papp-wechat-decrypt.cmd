@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PAPP_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PAPP_PYTHON%" (
    echo [ERROR] Bundled Python is missing. Extract the complete PAPP package.
    pause
    exit /b 1
)
"%PAPP_PYTHON%" -I -B -X utf8 "%~dp0support\run_wechat_decrypt.py" %*
set "PAPP_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %PAPP_EXIT%
