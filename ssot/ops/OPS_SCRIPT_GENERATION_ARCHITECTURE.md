# OPS_SCRIPT_GENERATION_ARCHITECTURE — 高品質Aテキスト大量生産の設計（SSOT）

目的:
- 「字数だけ合格」「雰囲気で水増し」「作り話感」「タイトルとズレ」などの事故を構造で潰す。
- LLMを“何度もリトライして当てる”のではなく、最初から狙いどおりに出る確率を上げ、必要なときだけ最小の修正を行う。

前提:
- 台本は全て AIナレーション用の Aテキスト。
- ポーズ記号は `---` のみ（1行単独）。それ以外の区切りは使わない。
- **Aテキスト本文（`content/chapters/*.md` / `content/assembled*.md`）の生成・修正は、対話型AIエージェントが「明示ルート」で行う**（サイレントfallback禁止）。
  - 既定（CLI）: `./ops claude script -- --channel CHxx --video NNN --run`（model: sonnet / fallback: Gemini 3 Flash Preview → qwen-oauth）
  - 明示API（この実行だけ）: `./ops api script <MODE> -- --channel CHxx --video NNN`（API失敗→THINK自動フォールバックは禁止）
  - Codex exec（非対話CLI）は本文を書かない（読み取り/判定/提案はOK。ただし本文へ混入させない）。
  - **Blueprint必須**: Writer CLI は `topic_research`/`script_outline`/`script_master_plan` の成果物（outline/research/master_plan）が揃うまで停止し、FULL prompt に blueprint bundle を自動追記してから執筆に入る（例外は `YTM_EMERGENCY_OVERRIDE=1` の明示実行のみ）。
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
- ここは決定論（コード）で作る（骨格/字数配分はLLMに設計させない）。
- 各企画の **細かい構成（設計図）** は Codex（対話型AIエージェント）が作る（Webサーチ→`topic_research`→`script_outline`→`script_master_plan`）。入口/表は `ssot/agent_runbooks/RUNBOOK_JOB_SCRIPT_PIPELINE.md` を正とする。
- CH07など「逸話必須」パターンでは、plan に `core_episode.safe_retelling` を含め、台本の中心エピソードは **その1件だけ**を中心に深掘りする（別の概念や逸話へ分散させない）。

### 2.2 単発生成（執筆）
- 入力: 企画CSV（タイトル/タグ/サムネ文言）、persona、channel_prompt（Aテキスト用は衝突要素を落とした `a_text_channel_prompt` に派生）、全体ルール、パターンプラン
- 出力: `assembled.md`（通常の下流互換）
  - 人手編集（UI）が入る場合は `assembled_human.md` を作り、以後はそれを正本として `assembled.md` に同期する
- 目標: 1回で狙いどおりに書ける確率を上げ、後段の修正コストを減らす。

### 2.2.1 セクション分割→組み上げ（長尺の安定化）
長尺は「一撃で書く」より、**決定論×推論**で分割統治したほうが安定する。

標準手順（コストは増えるが品質が安定）:
1) SSOTパターンから決定論プラン（セクションと字数配分）を作る  
2) セクションごとに執筆（局所制約: 主題逸脱/水増し/禁則を抑える）  
   - 決定論バリデーションでNGなら **そのセクションだけ**再生成（最大N回）  
   - セクション草稿内では `---` を禁止（区切りは組み上げ工程でのみ付与）  
3) 推論で組み上げ（繋ぎ・一貫性・字数レンジ・反復削除）  
   - 組み上げ後も決定論バリデーションでNGなら、必要に応じて **組み上げのみ**再試行（最大M回）  
4) `script_validation`（Judge/Fix）で収束させる（内容品質の最終ゲート）  

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
- 章の字数配分（char_budget）と記号上限（「」/（））
- コアメッセージ（全章でブレない）
- persona要点 / channel指針要点（長文化させず要点のみ）
- 直前章末尾（~320字。文脈だけ。コピー禁止）

章AIへの制約（必須）:
- 本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタ禁止）
- `---` を章本文に入れない（ブロック境界にだけ入れる）
- 途中章で「最後に/まとめると/結論/挨拶」などの締めをしない
- `「」`/`『』` と `（）`/`()` は既定: 0（セリフ（発話）がある章だけ最小限にする）
- “字数合わせの言い換え”禁止: 各段落に「新しい理解」を最低1つ入れる（具体/見立て/手順/落とし穴）

### 2.3 推論Judge→必要最小修正（品質固定）
- SSOT: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 「内容」の合否は推論モデルが判断する（機械判定ではない）。
- NGのときだけ、Fixerが最小の加除修正で通す。
- リトライ回数を増やさない。設計と入力の質で勝つ。

### 2.4 LLM実行ルーティング（Codex exec / LLM API）— SSOT

狙い:
- **迷わない/勝手に逸れない**: ルートは “明示” で選ぶ。サイレントフォールバックで別ルートへ逃げない。
- **THINK がデフォルト**: 外部LLM APIは勝手に呼ばない（pending を作って止める）。
- **台本（本文）は対話型AIエージェントが仕上げる**: Claude CLI（sonnet 4.5 既定）/ Gemini CLI / `qwen -p` / 明示したAPI を使う（微調整も対話型AIが担当）。

定義:
- **Aテキスト本文**: 視聴者に読み上げる本文（`assembled.md` / `chapters/*.md`）。
- **非本文（Aテキスト以外）**: 企画分析、調査メモ、アウトライン、品質判定レポート、JSON、字幕/画像/サムネ生成プロンプトなど。

固定ルール（壊さないための境界）:
1) **本文（Aテキスト）に Codex exec を混ぜない**
   - 対象: 本文生成・本文リライト・本文短縮/追記・最終整形・意味整合Fix など、本文ファイルを書き換える可能性がある task。
   - 禁止: codex exec（非対話CLI）で本文を自動生成/書換（文体ドリフト/混線防止）。
2) **自動フォールバック禁止（明示して止める）**
   - 禁止: API失敗→THINK の自動フォールバック（失敗したら停止して報告）。
   - 固定: qwen は **qwen-oauth** だけ使う（qwen で Claude/Gemini/OpenAI を使わない）。
   - 禁止: qwen の auth-type 切替（`--auth-type`）。
   - 禁止: qwen の model/provider 指定（`--model` / `-m` / `--qwen-model`）。このrepoでは物理的にブロックする（`qwen -p` 固定）。Claude を qwen 経由で呼ばない（Claudeは `./ops claude script`）。
3) **ルート選択（入口固定）**
   - 既定（THINK）: `./ops script <MODE> -- --channel CHxx --video NNN`（pending）
   - 明示API: `./ops api script <MODE> -- --channel CHxx --video NNN`
   - 明示CLI（Claude; 既定）: `./ops claude script -- --channel CHxx --video NNN --run`
   - 明示CLI（Gemini）: `./ops gemini script -- --channel CHxx --video NNN --run`
   - 明示CLI（Qwen）: `./ops qwen script -- --channel CHxx --video NNN --run`

運用スイッチ（迷わないための最小セット）:
- 切替（入口固定）: **exec-slot** で切替（モデル名/環境変数の直書きを増やさない）
  - Codex exec を優先: `LLM_EXEC_SLOT=1`
  - Codex exec を無効化: `LLM_EXEC_SLOT=2`
- Codex exec の既定設定は `configs/codex_exec.yaml`（個別環境は `configs/codex_exec.local.yaml`）で管理する（SSOTを汚さない）。
- 緊急デバッグのみ（通常運用ではロックダウンで停止）:
  - `YTM_CODEX_EXEC_DISABLE=1` / `YTM_CODEX_EXEC_ENABLED=1|0`
  - `YTM_CODEX_EXEC_PROFILE`（default: `claude-code`） / `YTM_CODEX_EXEC_MODEL`（省略可）
  - 使う場合は `YTM_EMERGENCY_OVERRIDE=1` を同時にセットして「この実行だけ」例外扱いにする

### 2.5 LLM API ルート（台本では “明示した実行だけ”）— SSOT

固定ルール:
- 台本本文（Aテキスト）の既定ルートは **CLI（Claude→Gemini→qwen）**。APIは **明示した実行だけ**使う。
  - 入口: `./ops api script <MODE> -- --channel CHxx --video NNN`
- **API失敗→停止**（THINKへ自動フォールバックしない / 勝手に別ルートへ逃げない）。
- **モデル/プロバイダの恒久変更はしない**（SSOT更新 + オーナーの明示指示が揃うまで、エージェントは変更しない）。

設定の正本:
- APIルーティング: `configs/llm_router.yaml`
- 台本 task の上書き（必要時のみ）: `configs/llm_task_overrides.yaml`
- 入口スイッチ（どこで動かすか）: `configs/llm_exec_slots.yaml`（`LLM_EXEC_SLOT`）

観測（固定）:
- 1本ごとの provider/model は `workspaces/scripts/{CH}/{NNN}/status.json: stages.*.details.llm_calls` に記録する。
- トークン/回数は `workspaces/logs/llm_usage.jsonl` に集計される。

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
- 本文内容と無関係な“言い回しの好み”だけでパイプラインを停止させる（言い回しの調整は「校正」で行う）

---

## 6) “ベスト”にするための残タスク（超長尺の仕上げ）
現状の設計でも長尺は作れるが、2〜3時間級で反復/微妙なズレをさらに減らすには以下が必要。

現状（実装済み）:
- Marathon v1.1 で `content/analysis/longform/memory.json` / `chapter_summaries.json` を生成し、章プロンプトに Memory を投入する（既定ON。無効化は `--no-memory`）。

実装済み（Marathon v1.2）:
- ブロック単位で “要約＋抜粋” による Judge を行い、NG時は「問題章番号」を返す（全文LLM禁止）
- Fix は問題章だけを差し替える（全文Fix禁止）＋差し替え履歴/判定結果を `content/analysis/longform/quality_gate/` に残す（diff/再現性）
  - 実装: `scripts/ops/a_text_marathon_compose.py`
