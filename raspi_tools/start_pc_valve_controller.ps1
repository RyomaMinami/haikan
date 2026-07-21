param(
  [string]$PiHost = "192.168.0.218"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir

Set-Location $RepoDir

python .\raspi_tools\pc_valve_controller_sender.py `
  --pi-host $PiHost `
  --command-port 8092 `
  --controller-port 8091 `
  --deadzone 0.18 `
  --step-axis 0 `
  --motor-axis 1
