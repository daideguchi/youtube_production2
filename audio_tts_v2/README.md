# audio_tts_v2

新規TTSパイプライン。目的は「誤読なくストレスなく聞ける音声と字幕」を作ること。旧 commentary_01_srtfile_v2 には依存しない。

## 環境とエンジン
- LLM: gpt-5-mini 固定（必須）。`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` を使用。
- VOICEVOX: 青山流星固定。`AOYAMA_SPEAKER_ID` 必須。ローカル `http://127.0.0.1:50021`。
- Voicepeak: `/Applications/voicepeak.app/Contents/MacOS/voicepeak`, narrator=Japanese Male 3（CH01）、Female 1（CH03）。
- ElevenLabs: `eleven_v3` デフォルト。Kyoko=4lOQ7A2l7HPuG7UIHiKA, Ishibashi=Mv8AjrYZCBkdsmDHNwcB, Hinata=j210dv0vWm7fCknyQpbA。
- デフォルトエンジン: voicevox。CH01→voicepeak, CH02/CH05/CH06→voicevox, CH03→voicepeak, CH04→elevenlabs。`ENGINE_DEFAULT_OVERRIDE` で上書き可。

## 入力と出力
- 入力選択: `script_pipeline/data/<CH>/<VIDEO>/audio_prep/script_sanitized.txt` を優先。無ければ `content/assembled.md`。CLIの `--input` で明示可能。
- 出力: `audio_tts_v2/artifacts/audio/<engine>/<channel>/<video>/` に中間生成、`audio_tts_v2/artifacts/final/<channel>/<channel>-<video>.{wav,srt,log.json}` に集約。

## セグメント分割とポーズ（LLM必須＋機械ガード）
- 見出しシグナル: 行頭の `# / ##` または `第X章` を Aテキストに残す。ここを基準に必ず分割・ポーズを入れる。
- LLM分割: 見出しを含んだテキストを LLM に渡し、セグメント候補を得る。
- 機械ガード: 見出しと本文が連結されていれば強制二分割（コロン/句点/最大40文字でカット）。連結のまま進めない。
- ポーズ（LLM提案にバイアスを上乗せ）:
  - 見出し後 1.0s、次が見出しならその前も 1.0s。
  - 段落0.75s、文末0.3–0.75s、読点0.25–0.5s、その他0.25–0.4s（上限1.5s）。
- # はロジック内で保持し、TTS直前にのみ除去。AテキストとSRT本文は正規化一致をチェック。
- LLM失敗時は句読点/改行ベースの簡易分割にフォールバックするが、見出し連結が残る場合はエラーになる。

## 推奨ワークフロー
1. assembled.md に見出しシグナル（# / ## / 第X章）を入れておく。章立てがない場合も「ここで一拍ほしい」場所に `## ...` を足すと安定。
2. SRT分割のみで一度実行し、見出しが単独セグメントかつポーズ値が意図どおりか確認。
3. 問題なければ音声生成を1回だけ行う。同時に複数 run を走らせない。

## 典型コマンド
```
cd /Users/dd/10_YouTube_Automation/factory_commentary
PYTHONPATH=audio_tts_v2 \\
  python audio_tts_v2/scripts/run_tts.py \\
  --channel CH06 --video 001 \\
  --input script_pipeline/data/CH06/001/content/assembled.md
```

## 参照
- handoff/audio_tts_pipeline/README.md （A→B→音声の基本仕様）
- configs/llm_tasks.yaml （LLMプロンプト定義）
