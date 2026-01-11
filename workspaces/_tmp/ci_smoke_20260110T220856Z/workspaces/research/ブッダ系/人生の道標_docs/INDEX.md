# ブッダ系/人生の道標_docs — 索引（自動生成）

<!-- MANUAL START -->
## メモ（手動）
- このフォルダはCH01（人生の道標）の運用/ベンチ/学習メモ置き場（本文生成の正本はSSOT/プロンプト側）。
- 正本(SoT): `packages/script_pipeline/prompts/channels/CH01.yaml`（同期先: `packages/script_pipeline/channels/CH01-人生の道標/script_prompt.txt`）
- 構成（固定）: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml` の `ch01_historical_proof_bridge_v1`（史実で証明→日常へ橋渡し→実践）
- 重大禁止: 架空の現代人物ストーリー（名前/年齢/職業/台詞の作り込み）。  
  - 自動検出（`packages/script_pipeline/validator.py` の `ch01_fictional_person_intro`）は **行全体が** `姓名、年齢(歳/才)。` の単独行（例: `田村幸子、六十七歳。`）だけ。`ブッダが29歳のとき…` のような文中表現はOK。
- 入力生成: `scripts/ch01/generate_prompt_input.py` / QA: `scripts/ch01/check_script.py`（旧「主人公/敵役/逆転の一言」会話劇フレームは使わない）
- 外部参考（別プロジェクト）: `/Users/dd/LocalProjects/01_create/youtube事業/01_人生の道標`（旧資料も混在するので SSOT/CH01プロンプト優先）
<!-- MANUAL END -->

## このフォルダのファイル（直下）

| kind | path | refs | size | modified | items |
| --- | --- | ---: | ---: | --- | ---: |
| FILE | `ブッダ系/人生の道標_docs/187_修正履歴.md` | 0 | 128,905 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/187_深い学習記録.md` | 0 | 35,239 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/AI音声YouTube台本作成ガイド.md` | 0 | 30,654 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/ベンチマーク_逆転の一言_観察メモ.md` | 0 | 3,114 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/ベンチマーク参考` | 0 | 20,946 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/台本執筆プロンプト.md` | 0 | 4,240 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/学習整理_人生の道標.md` | 0 | 8,885 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/運用マニュアル_人生の道標台本制作.md` | 0 | 6,223 | 2025-12-26 | — |
| FILE | `ブッダ系/人生の道標_docs/高品質台本生成ワークフロー.md` | 0 | 6,159 | 2025-12-26 | — |

---

_generated_by: `python3 scripts/ops/research_genre_index.py --apply`_
