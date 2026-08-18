# Creates a proper Desktop app icon "LeadOps" with custom .ico
# Target = LeadOps.vbs (no black CMD flash)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "LeadOps.vbs"))) {
    $root = $PSScriptRoot
}

$vbs = Join-Path $root "LeadOps.vbs"
$ico = Join-Path $root "static\leadops.ico"
if (-not (Test-Path $vbs)) {
    Write-Host "ERROR: LeadOps.vbs not found"
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "LeadOps.lnk"

# Remove old bat-based shortcut so icon refreshes
if (Test-Path $lnkPath) {
    Remove-Item $lnkPath -Force
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnkPath)
$sc.TargetPath = $vbs
$sc.WorkingDirectory = $root
$sc.WindowStyle = 1
$sc.Description = "LeadOps Agent Intelligence"
if (Test-Path $ico) {
    $sc.IconLocation = "$ico,0"
} else {
    $sc.IconLocation = "%SystemRoot%\System32\shell32.dll,13"
}
$sc.Save()

Write-Host "Desktop app icon created: $lnkPath"
exit 0
