# audio_tts_v2

新規TTSパイプライン。目的は「誤読なくストレスなく聞ける音声と字幕」を作ること。旧 commentary_01_srtfile_v2 には依存しない。

## 環境とエンジン
- LLM: `factory_common.llm_router` 経由（`configs/llm_router*.yaml` / `configs/llm_task_overrides.yaml`）。
  - `LLM_MODE=api|think|agent`（API実行 or pending/結果キュー運用）
- Voiceエンジン: `tts/routing.py`（channel/videoごとのデフォルト）+ env override。
  - VOICEVOX: ローカル `http://127.0.0.1:50021`
  - Voicepeak: `/Applications/voicepeak.app/Contents/MacOS/voicepeak`
  - ElevenLabs: env設定（IDは `configs/*` とルーティング設定を参照）

## 入力と出力
- 入力（Aテキスト正本）: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（無ければ `assembled.md`）
  - `run_tts.py` が `assembled_human.md -> assembled.md` を自動同期して「古い台本で合成」事故を防ぐ。
  - 音声用のサニタイズ済み入力は `workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt` として保存される（作業領域。SoTではない）。
- 最終出力（SoT）: `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`, `{CH}-{NNN}.srt`, `log.json`, `a_text.txt`, `audio_manifest.json`

## セグメント分割とポーズ（LLM必須＋機械ガード）
- ポーズ挿入の区切り記号は `---` のみ（1行単独）。
  - ルール正本: `ssot/OPS_A_TEXT_GLOBAL_RULES.md`
  - 実装: `tts/strict_segmenter.py`

## 推奨ワークフロー
1. `assembled_human.md` を編集して確定（無ければ `assembled.md` を編集）。
2. `scripts/sanitize_a_text.py` でメタ（出典/脚注/URL）混入を除去してから音声生成する。
3. `run_tts.py` を実行し、`workspaces/audio/final/...` に出力が揃うことを確認する。

## 典型コマンド
```
PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts \
  --channel CH06 --video 001 \
  --input workspaces/scripts/CH06/001/content/assembled.md
```

## 参照
- `ssot/OPS_AUDIO_TTS_V2.md`
- `ssot/OPS_A_TEXT_GLOBAL_RULES.md`
- `ssot/OPS_ENTRYPOINTS_INDEX.md`
