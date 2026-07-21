param(
  [string]$PiHost = "192.168.0.218"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

python .\raspi_tools\pc_robot_command.py `
  status `
  --pi-host $PiHost `
  --dashboard-port 8090
