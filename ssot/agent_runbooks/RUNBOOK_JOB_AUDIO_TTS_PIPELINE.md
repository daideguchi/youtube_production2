# RUNBOOK_JOB_AUDIO_TTS_PIPELINE — 音声生成（end-to-end）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_AUDIO_TTS_PIPELINE
- **ステータス**: Active
- **対象**: audio/TTS を THINK MODE で完走させる
- **最終更新日**: 2026-01-10

重要:
- この Runbook は **AIエージェント（Codex）で pending を解決する運用**を想定している。
- **codex exec（非対話CLI）ではない**。

## 1. 実行例
```bash
./scripts/think.sh --tts -- \
  python -m script_pipeline.cli audio --channel CH06 --video 033
```

## 2. pending が出たら
- `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
- 出力を作って `complete` → rerun
