param(
  [string]$PiHost = "192.168.0.218",
  [switch]$RollPositionChangeMode
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

$launcherArgs = @(
  ".\raspi_tools\pc_experiment_launcher.py",
  "--pi-host", $PiHost,
  "--dashboard-port", "8090",
  "--local-dashboard-port", "8095",
  "--command-port", "8092",
  "--controller-port", "8091",
  "--deadzone", "0.18",
  "--step-axis", "0",
  "--motor-axis", "1",
  "--motor-response-exponent", "1.0",
  "--motor-throttle-axis", "3",
  "--invert-motor-throttle",
  "--motor-throttle-full-scale", "0.95",
  # Axis 3 is normal-speed control only. After the 2026-07-26 power cycle the
  # actual machine saturated around command 69 / pwm_out about 87. Cap normal
  # control at command about 51 / pwm_out about 65. Hat 0 still uses full speed.
  "--motor-max-duty-ratio", "0.20",
  # Hat 0 up/down selects dedicated maximum forward/reverse operation.
  "--motor-full-speed-hat", "0",
  "--pressure-target-mpa", "0.60",
  "--pressure-hysteresis-mpa", "0.02"
)

if ($RollPositionChangeMode) {
  $launcherArgs += "--roll-position-change-mode"
}

python @launcherArgs
