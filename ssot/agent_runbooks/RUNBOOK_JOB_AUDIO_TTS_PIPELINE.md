# RUNBOOK_JOB_AUDIO_TTS_PIPELINE — 音声生成（end-to-end）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_AUDIO_TTS_PIPELINE
- **ステータス**: Active
- **対象**: audio/TTS を **対話型AIエージェント（THINK）が推論して完走**させる（外部LLM自動呼び出しなし / 決定論ガードあり）
- **最終更新日**: 2026-01-19

重要（固定）:
- **読みLLM（auditor）無効**: `SKIP_TTS_READING=1`（`YTM_ROUTING_LOCKDOWN=1` 下で `SKIP_TTS_READING=0` は禁止）
- 「アノテーション確定」はエンジン別（SSOT: `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md`）:
  - VOICEVOX: prepass mismatch=0（`reading_mismatches__*.json` が出ない）
  - VOICEPEAK: prepass → B安全形（ASCII/数字ゼロ）→ 合成 → `afplay` で要所確認 + 証跡

## 1. 実行例
```bash
./ops audio -- --channel CH06 --video 033
```

## 2. prepass（必須）→ engine判定 → 分岐 → 合成
1) prepass（wav作らない）:
```bash
./ops audio -- --channel CH06 --video 033 --prepass
```
2) engine を確認（どちらかでOK）:
- コンソール: `[RUN] ... Engine=voicevox|voicepeak`
- ファイル: `workspaces/scripts/CH06/033/audio_prep/log.json` の `engine`

3) engine=voicevox の場合:
- mismatch が出たら（停止する）:
  - `workspaces/scripts/CH06/033/audio_prep/reading_mismatches__*.json` を確認
  - 修正は **B側**（辞書/override/ローカル）で行う（SSOT: `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md`）
- prepass を繰り返して mismatch=0 になったら、合成:
```bash
./ops audio -- --channel CH06 --video 033
```

4) engine=voicepeak の場合:
- B を点検（ASCII/数字が残っていたら辞書/overrideで潰す）:
  - `rg -n \"[A-Za-z]\" workspaces/scripts/CH06/033/audio_prep/script_sanitized.txt`
  - `rg -n \"\\d\" workspaces/scripts/CH06/033/audio_prep/script_sanitized.txt`
- 合成:
  - `./ops audio -- --channel CH06 --video 033`
- サンプル再生チェック（必須）:
  - `afplay workspaces/audio/final/CH06/033/CH06-033.wav`
  - 証跡: `workspaces/scripts/CH06/033/audio_prep/voicepeak_manual_check.txt` に `OK/NG + 理由1行`
