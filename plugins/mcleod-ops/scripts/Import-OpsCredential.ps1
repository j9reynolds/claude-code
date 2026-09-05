<#
.SYNOPSIS
Load a credential from Windows Credential Manager into environment variables,
and optionally verify it against the service it belongs to.

.DESCRIPTION
Reads a Generic credential with the Windows CredRead API directly, so no
third-party PowerShell module is required. Sets the named environment
variables for the current process; start Claude Code from the same shell so it
inherits them.

The password is never printed, logged, or written to disk.

.EXAMPLE
    # ops mailbox (defaults target the mail variables), then verify IMAP login
    .\Import-OpsCredential.ps1 -Target "MyMailTarget" -TestImap

.EXAMPLE
    # McLeod API credential, then verify the endpoint accepts it
    .\Import-OpsCredential.ps1 -Target "https://host/exchange/exchangeRest" `
        -UsernameVariable MCLEOD_API_USER -PasswordVariable MCLEOD_API_PASSWORD `
        -TestHttp "https://host/exchange/exchangeRest"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$UsernameVariable = "OPS_MAILBOX",
    [string]$PasswordVariable = "OPS_MAIL_APP_PASSWORD",
    [string]$UserName,
    [string]$ImapHost = "outlook.office365.com",
    [int]$Port = 993,
    [switch]$TestImap,
    [string]$TestHttp
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
if (-not $UserName) { $UserName = $stored.UserName }
if (-not $UserName) { throw "The credential stores no user name; pass -UserName explicitly." }
if (-not $stored.Password) { throw "The credential for '$Target' has an empty password blob." }

Set-Item -Path "Env:$UsernameVariable" -Value $UserName
Set-Item -Path "Env:$PasswordVariable" -Value $stored.Password

Write-Host "Loaded credential '$($stored.Target)'" -ForegroundColor Green
Write-Host "  $UsernameVariable = $UserName"
Write-Host "  $PasswordVariable = (set, $($stored.Password.Length) chars, not shown)"

if ($TestImap) {
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

        Write-Host "  server: $($reader.ReadLine())"
        $writer.WriteLine("A01 LOGIN $(ConvertTo-ImapQuoted $UserName) $(ConvertTo-ImapQuoted $stored.Password)")

        $result = $null
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.StartsWith("A01 ")) { $result = $line; break }
        }

        if ($result -and $result.StartsWith("A01 OK")) {
            Write-Host "  LOGIN OK - the app password works over IMAP." -ForegroundColor Green
            $writer.WriteLine("A02 LOGOUT")
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
}

if ($TestHttp) {
    Write-Host "`nTesting HTTP auth against $TestHttp ..." -ForegroundColor Cyan
    $pair = [System.Text.Encoding]::UTF8.GetBytes("${UserName}:$($stored.Password)")
    $headers = @{ Authorization = "Basic " + [Convert]::ToBase64String($pair) }

    # Control request with no credentials. If the server answers identically with and
    # without them, it is not evaluating our Authorization header at all — which tells
    # us the scheme is wrong, rather than the password.
    $control = $null
    try {
        $control = Invoke-WebRequest -Uri $TestHttp -Method Get -TimeoutSec 30 -SkipHttpErrorCheck
        Write-Host "  control (no credentials sent): HTTP $($control.StatusCode), $($control.RawContentLength) bytes"
    } catch {
        Write-Host "  control request failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    try {
        $resp = Invoke-WebRequest -Uri $TestHttp -Headers $headers -Method Get -TimeoutSec 30 -SkipHttpErrorCheck
        Write-Host "  HTTP $($resp.StatusCode) $($resp.StatusDescription)" -ForegroundColor Green

        Write-Host "  --- response headers ---"
        foreach ($key in $resp.Headers.Keys) {
            Write-Host "  $key`: $(($resp.Headers[$key]) -join ', ')"
        }

        # Error pages are usually HTML; the sentence that matters is buried in markup.
        $text = [regex]::Replace(($resp.Content | Out-String), '(?s)<script.*?</script>|<style.*?</style>', ' ')
        $text = [regex]::Replace($text, '<[^>]+>', ' ')
        $text = [System.Net.WebUtility]::HtmlDecode($text)
        $text = [regex]::Replace($text, '\s+', ' ').Trim()
        if ($text.Length -gt 600) { $text = $text.Substring(0, 600) + "..." }
        if ($text) { Write-Host "  --- body (tags stripped) ---`n  $text" }

        if (-not $resp.Headers['WWW-Authenticate']) {
            Write-Host "`n  No WWW-Authenticate header." -ForegroundColor Yellow
            if ($control -and $control.StatusCode -eq $resp.StatusCode -and
                $control.RawContentLength -eq $resp.RawContentLength) {
                Write-Host "  The response is IDENTICAL with and without credentials, so the server is not" -ForegroundColor Yellow
                Write-Host "  evaluating the Authorization header. Basic is the wrong scheme for this URL:" -ForegroundColor Yellow
                Write-Host "  the API most likely wants a login call that returns a token or session, or this" -ForegroundColor Yellow
                Write-Host "  is a base path rather than a callable resource. Get the login flow from the docs." -ForegroundColor Yellow
            } else {
                Write-Host "  The response DIFFERS from the no-credential control, so Basic is being processed" -ForegroundColor Yellow
                Write-Host "  and these particular credentials were rejected. Confirm this account is an API" -ForegroundColor Yellow
                Write-Host "  user rather than a portal login." -ForegroundColor Yellow
            }
        } else {
            Write-Host "`n  The WWW-Authenticate header above names the scheme this endpoint wants;" -ForegroundColor Yellow
            Write-Host "  put that scheme in sources.json rather than assuming Basic." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}
