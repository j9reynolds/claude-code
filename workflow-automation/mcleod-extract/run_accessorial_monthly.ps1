<#
=====================================================================================
 Accessorial Shadow Report - monthly runner   (extract -> analyze -> styled workbook)
-------------------------------------------------------------------------------------
 READ-ONLY. Runs on a Delta host that can reach McLeod DB02 and has Python 3 + the
 SqlServer PowerShell module (PowerShell 7 / pwsh, where that module is installed).
 One scheduled run per month produces a review workbook of accessorial "money left on
 the table" for the prior calendar month, ALL customers. It reports only - it never
 bills, pays, or writes to McLeod.

 Steps:
   1. Invoke-Sqlcmd runs mcleod_accessorial_monthly.sql (READ-ONLY; scopes to LAST
      calendar month). Its five result sets are split into five CSVs via -OutputAs
      DataTables (loads / othercharges / carrierpay / chargecodes / stops).
   2. accessorial_shadow_report.py turns those CSVs into the styled review workbook.
   3. Output lands in -OutDir, named "Accessorial Shadow Report - <Mon YYYY>.xlsx".
   4. EMAIL the workbook to -EmailTo for review (guarded; logs+continues if mail is not
      ready). Sent from THIS host via classic Outlook COM (needs a NON-elevated run + a
      signed-in classic Outlook profile) or an SMTP relay - NOT the Claude connector.
   5. Optional SharePoint drop via -SharePointDir (guarded Copy-Item on this host).

 Schedule (Task Scheduler, run only when logged on, non-elevated) - use a .cmd wrapper
 that calls pwsh so the WindowsApps path with spaces does not break schtasks:
   run_accessorial.cmd:
     @echo off
     pwsh -NoProfile -ExecutionPolicy Bypass -File C:\path\run_accessorial_monthly.ps1
   schtasks /Create /TN "Accessorial Shadow Report" /SC MONTHLY /D 2 /ST 06:30 ^
     /TR "C:\path\run_accessorial.cmd"
 (Day 2 so the prior month is fully billed; adjust to taste.)

 Prereqs: Import-Module SqlServer (PowerShell 7); python on PATH; the SQL + the three
 python files (accessorial_shadow_report.py, accessorial_workbook.py, analyze_leakage.py)
 co-located with this script.
=====================================================================================
#>
param(
  [string]$Server    = "DB02",
  [string]$Database   = "LME_1720",
  [string]$OutDir     = "$PSScriptRoot\output",
  [string]$Month      = "",                                   # optional YYYY-MM override (label only)
  [ValidateSet("Outlook","Smtp","None")]
  [string]$MailMethod = "Outlook",
  [string]$EmailTo    = "J.Reynolds@DeltaGroupLog.com",       # you review before any action
  [string]$EmailCc    = "",
  [string]$EmailFrom  = "J.Reynolds@DeltaGroupLog.com",       # Smtp method only
  [string]$SmtpServer = "",                                   # Smtp method only
  [string]$SharePointDir = ""                                 # blank = skip the drop
)

$ErrorActionPreference = "Stop"
$here    = $PSScriptRoot
$sqlFile = Join-Path $here "mcleod_accessorial_monthly.sql"
$py      = Join-Path $here "accessorial_shadow_report.py"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not $Month) { $Month = (Get-Date).AddMonths(-1).ToString("yyyy-MM") }
$monLabel = [datetime]::ParseExact($Month, "yyyy-MM", $null).ToString("MMM yyyy")   # "Aug 2026"
$stamp    = Get-Date -Format "yyyyMMdd_HHmmss"
$log      = Join-Path $OutDir "run_$stamp.log"
$outXlsx  = Join-Path $OutDir "Accessorial Shadow Report - $monLabel.xlsx"

"[{0}] extract (READ-ONLY): {1}\{2} <- {3}" -f (Get-Date), $Server, $Database, $sqlFile |
  Tee-Object -FilePath $log -Append

# 1) EXTRACT - one batch, five result sets -> five CSVs (order matches Query A..E).
Import-Module SqlServer -ErrorAction Stop
$tables = Invoke-Sqlcmd -ServerInstance $Server -Database $Database -InputFile $sqlFile `
            -TrustServerCertificate -OutputAs DataTables
if ($tables.Count -lt 5) { throw "expected 5 result sets, got $($tables.Count) - check the SQL" }

$csv = @{
  loads        = Join-Path $OutDir "loads_$stamp.csv"
  othercharges = Join-Path $OutDir "othercharges_$stamp.csv"
  carrierpay   = Join-Path $OutDir "carrierpay_$stamp.csv"
  chargecodes  = Join-Path $OutDir "chargecodes_$stamp.csv"
  stops        = Join-Path $OutDir "stops_$stamp.csv"
}
$tables[0] | Export-Csv -Path $csv.loads        -NoTypeInformation -Encoding UTF8
$tables[1] | Export-Csv -Path $csv.othercharges -NoTypeInformation -Encoding UTF8
$tables[2] | Export-Csv -Path $csv.carrierpay   -NoTypeInformation -Encoding UTF8
$tables[3] | Export-Csv -Path $csv.chargecodes  -NoTypeInformation -Encoding UTF8
$tables[4] | Export-Csv -Path $csv.stops        -NoTypeInformation -Encoding UTF8
$nLoads = $tables[0].Rows.Count
"[{0}] extracted {1} delivered loads for {2}" -f (Get-Date), $nLoads, $monLabel |
  Tee-Object -FilePath $log -Append
if ($nLoads -eq 0) { throw "No delivered loads for $monLabel - aborting (check the month)." }

# 2) ANALYZE + STYLE -> workbook
& python $py $csv.loads $csv.othercharges $csv.carrierpay $csv.chargecodes $csv.stops `
    $Month $outXlsx 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { throw "generator failed (exit $LASTEXITCODE) - see $log" }
if (-not (Test-Path $outXlsx)) { throw "expected workbook not found: $outXlsx (see $log)" }
"[{0}] workbook ready: {1}" -f (Get-Date), $outXlsx | Tee-Object -FilePath $log -Append

# 3) EMAIL (guarded)
if ($MailMethod -ne "None") {
  $subject = "Accessorial Shadow Report - $monLabel"
  $body    = "Read-only accessorial shadow report for $monLabel is attached (money left on " +
             "the table: un-billed detention, accessorial margin, rate-con gap). Review before " +
             "acting - nothing was billed, paid, or written. Generated $((Get-Date).ToString('g'))."
  try {
    if ($MailMethod -eq "Outlook") {
      $ol = New-Object -ComObject Outlook.Application
      $mail = $ol.CreateItem(0)
      $mail.To = $EmailTo
      if ($EmailCc) { $mail.CC = $EmailCc }
      $mail.Subject = $subject
      $mail.Body    = $body
      $mail.Attachments.Add($outXlsx) | Out-Null
      $mail.Send()
    }
    elseif ($MailMethod -eq "Smtp") {
      if (-not $SmtpServer) { throw "MailMethod=Smtp requires -SmtpServer" }
      $cc = if ($EmailCc) { $EmailCc -split '\s*,\s*' } else { $null }
      Send-MailMessage -To $EmailTo -Cc $cc -From $EmailFrom -Subject $subject `
        -Body $body -Attachments $outXlsx -SmtpServer $SmtpServer
    }
    "[{0}] emailed via {1} to {2}" -f (Get-Date), $MailMethod, $EmailTo | Tee-Object -FilePath $log -Append
  }
  catch {
    "[{0}] EMAIL SKIPPED ({1}): {2}. Workbook ready for manual send: {3}" `
      -f (Get-Date), $MailMethod, $_.Exception.Message, $outXlsx | Tee-Object -FilePath $log -Append
    Write-Warning "Email not sent ($($_.Exception.Message)). Send manually: $outXlsx"
  }
}

# 4) SHAREPOINT DROP (guarded, optional)
if ($SharePointDir) {
  try {
    if (-not (Test-Path -LiteralPath $SharePointDir)) {
      New-Item -ItemType Directory -Force -Path $SharePointDir | Out-Null
    }
    $spTarget = Join-Path $SharePointDir ("Accessorial Shadow Report - $monLabel.xlsx")
    Copy-Item -LiteralPath $outXlsx -Destination $spTarget -Force
    "[{0}] copied to SharePoint folder: {1}" -f (Get-Date), $spTarget | Tee-Object -FilePath $log -Append
  }
  catch {
    "[{0}] SHAREPOINT DROP SKIPPED: {1}. Workbook remains at: {2}" `
      -f (Get-Date), $_.Exception.Message, $outXlsx | Tee-Object -FilePath $log -Append
    Write-Warning "SharePoint drop failed ($($_.Exception.Message)). File is at: $outXlsx"
  }
}

Write-Host "`nAccessorial shadow report ready for review:`n  $outXlsx"
