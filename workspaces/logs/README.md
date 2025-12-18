# workspaces/logs/ (logs_root)

このディレクトリは **運用ログの正本**（`factory_common.paths.logs_root()`）です。  
`logs/` は互換 symlink で、実体は `workspaces/logs/` に集約されます。

正本ドキュメント:
- `ssot/OPS_LOGGING_MAP.md`（どこに何が溜まるかの確定マップ）

## L1（長期保持 / 重要）
- `llm_usage.jsonl`（LLM usage）
- `image_usage.log`（画像生成 usage）
- `tts_voicevox_reading.jsonl`（TTS reading/quality）
- `tts_llm_usage.log`（TTS LLM usage）
- `audit_report_global.txt`（監査集約）
- `agent_tasks/`（agent/think-mode のキュー & 結果：運用SoT）

## L3（短期保持 / 再生成可）
- `regression/`（回帰/スモークの出力。例: `validate_status`）
- `ops/`（単発運用のログ束）
- `ui_hub/`（UI起動ログ/PID）
- `swap/`, `repair/`（UI運用の補助ログ）

## 片付け（推奨）
- `python3 scripts/cleanup_workspace.py --logs --dry-run`（まず確認）
- `python3 scripts/ops/cleanup_logs.py --dry-run`（L3のみ対象。agentキュー/L1は保護）
