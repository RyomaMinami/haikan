# Windows / Inventor共通実行手順
Windows、起動済みInventor、Windows Python3.11以上とpywin32/pymupdfが必要。Inventor2024.3で確認。別版はローカル試験が必要。CAD操作を同じInventorプロセスへ並列実行しない。
原本/参照に未保存変更があれば、既存文書へSaveしない。利用者の保存済みスナップショットを使う。原本不変ハッシュ、既存ユーザー文書維持、共有スタイル不変更を守る。自分で開いた文書だけClose(True)、SilentOperationはfinallyで復元する。

PowerShell、スキルフォルダを現在位置にした例:
```powershell
python -m pip install -r requirements.txt
python scripts/doctor.py
python scripts/inventor_drawings.py inspect --input 'D:/CAD/part.idw' --output-dir 'D:/work/check-001'
python scripts/inventor_drawings.py export --input 'D:/CAD/part.idw' --output-dir 'D:/work/export-001' --render-pdf
python scripts/inventor_drawings.py draft --input 'D:/CAD/part.ipt' --template 'assets/templates/portable-a3.idw' --output-dir 'D:/work/draft-001' --probe-model --render-pdf
python scripts/run.py --profile machining-ja draft --input 'D:/CAD/part.ipt' --output-dir 'D:/work/draft-002' --probe-model --render-pdf
```
入力は実在パスに置換。出力は入力ツリー外の未存在フォルダ。inspect/exportはIDW/Inventor DWGのみで、一般AutoCAD DWGは対象外。draftはIPT/IAM。
inspectは値/関連/参照の一部を記録。exportは現行参照を解決してPDFを再出力し、過去PDFの再現を保証しない。draftは未完成ビューのみで、独立ベースビューである。完成時には必要な投影へ作り直し、ParentViewと第三角配置/記号を一致させる。寸法・中心十字・接続中心線・対称軸・仕様・工程図・BOMは自動配置しない。
assets/templates/portable-a3.idwはモデル参照のない汎用A3空図面。特定組織の既存表題欄ではない。作成元Inventor2024で、古い版で開けない場合は利用者自身の空IDWテンプレートを指定するかscripts/make_template.pyで再作成する。Autodeskの既定テンプレート一式は同梱しない。
scripts/portable_helpers.pyはネイティブ投影・中心十字・接続・対称軸等の部品関数。対象幾何と基準/数値は呼び出し側が根拠を確認する。標準CLIで完成図を全自動生成できるとは案内しない。

run.pyはプロファイルの角部注記と同梱テンプレートをCLIへ渡す。粗さ・加工方法・下穴詳細の扱い等はエージェントが設定を読んで完成図へ適用する（CLIが全設定を自動反映するわけではない）。make_template.pyで再生成したIDWには利用者PCの保存履歴が残り得るため、外部公開前にメタデータも確認する。
