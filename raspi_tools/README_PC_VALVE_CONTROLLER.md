# PCコントローラ操作

更新日: 2026-07-29

Logitech Extreme 3D Proから、モータ速度とRaspberry Pi上の電磁弁を操作します。全体構成と速度変換の詳細は [操縦・リアルタイム表示システム](README_ROBOT_CONTROL_AND_VISUALIZATION.md) を参照してください。

## 推奨起動方法

操縦、PCローカル画面、Pi状態確認をまとめて起動します。

```powershell
.\raspi_tools\start_pc_experiment_launcher.ps1 -PiHost 192.168.0.218
```

コントローラ送信だけを起動する場合:

```powershell
.\raspi_tools\start_pc_valve_controller.ps1 -PiHost 192.168.0.218
```

コントローラ番号を確認:

```powershell
python .\raspi_tools\pc_valve_controller_sender.py --list
```

## 現在の本番割り当て

| 入力 | 動作 |
|---|---|
| 軸0 | ステッピングモータの方向・速度 |
| 軸1 | DCモータの正転・逆転 |
| 軸3 | DCモータの通常速度。最高指令の20%まで |
| ハット0 上下 | DCモータ専用最高速の正転・逆転 |
| ボタン7 | ドリル押し出し。押している間だけON |
| ボタン6 | ドリル引き込み。押している間だけON |
| ボタン9 | 走行輪押し付け。押している間だけON |
| ボタン8 | 走行輪引き込み。押している間だけON |
| ボタン10 | グラインダ空圧ON |
| ボタン11 | グラインダ空圧OFF |

軸の不感帯は `0.18` です。DCモータは、起動後に軸3を一度最低位置へ戻すまで出力されません。

通常運転では圧力制御を行いません。0.60 MPa制御を使うロール位置変更モード:

```powershell
.\raspi_tools\start_pc_experiment_launcher.ps1 `
  -PiHost 192.168.0.218 `
  -RollPositionChangeMode
```

## 通信

- UDP 8092: `AXIS,ABS_X`、`AXIS,ABS_Y`、`VALVE` の実操作指令
- UDP 8091: 軸、ボタン、ハットの表示用JSON
- 送信周期: 20 Hz
- 電磁弁保持再送: 50 ms

`pc_controller_sender.py` と `start_controller_sender.ps1` は表示用UDP 8091だけを送る旧版です。実機操作には使いません。

## Raspberry Pi GPIO

現行の `dashboard_server.py` が次のGPIOを直接駆動します。

| 論理名 | GPIO |
|---|---:|
| `DRILL_PUSH` | 17 |
| `DRILL_PULL` | 18 |
| `MOVE_PUSH` | 19 |
| `MOVE_PULL` | 20 |
| `GRINDER_AIR` | 22 |

押し出しと引き込みは同時ON禁止です。ON指令が350 ms途切れるとPi側ウォッチドッグが自動OFFします。

## 安全停止

起動中のPowerShellで `Ctrl+C` を押します。プログラムは次を複数回送信します。

```text
AXIS,ABS_X,512
AXIS,ABS_Y,512
VALVE,ALL,0
```

別のPowerShellから停止:

```powershell
.\raspi_tools\stop_robot_operation.ps1 -StopPcSender
```

## トラブル確認

1. PowerShellの `pressed=[...]` でボタン入力を確認
2. ダッシュボードの `controller` 更新時刻と軸値を確認
3. `valves` の論理状態を確認
4. `motor.duty`、`motor.pwm_out`、`motor.enabled`、`motor.rpm` を確認
5. コントローラ送信プログラムが二重起動していないか確認

コントローラ個体で番号が異なる場合は `--map move_push=9` のように上書きできます。本番割り当てを変更するときは `DEFAULT_BUTTON_MAP` と起動資料も同時に更新してください。
