@echo off
echo ============================================
echo    Live Meeting Recording + Transcription
echo ============================================
echo.
echo This will:
echo   1. Record audio to WAV file
echo   2. Show live transcription on screen
echo.
cd /d "%~dp0"

REM Start recording in background
start "Audio Recorder" cmd /c "python record_meeting.py"

REM Start live transcription
echo Starting live transcription...
echo.
python live_transcribe.py

pause
