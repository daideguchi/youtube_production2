# RUNBOOK_JOB_AUDIO_TTS_PIPELINE — 音声生成（end-to-end）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_AUDIO_TTS_PIPELINE
- **ステータス**: Active
- **対象**: audio/TTS を THINK MODE で完走させる
- **最終更新日**: 2025-12-12

## 1. 実行例
```bash
./scripts/think.sh --tts -- \
  python -m script_pipeline.cli audio --channel CH06 --video 033
```

## 2. pending が出たら
- `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
- 出力を作って `complete` → rerun

