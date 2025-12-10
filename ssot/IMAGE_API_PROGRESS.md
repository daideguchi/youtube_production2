# 画像APIリファクタ進捗メモ

## 成果
- `ImageClient` を追加し、タスク名→tier→モデルの解決と capability ベースのオプション正規化を行うルートを用意した。Gemini 画像 API へのアダプタを実装し、`ImageTaskOptions` で aspect_ratio/n/seed などを安全に扱う。生成結果は provider/model/request_id 付きで bytes を返す。 
- `configs/image_models.yaml` を導入し、provider 設定（Gemini API キー環境変数）、モデル定義、tier 候補、タスクのデフォルト値を一元管理した。タスク `visual_image_gen` を 16:9 既定で `image` tier に紐づけている。
- `nanobanana_client` の direct モードで `ImageClient` を先に呼び、初期化失敗や生成失敗時は既存の `llm_router` へフォールバックするルートを保持した。生成成功時は request_id/engine をログ出力し、16:9 リサイズやプレースホルダー生成も従来通り行う。

## 課題
- ImageClient は tier 候補の先頭モデルを固定で採用しており、候補の優先度ローテーションやフェイルオーバーは未実装。usage/コストなどのメタデータ収集も無い。
- Gemini 以外のプロバイダアダプタが未整備で、`extra` や provider 固有パラメータの扱いも限定的。capability で除外するだけでオプションを警告したり補完する仕組みがない。
- `nanobanana_client` 以外の画像生成経路には未適用で、リトライや rate limit ハンドリングも旧ルータ依存のまま。E2E テストや生成枚数・プロバイダ切替の検証も実施していない。
