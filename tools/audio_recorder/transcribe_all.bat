@echo off
echo ============================================
echo    Post-Meeting Transcription (Whisper)
echo ============================================
echo.
cd /d "%~dp0"

if "%1"=="" (
    echo Transcribing all recordings in ./recordings folder...
    python transcribe_recording.py recordings --model medium
) else (
    echo Transcribing: %1
    python transcribe_recording.py %1 --model medium
)

pause
