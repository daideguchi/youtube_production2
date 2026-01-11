# PLAN_OPS_PERFORMANCE_BOTTLENECKS — 処理が遅い/詰まる課題（SSOT）

目的:
- 「時間がかかりすぎる」原因を **観測事実ベース** で集約し、改善タスク（DoD付き）を固定する。
- 日々の進捗や議論は board thread に寄せ、ここは **課題の正本** とする。

更新ルール:
- 憶測は書かない（仮説は「要調査」と明記）。
- 具体のログ/再現コマンド/対象ファイルを添える。

---

## 1) 観測されたボトルネック（確定）

### 1.1 サムネ再合成（CH26）
- 実行: `python3 scripts/thumbnails/ch26_make_two_variants.py --overwrite`
- 結果: 30本（001..030）を再生成（`00_thumb_1.png`/`00_thumb_2.png`）するのに **約101秒** かかった（実行ログ参照）。
- 参考ログ: `workspaces/logs/ops/thumbnails/ch26_make_two_variants_overwrite__20251229T115839Z.log`

### 1.2 QC（コンタクトシート）生成・公開
- thumb_1 のQC（`library/qc/contactsheet.png`）は `python3 scripts/thumbnails/build.py qc` で生成・publishできるが、
  thumb_2 は `--out` で別名を指定して `library/qc/` に置く必要がある（運用上の手数が増える）。
- 参考ログ:
  - `workspaces/logs/ops/thumbnails/ch26_qc_thumb1_publish__20251229T120131Z.log`
  - `workspaces/logs/ops/thumbnails/ch26_qc_thumb2_library__20251229T120131Z.log`

### 1.3 CLIの引数UX（作業ミス→手戻り）
- `scripts/thumbnails/ch26_make_two_variants.py` の `--videos` は **カンマ区切り文字列**（例: `--videos 010,006,005`）のみ受け付ける。
  - `--videos 010 006 005` は失敗する（観測済み: `unrecognized arguments`）。

### 1.4 UIでの「上書き」反映（キャッシュ/更新トークン）
- 同じ `image_path` のファイルを上書きした場合、UI側の cache-bust トークン（`updated_at`）が更新されないと見た目が更新されず、確認に時間がかかる。
- 対応済み（2025-12-29）:
  - `apps/ui-backend/backend/main.py` の disk variant merge で、同一 `image_path` の場合は disk の `updated_at`（mtime）を反映するように修正。

### 1.5 UI「文字を編集」モーダル（視認性/崩れ）
- 文字色と背景が同化、textarea がカード/パネルからはみ出す等で、作業が止まる。
- 対応済み（2025-12-29）:
  - `apps/ui-frontend/src/App.css` の `.thumbnail-planning-dialog` / `.thumbnail-planning-form` を補強（contrast・box-sizing・responsive scope）。

---

## 2) 改善タスク（DoD付き）

表記:
- 優先度: `P0` (停止/事故) / `P1` (主線) / `P2` (改善) / `P3` (後回し)
- 状態: `todo` / `doing` / `blocked` / `done`

### 2.1 Thumbnails / Performance
- [ ] `TODO-THUMB-PERF-001` サムネ再合成の速度改善（P2, todo）
  - scope: `scripts/thumbnails/ch26_make_two_variants.py`, `packages/script_pipeline/thumbnails/**`
  - 現状: CH26 30本で約101秒（ログ: `workspaces/logs/ops/thumbnails/ch26_make_two_variants_overwrite__20251229T115839Z.log`）
  - DoD:
    - `CH26` 30本の `--overwrite` が **60秒未満**（目標: 30秒台）で完走する
    - ベンチ結果（時間・環境・変更点）を `workspaces/logs/ops/thumbnails/` に残す
  - 要調査（候補）:
    - PIL保存の `optimize` 設定
    - 背景強調/文字合成の中間生成物の再利用
    - 動画単位の並列化（CPU/IOバランスを見て上限を決める）

### 2.2 Thumbnails / QC運用
- [ ] `TODO-THUMB-QC-001` 2バリアントQCを「1コマンドで」生成・公開（P2, todo）
  - scope: `scripts/thumbnails/build.py`
  - DoD:
    - `thumb_1`/`thumb_2` の contactsheet を **どちらも** `assets/{CH}/library/qc/` に publish できる（ファイル名も指定可）
    - UIのQCタブから両方確認できる

### 2.3 Thumbnails / CLI UX
- [ ] `TODO-THUMB-UX-001` `--videos` の複数指定を受け付ける（P3, todo）
  - scope: `scripts/thumbnails/ch26_make_two_variants.py`
  - DoD:
    - `--videos 010 006 005` と `--videos 010,006,005` の **両方** が動く

### 2.4 UI / Cache bust（回帰防止）
- [ ] `TODO-UI-CACHE-001` 同一 `image_path` 上書き時の更新反映を回帰テスト化（P2, todo）
  - scope: `apps/ui-backend/backend/main.py`, `tests/**`（既存方針に合わせて追加）
  - DoD:
    - disk mtime が更新された時に、APIレスポンスの `updated_at` が更新されることを自動で検証できる

---

## 3) 付記（運用上の制約）

- `ssot/ops/**` は別ロック（no_touch）対象のため、本件の課題は `ssot/plans/` に集約した。
