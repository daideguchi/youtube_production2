# OPS_SCRIPT_INPUT_CONTRACT — 入力契約（L1/L2/L3）でコンテキスト汚染を防ぐ（SSOT）

目的:
- 企画CSV（Planning SoT）の「混線/汚染」が、台本生成や品質判定を誤誘導する事故を止める。
- どのAIエージェントでも同じ入力を参照できるようにし、品質の再現性とコスト効率を上げる。

関連（正本）:
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- Lint（機械・低コスト）: `python scripts/ops/planning_lint.py --channel CHxx`
- 台本アーキテクチャ: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`
- Aテキスト品質ゲート: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`

---

## 1) 入力の階層（L1/L2/L3）

### L1（必須・最優先 / 正本入力）
- `タイトル`（企画テーマの核）
- persona / channel_prompt（チャンネル固有の狙い・トーン）
- SSOTパターン（骨格・字数配分）: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml`
- 全チャンネル共通ルール: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

L1は、生成/判定/修正の全工程で「ズレてはいけない」基準。

### L2（補助ヒント / 使えるときだけ使う）
- Planning CSV の補助メタ（例: `企画意図`, `ターゲット層`, `具体的な内容（話の構成案）`, `内容（企画要約）`, `悩みタグ`, `キーコンセプト` など）

L2は便利だが、汚染されやすい。
**矛盾・混線が見えたら、L2は捨ててL1で書く**（これが最も安全で安い）。

### L3（混入禁止 / 参照しない）
- 旧フロー由来の「冒頭サンプル」「台本本文」など、コピペ用の自由文
- 生成途中の断片や、人間向けの作業メモ

L3は、人間には役立つことがあっても、AI入力に入れると高確率で汚染源になる。

---

## 2) 汚染の代表例と対策（確定）

### 2.1 【タグ】不一致（タイトル vs 内容（企画要約））
症状:
- タイトル先頭の `【...】` と、`内容（企画要約）` 先頭の `【...】` が一致しない。

例:
- タイトル: `【因果応報】...`
- 企画要約: `【老いの不安】...`

このケースは「行が混線している」可能性が高いので、**L2をそのまま使わない**。

確定対策:
- パイプラインは `tag_mismatch` を検出したら、テーマを強く縛るL2を自動で落とす（例: `content_summary`, `content_notes`, `悩みタグ`, `キーコンセプト`, `ベネフィット` 等）。
- その上で L1（タイトル+SSOTパターン+channel_prompt）で生成/修正/判定を行う。

注:
- これは「ガチガチな検査」ではなく、誤誘導を止める最低限の安全弁。
- 修正は CSV 側で行うのが本筋。まずは lint で見える化する。

### 2.2 【タグ】片側だけ（内容（企画要約）だけが `【...】` を持つ）
症状:
- タイトル先頭に `【...】` が無いのに、`内容（企画要約）` だけが `【...】` で始まる。

このケースは「タイトル側で検算できない」ため、Planning 行が混線している可能性が上がる。
（特に、内容（企画要約）が別エピソードの要約に差し替わっている事故が起きやすい。）

確定対策:
- パイプラインは `no_title_tag` として扱い、テーマを強く縛るL2を自動で落とす（`concept_intent`, `content_summary`, `key_concept` など）。
- L1（タイトル+SSOTパターン+channel_prompt）で生成/修正/判定を続行する。

---

## 3) 運用（最短で迷子を止める）

1) まず lint:
- `python scripts/ops/planning_lint.py --channel CHxx`

2) `tag_mismatch` が多い場合:
- CSV修正（正しい行に戻す）を優先
- 直せない/すぐ直せないなら、パイプラインは L2 を落として L1 で続行（台本の品質を守る）

3) 台本は最終的に `script_validation`（LLM Judge）で内容合否を取る:
- `python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
