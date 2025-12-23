# OVERVIEW_PINNED — 共同運用のピン留め（SSOT）

目的:
- どのAIエージェントでも「同じ情報」を参照して台本を作れるようにする
- 高品質の再現性を上げつつ、無駄なリトライとコストを減らす

正本（必ずここを見る）:
- 全チャンネル共通Aテキストルール: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`
- LLM品質ゲート（Judge→Fixer→必要ならRebuild）: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 構成パターン（骨格固定）: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml`
- 大量生産アーキテクチャ: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`
- 入口索引: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`

運用の結論（最短で収束させる）:
1) まず「SSOTパターンで骨格と分量」を固定する（自由に書かせない）
2) 台本は1回で書かせる（設計図→一気に本文）
3) 合否はLLM Judgeで「内容」を見る（機械判定は禁則/字数だけ）
4) failならFixerで最小修正、まだダメならRebuildで作り直し（回数は増やさない）

プロンプト設計の原則（全チャンネル共通で事故らないため）:
- `packages/script_pipeline/prompts/*.txt` は **全チャンネル共通**なので、ドメイン語（例: 特定宗教・特定分野名）を直書きしない。
- ドメイン固有の指針は `configs/sources.yaml` の `channel_prompt`（CH別）と、SSOTパターンの `core_episode_candidates.safe_retelling`（中心エピソード）に集約する。
- Aテキスト向けLLM呼び出しでは `channel_prompt` をそのまま渡さず、衝突しやすい「構成/形式/記号」指示を落とした `a_text_channel_prompt`（派生）を渡す（骨格はSSOTパターンが唯一の正本）。

台本が壊れている時の最短復旧（コマンド）:
- SSOTパターンからAテキストを再構築（plan→draft）:
  - `python -m script_pipeline.cli a-text-rebuild --channel CHxx --video NNN`
- 仕上げの品質ゲート（内容はLLM Judge、形式はハード検査）:
  - `python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
- 音声（台本が validated のものだけ）:
  - `python -m script_pipeline.cli audio --channel CHxx --video NNN`

コスト/モデル（重要）:
- `o3` 系は使用禁止（コストが高い）。設定は無効化済み。
- 原則は `or_deepseek_v3_2_exp` を先頭に、必要なときだけ上位にフォールバックする。
