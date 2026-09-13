@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows_data_prep.ps1"
exit /b %errorlevel%
