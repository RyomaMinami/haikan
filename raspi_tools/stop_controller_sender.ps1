$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $LogDir "controller_sender.pid"

if (Test-Path $PidFile) {
    $PidValue = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($PidValue -and (Get-Process -Id $PidValue -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $PidValue -Force
        Write-Host "[controller] stopped pid=$PidValue"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[controller] pid file not found"
}
