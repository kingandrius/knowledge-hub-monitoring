# Install the monitoring agent on a Windows host (DirectoryServer, FileServer,
# StaffPC1, StaffPC2). Needs Python 3.12+ installed "for all users".
#
#   powershell -ExecutionPolicy Bypass -File deploy\install-agent.ps1 -Key kh-xxxx-...
#
# Result:
#   C:\kh-agent          monitoring.py + venv + .env + buffer database
#                        (ACL: SYSTEM and Administrators only)
#   Scheduled tasks      "KH Agent - measure" every 5 min, "KH Agent - send"
#                        every 15 min, both as SYSTEM
param(
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$Collector = "10.10.10.30"
)
$ErrorActionPreference = "Stop"
$Dir = "C:\kh-agent"
$Repo = Split-Path -Parent $PSScriptRoot

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run PowerShell as Administrator." }

Write-Host "== 1/5 Python and folder"
$base = $null
foreach ($cmd in @("py", "python")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $args3 = @(); if ($cmd -eq "py") { $args3 = @("-3") }
        $out = & $cmd @args3 -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $base = $out.Trim(); break }
    }
}
if (-not $base) { throw "Python 3.10+ not found. Install it for all users and tick 'Add to PATH'." }
Write-Host "  Python: $base"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
# Only SYSTEM (S-1-5-18) and Administrators (S-1-5-32-544): .env holds the key
icacls $Dir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null; Check "icacls"

Write-Host "== 2/5 code and venv"
Copy-Item "$Repo\monitoring.py", "$Repo\requirements.txt" $Dir -Force
if (-not (Test-Path "$Dir\.venv\Scripts\python.exe")) { & $base -m venv "$Dir\.venv"; Check "venv" }
& "$Dir\.venv\Scripts\python.exe" -m pip install -q -r "$Dir\requirements.txt"; Check "pip install"

Write-Host "== 3/5 collector certificate (trust on first use, check the fingerprint)"
$tcp = New-Object Net.Sockets.TcpClient($Collector, 8443)
$callback = [Net.Security.RemoteCertificateValidationCallback] { $true }  # fetch only; verified below
$ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, $callback)
$ssl.AuthenticateAsClient($Collector, $null, [Security.Authentication.SslProtocols]::Tls12, $false)
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
$ssl.Dispose(); $tcp.Dispose()
$sha = [Security.Cryptography.SHA256]::Create().ComputeHash($cert.RawData)
$fingerprint = ([BitConverter]::ToString($sha)) -replace "-", ":"
Write-Host "  sha256 Fingerprint=$fingerprint"
$answer = Read-Host "Same fingerprint as shown on MonitorServer? (yes/no)"
if ($answer -ne "yes") { throw "Stopped: certificate not trusted." }
$pem = "-----BEGIN CERTIFICATE-----`n" +
       [Convert]::ToBase64String($cert.RawData, "InsertLineBreaks") +
       "`n-----END CERTIFICATE-----`n"
[IO.File]::WriteAllText("$Dir\collector.crt", $pem)   # UTF-8 without BOM

Write-Host "== 4/5 settings and scheduled tasks"
$envText = "KH_API_URL=https://${Collector}:8443/api/v1`nKH_API_KEY=$Key`nKH_CA_BUNDLE=$Dir\collector.crt`n"
[IO.File]::WriteAllText("$Dir\.env", $envText)

$py = "$Dir\.venv\Scripts\python.exe"
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-18" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$tasks = @(
    @{ Name = "KH Agent - measure"; Minutes = 5;
       Action = New-ScheduledTaskAction -Execute $py -Argument "monitoring.py measure" -WorkingDirectory $Dir },
    @{ Name = "KH Agent - send"; Minutes = 15;
       Action = New-ScheduledTaskAction -Execute "cmd.exe" `
                -Argument "/c $py monitoring.py send >> $Dir\send.log 2>&1" -WorkingDirectory $Dir }
)
foreach ($t in $tasks) {
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
               -RepetitionInterval (New-TimeSpan -Minutes $t.Minutes)
    Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "  task '$($t.Name)' every $($t.Minutes) min"
}

Write-Host "== 5/5 first measurement and send"
Push-Location $Dir
& $py monitoring.py measure
& $py monitoring.py send
Pop-Location
