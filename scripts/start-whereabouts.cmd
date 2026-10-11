@echo off
rem === EDIT THESE TWO PATHS FOR YOUR MACHINE ===
set SPATIAL_ROOT=C:\tools\spatial-awareness
set SPATIAL_ENV=C:\tools\spatial-awareness\.env
rem whereabouts-mcp ingest server launcher (called by watchdog.cmd / schtasks)
cd /d %SPATIAL_ROOT%\whereabouts-mcp

set envfile=%SPATIAL_ENV%
for /f "usebackq tokens=1,* delims==" %%a in ("%envfile%") do (
    if "%%a"=="WHEREABOUTS_TOKEN" set WHEREABOUTS_TOKEN=%%b
    if "%%a"=="WHEREABOUTS_HOME_CENTER" set WHEREABOUTS_HOME_CENTER=%%b
    if "%%a"=="WHEREABOUTS_WORK_CENTER" set WHEREABOUTS_WORK_CENTER=%%b
    if "%%a"=="WHEREABOUTS_KNOWN_PLACES" set WHEREABOUTS_KNOWN_PLACES=%%b
)

set WHEREABOUTS_STATE_DIR=%SPATIAL_ROOT%\state
set WHEREABOUTS_HOST=0.0.0.0
set WHEREABOUTS_PORT=4318
set WHEREABOUTS_HISTORY_LIMIT=20
set WHEREABOUTS_MOVEMENT_EVENT_LIMIT=20
set WHEREABOUTS_BATTERY_HISTORY_LIMIT=100
set WHEREABOUTS_PLACE_RADIUS_METERS=150

node .\bin\whereabouts-mcp.js serve >> %SPATIAL_ROOT%\whereabouts-service.log 2>&1
