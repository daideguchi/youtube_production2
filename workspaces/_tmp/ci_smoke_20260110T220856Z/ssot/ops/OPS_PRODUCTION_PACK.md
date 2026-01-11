# OPS_PRODUCTION_PACK — 量産投入前の「Production Pack」定義（正本）

目的:
- 入口（Planning/任意入力）〜量産投入直前までを **1つのスナップショット** に束ね、再現性と品質を安定させる。
- 「入力が無くても破綻しない」設計に寄せつつ、入力が追加された場合は **拡張として品質が上がる** 形にする。
- 企画の上書き/追加/部分更新が起きても、**何が変わったか（差分）** が追跡できる状態にする。

関連:
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 参照フレーム（入口〜投入前の整理）: `ssot/ops/OPS_PREPRODUCTION_FRAME.md`
- 企画SoT（CSV）: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 入力契約（タイトル=正）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 整合チェック: `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- スクリプト工場（入口固定）: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
- 運用ログ: `ssot/ops/OPS_LOGGING_MAP.md`

---

## 0. Production Pack とは（定義）

Production Pack は「この動画を量産パイプラインへ投入する直前に必要な情報」を、決定論的にまとめたもの。

- **正本（SoT）を置き換えない**:
  - Planning SoT は引き続き `workspaces/planning/channels/CHxx.csv`
  - Script/Audio/Video の SoT はそれぞれ `workspaces/scripts|audio|video` に残る
- Pack は **スナップショット**（= 生成物）:
  - 作成時点の入力/参照/ゲート結果を固定し、後から追跡できるようにする

---

## 1. どこで使うか（工程上の位置）

Pack は「量産投入の前段」で使う（= ここで止めれば事故が最安）。

推奨:
1) Planning を更新（UI or 直接編集）
2) Planning lint / 最低限の QA を通す
3) Production Pack を生成（スナップショット + 判定）
4) Pack を見て投入判断（pass / warn / fail）
5) 台本→音声→動画→投稿へ

生成（CLI）:
- `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`
- 出力: `workspaces/logs/regression/production_pack/`
- 終了コード（自動化向け）: `0=pass`, `1=warn`, `2=fail`

---

## 2. 必須入力 / 任意入力（入力が無くても破綻しない設計）

### 2.1 必須（無いと止める）
- `channel`（`CHxx`）
- `video`（`NNN`）
- Planning CSV の該当行
  - 最低限: `タイトル` が存在すること

### 2.2 任意（無くても動くが、あると品質/精度が上がる）
- Persona（例: `workspaces/planning/personas/CHxx_PERSONA.md`）
- ベンチマーク/バズ台本/勝ちパターン（例: `packages/script_pipeline/channels/CHxx-*/channel_info.json: benchmarks` → `workspaces/research/**`）
- サムネ参照（例: `workspaces/thumbnails/projects.json`, `workspaces/thumbnails/assets/{CH}/{NNN}/`）
- 動画テンプレ参照（例: `packages/video_pipeline/config/channel_presets.json`）
- チャンネルの入出典（chapter_count/文字数など）: `configs/sources.yaml`

任意入力は **「欠落=空」でもパイプラインが進む** ように扱い、存在する場合のみ後段の品質に効かせる。

---

## 3. Pack に含めるべき情報（スキーマの考え方）

Pack は最低限、次を保持する:
- **識別子**: `channel`, `video`, `script_id (CHxx-NNN)`
- **Planning row snapshot**（タイトル最優先）
- **参照の解決結果**（どのファイル/テンプレ/プロンプトを使ったか）
- **QA Gate 結果**（pass/warn/fail + 要約 + 根拠）
- **再現性メタ**:
  - 入力ファイルの hash / mtime
  - 作成時刻、スキーマversion、生成コマンド（あれば）

出力形式は JSON を基本とする（例: `schema: ytm.production_pack.v1`）。

### 3.1 現行 `production_pack.py` がスナップショットする主な参照（実装ベース）
- Planning: `workspaces/planning/channels/CHxx.csv` の該当行（row snapshot）+ planning_lint（targeted）
- Planning Patch: `workspaces/planning/patches/*.yaml`（該当episodeのみ、best-effort）
- Sources: `configs/sources.yaml`（+ overlay `packages/script_pipeline/config/sources.yaml`）の該当CH設定（`resolved.sources.*`）
- Script: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`, `channel_info.json`, `templates.yaml`, `stages.yaml`
- Audio: `packages/script_pipeline/audio/channels/CHxx/voice_config.json`（音声設定の正本。存在/JSON妥当性をゲートする）
- Video: `packages/video_pipeline/config/channel_presets.json`（該当CHのpreset解決）, `template_registry.json`（prompt_template の登録表）, `system_prompt_for_image_generation.txt`
- Thumbnails: `workspaces/thumbnails/templates.json`, `workspaces/thumbnails/projects.json`, `workspaces/thumbnails/assets/{CH}/{NNN}/`（存在のみ）
- 任意: `workspaces/planning/personas/CHxx_PERSONA.md`, `workspaces/planning/templates/CHxx_planning_template.csv`, `benchmarks_summary`（channel_info由来）

---

## 4. QA Gate（最低限の合否/警告の考え方）

Production Pack 生成時に、最低限ここまでを判定する:

**Fail（投入禁止）**
- Planning CSV が存在しない / 該当行が見つからない
- `タイトル` が空
- チャンネル定義が壊れている（例: channels registry の欠落）
- 音声設定が壊れている（`packages/script_pipeline/audio/channels/CHxx/voice_config.json` が無い/壊れている）
- `video_workflow=capcut` なのに、Video preset が欠落/壊れ（`channel_presets.json` に該当CHが無い、または `capcut_template` が空）
- `prompt_template` を指定しているのに、テンプレファイルが存在しない（実行時に停止するため）

**Warn（投入はできるが、後段の品質リスクが高い）**
- Persona が無い（必須ではないが品質劣化しやすい）
- Planning lint が warning を含む（内容混入の兆候など）
- sources.yaml の CH 定義が欠落（例: `configs/sources.yaml: channels.CHxx` が無い）
- Planning 行がポリシー必須フィールドを欠落（`planning_requirements` が要求する列が空/欠落。チャンネル/動画番号によって適用される）
- Planning 行が「投稿済み（published lock）」っぽい（誤って再投入しないための注意喚起）
- `video_workflow=capcut` で `prompt_template` が未指定（既定テンプレで進むが、画風/品質が安定しにくい）
- `prompt_template` が `template_registry.json` に未登録（ガバナンス上のwarning）

**Pass**
- 上記 fail/warn に該当しない

※ Gate の詳細は既存の決定論ツールを優先する:
- Planning lint: `python3 scripts/ops/planning_lint.py ...`
- A-text lint: `python3 scripts/ops/a_text_lint.py ...`

メモ:
- `production_pack.py` の `qa_gate` には `result (pass/warn/fail)` に加えて `score (0-100)` と `counts` を含める（運用の目安）。
- `qa_gate.issues[*].fix_hints`（任意）がある場合は、それが最短の修復導線。体系は `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md` を正とする。

---

## 5. 差分ログ（企画の上書き/追加/部分更新の追跡）

Production Pack は「その時点のスナップショット」なので、再生成すると差分が出る。
差分は **必ずログに残す**（後から「なぜ変わったか」を辿れることが品質安定の条件）。

- 変更元:
  - Planning CSV の更新
  - 企画上書きの適用（patch）
  - Persona/ベンチマーク等の任意入力の追加
- 変更ログ:
  - `workspaces/logs/regression/production_pack/`（Pack 本体 + diff/summary）

`--write-latest` を付けると、直前の `*_latest.json` と比較した diff も生成する:
- Pack:
  - `production_pack_<CHxx_NNN>__<ts>.json`
  - `production_pack_<CHxx_NNN>__latest.json`
- Diff:
  - `production_pack_<CHxx_NNN>__diff__<ts>.json`
  - `production_pack_<CHxx_NNN>__diff__latest.json`

diff では `generated_at` や `tool.*` など「毎回変わるノイズ」は無視する（意味のある差分が見えるようにする）。

---

## 6. 段階導入プラン（現行ラインを壊さない）

Phase 0（今すぐ）:
- Pack は「生成して眺める」だけで良い（現行 runner を変更しない）。
- Pack の pass/warn/fail を人間/UI が参照して投入判断する。
- 併用（推奨）: `preproduction_audit` でチャンネル横断の抜け漏れを先に潰す（`python3 scripts/ops/preproduction_audit.py --all --write-latest`）。
- 修復は `qa_gate.issues[*].fix_hints`（任意）と `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md` を正とする。

Phase 1（運用が固まってから）:
- UI で「Production Pack を生成 → Gate 結果を表示」する。
- 企画上書き（patch）を UI/CLI から適用し、差分を見える化する。

Phase 2（最終）:
- runner/ジョブが Pack を入力として受け取り、参照の解決とゲート結果を固定した状態で処理を走らせる。
