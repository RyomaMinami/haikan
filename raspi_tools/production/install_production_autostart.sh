#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="${HOME}/.config/systemd/user"
BASE="${BASE:-/home/haikan/pipe_robot_dev}"
mkdir -p "$SERVICE_DIR"

cat >"${SERVICE_DIR}/pipe-robot-production.service" <<SERVICE
[Unit]
Description=Pipe robot production dashboard, recording, and logging
After=default.target network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${BASE}/production/start_production_robot.sh
ExecStop=${BASE}/production/stop_production_robot.sh
RemainAfterExit=yes
TimeoutStopSec=20
TimeoutStartSec=180

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable pipe-robot-production.service
systemctl --user restart pipe-robot-production.service

echo "[install] user service enabled: pipe-robot-production.service"
echo "[install] status:"
systemctl --user --no-pager status pipe-robot-production.service || true

cat <<'NOTE'

If this service does not start after a cold boot until login, enable user lingering once:
  sudo loginctl enable-linger haikan

NOTE
