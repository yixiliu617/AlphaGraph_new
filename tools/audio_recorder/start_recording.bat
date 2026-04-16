@echo off
echo ==========================================
echo    Zoom Meeting Audio Recorder
echo ==========================================
echo.
cd /d "%~dp0"
python record_meeting.py %*
pause
