# PROMPTS_INDEX — プロンプト配置の正本一覧（自動生成）

Generated: `2025-12-27T23:31:46.673740Z` by `scripts/ops/prompts_inventory.py`

原則:
- プロンプトの正本は `packages/**` 側（複製・同期しない）
- root `prompts/` は索引/ハブ（このファイル含む）

## Script pipeline — 共通プロンプト（.txt）

- `packages/script_pipeline/prompts/a_text_final_polish_prompt.txt`
- `packages/script_pipeline/prompts/a_text_quality_expand_prompt.txt`
- `packages/script_pipeline/prompts/a_text_quality_extend_prompt.txt`
- `packages/script_pipeline/prompts/a_text_quality_fix_prompt.txt`
- `packages/script_pipeline/prompts/a_text_quality_judge_prompt.txt`
- `packages/script_pipeline/prompts/a_text_quality_shrink_prompt.txt`
- `packages/script_pipeline/prompts/a_text_rebuild_draft_prompt.txt`
- `packages/script_pipeline/prompts/a_text_rebuild_plan_prompt.txt`
- `packages/script_pipeline/prompts/a_text_seed_prompt.txt`
- `packages/script_pipeline/prompts/chapter_brief_prompt.txt`
- `packages/script_pipeline/prompts/chapter_enhancement_prompt.txt`
- `packages/script_pipeline/prompts/chapter_prompt.txt`
- `packages/script_pipeline/prompts/chapter_review_prompt.txt`
- `packages/script_pipeline/prompts/consistency_prompt.txt`
- `packages/script_pipeline/prompts/cta_prompt.txt`
- `packages/script_pipeline/prompts/init.txt`
- `packages/script_pipeline/prompts/llm_polish_template.txt`
- `packages/script_pipeline/prompts/master_plan_prompt.txt`
- `packages/script_pipeline/prompts/orchestrator_prompt.txt`
- `packages/script_pipeline/prompts/outline_prompt.txt`
- `packages/script_pipeline/prompts/phase2_audio_prompt.txt`
- `packages/script_pipeline/prompts/quality_review_prompt.txt`
- `packages/script_pipeline/prompts/research_prompt.txt`
- `packages/script_pipeline/prompts/semantic_alignment_check_prompt.txt`
- `packages/script_pipeline/prompts/semantic_alignment_fix_minor_prompt.txt`
- `packages/script_pipeline/prompts/semantic_alignment_fix_prompt.txt`
- `packages/script_pipeline/prompts/tts_reading_prompt.txt`
- `packages/script_pipeline/prompts/youtube_description_prompt.txt`

- 件数: 28

## Script pipeline — テンプレ（templates/*.txt）

- `packages/script_pipeline/prompts/templates/chapter_draft_prompt.txt`
- `packages/script_pipeline/prompts/templates/chapter_prompt.txt`
- `packages/script_pipeline/prompts/templates/cta_prompt.txt`
- `packages/script_pipeline/prompts/templates/enhancement_prompt.txt`
- `packages/script_pipeline/prompts/templates/intro_prompt.txt`
- `packages/script_pipeline/prompts/templates/orchestrator_scan_prompt.txt`
- `packages/script_pipeline/prompts/templates/outline_prompt.txt`
- `packages/script_pipeline/prompts/templates/outro_prompt.txt`
- `packages/script_pipeline/prompts/templates/quality_review_prompt.txt`
- `packages/script_pipeline/prompts/templates/research_prompt.txt`
- `packages/script_pipeline/prompts/templates/system_base.txt`

- 件数: 11

## Script pipeline — チャンネル方針（prompts/channels/*.yaml）

- `packages/script_pipeline/prompts/channels/CH01.yaml`
- `packages/script_pipeline/prompts/channels/CH02.yaml`
- `packages/script_pipeline/prompts/channels/CH03.yaml`
- `packages/script_pipeline/prompts/channels/CH04.yaml`
- `packages/script_pipeline/prompts/channels/CH05.yaml`
- `packages/script_pipeline/prompts/channels/CH06.yaml`

- 件数: 6

## Script pipeline — チャンネル固有（channels/**/script_prompt.txt）

- `packages/script_pipeline/channels/CH01-人生の道標/script_prompt.txt`
- `packages/script_pipeline/channels/CH02-静寂の哲学/script_prompt.txt`
- `packages/script_pipeline/channels/CH03-【シニアの健康】朗読図書館/script_prompt.txt`
- `packages/script_pipeline/channels/CH04-隠れ書庫アカシック/script_prompt.txt`
- `packages/script_pipeline/channels/CH05-シニア恋愛/script_prompt.txt`
- `packages/script_pipeline/channels/CH06-都市伝説のダーク図書館/script_prompt.txt`
- `packages/script_pipeline/channels/CH07-仏教の心【全てうまくいく】/script_prompt.txt`
- `packages/script_pipeline/channels/CH08-隠れ書庫アカシック/script_prompt.txt`
- `packages/script_pipeline/channels/CH09-不動の指針/script_prompt.txt`
- `packages/script_pipeline/channels/CH10-人生を変える偉人思考【今日から実装】/script_prompt.txt`
- `packages/script_pipeline/channels/CH11-ブッダの法話/script_prompt.txt`
- `packages/script_pipeline/channels/CH12-ブッダの黄昏夜話/script_prompt.txt`
- `packages/script_pipeline/channels/CH13-ブッダの禅処方箋/script_prompt.txt`
- `packages/script_pipeline/channels/CH14-ブッダの執着解除/script_prompt.txt`
- `packages/script_pipeline/channels/CH15-ブッダの心胆鍛錬/script_prompt.txt`
- `packages/script_pipeline/channels/CH16-ブッダの老後整え方/script_prompt.txt`
- `packages/script_pipeline/channels/CH17-安らぎ仏教夜話/script_prompt.txt`
- `packages/script_pipeline/channels/CH18-ゆったり仏教伝説紀行/script_prompt.txt`
- `packages/script_pipeline/channels/CH19-眠れる昔ばなし朗読館/script_prompt.txt`
- `packages/script_pipeline/channels/CH20-ゆったり雑学ナイトラジオ/script_prompt.txt`
- `packages/script_pipeline/channels/CH21-夢見るゆる旅案内/script_prompt.txt`
- `packages/script_pipeline/channels/CH22-老後の友人関係ラボ/script_prompt.txt`
- `packages/script_pipeline/channels/CH23-熟年夫婦の現実ノート/script_prompt.txt`
- `packages/script_pipeline/channels/CH24-叡智の扉/script_prompt.txt`
- `packages/script_pipeline/channels/CH25-寝落ち偉人講話【眠りながら学ぶ】/script_prompt.txt`
- `packages/script_pipeline/channels/CH26-拝啓、偉人より/script_prompt.txt`

- 件数: 26

## Video — 画像生成 system prompt

- `packages/video_pipeline/system_prompt_for_image_generation.txt`

- 件数: 1

## Video — 画像プロンプトテンプレ（templates/*.txt）

- `packages/video_pipeline/templates/dark_library_calm_curiosity.txt`
- `packages/video_pipeline/templates/default.txt`
- `packages/video_pipeline/templates/japanese_visual.txt`
- `packages/video_pipeline/templates/jinsei191_masako_strict.txt`
- `packages/video_pipeline/templates/jinsei_contextual_variety.txt`
- `packages/video_pipeline/templates/jinsei_no_michishirube_buddhist.txt`
- `packages/video_pipeline/templates/jinsei_no_michishirube_contextual.txt`
- `packages/video_pipeline/templates/jinsei_standard_illustration.txt`
- `packages/video_pipeline/templates/jinsei_warm_gold_blue_strict.txt`
- `packages/video_pipeline/templates/oil_painting_akashic_fantasy.txt`
- `packages/video_pipeline/templates/oil_painting_philosophy.txt`
- `packages/video_pipeline/templates/philosophy_calm_watercolor_oil.txt`
- `packages/video_pipeline/templates/senior_health_reading.txt`
- `packages/video_pipeline/templates/senior_romance_jp_consistent.txt`
- `packages/video_pipeline/templates/senior_romance_jp_ultra_soft.txt`
- `packages/video_pipeline/templates/senior_romance_sensual.txt`
- `packages/video_pipeline/templates/senior_story_friendly_psychology.txt`
- `packages/video_pipeline/templates/watercolor_gold_blue_ch02.txt`
- `packages/video_pipeline/templates/watercolor_gold_blue_ch06_dark.txt`
- `packages/video_pipeline/templates/watercolor_gold_blue_strict.txt`

- 件数: 20
