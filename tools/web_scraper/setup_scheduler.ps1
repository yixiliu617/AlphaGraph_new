# AlphaGraph Scraper — Windows Task Scheduler Setup
# Run this script in PowerShell (as Admin) to create the scheduled task

$taskName = "AlphaGraph_Scraper"
$batPath = "C:\Users\Sharo\AI_projects\AlphaGraph_new\tools\web_scraper\scheduled_scrape.bat"

# Create action
$action = New-ScheduledTaskAction -Execute $batPath

# Create trigger: every 2 hours, starting now
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 365)

# Settings: run whether user is logged in, don't stop on idle
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Register the task
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "AlphaGraph: Scrape Google News + Reddit every 2 hours" -Force

Write-Host "Scheduled task '$taskName' created successfully!"
Write-Host "  Runs every 2 hours"
Write-Host "  Script: $batPath"
Write-Host "  Log: tools\web_scraper\scrape_log.txt"
Write-Host ""
Write-Host "To check status:  schtasks /query /tn AlphaGraph_Scraper"
Write-Host "To run now:       schtasks /run /tn AlphaGraph_Scraper"
Write-Host "To delete:        schtasks /delete /tn AlphaGraph_Scraper /f"
