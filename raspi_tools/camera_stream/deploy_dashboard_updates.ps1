$ErrorActionPreference = "Stop"

$PiHost = if ($args.Count -ge 1) { $args[0] } else { "192.168.0.218" }
$Key = "C:\Users\minam\yes"
$Root = "C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\camera_stream"
$Remote = "/home/haikan/pipe_robot_dev/camera_stream"

icacls $Key /inheritance:r | Out-Null
icacls $Key /remove:g "marion\CodexSandboxUsers" "Users" "Authenticated Users" "Everyone" 2>$null | Out-Null
icacls $Key /grant:r "$env:USERNAME`:R" "SYSTEM:R" "Administrators:R" | Out-Null

ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o ConnectTimeout=8 -i $Key "haikan@$PiHost" "mkdir -p $Remote /home/haikan/pipe_robot_dev/docs"

scp -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -i $Key `
  "$Root\dashboard_server.py" `
  "$Root\robot_dashboard.html" `
  "$Root\README_robot_dashboard_handover.md" `
  "haikan@${PiHost}:$Remote/"

ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o ConnectTimeout=8 -i $Key "haikan@$PiHost" "cp $Remote/README_robot_dashboard_handover.md /home/haikan/pipe_robot_dev/docs/README_robot_dashboard_handover.md; cd $Remote; ./start_camera_dashboard.sh"

Write-Host "Updated dashboard on $PiHost"
Write-Host "Open: http://$PiHost:8090/robot_dashboard.html"
