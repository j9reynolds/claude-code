<#
=====================================================================================
 USPS GEGW Self Report — monthly runner   (extract -> generator -> styled workbook)
-------------------------------------------------------------------------------------
 Runs on the Delta network (a host that can reach McLeod DB02 and has Python 3 + the
 SqlServer PowerShell module). One scheduled run per month produces the filled,
 styled .xlsx for the account owner to review and send.

 Steps:
   1. Invoke-Sqlcmd runs usps_selfreport_extract.sql against DB02/LME_1720 (the SQL
      itself scopes to LAST calendar month), exporting the per-load rows to a CSV.
   2. usps_selfreport_pipeline.py turns that CSV into the 3-tab, styled workbook.
   3. Output lands in -OutDir, named like the filed report.

 Schedule it (Task Scheduler, 6 AM on the 1st):
   schtasks /Create /TN "USPS Self Report" /SC MONTHLY /D 1 /ST 06:00 ^
     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\run_monthly.ps1"

 Prereqs: Import-Module SqlServer  (Install-Module SqlServer -Scope CurrentUser once);
          python on PATH; run_monthly.ps1 next to usps_selfreport_extract.sql and
          usps_selfreport_pipeline.py.
=====================================================================================
#>
param(
  [string]$Server  = "DB02",
  [string]$Database = "LME_1720",
  [string]$Program  = "RTH",                                  # title label on the Overview tabs
  [string]$OutDir   = "$PSScriptRoot\output",
  [string]$Month    = ""                                      # optional YYYY-MM label override
)

$ErrorActionPreference = "Stop"
$here     = $PSScriptRoot
$sqlFile  = Join-Path $here "usps_selfreport_extract.sql"
$pipeline = Join-Path $here "usps_selfreport_pipeline.py"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# Month label: the SQL always pulls LAST calendar month, so default the label to that too.
if (-not $Month) { $Month = (Get-Date).AddMonths(-1).ToString("yyyy-MM") }
$monLabel = [datetime]::ParseExact($Month, "yyyy-MM", $null).ToString("MMM yyyy")   # e.g. "Aug 2026"
$stamp    = Get-Date -Format "yyyyMMdd_HHmmss"
$rawCsv   = Join-Path $OutDir "rawrows_$stamp.csv"
$outXlsx  = Join-Path $OutDir "0029H Self Report - Delta Group Logistics - $monLabel.xlsx"
$log      = Join-Path $OutDir "run_$stamp.log"

"[{0}] extract: {1}\{2} <- {3}" -f (Get-Date), $Server, $Database, $sqlFile | Tee-Object -FilePath $log -Append

# 1) EXTRACT — Invoke-Sqlcmd + Export-Csv handles quoting of commas in lane names cleanly.
Import-Module SqlServer -ErrorAction Stop
Invoke-Sqlcmd -ServerInstance $Server -Database $Database -InputFile $sqlFile -TrustServerCertificate `
  | Export-Csv -Path $rawCsv -NoTypeInformation -Encoding UTF8
$rows = (Import-Csv $rawCsv).Count
"[{0}] extracted {1} rows -> {2}" -f (Get-Date), $rows, $rawCsv | Tee-Object -FilePath $log -Append
if ($rows -eq 0) { throw "No rows returned from the extract — aborting (check the month/customer)." }

# 2) GENERATE + STYLE
"[{0}] generate: {1}" -f (Get-Date), $outXlsx | Tee-Object -FilePath $log -Append
& python $pipeline $rawCsv $Month $Program $outXlsx 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { throw "pipeline failed (exit $LASTEXITCODE) — see $log" }

"[{0}] DONE. Review then send: {1}" -f (Get-Date), $outXlsx | Tee-Object -FilePath $log -Append
Write-Host "`nReport ready for review:`n  $outXlsx"
