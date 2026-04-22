' AlphaGraph Scheduled Scraper — runs silently (no popup window)
' This VBS wrapper launches the bat file hidden

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """C:\Users\Sharo\AI_projects\AlphaGraph_new\tools\web_scraper\scheduled_scrape.bat""", 0, True
