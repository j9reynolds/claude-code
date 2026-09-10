<#
=====================================================================================
 Tests for Remove-DuplicateMessages.ps1 - pure logic only, no Graph, no mailbox.
-------------------------------------------------------------------------------------
 The script is one self-contained file (house style), so these tests lift its function
 definitions out of the AST rather than dot-sourcing it, which would execute the whole
 run. Everything under test is pure: subject normalization, the duplicate key, and the
 keep/remove plan - the one piece of logic that decides which mail dies.

 Run:  pwsh -NoProfile -File .\test_Remove-DuplicateMessages.ps1
=====================================================================================
#>
$ErrorActionPreference = "Stop"

$target = Join-Path $PSScriptRoot "Remove-DuplicateMessages.ps1"
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($target, [ref]$null, [ref]$errors)
if ($errors) { throw "Script does not parse: $($errors[0].Message)" }

foreach ($name in @("Get-NormalizedSubject", "Get-DuplicateKeyValue", "New-RemovalPlan")) {
  $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
  if (-not $fn) { throw "Function $name not found in the script" }
  . ([scriptblock]::Create($fn[0].Extent.Text))
}

$script:Pass = 0
$script:Fail = 0
function Assert-Equal {
  param($Expected, $Actual, [string]$Because)
  if ("$Expected" -eq "$Actual") { $script:Pass++; Write-Host ("  ok   {0}" -f $Because) -ForegroundColor DarkGreen }
  else {
    $script:Fail++
    Write-Host ("  FAIL {0}`n       expected <{1}> got <{2}>" -f $Because, $Expected, $Actual) -ForegroundColor Red
  }
}

function New-TestMessage {
  param([string]$Id, [string]$Imid, [string]$Subject = "Rate con", [string]$From = "a@b.com",
        [datetime]$Received = ([datetime]"2026-01-01T00:00:00Z"), [string]$Folder = "Inbox")
  return [pscustomobject]@{
    Id = $Id; InternetMessageId = $Imid; Subject = $Subject; From = $From
    Received = $Received; HasAttachments = $false; FolderId = "f-$Folder"; FolderName = $Folder
    Key = $Imid.Trim().Trim('<', '>').ToLowerInvariant()
  }
}

# --------------------------------------------------------------- subject normalization
Write-Host "Get-NormalizedSubject" -ForegroundColor Cyan
Assert-Equal "load 4471 detention" (Get-NormalizedSubject "RE: Load 4471 Detention") "strips Re:"
Assert-Equal "load 4471 detention" (Get-NormalizedSubject "Fwd: RE:  Load 4471   Detention ") "strips stacked prefixes and collapses spaces"
Assert-Equal "load 4471" (Get-NormalizedSubject "FW[2]: Load 4471") "strips numbered Fw prefix"
Assert-Equal "" (Get-NormalizedSubject "") "empty subject is empty"

# ------------------------------------------------------------------------ duplicate key
Write-Host "Get-DuplicateKeyValue (MessageId)" -ForegroundColor Cyan
$DuplicateKey = "MessageId"
$a = [pscustomobject]@{ internetMessageId = "<ABC123@contoso.com>"; subject = "x"; receivedDateTime = "2026-01-01T00:00:00Z"; bodyPreview = "p" }
$b = [pscustomobject]@{ internetMessageId = "abc123@contoso.com";   subject = "y"; receivedDateTime = "2026-02-02T00:00:00Z"; bodyPreview = "q" }
Assert-Equal "abc123@contoso.com" (Get-DuplicateKeyValue -Message $a) "strips angle brackets and lowercases"
Assert-Equal (Get-DuplicateKeyValue -Message $a) (Get-DuplicateKeyValue -Message $b) "same Message-ID matches across folders regardless of other fields"

$noId = [pscustomobject]@{ internetMessageId = ""; subject = "x"; receivedDateTime = "2026-01-01T00:00:00Z"; bodyPreview = "p" }
Assert-Equal $null (Get-DuplicateKeyValue -Message $noId) "a message with no Message-ID gets no key (so it is never removed)"

Write-Host "Get-DuplicateKeyValue (Content)" -ForegroundColor Cyan
$DuplicateKey = "Content"
$c1 = [pscustomobject]@{ internetMessageId = "<1@x>"; subject = "RE: Load 900"; receivedDateTime = "2026-03-01T10:00:00Z"; bodyPreview = "rate is 1200"; from = [pscustomobject]@{ emailAddress = [pscustomobject]@{ address = "Dispatch@Carrier.com" } } }
$c2 = [pscustomobject]@{ internetMessageId = "<2@x>"; subject = "Load 900";     receivedDateTime = "2026-03-01T10:00:00Z"; bodyPreview = "rate is  1200 "; from = [pscustomobject]@{ emailAddress = [pscustomobject]@{ address = "dispatch@carrier.com" } } }
$c3 = [pscustomobject]@{ internetMessageId = "<3@x>"; subject = "Load 900";     receivedDateTime = "2026-03-01T10:00:01Z"; bodyPreview = "rate is 1200"; from = [pscustomobject]@{ emailAddress = [pscustomobject]@{ address = "dispatch@carrier.com" } } }
Assert-Equal (Get-DuplicateKeyValue -Message $c1) (Get-DuplicateKeyValue -Message $c2) "distinct Message-IDs still match on content (case, Re:, whitespace normalized)"
if ((Get-DuplicateKeyValue -Message $c1) -ne (Get-DuplicateKeyValue -Message $c3)) { $script:Pass++; Write-Host "  ok   a one-second difference in receivedDateTime is not a duplicate" -ForegroundColor DarkGreen }
else { $script:Fail++; Write-Host "  FAIL a one-second difference in receivedDateTime should not be a duplicate" -ForegroundColor Red }

# ------------------------------------------------------------------------ removal plan
Write-Host "New-RemovalPlan" -ForegroundColor Cyan
$msgs = @(
  (New-TestMessage -Id "m2" -Imid "<dup@x>" -Received ([datetime]"2026-01-02T00:00:00Z")),
  (New-TestMessage -Id "m1" -Imid "<dup@x>" -Received ([datetime]"2026-01-01T00:00:00Z")),
  (New-TestMessage -Id "m3" -Imid "<dup@x>" -Received ([datetime]"2026-01-03T00:00:00Z")),
  (New-TestMessage -Id "s1" -Imid "<solo@x>" -Received ([datetime]"2026-01-01T00:00:00Z"))
)
$groups = $msgs | Group-Object -Property Key | Where-Object { $_.Count -gt 1 }

$planOldest = @(New-RemovalPlan -Groups $groups -KeepRule "Oldest")
Assert-Equal 1 (@($planOldest | Where-Object { $_.Disposition -eq "Keep" }).Count) "exactly one survivor per group"
Assert-Equal 2 (@($planOldest | Where-Object { $_.Disposition -eq "Remove" }).Count) "every other copy is removed"
Assert-Equal "m1" (($planOldest | Where-Object { $_.Disposition -eq "Keep" }).MessageId) "Keep=Oldest survives the earliest copy"
Assert-Equal 3 $planOldest.Count "the un-duplicated message never enters the plan"

$planNewest = @(New-RemovalPlan -Groups $groups -KeepRule "Newest")
Assert-Equal "m3" (($planNewest | Where-Object { $_.Disposition -eq "Keep" }).MessageId) "Keep=Newest survives the latest copy"

# A survivor must never also be listed for removal - the one bug that would lose mail.
$keepIds = @($planOldest | Where-Object { $_.Disposition -eq "Keep" } | ForEach-Object { $_.MessageId })
$removeIds = @($planOldest | Where-Object { $_.Disposition -eq "Remove" } | ForEach-Object { $_.MessageId })
$overlap = @($keepIds | Where-Object { $removeIds -contains $_ })
Assert-Equal 0 $overlap.Count "no message is both kept and removed"

# Deterministic survivor when timestamps tie.
$tied = @(
  (New-TestMessage -Id "zzz" -Imid "<t@x>" -Received ([datetime]"2026-01-01T00:00:00Z")),
  (New-TestMessage -Id "aaa" -Imid "<t@x>" -Received ([datetime]"2026-01-01T00:00:00Z"))
)
$tiedGroups = $tied | Group-Object -Property Key | Where-Object { $_.Count -gt 1 }
Assert-Equal "aaa" ((New-RemovalPlan -Groups $tiedGroups -KeepRule "Oldest" | Where-Object { $_.Disposition -eq "Keep" }).MessageId) "ties break on Id, so the survivor is stable across runs"

# PerFolder scope keys on folder+message, so the same mail filed in two folders is not a group.
$twoFolders = @(
  (New-TestMessage -Id "i1" -Imid "<same@x>" -Folder "Inbox"),
  (New-TestMessage -Id "a1" -Imid "<same@x>" -Folder "Archive")
)
$perFolder = @($twoFolders | Group-Object -Property { "$($_.FolderId)|$($_.Key)" } | Where-Object { $_.Count -gt 1 })
Assert-Equal 0 $perFolder.Count "PerFolder scope does not treat a copy filed in another folder as a duplicate"
$perMailbox = @($twoFolders | Group-Object -Property Key | Where-Object { $_.Count -gt 1 })
Assert-Equal 1 $perMailbox.Count "PerMailbox scope does group the same mail across folders"

# Empty input must not throw.
Assert-Equal 0 (@(New-RemovalPlan -Groups @() -KeepRule "Oldest")).Count "no duplicates produces an empty plan"

Write-Host ""
Write-Host ("{0} passed, {1} failed" -f $script:Pass, $script:Fail) -ForegroundColor $(if ($script:Fail) { "Red" } else { "Green" })
if ($script:Fail) { exit 1 }
