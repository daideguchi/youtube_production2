# OPS_PREPRODUCTION_REMEDIATION — 入口〜投入前の“抜け漏れ”修復導線（issue → 直す場所 → 検証）

目的:
- `preproduction_audit` / `production_pack` が出す issue を、**誰でも同じ手順で**修復できるようにする。
- “仕様で縛る”ではなく、現行ラインの都合に合わせた **運用導線（直す場所＝SoT）** を固定する。
- 企画の上書き/追加/部分更新が起きても、**判断キー（episode）+ 差分ログ**で追跡できる状態を保つ。

関連（入口/正本）:
- 参照フレーム: `ssot/ops/OPS_PREPRODUCTION_FRAME.md`
- 入力カタログ: `ssot/ops/OPS_PREPRODUCTION_INPUTS_CATALOG.md`
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- Planning Patch: `ssot/ops/OPS_PLANNING_PATCHES.md`
- Production Pack: `ssot/ops/OPS_PRODUCTION_PACK.md`
- ロック運用: `AGENTS.md`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`

---

## 0) 修復の基本手順（強制）

1) **lock確認 → lock作成**（並列衝突防止）
```bash
python3 scripts/agent_org.py locks --path <touch_path>
python3 scripts/agent_org.py lock '<touch_path>' --mode no_touch --ttl-min 60 --note 'fix preproduction issue'
```

2) まず “現状” を出す（決定論）
```bash
python3 scripts/ops/preproduction_audit.py --all --write-latest
python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest
```

3) **直す場所＝SoT** を確定して修正
- Planning（企画）: `workspaces/planning/channels/CHxx.csv`（or Patch）
- Channel config: `packages/script_pipeline/channels/CHxx-*/channel_info.json`
- Script prompt: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`
- Sources config: `configs/sources.yaml`
- Audio config: `packages/script_pipeline/audio/channels/CHxx/voice_config.json`
- Video preset: `packages/video_pipeline/config/channel_presets.json`

4) 再検証して収束（同じコマンドでOK）

---

## 1) issue → 修復導線（代表）

このセクションは **「直す場所」と「最短の確認」** を固定するための一覧。

### 1.1 Sources / 参照解決（configs/sources.yaml）

- `missing_sources_entry` / `missing_sources_channel_entry`
  - 意味: `configs/sources.yaml` に `channels.CHxx` が無い（入口が迷子になる）。
  - 直す場所: `configs/sources.yaml`
  - 推奨修復:
    - 既存チャンネルの登録漏れなら `channels.CHxx` ブロックを追加（planning/persona/prompt/chapter/文字数）。
    - 新チャンネル追加なら `python3 -m script_pipeline.tools.channel_registry create ...` を使う（雛形生成 + sources追記）。
  - 再検証: `python3 scripts/ops/preproduction_audit.py --channel CHxx --write-latest`

- `missing_sources_yaml`
  - 意味: `configs/sources.yaml` が存在しない（入口の参照解決が壊れる）。
  - 直す場所: `configs/sources.yaml`（tracked。削除してはいけない）
  - 再検証: `python3 scripts/ops/preproduction_audit.py --all --write-latest`

- `missing_sources_planning_csv` / `missing_planning_csv`
  - 意味: planning CSV のパスが無い/ファイルが存在しない。
  - 直す場所: `workspaces/planning/channels/CHxx.csv`（ファイル作成/復元） + `configs/sources.yaml`（パス）
  - 再検証: `python3 scripts/ops/planning_lint.py --channel CHxx --write-latest`

- `missing_sources_persona`
  - 意味: sources.yaml に persona の参照が無い（任意入力。欠落はwarn止まり）。
  - 直す場所: `configs/sources.yaml`（任意: `channels.CHxx.persona` を追加）
  - 再検証: `python3 scripts/ops/preproduction_audit.py --channel CHxx --write-latest`

- `missing_sources_channel_prompt` / `missing_channel_prompt`
  - 意味: `channel_prompt`（実体は `script_prompt.txt`）のパスが無い/存在しない。
  - 直す場所:
    - 実体: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`
    - 参照: `configs/sources.yaml: channels.CHxx.channel_prompt`
  - 再検証: `python3 scripts/ops/preproduction_audit.py --channel CHxx --write-latest`

- `sources_yaml_parse_error`
  - 意味: YAMLが壊れている/パースできない。
  - 直す場所: `configs/sources.yaml`（+ overlay があるなら `packages/script_pipeline/config/sources.yaml`）
  - 再検証: `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`

### 1.2 Planning（企画/進捗）

- `missing_planning_row`
  - 意味: `CHxx/NNN` の行が CSV に存在しない。
  - 直す場所:
    - 手で追加（UI推奨）: `/planning`
    - 追跡したい場合: Patch で `add_row`（`workspaces/planning/patches/*.yaml`）
  - 再検証: `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`

- `missing_title`
  - 意味: `タイトル` が空（投入不可）。
  - 直す場所: `workspaces/planning/channels/CHxx.csv`（or Patch の `set`）
  - 再検証: `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`

- `planning_lint_failed` / `planning_lint_global_errors`
  - 意味: planning_lint が error を検出（混入や必須列の欠落など）。
  - 直す場所: 基本は `workspaces/planning/channels/CHxx.csv`
  - 補助:
    - “L3汚染”の除去（決定論）: `python3 scripts/ops/planning_sanitize.py --channel CHxx --apply --write-latest`
  - 再検証: `python3 scripts/ops/planning_lint.py --channel CHxx --write-latest`

- `planning_lint_warnings`
  - 意味: warning がある（投入はできるが、後段のズレ/事故率が上がる）。
  - 直す場所: `workspaces/planning/channels/CHxx.csv`（or Patch）
  - 補助（決定論/任意）:
    - タイトルに沿って“汚染しやすい列”を安全に揃える: `python3 scripts/ops/planning_realign_to_title.py --channel CHxx --from NNN --to MMM --apply --write-latest`
  - 再検証: `python3 scripts/ops/planning_lint.py --channel CHxx --write-latest`

- `missing_required_fields_by_policy`
  - 意味: `planning_requirements` ポリシーで “この動画番号から必須” の列が空/欠落。
  - 直す場所: `workspaces/planning/channels/CHxx.csv`（or Patch）
  - 備考: ルール正本は `packages/script_pipeline/tools/planning_requirements.py`（運用SSOTは `OPS_PLANNING_CSV_WORKFLOW`）。

- `planning_row_published_lock`
  - 意味: “投稿済み” のロックが疑われる（誤って再投入しないための警告）。
  - 直す場所: 原則触らない。誤ロックのときだけ `OPS_PLANNING_CSV_WORKFLOW` の手順で解除。

### 1.3 Channel assets（script_pipeline/channels）

- `missing_channel_info_json` / `invalid_channel_info_json` / `invalid_channel_info_schema`
  - 意味: チャンネル定義が無い/壊れている。
  - 直す場所: `packages/script_pipeline/channels/CHxx-*/channel_info.json`
  - 補助（正規化/再生成）: `python3 scripts/ops/channel_info_normalize.py --channel CHxx --apply`

- `missing_script_prompt` / `missing_script_prompt_txt`
  - 意味: `script_prompt.txt` が無い（品質/再現性が落ちる）。
  - 直す場所: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`

### 1.4 Audio（voice_config）

- `missing_voice_config` / `invalid_voice_config_json` / `invalid_voice_config_schema`
  - 意味: 音声設定が無い/壊れている（投入前 fail）。
  - 直す場所: `packages/script_pipeline/audio/channels/CHxx/voice_config.json`

### 1.5 Video（CapCut preset / template）

- `missing_video_channel_preset`
  - 意味: `channel_presets.json` に該当CHが無い（CapCut主線では停止する）。
  - 直す場所: `packages/video_pipeline/config/channel_presets.json`

- `active_preset_missing_capcut_template`
  - 意味: active preset なのに `capcut_template` が空（CapCutドラフト生成が停止する）。
  - 直す場所: `packages/video_pipeline/config/channel_presets.json`

- `active_preset_missing_prompt_template`
  - 意味: `prompt_template` が未指定（既定テンプレで進むが、画風/品質が安定しにくい）。
  - 直す場所: `packages/video_pipeline/config/channel_presets.json`（任意の品質改善）

- `missing_prompt_template`
  - 意味: `prompt_template` が未指定（既定テンプレで進むが、画風/品質が安定しにくい）。
  - 直す場所: `packages/video_pipeline/config/channel_presets.json`（任意の品質改善）

- `missing_prompt_template_file`
  - 意味: `prompt_template` を指定しているのにファイルが存在しない（実行時に停止）。
  - 直す場所:
    - テンプレ実体: `packages/video_pipeline/templates/*.txt`
    - 参照: `packages/video_pipeline/config/channel_presets.json`

- `prompt_template_not_registered`
  - 意味: `template_registry.json` に登録されていない（ガバナンス上のwarning）。
  - 直す場所: `packages/video_pipeline/config/template_registry.json`

### 1.6 Benchmarks（任意入力）

- `missing_benchmarks` / `benchmarks_empty_channels` / `benchmarks_empty_script_samples`
  - 意味: ベンチマークが無い/空（品質劣化しやすいが投入は可能）。
  - 直す場所: `packages/script_pipeline/channels/CHxx-*/channel_info.json: benchmarks`
  - SSOT: `ssot/ops/OPS_CHANNEL_BENCHMARKS.md`
