<#
=====================================================================================
 Remove-DuplicateMessages.ps1 - find and remove duplicate mail in Exchange Online
-------------------------------------------------------------------------------------
 Groups messages by a duplicate key, keeps exactly ONE message per group, and reports,
 quarantines, or deletes the rest. Report-only by default: it changes nothing until you
 pass -Action with something other than Report.

 DO NOT RUN THIS AGAINST AN EMAILAGENT INTAKE QUEUE (JR_Test@DeltaGroupLog.com)
   This picks its candidates from a MAILBOX LISTING. The intake pipeline's own tool,
   etl/Remove-IngestedJrTestMail.ps1 in dgl-command-center, picks its candidates from the
   DATABASE (intake.Request rows), so un-ingested mail is untouchable by construction rather
   than merely checked for - the stronger guarantee, and the right one on a queue mailbox.
   Duplicates there are a missing-identity problem in the pipeline (schema v39), not a
   mailbox-hygiene problem. See ../email-ingestion/ARCHITECTURE.md.
   This script is for an ad-hoc pass over a human's own mailbox, where no pipeline owns the
   mail and there is no database to ask.

 WHY MICROSOFT GRAPH AND NOT EXCHANGE ONLINE POWERSHELL
   Search-Mailbox was retired, and Compliance Search + New-ComplianceSearchAction -Purge
   is all-or-nothing over a query - it cannot keep one copy and drop the rest. Per-message
   control needs Graph. Graph is also the only path that works from a locked-down host:
   HTTPS/443 only, modern auth, no basic auth (this tenant has basic auth off - IMAP with
   an app password is refused however correct the credential is).

 WHAT COUNTS AS A DUPLICATE
   -DuplicateKey MessageId  (default) groups on internetMessageId, the RFC822 Message-ID
     with the angle brackets stripped and lowercased. This is the identity that survives a
     move between folders; the Graph per-message "id" does NOT (it is mailbox- and
     folder-scoped, so the same mail re-keys the moment a rule files it).
   -DuplicateKey Content    groups on a SHA256 of sender + normalized subject +
     receivedDateTime (to the second) + bodyPreview. Use this for copies that were re-sent
     rather than copied, so each carries its own distinct Message-ID.

 SCOPE - READ THIS BEFORE USING PerMailbox
   -Scope PerFolder  (default) only treats messages as duplicates when they sit in the SAME
     folder. This is the safe setting: it removes true repeats and never collapses a
     deliberate filed copy.
   -Scope PerMailbox treats every copy across all included folders as one group and keeps a
     single survivor. That is what you want for "this mailbox has the same mail in six
     places", but it WILL collapse copies someone filed on purpose. Report first.

 EXCLUDED BY DEFAULT
   Drafts, Outbox, Sent Items, Deleted Items, Junk Email, Conversation History, Sync Issues
   (and the quarantine folder itself). Sent Items shares Message-IDs with Inbox copies of
   your own mail by design, so including it invites false positives. Draft messages are
   skipped outright.

 REQUIREMENTS
   PowerShell 5.1 or later (PowerShell 7 recommended) and the Graph SDK auth module:
     Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
   Delegated scopes: Mail.ReadWrite for your own mailbox; Mail.ReadWrite.Shared plus Full
   Access on the target for a shared/other mailbox. Missing access returns a permission
   error, not an empty result - never read a failure as a quiet zero.

 TYPICAL RUN (in this order, do not skip step 1)
   1. .\Remove-DuplicateMessages.ps1 -Mailbox ops@example.com -Since 2026-01-01
   2. review the CSV it writes, then
   3. .\Remove-DuplicateMessages.ps1 -Mailbox ops@example.com -Since 2026-01-01 -Action Quarantine
   4. confirm the quarantine folder looks right, then empty it by hand (or re-run with
      -Action Delete once you trust the match).
=====================================================================================
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
  # Mailbox to clean. Omit for the signed-in user.
  [string]$Mailbox = "",

  # Only consider mail received on/after this date (recommended - it bounds the blast radius).
  [datetime]$Since,

  # Only consider mail received before this date.
  [datetime]$Before,

  [ValidateSet("MessageId", "Content")]
  [string]$DuplicateKey = "MessageId",

  [ValidateSet("PerFolder", "PerMailbox")]
  [string]$Scope = "PerFolder",

  # Report = write the CSV and change nothing. Quarantine = move copies to -QuarantineFolder.
  # Delete = Graph delete (lands in Deleted Items, user-recoverable).
  # Purge  = delete, then delete again out of Deleted Items into Recoverable Items. Still
  #          admin/user-recoverable for the retention window - this is NOT an unrecoverable
  #          wipe, and it requires -Force.
  [ValidateSet("Report", "Quarantine", "Delete", "Purge")]
  [string]$Action = "Report",

  # Which copy survives each group.
  [ValidateSet("Oldest", "Newest")]
  [string]$Keep = "Oldest",

  [string]$QuarantineFolder = "Duplicates - Quarantine",

  # Restrict to these folder display names (default: every folder except the exclusions).
  [string[]]$IncludeFolders = @(),

  [string[]]$ExcludeFolders = @(
    "Drafts", "Outbox", "Sent Items", "Deleted Items", "Junk Email",
    "Conversation History", "Sync Issues", "Recoverable Items"
  ),

  # Hard cap on messages examined. The run stops and says so rather than silently truncating.
  [int]$MaxMessages = 50000,

  [string]$ReportPath = "",

  # Required for -Action Purge.
  [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------- helpers

function Get-UserPath {
  if ([string]::IsNullOrWhiteSpace($Mailbox)) { return "/me" }
  return "/users/$([uri]::EscapeDataString($Mailbox))"
}

function Invoke-Graph {
  param(
    [Parameter(Mandatory = $true)][string]$Method,
    [Parameter(Mandatory = $true)][string]$Uri,
    [hashtable]$Body,
    [int]$MaxRetries = 5
  )
  $attempt = 0
  while ($true) {
    try {
      $req = @{ Method = $Method; Uri = $Uri; OutputType = "PSObject" }
      if ($Body) { $req["Body"] = ($Body | ConvertTo-Json -Depth 5 -Compress) }
      return Invoke-MgGraphRequest @req
    }
    catch {
      $resp = $null
      try { $resp = $_.Exception.Response } catch { $resp = $null }
      $status = 0
      if ($resp -and $resp.PSObject.Properties.Name -contains "StatusCode") {
        $status = [int]$resp.StatusCode
      }
      # 429 = throttled, 503/504 = transient. Honour Retry-After when Graph sends one.
      if (($status -eq 429 -or $status -eq 503 -or $status -eq 504) -and $attempt -lt $MaxRetries) {
        $wait = [math]::Pow(2, $attempt) * 2
        try {
          $ra = $resp.Headers["Retry-After"]
          if ($ra) { $wait = [double]$ra }
        }
        catch { }
        Write-Warning ("Graph returned {0}; retrying in {1}s (attempt {2}/{3})" -f $status, $wait, ($attempt + 1), $MaxRetries)
        Start-Sleep -Seconds $wait
        $attempt++
        continue
      }
      throw
    }
  }
}

function Get-AllPages {
  param([Parameter(Mandatory = $true)][string]$Uri)
  $items = @()
  $next = $Uri
  while ($next) {
    $page = Invoke-Graph -Method GET -Uri $next
    if ($page.PSObject.Properties.Name -contains "value" -and $page.value) { $items += $page.value }
    $next = $null
    if ($page.PSObject.Properties.Name -contains "@odata.nextLink") { $next = $page."@odata.nextLink" }
    if (($items.Count + $script:Examined) -ge $MaxMessages) {
      Write-Warning ("Hit -MaxMessages ({0}). Results are TRUNCATED - narrow -Since/-Before or raise the cap." -f $MaxMessages)
      $script:Truncated = $true
      break
    }
  }
  return $items
}

function Get-MailFolderTree {
  # Recursive walk. Graph only returns one level of childFolders per call.
  param([string]$ParentId = "")
  $base = Get-UserPath
  $uri = if ($ParentId) {
    "https://graph.microsoft.com/v1.0$base/mailFolders/$ParentId/childFolders?`$top=100"
  }
  else {
    "https://graph.microsoft.com/v1.0$base/mailFolders?`$top=100&includeHiddenFolders=true"
  }
  $out = @()
  foreach ($f in (Get-AllPages -Uri $uri)) {
    $out += [pscustomobject]@{
      Id           = $f.id
      DisplayName  = $f.displayName
      TotalItems   = $f.totalItemCount
      ChildCount   = $f.childFolderCount
    }
    if ($f.childFolderCount -gt 0) { $out += Get-MailFolderTree -ParentId $f.id }
  }
  return $out
}

function Get-NormalizedSubject {
  param([string]$Subject)
  if (-not $Subject) { return "" }
  $s = $Subject.Trim()
  # Strip stacked Re:/Fw:/Fwd: prefixes so a forwarded copy still matches.
  while ($s -match '^\s*(re|fw|fwd)\s*(\[\d+\])?\s*:\s*(.*)$') { $s = $Matches[3].Trim() }
  return ($s -replace '\s+', ' ').ToLowerInvariant()
}

function Get-DuplicateKeyValue {
  param([Parameter(Mandatory = $true)]$Message)

  if ($DuplicateKey -eq "MessageId") {
    $imid = ""
    if ($Message.PSObject.Properties.Name -contains "internetMessageId") { $imid = [string]$Message.internetMessageId }
    if ([string]::IsNullOrWhiteSpace($imid)) { return $null }   # no key -> never grouped, never removed
    return $imid.Trim().Trim('<', '>').ToLowerInvariant()
  }

  $from = ""
  try { $from = [string]$Message.from.emailAddress.address } catch { $from = "" }
  $recv = ""
  if ($Message.receivedDateTime) { $recv = ([datetime]$Message.receivedDateTime).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
  $preview = ""
  if ($Message.PSObject.Properties.Name -contains "bodyPreview" -and $Message.bodyPreview) {
    $preview = ($Message.bodyPreview -replace '\s+', ' ').Trim()
  }
  $material = "{0}|{1}|{2}|{3}" -f $from.ToLowerInvariant(), (Get-NormalizedSubject $Message.subject), $recv, $preview
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($material))
    return ([System.BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Resolve-QuarantineFolderId {
  param([Parameter(Mandatory = $true)]$Folders)
  $existing = $Folders | Where-Object { $_.DisplayName -eq $QuarantineFolder } | Select-Object -First 1
  if ($existing) { return $existing.Id }
  $base = Get-UserPath
  $created = Invoke-Graph -Method POST `
    -Uri "https://graph.microsoft.com/v1.0$base/mailFolders" `
    -Body @{ displayName = $QuarantineFolder }
  Write-Host ("Created quarantine folder '{0}'." -f $QuarantineFolder) -ForegroundColor Cyan
  return $created.id
}

function New-RemovalPlan {
  <#
    Turns duplicate groups into a flat plan: exactly one "Keep" row per group, every other
    copy a "Remove" row. This is the only place that decides what dies, so it is kept pure
    (no Graph calls, no state) and unit-tested by test_Remove-DuplicateMessages.ps1.
  #>
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()]$Groups,
    [ValidateSet("Oldest", "Newest")][string]$KeepRule = "Oldest"
  )
  $rows = @()
  foreach ($g in $Groups) {
    # Ties on Received are broken by Id so the survivor is deterministic across runs.
    $ordered = if ($KeepRule -eq "Oldest") {
      $g.Group | Sort-Object -Property @{ Expression = "Received"; Descending = $false }, @{ Expression = "Id"; Descending = $false }
    }
    else {
      $g.Group | Sort-Object -Property @{ Expression = "Received"; Descending = $true }, @{ Expression = "Id"; Descending = $false }
    }
    $survivor = $ordered | Select-Object -First 1
    $victims = $ordered | Select-Object -Skip 1

    $rows += [pscustomobject]@{
      Disposition = "Keep"; GroupKey = $g.Name; MessageId = $survivor.Id
      Folder = $survivor.FolderName; Subject = $survivor.Subject; From = $survivor.From
      Received = $survivor.Received; InternetMessageId = $survivor.InternetMessageId
      HasAttachments = $survivor.HasAttachments
    }
    foreach ($v in $victims) {
      $rows += [pscustomobject]@{
        Disposition = "Remove"; GroupKey = $g.Name; MessageId = $v.Id
        Folder = $v.FolderName; Subject = $v.Subject; From = $v.From
        Received = $v.Received; InternetMessageId = $v.InternetMessageId
        HasAttachments = $v.HasAttachments
      }
    }
  }
  return $rows
}

# --------------------------------------------------------------------------- preflight

if ($Action -eq "Purge" -and -not $Force) {
  throw "-Action Purge removes copies from Deleted Items into Recoverable Items. Re-run with -Force once you have reviewed the report."
}

if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
  throw "Microsoft.Graph.Authentication is not installed. Run: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser"
}
Import-Module Microsoft.Graph.Authentication -ErrorAction Stop

$ctx = $null
try { $ctx = Get-MgContext } catch { $ctx = $null }
if (-not $ctx) {
  $scopes = @("Mail.ReadWrite")
  if ($Mailbox) { $scopes += "Mail.ReadWrite.Shared" }
  Write-Host ("Connecting to Graph (scopes: {0})..." -f ($scopes -join ", ")) -ForegroundColor Cyan
  Connect-MgGraph -Scopes $scopes -NoWelcome
  $ctx = Get-MgContext
}
$who = if ($Mailbox) { $Mailbox } else { $ctx.Account }
Write-Host ("Mailbox: {0}   Key: {1}   Scope: {2}   Action: {3}   Keep: {4}" -f $who, $DuplicateKey, $Scope, $Action, $Keep) -ForegroundColor Cyan

# --------------------------------------------------------------------------- enumerate

$folders = Get-MailFolderTree
$excluded = @($ExcludeFolders) + @($QuarantineFolder)
$targets = $folders | Where-Object { $excluded -notcontains $_.DisplayName }
if ($IncludeFolders.Count -gt 0) {
  $targets = $targets | Where-Object { $IncludeFolders -contains $_.DisplayName }
}
if (-not $targets) { throw "No folders matched. Check -IncludeFolders / -ExcludeFolders against the mailbox." }

$filters = @()
if ($PSBoundParameters.ContainsKey("Since"))  { $filters += "receivedDateTime ge $($Since.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" }
if ($PSBoundParameters.ContainsKey("Before")) { $filters += "receivedDateTime lt $($Before.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" }
$filterQs = if ($filters.Count -gt 0) { "&`$filter=" + [uri]::EscapeDataString(($filters -join " and ")) } else { "" }

$script:Examined = 0
$script:Truncated = $false
$select = "id,internetMessageId,subject,from,receivedDateTime,hasAttachments,bodyPreview,isDraft,parentFolderId"
$base = Get-UserPath
$messages = @()

foreach ($f in $targets) {
  if ($script:Truncated) { break }
  Write-Host ("  scanning {0} ({1} items)" -f $f.DisplayName, $f.TotalItems)
  $uri = "https://graph.microsoft.com/v1.0$base/mailFolders/$($f.Id)/messages?`$select=$select&`$top=100$filterQs"
  foreach ($m in (Get-AllPages -Uri $uri)) {
    if ($m.isDraft) { continue }
    $messages += [pscustomobject]@{
      Id                = $m.id
      InternetMessageId = $(if ($m.PSObject.Properties.Name -contains "internetMessageId") { $m.internetMessageId } else { "" })
      Subject           = $m.subject
      From              = $(try { $m.from.emailAddress.address } catch { "" })
      Received          = $(if ($m.receivedDateTime) { [datetime]$m.receivedDateTime } else { [datetime]::MinValue })
      HasAttachments    = $m.hasAttachments
      FolderId          = $f.Id
      FolderName        = $f.DisplayName
      Key               = (Get-DuplicateKeyValue -Message $m)
    }
    $script:Examined++
  }
}

if ($script:Truncated) {
  Write-Warning "The scan was truncated, so some duplicate groups may be incomplete. Treat this run as a partial view."
}

Write-Host ("Examined {0} messages across {1} folders." -f $messages.Count, @($targets).Count) -ForegroundColor Cyan

$keyed = $messages | Where-Object { $_.Key }
$noKey = @($messages).Count - @($keyed).Count
if ($noKey -gt 0) { Write-Warning ("{0} messages had no usable duplicate key and were left alone." -f $noKey) }

$groups = if ($Scope -eq "PerFolder") {
  $keyed | Group-Object -Property { "$($_.FolderId)|$($_.Key)" }
}
else {
  $keyed | Group-Object -Property Key
}
$dupeGroups = $groups | Where-Object { $_.Count -gt 1 }

# --------------------------------------------------------------------------- plan

$plan = @(New-RemovalPlan -Groups $dupeGroups -KeepRule $Keep)
$toRemove = @($plan | Where-Object { $_.Disposition -eq "Remove" })
Write-Host ("Duplicate groups: {0}   copies to remove: {1}   survivors kept: {2}" -f @($dupeGroups).Count, $toRemove.Count, @($dupeGroups).Count) -ForegroundColor Yellow

if (-not $ReportPath) {
  $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
  $safe = ($who -replace '[^a-zA-Z0-9._-]', '_')
  $ReportPath = Join-Path $PSScriptRoot ("duplicates-{0}-{1}.csv" -f $safe, $stamp)
}
$plan | Export-Csv -Path $ReportPath -NoTypeInformation -Encoding UTF8
Write-Host ("Report written: {0}" -f $ReportPath) -ForegroundColor Green

if ($Action -eq "Report") {
  Write-Host "Report-only run - nothing was changed. Review the CSV, then re-run with -Action Quarantine." -ForegroundColor Green
  return
}
if ($toRemove.Count -eq 0) { Write-Host "Nothing to remove." -ForegroundColor Green; return }

# --------------------------------------------------------------------------- act

$quarantineId = $null
if ($Action -eq "Quarantine") { $quarantineId = Resolve-QuarantineFolderId -Folders $folders }

$deletedItemsId = $null
if ($Action -eq "Purge") {
  $di = $folders | Where-Object { $_.DisplayName -eq "Deleted Items" } | Select-Object -First 1
  if ($di) { $deletedItemsId = $di.Id }
}

$done = 0; $failed = 0
foreach ($item in $toRemove) {
  $label = "[{0}] {1} ({2:yyyy-MM-dd})" -f $item.Folder, $item.Subject, $item.Received
  if (-not $PSCmdlet.ShouldProcess($label, $Action)) { continue }
  try {
    switch ($Action) {
      "Quarantine" {
        Invoke-Graph -Method POST `
          -Uri "https://graph.microsoft.com/v1.0$base/messages/$($item.MessageId)/move" `
          -Body @{ destinationId = $quarantineId } | Out-Null
      }
      "Delete" {
        Invoke-Graph -Method DELETE -Uri "https://graph.microsoft.com/v1.0$base/messages/$($item.MessageId)" | Out-Null
      }
      "Purge" {
        # First delete lands the copy in Deleted Items and returns a NEW id there; the
        # second delete removes it from Deleted Items into Recoverable Items.
        $moved = Invoke-Graph -Method POST `
          -Uri "https://graph.microsoft.com/v1.0$base/messages/$($item.MessageId)/move" `
          -Body @{ destinationId = $(if ($deletedItemsId) { $deletedItemsId } else { "deleteditems" }) }
        Invoke-Graph -Method DELETE -Uri "https://graph.microsoft.com/v1.0$base/messages/$($moved.id)" | Out-Null
      }
    }
    $done++
    if ($done % 50 -eq 0) { Write-Host ("  {0}/{1}..." -f $done, $toRemove.Count) }
  }
  catch {
    $failed++
    Write-Warning ("FAILED {0}: {1}" -f $label, $_.Exception.Message)
  }
}

Write-Host ("{0}: {1} succeeded, {2} failed. Survivors untouched: {3}." -f $Action, $done, $failed, @($dupeGroups).Count) -ForegroundColor Green
if ($Action -eq "Quarantine") {
  Write-Host ("Copies are in '{0}'. Verify, then empty that folder by hand." -f $QuarantineFolder) -ForegroundColor Green
}
