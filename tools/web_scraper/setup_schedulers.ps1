# ---------------------------------------------------------------------------
# AlphaGraph -- Windows Task Scheduler setup.
#
# Registers (or updates) three scheduled tasks:
#   1. AlphaGraph_SocialScheduler   -- APScheduler long-running (social + GPU + X + pcpartpicker)
#   2. AlphaGraph_TaiwanScheduler   -- APScheduler long-running (MOPS revenue + material info + TPEx)
#   3. AlphaGraph_EdgarDaily        -- one-shot daily, 06:00 local (= 6pm ET during EDT)
#
# Resilience model (all three):
#   - Trigger: at user logon (daily task also has a daily time trigger)
#   - Run only when user is logged on (per user preference -- no stored password)
#   - Restart on failure every 5 min, up to 99 attempts
#   - Do not allow a new instance if one is already running
#   - Long-running tasks: no time limit; daily one-shot: 3h execution limit
#
# Run from an elevated PowerShell:
#   cd C:\Users\Sharo\AI_projects\AlphaGraph_new
#   powershell -ExecutionPolicy Bypass -File tools\web_scraper\setup_schedulers.ps1
#
# To roll back, delete any task via Task Scheduler GUI or:
#   Unregister-ScheduledTask -TaskName AlphaGraph_<name> -Confirm:$false
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Sharo\AI_projects\AlphaGraph_new"
$WebScraper  = Join-Path $ProjectRoot "tools\web_scraper"
$User        = $env:USERNAME

function Register-ResilientTask {
    param(
        [Parameter(Mandatory=$true)] [string]   $TaskName,
        [Parameter(Mandatory=$true)] [string]   $Description,
        [Parameter(Mandatory=$true)] [string]   $BatPath,
        [Parameter(Mandatory=$true)]             $Triggers,
        [Parameter(Mandatory=$false)][int]      $ExecutionTimeLimitMinutes = 0   # 0 = no limit
    )

    # Action: run the .bat via cmd.exe /c, working directory at project root.
    $Action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument "/c `"$BatPath`"" `
        -WorkingDirectory $ProjectRoot

    # Principal: run as the logged-in user, interactive (no stored password).
    $Principal = New-ScheduledTaskPrincipal `
        -UserId $User `
        -LogonType Interactive `
        -RunLevel Limited

    # Settings: the resilience knobs.
    $SettingsArgs = @{
        AllowStartIfOnBatteries         = $true
        DontStopIfGoingOnBatteries      = $true
        StartWhenAvailable              = $true   # catch up if missed (e.g. laptop was asleep)
        MultipleInstances               = "IgnoreNew"   # don't stack if one's still running
        RestartCount                    = 99
        RestartInterval                 = (New-TimeSpan -Minutes 5)
    }
    if ($ExecutionTimeLimitMinutes -gt 0) {
        $SettingsArgs.ExecutionTimeLimit = New-TimeSpan -Minutes $ExecutionTimeLimitMinutes
    } else {
        $SettingsArgs.ExecutionTimeLimit = (New-TimeSpan -Seconds 0)   # 0 = unlimited
    }
    $Settings = New-ScheduledTaskSettingsSet @SettingsArgs

    # Remove the existing task if present (so we can recreate cleanly).
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  [update] removing existing task $TaskName" -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    Register-ScheduledTask `
        -TaskName    $TaskName `
        -Description $Description `
        -Action      $Action `
        -Trigger     $Triggers `
        -Principal   $Principal `
        -Settings    $Settings | Out-Null

    Write-Host "  [ok] $TaskName registered" -ForegroundColor Green
}

# ===========================================================================
# 1. Social scheduler -- long-running, at logon, auto-restart
# ===========================================================================
Write-Host "`n[1/3] AlphaGraph_SocialScheduler" -ForegroundColor Cyan

$SocialTriggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $User
)
$SocialTriggers[0].Delay = "PT30S"   # 30 second delay so login is fully settled

Register-ResilientTask `
    -TaskName     "AlphaGraph_SocialScheduler" `
    -Description  "APScheduler: news / reddit / gpu / x / pcpartpicker -- with auto-restart on failure" `
    -BatPath      (Join-Path $WebScraper "run_social_scheduler.bat") `
    -Triggers     $SocialTriggers `
    -ExecutionTimeLimitMinutes 0

# ===========================================================================
# 2. Taiwan scheduler -- long-running, at logon, auto-restart
# ===========================================================================
Write-Host "`n[2/3] AlphaGraph_TaiwanScheduler" -ForegroundColor Cyan

$TaiwanTriggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $User
)
$TaiwanTriggers[0].Delay = "PT45S"   # stagger 15s after social to avoid Chrome port collision

Register-ResilientTask `
    -TaskName     "AlphaGraph_TaiwanScheduler" `
    -Description  "APScheduler: MOPS monthly revenue + material info + TPEx OpenAPI + weekly patches" `
    -BatPath      (Join-Path $WebScraper "run_taiwan_scheduler.bat") `
    -Triggers     $TaiwanTriggers `
    -ExecutionTimeLimitMinutes 0

# ===========================================================================
# 3. EDGAR daily -- one-shot, 06:00 Asia/Taipei = 6pm EDT (5pm EST in winter)
# ===========================================================================
Write-Host "`n[3/3] AlphaGraph_EdgarDaily" -ForegroundColor Cyan

# Pick an arbitrary past date so the daily trigger just uses the time portion.
$EdgarTriggers = @(
    New-ScheduledTaskTrigger -Daily -At "6:00AM"
)

Register-ResilientTask `
    -TaskName     "AlphaGraph_EdgarDaily" `
    -Description  "Daily EDGAR topline refresh -- detects new 10-K/10-Q filings, rebuilds changed tickers" `
    -BatPath      (Join-Path $WebScraper "run_edgar_refresh.bat") `
    -Triggers     $EdgarTriggers `
    -ExecutionTimeLimitMinutes 180

Write-Host "`n------------------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "All three AlphaGraph tasks registered. Verify with:" -ForegroundColor Gray
Write-Host "  Get-ScheduledTask AlphaGraph_* | Get-ScheduledTaskInfo" -ForegroundColor Gray
Write-Host "`nNotes:" -ForegroundColor Gray
Write-Host "  - Social + Taiwan start at logon (30s / 45s delay) and restart every 5-min up to 99 times on crash." -ForegroundColor Gray
Write-Host "  - EDGAR fires daily at 06:00 local (= 6pm EDT / 5pm EST)." -ForegroundColor Gray
Write-Host "  - Log files: logs\social_scheduler.log, logs\taiwan_scheduler.log, logs\edgar_refresh.log" -ForegroundColor Gray
