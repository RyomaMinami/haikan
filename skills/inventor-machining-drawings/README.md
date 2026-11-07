# 配管の図面作成スキル v1.0.0
Claude CodeとCodex向け。共通製図ルール、AI別実行手順、ローカルCOMスクリプト、汎用A3空IDW、利用者別設定を同梱。元製品モデル・実案件図面・研究室名入り表題欄・個人パスは含めない。

## Claude Codeへの導入
ZIPを展開し、SKILL.mdのあるinventor-machining-drawingsフォルダで以下を実行する。
```powershell
python scripts/install.py --target claude
```
~/.claude/skills/inventor-machining-drawingsへコピー。既存フォルダがあれば停止。プロジェクト限定は`--project 'D:/my-project'`を追加。コピー後Claude Codeで`/inventor-machining-drawings`を指定する。[公式仕様](https://code.claude.com/docs/en/skills)。
Windows PythonとInventorを用意し、導入先で次を実行。
```powershell
python -m pip install -r requirements.txt
python scripts/doctor.py
```
実行例と制限はruntime/windows-inventor.md。Codexへの導入は`--target codex`。

## できることと限界
製図ルール/寸法計画/検図の判断をAIへ渡す。CLIは既存IDW検査・PDF出力・未完成ビュー下書きまで。任意モデルの完成図/BOM/溶接工程をワンクリックで自動生成するものではない。モデル固有の寸法/仕様を確認し、エージェントが作図・全頁画像検図を行う必要がある。
汎用プロファイルは粗さ/角部等未指定。従来依頼者向け設定はprofiles/machining-ja.jsonを明示選択。元環境固有のRa18を全員へ強制しない。
assets/templates/portable-a3.idwはInventor2024で作成したモデル参照なしの空図面。特定組織の枠は移植せず、汎用仮表題欄を使用。完成時は利用者の枠・投影記号に合わせる。

## GitHubで渡す
公開リポジトリ RyomaMinami/haikan の skills/inventor-machining-drawings で共同更新する。相手はclone後にinstall.pyを実行する。試験結果はIssue、変更案はPull Requestで共有する。詳しくは[共同改善手順](CONTRIBUTING.md)を参照。CAD原本や実案件PDFを同じリポジトリへ自動追加しない。.gitignoreは事故防止用で、追跡済みファイルの公開を防ぐものではない。
公開配布時のライセンスは所有者が決める。MIT等を勝手に付与していない。第三者素材・Autodesk製品本体・既定テンプレート一式は同梱しない。

## 検証
VALIDATION.mdとmanifest.json参照。Claude Code本体/相手PCでの実行は未確認。導入先でdoctorと試験モデルのdraftを確認する。元モデル・既存図面・アプリ設定は変更しない。

## チームでの試用と更新
共有入口: https://github.com/RyomaMinami/haikan/tree/main/skills/inventor-machining-drawings

```powershell
git clone https://github.com/RyomaMinami/haikan.git
cd haikan/skills/inventor-machining-drawings
python scripts/install.py --target claude
```
初回導入後、導入先で依存環境を準備しdoctorを実行する。既存の同名スキルがある場合は停止する。更新時も自分の修正を退避してから導入先を差し替える。手順はCONTRIBUTING.mdを参照。
