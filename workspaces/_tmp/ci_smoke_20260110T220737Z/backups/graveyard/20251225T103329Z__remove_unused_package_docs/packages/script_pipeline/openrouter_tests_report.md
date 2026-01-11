# OpenRouter 改行テスト・モデル検証レポート（最新版）

作成日: 2025-11-27
作成者: Codex

## 目的
- 字幕改行（句読点直後のみ、30文字以内）を LLM だけで実現できるか検証。
- Azure mini/chat でうまく行かないため、OpenRouter のモデルを試験。
- モデルが変わっても正確にパラメータを調整できる仕組み整備の下準備。

## 変更点（コード/設定）
1. `script_pipeline/tools/openrouter_models.py`
   - `/api/v1/models` から取得したメタ情報を拡充：`default_parameters`, `per_request_limits` を保存。
   - `default_max_completion_tokens` の算出優先度を拡張：
     1) `top_provider.max_completion_tokens`
     2) `per_request_limits.completion_tokens`
     3) `context_length // 2`
     4) fallback 4096
   - `supported_parameters` を空配列ガード付きで保存。
2. `python -m script_pipeline.tools.openrouter_models --free-only` を実行し、`script_pipeline/config/openrouter_models.json` を再生成（29件）。

## 改行タスクで試したモデルと結果（最終）
- 共通プロンプト（例）：
  "句読点（。 、 ！ ？ ）直後のみ改行、各行30文字以内。句読点以外で改行したら失敗。改行ゼロも失敗。文字は変えない。"
- テスト文：ギョベクリ・テペ紹介の短い段落 1 本など。

### 採用: Gemini 3 Pro Preview (thinkingLevel=low)
- エンドポイント: `https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent`
- 認証: `x-goog-api-key: $GEMINI_API_KEY`
- generationConfig: `maxOutputTokens: 4096`, `thinkingConfig.thinkingLevel: "low"`
- 結果: 句読点以外で改行が混ざる（「は、」「考古学者が、」など）が、安定して応答する。行長は概ね30字以内。

### 非採用: 他モデル
- Azure gpt-5-mini (Responses): 改行ほぼゼロ、行長オーバー。句読点改行守らず。
- Azure gpt-5-chat: 改行はするが句読点以外でも折る。行長は抑えるが指示違反。
- o4-mini (Azure chat): タイムアウト。
- OpenRouter 有料 `anthropic/claude-opus-4.5`: 改行は綺麗だが句読点以外改行あり。
- OpenRouter 無料 (grok, qwen系, gpt-oss, gemini-2.0-free 等): タイムアウト/空返答で実用不可。

## 採用ポリシー（台本量産用）
- 改行ステージ（整形）は `gemini-3-pro-preview` を使用し、thinkingLevel=low、maxOutputTokens=4096 で固定。
- 句読点以外で改行が混ざる可能性があるため、後段のバリデータで「句読点以外改行」を弾く前提で運用する。
- 行長制約は 30 字目標（検証は 35 まで緩和）。

## 気づき（最終）
- 無料モデルは負荷・レート制限で長文・制約付きリクエストが不安定（改行タスクほぼ不可）。
- Azure mini/chat は句読点限定改行を守らない／改行ゼロ問題が残る。
- Gemini 3 Pro Preview (low) が最も安定して応答し、行長も抑えやすい。ただし句読点以外改行を完全には制御できないため、バリデータで補完する。

## 次のアクション案（提案）
1. OpenRouter 無料枠は現状実用不可と見なす（改行タスクでは応答なしが多数）。
2. 有料モデルを使う場合は Opus 4.5 などを候補にし、後段バリデータで「句読点以外改行」を弾く運用で妥協するか検討。
3. Azure mini を継続するなら、FAIL_HINT をさらに強化し、プロンプトを「句読点以外改行は即失敗、改行ゼロも失敗」を冒頭で明示。検証許容を 35 文字にしているので、そこに合わせた文言にする。
4. モデル切替時のパラメータ管理は `openrouter_models.json` のメタを活用（`supported_parameters`, `default_parameters`, `context_length`, `max_completion_tokens` 推定を自動化）。

## 参考コマンド（例）
  - OpenRouter メタ再取得（無料のみ）:
    ```bash
    cd <REPO_ROOT>
    ./scripts/with_ytm_env.sh python -m script_pipeline.tools.openrouter_models --free-only
    ```
- Grok 4.1 Fast 短文 QA:
  ```bash
  MODEL="x-ai/grok-4.1-fast:free"
  MSG="How many r's are in the word 'strawberry'?"
  curl -s https://openrouter.ai/api/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $OPENROUTER_API_KEY" \
    -H "HTTP-Referer: https://localhost" \
    -H "X-Title: grok-test" \
    -d '{"model":"'$MODEL'","messages":[{"role":"user","content":"'$MSG'"}],"max_tokens":64}'
  ```
