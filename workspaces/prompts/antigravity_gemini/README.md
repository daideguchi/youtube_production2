# antigravity Gemini 用プロンプト置き場

目的:
- Fireworks が停止した前提で、台本本文 Aテキストを Gemini に生成させるための指示書を保存する。
- このディレクトリ内のファイルは、台本そのものではなくプロンプトのみ。

## 使い方

1. `workspaces/prompts/antigravity_gemini/MASTER_PROMPT.md` を Gemini に貼る
2. 対象チャンネルと動画の個別プロンプトを続けて貼る
3. Gemini の出力が本文のみになっていることを確認し、台本に反映する

注意:
- Gemini が前置きやチェックリストを出したら、その出力は使わない。本文のみで出し直す。
- 本文に スクリプト、コード、コマンド、JSON、YAML、設定 が混入したら不合格。
- 本文に URL、脚注、箇条書き、見出し行、丸括弧が混入したら不合格。
- ポーズは `---` のみ。`---` 以外の区切りは不合格。

## CH06 対応状況

作成済み:
- `workspaces/prompts/antigravity_gemini/CH06/CH06_035_PROMPT.md`
- `workspaces/prompts/antigravity_gemini/CH06/CH06_036_PROMPT.md`
- `workspaces/prompts/antigravity_gemini/CH06/CH06_037_PROMPT.md`
- `workspaces/prompts/antigravity_gemini/CH06/CH06_038_PROMPT.md`
- `workspaces/prompts/antigravity_gemini/CH06/CH06_039_PROMPT.md`
- `workspaces/prompts/antigravity_gemini/CH06/CH06_040_PROMPT.md`

補足:
- 現在の `workspaces/scripts/CH06/` には 041 以降のディレクトリが存在しないため、対象は 035〜040 の 6 本。
- CH06 は派生プロンプト間で末尾指示が揺れているが、個別プロンプトではチャンネル SoT の方針に合わせ、最後は疑問符で締めない。
