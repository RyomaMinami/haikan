from __future__ import annotations

import argparse
import time
import webbrowser
from pathlib import Path

from pc_experiment_launcher import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_LOCAL_DASHBOARD_PORT,
    DEFAULT_PI_HOST,
    start_local_dashboard,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline-capable PC editor for STL joint and sensor mapping."
    )
    parser.add_argument("--pi-host", default=DEFAULT_PI_HOST)
    parser.add_argument("--dashboard-port", type=int, default=DEFAULT_DASHBOARD_PORT)
    parser.add_argument(
        "--local-dashboard-port",
        type=int,
        default=DEFAULT_LOCAL_DASHBOARD_PORT,
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    dashboard_dir = Path(__file__).resolve().parent / "camera_stream"
    server = None
    thread = None
    try:
        server, thread = start_local_dashboard(
            args.pi_host,
            args.dashboard_port,
            args.local_dashboard_port,
            dashboard_dir,
        )
        url = (
            f"http://127.0.0.1:{args.local_dashboard_port}/"
            "robot_model_joint_editor.html"
        )
        print(f"[OPEN] Joint editor: {url}")
        print("[RUN] Pi/Wi-Fiなしでも編集できます。Ctrl+Cで終了します。")
        if not args.no_browser:
            webbrowser.open(url)
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[STOP] Joint editor")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
