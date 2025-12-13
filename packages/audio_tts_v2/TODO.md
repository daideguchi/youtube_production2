# audio_tts_v2 TODO (音声作成用)

- [x] Azure gpt-5-mini 実呼び出し 404 解消（/openai/responses API、deployment 名 gpt-5-mini、api-version は 2025-03-01-preview 優先）
- [x] `scripts/run_tts.py` を AZURE_OPENAI_API_KEY + AOYAMA_SPEAKER_ID=13 で通すスモーク
- [x] Voicepeak CLI (Japanese Male 3) 直列処理での生成確認（サンプルテキスト使用）
- [x] QA (StageT-80) を gpt-5-mini で実行できるか確認
- [x] ログ出力 (`logs/audio_tts/..._tts.json`) の項目が仕様通りか再確認
- [x] 不要フォールバックが残っていないかチェック
