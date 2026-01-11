# CH24 Persona & Planning Template

## 0. SoT
- Planning SoT: `workspaces/planning/channels/CH24.csv`
- 口調/禁止/タグ/サムネ/固定文言のSoT: このファイル

## 1. 共通ペルソナ（固定一文）
> 30〜70代中心。人間関係や不安で心が疲れていて、弘法大師（空海）の教えで静かに整う視点と「今夜の一手」が欲しい人。

## 2. 運用ルール（確定）
- 不安を煽らない。断罪・論破・勝利の結末に寄せない。
- 医療/法律/投資の断定的助言はしない（安全な一般論＋生活の小さな一手に留める）。
- 出典・統計・大学名・論文名・割合（％）の捏造は禁止。
- 「空海の直引用」は避け、教えは安全な一般化（〜と考える/〜と説くことが多い）で扱う。
- タイトル末尾の「【弘法大師の教え】」は必ず付ける（文末固定）。

## 3. タグセット（推奨）
| フィールド | 推奨 |
| --- | --- |
| 悩みタグ_メイン | 不安 |
| 悩みタグ_サブ | 人間関係 |
| ライフシーン | 日常 |
| キーコンセプト | 空海の教え |
| ベネフィット一言 | 心が軽くなる |
| たとえ話イメージ | 扉がひらく |
| 説明文_リード | 弘法大師（空海）の教えで、心を静かに整える時間。 |
| 説明文_この動画でわかること | ・悩みの見方が1つ変わる<br>・今夜できる一手 |

## 4. 記入ルール
1. 企画テーマ（CSVの内容/要約）は「1つの悩み」に固定し、話を散らさない。
2. 構成は「導入（違和感/恐怖の起動）→有限個回収（3/5/7）→日常の一手→祝福着地」を基本にする。
3. サムネ文言（TOP/BOTTOM）は企画ごとに固定し、勝手に短縮・言い換えしない。

## 5. ブランド（制作素材）
### 5.1 ハンドル候補
- `@eichi_tobira`（採用）

### 5.2 アイコン生成プロンプト（画像内文字なし）
```text
YouTube channel icon, 1:1, Japanese 2D illustration, calm and warm, soft watercolor texture, subtle thin lineart, no photorealism.

SUBJECT:
A simple traditional wooden door slightly open, soft light spilling out, a small gold glow (wisdom), minimal background.

NEGATIVE:
text, letters, logo, watermark, signature, speech bubble, oversaturated, lowres, clutter.
```

### 5.3 固定コメント（テンプレ）
```text
誹謗中傷・個人情報の書き込みはお控えください。
医療・法律・投資の断定的な助言は行いません。
```

## 6. サムネ基調（固定）
### 6.1 レイヤ前提
- 「弘法大師（縦）」は固定レイヤで入れる前提（このCSVには含めない）。
- 左側テキストは TOP/BOTTOM の2段（企画ごとに固定）。

### 6.2 デザイン指示（固定）
- 配置: 左35%を文字エリア。上15%/下25%は重要物なし。
- フォント: Noto Sans JP Black（太め）。
- 文字: 白文字＋黒ストローク（基本）。
- 赤ワード: 指定語のみ赤（#D61F1F）。ストロークは基本より +2px 太くする（固定ルール）。

### 6.3 30本の文言SoT（固定）
- サムネ文字（TOP/BOTTOM）30本の正本: `workspaces/thumbnails/assets/CH24/kobo_text_layer_spec_30.json`
- `workspaces/planning/channels/CH24.csv` の `サムネタイトル上/サムネタイトル下` は上記JSONと一致させる（ズレると意味整合チェックが落ちる）。
