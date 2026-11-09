---
name: inventor-machining-drawings
description: InventorのIPT/IAMから加工依頼図面を作成・改訂し、IDW/DWGの検査とPDF出力を行う。組図・部品図・溶接工程図の計画、穴寸法・対称軸・注記・加工者の読みやすさの検図に使う。自動CLIはビュー下書きまで。
---
# Inventor加工図面（配布版）
共通の製図判断とAI固有の実行手段を分離する。利用者の確定仕様を優先し、他案件の数値や公差を流用しない。

1. [共通製図ルール](rules/drafting.md)を読む。アセンブリなら[組図・工程図](rules/assemblies.md)も読む。
2. [共通実行手順](runtime/windows-inventor.md)で環境・入力・原本保護を確認する。実行AIに応じて[Claude Code](runtime/claude-code.md)または[Codex](runtime/codex.md)を読む。
3. [利用者設定](profiles/default.json)を確認し、必要なら[従来の加工依頼向け設定](profiles/machining-ja.json)を明示的に選ぶ。粗さ等の固有指定を他者に無条件で適用しない。
4. 出図前に寸法計画と全穴列一覧を[記録用テンプレート](templates/dimension-plan.json)へ整理する。まず読みやすい1枚を検討。基準→初穴→送り→仕様・深さをたどれる配置を選ぶ。
5. scriptsのCLIはinspect/export/draftのみ。完成図に必要な寸法・中心線・対称・工程判断はエージェントがモデルから確認して作成する。scripts/portable_helpers.pyはモデル曲線を指定した関連寸法・中心線・第三角投影等を補助するが、対象曲線や製作仕様は自動選定しない。
   下書きは規格尺度と第三角親子投影を使う。押出穴は穴フィーチャ一覧だけで判断せず形状候補を確認し、寸法追加後に十字の全腕と側面穴軸を再検図する。状態/図番・和文フォント・関連径寸法などの[補助APIと制約](runtime/windows-inventor.md)を読む。
6. [検図](rules/review.md)に従い保存後の関連・値・参照・原本不変を確認し、PDF全頁を実表示。別担当が利用できれば同じ改訂を独立検図。未実施を独立検図済みとしない。
7. 編集可能IDW・参照モデル・PDF・全頁画像・検図記録をまとめる。仕様未確定は確認用と明示する。外部公開やGitHubへのアップロードは別途の明示依頼時のみ。
