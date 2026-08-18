# LeadOps desktop launch — start server + open browser (no scraper changes).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Ensure-DesktopIcon {
    $ps1 = Join-Path $PSScriptRoot "create_desktop_shortcut.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ps1 | Out-Null
}

function Test-ServerUp {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5000/login" -UseBasicParsing -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

function Find-Python {
    # Prefer `python` when it is 3.9+ (many PCs have no `py` launcher)
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ver = & $cmd.Source -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            if ($ver) {
                $parts = $ver.Trim().Split(".")
                if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9)) {
                    return $cmd.Source
                }
            }
        } catch {}
    }
    try {
        $v = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($v) { return $v.Trim() }
    } catch {}
    try {
        $v = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($v) { return $v.Trim() }
    } catch {}
    return $null
}

# Always keep a proper Desktop app icon
Ensure-DesktopIcon

$py = Find-Python
if (-not $py) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Python 3.9+ was not found.`nInstall Python 3.12 and tick Add to PATH.",
        "LeadOps"
    )
    exit 1
}

# Ensure nodriver for Realtor
& $py -c "import nodriver" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install -r (Join-Path $root "requirements.txt") | Out-Null
}

if (Test-ServerUp) {
    Start-Process "http://127.0.0.1:5000"
    exit 0
}

$serverBat = Join-Path $PSScriptRoot "start_server_window.bat"
Start-Process -FilePath $serverBat -WorkingDirectory $root -WindowStyle Minimized

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-ServerUp) { $ok = $true; break }
}

if (-not $ok) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "LeadOps server did not start.`nCheck PostgreSQL is running and .env is correct.`nAlso try: python frontend_server.py",
        "LeadOps"
    )
    exit 1
}

Start-Process "http://127.0.0.1:5000"
exit 0
