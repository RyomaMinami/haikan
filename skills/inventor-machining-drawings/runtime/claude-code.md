# Claude Codeでの実行
SKILL.mdを含むフォルダ全体を、個人用なら~/.claude/skills/inventor-machining-drawings/、プロジェクト用なら.claude/skills/inventor-machining-drawings/へ配置する。インストーラーはscripts/install.py。
起動後 `/inventor-machining-drawings 対象ファイルと依頼内容` で明示起動できる。共通ルールとWindows実行手順を読み、利用者プロファイルを適用する。
実行はWindowsネイティブのPythonを使う。WSL内PythonからInventor COMは動かさない。Claude CodeがWSLで動く場合もWindows側Python/PowerShell経由で起動し、Windows形式の絶対パスへ変換する。安全な引数配列またはスクリプトファイルを使い、対象名をシェルの実行文へ埋め込まない。
Claude Codeのローカルファイル/シェルでscriptsを呼び、画像表示機能で最終PNGを全頁読む。利用できない操作を実施済みとしない。独立検図は利用可能な別担当に共通review手順を渡す。使えなければ自己検図と記録する。Codex固有ツール、MCP、model名、project-memoryスキル、Gemini CLIに依存しない。
テンプレートと補助スクリプトのローカル実行は確認済みだが、この配布作成時にClaude Code本体での実行は未確認。最初に環境診断と自分の試験モデルでsmoke testを行う。
公式仕様確認: https://code.claude.com/docs/en/skills （2026-11-07確認）。
