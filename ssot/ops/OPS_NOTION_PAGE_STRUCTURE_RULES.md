# OPS_NOTION_PAGE_STRUCTURE_RULES — Notion迷子を止める「入口/命名/タグ/合言葉」運用ルール（SSOT）

最終更新（UTC）: 2026-01-28T15:04:32Z

目的:
- Notionが「迷路」にならないよう、**入口（START HERE）→索引DB→詳細** の導線を固定する。
- 「何が正本か」を曖昧にしない（**正本=repo SSOT / workspaces**、Notionは**ナビ/記録/検索**）。
- 自動化（夜間バッチ/LLM）を入れても品質を落とさないため、**提案→diff→人間採用**を前提にする。

非目的:
- Notionだけで全ての真実（SoT）を持つこと（repo SSOT / workspaces と二重化させない）。
- 既存ページをAPIだけで完全に「移動」して再配置すること（Notion API制約あり）。  
  → まずは **入口と索引** を作って迷子を止め、必要な移動はUIで段階的に行う。

---

## 0) 用語

- **Hub**: 旧メモ/ログの入口ページ（Notion内の固定親）。
- **入口（TOP）**: `🏠 社長室ポータル（入口）`（新規導線の唯一の入口）。
- **START HERE（迷子停止）**: `00_NOTION_MAP（迷子停止）`（最初に開くページ）。
- **Ops Log DB**: 作業ログを時系列+タグで引ける索引DB。
- **合言葉インデックスDB**: 見出し（セクション）へ到達するための索引DB（合言葉→ページ/ブロックURL）。

---

## 1) 入口（固定）

### 1.1 TOP入口（最優先）

Notion API制約上「workspace直下にページ作成」ができないため、workspace直下相当の親として `🏠 社長室ポータル（入口）` を使う（canonical）。

- `🏠 社長室ポータル（入口）` page_id: `2f403c60-8a71-81ff-a5cf-c54ddd6b1310`
- `00_NOTION_MAP（迷子停止）` page_id: `2f603c60-8a71-81dc-a511-e233d308197b`
- `01_Notion運用ルール（命名/タグ/合言葉/UTC）` page_id: `2f603c60-8a71-81c1-aef0-fb9decea59af`
- `02_制作フロー（企画→アノテ前）` page_id: `2f603c60-8a71-8150-aa43-f19d9a8a061a`
- `03_RUNBOOKS（運用/復旧/ネットワーク）` page_id: `2f603c60-8a71-81a2-98dd-cd6a894e6efc`
- `99_ARCHIVE（ゴミ箱/退避/未整理）` page_id: `2f603c60-8a71-81ac-a2e5-dbe84d3c000b`

### 1.2 Hub直下（旧メモ/ログの入口）

- Hub: `旧メモ/ログ ハブ（入口）` page_id: `2f103c60-8a71-810f-ac62-d05e95210002`

Hub直下に以下を置く（タイトル接頭辞で並ぶようにする）:

- `OPS_LOG（作業ログ）`（DB） database_id: `2f603c60-8a71-81dc-aa33-f95e80b08564`
- `合言葉インデックス`（DB） database_id: `2f603c60-8a71-8142-a055-e27fe7d9c26e`
- `01_Notion運用ルール（命名/タグ/合言葉）`（legacy / 旧） page_id: `2f603c60-8a71-81a9-b37a-c4348bb8797e`
- `HUB_START_HERE（Hub内/旧）`（※Hub内に残っている過去入口。新規導線はTOP入口へ集約）

運用ルール:
- 新しい重要情報は「コメントで埋めない」。**Ops Log DB** に1行で記録し、必要ならページ本文に詳細を置く。
- `🏠 社長室ポータル（入口）` → `00_NOTION_MAP（迷子停止）` から **2クリック以内** に「主要DB/主要SSOT/最新作業ログ」へ到達できる状態を保つ。
- 迷子防止のため、`🏠 社長室ポータル（入口）` と `00_NOTION_MAP（迷子停止）` を Notion の「お気に入り（Favorites）」へ固定する（最優先）。
- バックアップ導線として Hub（`旧メモ/ログ ハブ（入口）`）もお気に入りに入れる（ただし新規導線は必ずTOP入口へ集約）。
- 入口（TOP）には「外部UI/iPhone/監視リンク」を増やさない（迷路化する）。外部リンクは `03_RUNBOOKS` に集約する。

---

## 2) 命名規則（タイトル）

### 2.1 ページ（固定接頭辞）

- 入口/ルール系: `00_...`, `01_...`（数字接頭辞で固定）
- 作業ログ（ページ化する場合）: `YYYY-MM-DD — <短い要約> — <タグ>`（日付を先頭）
- 運用/設計: `OPS — <topic> — YYYY-MM-DD`（OPSを先頭にして検索しやすく）

### 2.2 DB行（作業ログ）

タイトルは短く:
- 例: `JP Polish I/O確定`, `Notion迷子停止（入口整備）`, `合言葉インデックスDB作成`

---

## 3) タイムスタンプ規則

詳細（正本）: `ssot/ops/OPS_NOTION_TIMESTAMP_RULES.md`

原則:
- DBに `日時(UTC)` を必須で入れる（=ソート/検索の主キー）。
- 文章中には `実行日時（UTC）: 2026-01-28T12:34:56Z` の形式で残す（機械にも人にも強い）。

---

## 4) タグ規則（最小セット）

タグは「探すためのメタ」。増やしすぎない（まずは下で固定）。

推奨タグ（例）:
- `ops`
- `notion`
- `script`
- `jp-polish`
- `ollama`
- `rag`
- `lenovo`
- `incident`
- `decision`
- `report`

---

## 5) 合言葉（コードワード）運用

目的:
- Notionの長いページでも「該当セクションへ即到達」できるようにする。

ルール:
- 見出し（heading_1/2/3）に `（合言葉: deadbeef）` の形式で付与する（運用側で一貫）。
- 参照は `合言葉インデックスDB` を使う（合言葉→ページURL/ブロックURL）。

注意:
- 合言葉は「人間が覚える」より「機械/検索で一発」が目的。  
  覚えやすさが必要な箇所は、**タグ/タイトル**で補う。

---

## 6) 品質を落とさないための強制ルール（LLM運用）

JP Polish / 整形 / 自動処理は、以下を満たすこと:
- **正本を自動上書きしない**（提案本文 + diff + 変更理由を出す）
- `---`（ポーズ行）は **増減/位置変更禁止**
- 数字/固有名詞/否定（not）周りは特に危険なので、検証を必須にする（validateで検出→人間レビュー）

---

## 7) I/O（記録の置き場）

正本:
- repo SSOT: `ssot/**`
- 台本SoT: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（無ければ `assembled.md`）

Notion（入口/索引）:
- START HERE から Ops Log / 合言葉インデックス / 主要設計ページへ誘導する

夜間バッチ（Lenovo常駐）の出力:
- `workspaces/scripts/{CH}/{NNN}/content/analysis/jp_polish/`（提案本文/diff/validate/log）
- `ytm_workspaces/scripts/_night_jobs/jp_polish/{YYYY-MM-DD}.jsonl`（夜間一覧）
