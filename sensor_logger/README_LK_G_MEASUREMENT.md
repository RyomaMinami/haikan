# LK-G85A / DL50 Hi pipe measurement tools

このフォルダは、配管内くぼみ計測実験で使用したPC側の計測・可視化・評価プログラムをまとめたものです。

対象機器:

- KEYENCE LK-G85A
- KEYENCE LK-G3000
- SICK DL50 Hi
- KRS-9004 / ICS系サーボ
- 車輪用連続回転サーボ

## 重要な注意

KEYENCEのDLLはGitHubには含めていません。

ローカルで使用する場合は、KEYENCE配布物から次のDLLを `sensor_logger` フォルダに配置してください。

```text
sensor_logger/
  LkIF.dll
  KeyUsbDrv.dll
```

Pythonと `LkIF.dll` のbit数は一致している必要があります。

例:

- 64bit Python には 64bit DLL
- 32bit Python には 32bit DLL

bit数が違うと、次のようなエラーになります。

```text
OSError: [WinError 193] %1 is not a valid Win32 application
```

## インストール

リポジトリ直下で実行します。

```powershell
python -m pip install -r requirements_sensor_logger.txt
```

## 最小構成の動作確認

### 1. DL50 Hi単体確認

DL50 Hiは、実験時は `115200 bps, 7E1, continuous` で安定して取得できました。

```powershell
cd sensor_logger
python dl50_probe.py --port COM10 --baud 115200 --bytesize 7 --parity E --request
```

連続出力形式は次のような値として読めます。

```text
+0023993\r\n
```

値は10で割ってmmに変換します。

### 2. LK-G3000単体確認

```powershell
cd sensor_logger
python lk_probe.py
```

正常例:

```text
DLL loaded.
LKIF_GetCalcData: found
1: ok=True OUT1=-21.521999 (VALID) OUT2=-9.685000 (VALID)
```

### 3. LK-G + DL50 同時取得

```powershell
cd sensor_logger
python sensor_logger.py --dl50-port COM10 --csv both_log.csv
```

CSV列:

```text
pc_time
elapsed_s
lk_out1_mm
lk_out2_mm
lk_out1_status
lk_out2_status
dl50_hi_mm
dl50_raw
```

## 主な計測プログラム

### sensor_logger.py

LK-G3000とDL50 Hiを同時取得し、CSVに保存する最小構成のロガーです。

### step_trigger_logger.py

DL50 Hiの初期値をキー入力で記録し、距離が一定量増えるごとにLK-Gの値を記録します。

### angle_step_trigger_logger.py

角度ごとに距離ステップ計測を行うための手動・半自動ロガーです。

### auto_angle_wheel_scan_logger.py

レーザ角度と車輪移動を組み合わせて、配管軸方向と角度方向の点群データを自動取得するためのプログラムです。

基本例:

```powershell
python auto_angle_wheel_scan_logger.py `
  --dl50-port COM10 `
  --servo-port COM8 `
  --laser-id 5 `
  --wheel-id 4 `
  --angle-start -115 `
  --angle-end 115 `
  --angle-step 5 `
  --step-mm 1 `
  --csv auto_scan.csv
```

実験では、始点と終点を固定して補正しながら計測する運用も行いました。

## 可視化・点群処理

### pipe_surface_visualizer.py

計測CSVから配管内面の点群・表面マップをHTMLで作成します。

```powershell
python pipe_surface_visualizer.py --input auto_scan.csv --base-radius-mm 120 --invert-lk
```

### axis_correct_point_cloud.py

得られた点群から配管軸を推定し、軸補正後の点群を作成します。

### axis_edge_detect.py

LK-G85Aの特性を考慮し、配管軸方向に強いエッジを中心にエッジ候補を抽出します。

### axis_edge_template_match.py

円形くぼみとして見えるはずのエッジ形状と、実測データをテンプレートマッチングします。

### axis_dent_region_from_match.py

テンプレートマッチング結果から、くぼみ領域と中心候補を推定します。

### combined_pipe_analysis_view.py

生データ、軸補正データ、円形エッジ評価、くぼみ評価などをまとめたHTMLを作成します。

## 中心推定・移動

### move_to_detected_dent_center.py

推定されたくぼみ中心へ、DL50 Hiとサーボ/車輪を用いて自動移動するためのプログラムです。

### move_to_resolution_center.py

分解能条件ごとに推定された中心へ移動します。

### move_resolution_centers_sequence.py

複数条件の推定中心へ順番に移動します。写真評価用に、Enterで次の条件へ進む運用を想定しています。

## 分解能・手法評価

### resolution_ablation_study.py

既存の高分解能データを間引き、角度間隔や距離間隔を変えた場合の中心推定結果を比較します。

### processing_method_study.py

フィルタや前処理の違いによる中心推定の変化を評価します。

### processing_resolution_matrix.py

処理方法と分解能条件を組み合わせた比較表を作ります。

### detection_method_study.py

くぼみ検出方法そのものの違いを比較します。

### evaluate_detection_method_photo_accuracy.py

外側写真から、真値穴中心とレーザー照射位置の誤差を評価します。

## LK-Gのみでの距離推定に関する検討

### lkg_only_distance_validation.py

DL50 Hiの距離を真値として、LK-G85Aの波形だけから移動量推定ができるか検証します。

### ridge_peak_valley_detection

配管内壁の畝の山・谷を検出し、畝間隔や特徴点から距離推定に使えるかを検討するための出力フォルダです。

GitHubには大きな出力画像やCSVは含めていません。必要な場合はローカルまたはGoogle Drive側の研究資料を参照してください。

## GitHubに含めないもの

次は意図的にGitHub管理から外しています。

- KEYENCE DLL
- 生CSV
- 大きなHTML可視化結果
- 写真評価の画像
- 生成済み点群CSV
- 実験ログ

理由:

- GitHubリポジトリが数GBになってしまう
- KEYENCE DLLは配布権限の問題がある
- 実験結果はGoogle Drive等で管理した方が共有しやすい

## 推奨する実験時の流れ

1. `lk_probe.py` でLK-G3000の値を確認
2. `dl50_probe.py` でDL50 Hiの値を確認
3. `auto_angle_wheel_scan_logger.py` で自動計測
4. `pipe_surface_visualizer.py` で簡易可視化
5. `axis_correct_point_cloud.py` で軸補正
6. `axis_edge_template_match.py` と `axis_dent_region_from_match.py` でくぼみ中心推定
7. `move_to_detected_dent_center.py` で中心位置へ移動
8. 必要に応じて写真評価、分解能評価、手法比較を実施
