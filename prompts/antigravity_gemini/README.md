# antigravity Gemini 用プロンプト置き場

目的:
- Fireworks が停止した前提で、台本本文 Aテキストを Gemini に生成させるための指示書を保存する。
- このディレクトリ内のファイルは、台本そのものではなくプロンプトのみ。

## 使い方

基本（CH01/CH06 など）:
1. `prompts/antigravity_gemini/MASTER_PROMPT.md` を Gemini に貼る
2. 対象動画の個別プロンプト（例: `prompts/antigravity_gemini/CH06/CH06_040_PROMPT.md`）を続けて貼る
3. Gemini の出力が本文のみになっていることを確認し、`workspaces/scripts/CHxx/NNN/content/assembled.md` に保存される前提で運用する（`assembled_human.md` がある場合はそれが正本）

CH27〜CH31（チャンネル別マスターを使う）:
1. 対象チャンネルの `prompts/antigravity_gemini/CH27/MASTER_PROMPT.md`（など）を貼る
2. 対象動画の個別プロンプト（例: `prompts/antigravity_gemini/CH27/CH27_001_PROMPT.md`）を続けて貼る
3. Gemini の出力が本文のみになっていることを確認し、`workspaces/scripts/CHxx/NNN/content/assembled.md` に保存される前提で運用する（`assembled_human.md` がある場合はそれが正本）

CH27〜CH31 を連続で書かせる運用（完了済みスキップ + 文字稼ぎ封じ）:
- `prompts/antigravity_gemini/RUNBOOK_CONTINUOUS_CH27_31.md`

CH27〜CH31 autopilot（進捗JSONで自動再開 / 最大5本で停止）:
- `prompts/antigravity_gemini/RUNBOOK_AUTOPILOT_CH27_31.md`

Batch運用（Gemini Developer API Batch）:
- 個別/Full プロンプト生成: `./scripts/with_ytm_env.sh python3 scripts/ops/gemini_batch_script_prompts.py build --channel CHxx --videos NNN-NNN`
- Batch submit/fetch: `./scripts/with_ytm_env.sh python3 scripts/ops/gemini_batch_generate_scripts.py --help`

注意:
- Gemini が前置きやチェックリストを出したら、その出力は使わない。本文のみで出し直す。
- 入力が不足している場合は `[NEEDS_INPUT]` が返る（本文は書かない）。不足項目を埋めて再実行する。
- 本文に スクリプト、コード、コマンド、JSON、YAML、設定 が混入したら不合格。
- 本文に URL、脚注、箇条書き、見出し行、丸括弧が混入したら不合格。
- ポーズは `---` のみ。`---` 以外の区切りは不合格。

## 対応状況

CH01:
- `prompts/antigravity_gemini/CH01/CH01_001_PROMPT.md`

CH06:
- `prompts/antigravity_gemini/CH06/CH06_035_PROMPT.md`
- `prompts/antigravity_gemini/CH06/CH06_036_PROMPT.md`
- `prompts/antigravity_gemini/CH06/CH06_037_PROMPT.md`
- `prompts/antigravity_gemini/CH06/CH06_038_PROMPT.md`
- `prompts/antigravity_gemini/CH06/CH06_039_PROMPT.md`
- `prompts/antigravity_gemini/CH06/CH06_040_PROMPT.md`

CH27〜CH31:
- `prompts/antigravity_gemini/CH27/MASTER_PROMPT.md` + `CH27_001_PROMPT.md`〜`CH27_030_PROMPT.md`
- `prompts/antigravity_gemini/CH28/MASTER_PROMPT.md` + `CH28_001_PROMPT.md`〜`CH28_030_PROMPT.md`
- `prompts/antigravity_gemini/CH29/MASTER_PROMPT.md` + `CH29_001_PROMPT.md`〜`CH29_030_PROMPT.md`
- `prompts/antigravity_gemini/CH30/MASTER_PROMPT.md` + `CH30_001_PROMPT.md`〜`CH30_030_PROMPT.md`
- `prompts/antigravity_gemini/CH31/MASTER_PROMPT.md` + `CH31_001_PROMPT.md`〜`CH31_030_PROMPT.md`

補足:
- 現在の `workspaces/scripts/CH06/` には 041 以降のディレクトリが存在しないため、対象は 035〜040 の 6 本。
- CH06 は派生プロンプト間で末尾指示が揺れているが、個別プロンプトではチャンネル SoT の方針に合わせ、最後は疑問符で締めない。
