# RUNBOOK_AUDIO_TTS — 音声/TTS（推論=対話型AIエージェント / 読みLLM無効）

## Runbook metadata
- **Runbook ID**: RUNBOOK_AUDIO_TTS
- **ステータス**: Active
- **対象**: audio/TTS（VOICEVOX: prepass mismatch=0 / VOICEPEAK: prepass + サンプル再生で合格）
- **最終更新日**: 2026-01-19

重要（固定）:
- **読みLLM（auditor）無効**: `SKIP_TTS_READING=1`（`YTM_ROUTING_LOCKDOWN=1` 下で `SKIP_TTS_READING=0` は禁止）
- `tts_*`（`tts_reading` 等）の LLM 経路は “実装/実験の痕跡” として残っているが、運用では使わない（誤用防止ガードあり）。
- このrunbookの判断者: **対話型AIエージェント（THINK）**。
- エンジンは **自動決定**（通常運用で `ENGINE_DEFAULT_OVERRIDE` / `--engine-override` は使わない。ロックダウン中は停止）。

## 1. 目的（DoD）
- `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav/.srt` を揃える。
- エンジン別DoD:
  - **VOICEVOX**: `--prepass` で mismatch=0（`reading_mismatches__*.json` が出ない）→ 合成へ進む
  - **VOICEPEAK**: `--prepass` 実施 → B（`script_sanitized.txt`）が安全形（ASCII/数字が残らない）→ 合成 → `afplay` で要所確認し `voicepeak_manual_check.txt` に `OK/NG` を残す

## 2. 手順（共通: prepass → engine判定 → 分岐）
1) prepass（wavは作らない）:
   - `./ops audio -- --channel CHxx --video NNN --prepass`
2) engine を確認（どちらかでOK）:
   - コンソール: `[RUN] ... Engine=voicevox|voicepeak`
   - ファイル: `workspaces/scripts/{CH}/{NNN}/audio_prep/log.json` の `engine`
3) engine=voicevox → **3章**へ / engine=voicepeak → **4章**へ

## 3. VOICEVOX（決定論: mismatch=0 を必ず満たす）
1) mismatch が出たら（停止する）:
   - report: `workspaces/scripts/{CH}/{NNN}/audio_prep/reading_mismatches__*.json`
2) 修正箇所（安全順）:
   - グローバル確定語: `packages/audio_tts/data/global_knowledge_base.json`
   - チャンネル確定語: `packages/audio_tts/data/reading_dict/CHxx.yaml`
   - 回ローカル（フレーズのみ）: `workspaces/scripts/{CH}/{NNN}/audio_prep/local_reading_dict.json`
   - 回ローカル（位置指定）: `workspaces/scripts/{CH}/{NNN}/audio_prep/local_token_overrides.json`
3) prepass を繰り返して mismatch=0 → 合成:
   - `./ops audio -- --channel CHxx --video NNN`

## 4. VOICEPEAK（決定論: Bを安全形へ寄せる + サンプル再生で合格）
1) prepass 後に B を点検（ASCII/数字が残っていたら辞書/overrideで潰す）:
   - `rg -n \"[A-Za-z]\" workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`
   - `rg -n \"\\d\" workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`
2) 合成:
   - `./ops audio -- --channel CHxx --video NNN`
3) サンプル再生チェック（必須）:
   - `afplay workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav`
   - 証跡: `workspaces/scripts/{CH}/{NNN}/audio_prep/voicepeak_manual_check.txt` に `OK/NG + 理由1行`

## 5. 禁止事項
- `SKIP_TTS_READING=0` での運用（ロックダウン中は禁止）
- Aテキスト（`assembled*.md`）をTTS目的で書き換える（SoT破壊）
- engine の強制上書き（`ENGINE_DEFAULT_OVERRIDE` / `--engine-override`）を通常運用で使う（迷子/再現性崩壊）
