# 穴拡張動作モード メモ

穿孔後の穴拡張作業用に、ESP32側へ `EXPAND` コマンドを追加した。
DCモータでグラインダを回し、ステッピングモータで所定ステップだけ送り、一定時間保持してから戻る。

## 基本コマンド

PiまたはPCからESP32へ1行送信する。

```text
EXPAND,START,dc=70,feed_steps=400,feed_hz=500,dwell_ms=300,passes=1
EXPAND,STOP
EXPAND,HOME
EXPAND,STATUS
```

## パラメータ

- `dc`: DCモータ指令。既定値は `70`、安全上 `±140` で制限。
- `feed_steps`: 拡張方向へ送るステッピングモータのステップ数。
- `retract_steps`: 戻すステップ数。省略時は `400`。
- `feed_hz`: ステップ周波数。既定値は `500 Hz`、最大 `1500 Hz`。
- `spinup_ms`: DCモータを先に回して安定させる時間。
- `dwell_ms`: 外側まで送った後、その位置で削る保持時間。
- `pass_pause_ms`: 複数パス時の待ち時間。
- `passes`: 拡張動作の繰り返し回数。最大 `20`。

## 動作順序

1. DCモータを `dc` で回す。
2. `spinup_ms` 待つ。
3. ステッピングモータを `feed_steps` だけ送る。
4. `dwell_ms` 保持する。
5. `retract_steps` だけ戻す。
6. `passes` 分だけ繰り返す。
7. DCモータとステッピングモータを停止する。

## 安全系

- ジョイスティックの `BTN_TRIGGER` で非常停止が入ると、拡張動作は中断される。
- `EXPAND,STOP` でも中断できる。
- 拡張動作中は通常の手動 `AXIS` 入力より拡張モードを優先する。
- `EXPAND,HOME` はESP内の推定ステップ位置を0に戻すためのコマンド。

## テレメトリ

既存の `TEL` 行へ以下を追加した。

- `mode`: `manual` または `expand`
- `expand_state`: `spinup`, `feed_out`, `dwell`, `retract`, `done` など
- `expand_pass`: 現在のパス番号
- `expand_passes`: 総パス数
- `expand_est_step`: ESP側の推定ステップ位置

## 注意

この実装は実験用の最小構成で、ステップ数はLEDCのステップ周波数と時間から推定している。
高精度な絶対位置が必要な場合は、ステップパルスを割り込みまたは専用カウンタで数える実装に発展させる。
