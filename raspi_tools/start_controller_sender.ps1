$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sender = Join-Path $Root "pc_controller_sender.py"
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $LogDir "controller_sender.pid"
$OutLogFile = Join-Path $LogDir "controller_sender.out.log"
$ErrLogFile = Join-Path $LogDir "controller_sender.err.log"

$PiHost = if ($env:PI_HOST) { $env:PI_HOST } else { "192.168.0.218" }
$PiPort = if ($env:PI_CONTROLLER_PORT) { $env:PI_CONTROLLER_PORT } else { "8091" }
$RateHz = if ($env:CONTROLLER_RATE_HZ) { $env:CONTROLLER_RATE_HZ } else { "20" }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid -and (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) {
        Write-Host "[controller] already running pid=$OldPid"
        exit 0
    }
}

$Args = @(
    $Sender,
    "--pi-host", $PiHost,
    "--pi-port", $PiPort,
    "--rate-hz", $RateHz
)

$Process = Start-Process -FilePath "python" -ArgumentList $Args -PassThru -WindowStyle Hidden -RedirectStandardOutput $OutLogFile -RedirectStandardError $ErrLogFile
$Process.Id | Set-Content $PidFile

Write-Host "[controller] started pid=$($Process.Id)"
Write-Host "[controller] sending to $PiHost`:$PiPort at $RateHz Hz"
Write-Host "[controller] log: $OutLogFile"
Write-Host "[controller] err: $ErrLogFile"
