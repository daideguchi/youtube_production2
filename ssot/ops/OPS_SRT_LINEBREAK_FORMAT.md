# OPS: SRT 字幕改行整形（意図した改行をSRT本文に入れる）

## 目的
SRT生成後、字幕本文が「改行なし」のままGUI側の自動折り返しで表示されると、語や意味の塊が不自然な位置で折れて視認性が落ちる。  
そのため **SRT各キューの字幕本文に“意図した改行（\\n）”を付与**し、表示時の折り返しをこちらで制御する。

## 絶対条件（安全）
- **字幕内容（意味・語彙・語順）を編集しない**。許可される変更は **改行の追加/位置調整のみ**。
- 音声と字幕が別物になることは厳禁。  
  実装では「改行を除いた連結文字列」が元テキストと完全一致することを条件にし、逸脱した出力は採用しない。

## 入出力（I/O）
### 入力
- SRTファイル（各キューの `text` は1行でも複数行でも可）

### 出力
- SRTファイル（各キューの `text` に1〜MAX_LINES行の改行を含められる）
- 改行なし（1行）出力も許容

## パラメータ（運用で調整可能）
- `MAX_LINES`（デフォルト: 2）
- `MAX_CHARS_PER_LINE`（デフォルト: 24）
- `RETRY_LIMIT`（デフォルト: 1）

環境変数（疎結合・運用向け）:
- `SRT_LINEBREAK_ENABLED`（default: `1`）
- `SRT_LINEBREAK_MODE`（default: `heuristic` / `heuristic|llm`）
- `SRT_LINEBREAK_MAX_LINES`
- `SRT_LINEBREAK_MAX_CHARS_PER_LINE`
- `SRT_LINEBREAK_RETRY_LIMIT`

## 改行整形エンジン
### heuristic（デフォルト）
- 高速・決定論（LLM不要）
- 文脈ベース（句読点/括弧/助詞など）で改行位置を選ぶ
- **安全条件**（改行除去後の本文一致）を必ず満たす

### llm（オプション）
本文の内容は維持したまま、読みやすさを最大化する改行位置（または改行無し）を決める。

優先:
- 語/意味の塊の途中で折れない
- 意味のまとまり単位で折る
- 日本語として自然なところで折る

### 形式（パースしやすいJSON / llmモード）
入力（バッチ）:
```json
{
  "max_lines": 2,
  "max_chars_per_line": 24,
  "items": [
    {"index": 12, "text": "（改行なしの本文）"}
  ]
}
```

出力:
```json
{
  "items": [
    {"index": 12, "lines": ["1行目", "2行目"]}
  ]
}
```

## 軽量チェック / リトライ / 失敗時の扱い
### チェック（最小限）
- `lines.length <= MAX_LINES`
- 可能な場合は各行 `len(line) <= MAX_CHARS_PER_LINE`（※本文が `MAX_LINES * MAX_CHARS_PER_LINE` を超えると厳密には不可能）
- **安全チェック**: `"".join(lines) == original_text`（改行除去後の原文と完全一致）

### リトライ
上記を満たさない場合のみ、短い再依頼を `RETRY_LIMIT` 回まで行う。

### リトライでも満たさない場合
処理は止めない。該当キューは **heuristic（安全条件厳守）にフォールバック**し、それでも判断できない場合のみ **改行なし（元テキストのまま）** で通す。  
機械的な強制分割（等分/固定幅カット）は行わない。

## 組み込み方針（疎結合）
- **TTS生成パイプライン（現行）**: Strict pipeline は `packages/audio_tts/tts/strict_synthesizer.py` の `generate_srt()` で SRT を生成し、同じ関数内で `format_srt_lines()` を呼び出して改行整形する（デフォルトは `heuristic`）。
  - 実装: `packages/audio_tts/tts/llm_adapter.py` の `format_srt_lines()` / `scripts/format_srt_linebreaks.py`
- **TTS生成パイプライン（Legacy）**: 旧 orchestrator は SRT 書き出し直前に `format_srt_lines()` を呼ぶ設計だった（現在はアーカイブ）。
- **CapCut（ドラフト注入）**: SRTパース時にキュー内改行を潰さない（改行保持）。
  - 実装: `packages/video_pipeline/tools/capcut_bulk_insert.py` の `parse_srt_file()`
- **Remotion**: SRTパースで改行を保持し、字幕描画で改行を表示できるよう `whiteSpace: pre-line` を適用。

## 手動実行（後処理としての入口）
- `scripts/format_srt_linebreaks.py`
  - 例: `python3 scripts/format_srt_linebreaks.py workspaces/audio/final/CH01/216/CH01-216.srt --in-place`
