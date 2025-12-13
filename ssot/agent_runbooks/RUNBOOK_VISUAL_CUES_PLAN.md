# RUNBOOK_VISUAL_CUES_PLAN — srt2images cues計画（THINK/API共通の型）

## 目的（DoD）
- `visual_image_cues_plan` の pending に対して、**SRTの文脈に基づく自然な画像カット割り**を作り、`sections` を返す。
- **機械的な等間隔分割は禁止**（契約上NG）。必ず文脈（話題の切替/例/比喩/感情の山/列挙の区切り）で切る。

## 出力フォーマット（厳守）
- `response_format=json_object` のため、返答は **JSONオブジェクトのみ**（コードフェンス/前置き禁止）。

### Schema（トップレベル）
```json
{
  "sections": [
    [start_segment, end_segment, summary, visual_focus, emotional_tone, persona_needed, role_tag, section_type]
  ]
}
```

### 各フィールド
- `start_segment` / `end_segment`: **1-based** の整数、**inclusive**（例: 1〜5）
- `summary`: 日本語の短いラベル（<=30文字）
- `visual_focus`: 英語の短い「何を映すか」（<=14 words, 具体/カメラ指示寄り、画面内テキスト禁止）
- `emotional_tone`: 1〜2語（例: calm, uneasy, nostalgic）
- `persona_needed`: 同一人物の顔/服/髪/小物の一貫性が必要なカットだけ `true`
- `role_tag`: `explanation|story|dialogue|list_item|metaphor|quote|hook|cta|recap|transition|viewer_address`
- `section_type`: `story|dialogue|exposition|list|analysis|instruction|context|other`

## 重要ルール（事故防止）
- **連続性**: セクションは **連続するセグメントを隙間なくカバー**（overlap/gap禁止）
- **長さ**: 目安は 8〜18秒、長くても 20秒程度。単調に同じ秒数を並べない（緩急を作る）
- **余計な人物追加禁止**: 台本にない僧侶/瞑想/正座/赤鉢巻/鎌おじさん等を「デフォルト」で入れない
- **人物の特定**: 歴史人物/固有名詞が出る場合は、その人物の属性（年代/衣装/地域）を崩さない
- **隣接差分**: 連続カットで同じ構図/距離/ポーズにしない（角度/距離/手元/前景/時間帯を変える）

## チェックリスト（提出前）
- [ ] `sections` が空ではない
- [ ] `start_segment`/`end_segment` が 1..N の範囲
- [ ] 最初が 1、最後が N（全カバー）
- [ ] 次の start が「前の end+1」（gap/overlapなし）
- [ ] 画が浮かぶ `visual_focus` になっている（抽象語だけになっていない）

