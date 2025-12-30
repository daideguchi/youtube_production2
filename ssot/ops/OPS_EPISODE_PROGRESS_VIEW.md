# OPS_EPISODE_PROGRESS_VIEW — 進捗管理の統一ビュー（派生 / Read-only）

目的:
- 進捗が Planning CSV / status.json / audio final / video runs / CapCut draft 等に散在している現状で、
  「CH12-013 の CapCut ドラフトどこまで？」のような質問に **1コマンド/1画面** で答えられるようにする。

結論（重要）:
- **新しい SoT（正本）は作らない。**
- 各フェーズの SoT は従来通り維持し、**episode 単位の progress は “派生ビュー” として集計**する。
- 派生ビューは基本 **read-only**（人間の意思決定を補助）。正本の更新は既存の導線で行う。

関連（正本）:
- 確定フロー/SoT: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- publish lock（投稿済みロック）: `packages/factory_common/publish_lock.py`
- episode link/run 選択: `scripts/episode_ssot.py`

---

## 1. 入力 SoT（参照元）

episode_progress は以下を参照して集計する（書き換えない）:

- Planning SoT: `workspaces/planning/channels/CHxx.csv`
  - `進捗`（文字列。表記揺れあり）
  - `音声生成/音声品質/納品` 等（運用で手動更新されることがある）
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json`
  - `stages.*.status`（pending/processing/completed/failed）
  - `metadata.video_run_id`（採用 run）
  - `metadata.published_lock`（投稿済みロック）
- Audio SoT（下流参照の正本）: `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`, `{CH}-{NNN}.srt` の存在を基準に `audio_ready` を判定
- Video SoT（run 単位）: `workspaces/video/runs/{run_id}/`
  - `timeline_manifest.json`, `capcut_draft`（symlink/dir）, `capcut_draft_info.json` 等

---

## 2. 派生フィールド（集計結果）

### 2.1 最低限（UI/一覧で必要）
- `published_locked`: 投稿済みロック（Planning CSV / status.json のどちらかで true）
- `script_status`: status.json を基準にした状態（missing / pending / processing / completed / failed）
- `audio_ready`: audio final の wav+srt が揃っているか
- `video_run_id`: status.json の `metadata.video_run_id`（未設定なら null）
- `capcut_draft_status`:
  - `missing`: run_dir に `capcut_draft` が存在しない
  - `broken`: `capcut_draft` は symlink だがターゲットが存在しない
  - `ok`: `capcut_draft` が dir または有効 symlink

### 2.1.1 集計サマリ（一覧の上に出せる）
- `episodes_total`: エピソード総数（view に含まれる件数）
- `episodes_with_issues`: issues が1つ以上あるエピソード数
- `issues_summary`: issues の出現回数サマリ
- `planning_duplicate_videos`: Planning CSV 側で動画番号が重複しているもの（存在する場合）

### 2.2 付帯（調査・復旧のための情報）
- `run_candidates`: episode に紐づく run_dir 候補（複数あると迷子になるため、一覧で見える化）
- `issues`: “迷子ポイント” を短いコード/文で列挙
  - 例: `planning_stale_vs_status`, `video_run_unselected`, `video_run_missing`, `capcut_draft_broken`
  - 方針: **人間が修復/判断すべき異常だけ**を issues に入れる（CSV のミラー遅れ等は issues にしない）

---

## 3. 入口（使い方）

### 3.1 CLI（read-only）
- `python3 scripts/ops/episode_progress.py --channel CH12`
- `python3 scripts/ops/episode_progress.py --channel CH12 --videos 012,013 --format json`

### 3.2 Backend API（UI 用）
- `GET /api/channels/{ch}/episode-progress`
  - query:
    - `videos=012,013`（任意）

### 3.3 UI
- `/planning` に read-only 列を追加して表示（例: `動画run`, `CapCutドラフト`）
- “正本の更新” は既存導線（published lock / status.json / run 選択ツール等）に限定する
