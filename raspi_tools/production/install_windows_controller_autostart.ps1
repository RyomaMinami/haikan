param(
    [string]$TaskName = "PipeRobotControllerSender",
    [string]$PiHost = "192.168.0.218"
)

$ErrorActionPreference = "Stop"

$repo = "C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk"
$script = Join-Path $repo "raspi_tools\pc_valve_controller_sender.py"
$python = (Get-Command python).Source
$args = "`"$script`" --pi-host $PiHost --command-port 8092 --controller-port 8091 --deadzone 0.18 --step-axis 0 --motor-axis 1"

$action = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory (Join-Path $repo "raspi_tools")
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Pipe robot PC controller UDP sender" -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Pi host: $PiHost"
Write-Host "Start now with:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
