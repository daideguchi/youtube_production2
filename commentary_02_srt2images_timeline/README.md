# Commentary Video Automation Pipeline

## 🚀 Quick Start (The Golden Path)

**All production tasks should be executed via the Factory CLI.**
Do not run `auto_capcut_run.py` or `run_pipeline.py` directly unless you are debugging.

### 1. New Production (Images -> Belt -> Draft)
Standard flow. Generates images, belt config, and CapCut draft.
```bash
python3 tools/factory.py CH01 input/CH01/script.srt
```

### 2. Resume / Re-Draft (Skip Image Gen)
If images are already generated and you just want to rebuild the draft or belt config.
```bash
python3 tools/factory.py CH01 input/CH01/script.srt draft
```

### 3. Force Regeneration (Fix Images)
Forces regeneration of all images and rebuilds the draft.
```bash
python3 tools/factory.py CH01 input/CH01/script.srt fix
```

---

## Architecture & SSOT

- **Entry Point**: `tools/factory.py`
  - Wraps `tools/auto_capcut_run.py` with simplified intents.
- **Channel Config**: `config/channel_presets.json`
  - Defines templates, layout, image generation density, and styles per channel.
- **System Config**: `src/core/config.py`
  - Manages API Keys and environment variables.
- **Image Logic**: `src/srt2images/`
  - `cue_maker.py`: Determines image density based on channel config.
  - `llm_prompt_refiner.py`: Integrates context, style, and persona into prompts.

## Legacy / Advanced Usage

Direct execution of `auto_capcut_run.py` is still possible for fine-grained control, but `tools/factory.py` is preferred for standard operations.

- UI integration uses the same underlying modules (`src/srt2images`), ensuring consistency in image generation logic.
- CapCut draft validation and structure are managed by `src/core/domain` schemas.