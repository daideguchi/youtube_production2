# OPS_NOTION_TIMESTAMP_RULES — タイムスタンプ徹底（Notion/OPS_LOG/出力物）運用（SSOT）

最終更新（UTC）: 2026-01-28T15:24:05Z

目的:
- Notionが迷路化する最大要因（「いつの情報か分からない」「最新が不明」）を潰す。
- 夜間バッチ/LLM運用でも、**翌朝に一瞬で状況が復元**できるようにする。
- 「正本=repo/workspaces」「Notion=ナビ/ログ/検索」を崩さない。

関連（正本）:
- Notion構成ルール: `ssot/ops/OPS_NOTION_PAGE_STRUCTURE_RULES.md`
- 工程/I-O（企画→アノテ前）: `ssot/ops/OPS_SCRIPT_PRE_ANNOTATION_WORKFLOW.md`

Notion入口（canonical / 迷子停止）:
- 🏠 社長室ポータル（入口）: https://www.notion.so/2f403c608a7181ffa5cfc54ddd6b1310
- 00_NOTION_MAP（迷子停止）: https://www.notion.so/00_NOTION_MAP-2f603c608a7181dca511e233d308197b
- 01_Notion運用ルール（命名/タグ/合言葉/UTC）: https://www.notion.so/01_Notion-UTC-2f603c608a7181c1aef0fb9decea59af
- 02_制作フロー（企画→アノテ前）: https://www.notion.so/02_-2f603c608a718150aa43f19d9a8a061a
- 03_RUNBOOKS（運用/復旧/ネットワーク）: https://www.notion.so/03_RUNBOOKS-2f603c608a7181a298ddcd6a894e6efc
- 99_ARCHIVE（ゴミ箱/退避/未整理）: https://www.notion.so/99_ARCHIVE-2f603c608a7181aca2e5dbe84d3c000b
- OPS_LOG（作業ログDB）: https://www.notion.so/2f603c608a7181dcaa33f95e80b08564
- 合言葉インデックス（DB）: https://www.notion.so/2f603c608a718142a055e27fe7d9c26e

---

## 1) 絶対ルール（UTC固定）

- **すべてUTC**（例: `2026-01-28T12:34:56Z`）
- “今日/昨日”は禁止（必ず絶対時刻）。
- 形式はISO 8601（`Z` 付き）。

---

## 2) Notionページ（非DB）: 本文先頭に必須

Notionの「設計/ルール/レポート」系ページは、本文先頭（最初の段落 or callout）に必ず入れる:

- `最終更新（UTC）: 2026-01-28T12:34:56Z`
- `作成（UTC）: 2026-01-28T12:00:00Z`（既存ページは不明なら省略可）
- `正本（SSOT）: <repo-relative path>`（該当する場合）

理由:
- Notion側でタイトルだけ見ても「最新」が判定できないケースが多い。
- API/自動化でもパースしやすい。

---

## 3) OPS_LOG（作業ログDB）: 必須項目チェック

OPS_LOG は「後から探す/復元する」ための索引。**未入力禁止**（最低限）。

必須:
- `件名`（短く/検索語を含める）
- `日時(UTC)`（date）
- `種別`（select: decision/report/setup/incident/design）
- `タグ`（multi_select: ops/notion/script/jp-polish/ollama/rag/lenovo/incident/decision/report）

状況に応じて必須（空欄にしない）:
- `SSOT`（触った正本のパス、または参照したSSOTパス）
- `出力/場所`（ファイル出力先 / レポートパス）
- `Notionページ`（関連ページURL or page_id）
- `合言葉`（該当セクションがある場合）

---

## 4) Notionタイトル命名（“日付”は必須 / “時刻”は本文へ）

原則:
- タイトルには **日付（YYYY-MM-DD）を必須**。
- 詳細な時刻（HH:MM:SS）は本文先頭の `最終更新（UTC）` へ寄せる（タイトルの乱立を防ぐ）。

例:
- `OPS — Notion運用ルール改訂 — 2026-01-28`
- `2026-01-28 — Notion迷路停止（入口/導線） — ops/notion`

---

## 5) ファイル出力（夜間バッチ/LLM提案）: run_id を必須にする

run_id:
- 形式: `YYYYMMDDTHHMMSSZ`（UTC固定）

必須ファイル（最小 / 1実行=1run_id）:
- `run_meta_{run_id}.json` / `run_meta_latest.json`（run_id / created_at(UTC) / input_path / model / duration / status）
- `proposed_a_text_{run_id}.md` / `proposed_a_text_latest.md`（提案本文）
- `proposed_a_text_{run_id}.diff` / `proposed_a_text_latest.diff`（unified diff）
- `change_summary_{run_id}.md` / `change_summary_latest.md`（変更理由の要点）
- `validate_{run_id}.md` / `validate_latest.md`（禁則検証）
- `log_{run_id}.jsonl` / `log_latest.jsonl`（証跡）

夜間の集約一覧（必須）:
- `ytm_workspaces/scripts/_night_jobs/jp_polish/YYYY-MM-DD.jsonl`
  - 1行=1エピソード（created_at / script_id / input_hash / out_dir / status / model / wall_time_s）

---

## 6) Slack通知（短く / 参照先を必ず書く）

Slackは“今の状況を知る”用。必ず以下を含める:
- 何を決めた/何を変えた
- いつ（UTC）
- どこ（SSOTパス / 出力パス / OPS_LOG）
- 次に誰が何をする（最小手順）

---

## 7) このドキュメントの位置づけ

`OPS_NOTION_PAGE_STRUCTURE_RULES.md` に統合するまでの **タイムスタンプ特化の追補**。
統合作業は、当該SSOTの lock が空いたタイミングで行う。
