@echo off
set SPATIAL_ROOT=C:\tools\spatial-awareness
rem whereabouts-mcp watchdog: health check 4318, restart if dead
set LOG=%SPATIAL_ROOT%\watchdog.log

curl -s -m 5 -o nul -w "%%{http_code}" http://127.0.0.1:4318/healthz > "%TEMP%\whb_code.txt" 2>nul
set /p CODE=<"%TEMP%\whb_code.txt"

if "%CODE%"=="200" exit /b 0

echo [%date% %time%] dead (code=%CODE%), restarting... >> "%LOG%"
call %SPATIAL_ROOT%\start-whereabouts.cmd
echo [%date% %time%] restart issued >> "%LOG%"
