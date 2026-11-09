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
inspectは値/関連/参照の一部を記録。exportは現行参照を解決してPDFを再出力し、過去PDFの再現を保証しない。draftは未完成ビューのみ。FRONTを親とするTOP/BOTTOM/LEFT/RIGHTは第三角のネイティブ投影、BACKとISOは独立ビュー。ISOは隠れ線なし。複数ビューにはFRONTを含める。規格尺度を指定上限（--scale、既定1）から収まる最大値へ選び、表題欄は1:2等の比で表示する。寸法・中心十字・接続中心線・対称軸・仕様・工程図・BOMは自動配置しない。
assets/templates/portable-a3.idwはモデル参照のない汎用A3空図面。特定組織の既存表題欄ではない。作成元Inventor2024で、古い版で開けない場合は利用者自身の空IDWテンプレートを指定するかscripts/make_template.pyで再作成する。Autodeskの既定テンプレート一式は同梱しない。
scripts/portable_helpers.pyはネイティブ投影・中心十字・接続・対称軸等の部品関数。対象幾何と基準/数値は呼び出し側が根拠を確認する。標準CLIで完成図を全自動生成できるとは案内しない。

run.pyはプロファイルの角部注記と同梱テンプレートをCLIへ渡す。粗さ・加工方法・下穴詳細の扱い等はエージェントが設定を読んで完成図へ適用する（CLIが全設定を自動反映するわけではない）。make_template.pyで再生成したIDWには利用者PCの保存履歴が残り得るため、外部公開前にメタデータも確認する。

## 穴・注記の補助API（v1.1）
- `probe_model` の `holes` はネイティブ穴フィーチャ。`geometric_hole_candidates` は円筒内面と両端円から得る形状候補で、押し出しカットも含む。両一覧を足して個数を数えない。`through_status=two_planar_openings` は両端が平面の内ループに開く形状の証拠で、工具経路・加工承認ではない。止まり・段付き・交差・部分円筒は追加確認し、`axial_span_mm` を下穴/ねじ深さに転用しない。
- `diameter_dimension(sheet, point, actual_circle_curve, expected_mm, suffix=' 貫通')` は関連付き径寸法。貫通は呼び出し側が確認して渡す。DimensionValueは径記号込みなのでφを二重追加しない。HoleThreadNoteが使えない押出穴にも使える。
- `projected_hole_axis(sheet, wall1, wall2)` は同じ穴の投影壁2本から軸を作る。内部でGeometryIntentを渡す。別穴の壁や外形2本を誤って選ばない。
- 穴心寸法の補助線が十字を覆う場合は、`linear_dimension(..., center_clearance_cm=...)` でその寸法だけのスタイルをコピーし起点間隔を取れる。値は投影半径＋十字の延長＋隙間（シートcm）から決める。両補助線へ適用されるので短区間や基準端も確認する。中心が特定できないほど消す方法や白抜きでの隠蔽は使わない。
- `add_draft_title_block(..., status='確認用（製作承認ではない）', drawing_number='P-001')` で状態と図番を明示できる。下書きCLIは未完成のまま。確認用へ進めたときは左上の注記も合わせて更新し、自動で承認済みにしない。
- 新規図面のローカル文字スタイルと表題欄はＭＳ ゴシック。`configure_drawing_font(doc, font=...)` で利用可能な和文フォントを選べる。共有ライブラリや元テンプレートへ保存しない。既存テンプレートのバイナリは変更せず生成時に適用する。
- `inspect` は改行を保持した注記と生の文字列/書式を併記。AIGDTの径グリフを読みやすいφへ変換するが、元文字と書式も残す。記号や干渉の最終判定はPDFで行う。
