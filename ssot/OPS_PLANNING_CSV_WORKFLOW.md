# OPS_PLANNING_CSV_WORKFLOW — 企画/進捗CSV（Planning SoT）の運用手順

目的:
- `workspaces/planning/channels/CHxx.csv` を **Planning の正本（SoT）** として安全に更新し、台本/音声/動画工程の迷子と破壊を防ぐ。
  - 互換: `progress/channels/CHxx.csv`（symlink）

関連:
- 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
- I/Oスキーマ: `ssot/OPS_IO_SCHEMAS.md`
- 整合チェック: `ssot/OPS_ALIGNMENT_CHECKPOINTS.md`

---

## 0. 正本の定義

- Planning SoT（正本）: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
- Script SoT（正本）: `workspaces/scripts/{CH}/{NNN}/status.json`（互換: `script_pipeline/data/...`）
- Audio SoT（下流参照の正本）: `workspaces/audio/final/{CH}/{NNN}/`（互換: `audio_tts_v2/artifacts/final/...`）

※ Planning CSV は「企画/在庫/意図/補助メタ」を保持し、Script/Audio の実行状態はそれぞれの SoT に残る。

---

## 1. 行ID（識別子）の確定ルール

下流工程で必ず揃えるキー:
- `CH`（チャンネルコード）: `CH01` のような `CH` + 2 桁
- `NNN`（動画番号）: `001` のような 3 桁ゼロ埋め
- `script_id`（動画ID）: `CHxx-NNN`

CSV 側は列名の揺れがあるため、最低限次のどれかが存在すればよい（コード側で吸収される）:
- 動画ID: `動画ID` または `台本番号` または `ScriptID`
- 動画番号: `動画番号` または `No.` または `VideoNumber`

---

## 2. 更新の基本導線（推奨）

### 2.1 UI 経由（推奨）
- UI の Planning 画面（`GET /api/planning` 系）で編集する。
- 外部で CSV を編集した場合は、UI から `POST /api/planning/refresh` を実行して再読込する。

### 2.2 手動編集（許可・ただし厳守）
1) 対象 CSV を直接編集: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
2) `動画番号` / `動画ID`（または同等列）を壊さない
3) 変更後、UI を開いて一覧が意図通りに表示されることを確認する

---

## 3. 追加（新規行）の確定仕様

UI の `POST /api/planning` で作成される行は、概ね以下を自動で埋める:
- `チャンネル` = `CHxx`
- `動画番号` = `NNN`（3桁）
- `動画ID` と `台本番号` = `CHxx-NNN`（存在する列のみ）
- `タイトル` = payload.title
- `進捗` = 省略時 `topic_research: pending`
- `品質チェック結果` = 未設定時 `未完了`
- `更新日時` = 現在時刻

チャンネルによって「必須列」が異なる場合がある（UI は `planning_requirements` に基づいてガードする）。

---

## 4. 変更を下流へ反映するルール（超重要）

Planning CSV を更新しても、既に生成済みの台本/音声/動画は自動で差し替わらない。  
下流へ反映したい場合は、原則「reset → 再生成」で揃える。

例:
- タイトル/企画意図/タグを変更した → Script の該当ステージを reset して再生成
- 台本が出来た後に企画を大きく変えた → その動画は **最初からやり直す**（混在が最悪の事故源）

---

## 5. カラム設計（現状の“許容”と最小要件）

### 5.1 最低限（推奨）
- `チャンネル`
- `動画番号`（または `No.`）
- `動画ID`（または `台本番号`）
- `タイトル`
- `更新日時`

### 5.2 あると運用が安定する（強く推奨）
- `進捗`（UI 表示の補助。表記揺れはあるが、空より良い）
- `台本` または `台本パス`（台本の所在を明示。最終的には paths SSOT で正規化する）
- `企画意図`, `ターゲット層`, `具体的な内容（話の構成案）`

ヘッダ例は `ssot/OPS_IO_SCHEMAS.md` を参照。

### 5.3 入力契約（L1/L2/L3）— 迷走と混線を止める

Planning CSV は列が増えるほど「別行の古い内容が混入」しやすく、台本のズレ/担当の混乱の原因になる。  
そのため Aテキスト生成に使う列は **契約として固定**し、矛盾した列は機械的に無視/再生成する。

**L1（正本 / 生成の根拠）**
- `タイトル`（絶対）
- `企画意図`（チャンネルで必須の場合は絶対）
- `具体的な内容（話の構成案）`（チャンネルで必須の場合は絶対）
- `ターゲット層`（チャンネルで必須の場合は絶対）

**L2（従属 / あっても良いが矛盾したら捨てる）**
- `内容（企画要約）`（= content_summary）
  - タイトル先頭 `【...】` と、要約先頭 `【...】` が不一致なら **別テーマ混入**の可能性が高いので L2 扱いでドロップ/再生成する。

**L3（廃止 / 人間・エージェントの混乱源になるため入力に混ぜない）**
- `台本本文（冒頭サンプル）` など「本文っぽいサンプル」
  - 生成に混ぜると定型文（例: “深夜の偉人ラジオへようこそ”）が復活しやすい。
  - 保管しても良いが、Aテキスト生成入力としては使用しない。

※ UI/運用上の表示・編集も L1/L2/L3 を分けるのが安全（L3は既定で非表示推奨）。

### 5.4 整合チェック（決定論 lint）— 生成前に止める

Planning の汚染/矛盾は **生成前**に検知して直すのが最安・最速。

- Planning lint（CSV整合）:
  - `python scripts/ops/planning_lint.py --channel CH07 --write-latest`
  - 出力: `logs/regression/planning_lint/`（JSON+Markdown）
- A-text lint（反復/禁則/字数）:
  - `python scripts/ops/a_text_lint.py --channel CH07 --video 009 --write-latest`
  - 出力: `logs/regression/a_text_lint/`（JSON+Markdown）

---

## 6. 投稿済みロック（最終固定 / 触らない指標）

- Planning CSV の `進捗=投稿済み` は **公開済みロック**（以後は原則触らない）。
- ロックを立てると、UI/運用上は「この動画は完了。リテイク対象外」と見なせる。

### 6.1 UI から投稿済みにする（推奨）
- 画面: `Progress` → 行クリック → 詳細モーダル → `投稿済みにする（ロック）`
- 効果:
  - `進捗=投稿済み`
  - `納品` / `音声整形` / `音声検証` / `音声生成` / `音声品質` を **空欄なら強制埋め**（"forced" と明示）
  - `status.json` が存在する場合は `metadata.published_lock=true` と `redo_* = false` を付与
  - ロック中は `redo_script/redo_audio` の UI 変更を禁止（誤操作防止）

### 6.2 API（UI内部利用）
- `POST /api/channels/{CH}/videos/{NNN}/published`
  - payload: `{ "force_complete": true, "published_at": "YYYY-MM-DD" }`（`published_at` は省略可）

### 6.3 解除（誤ロックの修正）
基本は UI の `投稿済みにする` を使うが、**誤ってロックした場合のみ解除可**。  
解除は「進捗=投稿済み」と `status.json` の `published_lock` を戻す操作。

- CLI（推奨）:
  - `python3 scripts/ops/publish_lock_cli.py unlock --channel CH02 --video 024`
  - 進捗を特定値へ戻す場合: `--restore-progress "script: drafted"`
- API:
  - `DELETE /api/channels/{CH}/videos/{NNN}/published`
