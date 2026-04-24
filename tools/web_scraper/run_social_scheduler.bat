@echo off
REM ---------------------------------------------------------------------------
REM AlphaGraph social scheduler launcher.
REM Replaces the broken scheduled_scrape.bat. Uses absolute Python path so
REM Task Scheduler's minimal environment can find the interpreter.
REM Run from Task Scheduler at user logon; process survives until reboot/logoff
REM or until the scheduler itself exits. Logs land in logs\social_scheduler.log.
REM ---------------------------------------------------------------------------

cd /d C:\Users\Sharo\AI_projects\AlphaGraph_new

if not exist logs mkdir logs

set PYTHONUNBUFFERED=1
set PYTHONPATH=C:\Users\Sharo\AI_projects\AlphaGraph_new

C:\Users\Sharo\miniconda3\python.exe -m backend.app.services.social.scheduler >> logs\social_scheduler.log 2>&1
