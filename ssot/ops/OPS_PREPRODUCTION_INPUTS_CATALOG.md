# OPS_PREPRODUCTION_INPUTS_CATALOG — 入口〜量産投入前の入力カタログ（SoT/必須/任意/上書き）

目的:
- 入口（Planning/任意入力）〜量産投入直前までの「何が入力で、どこが正本で、何が必須/任意か」を **1枚で漏れなく**把握できる状態にする。
- “完全固定の仕様”ではなく、**現行ラインの都合**（命名/配置/成果物形式）に合わせて **参照フレーム**として使う。
- 入力が追加された場合は **拡張として品質が上がる** 一方、入力が無くても **破綻せずに回る** ことを優先する。

関連（正本）:
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 参照フレーム（入口〜投入前の整理）: `ssot/ops/OPS_PREPRODUCTION_FRAME.md`
- Production Pack（投入判断の正本）: `ssot/ops/OPS_PRODUCTION_PACK.md`
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 入力契約（タイトル=正）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 企画差分（Patch）: `ssot/ops/OPS_PLANNING_PATCHES.md`
- ログ配置: `ssot/ops/OPS_LOGGING_MAP.md`

---

## 0) 判断キー（最小で迷わない）

episode を識別する最小キー:
- `channel`: `CHxx`
- `video`: `NNN`（3桁ゼロ埋め）

補助キー:
- `script_id`: `CHxx-NNN`

以降の “必須/任意” は **「量産投入（Script→Audio→Video）に必要か」** の観点で定義する。

---

## 1) 入力の分類（SoT / Config / Extension / Artifact）

- **SoT（正本）**: その工程の唯一の真実（編集対象）。
- **Config**: 参照解決や既定値の決定に使う（基本はgit管理。誤ると再現性が壊れる）。
- **Extension（拡張入力）**: 無くても進むが、あると品質が上がる（欠落はwarn止まりが基本）。
- **Artifact（生成物）**: スナップショット/差分ログ（再現/監査用。SoTは置き換えない）。

---

## 2) 入力カタログ（入口→投入前）

### 2.1 Planning（企画/進捗）

- **SoT**: `workspaces/planning/channels/CHxx.csv`
- **必須**:
  - 該当行（`channel`+`video`）が存在する
  - `タイトル` が空でない（投入不可の停止条件）
- **任意（拡張）**:
  - `企画意図`, `ターゲット層`, `具体的な内容（話の構成案）`
  - `説明文_リード`, `説明文_この動画でわかること`（投稿用説明文の下書き/要点。無くても進むが、あると後段の説明文整備が安定）
  - 追加の“任意列”のキー化は `packages/script_pipeline/tools/optional_fields_registry.py` が正本（表記揺れ対策もここ）。
- **品質ガード**:
  - `python3 scripts/ops/planning_lint.py --channel CHxx --write-latest`
  - “L3汚染”除去（任意・決定論）: `python3 scripts/ops/planning_sanitize.py --channel CHxx --apply --write-latest`

### 2.2 Planning Template（入力の雛形）

- **Config（推奨）**: `workspaces/planning/templates/CHxx_planning_template.csv`
- **位置づけ**: 無くても運用は回るが、CSV追加/列統一の事故率が下がるため **推奨**。

### 2.3 企画の上書き/追加/部分更新（Planning Patch）

- **Config（tracked）**: `workspaces/planning/patches/*.yaml`
- **位置づけ**: CSV手編集を禁止しないが、「追跡したい変更」は Patch を正とする。
- **適用**:
  - dry-run: `python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/<PATCH>.yaml`
  - apply: `python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/<PATCH>.yaml --apply`
- **Artifact（差分ログ）**: `workspaces/logs/regression/planning_patch/`

### 2.4 チャンネル定義（台本/ベンチマーク/動画ワークフロー）

- **Config（正本）**: `packages/script_pipeline/channels/CHxx-*/channel_info.json`
  - `video_workflow`: `capcut` など（動画側の必須/任意判定に影響）
  - `benchmarks`: 任意（あると精度↑。欠落はwarn）
  - `youtube_description`: 任意（投稿用の固定テンプレ。欠落時は `python3 scripts/ops/channel_info_normalize.py --channel CHxx --apply` で補完できる）
- **Config（カタログ/ミラー）**: `packages/script_pipeline/channels/channels_info.json`
  - 編集対象ではない（再生成される）。

### 2.5 チャンネルの script prompt（台本プロンプト）

- **Config（正本）**: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`
- **参照解決**: `configs/sources.yaml: channels.CHxx.channel_prompt` が入口（runnerは repo相対で解決）
- **位置づけ**: “無くても動く”よりも、再現性と品質に直結するため **原則必須**（無い場合は投入判断で止める寄せ方）。

### 2.6 sources.yaml（入口の参照解決）

- **Config（primary/正本）**: `configs/sources.yaml`
- **Config（overlay/局所上書き）**: `packages/script_pipeline/config/sources.yaml`
  - runner は `configs → overlay` の順でマージして解決する（運用上の互換/局所差分用）。
- **保持する情報（例）**:
  - `planning_csv`, `persona`, `channel_prompt`, `chapter_count`, `target_chars_min/max`, `web_search_policy`
- **位置づけ**:
  - 値が欠けても “既定パス” で動く部分はあるが、入口の迷子を減らすため **正本として整備**する。

### 2.7 Persona（拡張入力）

- **Extension（推奨）**: `workspaces/planning/personas/CHxx_PERSONA.md`
- **位置づけ**: 無くても進む（品質が落ちやすいので warn）。
- **注意**: persona は “人間用の表/テンプレ” が混入しやすいので、runner は LLM投入用に抽出・短縮する（詳細は `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`）。

### 2.8 音声設定（voice_config）

- **Config（正本）**: `packages/script_pipeline/audio/channels/CHxx/voice_config.json`
- **位置づけ**: Audio生成の再現性の核なので **必須**（欠落/破損は fail）。

### 2.9 動画 preset / prompt_template（CapCut系）

- **Config（正本）**: `packages/video_pipeline/config/channel_presets.json`
  - `video_workflow=capcut` のチャンネルでは **必須**（preset欠落は fail）。
- **Config（正本）**: `packages/video_pipeline/templates/*.txt`
  - preset の `prompt_template` が指す “画像プロンプトテンプレ”。
- **Config（登録表）**: `packages/video_pipeline/config/template_registry.json`
  - `prompt_template` の登録表（capcut_template の登録表ではない）。
- **位置づけ**:
  - `video_workflow=capcut` の場合は preset（特に `capcut_template`）が **必須**（欠落は fail）。
  - `prompt_template` は **任意**（未指定なら既定テンプレで進む）。ただし指定しているのにファイルが無い場合は実行時に停止するため fail。
  - `template_registry.json` への登録は推奨（未登録は warning）。

### 2.10 サムネ（独立動線）

- **SoT（管理）**: `workspaces/thumbnails/projects.json`, `workspaces/thumbnails/templates.json`
- **SoT（素材）**: `workspaces/thumbnails/assets/{CH}/{NNN}/`
- **位置づけ**: Script/Audio/Video の主線とは独立だが、同じ episode key を共有するため **入力としてカタログ化**する。
- **運用正本**: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`

---

## 3) QAゲート（入口〜投入前）

### 3.1 チャンネル横断（抜け漏れ監査）
- 入口: `python3 scripts/ops/preproduction_audit.py --all --write-latest`
- 出力: `workspaces/logs/regression/preproduction_audit/`
- 目的: “入力が散らばっている”問題を **決定論で列挙**して修正対象を切り分ける。

### 3.2 エピソード単位（投入判断）
- 入口: `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`
- 出力: `workspaces/logs/regression/production_pack/`
- 目的: “この瞬間の入力/設定/ゲート結果”をスナップショットして **再現性を固定**する（SoTは置き換えない）。
- 修復導線（issue → 直す場所）: `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md`

---

## 4) 差分ログ（上書き/追加/部分更新を追跡する）

最低限、次の2系統で「何が変わったか」を追える状態にする:
- Planning Patchログ: `workspaces/logs/regression/planning_patch/`
- Production Pack diff: `workspaces/logs/regression/production_pack/`

ログ配置と保持/削除は `ssot/ops/OPS_LOGGING_MAP.md` と `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正とする。
