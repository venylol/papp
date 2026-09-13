@echo off
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Usage: %~nx0 C:\path\to\extracted_v3_server_root
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_extension_1200_data_prep.ps1" -ServerRoot "%~1"
exit /b %ERRORLEVEL%
