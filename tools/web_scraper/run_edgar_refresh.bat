@echo off
REM ---------------------------------------------------------------------------
REM AlphaGraph EDGAR daily refresh launcher.
REM One-shot (not long-running): incremental refresh via ToplineBuilder.refresh().
REM Invoked by the AlphaGraph_EdgarDaily Windows task once per day at
REM 06:00 local (Asia/Taipei) = 6pm US Eastern during EDT (May-Nov).
REM During EST (Nov-Mar) this fires at 5pm ET — 1h earlier than requested.
REM ---------------------------------------------------------------------------

cd /d C:\Users\Sharo\AI_projects\AlphaGraph_new

if not exist logs mkdir logs

set PYTHONUNBUFFERED=1
set PYTHONPATH=C:\Users\Sharo\AI_projects\AlphaGraph_new

REM Timestamped header so multiple runs in one log are distinguishable.
echo ======================================================================== >> logs\edgar_refresh.log
echo [%date% %time%] EDGAR refresh starting >> logs\edgar_refresh.log

C:\Users\Sharo\miniconda3\python.exe -m backend.scripts.refresh_topline >> logs\edgar_refresh.log 2>&1

echo [%date% %time%] EDGAR refresh finished (exit=%errorlevel%) >> logs\edgar_refresh.log
