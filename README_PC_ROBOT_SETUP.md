# Pipe robot PC-side setup

別PCでロボットを操作するためのPC側セットアップメモです。

## 必要なもの

- Windows PC
- Python 3.11系
- PCに接続したコントローラ
- Raspberry Piと通信できるLAN
- このリポジトリ一式

有線LANの実験構成では、Raspberry Piは次のIPを使います。

```text
192.168.0.218
```

Wi-Fi側では次のIPを使っていました。

```text
192.168.50.154
```

## 初回セットアップ

GitHubからcloneした後、リポジトリ直下で実行します。

```powershell
python -m pip install -r requirements_pc.txt
```

LK-G85A / DL50 Hi計測・点群解析も同じPCで使う場合は、追加で次を実行します。

```powershell
python -m pip install -r requirements_sensor_logger.txt
```

LK-G関連の詳しい説明は次を参照してください。

```text
sensor_logger/README_LK_G_MEASUREMENT.md
```

通信確認:

```powershell
ping 192.168.0.218
```

ダッシュボード確認:

```text
http://192.168.0.218:8090/robot_dashboard.html
```

## 実験ランチャー

Piの状態確認、PCコントローラ送信開始、配信画面オープンをまとめて行います。

```powershell
cd .\raspi_tools
.\start_pc_experiment_launcher.ps1
```

Wi-Fi側で使う場合:

```powershell
.\start_pc_experiment_launcher.ps1 -PiHost 192.168.50.154
```

## 状態確認

```powershell
cd .\raspi_tools
.\show_robot_status.ps1
```

## ロボット操縦停止

PC側からモータ中立、電磁弁OFFを送ります。

```powershell
cd .\raspi_tools
.\stop_robot_operation.ps1
```

PC側のコントローラ送信プログラムも同時に止めたい場合:

```powershell
.\stop_robot_operation.ps1 -StopPcSender
```

## Pi側自動システム再起動

Pi側のproductionシステムを停止してから再起動します。

```powershell
cd .\raspi_tools
.\restart_pi_system.ps1
```

SSH鍵の場所が違う場合:

```powershell
.\restart_pi_system.ps1 -SshKey C:\Users\ユーザー名\yes
```

このコマンドはPiへSSHできることが前提です。

```powershell
ssh -i C:\Users\ユーザー名\yes haikan@192.168.0.218
```

## GitHubに上げるときの注意

次のようなファイルはGitHubに含めない方が安全です。

- SSH秘密鍵
- 実験ログ
- 録画データ
- 大きい画像、動画
- 個人情報を含むファイル

別PCで使うだけなら、まずはPC操作に必要な `raspi_tools` と `requirements_pc.txt` を中心に管理すれば十分です。

## USBカメラ3台でPiが落ちる場合

`vcgencmd get_throttled` で `0x50000` が出た場合、過去に低電圧とスロットリングが発生しています。
3台目のUSBカメラを挿した瞬間にPiが落ちる場合は、USBカメラの突入電流や5V降下が原因である可能性が高いです。

対策:

- 3台のUSBカメラはセルフパワーUSBハブに接続する
- Pi本体のUSBポートから3台分のカメラ電源を取らない
- カメラを挿す順番を固定し、起動直後に同時認識させない
- 実験前に `vcgencmd get_throttled` を確認する
- `throttled=0x0` 以外の場合は、電源系を見直してから実験する

確認コマンド:

```powershell
ssh -i C:\Users\ユーザー名\yes haikan@192.168.0.218 "vcgencmd get_throttled; lsusb; ls -l /dev/video*"
```
