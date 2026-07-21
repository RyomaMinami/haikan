# PCコントローラ操作メモ

更新日: 2026-07-20

## 目的

PCに接続したゲームコントローラから、Raspberry Piの配信サーバを経由してロボットを操作する。
現在は以下を同じプログラムで扱う。

- 電磁弁: Raspberry Pi GPIOを直接ON/OFFする。
- ステッピングモータ: ESP32へ `AXIS,ABS_X,<0..1023>` を送る。
- DCモータ: ESP32へ `AXIS,ABS_Y,<0..1023>` を送る。
- コントローラ状態: 配信画面へUDPで表示する。

## 起動前確認

Raspberry Pi側で配信サーバが動いていることを確認する。

```text
http://192.168.50.154:8090/robot_dashboard.html
```

PC側でコントローラを確認する。

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk
python .\raspi_tools\pc_valve_controller_sender.py --list
```

## 起動方法

通常はこれを使う。

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk
.\raspi_tools\start_pc_valve_controller.ps1
```

直接起動する場合:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --pi-host 192.168.50.154 --deadzone 0.18
```

有線直結でPiを `192.168.0.218` にした場合:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --pi-host 192.168.0.218 --deadzone 0.18
```

## 現在の操作割り当て

```text
左/右スティック軸(axis 0) -> ステッピングモータ速度
前/後スティック軸(axis 1) -> DCモータ速度

button 0  -> DRILL_PUSH   ドリル押し出し。押している間だけON。
button 1  -> DRILL_PULL   ドリル引き込み。押している間だけON。
button 3  -> MOVE_PUSH    移動体押し出し。押している間だけON。
button 4  -> MOVE_PULL    移動体引き込み。押している間だけON。
button 10 -> GRINDER_AIR  グラインダ用エアON。
button 11 -> GRINDER_AIR  グラインダ用エアOFF。
```

何も触っていない状態で少し動いただけではモータが動かないように、不感帯は初期値 `0.18` としている。
必要なら起動時に `--deadzone 0.25` のように大きくする。

## 軸方向の調整

ステッピングモータの左右が逆の場合:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --invert-step
```

DCモータの前後が逆の場合:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --no-invert-motor
```

コントローラの軸番号が違う場合:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --step-axis 0 --motor-axis 1
```

## Raspberry Pi GPIO割り当て

電磁弁はESP2ではなくRaspberry Pi GPIOを直接ON/OFFする。
2026-07-20時点の割り当て:

```text
DRILL_PUSH   -> GPIO17
DRILL_PULL   -> GPIO18
MOVE_PULL    -> GPIO19
MOVE_PUSH    -> GPIO20
GRINDER_AIR  -> GPIO22
```

コピー前環境では `SOL_A=17`, `SOL_B=18`, `SOL_C=19`, `SOL_D=20`, `SOL_E=21`, `SOL_F=22` だった。
GPIO21は押下時にステッピングモータ保持音と思われるモスキート音が変化し、電磁弁としては使わない方針にした。

## 安全仕様

- ドリル押し出し/引き込み、移動体押し出し/引き込みは同時ONしないようPi側でインターロックする。
- 押しっぱなし系の電磁弁は、PC側からON信号が途切れるとPi側ウォッチドッグで自動OFFする。
- Ctrl+Cで終了すると `AXIS,ABS_X,512`, `AXIS,ABS_Y,512`, `VALVE,ALL,0` を送る。
- ステッピングモータは停止中も保持電流が流れるため、保持音が出ることがある。保持が必要なので現時点では許容する。

## 手動テスト

全OFF:

```powershell
Invoke-WebRequest -UseBasicParsing "http://192.168.50.154:8090/api/valve?name=ALL&on=0"
```

短時間だけ移動体押し出し:

```powershell
Invoke-WebRequest -UseBasicParsing "http://192.168.50.154:8090/api/valve?name=MOVE_PUSH&on=1"
Start-Sleep -Milliseconds 200
Invoke-WebRequest -UseBasicParsing "http://192.168.50.154:8090/api/valve?name=MOVE_PUSH&on=0"
```

## 2026-07-20のボタン確認メモ

- グラインダは実測で `button 10 = ON`, `button 11 = OFF`。
- `button 12` はコントローラが12ボタン機の場合、Python上では存在しない可能性が高い。
- 移動体は昨日動作していた系に戻し、`button 3/4` を使う。
- 操作中にPowerShellへ `pressed=[...]` が出るので、反応しないときはその番号を見て `--map move_push=<番号>` のように上書きする。

## 今日の作業予定

2026-07-20の予定:

1. コントローラ操作を詰める。
2. ステッピングモータの電流値を最適化する。
3. LANケーブル配線は他の人に任せるが、実験前に通信確認を行う。
