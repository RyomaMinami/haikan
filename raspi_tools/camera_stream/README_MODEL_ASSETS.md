# 3Dモデル資産の更新

ダッシュボードはFusion配置を保った一体STLと、ACDC4Robotで出力したURDFの両方に対応します。

## リポジトリ内の実行時資産

```text
assets/
├─ robot_model.stl
├─ robot_model_components.bin
└─ robot_model_pipe_urdf2/
   ├─ robot_model_pipe_urdf2.urdf
   └─ meshes/
```

`robot_model.stl` と `robot_model_components.bin` は必ず同じ生成回の組にします。対応表の三角形数がSTLと違う場合、画面は読み込みを拒否します。

## URDFの更新

1. Fusionでリンクとジョイントを設定する
2. ACDC4RobotでURDFとメッシュを出力する
3. 出力フォルダを `assets/robot_model_pipe_urdf2/` と置き換える
4. URDF内のリンク名が次の4リンクを含むことを確認する

```text
motor_module-v1
drilling_module1-v1
drilling_module2-v1
bit-v1
```

## 一体STLの更新

各モジュールのSTLが同じアセンブリ座標で保存されている場合:

```powershell
python .\raspi_tools\camera_stream\build_module_stl_model.py `
  .\raspi_tools\camera_stream\assets\robot_model.stl `
  .\raspi_tools\camera_stream\assets\robot_model_components.bin `
  <base_link.stl> <motor_module.stl> <drilling_module1.stl> `
  <drilling_module2.stl> <bit.stl>
```

STLが各モジュールのローカル座標の場合は、Fusionアドイン `raspi_tools/fusion_joint_exporter/` でOccurrence変換を書き出し、`build_fusion_positioned_stl_model.py` を使います。

```powershell
python .\raspi_tools\camera_stream\build_fusion_positioned_stl_model.py `
  <haikan_fusion_joints.json> `
  <output.stl> <output_components.bin> `
  "<Fusion occurrence full path>=<module STL>"
```

単一STLを頂点接続だけで部品分割する場合はNumPyが必要です。

```powershell
python -m pip install numpy
python .\raspi_tools\camera_stream\build_stl_component_map.py `
  <robot_model.stl> <robot_model_components.bin>
```

## 配置とジョイントの検証

```powershell
.\raspi_tools\start_robot_model_joint_editor.ps1
```

4軸を最小・ゼロ・最大で確認し、「設定を保存」を押します。設定結果は `robot_model_config.json` に保存されます。

確認対象:

- `slider6`: `motor_module`
- `rotation9`: `drilling_module1`
- `slider11`: `drilling_module2`
- `rotation12`: `bit`

更新後はダッシュボードを再起動し、`?cad=1&demo=1` のセンサ入力テストで、配管表示を含めて確認してください。
