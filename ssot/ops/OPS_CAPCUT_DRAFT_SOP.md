# OPS_CAPCUT_DRAFT_SOP — CapCutドラフトの内部仕様・安全運用・復旧手順（SSOT / 全チャンネル共通）
#
# 目的:
# - 「CapCutドラフトを扱うプロダクト」として、ドラフトの内部ファイル/索引の扱い方を SSOT として固定する。
# - “雑な対応” によるドラフト全損（一覧消失/参照不整合/二重ID）を防ぐ。
#
# 適用範囲:
# - CapCutドラフト生成/編集/差し替えに関わるすべての運用・ツール（`auto_capcut_run`, `capcut_bulk_insert`, `safe_image_swap` 等）。
#
# 結論（最重要）:
# - CapCutのドラフト一覧は **`root_meta_info.json`**（索引）に強く依存する。
# - `root_meta_info.json` は CapCut 自身も更新するため、**読み取り中に壊れたJSON（途中書き）を踏みやすい**。
# - “parse失敗→最小構造で上書き” は **索引の全損（一覧から大量消失）**になるので絶対にやらない。
#
# 最終更新日: 2026-01-26

---

## 1) 主要パス（macOS）

- CapCutドラフトroot（標準）: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`
- CapCut設定（ドラフトrootの実効値）: `~/Movies/CapCut/User Data/Config/globalSetting`
  - `currentCustomDraftPath=...` が上記rootを指していること（別rootに切り替わると「あるのに見えない」が起きる）
- Factory側のフォールバック（権限NG時の生成先）: `workspaces/video/_capcut_drafts/`
  - SSOT: `ssot/reference/【消さないで！人間用】確定ロジック.md` の CapCut節

---

## 2) CapCutドラフトの“内部構造”と役割

### 2.1 ドラフトroot直下（一覧/索引）

- `root_meta_info.json`
  - CapCutのドラフト一覧（少なくとも “最近表示/検索/再開”）のための索引。
  - **SoTではない**（壊れる/再生成される）。しかし壊れると UI からドラフトが消える。
  - 重要: CapCut自身が書き換える。並列プロセスが触ると壊れやすい。

### 2.2 各ドラフトフォルダ（例: `★CH01-252-.../`）

必須ファイル（最低限これが揃わないと開けない/表示されない可能性が高い）:
- `draft_info.json`（プロジェクト本体: ID/トラック/設定）
- `draft_content.json`（素材/タイムライン実体）
- `draft_meta_info.json`（CapCutが一覧作成に使う“フォルダのメタ”）
- `draft_cover.jpg`（一覧サムネ。無くても動くが “一覧からの見え方” に影響）

---

## 3) 絶対ルール（事故防止 / 全損防止）

### 3.1 “索引ファイル” を壊さない

- `root_meta_info.json` を編集する前に必ず:
  1) CapCutを完全終了（⌘Q）
  2) バックアップを取る（例: `root_meta_info.json.bak_<UTC>`）
- `root_meta_info.json` の読み取りで JSON parse に失敗した場合は:
  - **それは「壊れている」のではなく「途中書き」を踏んだ可能性が高い**  
  - → **上書き禁止**（最小構造で作り直すのも禁止）
  - → リトライ（短い待ち） or “今回は更新をスキップ” が正解
- 書き込みは必ず:
  - **原子的置換**（tempに書いて `os.replace`）で行う
  - 出力は **1行JSON**（CapCutネイティブ形式に揃える）を推奨

### 3.2 “ドラフトメタ” を壊さない

- `draft_meta_info.json` は **CapCutが出力する形式（1行JSON）を維持**する
  - 実運用上、改行/整形された `draft_meta_info.json` は “一覧に載らない/更新されない” リスクが上がるため禁止
- `draft_meta_info.json` の整合性（最低限）:
  - `draft_fold_path` は **そのフォルダ自身の絶対パス**と一致していること
  - `draft_name` は **フォルダ名（または CapCutが想定する表示名）**と矛盾しないこと
  - `draft_id` は `draft_info.json` の `draft_id` と一致していること
  - `(1)` の付いた別名を指していないこと（フォルダ実体が無いのに `(1)` を指すと確実に迷子になる）

### 3.3 “draft_id をいじらない”

- `draft_id` は実質 “主キー”。
- **不用意に `draft_id` を変更すると、別ドラフト扱い / 参照切れ / `(1)` 増殖** の原因になる。
- 変更が必要なケースは “復旧手順” に従い、必ずバックアップを取って慎重に行う（通常は変更しない）。

### 3.4 自動削除（purge）を信用しない

- 「未投稿（Hot）」が消えるのが最悪の事故なので、**自動での `shutil.rmtree` / purge は既定OFF**が前提。
- purge をする場合は必ず **二段階ゲート**を通す:
  1) purge_queue に `allow_purge=true`（明示的な許可）
  2) archive 側に `_ARCHIVED_FROM_MAC.(json|txt)`（実体が保管されている証拠）
- purge は **CapCutが起動していない時のみ**（ファイルロック/途中書き回避）。

---

## 4) 典型症状 → 原因 → 安全な対処

### 4.1 「フォルダはあるのにCapCutの一覧に出ない」

原因候補（頻出順）:
1) `globalSetting` の `currentCustomDraftPath` が別rootを指している
2) `root_meta_info.json` が “途中書き” / “破壊的上書き” で痩せている（一覧索引が消えた）
3) 対象ドラフトの `draft_meta_info.json` が不整合（`(1)` 指し、改行整形、`draft_fold_path` 不一致）

安全な対処（手順は 5章）:
- CapCutを終了 → root を確認 → `draft_meta_info.json` を整合 → `root_meta_info.json` を復元/再構築 → CapCut再起動

### 4.2 「run_dir の capcut_draft が壊れている（リンク先が無い/別名）」

原因:
- CapCutが同名衝突でフォルダ名を `(1)` に変更 → symlink が古いまま

対処:
- `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.audit_fix_drafts --channel CHxx --min-id ... --max-id ...`（dry-run→必要ならapply）
  - ※このツールは run_dir 側のリンク整備が主。CapCut索引（root_meta）の全量修復ではない。

---

## 5) 復旧手順（“ドラフトが見えない/開けない”）

> まず「壊さない」。復旧より先にバックアップを固定する。

### 5.0 事前バックアップ（必須）

- `root_meta_info.json` をコピー:
  - `cp -a "<draft_root>/root_meta_info.json" "<draft_root>/root_meta_info.json.bak_<UTC>"`
- 対象ドラフトフォルダも最低限コピー（または `draft_info.json` / `draft_meta_info.json` を退避）:
  - `cp -a "<draft_dir>/draft_info.json" "<draft_dir>/draft_info.json.bak_<UTC>"`
  - `cp -a "<draft_dir>/draft_meta_info.json" "<draft_dir>/draft_meta_info.json.bak_<UTC>"`

### 5.1 root が正しいか確認

- `~/Movies/CapCut/User Data/Config/globalSetting` の `currentCustomDraftPath` を確認
  - 想定外の場所に向いていたら、まずCapCut側設定で戻す（ファイル手編集は最終手段）

### 5.2 対象ドラフトの “最低限整合” を確認

- `<draft_dir>/draft_info.json` の `draft_id` / `draft_name` を確認
- `<draft_dir>/draft_meta_info.json` が:
  - JSONとして読める
  - 1行JSON（推奨）
  - `draft_fold_path` が `<draft_dir>` そのものを指す
  - `draft_id` が `draft_info.json` と一致

### 5.3 `root_meta_info.json` を復元/再構築

原則:
- CapCut終了中に実施
- “parse失敗→最小構造で上書き” は禁止

推奨:
- `root_meta_info.json.bak_*` があるなら、それをベースに:
  - 重複は `draft_fold_path` で除去
  - 対象ドラフトのエントリを追加/更新（`draft_id` は `draft_info.json` の値を使う）
  - UIで見つけやすいように **先頭へ移動**（`tm_draft_modified` を現在時刻へ更新）

### 5.4 CapCutを再起動して確認

- CapCutを起動 → 一覧の先頭/検索で対象ドラフトが見えるか確認
- それでも一覧から開けない場合:
  - `open -a CapCut "<draft_dir>/draft_info.json"` を試す（環境/バージョン依存）

---

## 6) ツール実装側の規約（プロダクトとして守る）

**CapCutドラフトroot配下に書き込むツールは、次を必ず満たすこと。**

- `root_meta_info.json` 更新は:
  - 途中書きに対するリトライを持つ
  - parse失敗時は **スキップ**（clobber禁止）
  - 原子的置換（temp → `os.replace`）
  - 出力は1行JSON（推奨）
- 並列実行が想定されるため、可能ならロック（best-effort）を取る

実装メモ:
- `packages/video_pipeline/tools/capcut_bulk_insert.py` は上記ポリシーに合わせて更新済み（2026-01-26）。

---

## 関連SSOT / 参照

- 運用（編集/共有/Workset）: `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- 置き場戦略（Hot/Warm/Cold）: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`
- CH02固有SOP: `ssot/ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`
- 実装側メモ（補助）: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`
