# Commentary Video Automation Pipeline

> **Note (SSOT)**: 本番運用の入口/コマンドは `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` を正とします。  
> この README は開発者向けの補助情報であり、SSOT と矛盾する場合は SSOT を優先してください。

## 🚀 Quick Start (The Golden Path)

**All production tasks should be executed via the Factory CLI.**
Do not run `auto_capcut_run.py` or `run_pipeline.py` directly unless you are debugging.

### 1. New Production (Images -> Belt -> Draft)
標準フロー。Gemini 2.5 Flash Image を使い、CapCutドラフトまで生成。
```bash
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.factory CH01 workspaces/video/input/CH01_<PresetName>/CH01-001.srt --nanobanana direct
```

### 2. Resume / Re-Draft (Skip Image Gen)
画像を再生成せず、最新 run_dir からドラフト/ベルトを再構築。
```bash
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.factory CH01 workspaces/video/input/CH01_<PresetName>/CH01-001.srt draft
```

### 3. Validation Only (No Images)
画像生成なしでセクション/Belt/タイトルのみチェック。
```bash
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.factory CH01 workspaces/video/input/CH01_<PresetName>/CH01-001.srt check --nanobanana none
```

---

## Architecture & SSOT

- **Entry Point**: `tools/factory.py`
  - Wraps `tools/auto_capcut_run.py` with simplified intents.
  - Image generation path is single: `nanobanana=direct` (ImageClient + Gemini 2.5 flash image). Use `--nanobanana none` to skip images.
  - Optional safety: `--abort-on-log "Unknown field,quota,RESOURCE_EXHAUSTED"` でログ検知中断が可能（タイムアウト無しで待つ場合の保険）。
  - Timeout: デフォルトは無制限。必要な場合のみ `--timeout-ms` を指定。
- **Channel Config**: `config/channel_presets.json`
  - Defines templates, layout, image generation density, and styles per channel.
- **Template Registry (SSOT)**: `config/template_registry.json`
  - 全テンプレートの単一ソース。UI/プリセット/ツールはここに列挙されたものだけを使う。
  - 追加したら `scripts/lint_check_templates.py` を実行し、channel_presets の prompt_template が登録済みか検証する。
- **System Config**: `src/core/config.py`
  - Manages API Keys and environment variables.
- **Image Logic**: `src/srt2images/`
  - `cue_maker.py`: Determines image density based on channel config.
  - `llm_prompt_refiner.py`: Integrates context, style, and persona into prompts.

## Legacy / Advanced Usage

Direct execution of `auto_capcut_run.py` is still possible for fine-grained control, but `tools/factory.py` is preferred for standard operations.

- UI integration uses the same underlying modules (`src/srt2images`), ensuring consistency in image generation logic.
- CapCut draft validation and structure are managed by `src/core/domain` schemas.
