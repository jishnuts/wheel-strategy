@echo off
rem Auto-schedule Wheel Bot via Windows Task Scheduler
rem Run once as Administrator. Schedule: Mon-Fri 9:45 AM

set TASK_NAME=WheelStrategyBot
set BOT_DIR=%~dp0
set BAT_FILE=%BOT_DIR%run_daily.bat

echo Creating scheduled task "%TASK_NAME%"...
schtasks /Create /TN "%TASK_NAME%" /TR "cmd /c \"%BAT_FILE%\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:45 /RL HIGHEST /F
if errorlevel 1 (echo ERROR: Run as Administrator. & exit /b 1)
echo Task created. To test: schtasks /Run /TN "%TASK_NAME%"
