# 配管ロボット 別PCセットアップ手順

この手順書は、実験用PCとは別のWindows PCで、配管ロボットの操作・配信画面表示・停止コマンドを使えるようにするためのものです。

## 1. 必要なもの

- Windows PC
- 有線LANアダプタ、またはPC内蔵LANポート
- コントローラ
- Python 3
- Git
- Raspberry Piと通信できるLAN接続

実験時のRaspberry Piの有線LAN IPは次を想定しています。

```text
192.168.0.218
```

## 2. GitとPythonの確認

PowerShellを開いて確認します。

```powershell
git --version
python --version
```

どちらかが認識されない場合は、先にGitまたはPythonをインストールしてください。

## 3. プログラムを取得する

作業したい場所で次を実行します。

```powershell
git clone https://github.com/RyomaMinami/haikan.git
cd haikan
```

すでにclone済みの場合は、最新版に更新します。

```powershell
cd haikan
git pull
```

## 4. Pythonライブラリを入れる

```powershell
python -m pip install -r requirements_pc.txt
```

主にPC接続コントローラを読むために `pygame` を使用します。

## 5. 有線LANの接続確認

PCとRaspberry Piを有線LANで接続します。

接続後、PowerShellで確認します。

```powershell
ping 192.168.0.218
```

応答があれば通信できています。

ブラウザで次を開けるか確認します。

```text
http://192.168.0.218:8090/robot_dashboard.html
```

開けない場合は、Pi側の電源、LANケーブル、PC側IP設定、Pi側システム起動状態を確認してください。

## 6. 実験ランチャーを起動する

コントローラとLANを接続した状態で実行します。

```powershell
cd haikan\raspi_tools
.\start_pc_experiment_launcher.ps1
```

このコマンドで行うこと:

- Piの配信/APIが起動しているか確認
- 配信画面をPCブラウザで開く
- PC接続コントローラの入力をPiへ送信
- PC側PowerShellに、受信状態、モータ状態、電磁弁状態を表示

停止するときは `Ctrl+C` を押します。停止時にはモータ中立、電磁弁OFFを送ります。

## 7. ロボット状態だけ確認する

```powershell
cd haikan\raspi_tools
.\show_robot_status.ps1
```

表示される主な項目:

- controller: PCコントローラの認識状態
- axes: コントローラの軸入力
- buttons: 押されているボタン
- motor: DCモータ状態
- step_hz: ステッピングモータ指令
- valves: 電磁弁状態

## 8. 操縦を停止する

ロボットを安全側に戻すため、PCから停止指令を送ります。

```powershell
cd haikan\raspi_tools
.\stop_robot_operation.ps1
```

リポジトリ直下 `haikan` にいる場合は、次のように実行します。

```powershell
.\raspi_tools\stop_robot_operation.ps1
```

PC側のコントローラ送信プログラムも止めたい場合:

```powershell
.\stop_robot_operation.ps1 -StopPcSender
```

送信される内容:

- DCモータ中立
- ステッピングモータ停止
- 電磁弁すべてOFF

## 9. Pi側システムを再起動する

Pi側の配信・ログ記録・録画などのproductionシステムを再起動します。

```powershell
cd haikan\raspi_tools
.\restart_pi_system.ps1
```

SSH鍵の場所が違う場合:

```powershell
.\restart_pi_system.ps1 -SshKey C:\Users\ユーザー名\yes
```

このコマンドは、次のSSH接続ができることが前提です。

```powershell
ssh -i C:\Users\ユーザー名\yes haikan@192.168.0.218
```

## 10. Wi-Fi側で使う場合

Wi-Fiで接続する場合は、PiのIPを指定します。

```powershell
cd haikan\raspi_tools
.\start_pc_experiment_launcher.ps1 -PiHost 192.168.50.154
```

状態確認や停止コマンドも同様に `-PiHost` を指定できます。

```powershell
.\show_robot_status.ps1 -PiHost 192.168.50.154
.\stop_robot_operation.ps1 -PiHost 192.168.50.154
```

## 11. よくある問題

### `param` が認識されない

古いスクリプトを使っている可能性があります。

```powershell
git pull
```

で最新版に更新してください。

### Pi APIに接続できない

LANがつながっていない、PiのIPが違う、Pi側システムが起動していない可能性があります。

確認:

```powershell
ping 192.168.0.218
```

```powershell
.\show_robot_status.ps1
```

### コントローラが効かない

次を確認してください。

- コントローラがPCに接続されているか
- `start_pc_experiment_launcher.ps1` が起動しているか
- PowerShell上で `axes` の値が動くか
- ブラウザの配信画面に controller 情報が出ているか

### カメラ3台目を挿すとPiが落ちる

Pi側で過去に低電圧が出ている可能性があります。

```powershell
ssh -i C:\Users\ユーザー名\yes haikan@192.168.0.218 "vcgencmd get_throttled"
```

`0x50000` などが出る場合、過去に低電圧やスロットリングが発生しています。
USBカメラ3台はセルフパワーUSBハブに接続することを推奨します。

## 12. 実験時の基本手順

1. Piの電源を入れる
2. PCとPiを有線LANで接続する
3. コントローラをPCに接続する
4. 必要ならカメラを接続する
5. PowerShellで次を実行する

```powershell
cd haikan\raspi_tools
.\start_pc_experiment_launcher.ps1
```

6. 配信画面とPowerShellの状態表示を確認する
7. 実験終了時は `Ctrl+C`
8. 念のため停止指令を送る

```powershell
.\stop_robot_operation.ps1
```
