@echo off
title Job Tracker - Scan URLs
echo ============================================
echo   Job Tracker - Starting Scan Pipeline
echo ============================================
echo.

:: Generate timestamped log filename (YYYY-MM-DD_HH-MM-SS)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set LOGNAME=%datetime:~0,4%-%datetime:~4,2%-%datetime:~6,2%_%datetime:~8,2%-%datetime:~10,2%-%datetime:~12,2%
set LOGFILE=%~dp0scan_logs\%LOGNAME%.log

echo Logging to: %LOGFILE%
echo.

:: Run WSL and tee output to both console and log file
wsl bash -c "cd /mnt/e/JobTrackerFullstack/JobTracker && bash run_scan.sh 2>&1" > "%LOGFILE%" 2>&1
type "%LOGFILE%"

echo.
echo ============================================
echo   Finished - Log saved to scan_logs\%LOGNAME%.log
echo ============================================
@REM pause
