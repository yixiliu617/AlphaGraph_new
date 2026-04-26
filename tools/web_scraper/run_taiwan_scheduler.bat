@echo off
REM ---------------------------------------------------------------------------
REM AlphaGraph Taiwan scheduler launcher.
REM Long-running APScheduler process: MOPS monthly revenue + material info +
REM TPEx OpenAPI + weekly patches. See backend/app/services/taiwan/scheduler.py
REM for the full job list.
REM
REM Invoked by the AlphaGraph_TaiwanScheduler Windows task at user logon.
REM Auto-restart on failure is handled by the Windows task settings.
REM ---------------------------------------------------------------------------

cd /d C:\Users\Sharo\AI_projects\AlphaGraph_new

if not exist logs mkdir logs

set PYTHONUNBUFFERED=1
set PYTHONPATH=C:\Users\Sharo\AI_projects\AlphaGraph_new

C:\Users\Sharo\miniconda3\python.exe -m backend.app.services.taiwan.scheduler >> logs\taiwan_scheduler.log 2>&1
