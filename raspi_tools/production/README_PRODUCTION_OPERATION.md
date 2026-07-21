# 配管ロボット 本番運用メモ

目的は、Raspberry Piの電源投入後に、監視画面、コントローラ受信、カメラ配信、センサログ、カメラ録画を自動で開始すること。

## 起動されるもの

- ダッシュボード: `http://192.168.0.218:8090/robot_dashboard.html`
- Wi-Fi時のダッシュボード: `http://192.168.50.154:8090/robot_dashboard.html`
- UDPコントローラ受信: `8091`
- UDPロボットコマンド受信: `8092`
- カメラ配信: `8080`, `8081`, `8082`
- 状態CSV/JSONL記録: `/home/haikan/pipe_robot_logs/production`
- カメラ録画: `/home/haikan/pipe_robot_logs/production/video`

## Pi側の手動起動/停止

```bash
cd /home/haikan/pipe_robot_dev/production
./start_production_robot.sh
./stop_production_robot.sh
```

## 自動起動の登録

```bash
cd /home/haikan/pipe_robot_dev/production
./install_production_autostart.sh
```

起動確認:

```bash
systemctl --user status pipe-robot-production.service
pgrep -af 'dashboard_server|mjpg_streamer|state_api_logger|ffmpeg'
curl http://127.0.0.1:8090/api/state
```

コールドブート後にログインするまで動かない場合だけ、次を一度実行する。

```bash
sudo loginctl enable-linger haikan
```

現在、`pipe-robot-production.service` は有効化済み。`Linger=no` のままだと、Pi起動後に `haikan` ユーザーがログインしてから起動する。電源投入だけでログイン前から動かすには、Pi上で上の `sudo loginctl enable-linger haikan` を実行する。

## PC側コントローラ

コントローラはPCに接続しているため、PC側で送信プログラムが必要。

有線LANの本番IPではPiは `192.168.0.218`。PC側は次のスクリプトを起動する。

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools
.\start_pc_valve_controller.ps1
```

現在の割当:

- 左右軸: ステッピングモータ
- 前後軸: DCモータ
- ボタン0/1: ドリル押し出し/引き込み
- ボタン9/10/11付近: グラインダ空圧ON/OFFは実機確認済みの割当に注意

PCも自動化したい場合は、Windowsのスタートアップまたはタスクスケジューラに `start_pc_valve_controller.ps1` を登録する。

2026-07-20時点では、Windowsのスタートアップフォルダに次のショートカットを作成済み。

```text
C:\Users\minam\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PipeRobotControllerSender.lnk
```

これにより、Windowsログオン後にPC側コントローラ送信が有線IP `192.168.0.218` 向けで起動する。

## 記録ファイル

状態ログ:

```text
/home/haikan/pipe_robot_logs/production/state_YYYYMMDD_HHMMSS.csv
/home/haikan/pipe_robot_logs/production/state_YYYYMMDD_HHMMSS.jsonl
```

カメラ録画:

```text
/home/haikan/pipe_robot_logs/production/video/global_left_YYYYMMDD_HHMMSS.mkv
/home/haikan/pipe_robot_logs/production/video/usb_16mp_YYYYMMDD_HHMMSS.mkv
/home/haikan/pipe_robot_logs/production/video/global_right_YYYYMMDD_HHMMSS.mkv
```

録画は10分ごとに分割される。

## 注意点

- カメラ3台を接続したまま電源投入するとPiが落ちることがある。2台で起動してから3台目を接続するか、セルフパワーUSBハブを使う。
- ダッシュボードサーバがESPのUARTを読む。別のロガーで `/dev/ttyAMA2` や `/dev/ttyAMA4` を直接開くと競合する。
- ステッピング電流検出のIO32はレベル変換モジュールから外した状態を維持する。アナログ測定ピンにレベル変換や別信号を接続しない。
- ESP32のOTAは起動後30秒だけ有効。その後Wi-FiをOFFにしてADC2を安定させる。
- 実験前は必ずダッシュボードで `motor`, `valves`, `controller`, `step_sense` が更新されているか見る。
