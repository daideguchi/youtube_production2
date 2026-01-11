# CH04 SoT

このディレクトリは Phase 1〜3 の作業成果を集約する Single Source of Truth です。
- `config.json` : チャンネル設定（Sheets 仕様など）
- `NNN/` : 台本番号ごとの作業ディレクトリ
  - `content/` : 台本・分析ファイル（SoT）
  - `audio_prep/` : TTS 用の整形テキスト（任意）
  - `status.json` : 進捗ステータス

※ legacy 互換層は廃止済みです。成果物は data/ 以下のみを参照してください（`output/` は廃止）。
