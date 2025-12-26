# LLM トークン予測・モデル適正評価計画

> Plan metadata
> - Plan ID: **PLAN_LLM_USAGE_MODEL_EVAL**
> - ステータス: Draft
> - 担当/レビュー: Codex（LLM 運用）
> - 対象範囲: 台本（script_pipeline）、B テキスト/TTS（audio_tts/tts）、画像ドラフト（video_pipeline）における LLM 全呼び出し
> - 最終更新日: 2025-12-12

## 0. TL;DR（ざっくり要約）
- **どこを見ればいい？** 表は「どのステージで何トークンくらい使い、どの tier/model が最適か」を一目で示す。困ったら `2. ステージ別トークン予測と推奨モデル` を見る。
- **総コスト感**: 台本（~50–60k）、TTS（~47k）、画像文脈（~60k）を足しても 128k 以内。`azure_gpt5_mini` でほぼカバーでき、重いステップだけ `openrouter_deepseek_r1` に逃がす。
- **優先アクション（短期）**:
  1. heavy_reasoning は `thinking=high` を明示、standard/cheap は thinking 無効で課金事故を防止。
  2. 台本 draft の章長さを均す（長い章を自動分割）＋ format chunk を 600 文字程度に縮めて 25% 節約。
  3. TTS 前処理で記号だけのトークンを除外し、visual_section は 600 セグメント単位で分割呼び出し。

## 1. 目的と前提
- 台本執筆〜動画完成までの **全 LLM 呼び出し箇所** を洗い出し、推定トークン数と最適モデルを提示する。
- 既存の LLM ルーター設定（`configs/llm_router.yaml`）と実装を踏まえ、コスト・品質・速度を両立する改善策をまとめる。【F:configs/llm_router.yaml†L4-L172】
- **トークン試算の前提**（日本語中心）
  - 100 文字 ≒ 100 tokens（gpt 系は 0.7〜1.1 の幅を許容）。
  - 1,600 "word target" は約 1,600〜2,000 tokens とみなす。【F:script_pipeline/runner.py†L1318-L1336】
  - 出力は入力文字数の 0.8〜1.1 倍で概算（リライト系は 1:1、整形系は 0.9）。

## 2. ステージ別トークン予測と推奨モデル
### 2.1 台本パイプライン（script_pipeline）
| ステージ | 入力ボリューム (概算) | 期待出力 | 1 回あたり推定 Tokens (prompt + completion) | 呼び出し回数 | 推奨モデル/ tier | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| script_outline | タイトル + persona + 目標総語数 (~2k) で ~1k tokens | 見出し 6-8 本 | ~1.2k + 0.5k = **1.7k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 高精度アウトラインが後続すべての品質を決める。【F:script_pipeline/runner.py†L1231-L1270】【F:configs/llm_router.yaml†L94-L107】 |
| chapter_brief | outline + 章数 (デフォ 7) で ~1.5k tokens | 章ごとの要約 JSON | ~1.5k + 0.7k = **2.2k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 章整合性チェックがあるため reasoning 必須。【F:script_pipeline/runner.py†L1271-L1292】【F:configs/llm_router.yaml†L100-L108】 |
| script_draft | 1 章あたり 1,600 words (CH05 は 900) 目標 → ~1.8k tokens 入力 | 章本文 1.6k–2.0k tokens | **3.6k**/章 × 7 章 ≒ **25k** | 7 | heavy_reasoning → **azure_gpt5_mini** (fallback: or_deepseek_r1) | 章ごと生成で長文。thinking/長尺に強いモデルが必要。【F:script_pipeline/runner.py†L1293-L1343】【F:configs/llm_router.yaml†L104-L119】 |
| script_draft_format | 段落を 800 文字 chunk に分割【F:script_pipeline/runner.py†L1376-L1404】 | 29 文字改行の整形 | ~1.0k + 0.9k = **1.9k**/chunk。1 章あたり 4 chunk 想定 → **7.6k**/章 | ~28 chunk | standard → **azure_gpt5_mini** | 正規化のみ。品質より速度重視、ただし JSON/29 字縛りで安定性重視。【F:configs/llm_router.yaml†L121-L124】 |
| script_review / quality_check | 全章連結（~12k tokens） | 問題指摘 + リライト | **12k + 3k = 15k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 長文レビュー。responses API の長尺が必要。【F:configs/llm_router.yaml†L109-L120】 |

**累計目安**（7 章想定）: ~50–60k tokens/prompt + completion。thinking トークン込みでも azure_gpt5_mini の 128k 上限内。【F:configs/llm_router.yaml†L17-L29】

### 2.2 B テキスト/TTS（audio_tts/tts）
| ステージ | 入力ボリューム | 期待出力 | 推定 Tokens | 呼び出し回数 | 推奨モデル/ tier | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| tts_annotate (危険トークン注釈) | MeCab トークン列 (3k〜4k 文字想定) + 厳格 JSON ルール | token_annotations JSON | ~3.5k + 1.0k = **4.5k** | 1 | standard → **azure_gpt5_mini** | router 経由の JSON 必須で安定性優先。【F:audio_tts/tts/llm_adapter.py†L113-L198】【F:configs/llm_router.yaml†L125-L134】 |
| llm_readings_for_candidates | リスク候補ごとに少量文脈 | 読みの JSON | **0.4k**/batch × 5 = **2k** | ~5 | standard → **azure_gpt5_mini** | 短文多数。スループット重視で standard tier。【F:audio_tts/tts/llm_adapter.py†L200-L226】【F:configs/llm_router.yaml†L125-L134】 |
| tts_segment (SRT 分割) | A テキスト全体 (~12k tokens) を 35–70 文字で分割【F:audio_tts/tts/llm_adapter.py†L26-L47】 | segments JSON | ~12k + 2k = **14k** | 1 | standard → **azure_gpt5_mini** | チャンク化のみ。速度優先。【F:configs/llm_router.yaml†L125-L138】 |
| tts_pause | segments (~2k 文字) | pause JSON | **2.5k** | 1 | standard → **azure_gpt5_mini** | 短文、安定性重視。【F:audio_tts/tts/llm_adapter.py†L36-L48】【F:configs/llm_router.yaml†L135-L142】 |
| tts_reading / B_TEXT_GEN | 機械分割された本文 (~12k tokens) | 読み付き B テキスト | **12k + 12k = 24k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 読み修正 + ポーズ挿入で長尺。品質最優先。【F:audio_tts/tts/orchestrator.py†L230-L269】【F:configs/llm_router.yaml†L139-L146】 |

**累計目安**: ~47k tokens。script と合わせても 128k 以内だが、B テキスト生成は別ジョブ実行を推奨。

### 2.3 画像文脈解析（video_pipeline）
| ステージ | 入力ボリューム | 期待出力 | 推定 Tokens | 呼び出し回数 | 推奨モデル/ tier | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| visual_persona | SRT 連結テキスト（数千文字） + Visual Bible | ペルソナテキスト (<=1200 chars) | ~6k + 1k = **7k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 役柄抽出。短尺だが hallucination 回避に reasoning。【F:video_pipeline/src/srt2images/llm_context_analyzer.py†L90-L132】【F:configs/llm_router.yaml†L147-L155】 |
| visual_section_plan | 最大 1000 セグメントを結合（目安 50k chars）【F:video_pipeline/src/srt2images/llm_context_analyzer.py†L35-L84】【F:video_pipeline/src/srt2images/llm_context_analyzer.py†L182-L220】 | section JSON | **55k** | 1 | heavy_reasoning → **azure_gpt5_mini** | 最長ステップ。長文応答と Visual Bible システム文脈が必要。【F:configs/llm_router.yaml†L147-L160】 |
| visual_prompt_refine | セクションごとの短文 (~1k) | 画像プロンプト | **1.5k** | 20–30 (セクション数) | heavy_reasoning → **or_deepseek_r1** | 中量テキストを多数呼ぶため、コスト最適化で heavy tier の二番手を推奨。【F:configs/llm_router.yaml†L69-L86】【F:configs/llm_router.yaml†L147-L160】 |
| visual_image_gen | 画像生成 API | 画像 | - | セクション数と同等 | image_gen → **gemini_2_5_flash_image** | 画像専用モデルのみ指定。UI/auto は direct/none の 1本道。【F:configs/llm_router.yaml†L31-L39】【F:configs/llm_router.yaml†L160-L163】【F:configs/image_models.yaml†L25-L41】 |
| e2e_smoke | 便宜上の環境ゲート | - | - | - | RUN_E2E_SMOKE=1 でのみ実行 | 重いテストの誤実行防止（ゲートのみ）。 |

## 3. モデル適性評価
- **azure_gpt5_mini (responses/chat)**: 128k context で reasoning/JSON/長文が安定。script・長尺レビュー・B テキスト・visual_section に最優先。【F:configs/llm_router.yaml†L17-L38】【F:configs/llm_router.yaml†L67-L87】
- **or_deepseek_r1**: reasoning 強めだが 8k 上限。短中尺の重推論（visual_prompt_refine 等）でコスパ良好。【F:configs/llm_router.yaml†L50-L57】
- **or_qwen_free / or_llama_free**: 4k 上限で cheap/standard fallback。整形や小タスクに限定。【F:configs/llm_router.yaml†L40-L66】
- **gemini_2_5_flash_image**: 画像生成専用。文章生成には使わない。【F:configs/llm_router.yaml†L31-L39】【F:configs/llm_router.yaml†L160-L163】

## 4. 改善提案（優先度順）
1) **script_draft のトークン平準化**: 章ごとの target を `len(chapters)` で均等割しているが、長章は 1.6k tokens を超えやすい。【F:script_pipeline/runner.py†L1318-L1343】 → outline 時に `chapter_word_cap` を設定し、超過時は自動で章を分割する。
2) **format ステージの chunk 圧縮**: 800 文字 chunk を 600 文字に縮め、completion 0.9 → 0.7 倍を狙う。トークンを ~25% 削減しつつ安定性向上。【F:script_pipeline/runner.py†L1376-L1404】
3) **tts_annotate の前フィルタ**: MeCab tokens から数値・記号のみのトークンを事前除外し、入力 10–15% 縮小。コストと JSON 崩れのリスクを下げる。【F:audio_tts/tts/llm_adapter.py†L113-L198】
4) **visual_section_plan の分割実行**: 1000 セグメント上限時は 50k tokens 超。600 セグメント単位で分割し、結果を統合するサブルーチンを追加して失敗率とコストを抑制。【F:video_pipeline/src/srt2images/llm_context_analyzer.py†L35-L84】【F:video_pipeline/src/srt2images/llm_context_analyzer.py†L182-L220】
5) **router で thinking_level デフォルト明示**: heavy_reasoning タスクに `thinking=high` を明記し、standard/cheap では強制 none。意図せぬ reasoning 課金を防ぐ。【F:configs/llm_router.yaml†L67-L172】
6) **ログとモニタリング**: すべての router 呼び出しで `prompt_tokens/completion_tokens` を SSOT に集約し、ステージ別コストダッシュボードを後続で実装。

## 6. 実測ログ運用メモ
- 実装済み: `packages/factory_common/llm_client.py` が呼び出し成功ごとに `workspaces/logs/llm_usage.jsonl` へ JSONL 追記。
- 環境変数: `LLM_USAGE_LOG_PATH` でログパス変更、`LLM_USAGE_LOG_DISABLE=1` でロギング停止。
- 集計: `python3 scripts/aggregate_llm_usage.py --log workspaces/logs/llm_usage.jsonl --top 20` で task/provider/model ごとの call/token 集計を確認。
- 画像生成の経路は単一: `nanobanana=direct`（ImageClient + Gemini 2.5 flash image）か `none`（スキップ）。legacy router/cli/mcp は廃止し、Gemini には aspect_ratio を送らない設定（capabilities supports_aspect_ratio=false）。
- 残課題: 長尺 SRT を使った `visual_section_plan`（600 セグ分割）のスモークを実施し、トークン/セクション品質を目視確認する。

## 5. 実行順序（着手ガイド）
1. router 既定値に thinking_level/clamp を追加し、config へ `max_output_tokens` を記載。
2. script_draft と format ステージで章/段落の上限を再計算し、トークン平準化を適用。
3. tts_annotate の入力フィルタと visual_section_plan の分割呼び出しを実装。
4. SSOT へのトークン実測ログ集計を開始し、推定値との差分を補正。
