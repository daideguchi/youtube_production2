# OPS_SCRIPT_GENERATION_ARCHITECTURE — 高品質Aテキスト大量生産の設計（SSOT）

目的:
- 「字数だけ合格」「雰囲気で水増し」「作り話感」「タイトルとズレ」などの事故を構造で潰す。
- LLMを“何度もリトライして当てる”のではなく、最初から狙いどおりに出る確率を上げ、必要なときだけ最小の修正を行う。

前提:
- 台本は全て AIナレーション用の Aテキスト。
- ポーズ記号は `---` のみ（1行単独）。それ以外の区切りは使わない。
- **Aテキスト本文（`content/chapters/*.md` / `content/assembled*.md`）の生成・修正は常に LLM API（原則 Fireworks）で行う**。
  - Codex exec は本文を書かない（読み取り/判定/提案はOK。ただし本文へ混入させない）。
- ルール正本: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`
- 台本量産ロジック（単一SSOT）: `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`（本書はアーキテクチャ詳細）

---

## 1) なぜ壊れるか（根本原因）

LLMに「自由に長文を書かせる」と、ほぼ必ず以下が起きる:
- 分量合わせの冗長・繰り返し・抽象語の連打（内容が増えない）
- 不自然な具体例の連打（年齢/職業/台詞を作り込み、最後を「〜そうです」で締める等）
- タイトル/サムネ訴求からの逸脱（別テーマに寄り道して戻れない）
- TTS事故（`「」`/`（）` の多用、区切り記号乱用、箇条書き混入）

→ 解決は「プロンプトを長くする」ではなく、骨格と分量を先に固定して、LLMの自由度を絞ること。

---

## 2) 解決アーキテクチャ（結論）

本リポジトリの高品質生成は、次の3層で固定する。

### 2.1 SSOTパターン（骨格固定）
- 正本: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml`
- チャンネルとタイトルから「構成パターン」を選び、セクション構成と字数配分を決定する。
- ここは決定論（コード）で作る。LLMに設計させない。
- CH07など「逸話必須」パターンでは、plan に `core_episode.safe_retelling` を含め、台本の中心エピソードは **その1件だけ**を中心に深掘りする（別の概念や逸話へ分散させない）。

### 2.2 単発生成（執筆）
- 入力: 企画CSV（タイトル/タグ/サムネ文言）、persona、channel_prompt（Aテキスト用は衝突要素を落とした `a_text_channel_prompt` に派生）、全体ルール、パターンプラン
- 出力: `assembled.md`（通常の下流互換）
  - 人手編集（UI）が入る場合は `assembled_human.md` を作り、以後はそれを正本として `assembled.md` に同期する
- 目標: 1回で狙いどおりに書ける確率を上げ、後段の修正コストを減らす。

### 2.2.1（推奨）セクション分割→組み上げ（長尺の安定化）
長尺は「一撃で書く」より、**決定論×推論**で分割統治したほうが安定する。

ベストプラクティス（コストは増えるが品質が安定）:
1) SSOTパターンから決定論プラン（セクションと字数配分）を作る  
2) セクションごとに執筆（局所制約: 主題逸脱/水増し/禁則を抑える）  
   - 決定論バリデーションでNGなら **そのセクションだけ**再生成（最大N回）  
   - セクション草稿内では `---` を禁止（区切りは組み上げ工程でのみ付与）  
3) 推論で組み上げ（繋ぎ・一貫性・字数レンジ・反復削除）  
   - 組み上げ後も決定論バリデーションでNGなら、必要に応じて **組み上げのみ**再試行（最大M回）  
4) 必要なら `script_validation`（Judge/Fix）で収束させる（内容品質の最終ゲート）  

実装（ops）:
- `python scripts/ops/a_text_section_compose.py --channel CH07 --video 009`（dry-runで候補生成）
- `python scripts/ops/a_text_section_compose.py --channel CH07 --video 009 --apply --run-validation`（正本に反映→品質ゲート）
  - セクション再生成/組み上げ再試行はフラグで調整可:
    - `--section-max-tries N`（default: 3）
    - `--assemble-max-tries M`（default: 1）

補助（決定論lint）:
- Planning汚染検知: `python scripts/ops/planning_lint.py --channel CH07 --write-latest`
- Aテキスト機械lint: `python scripts/ops/a_text_lint.py --channel CH07 --video 009 --write-latest`

### 2.2.2（超長尺）章設計（plan）→章ごと執筆→決定論アセンブル（Marathon）
2〜3時間級は「全文をLLMに渡して品質判定/修正」すると、コンテキスト超過・コスト過大・部分改変事故で破綻しやすい。  
そのため “全文LLM” を避け、**章単位で収束**させる Marathon を正本運用にする（詳細: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`）。

- 実装（ops）: `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 [--block-template ...] [--apply]`
- フロー（重要順）:
  1) plan（JSON）: 章数/ブロック/各章の goal/must/avoid を固定（迷子防止のSSOT）
  2) chapter: plan を入力として章本文を1つずつ生成（NGなら **その章だけ** 再生成）
  3) quality-gate（推論）: ブロック単位Judge（要約＋抜粋）→ 問題章だけ差し替え（全文LLM禁止）
  4) assemble: ブロック境界にだけ `---` を挿入し、章本文を決定論で結合（章本文内に `---` は禁止）
  5) validate: `validate_a_text` + `a_text_lint` で機械禁則/反復を検査
  6) length-balance（必要時）: 超過したら長い章だけ短縮（全文LLM禁止）
- ブロック雛形（章の箱）:
  - 正本: `configs/longform_block_templates.json`
  - 指定: `--block-template`（CH別固定は `channel_overrides`）

### 2.2.3 章AIに渡す「書き方の指示パック」（迷子防止の核）
章ごとの執筆AIに “細切れ情報を全部投げる” と、重要情報が埋もれてズレ/反復が起きる。  
そこで、先に plan で整理し、各章には **必要十分な指示だけ** を「パック」として渡す。

章AIへの入力（必須）:
- 企画タイトル（契約）
- 章番号/全章数、所属ブロック（章の役割）
- `goal`（この章で増やす理解は1つだけ）
- `must_include`（最大3） / `avoid`（最大3）
- 章の字数目安（char_budget）と記号上限（「」/（））
- コアメッセージ（全章でブレない）
- persona要点 / channel指針要点（長文化させず要点のみ）
- 直前章末尾（~320字。文脈だけ。コピー禁止）

章AIへの制約（必須）:
- 本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタ禁止）
- `---` を章本文に入れない（ブロック境界にだけ入れる）
- 途中章で「最後に/まとめると/結論/挨拶」などの締めをしない
- `「」`/`『』` と `（）`/`()` は原則0（必要時だけ緩和）
- “字数合わせの言い換え”禁止: 各段落に「新しい理解」を最低1つ入れる（具体/見立て/手順/落とし穴）

### 2.3 推論Judge→必要最小修正（品質固定）
- SSOT: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 「内容」の合否は推論モデルが判断する（機械判定ではない）。
- NGのときだけ、Fixerが最小の加除修正で通す。
- リトライ回数を増やさない。設計と入力の質で勝つ。

### 2.4 LLM実行ルーティング（Codex exec / LLM API）— SSOT

狙い:
- **本文品質（自然な日本語）を守る**: Aテキスト本文は LLM API で統一し、Codex の言い回し混入を構造でゼロにする。
- **コスト最適化**: Codex（`xhigh`）を「非本文タスク」に最大限活用し、使えないときは即 API に落ちる。
- **恒久依存を避ける**: Codex を完全にOFFにしても完走できる（API-only ルートを常時確保）。

定義:
- **Aテキスト本文**: 視聴者に読み上げる本文（`assembled.md` / `chapters/*.md`）。
- **非本文（Aテキスト以外）**: 企画分析、調査メモ、アウトライン、品質判定レポート、JSON、字幕/画像/サムネ生成プロンプトなど。

固定ルール（壊さないための境界）:
1) **本文を書く/直すのは LLM API だけ**
   - 対象: 本文生成・本文リライト・本文短縮/追記・最終整形・意味整合Fix など、本文ファイルを書き換える可能性がある task。
   - Codex exec に渡してはいけない（本文に Codex の言い回しが混入しうるため）。
2) **Codex exec は「非本文」タスクのみ（codex-first）**
   - 目的: `xhigh` 推論での分析/構造化/判定/抽出を、追加のAPIコストを増やさずに回す。
   - 失敗/無効化/タイムアウト時は LLM API にフォールバックして続行する（パイプライン停止を避ける）。
   - 本文に影響する入力（outline/brief等）を作る場合は、出力を **構造化（JSON/箇条書き/短文）** に寄せ、長い散文を生成しない（スタイル汚染を避ける）。
3) **API-only ルートを常に成立させる（Codexはオプション）**
   - 事故/レート制限/契約変更に備え、Codexを完全にOFFにしても量産が止まらないことを要件とする。

運用スイッチ（迷わないための最小セット）:
- 推奨: **exec-slot** で切替（モデル名/環境変数の直書きを増やさない）
  - Codex exec を優先: `LLM_EXEC_SLOT=1`
  - Codex exec を無効化: `LLM_EXEC_SLOT=2`
- Codex exec の既定設定は `configs/codex_exec.yaml`（個別環境は `configs/codex_exec.local.yaml`）で管理する（SSOTを汚さない）。
- 緊急デバッグのみ（通常運用ではロックダウンで停止）:
  - `YTM_CODEX_EXEC_DISABLE=1` / `YTM_CODEX_EXEC_ENABLED=1|0`
  - `YTM_CODEX_EXEC_PROFILE`（default: `claude-code`） / `YTM_CODEX_EXEC_MODEL`（任意）
  - 使う場合は `YTM_EMERGENCY_OVERRIDE=1` を同時にセットして「この実行だけ」例外扱いにする

### 2.5 本文モデルの「1スイッチ切替」設計（DeepSeek ⇄ Mistral ⇄ GLM-4.7）

要件:
- 既定は **DeepSeek v3.2 exp（thinking ON）**（コストと品質のバランス）。
- Mistral / GLM-4.7 などに **スイッチ1つ**で切替できる（検証しやすく、混乱しない）。
- モデル名をコード/プロンプトに直書きしない（設定に集約し、差分が追える）。

SSOT配置（正本）:
- 数字スロット（運用の主レバー）: `configs/llm_model_slots.yaml`（個別上書き: `configs/llm_model_slots.local.yaml`）
- タスク別のモデル指定: `configs/llm_task_overrides.yaml`（`script_*` の override）
- モデルID/プロバイダ登録（SSOT）:
  - `configs/llm_router.yaml`（provider/model 定義）
  - `configs/llm_model_codes.yaml`（運用コード→model_key 解決）
  - 注: `configs/llm.yml` は legacy（互換/テスト用）であり、通常運用のルーティングSSOTではない

設計方針（“1スイッチ”を崩さない）:
- **Aテキスト本文に関わる task は、同一の「本文モデルプロファイル」を参照する**（タスクごとにモデルを散らさない）。
  - 対象例: `script_chapter_draft`, `script_chapter_review`, `script_a_text_quality_fix`,
    `script_a_text_quality_shrink`, `script_a_text_quality_extend`, `script_a_text_final_polish`,
    `script_semantic_alignment_fix` など（本文を書き換える可能性があるもの）。
- **切替レバーは1つに統一**し、複数の方式を併用しない（運用の混乱防止）。
  - 推奨（迷わない/壊さない）: **数字スロット** `LLM_MODEL_SLOT`（または入口CLIの `--llm-slot`）で **この実行だけ**切替する。
    - repoのYAMLを触らずに比較できる（ロールバック事故を避ける）。モデル名の書き換えもしない。
    - 入口（例）:
      - `python3 scripts/ops/script_runbook.py … --llm-slot 4`
      - `python -m script_pipeline.cli … --llm-slot 2`
    - 互換（暫定）: `--llm-model 2` のように **数字だけ**渡した場合も slot として解釈する。
  - `LLM_FORCE_MODELS` / `--llm-model fw-g-1` のような **明示モデル指定は緊急デバッグ用途のみ**（運用では増やさない）。
  - 既定の更新（恒久切替）が必要な場合は、`configs/llm_model_slots.yaml` の slot 定義を更新する（個別環境の差分は `configs/llm_model_slots.local.yaml`）。

モデルプロファイルの契約（Aテキスト本文用）:
- **thinking 必須**（内容生成/修正は推論品質が支配的）。
- chain-of-thought を本文に混ぜない（“最終出力のみ”を抽出できる設定を前提にする）。
- 最大出力が大きいこと（長文でも `finish_reason=length` でループしない）。

モデル方針（本文/台本）:
- **既定は slot 0**（`configs/llm_model_slots.yaml`）。
  - `script_*` は slot の `script_tiers` に従う（現行: Fireworks `script-main-1` = DeepSeek v3.2 exp + thinking）。
  - “モデル名を書き換えない” ため、運用の切替は `--llm-slot <N>` / `LLM_MODEL_SLOT=<N>` だけで行う。
- **Fireworks（text/台本） は通常運用で有効**（台本の既定が Fireworks / DeepSeek v3.2 exp のため）。
  - 例外（デバッグのみ）: `YTM_EMERGENCY_OVERRIDE=1 YTM_DISABLE_FIREWORKS_TEXT=1`（この実行だけ Fireworks を止める）
  - `script_*` は API 停止時に **即停止**（THINK フォールバックしない）
- Fireworks script keyring（`FIREWORKS_SCRIPT*` / `FIREWORKS_SCRIPT_KEYS_*`）でキーを管理する（複数キーは自動ローテーション + 排他lease）。

観測（比較で迷わない）:
- 1本ごとの provider/model は `workspaces/scripts/{CH}/{NNN}/status.json: stages.*.details.llm_calls` に残す。
- トークン/回数は `workspaces/logs/llm_usage.jsonl`（`routing_key=CHxx-NNN`）で集計する（人間が読める証跡）。

---

## 3) 運用フロー（最短で安定）

1. タイトル/サムネ/企画メモを読む（Planning SoT）
2. パターン選択（`OPS_SCRIPT_PATTERNS.yaml`）
3. パターンプランに沿ってAテキスト生成（1回）
4. ハード禁則検査（URL/箇条書き/区切り/字数など）
   - 既定では「字数だけNG」を自動で水増し/圧縮して通す運用はしない（止める）。
   - 緊急時のみ `SCRIPT_VALIDATION_AUTO_LENGTH_FIX=1` で `length_too_long` を shrink で救済できる（危険・既定OFF）。
5. LLM Judgeで「内容」合否（流れ・水増し・作り話感・整合）
6. failならFixerで最小修正→再Judge（ここで止める）
7. OK台本のみ音声（VOICEVOX等）へ進む

---

## 4) 実装の責務分離（壊さないための境界）

- SSOT（人が読む正本）:
  - `OPS_A_TEXT_GLOBAL_RULES.md`（全体ルール）
  - `OPS_SCRIPT_PATTERNS.yaml`（骨格・字数配分）
  - `packages/script_pipeline/channels/*/script_prompt.txt`（チャンネル固有の口調/禁則/必須要素）
- コード（自動で守らせる）:
  - パターン選択（チャンネル/タイトル→pattern_id）
  - 最終入力の合成（persona + a_text_channel_prompt + SSOT）
  - 生成/判定/修正の呼び出しと証跡保存

---

## 5) 注意（やってはいけない）

- 「回数を増やせば当たる」前提の運用（コスト増・品質ばらつき増）
- 例の大量投入で分量を埋める（深く狭くの破壊）
- 複数の概念や逸話を並べて“広く”見せる（芯が薄くなり、視聴者の理解が増えない）
- 研究/統計/機関名を捏造して説得力を作る
- `---` を機械的に等間隔で入れる（文脈ベースのみ）
- 本文内容と無関係な“言い回しの好み”だけでパイプラインを停止させる（必要なら「校正」で直す）

---

## 6) “ベスト”にするための残タスク（超長尺の仕上げ）
現状の設計でも長尺は作れるが、2〜3時間級で反復/微妙なズレをさらに減らすには以下が必要。

現状（実装済み）:
- Marathon v1.1 で `content/analysis/longform/memory.json` / `chapter_summaries.json` を生成し、章プロンプトに Memory を投入する（既定ON、必要なら `--no-memory`）。

実装済み（Marathon v1.2）:
- ブロック単位で “要約＋抜粋” による Judge を行い、NG時は「問題章番号」を返す（全文LLM禁止）
- Fix は問題章だけを差し替える（全文Fix禁止）＋差し替え履歴/判定結果を `content/analysis/longform/quality_gate/` に残す（diff/再現性）
  - 実装: `scripts/ops/a_text_marathon_compose.py`
