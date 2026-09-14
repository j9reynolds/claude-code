@echo off
rem Task Scheduler wrapper for the Accessorial Shadow Report.
rem Calls pwsh (PowerShell 7) by its short alias so the Store-app path with spaces
rem does not break schtasks. Runs the monthly runner with defaults: emails the
rem workbook to J.Reynolds via classic Outlook (must run NON-elevated, as J.Reynolds,
rem with a signed-in classic Outlook profile). Read-only; nothing is billed or paid.
rem Adjust the path below if the files live somewhere other than this folder.
pwsh -NoProfile -ExecutionPolicy Bypass -File "D:\Project Folder\Automation\Accessorial\run_accessorial_monthly.ps1"
