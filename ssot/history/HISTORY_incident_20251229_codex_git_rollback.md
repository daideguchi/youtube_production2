# INCIDENT — 2025-12-29 Codex Gitロールバック事故（factory_commentary）

作成: 2025-12-29T21:12:30+0900（UTC: 2025-12-29T12:12:30Z）
対象repo: `/Users/dd/10_YouTube_Automation/factory_commentary`

方針:
- 憶測は書かない（ログ/コマンド/ファイル実体に存在する事実のみ）
- 詳細な雑ログ/抽出物は repo 直下に置かない（ノイズ回避）

---

## 1) 事象（要約 / 事実）

- 複数Codexの並列運用中、少なくとも1つのCodexが `git restore` / `git checkout` 等の破壊的Git操作を実行した。
- その結果、作業ツリーの一部が古い状態に戻った（ユーザー申告 + セッションログ抽出で `git restore/checkout` を確認）。
- ユーザーが「戻った（ロールバックされた）」と指摘した対象（ユーザー提示）:
  - `configs/llm_task_overrides.yaml`
  - `apps/ui-backend/backend/main.py`
  - `apps/ui-frontend/src/App.css`
  - `apps/ui-frontend/src/components/VideoDetailPanel.tsx`
  - `configs/sources.yaml`
  - `prompts/README.md`
  - `packages/script_pipeline/channels/CH22-老後の友人関係ラボ/script_prompt.txt`

## 2) 影響（要約 / 事実）

- UI: `/benchmarks`（ベンチマーク/ジャンル別）、台本ページ（`VideoDetailPanel`）、チャンネルポータル（企画一覧/チャンネル選択）
- Backend: 台本ページのBテキスト（TTS用テキスト）の参照元
- Research: yt-dlpベンチマーク（チャンネルアイコン・サムネ分析の集計/言語化）
- ログ: `log_research` 自体の消失/復旧が発生（別途証跡あり）

## 3) 復元（結果 / 事実）

復元の実施ログ（事実のみ）:
- `backups/_incident_archives/factory_commentary/20251229_rollback/log_research/20251229_ui_restore_worklog.md`
- `backups/_incident_archives/factory_commentary/20251229_rollback/log_research/20251229_ui_restore_worklog_orchestrator.md`

復元の主な到達点（抜粋 / 事実）:
- `/benchmarks`
  - yt-dlp側: 日本語ラベル化・チャンネルアイコン表示・「詳細」開閉・集計カードの余白削減
  - ジャンル別（台本）: 一覧/プレビューの読みやすさ改善（折返し/チップ/ヒント文言）
- チャンネルポータル
  - 企画一覧の行クリックで台本ページへ遷移
  - チャンネル選択UIでアイコン表示
- 台本ページ
  - BテキストがAテキストで埋まる不具合を修正（作成されていなければ空）
- yt-dlp分析
  - チャンネルアイコンURLの保存（report/REPORTS.json）
  - サムネ言語化プロンプトの具体化 + 正規化（集計のブレ低減）

## 4) 再発防止（実施済み / 事実）

1) Codex execpolicy（破壊的Git禁止）
- 対象: `/Users/dd/.codex/rules/default.rules`
- 反映内容（要約）:
  - `git` は原則 `prompt`（Codexからは実行不能）
  - `git restore/checkout/reset/clean/revert/switch` は `forbidden`
  - `rm -rf log_research` は `forbidden`
- 証跡: `backups/_incident_archives/factory_commentary/20251229_rollback/log_research/20251229_guardrails_execpolicy.md`

2) ghost snapshot 警告（large untracked dir）対処
- 対象: `/Users/dd/.codex/config.toml`
- 追加: `[ghost_snapshot] ignore_large_untracked_dirs = false`
- 目的: `workspaces/planning/patches` の警告抑止（snapshot/undo対象から除外されないようにする）
- 証跡: `backups/_incident_archives/factory_commentary/20251229_rollback/log_research/20251229_ui_restore_worklog_orchestrator.md`（ghost snapshot 節）

## 5) 証跡（雑ログの退避 / 事実）

`log_research` はノイズ対策のため、以下に退避した（repo直下から移動）:
- 退避先: `backups/_incident_archives/factory_commentary/20251229_rollback/log_research/`

この退避ディレクトリ内に、セッション抽出TSV/JSONL/時系列MD、復元ログ、execpolicy確認ログ等を保管する。

## 6) 未実施TODO（人間作業）

- 今回の復元差分を commit/push（git操作は人間が実施）
- `workspaces/planning/patches` の tracked/untracked 状態を整理（SSOT上は tracked 想定）
