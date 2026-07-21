# 配管ロボット配信ダッシュボード引き継ぎメモ

作成日: 2026-07-11

このメモは、Raspberry Pi 上で動かしている配管ロボット用の監視画面について、次に作業する人またはAIが状況を誤解しないように残すものです。

## 目的

配管内実験で、以下を1つのブラウザ画面にまとめて確認する。

- カメラ3台の映像
- ESP1側のセンサ値
  - 加速度センサ
  - 距離センサ
  - 圧力センサ
  - KI1233-AAによるグラインダ回転数
- ESP2側のモータ・電流値
  - ESCON電流
  - ESCONエンコーダ回転数
  - ステッピングモータ電流
  - step_hz など
- PCに接続したコントローラ入力
- ロボット姿勢の3D表示

実験中はリアルタイム性を優先する。CADモデルは重いため、通常画面では軽量モデルを表示する。

## 主な場所

Raspberry Pi 側:

```text
/home/haikan/pipe_robot_dev/camera_stream/
```

PC 側の同期元:

```text
C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\camera_stream\
```

## ネットワーク

実験時は有線LANを想定している。

- Raspberry Pi 有線IP: `192.168.0.218`
- PC 有線IP: `192.168.0.182`
- Raspberry Pi Wi-Fi IP: `192.168.50.154`

SSH例:

```bash
ssh -i C:\Users\minam\yes haikan@192.168.0.218
```

## 起動方法

PiにSSH接続して、以下を実行する。

```bash
cd /home/haikan/pipe_robot_dev/camera_stream
./start_camera_dashboard.sh
```

起動後、PCのブラウザで以下を開く。

```text
http://192.168.0.218:8090/robot_dashboard.html
```

CADモデルを確認したいときだけ以下を開く。

```text
http://192.168.0.218:8090/robot_dashboard.html?cad=1
```

通常の実験では `?cad=1` を付けない。STLが約58MBあり、読み込みが重くなるため。

## 停止方法

```bash
cd /home/haikan/pipe_robot_dev/camera_stream
./stop_camera_dashboard.sh
./stop_camera_watchdog.sh
./stop_mjpg_3cams.sh
```

## ファイルの役割

### `robot_dashboard.html`

ブラウザに表示するメイン画面。

- 上半分にカメラ3台を横並び表示
- 下半分に3D姿勢、IMU/センサ、モータ/電流、コントローラ入力を表示
- `/api/state` を250ms周期で取得
- 通常は軽量3Dモデルを表示
- URLに `?cad=1` を付けたときだけ `assets/robot_model.stl` を読み込む

3D表示は Three.js を使用している。

### `dashboard_server.py`

HTTPサーバと状態集約プログラム。

主な役割:

- `robot_dashboard.html` などの静的ファイル配信
- `/api/state` で現在状態をJSON配信
- ESP1のシリアル値を読む
- ESP2のシリアル値を読む
- PCコントローラ入力をUDPで受信する

ESP1の `GY` は、KI1233-AAのスリット検出信号として扱う。ダッシュボードでは `KI1233-AA rpm` として表示する。

デフォルト:

- Webポート: `8090`
- ESP1: `/dev/ttyAMA2`
- ESP2: `/dev/ttyAMA4`
- コントローラUDP: `8091`
- baudrate: `115200`

## KI1233-AAとグラインダ回転数

KI1233-AAは、グラインダに取り付けられているスリット円板を読むためのセンサ。

スリット条件:

```text
穴数: 24個 / 1回転
穴位置: グラインダ回転中心から半径45 mm
穴サイズ: 2 mm
```

回転数の換算:

```text
rpm = pulse_hz / 24 * 60
rpm = pulse_hz * 2.5
```

円周方向の穴ピッチ:

```text
2 * pi * 45 / 24 = 約11.78 mm
```

注意:

- `dashboard_server.py` は `GY` の0/1立ち上がりから概算rpmを計算する。
- ただし、Pi側Pythonが受け取っているのが「ESPが周期送信した瞬時状態」だけの場合、高速回転ではパルスを取り逃がす。
- 正確なグラインダ回転数が必要な場合は、ESP側で割り込みによりパルス数または周波数を数え、`ki_hz=...` または `grinder_rpm=...` のような値をシリアル送信するのが望ましい。
- サーバ側は将来 `ki_hz`, `ki1233_hz`, `grinder_rpm`, `ki1233_rpm` が送られてきた場合、それを優先できるようにしてある。

### `start_camera_dashboard.sh`

実験時に基本的に使う起動スクリプト。

内部で以下を実行する。

1. `start_mjpg_3cams.sh`
2. `start_camera_watchdog.sh`
3. `dashboard_server.py --port 8090`

### `start_mjpg_3cams.sh`

mjpg-streamerでカメラ3台を配信する。

現在はリアルタイム性と安定性優先で低負荷設定。

```bash
WIDTH=320
HEIGHT=240
FPS=5
QUALITY=60
```

カメラ配信:

```text
Camera 1 global_left  : http://192.168.0.218:8080/?action=stream
Camera 2 usb_16mp     : http://192.168.0.218:8081/?action=stream
Camera 3 global_right : http://192.168.0.218:8082/?action=stream
```

### `camera_watchdog.sh`

カメラ映像が固まったり、snapshot取得に失敗した場合にmjpg-streamerを再起動する監視スクリプト。

過去に「カメラが見えなくなる」「ブラウザで黒画面になる」問題があったため追加した。

### `assets/robot_model.stl`

Inventorから出力したロボットモデル。

元ファイル:

```text
C:\Users\minam\Documents\Inventor\配管穿孔ロボット\配管穿孔　動作実験用ver.2.stl
```

Pi内の配置:

```text
/home/haikan/pipe_robot_dev/camera_stream/assets/robot_model.stl
```

サイズが大きいため、通常画面では読み込まない。

### `vendor/three.min.js`

3D表示用のThree.js。

Piがオフラインでも動くようにローカル配置している。

## PCコントローラ入力

PCに接続したコントローラは、PC側Pythonで読み取り、UDPでPiへ送る。

PC側ファイル:

```text
C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools\pc_controller_sender.py
```

起動:

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools
powershell -ExecutionPolicy Bypass -File .\start_controller_sender.ps1
```

停止:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_controller_sender.ps1
```

デフォルト送信先:

```text
192.168.0.218:8091
```

確認済みコントローラ:

```text
Logitech Extreme 3D
axes=4, buttons=12, hats=1
```

## 3D表示の考え方

最初はCSSで簡易的な箱モデルを表示していたが、現在はThree.jsに変更した。

ただし、CAD STLは重いため、実験時は軽量モデルを使う。

- 通常: 軽量モデル
- `?cad=1`: CAD STLモデル

加速度センサの向きは実機への取り付け方向と一致していない可能性がある。そのため、現在の3D表示は「起動時またはページ読み込み時の加速度値を基準姿勢」とし、そこからの相対変化でroll/pitchを表示している。

注意:

- 現在の3D姿勢は厳密なIMU姿勢推定ではない
- gyro積分やセンサフュージョンはまだ入っていない
- 実機姿勢の見た目確認用

## 加速度センサ追加時の注意

メイン基板の `accerareta2` 部分に加速度センサを取り付けたところ不具合が出た。

現時点で疑うべき原因:

- I2Cアドレス衝突
  - 同じ型番の加速度センサを2個つなぐと、アドレス変更ピンがない場合に同一アドレスになり、バス上で衝突する。
- I2Cバス容量・配線長の増加
  - ロボット内の配線が長い、モータやESCONの近くを通る、GNDが弱い、などでSCL/SDA波形が崩れる可能性がある。
- プルアップ抵抗の重複または不足
  - 加速度センサ基板側とメイン基板側のプルアップが重複すると強すぎる場合がある。逆にプルアップがないと通信できない。
- 電源ノイズ
  - モータ駆動系、ESCON、ステッピングモータの電流変動で3.3V/GNDが揺れると、I2Cセンサが不安定になる。
- GPIOの機能衝突
  - ESP32ではADC2がWi-Fi使用中に不安定になる例がある。I2CでもブートストラップピンやUART共用ピンは避けた方がよい。

もう1つ加速度センサを追加する場合の方針:

1. 同じI2Cバスに同一アドレスのセンサを2個直結しない。
2. 可能ならI2Cマルチプレクサ、別I2Cバス、またはSPI対応IMUを使う。
3. ESP32に追加するなら、専用のI2Cバスとして空いているGPIOを使う。
4. Raspberry Piに直接追加するなら、まず既存I2Cバスのアドレス衝突を `i2cdetect` で確認する。

ESP32側で追加I2Cを作る場合の候補:

```text
SDA: GPIO25
SCL: GPIO26
```

理由:

- ADC2や入力専用ピンではない
- UART0のGPIO1/GPIO3を避けられる
- 既存のESCON/ステッピング/エンコーダで使っているGPIO18,19,21,22,23,32,33,34,35,36,39などと分離しやすい

ただし、実際には基板上で空いているか、他回路に接続されていないかを回路図で確認すること。

Raspberry Pi側に追加する場合:

```text
I2C1 SDA: GPIO2  / 物理ピン3
I2C1 SCL: GPIO3  / 物理ピン5
電源: 3.3V
GND: Pi GND
```

注意:

- Raspberry PiのI2Cは3.3V専用。5Vセンサを直結しない。
- 既存の別センサとアドレスが同じなら、そのままでは共存できない。
- モータノイズが強い場合は、Pi近傍にセンサを置くより、ESP近傍で読み取ってシリアル送信する方が安定する場合がある。

TODO:

- 加速度センサの具体的な取り付け位置と向きは、後でユーザーから共有される予定。
- 次に姿勢推定や3D表示を触るAIは、作業前に「加速度センサの位置と向きは共有済みか」を必ず確認すること。

## ステッピングモータ・スライダ表示

現状、Piに届いている主なステッピング情報は `step_hz` と電流値。

スライダの絶対位置はまだ取れていないため、3D上のスライダは以下のような表示になっている。

- `step_hz` があるときは動いているように表示
- コントローラ軸入力があるときはそれも表示に反映
- 実際のスライダ位置を保証するものではない

今後、ステップ数、原点センサ、リミットスイッチ、エンコーダなどで位置が取れるようになれば、3D表示を実位置連動にできる。

## 発生した問題と対応

### カメラが固まる、見えなくなる

症状:

- ブラウザ上でカメラ枠だけ表示され、映像が出ない
- `このサイトにはアクセスできない` と出ることがある
- 複数カメラ高解像度配信で不安定になる

対応:

- 解像度を `320x240` に下げた
- FPSを `5` に下げた
- JPEG qualityを `60` にした
- `camera_watchdog.sh` を追加した

### CADモデルが重い

症状:

- STLが約58MBあり、ブラウザ表示が重くなる可能性がある
- 実験中のリアルタイム性に悪影響が出る可能性がある

対応:

- 通常URLでは軽量モデルのみ表示
- CAD確認時だけ `?cad=1` を付ける

### 日本語文字化け

症状:

- 以前のHTMLで `3D蟋ｿ蜍｢` のような文字化けが発生した

対応:

- `robot_dashboard.html` をUTF-8で作り直した
- `<meta charset="utf-8">` を明示

### コントローラ入力をPiで直接読めない

構成:

- コントローラはPCに接続
- PCからPiへUDP送信
- Piのdashboard_server.pyがUDP 8091で受信

理由:

- 実験時にPC側で操作しながら、Piの画面に入力状態を表示したいため

## ログ

カメラ・ダッシュボード関連ログ:

```text
/home/haikan/pipe_robot_logs/camera_stream/
```

よく見るもの:

```bash
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/camera_dashboard_8090.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/global_left_8080.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/usb_16mp_8081.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/global_right_8082.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/camera_watchdog.log
```

## 状態確認コマンド

プロセス確認:

```bash
pgrep -af 'dashboard_server|mjpg_streamer|camera_watchdog'
```

API確認:

```bash
curl http://127.0.0.1:8090/api/state
```

カメラ確認:

```bash
curl -I http://127.0.0.1:8080/?action=snapshot
curl -I http://127.0.0.1:8081/?action=snapshot
curl -I http://127.0.0.1:8082/?action=snapshot
```

## 今後やるとよいこと

- IMU姿勢推定を改善する
  - 加速度のみではなくジャイロも使う
  - 取り付け向きのキャリブレーション値を保存する
- スライダ位置を実測値で表示する
  - ステップ数
  - 原点復帰
  - リミットスイッチ
  - エンコーダ
- カメラ設定を実験条件ごとに切り替え可能にする
  - 低遅延モード
  - 高画質記録モード
- Web画面からモータ指令を出す場合は、安全停止、リミット、非常停止を先に実装する

## AIへの注意

- 実験中はリアルタイム性を最優先すること
- CAD STLを通常表示で常時読み込ませないこと
- 既存のTailscale/VPN設定や他者の環境を壊さないこと
- Pi内の既存ディレクトリを勝手に大きく整理し直さないこと
- モータを動かすコードを書く場合は、必ず短時間・低速・停止処理つきにすること
- Web画面から駆動指令を出す前に、ユーザーへ安全確認を取ること

## 2026-07-12 追記: カメラ自動復旧、有線LAN、電磁弁

### カメラ配信の自動起動と復旧

Pi起動後に3台カメラとダッシュボードが自動起動するように、ユーザーcrontabへ以下を登録した。

```bash
@reboot sleep 20; /home/haikan/pipe_robot_dev/camera_stream/start_camera_dashboard.sh >> /home/haikan/pipe_robot_logs/camera_stream/autostart.log 2>&1 # pipe-robot-camera-dashboard
```

関係する主なファイル:

```text
/home/haikan/pipe_robot_dev/camera_stream/start_camera_dashboard.sh
/home/haikan/pipe_robot_dev/camera_stream/start_mjpg_3cams.sh
/home/haikan/pipe_robot_dev/camera_stream/stop_mjpg_3cams.sh
/home/haikan/pipe_robot_dev/camera_stream/camera_watchdog.sh
/home/haikan/pipe_robot_dev/camera_stream/install_camera_autostart.sh
```

`start_mjpg_3cams.sh` は `/dev/v4l/by-path` を優先してカメラを選ぶ。再起動やUSB抜き差しで `/dev/video0` などの番号が変わっても、物理ポートに近い名前で復旧しやすくするため。`camera_watchdog.sh` はスナップショット取得に失敗したカメラが続いた場合に `mjpg_streamer` を再起動する。

確認コマンド:

```bash
pgrep -af 'dashboard_server|mjpg_streamer|camera_watchdog'
curl -I http://127.0.0.1:8080/?action=snapshot
curl -I http://127.0.0.1:8081/?action=snapshot
curl -I http://127.0.0.1:8082/?action=snapshot
curl http://127.0.0.1:8090/api/state
```

PCから見るURL:

```text
http://192.168.50.154:8090/
http://192.168.50.154:8080/?action=stream
http://192.168.50.154:8081/?action=stream
http://192.168.50.154:8082/?action=stream
```

### 有線LANとWi-Fiの切り替え

実験時はPC、ルーター、Piを有線でつなぎ、普段はWi-Fiで使う方針。どちらでも同じIP `192.168.50.154` でアクセスしたいので、以下のスクリプトを作成した。

```text
/home/haikan/pipe_robot_dev/network/set_static_wifi_ip.sh
/home/haikan/pipe_robot_dev/network/restore_wifi_dhcp.sh
/home/haikan/pipe_robot_dev/network/set_static_eth_ip.sh
/home/haikan/pipe_robot_dev/network/use_wired_experiment_network.sh
/home/haikan/pipe_robot_dev/network/use_wifi_network.sh
/home/haikan/pipe_robot_dev/network/install_wired_wifi_failover.sh
/home/haikan/pipe_robot_dev/network/uninstall_wired_wifi_failover.sh
```

注意: NetworkManagerの設定変更にはsudo権限が必要。AI側からはパスワード入力できないため、ユーザーがPi上で実行する必要がある。

推奨は次のスクリプト:

```bash
cd /home/haikan/pipe_robot_dev/network
sudo ./install_wired_wifi_failover.sh
```

この設定を入れると、eth0が接続されているときは有線を優先し、Wi-Fi側を切って同じIPの重複を避ける。eth0が外れたときはWi-Fiを戻す。

2026-07-12時点の注意:

- 再起動直後に一度 `192.168.50.154` へSSHできた
- その時点では `wlan0` はまだDHCP表示だったため、固定IP/有線優先の設定は未適用の可能性が高い
- その後 `192.168.50.154` とTailscale IPのSSHがタイムアウトしたため、Piの現在IP確認または物理確認が必要

### 電磁弁制御

電磁弁はESP2側に実装した。押下中のみONにするデッドマン方式で、コマンドが途切れると自動OFFになる。

対象:

- 移動体用電磁弁 SY3320-5LZ-C4: 押し出し、引き込み
- 穿孔用電磁弁 SY3320-5LZ-C4: 押し出し、引き込み
- グラインダ用電磁弁 VXZ232シリーズ: ON/OFF

ESP2側の主な変更:

```text
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/src/config.h
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/src/main.cpp
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/SOLENOID_VALVES.md
```

現在のGPIOは未確定のため `config.h` では `PIN_VALVE_* = -1` としている。実配線が決まったら、ここを実GPIO番号に変更してPlatformIO/OTAで書き込む。

シリアルコマンド:

```text
VALVE,STATUS
VALVE,MOVE_PUSH,1
VALVE,MOVE_PUSH,0
VALVE,MOVE_PULL,1
VALVE,DRILL_PUSH,1
VALVE,DRILL_PULL,1
VALVE,GRINDER_AIR,1
VALVE,ALL,0
```

安全仕様:

- 押し出し/引き込みは同時ONしない
- コマンドが約350 ms途切れると自動OFF
- `VALVE,ALL,0` で全OFF

Pi側ダッシュボード:

- `dashboard_server.py` がESP2の `valve_*` テレメトリを読む
- `/api/valve?name=MOVE_PUSH&on=1` でHTTP制御できる
- UDP 8092でもコマンドを受けられる
- `robot_dashboard.html` に電磁弁状態表示パネルを追加した

補助スクリプト:

```text
/home/haikan/pipe_robot_dev/valve_command.py
/home/haikan/pipe_robot_dev/valve_controller_bridge.py
```

重要: ダッシュボード起動中は `/dev/ttyAMA4` を直接別プロセスで開かないこと。ESP2 UARTを複数プロセスが同時に読むと、文字化けやシリアル例外が発生した。ESP2への手動指令は、直接シリアルではなくダッシュボードAPIまたはUDP 8092経由を使う。

### 本体加速度センサ AE-KXR94-2050

ユーザーがロボット本体側の加速度センサをPiへ接続した。

接続メモ:

```text
AE-KXR94 X -> Raspberry Pi GPIO24
AE-KXR94 Y -> Raspberry Pi GPIO23
AE-KXR94 Z -> Raspberry Pi GPIO27
```

設置位置メモ:

```text
センサ座標系で、基準点はセンサから
x方向: 0 mm
y-方向: 8.517 mm
z-方向: 65.34 + 16 = 81.34 mm

dashboard_server.py内では以下で保持:
x = 0.0 mm
y = -8.517 mm
z = -81.34 mm
```

重要な注意:

- AE-KXR94-2050はアナログ電圧出力の加速度センサ
- Raspberry PiのGPIO24/23/27はアナログ電圧を読めない
- そのため、現在の直結構成では画面に表示できるのはGPIOの0/1生値のみ
- `roll_deg` / `pitch_deg` を正しく計算するには、MCP3008、ADS1115、またはESP32のADCなどを介して電圧を取得する必要がある

実装済み:

- `dashboard_server.py` に `body_accel` 状態を追加
- `robot_dashboard.html` に「本体加速度センサ」パネルを追加
- 現在は `raw_gpio`、ピン番号、設置位置、ADCが必要である旨を表示する

将来ADCを追加した場合の流れ:

1. X/Y/Zの電圧を読む
2. センサのゼロg電圧と感度から `ax_g`, `ay_g`, `az_g` に変換
3. `accel_to_orientation(ax, ay, az)` で `roll_deg`, `pitch_deg` を計算
4. 必要なら設置位置オフセットを使って、ロボット基準点での動的加速度補正を行う

## 2026-07-16 追記: 加速度センサ不安定化と実験運用まとめ

### 直近の状況

AE-KXR94-2050をRaspberry PiのGPIO24/23/27へ直接つないだ後から、PiのSSH、カメラ配信、ダッシュボードが不安定になった。ユーザーが加速度センサを外した後も、2026-07-16時点ではPCが `CIT-Wi-Fi` 側に接続されており、Pi側ネットワーク `aokilab2 / 192.168.50.x` に入れていないため、SSH復帰確認は未完了。

PC側確認結果:

```text
PC Wi-Fi: CIT-Wi-Fi
PC IP: 10.97.154.122
aokilab2: 接続試行時に「network specified by profile aokilab2 is not available」
192.168.50.154: SSH/API/camera timeout
```

次に確認すること:

```powershell
netsh wlan show interfaces
netsh wlan connect name="aokilab2" interface="Wi-Fi"
ssh -i C:\Users\minam\yes haikan@192.168.50.154
```

`aokilab2` が見えない場合は、ルーター、Pi電源、PiのWi-Fi接続状態を物理的に確認する。

### AE-KXR94-2050の接続方針

AE-KXR94-2050はアナログ電圧出力センサなので、Raspberry Pi GPIOへ直接接続してはいけない。

理由:

- PiのGPIOはデジタル入力で、アナログ電圧を読めない
- 3.3Vを超える電圧が入るとPiが不安定化または破損する可能性がある
- X/Y/Zの傾き計算には、0/1ではなく電圧値が必要

推奨接続:

```text
AE-KXR94 X/Y/Z -> ESP32側のACCE1_X/Y/Z または外付けADC
AE-KXR94 Vcc   -> 3.3V
AE-KXR94 GND   -> GND共通
```

ESP32側のACCE1がADC入力につながっているなら、Pi直結よりACCE1を使う。OTAは書き込み時だけ使うなら問題ない。ただし、OTA中はESP32が再起動し、センサ送信、モータ制御、電磁弁制御が一瞬止まるため、実験中にはOTAしない。

### 実験時のネットワーク構成

基本構成:

```text
PC -- 有線LAN -- ルーター -- 有線LAN -- Raspberry Pi
```

普段の開発:

```text
PC -- Wi-Fi(aokilab2) -- ルーター -- Wi-Fi -- Raspberry Pi
```

目標:

- 実験時は有線優先
- 有線が抜けたらWi-Fiへ戻る
- 可能なら同じIP `192.168.50.154` でアクセスする

関連スクリプト:

```text
/home/haikan/pipe_robot_dev/network/install_wired_wifi_failover.sh
/home/haikan/pipe_robot_dev/network/uninstall_wired_wifi_failover.sh
/home/haikan/pipe_robot_dev/network/use_wired_experiment_network.sh
/home/haikan/pipe_robot_dev/network/use_wifi_network.sh
/home/haikan/pipe_robot_dev/network/set_static_wifi_ip.sh
/home/haikan/pipe_robot_dev/network/set_static_eth_ip.sh
```

注意: NetworkManagerの設定変更にはsudoが必要。AI側からsudoパスワード入力はできないので、ユーザーがPi上で実行する。

### 配信の起動・確認・停止

起動:

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

ログ:

```bash
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/camera_dashboard_8090.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/camera_watchdog.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/global_left_8080.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/usb_16mp_8081.log
tail -n 100 /home/haikan/pipe_robot_logs/camera_stream/global_right_8082.log
```

### PCコントローラ入力の共有

PCに接続したコントローラ入力をPiダッシュボードへUDP送信する。

PC側:

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools
.\start_controller_sender.ps1
```

停止:

```powershell
.\stop_controller_sender.ps1
```

中身:

```text
pc_controller_sender.py -> PiのUDP 8091へ送信
dashboard_server.py -> UDP 8091を受信してcontroller欄に表示
```

### モータ・電流値の確認

ESCON/DCモータ、ステッピングモータ電流、回転数などはESP2側で取得し、PiへUART送信する。

関係ファイル:

```text
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/src/main.cpp
/home/haikan/pipe_robot_dev/esp32_escon_telemetry/src/config.h
/home/haikan/pipe_robot_dev/camera_stream/dashboard_server.py
```

PC側のテストスクリプト:

```text
raspi_tools/escon_drive_log_test.py
raspi_tools/escon_speed_ramp_log_test.py
raspi_tools/stepper_current_log_test.py
raspi_tools/hole_expand_command.py
```

注意:

- モータを動かす前にロボットが浮いたり転倒したりしない状態にする
- テスト時は短時間、低速、停止処理つきで行う
- ESCONのEnable、回転方向、アナログ指令、AO1/AO2設定を変更した場合は、このREADMEかESCON用READMEに残す
- ESP2がダッシュボードに接続中のとき、同じUARTを別プロセスで直接開かない

### 電磁弁の動かし方

電磁弁は押下中のみONにする。コマンドが途切れたら自動OFFにする設計。

HTTP API例:

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

PCコントローラ連携:

```bash
python3 /home/haikan/pipe_robot_dev/valve_controller_bridge.py
```

対象名:

```text
MOVE_PUSH
MOVE_PULL
DRILL_PUSH
DRILL_PULL
GRINDER_AIR
ALL
STATUS
```

安全仕様:

- `MOVE_PUSH` と `MOVE_PULL` は同時ONしない
- `DRILL_PUSH` と `DRILL_PULL` は同時ONしない
- 約350msコマンドが途切れたらOFF
- 緊急時は `VALVE,ALL,0` または `/api/valve?name=ALL&on=0`

### 次に作業するAIへの注意

- Piが見えないとき、まずPCが `aokilab2` または実験用有線LANにいるか確認する
- AE-KXR94をPi GPIOへ直結しない
- 加速度センサはACCE1またはADC経由にする
- ダッシュボード起動中にESP2 UARTを二重に開かない
- モータ、電磁弁、グラインダを動かす前に、ユーザーへ安全確認を取る
- 実験中はカメラ配信と状態表示のリアルタイム性を優先する

### 2026-07-16 ACCE1での加速度取得

AE-KXR94-2050をESP32メイン基板のACCE1へ接続したため、ESP2ファームとダッシュボードを更新した。

想定ピン:

```text
ACCE1_X -> ESP32 GPIO33
ACCE1_Y -> ESP32 GPIO25
ACCE1_Z -> ESP32 GPIO26
```

ESP32テレメトリへ追加した値:

```text
acce1_x_v
acce1_y_v
acce1_z_v
acce1_x_g
acce1_y_g
acce1_z_g
```

ダッシュボードでは `body_accel` として表示する。ゼロG電圧と感度の初期値は以下。

```text
zero_g = 1.65 V
sensitivity = 0.66 V/g
```

注意:

- GPIO25/26はESP32のADC2
- OTA/Wi-Fi有効時はADC2が読めず、Y/Zが `0.128V` 付近に張り付くことがある
- 対策として、ESP2ファームは起動後30秒だけOTAを受け付け、その後Wi-FiをOFFにする
- OTAを書き込みたい場合はESP32をリセットし、30秒以内に `pio run -e esp32dev_ota_wifi -t upload` を実行する

確認値の例:

```text
acce1_x_v=1.6150, acce1_y_v=2.3416, acce1_z_v=1.7180
acce1_x_g=-0.0530, acce1_y_g=1.0479, acce1_z_g=0.1030
roll=84.4 deg, pitch=2.9 deg
```
