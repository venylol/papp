@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "PAPP_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PAPP_PYTHON%" (
    echo [ERROR] 请完整解压 PAPP 包，缺少 runtime\python\python.exe。
    pause
    exit /b 1
)
"%PAPP_PYTHON%" -I -B -X utf8 "%~dp0support\install_dependencies.py" %*
set "PAPP_EXIT=%ERRORLEVEL%"
if not "%PAPP_EXIT%"=="0" echo [ERROR] 依赖安装或检查失败，请查看上面的错误。
if "%~1"=="" pause
exit /b %PAPP_EXIT%
