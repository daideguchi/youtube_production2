# RUNBOOK_AUDIO_TTS — 音声/TTS（tts_*）

## Runbook metadata
- **Runbook ID**: RUNBOOK_AUDIO_TTS
- **ステータス**: Active
- **対象**: `tts_*`
- **最終更新日**: 2026-01-10

重要（用語の固定）:
- ここで言う「Codex（AIエージェント）」は **pending（THINK/AGENT）運用で output を作る担当**のこと。
- **codex exec（非対話CLI）とは別物**。TTSは codex exec へ寄せない。

## 1. 目的（DoD）
- 読み/区切り/間/注釈など、TTS工程に必要な **出力コンテンツだけ** を生成する。
- `response_format=json_object` のタスクは JSON のみで返す（混ぜない）。

## 2. 手順
1. pending を確認: `python scripts/agent_runner.py show <TASK_ID>`
2. `messages` の指示どおりに出力を作る
   - 読みの根拠（mecab/voicevox/ruby等）が必要なら `messages` の要請に従って含める
3. results を投入: `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
4. 元コマンドを rerun

## 3. 禁止事項
- JSON指定で JSON 以外を混ぜない
