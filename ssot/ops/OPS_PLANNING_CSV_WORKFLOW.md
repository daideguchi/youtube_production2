# OPS_PLANNING_CSV_WORKFLOW — 企画/進捗CSV（Planning SoT）の運用手順

目的:
- `workspaces/planning/channels/CHxx.csv` を **Planning の正本（SoT）** として安全に更新し、台本/音声/動画工程の迷子と破壊を防ぐ。

関連:
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- I/Oスキーマ: `ssot/ops/OPS_IO_SCHEMAS.md`
- 整合チェック: `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- 入力契約（タイトル=正 / 補助 / 禁止）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
※ 注（2026-01-09）: `台本型`（kata1〜3）カラム運用は廃止。既存CSVに列が残っていても台本生成は参照しない。

---

## 0. 正本の定義

- Planning SoT（正本）: `workspaces/planning/channels/CHxx.csv`
- Script SoT（正本）: `workspaces/scripts/{CH}/{NNN}/status.json`
- Audio SoT（下流参照の正本）: `workspaces/audio/final/{CH}/{NNN}/`

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
- 外部で CSV を編集した場合は、UI の「企画CSVを再読込」で再取得する（Planning CSV は都度読み込みでキャッシュしない）。

### 2.2 手動編集（許可・ただし厳守）
1) 対象 CSV を直接編集: `workspaces/planning/channels/CHxx.csv`
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
- `作成フラグ` = 省略時 `3`（存在する列のみ）
- `品質チェック結果` = 未設定時 `未完了`
- `更新日時` = 現在時刻

チャンネルによって「必須列」が異なる場合がある（UI は `planning_requirements` に基づいてガードする）。

補助導線（pre-planning の企画カード在庫から投入したい場合）:
- `python3 scripts/ops/idea.py slot --channel CHxx --n 10`（patch生成→lint）
- `python3 scripts/ops/idea.py slot --channel CHxx --n 10 --apply`（Planning CSVへ反映）
- 運用SSOT: `ssot/ops/OPS_IDEA_CARDS.md`

---

## 4. 変更を下流へ反映するルール（超重要）

Planning CSV を更新しても、既に生成済みの台本/音声/動画は自動で差し替わらない。  
下流へ反映したい場合は、原則「reset → 再生成」で揃える。

例:
- タイトル/企画意図/タグを変更した → Script の該当ステージを reset して再生成
- 台本が出来た後に企画を大きく変えた → その動画は **最初からやり直す**（混在が最悪の事故源）

---

## 4.5 Planning汚染の検出（推奨）

Planning CSV は「内容汚染」が起きやすい（例: 別動画の `内容（企画要約）` が混入）。
下流（台本/判定）を誤誘導しないため、まずは lint で見える化する。

- Lint（機械・低コスト）:
  - `python3 scripts/ops/planning_lint.py --channel CHxx`
  - `python3 scripts/ops/planning_lint.py --all`
- 運用で「tag_mismatch を見逃さず止めたい」場合:
  - `python3 scripts/ops/planning_lint.py --channel CHxx --tag-mismatch-is-error`（exit非0）
  - 生成側も早期停止できる: `SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH=1`（高コストLLM前に止める。既定は `0`）
- `tag_mismatch_title_vs_content_summary` が出たら:
  - CSVを直すのが本筋
  - 直るまでの間は「入力契約（タイトル=正）」により、内容汚染しやすいテーマヒントが自動で無視される（= 事故防止）

---

## 4.6 エピソード重複の予防（全チャンネル共通）

台本が増えてくると「同じ内容のエピソードが乱立」しやすい。  
特に CH01 のようにテーマが近い回が続くと、視聴者が戸惑いやすいので **採用済みエピソードの分類タグ** を Planning に残して重複を検知する。

### 4.6.1 管理パラメータ（SoT = Planning CSV）
- 主キー（重複判定の軸）: `キーコンセプト`
- 補助タグ: `悩みタグ_メイン`, `悩みタグ_サブ`（必要なら `ライフシーン` も併用）

運用ルール:
- `キーコンセプト` は「その回の核」を短い語で固定し、**同一チャンネル内でなるべく重複しない**ようにする（完全禁止ではない）。
- 既に採用済み（Planning CSV 上は `進捗=投稿済み/公開済み`。UI の `投稿完了`=ON（`published_lock=true`）も採用済み扱い）の回と同じ `キーコンセプト` を使う場合は、内容が被らない理由を `企画意図` に明記してから進める。

### 4.6.2 検知（推奨）
- `python3 scripts/ops/planning_lint.py --channel CHxx`
  - 採用済み（Planning CSV の `進捗=投稿済み/公開済み`）の回との `キーコンセプト` 重複を警告として出す。
- strict運用（任意）:
  - `SCRIPT_BLOCK_ON_EPISODE_DUPLICATION=1` で、高コスト工程（research/outline/draft）の前に停止できる（既定OFF）。
  - 判定対象の「採用済み」は、Planning CSV の `進捗=投稿済み/公開済み` に加えて `published_lock=true`（UI の `投稿完了`）も含む。

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
- `史実エピソード候補`（CH01）: 使う史実/逸話を短くメモ（断定/会話捏造は禁止。曖昧な点は「伝えられています」「諸説あります」で安全化）

ヘッダ例は `ssot/ops/OPS_IO_SCHEMAS.md` を参照。

### 5.3 入力契約（タイトル=正 / 補助 / 禁止）— 迷走と内容汚染を止める

Planning CSV は列が増えるほど「別行の古い内容が混入」しやすく、台本のズレ/担当の混乱の原因になる。  
そのため Aテキスト生成に使う列は **契約として固定**し、矛盾した列は機械的に無視/再生成する。

**タイトル（絶対正 / 生成の根拠）**
- `タイトル`（絶対）
- `企画意図` / `具体的な内容（話の構成案）` / `ターゲット層`（チャンネルで必須の場合は必須）

**内容汚染しやすいテーマヒント（矛盾時は自動で無視）**
- `内容（企画要約）`
  - タイトル先頭 `【...】` と、要約先頭 `【...】` が不一致なら **別テーマ混入**の可能性が高いので、台本生成では無視する（CSV修正推奨）。
- `悩みタグ`, `キーコンセプト`, `ベネフィット一言` など（内容汚染しやすくズレ事故の元になりやすい）

**禁止（人間・エージェントの混乱源なのでAI入力に入れない）**
- `台本本文（冒頭サンプル）` など「本文っぽいサンプル」
  - 生成に混ぜると定型文（例: “こんばんは、◯◯へようこそ”）が復活しやすい。
  - 保管しても良いが、Aテキスト生成入力としては使用しない。

※ UI/運用上の表示・編集も「タイトル / 補助 / 禁止」を分けるのが安全（本文っぽいサンプルは既定で非表示推奨）。

### 5.4 整合チェック（決定論 lint）— 生成前に止める

Planning の汚染/矛盾は **生成前**に検知して直すのが最安・最速。

- Planning lint（CSV整合）:
  - `python scripts/ops/planning_lint.py --channel CH07 --write-latest`
  - 出力: `workspaces/logs/regression/planning_lint/`（JSON+Markdown）
- A-text lint（反復/禁則/字数）:
  - `python scripts/ops/a_text_lint.py --channel CH07 --video 009 --write-latest`
  - 出力: `workspaces/logs/regression/a_text_lint/`（JSON+Markdown）

---

## 6. 投稿済みロック（最終固定 / 触らない指標）

- Planning CSV の `進捗=投稿済み` は **公開済みロック**（以後は原則触らない）。
- ロックを立てると、UI/運用上は「この動画は完了。リテイク対象外」と見なせる。

### 6.1 UI から投稿済みにする（推奨）
- 画面: UI `/planning` → 行クリック → 詳細モーダル → `投稿済みにする（ロック）`
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

---

## 7. 対話AI監査（参考）— UI表示用（ゲートではない）

目的:
- 台本が大量にあると全件目視が困難なため、対話AIが行った「読み合わせ監査」を **UIに表示**して、人間が判断/指示しやすくする。
- これは **厳格な品質ゲートではなく参考情報**（最終判断は人間）。

SoT（保存先）:
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json`
  - `metadata.dialog_ai_audit`（監査時刻 `audited_at` を含む）
- 監査ツール SSOT: `ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md`

UI表示（現行）:
- Planning 画面: 列 `監査(参考)`
  - `pass` → `OK`
  - `fail` → `NG`
  - `grey` → `要確認`
  - 未設定 → `未`
  - `stale` → `要再査定`（監査後に台本が変更された可能性が高い）
- 台本詳細（/studio）: `要点 / 判定` に `監査(参考)` チップを表示
- 監査時刻: `audited_at`（UTC, `...Z`）をツールチップ等で参照する

`stale`（要再査定）の判定:
- `metadata.dialog_ai_audit.script_hash_sha1` と、現在の
  `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（あれば優先）/ `assembled.md` の sha1 が不一致なら `stale` とみなす。
- `stale` の場合は「監査が古い」可能性があるため、対話AIで再監査して `audited_at` を更新する（機械的に文章を足して帳尻合わせ、はしない）。

更新方法（LLM API 禁止）:
- `python3 scripts/ops/dialog_ai_script_audit.py mark --channel CHxx --video NNN --verdict pass|fail|grey --reasons "..." --note "..." --audited-by "$LLM_AGENT_NAME"`

---

## 8. 台本リセット（UI）— 途中修正より作り直しが早いケース

目的:
- 中途半端な台本を「機械的に継ぎ足して通す」のではなく、**一度まっさらに戻して作り直す**導線を用意する。
- これは品質のための運用であり、文字数合わせのための自動追記とは別物。

UI:
- 台本詳細（/studio）: `台本リセット` / `台本+リサーチもリセット`
  - `台本リセット`: 台本/音声/生成物を削除して初期化。リサーチは保持。
  - `台本+リサーチもリセット`: 研究メモ含めて削除（復元不可）。

API（UI内部利用）:
- `POST /api/meta/script_reset/{CH}/{NNN}`
  - payload: `{ "wipe_research": true|false }`

安全柵:
- `published_lock=true`（投稿済み）はリセット禁止（誤操作防止）。
- 作業ロック（`scripts/agent_org.py lock ...`）が当たっている場合は 409 で停止（並列衝突防止）。

リセット後:
- `workspaces/scripts/{CH}/{NNN}/status.json` が `pending` へ戻り、工程を最初から再実行できる。
- 既存の `metadata.redo_note`（リテイクメモ）は維持する（人間メモを誤って消さない）。
- `redo_script` / `redo_audio` は未設定に戻るため、UI/運用上はデフォルト（`true` 扱い）として再収録/再生成対象になる。

---

## 9. 壊れ台本の削除（ops）— prune_broken_scripts（LLM API禁止）

目的:
- 文章が空/壊れている台本を放置すると、UIや後工程で「あるように見えて中身がない」事故が起きるため、**安全に初期化（reset）**する。

ツール:
- dry-run:
  - `python3 scripts/ops/prune_broken_scripts.py --channel CHxx`
- apply（破壊的）:
  - `export LLM_AGENT_NAME=...` を設定した上で実行（自分の lock は許可、他人の lock はブロック）
  - `python3 scripts/ops/prune_broken_scripts.py --channel CHxx --apply`
  - 既定は「空/欠損のみ」を対象（短いだけの台本は触らない）。
  - どうしても短い台本もリセットしたい場合だけ `--include-too-short` を付ける（慎重に）。

投稿済み除外:
- `published_lock=true` だけでなく、Planning CSV の `進捗=投稿済み/公開済み` や `YouTubeID` の存在も published とみなして除外する（安全側）。

ログ:
- `workspaces/logs/ops/prune_broken_scripts/` に JSON/Markdown で出力する。
