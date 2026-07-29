# PC experiment launcher

PC側で実験開始時の抜けを減らすための起動プログラムです。

## 目的

次を1つのコマンドで行います。

- Raspberry Piの配信/APIが起動しているか確認する
- Piへ `PC_HELLO` を送信する
- PCローカルのダッシュボードサーバを `127.0.0.1:8095` で起動する
- PC側ブラウザでローカル画面を開く
- Piの `/api/state` をPCローカル画面へ中継する
- PC接続コントローラの送信プログラムを起動する
- Pi側で受信しているコントローラ値、モータ状態、電磁弁状態をPC側に表示する
- Ctrl+C時にモータ中立、電磁弁OFFを送る

## 起動方法

有線LANの実験構成ではPiを `192.168.0.218` として扱います。

```powershell
cd C:\Users\minam\Documents\Codex\2026-05-29\windows-pc-keyence-lk-g85a-lk\raspi_tools
.\start_pc_experiment_launcher.ps1
```

Wi-Fi側で使う場合は次のようにIPを指定します。

```powershell
.\start_pc_experiment_launcher.ps1 -PiHost 192.168.50.154
```

ブラウザで開く画面:

```text
http://127.0.0.1:8095/robot_dashboard.html?pi=192.168.50.154&cad=1
```

画面HTML、Three.js、URDF/CADモデル、3D姿勢計算、補間、警告判定はPC側に置かれる。Pi側にはPCで変更した画面ファイルを転送しない。PiからPCへ送られるのは状態JSONとカメラ映像で、PCからPiへはコントローラ／停止指令を送る。

## STLジョイント設定

運転画面とは別に、PiやWi-Fiがなくても使用できるPC専用設定画面がある。

```powershell
.\raspi_tools\start_robot_model_joint_editor.ps1
```

開くURL:

```text
http://127.0.0.1:8095/robot_model_joint_editor.html
```

設定画面では以下を行う。

- STLを連結部品単位でクリック選択
- センサ1（移動体）の回転ジョイントへ部品を割当
- センサ2（グラインダ周り）の回転ジョイントへ部品を割当
- 親子ジョイントを設定
- 回転軸、回転中心、倍率、オフセット、上下限を設定
- リニア軸と距離センサなどのデータ源を設定

「設定を保存」でPC上の `camera_stream/robot_model_config.json` に保存される。運転画面は起動時にこの設定を読み込む。標準のPCランチャーは設定済みCADモデルを使用し、軽量モデルで確認したい場合だけPythonランチャーへ `--lightweight-model` を指定する。

## 画面に表示される主な内容

- `controller axes`: PCコントローラの前後左右入力
- `buttons`: 押されているボタン番号
- `motor state`: DCモータ状態
- `duty`: DCモータ指令
- `step_hz`: ステッピングモータ指令
- `valves`: ONになっている電磁弁
- `serial`: PiからESP1/ESP2が開けているか

## 注意

- 既に `pc_valve_controller_sender.py` を手動で起動している場合、二重起動すると指令が競合します。
- 実験時はこのランチャーだけを起動する運用にすると安全です。
- ランチャーをCtrl+Cで終了すると、停止指令を送った後にPCローカル画面サーバも終了します。
- `127.0.0.1:8095` を別プログラムが使用している場合は、先にそのプログラムを終了してください。
- 動かない場合は、表示の `controller axes` が変化しているかを最初に確認します。変化していなければPC側のコントローラ認識、変化しているのに `duty` や `step_hz` が変わらなければPi/ESP側の指令変換を確認します。
