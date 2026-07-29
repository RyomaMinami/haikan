# Haikan Fusion joint exporter

Fusionで現在開いているアセンブリから、通常ジョイントと位置固定ジョイントの
種類、軸、中心、制限値、接続コンポーネントを出力します。

## 実行

1. Fusionでジョイント設定済みの `robot_model_pipe` を開く。
2. `ユーティリティ` → `アドイン` → `スクリプトとアドイン` を開く。
3. `スクリプト` で `HaikanJointExporter` を選び、`実行` を押す。
4. 現在のユーザーの `Downloads\haikan_fusion_joints.json` が生成される。

Fusion APIの内部長さ単位はcmなので、出力時にmmへ変換しています。
