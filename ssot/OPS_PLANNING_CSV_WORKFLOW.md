# OPS_PLANNING_CSV_WORKFLOW — 企画/進捗CSV（Planning SoT）の運用手順

目的:
- `progress/channels/CHxx.csv` を **Planning の正本（SoT）** として安全に更新し、台本/音声/動画工程の迷子と破壊を防ぐ。

関連:
- 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
- I/Oスキーマ: `ssot/OPS_IO_SCHEMAS.md`
- 整合チェック: `ssot/OPS_ALIGNMENT_CHECKPOINTS.md`

---

## 0. 正本の定義

- Planning SoT（正本）: `progress/channels/CHxx.csv`
- Script SoT（正本）: `script_pipeline/data/{CH}/{NNN}/status.json`
- Audio SoT（下流参照の正本）: `audio_tts_v2/artifacts/final/{CH}/{NNN}/`

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
1) 対象 CSV を直接編集: `progress/channels/CHxx.csv`
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

