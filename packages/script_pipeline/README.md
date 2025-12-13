# script_pipeline — 台本生成パイプライン（SoT: `workspaces/scripts`）

このパッケージは「企画CSV → 台本 → 音声/TTS入口」までの **ステージ管理・SoT管理** を担当します。  
入口と確定フローは `START_HERE.md` と `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md` を正とします。

## SoT / Inputs
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json`（互換: `script_pipeline/data/{CH}/{NNN}/status.json`）
- Planning SoT: `workspaces/planning/channels/{CH}.csv`
- Persona: `workspaces/planning/personas/{CH}_PERSONA.md`
- LLM: `configs/llm.yml`（ルーティング）+ `configs/*`（候補/上書き）
- Env: リポジトリ直下 `.env`（`sitecustomize.py` / runner がロード）

## Stages（`stages.yaml`）
1. `topic_research` → `content/analysis/research/*`
2. `script_outline` → `content/outline.md`
3. `chapter_brief` → `content/chapters/chapter_briefs.json`
4. `script_draft` → `content/chapters/chapter_*.md`
5. `script_enhancement` → （出力なし）
6. `script_review` → `content/assembled.md`（+ optional: `content/final/*`）
7. `quality_check` → `content/analysis/research/quality_review.md`
8. `script_validation` → （出力なし）
9. `audio_synthesis` → `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.{wav,srt}`

生成物の保持/削除は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正とします。

## CLI
```
# init（status.json が無い場合のみ）
python3 -m script_pipeline.cli init --channel CH06 --video 033 --title "<title>"

# pending ステージを 1 つ進める
python3 -m script_pipeline.cli next --channel CH06 --video 033

# 特定ステージを実行
python3 -m script_pipeline.cli run --channel CH06 --video 033 --stage script_review

# strict validate（必須出力チェック）
python3 -m script_pipeline.cli validate --channel CH06 --video 033
```

## Ops / Guards
- 整合チェック（repair込み）: `python3 scripts/validate_status_sweep.py --repair-global`
- 資産同期チェック（dry-run）: `python3 scripts/force_asset_sync.py --dry-run`

## Files
- `stages.yaml`, `templates.yaml`
- `sot.py`, `runner.py`, `validator.py`
- `tools/*`（planning_store/openrouter_models等）
