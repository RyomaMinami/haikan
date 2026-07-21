# PC experiment launcher

PC側で実験開始時の抜けを減らすための起動プログラムです。

## 目的

次を1つのコマンドで行います。

- Raspberry Piの配信/APIが起動しているか確認する
- Piへ `PC_HELLO` を送信する
- 配信画面をPC側ブラウザで開く
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
- 動かない場合は、表示の `controller axes` が変化しているかを最初に確認します。変化していなければPC側のコントローラ認識、変化しているのに `duty` や `step_hz` が変わらなければPi/ESP側の指令変換を確認します。
