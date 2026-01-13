# 画像APIリファクタ進捗メモ

DocType: Register（進捗メモ / 未整理）
注意: 実運用の正本は `ssot/ops/OPS_THUMBNAILS_PIPELINE.md` / `ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md` / `configs/image_models.yaml`（等）に寄せる。

## 成果
- `ImageClient` を追加し、タスク名→tier→モデルの解決と capability ベースのオプション正規化を行うルートを用意した。tier は round-robin の開始点を持ち、成功したモデルの次から呼び出す（“固定1モデル”になりにくい）。
- アダプタを整備:
  - Fireworks: `flux-1-schnell-fp8`（同期T2I）/ `flux-kontext-(pro|max)`（非同期→get_resultポーリング、input_image対応）
  - OpenRouter: Gemini系の画像生成（必要時のみマルチモーダル参照画像を添付）
  - Gemini: 直接API（互換/予備）
- `configs/image_models.yaml` を導入し、provider 設定（env var名）、モデル定義、tier 候補、タスクのデフォルト値を一元管理した。設定整合は `tests/test_image_models_config.py` で担保。
- `nanobanana_client` は direct=ImageClient を主線に寄せつつ、`batch`（Gemini Batch: submit→poll→fetch）を追加した。Batch は run_dir に `_gemini_batch/manifest.json` を保存し、途中停止しても再実行で回収できる。`input_images` を透過し、`use_persona=true` のキューでは前フレーム参照で人物/場面のドリフトを抑える（Kontextで特に効く）。
- UI/auto 実行系は `batch|direct|none` の 3 モードに統一（legacy cli/mcp は強制 direct）。CLI は `python3 -m video_pipeline.tools.auto_capcut_run ...` を正本にする。

## 課題
- Kontext（非同期）は `get_result` が一時的に `Task not found` を返すことがあるため、ポーリング間隔/タイムアウトのチューニングと観測（ログ）が必要。
- usage/コスト集計（ImageClientのログ集約）を “運用で見える形” にまだ落とし切れていない。
- 画像生成のE2E（枚数/差し替え/ドラフト反映）をCIで完全に自動化するのは難しく、最小のスモーク（設定整合＋import）中心になりがち。
