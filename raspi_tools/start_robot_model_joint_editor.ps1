param(
  [int]$LocalPort = 8095
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

python .\raspi_tools\pc_model_joint_editor.py `
  --local-dashboard-port $LocalPort
