$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonCommand = Get-Command python -ErrorAction Stop
$Simulator = Join-Path $ScriptDir "run_measured_pipe_simulator.py"
$Mesh = Join-Path $ScriptDir "output\pipe_wall_5mm_full360_helix_estimated.stl"

if (-not (Test-Path -LiteralPath $Mesh)) {
    & $PythonCommand.Source (Join-Path $ScriptDir "build_ridged_pipe_model.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Ridged pipe model generation failed with exit code $LASTEXITCODE"
    }
}

& $PythonCommand.Source $Simulator --mesh $Mesh
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Simulator exited with code $LASTEXITCODE" -ForegroundColor Red
    Read-Host "Press Enter to close"
}
