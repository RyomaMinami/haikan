# 配管ロボット実験システム

Windows PC、Raspberry Pi、ESP32を使った配管ロボットの操縦・カメラ配信・センサ監視・3D状態表示をまとめたリポジトリです。

現行の本番構成、コントローラ操作、速度制御、通信、安全仕様、URDF表示については、次の資料を参照してください。

- [操縦・リアルタイム表示システム](raspi_tools/README_ROBOT_CONTROL_AND_VISUALIZATION.md)
- [PC実験ランチャー](raspi_tools/README_PC_EXPERIMENT_LAUNCHER.md)
- [PCコントローラ操作](raspi_tools/README_PC_VALVE_CONTROLLER.md)
- [ダッシュボード引き継ぎメモ](raspi_tools/camera_stream/README_robot_dashboard_handover.md)

## 最短の起動手順

### 1. PCを準備

Windows、Python 3.11系、Logitech Extreme 3D Proを使用します。

```powershell
git clone https://github.com/RyomaMinami/haikan.git
cd haikan
python -m pip install -r requirements_pc.txt
```

### 2. Raspberry Piとの通信を確認

有線LANの標準IPは `192.168.0.218` です。

```powershell
ping 192.168.0.218
```

Wi-Fi側のIP `192.168.50.154` を使う場合は、PCも同じ `192.168.50.x` ネットワークへ接続してください。

### 3. 操縦と表示をまとめて起動

リポジトリ直下で実行します。

```powershell
.\raspi_tools\start_pc_experiment_launcher.ps1
```

PCローカルのダッシュボードが開きます。

```text
http://127.0.0.1:8095/robot_dashboard.html?pi=192.168.0.218&cad=1
```

Wi-Fi接続時:

```powershell
.\raspi_tools\start_pc_experiment_launcher.ps1 -PiHost 192.168.50.154
```

### 4. 停止

起動中のPowerShellで `Ctrl+C` を押すと、モータ中立・全電磁弁OFFを送って終了します。別のPowerShellから停止する場合:

```powershell
.\raspi_tools\stop_robot_operation.ps1 -StopPcSender
```

## Raspberry Piを起動していない場合

PCローカル画面と3Dモデルは開けますが、`/api/state`、実センサ値、カメラ映像、ロボット操作はオフラインになります。3D画面の「センサ入力テスト」を使うと、Piなしでジョイント動作を確認できます。

## 公開リポジトリに含めないもの

- SSH秘密鍵、パスワードを追加したローカル設定
- 実験ログ、録画映像、学習データ
- 不要な中間CAD、重複したSTL、作業用スクリーンショット
- 個人情報を含むファイル

URDF、表示に必要な分割メッシュ、検証済み一体STLと部品対応表はリポジトリに含めています。
