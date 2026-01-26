# RUNBOOK_VISUAL_CUES_PLAN — srt2images cues計画（THINK/API共通の型）

## 目的（DoD）
- `visual_image_cues_plan` の pending に対して、**SRTの文脈に基づく自然な画像カット割り**を作り、`sections` を返す。
- **機械的な等間隔分割は禁止**（契約上NG）。必ず文脈（話題の切替/例/比喩/感情の山/列挙の区切り）で切る。

## 再発防止（LLM APIで画像プロンプトを作らせない）
- `refined_prompt` は **人間/エージェントが作る**（LLM APIに丸投げしない）。抽象/象徴で埋める事故を防ぐため。
- 実装ガード: `packages/video_pipeline/src/srt2images/orchestration/pipeline.py` は、`SRT2IMAGES_DISABLE_TEXT_LLM=1` の場合に cues_plan を **manual-only** 扱いにする。
  - `visual_cues_plan.json` が無ければ pending の雛形だけ作って停止する（自動生成へ戻らない）。
- さらに明示的に止めたい場合は `SRT2IMAGES_CUES_PLAN_MANUAL_ONLY=1` を使う（チャンネル非依存）。
- ルータガード: `packages/factory_common/llm_router.py` は routing lockdown 下で `visual_image_cues_plan` を API 実行しようとすると **強制停止**する（手書き plan へ誘導）。

## データのありか（画像プロンプトの正本）
- run_dir（Video SoT）: `workspaces/video/runs/<run_id>/`
- 編集する場所（人間/エージェントが手で直す正本）: `visual_cues_plan.json` の `sections[*].refined_prompt`
- 実際に送った最終プロンプト（監査/証跡）: `image_cues.json` の `cues[*].prompt`（併記: `cues[*].refined_prompt/visual_focus/summary`）
- 参照: `srt_segments.json`（SRT決定論パース。planの前提） / `images/0001.png...`（cue index順）

## 出力フォーマット（厳守）
- `response_format=json_object` のため、返答は **JSONオブジェクトのみ**（コードフェンス/前置き禁止）。

### Schema（トップレベル）
```json
{
  "sections": [
    [start_segment, end_segment, summary, visual_focus, emotional_tone, persona_needed, role_tag, section_type, refined_prompt]
  ]
}
```

### 各フィールド
- `start_segment` / `end_segment`: **1-based** の整数、**inclusive**（例: 1〜5）
- `summary`: 日本語の短いラベル（<=30文字）
- `visual_focus`: 英語の短い「何を映すか」（<=14 words, 具体/カメラ指示寄り、画面内テキスト禁止）
- `emotional_tone`: 1〜2語（例: calm, uneasy, nostalgic）
- `refined_prompt`: 英語の短い本命プロンプト（<=220 chars, action/pose + setting/props + lighting + camera angle/distance を入れる、画面内テキスト禁止、隣接で同じ構図/同じ場所を繰り返さない）
- `persona_needed`: 同一人物の顔/服/髪/小物の一貫性が必要なカットだけ `true`
- `role_tag`: `explanation|story|dialogue|list_item|metaphor|quote|hook|cta|recap|transition|viewer_address`
- `section_type`: `story|dialogue|exposition|list|analysis|instruction|context|other`

## 重要ルール（事故防止）
- **連続性**: セクションは **連続するセグメントを隙間なくカバー**（overlap/gap禁止）
- **長さ**: 基準は「チャンネルpreset / 実行時promptで指示された秒数（base_seconds）」に従う。単調に同じ秒数を並べない（緩急を作る）
- **余計な人物追加禁止**: 台本にない僧侶/瞑想/正座/赤鉢巻/鎌おじさん等を「デフォルト」で入れない
- **人物の特定**: 歴史人物/固有名詞が出る場合は、その人物の属性（年代/衣装/地域）を崩さない
- **隣接差分**: 連続カットで同じ構図/距離/ポーズにしない（角度/距離/手元/前景/時間帯を変える）
- **schnell（短プロンプト）**: `refined_prompt` は空にしない（ここが本命差分。短くてもよいが具体にする）
- **禁止（機械プロンプト）**: キーワード辞書・固定モチーフ・固定プール等で `visual_focus` を自動決め打ちしない（例: time→pocket watch）。必ず当該セクションの文脈で具体化し、同じ象徴を連発しない。※禁止例の“具体単語”をそのままモデル向けプロンプトに列挙すると priming になり得るため、運用プロンプトでは禁止カテゴリを一般化して書く（例: cliché symbols）。
- **最重要（意味整合）**: `visual_focus` は **そのセクション内容を正確に表現**する（具体の行動/物体/状況/たとえに紐づける）。無関係な象徴で埋めない。抽象語だけで終わらせない。

## チェックリスト（提出前）
- [ ] `sections` が空ではない
- [ ] `start_segment`/`end_segment` が 1..N の範囲
- [ ] 最初が 1、最後が N（全カバー）
- [ ] 次の start が「前の end+1」（gap/overlapなし）
- [ ] 画が浮かぶ `visual_focus` になっている（抽象語だけになっていない）
- [ ] 各 `visual_focus` が当該セクションの具体に紐づき、同じ象徴/小物の連発になっていない
