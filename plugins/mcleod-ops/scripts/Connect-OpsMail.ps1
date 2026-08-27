<#
.SYNOPSIS
Load the ops mailbox app password from Windows Credential Manager into the
environment, and optionally verify it against the mail server over IMAP.

.DESCRIPTION
Reads a Generic credential with the Windows CredRead API directly, so no
third-party PowerShell module is required. Sets OPS_MAILBOX and
OPS_MAIL_APP_PASSWORD for the current process; start Claude Code from the same
shell so it inherits them.

The password is never printed, logged, or written to disk.

.EXAMPLE
    # find the stored target name first
    cmdkey /list

    .\Connect-OpsMail.ps1 -Target "MyStoredTarget" -Test
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$Mailbox,
    [string]$ImapHost = "outlook.office365.com",
    [int]$Port = 993,
    [switch]$Test
)

$ErrorActionPreference = "Stop"

Add-Type -Namespace Win32 -Name Cred -MemberDefinition @'
[DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);

[DllImport("advapi32.dll")]
public static extern void CredFree(IntPtr credential);

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct CREDENTIAL {
    public uint Flags;
    public uint Type;
    public string TargetName;
    public string Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public uint CredentialBlobSize;
    public IntPtr CredentialBlob;
    public uint Persist;
    public uint AttributeCount;
    public IntPtr Attributes;
    public string TargetAlias;
    public string UserName;
}
'@

function Read-StoredCredential {
    param([string]$Name)

    # cmdkey displays generic credentials with a "LegacyGeneric:target=" prefix;
    # CredRead wants the bare name, so try both spellings.
    foreach ($candidate in @($Name, "LegacyGeneric:target=$Name")) {
        $ptr = [IntPtr]::Zero
        if ([Win32.Cred]::CredRead($candidate, 1, 0, [ref]$ptr)) {
            try {
                $cred = [System.Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][Win32.Cred+CREDENTIAL])
                $secret = ""
                if ($cred.CredentialBlobSize -gt 0) {
                    $secret = [System.Runtime.InteropServices.Marshal]::PtrToStringUni(
                        $cred.CredentialBlob, [int]($cred.CredentialBlobSize / 2))
                }
                return [pscustomobject]@{ UserName = $cred.UserName; Password = $secret; Target = $candidate }
            } finally {
                [Win32.Cred]::CredFree($ptr)
            }
        }
    }
    throw "No generic credential found for target '$Name'. Run 'cmdkey /list' and pass the exact name shown after 'target='."
}

$stored = Read-StoredCredential -Name $Target
if (-not $Mailbox) { $Mailbox = $stored.UserName }
if (-not $Mailbox) { throw "The credential stores no user name; pass -Mailbox explicitly." }
if (-not $stored.Password) { throw "The credential for '$Target' has an empty password blob." }

$env:OPS_MAILBOX = $Mailbox
$env:OPS_MAIL_APP_PASSWORD = $stored.Password

Write-Host "Loaded credential '$($stored.Target)'" -ForegroundColor Green
Write-Host "  OPS_MAILBOX           = $Mailbox"
Write-Host "  OPS_MAIL_APP_PASSWORD = (set, $($stored.Password.Length) chars, not shown)"

if (-not $Test) { return }

function ConvertTo-ImapQuoted([string]$value) {
    '"' + ($value -replace '\\', '\\' -replace '"', '\"') + '"'
}

Write-Host "`nTesting IMAP login against ${ImapHost}:${Port} ..." -ForegroundColor Cyan
$client = $null; $ssl = $null
try {
    $client = [System.Net.Sockets.TcpClient]::new()
    if (-not $client.ConnectAsync($ImapHost, $Port).Wait(15000)) {
        throw "Timed out connecting to ${ImapHost}:${Port}. Port 993 may be blocked by a firewall or network policy."
    }
    $ssl = [System.Net.Security.SslStream]::new($client.GetStream(), $false)
    $ssl.AuthenticateAsClient($ImapHost)
    $reader = [System.IO.StreamReader]::new($ssl)
    $writer = [System.IO.StreamWriter]::new($ssl)
    $writer.AutoFlush = $true

    $greeting = $reader.ReadLine()
    Write-Host "  server: $greeting"

    $writer.WriteLine("A01 LOGIN $(ConvertTo-ImapQuoted $Mailbox) $(ConvertTo-ImapQuoted $stored.Password)")

    $result = $null
    while ($null -ne ($line = $reader.ReadLine())) {
        if ($line.StartsWith("A01 ")) { $result = $line; break }
    }

    if ($result -and $result.StartsWith("A01 OK")) {
        Write-Host "  LOGIN OK — the app password works over IMAP." -ForegroundColor Green
        $writer.WriteLine("A02 LOGOUT") | Out-Null
    } else {
        Write-Host "  LOGIN REJECTED: $result" -ForegroundColor Red
        Write-Host "  A rejection here usually means basic auth for IMAP is disabled for the tenant," -ForegroundColor Yellow
        Write-Host "  not that the password is wrong. Microsoft has disabled it broadly in Exchange Online." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($ssl) { $ssl.Dispose() }
    if ($client) { $client.Dispose() }
}
