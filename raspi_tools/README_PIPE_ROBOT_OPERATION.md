# 配管ロボット実験運用メモ

作成日: 2026-07-16

このファイルは、配管ロボットの配信、センサ、モータ、電磁弁、実験ネットワークについて、次に作業する人またはAIが最初に読むための入口メモ。

詳細は以下も読むこと。

```text
raspi_tools/camera_stream/README_robot_dashboard_handover.md
raspi_tools/camera_stream/README_mjpg_3cams.md
raspi_tools/esp32_escon_telemetry/README_escon_telemetry.md
raspi_tools/esp32_escon_telemetry/SOLENOID_VALVES.md
raspi_tools/esp32_escon_telemetry/HOLE_EXPANSION_MOTION.md
raspi_tools/STEP_STEPPER_CURRENT_TELEMETRY_NOTE.md
```

## 現在の重要注意

AE-KXR94-2050加速度センサをRaspberry PiのGPIOへ直接接続しないこと。

理由:

- AE-KXR94-2050はアナログ電圧出力
- Raspberry Pi GPIOはアナログ電圧を読めない
- 3.3Vを超える電圧が入るとPiが不安定化または破損する可能性がある
- 2026-07-16時点で、Pi GPIO24/23/27へX/Y/Zを直結した後からSSHや配信が不安定になった

推奨:

```text
AE-KXR94 X/Y/Z -> ESP32側のACCE1_X/Y/Z または外付けADC
AE-KXR94 Vcc   -> 3.3V
AE-KXR94 GND   -> GND共通
```

OTAは書き込み専用に使うならよい。ただし実験中にOTAしないこと。

2026-07-16追記:

- AE-KXR94-2050をESP32メイン基板のACCE1へ接続する方針に変更
- ACCE1は `X=GPIO33`, `Y=GPIO25`, `Z=GPIO26` としてファームへ追加
- ESP32テレメトリに `acce1_x_v`, `acce1_y_v`, `acce1_z_v`, `acce1_x_g`, `acce1_y_g`, `acce1_z_g` を追加
- ダッシュボードの「本体加速度センサ」欄へ電圧、加速度、roll/pitchを表示
- GPIO25/26はESP32のADC2なので、OTA用Wi-Fiが有効なままだとY/Zが `0.128V` 付近に張り付くことがある
- 対策として、OTA書き込み後30秒でESP32のWi-FiをOFFにし、ADC2を読めるようにした
- もう一度OTAしたい場合はESP32をリセットして、起動後30秒以内にOTAする

確認値の例:

```text
acce1_x_v = 1.6150 V, acce1_x_g = -0.0530 g
acce1_y_v = 2.3416 V, acce1_y_g =  1.0479 g
acce1_z_v = 1.7180 V, acce1_z_g =  0.1030 g
roll = 84.4 deg
pitch = 2.9 deg
```

## SSH

通常Wi-Fi側:

```powershell
ssh -i C:\Users\minam\yes haikan@192.168.50.154
```

Piが見えない場合、まずPCが `aokilab2` または実験用有線LANにいるか確認する。

```powershell
netsh wlan show interfaces
netsh wlan connect name="aokilab2" interface="Wi-Fi"
```

2026-07-16確認時はPCが `CIT-Wi-Fi` につながっており、`aokilab2` が見えなかったためPiへSSHできなかった。

## 配信開始

PiへSSH後:

```bash
cd /home/haikan/pipe_robot_dev/camera_stream
./start_camera_dashboard.sh
```

ブラウザ:

```text
http://192.168.50.154:8090/robot_dashboard.html
```

確認:

```bash
pgrep -af 'dashboard_server|mjpg_streamer|camera_watchdog'
curl http://127.0.0.1:8090/api/state
curl -I http://127.0.0.1:8080/?action=snapshot
curl -I http://127.0.0.1:8081/?action=snapshot
curl -I http://127.0.0.1:8082/?action=snapshot
```

停止:

```bash
cd /home/haikan/pipe_robot_dev/camera_stream
./stop_camera_dashboard.sh
./stop_mjpg_3cams.sh
./stop_camera_watchdog.sh
```

## 実験ネットワーク

実験時:

```text
PC -- 有線LAN -- ルーター -- 有線LAN -- Raspberry Pi
```

普段:

```text
PC -- Wi-Fi(aokilab2) -- ルーター -- Wi-Fi -- Raspberry Pi
```

有線優先、Wi-Fi復帰の設定スクリプト:

```bash
cd /home/haikan/pipe_robot_dev/network
sudo ./install_wired_wifi_failover.sh
```

sudoが必要なので、AIだけでは適用できない。

## PCコントローラ入力

PC側:

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools
.\start_controller_sender.ps1
```

停止:

```powershell
.\stop_controller_sender.ps1
```

ダッシュボード側はUDP 8091で受信する。

## モータと電流値

ESP2側がESCON/DCモータ、ステッピングモータ、電流値、回転数などを扱う。

主な場所:

```text
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/
raspi_tools/esp32_escon_telemetry/
```

テスト系:

```text
raspi_tools/escon_drive_log_test.py
raspi_tools/escon_speed_ramp_log_test.py
raspi_tools/stepper_current_log_test.py
raspi_tools/hole_expand_command.py
```

注意:

- モータを動かす前にロボットが転倒しない状態にする
- 低速、短時間、停止処理つきで試す
- ダッシュボードがESP2 UARTを読んでいる時に、別プロセスで同じUARTを直接開かない

## 電磁弁

対象:

```text
MOVE_PUSH
MOVE_PULL
DRILL_PUSH
DRILL_PULL
GRINDER_AIR
ALL
STATUS
```

HTTP API:

```text
http://192.168.50.154:8090/api/valve?name=MOVE_PUSH&on=1
http://192.168.50.154:8090/api/valve?name=MOVE_PUSH&on=0
http://192.168.50.154:8090/api/valve?name=ALL&on=0
```

Python補助:

```bash
python3 /home/haikan/pipe_robot_dev/valve_command.py --name MOVE_PUSH --on 1
python3 /home/haikan/pipe_robot_dev/valve_command.py --name ALL --on 0
```

安全仕様:

- 押下中のみON
- 約350msコマンドが途切れたら自動OFF
- 押し出し/引き込みの同時ONは禁止
- 緊急時は `ALL` OFF

## 次の作業者への注意

- Piが見えない時は、コードより先にPCの接続先Wi-Fi/有線を確認する
- AE-KXR94をPi GPIOへ戻さない
- 加速度はESP32 ADCまたは外付けADCで読む
- 実験中は配信と状態表示のリアルタイム性を優先する
- モータ、電磁弁、グラインダを動かす前にユーザーへ安全確認する
