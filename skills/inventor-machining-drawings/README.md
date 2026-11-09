# 配管の図面作成スキル

Claude Code・Codexで、Inventorのモデルをもとに加工図面を作成・検図するためのスキルです。チームで試用し、気づいた点を共有しながら改善していきます。

共通の製図ルール、AI別の実行手順、補助スクリプト、汎用A3テンプレートを収録しています。寸法の並べ方、穴の対応記号、中心十字・接続中心線、対称軸による寸法省略、加工者が読みやすい注記配置を扱います。

## 必要な環境

- Windowsと、起動済みのAutodesk Inventor
- Windows版Python 3.11以上
- Claude Code（またはCodex）
- Git（以下のclone手順を使う場合）

Inventor 2024.3でローカル動作を確認しています。別バージョンでは試験が必要です。同梱IDWを開けない場合は、自分のInventorで空テンプレートを用意してください。[試用報告 #2](https://github.com/RyomaMinami/haikan/issues/2)では、受領者のClaude Codeで導入・起動・単純板の寸法付き図面・IAM下書きまで動作したと報告されています。v1.1の修正は作成側のInventor 2024.3で再確認しており、受領者PCでの新版再試験は別途必要です。

## 1. Claude Codeへ導入する

以下はWindowsのPowerShellで実行します。

```powershell
git clone https://github.com/RyomaMinami/haikan.git
cd haikan/skills/inventor-machining-drawings
python scripts/install.py --target claude
```

スキル一式が `$HOME/.claude/skills/inventor-machining-drawings` にコピーされます。既に同名の導入先がある場合は、上書きせず停止します。更新の場合は後述の手順を使ってください。

次に、**導入先のフォルダへ移動して**依存ライブラリと環境を確認します。Inventorを起動してから診断してください。

```powershell
cd "$HOME/.claude/skills/inventor-machining-drawings"
python -m pip install -r requirements.txt
python scripts/doctor.py
```

ZIPで受け取る場合も、展開後に `SKILL.md` のあるフォルダで `install.py` を実行し、その後は同じ手順です。GitHubの「Code → Download ZIP」はhaikan全体のZIPなので、展開先の `skills/inventor-machining-drawings` を開いてください。

プロジェクト限定の導入は、インストール時に `--project 'D:/my-project'` を追加します。導入先はそのプロジェクトの `.claude/skills/inventor-machining-drawings` になるため、依存ライブラリと診断もそのフォルダで実行してください。

## 2. Claude Codeで試す

Claude Codeで対象モデルのある作業フォルダを開き、次のように依頼します。パスは自分のモデルに置き換えてください。

```text
/inventor-machining-drawings D:/CAD/sample.ipt の加工図面を作って。
まず寸法計画を立て、読みやすい1枚にまとめられるか検討して。
対称軸で省略できる寸法を整理し、加工の基準と送り量は読めるように残して。
完成後はPDF全頁を見て、穴の中心十字・接続中心線・注記の配置も検図して。
不明な材質・数量・公差は推測せず、確認用と明示して。
```

呼び出し方法と配置場所は[Claude Code公式ドキュメント](https://code.claude.com/docs/en/skills)も参照してください。スキルが表示されない場合は導入先を確認し、Claude Codeの新しいセッションで試してください。

最初は公開可能な単純モデルで試してください。完成図の作成には、モデル固有の寸法・加工面・仕様をAIが確認し、作図と検図を進める必要があります。**同梱CLIが自動で作るのは未完成のビュー下書きまで**です。

## 3. 試した結果を共有する

[試用報告フォーム](https://github.com/RyomaMinami/haikan/issues/new?template=drawing-skill-feedback.md)から、次の内容を共有してください。

- 利用したスキルの版またはコミット、Windows・Python・Inventor・AIの環境
- 入力モデルの特徴と、実際に行った手順
- 期待した結果と実際の結果、エラーがあれば内容
- 加工する人が基準・送り量・穴仕様・深さを読み取りにくかった箇所
- 寸法の重複、対称軸、中心線、注記配置などの改善案
- 検図した範囲（全頁画像を表示したか、文字だけを確認したか）

うまくいった例も共有してください。画像やモデルを添付する場合は、公開してよい合成例・切り出し画像を使い、機密図面や個人パス入りのログをそのまま載せないでください。コードやルールの修正案はPull Requestでも受け付けます。

詳しくは[共同改善手順](CONTRIBUTING.md)を参照してください。

## 4. 更新版を取り込む

1. cloneしたhaikanフォルダで `git pull --ff-only` を実行します。自分の変更がある場合は先に別ブランチなどへ保存してください。
2. 導入済みスキルと独自設定を、`.claude/skills` の外へ退避します。
3. cloneした側の `skills/inventor-machining-drawings` に移動し、`python scripts/install.py --target claude` を再実行します。
4. 導入先へ移動し、依存ライブラリ・doctor・試験モデルで再確認します。

`git pull` だけでは、コピー済みの導入先は更新されません。元のスキルを残したままインストーラーを再実行しても上書きはしません。

## 設定と同梱資料

- [中立設定](profiles/default.json)：粗さや一般角部の値は未指定です。
- [従来の加工依頼向け設定](profiles/machining-ja.json)：Ra18・糸面取りなどを明示選択する場合に使います。利用する場合はClaudeへの依頼にもプロファイル名を指定してください。
- [Windows・Inventor実行手順](runtime/windows-inventor.md)：CLIの使い方と制限。
- [Claude Code実行手順](runtime/claude-code.md)／[Codex実行手順](runtime/codex.md)
- [検証済み範囲](VALIDATION.md)、[変更履歴](CHANGELOG.md)、[ファイルハッシュ](manifest.json)

汎用A3テンプレートはモデル参照のない空図面です。仮表題欄は完成時に実尺度・図番・必要な仕様へ更新してください。元製品モデルや研究室名入りの実案件図面は同梱していません。

v1.1では、規格尺度、ISO追加時の第三角親子配置、和文フォント、表題欄の状態/図番、押出穴の形状調査、関連径寸法と側面穴軸の補助関数、中心十字を覆わない補助線の起点設定、改行を残す検査出力を改善しました。使い方と制約は[補助API](runtime/windows-inventor.md)を参照してください。

Codexへの導入は `python scripts/install.py --target codex` を使います。既定の導入先は `$HOME/.codex/skills/inventor-machining-drawings` です。
