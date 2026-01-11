# CHxx — 台本執筆ガイド（チャンネル別）【テンプレ】

このファイルは **チャンネル別ガイドを追加するためのテンプレ**です。  
既存の `prompts/guides/scriptwriting/channels/CH01.md` などを優先して参照し、必要なときだけ本テンプレを複製して使ってください。

---

## 0) TODO（このテンプレを使うとき）

- [ ] `CHxx` を実チャンネルID（例: `CH22`）に置換
- [ ] チャンネル名を記入
- [ ] `configs/sources.yaml` から `target_chars_min/max` と `web_search_policy` を転記
- [ ] SoT のファイルパスが存在するか確認（存在しない場合は「（なし）」と明記）
- [ ] `script_prompt.txt` と `channel_info.json:script_prompt` が一致しているか確認（不一致なら修正）

---

## 1) チャンネル情報

- チャンネルID: `CHxx`
- チャンネル名: （TODO）

## 2) チャンネル既定値（SSOT）

- 目標文字数: （TODO）〜（TODO）  
  参照: `configs/sources.yaml: channels.CHxx.target_chars_min/max`
- Web検索ポリシー: （TODO）  
  参照: `configs/sources.yaml: channels.CHxx.web_search_policy`

## 3) 参照する正本（SoT）

- チャンネル台本プロンプト: `packages/script_pipeline/channels/CHxx-<チャンネル名>/script_prompt.txt`
- チャンネル情報: `packages/script_pipeline/channels/CHxx-<チャンネル名>/channel_info.json`
- Persona: `workspaces/planning/personas/CHxx_PERSONA.md`
- 企画CSV: `workspaces/planning/channels/CHxx.csv`
- チャンネル方針YAML: （あれば）`packages/script_pipeline/prompts/channels/CHxx.yaml`

## 4) 守るべき共通ルール（SSOT）

- 共通ガイド: `prompts/guides/scriptwriting/SCRIPT_WRITING_GUIDE_COMMON.md`
- 入力契約: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- Aテキスト禁則: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

## 5) 執筆開始の最短手順

1. 上の SoT を開いて読む（特に `script_prompt.txt` と Persona）。
2. 共通ガイドの `## 2) 必須インプット` を埋め、不足があれば質問する。
3. 共通ガイドに従って Aテキスト本文だけを出力する。

