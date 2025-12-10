# SSOT (Single Source of Truth)

- このディレクトリは最新の設計・運用ドキュメントの正本です。旧階層は `ssot_old/` に移動しました。
- VOICEVOX 読み誤り対策の計画書（設計・実装・TODO 管理）は `PLAN_OPS_VOICEVOX_READING_REFORM.md` を参照してください。
- LLM パイプライン統合計画は `PLAN_LLM_PIPELINE_REFACTOR.md` を参照してください。
- 新規の計画書を作成する場合は、本 README の命名規則に従い、`PLAN_TEMPLATE.md` をコピーして着手してください。
- 新しいドキュメントを追加する場合は本ディレクトリに配置し、必要に応じて本 README へリンクを追記してください。

## 計画書の命名規則と作成手順

- **命名規則**: `PLAN_<ドメイン>_<テーマ>.md`（全て大文字のスネークケース）。例: `PLAN_LLM_PIPELINE_REFACTOR.md`, `PLAN_OPS_VOICEVOX_READING_REFORM.md`。
- **配置場所**: SSOT 配下（このディレクトリ）に直置きする。`ssot_old/` や他の階層に分散させない。
- **テンプレ使用**: 新規作成時は `PLAN_TEMPLATE.md` をコピーし、メタデータとセクションを必ず埋める。
- **参照リンク**: 追加した計画書は README に追記し、用途と範囲が分かる 1 行説明を付ける。

## 新規計画書
- `PLAN_LLM_USAGE_MODEL_EVAL.md`: 台本〜TTS〜画像生成までの LLM 呼び出しのトークン予測とモデル適正の評価・改善提案。
