@echo off
rem Wheel Strategy Bot - Windows Daily Runner
rem Usage: run_daily.bat [--dry-run]

cd /d "%~dp0"

if exist .env (
    for /f "usebackq tokens=1,2 delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
)

where python >nul 2>&1
if errorlevel 1 (echo ERROR: python not found & exit /b 1)

python -c "import sys; import pandas_market_calendars as mcal; from datetime import date; nyse=mcal.get_calendar('NYSE'); s=nyse.schedule(start_date=date.today().isoformat(),end_date=date.today().isoformat()); print('NOT_TRADING_DAY' if s.empty else 'TRADING_DAY')" > "%TEMP%\wheel_cal.txt" 2>&1
findstr /C:"NOT_TRADING_DAY" "%TEMP%\wheel_cal.txt" >nul
if not errorlevel 1 (echo Not a trading day. Skipped. & exit /b 0)

echo Running Wheel Strategy Bot...
python -m src.strategy %*
if errorlevel 1 (echo ERROR: exit code %errorlevel% & exit /b %errorlevel%)
echo Done.
