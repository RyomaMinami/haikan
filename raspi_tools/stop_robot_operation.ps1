$ErrorActionPreference = "Stop"
param(
  [string]$PiHost = "192.168.0.218",
  [switch]$StopPcSender
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

$argsList = @(
  ".\raspi_tools\pc_robot_command.py",
  "stop",
  "--pi-host", $PiHost,
  "--command-port", "8092"
)

if ($StopPcSender) {
  $argsList += "--stop-pc-sender"
}

python @argsList
