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
   4. EMAIL the workbook to -EmailTo (default: you). Sent from THIS host — NOT the Claude
      M365 connector — via Outlook desktop (default) or an SMTP relay. It is GUARDED: if the
      mail path is not ready, it logs a warning and still leaves the file for manual send, so
      the report starts arriving by email automatically the first month mail works.
   5. DROP a copy into the SharePoint USPS folder via -SharePointDir. This is a plain
      filesystem copy on THIS host — NOT the Claude M365 connector (which is read-only) — so
      point it at the LOCALLY-SYNCED path of the SharePoint "USPS" library (OneDrive sync
      client) or a mapped/UNC path to it. It is GUARDED the same way as email: if the folder
      is unset or unreachable, it logs a warning and continues, leaving the file in -OutDir.

 Schedule it (Task Scheduler, 6 AM on the 1st):
   schtasks /Create /TN "USPS Self Report" /SC MONTHLY /D 1 /ST 06:00 ^
     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\run_monthly.ps1"

 Prereqs: Import-Module SqlServer  (Install-Module SqlServer -Scope CurrentUser once);
          python on PATH; the three files co-located.
 Email prereq (pick one):
   * Outlook (default): the scheduled task runs as a user with Outlook installed + a signed-in
     profile (so the "once M365 is fixed" concern = get that mailbox working on the host).
   * SMTP: run with -MailMethod Smtp -SmtpServer <relay> (and -EmailFrom).
   * Disable email entirely with -MailMethod None (produces the file only).
 Recipient defaults to you for review; add K.Cash etc. via -EmailCc only when you want it to
 go straight out (auto-sending a partner report unreviewed is riskier — left off by default).
 SharePoint drop prereq: run with -SharePointDir pointing at the USPS library's path ON THIS
   HOST — either the OneDrive sync-client folder (e.g.
   "C:\Users\<you>\Delta Freight Systems\DeltaGroup - USPS Monthly Reporting") or a mapped/UNC
   path to it. Left blank => the drop is skipped (file still lands in -OutDir).
=====================================================================================
#>
param(
  [string]$Server   = "DB02",
  [string]$Database  = "LME_1720",
  [string]$Program   = "RTH",                                 # title label on the Overview tabs
  [string]$OutDir    = "$PSScriptRoot\output",
  [string]$Month     = "",                                    # optional YYYY-MM label override
  # ---- email delivery (runs on THIS host, not the Claude M365 connector) ----
  [ValidateSet("Outlook","Smtp","None")]
  [string]$MailMethod = "Outlook",                            # Outlook desktop COM (default), SMTP relay, or None
  [string]$EmailTo    = "J.Reynolds@DeltaGroupLog.com",       # recipient — you review, then forward to J.B. Hunt
  [string]$EmailCc    = "",                                   # optional, comma-separated
  [string]$EmailFrom  = "J.Reynolds@DeltaGroupLog.com",       # used only by the Smtp method
  [string]$SmtpServer = "",                                   # required only by the Smtp method
  # ---- SharePoint drop (a plain filesystem copy on THIS host, not the Claude M365 connector) ----
  [string]$SharePointDir = ""                                 # locally-synced USPS library path or UNC; blank = skip
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
# The pipeline names the workbook canonically from the data month; this is the expected path.
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

# 2) GENERATE + STYLE — pass the FOLDER; the pipeline names the file canonically for $Month.
"[{0}] generate into: {1}" -f (Get-Date), $OutDir | Tee-Object -FilePath $log -Append
& python $pipeline $rawCsv $Month $Program $OutDir 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { throw "pipeline failed (exit $LASTEXITCODE) — see $log" }
if (-not (Test-Path $outXlsx)) { throw "expected workbook not found: $outXlsx (see $log)" }

"[{0}] workbook ready: {1}" -f (Get-Date), $outXlsx | Tee-Object -FilePath $log -Append

# 3) EMAIL — deliver the workbook to $EmailTo. Guarded: if the mail path is not ready yet
#    (e.g. M365/Outlook not configured on this host), log a warning and continue — the file is
#    still produced for manual send, and the next run emails automatically once mail works.
if ($MailMethod -ne "None") {
  $subject = "USPS GEGW Self Report - $monLabel"
  $body    = "Automated USPS GEGW Self Report for $monLabel is attached (0029H). " +
             "Please review before forwarding to J.B. Hunt. Generated $((Get-Date).ToString('g'))."
  try {
    if ($MailMethod -eq "Outlook") {
      $ol   = New-Object -ComObject Outlook.Application
      $mail = $ol.CreateItem(0)                      # olMailItem
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
    Write-Host "`nReport sent to $EmailTo`n  $outXlsx"
  }
  catch {
    "[{0}] EMAIL SKIPPED ({1}): {2}. Workbook is ready for manual send: {3}" `
      -f (Get-Date), $MailMethod, $_.Exception.Message, $outXlsx | Tee-Object -FilePath $log -Append
    Write-Warning "Email not sent yet ($($_.Exception.Message)). Send manually for now: $outXlsx"
  }
}
else {
  "[{0}] DONE. Review then send: {1}" -f (Get-Date), $outXlsx | Tee-Object -FilePath $log -Append
  Write-Host "`nReport ready for review:`n  $outXlsx"
}

# 4) SHAREPOINT DROP — copy the workbook into the SharePoint USPS folder. This is a plain
#    Copy-Item on THIS host (the Claude M365 connector is read-only), so -SharePointDir must be
#    a filesystem path: the OneDrive-synced path of the SharePoint "USPS" library, or a mapped/
#    UNC path to it. GUARDED like the email step: if unset or unreachable, log and continue so
#    the run never fails just because the drop location isn't configured yet.
if ($SharePointDir) {
  try {
    if (-not (Test-Path -LiteralPath $SharePointDir)) {
      New-Item -ItemType Directory -Force -Path $SharePointDir | Out-Null
    }
    $spTarget = Join-Path $SharePointDir ("0029H Self Report - Delta Group Logistics - $monLabel.xlsx")
    Copy-Item -LiteralPath $outXlsx -Destination $spTarget -Force
    "[{0}] copied to SharePoint folder: {1}" -f (Get-Date), $spTarget | Tee-Object -FilePath $log -Append
    Write-Host "Dropped into SharePoint USPS folder:`n  $spTarget"
  }
  catch {
    "[{0}] SHAREPOINT DROP SKIPPED: {1}. Workbook remains at: {2}" `
      -f (Get-Date), $_.Exception.Message, $outXlsx | Tee-Object -FilePath $log -Append
    Write-Warning "SharePoint drop failed ($($_.Exception.Message)). File is still at: $outXlsx"
  }
}
else {
  "[{0}] SharePoint drop skipped (-SharePointDir not set)." -f (Get-Date) | Tee-Object -FilePath $log -Append
}
