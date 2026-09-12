@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "APP_DIR=%ROOT%third_party\checkin-frontend"
set "SERVER=%APP_DIR%\local-server.js"
set "APP_URL=http://127.0.0.1:4175/papp-portal/"
set "HEALTH_URL=http://127.0.0.1:4175/api/health"
set "SERVER_SERVICE=papp-local-frontend"
set "SERVER_VERSION=papp-local-frontend.23"

echo PAPP local frontend launcher
echo ===========================
echo.
echo This starts the local static page and its shared-state channel.
echo It does not start FTD, WeChat, Agent, or any external workflow.
echo.

if not exist "%SERVER%" (
  echo [ERROR] local-server.js not found:
  echo %SERVER%
  pause
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] node was not found in PATH.
  echo Install Node.js or add node to PATH.
  pause
  exit /b 1
)

echo [1/3] Checking local page server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $healthy=$false; try { $r=Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 1; if ($r.ok -eq $true -and $r.service -eq '%SERVER_SERVICE%' -and $r.version -eq '%SERVER_VERSION%') { $healthy=$true } } catch { }; if (-not $healthy) { $localServers=@(Get-CimInstance Win32_Process -Filter 'name = ''node.exe''' | Where-Object { $serverCommand=[string]$_.CommandLine; if ($serverCommand -notlike '*local-server.js*') { return $false }; if ($serverCommand -like '*%SERVER%*') { return $true }; $parent=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_.ParentProcessId); return ([string]$parent.CommandLine -like '*%APP_DIR%*') }); if ($localServers.Count) { Write-Host '[INFO] Restarting outdated PAPP local server.'; foreach ($p in $localServers) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }; Start-Process powershell -WindowStyle Minimized -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-Command','Set-Location -LiteralPath ''%APP_DIR%''; node ''%SERVER%''' }"

echo [2/3] Waiting for local API...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $ok=$false; for ($i=0; $i -lt 20; $i++) { try { $r=Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 1; if ($r.ok -eq $true -and $r.service -eq '%SERVER_SERVICE%' -and $r.version -eq '%SERVER_VERSION%') { $ok=$true; break } } catch { }; Start-Sleep -Milliseconds 500 }; if (-not $ok) { exit 1 }"
if errorlevel 1 (
  echo [ERROR] local API did not become ready.
  echo Try manually:
  echo cd /d "%APP_DIR%"
  echo node local-server.js
  pause
  exit /b 1
)

echo [3/3] Opening PAPP workspace selector...
start "" "%APP_URL%"

echo.
echo Ready:
echo - Local page: %APP_URL%
echo - Shared state API: %HEALTH_URL%
echo - Shared state file: %ROOT%data\checkin-state.json
echo.
echo The page saves its state in the browser and mirrors it to the local JSON
echo file when opened through this launcher. Stop the server window with Ctrl+C.
echo.
pause
endlocal
