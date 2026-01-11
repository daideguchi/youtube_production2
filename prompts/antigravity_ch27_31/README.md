# antigravity 用プロンプト（CH27-31）

目的:
- API が落ちている前提で、CH27〜CH31 の Aテキスト台本本文を antigravity に手貼りで生成させるためのプロンプトを保存する。
- 台本そのものではなく、貼り付け用のプロンプトのみ。

使い方:
1. 対象チャンネルの `MASTER_PROMPT.md` を貼る
2. 対象動画の `CHxx_yyy_PROMPT.md` を続けて貼る
3. 出力が台本本文のみになっていることを確認して取り込む

補足:
- 既存の `prompts/antigravity_gemini/` は no_touch ロック中のため、衝突を避けて別ディレクトリに出力している。
