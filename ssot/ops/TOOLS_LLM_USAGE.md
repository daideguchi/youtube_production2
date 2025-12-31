# LLM Usage ログ集計ツール

- ログファイル: `workspaces/logs/llm_usage.jsonl`（`LLM_ROUTER_LOG_PATH` で上書き、`LLM_ROUTER_LOG_DISABLE=1` で無効化）
- 集計スクリプト: `python3 scripts/aggregate_llm_usage.py --log workspaces/logs/llm_usage.jsonl --top 10`
  - 出力: モデル別/タスク別成功回数、失敗タスク数、fallback_chain 上位、モデル別平均レイテンシ
- 用途: コスト・安定性の傾向把握、fallback多発時の調査起点。
- UI API: `GET /api/llm-usage?limit=200` で最新ログを返却（バックエンドFastAPI）。フロント側で一覧表示やフィルタに利用可。
