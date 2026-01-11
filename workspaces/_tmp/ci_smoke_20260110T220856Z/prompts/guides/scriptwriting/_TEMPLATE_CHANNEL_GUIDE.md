# CHxx — 台本執筆ガイド（チャンネル別）テンプレ

チャンネル名: （ここに記入）

## 1) チャンネル既定値（SSOT）
- 目標文字数: MIN〜MAX（`configs/sources.yaml: channels.CHxx.target_chars_min/max`）
- Web検索ポリシー: required/disabled/auto（`configs/sources.yaml: channels.CHxx.web_search_policy`）

## 2) 参照する正本（SoT）
- チャンネル台本プロンプト: `packages/script_pipeline/channels/CHxx-.../script_prompt.txt`
- チャンネル情報: `packages/script_pipeline/channels/CHxx-.../channel_info.json`
- Persona: `workspaces/planning/personas/CHxx_PERSONA.md`
- 企画CSV: `workspaces/planning/channels/CHxx.csv`
- チャンネル方針YAML: `packages/script_pipeline/prompts/channels/CHxx.yaml`（無ければ「なし」）

## 3) 守るべき共通ルール（SSOT）
- 共通ガイド: `prompts/guides/scriptwriting/SCRIPT_WRITING_GUIDE_COMMON.md`
- 入力契約: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- Aテキスト禁則: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

## 4) 執筆開始の最短手順
1. 上の SoT を開いて読む（特に `script_prompt.txt` と Persona）。
2. 共通ガイドの `## 2) 必須インプット` を埋め、不足があれば質問。
3. 共通ガイドに従って Aテキスト本文だけを出力する。

## 5) 禁止（超要約）
- 本文にURL/脚注/出典メタを書かない
- `---` 以外の区切り禁止、等間隔分割禁止
- 丸括弧・箇条書き・見出し行禁止（Aテキスト）

