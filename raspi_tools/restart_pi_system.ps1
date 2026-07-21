param(
  [string]$PiHost = "192.168.0.218",
  [string]$SshUser = "haikan",
  [string]$SshKey = "$HOME\yes"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

python .\raspi_tools\pc_robot_command.py `
  restart-pi-system `
  --pi-host $PiHost `
  --ssh-user $SshUser `
  --ssh-key $SshKey
