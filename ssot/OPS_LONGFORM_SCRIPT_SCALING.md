# OPS_LONGFORM_SCRIPT_SCALING — 2〜3時間級の超長尺でも破綻しない台本生成設計（SSOT）

目的:
- 2〜3時間級（超長尺）のAテキストを **破綻なく・狙い通り・水増し少なく** 量産できる設計にする。
- 「リトライ回数を増やして当てる」ではなく、**構造と入力の質で最初から当たる確率を上げる**。

結論（先に）:
- **生成（章分割→決定論アセンブル）は現状でもスケール可能**（章数・目標文字数を上げれば長く作れる）。
- ただし **現行の“全文をLLMに渡す品質ゲート（Judge/Fix）”は2〜3時間級では破綻する**（コンテキスト超過/コスト過大/部分改変の事故）。
- したがって、超長尺では「全文LLM」から「**チャンク単位の判定/修正**」へ切り替える **Marathonモード**が必須。

関連（読む順）:
- `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`（現行フロー全体）
- `ssot/OPS_SCRIPT_GUIDE.md`（運用手順）
- `ssot/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`（長尺安定化の設計）
- `ssot/OPS_A_TEXT_GLOBAL_RULES.md`（機械禁則の正本）
- `ssot/OPS_A_TEXT_LLM_QUALITY_GATE.md`（Judge/Fixの正本）

---

## 0) 現行の台本生成経路（混乱を解くための整理）

### Route A（主線）: `script_pipeline`（章分割のSoT駆動）
- Stages: `packages/script_pipeline/stages.yaml`
  - `script_outline` → `chapter_brief` → `script_draft`（章ごとに生成）→ `script_review`（決定論で結合）→ `script_validation`
- 章生成は runner がアウトラインから章を列挙してループする:
  - `packages/script_pipeline/runner.py` の `script_draft` / `script_review`

**長尺に対して強い部分**
- `script_review` は LLMで全文を組み替えず、**章をそのまま結合**する（スケールする）。

**長尺に対して弱い部分**
- `quality_check` と `script_validation`（LLM Judge/Fix）が **全文（A_TEXT）をそのままプロンプトに埋め込む**ため、超長尺で破綻しやすい。
  - 例: `packages/script_pipeline/prompts/a_text_quality_judge_prompt.txt` は `<<A_TEXT>>` をそのまま要求する。

### Route B（補助）: `a_text_section_compose`（SSOTパターンでセクション分割→合成）
- `scripts/ops/a_text_section_compose.py`
- 「パターン→セクション草稿→組み上げ→script_validation」まで一気にやる補助ツール。

**長尺に対して弱い理由（致命）**
- 組み上げ時にセクション草稿を `12000` 文字に truncate しているため、超長尺では入力が欠落し、整合が崩れる。

### Route C（超長尺）: `a_text_marathon_compose`（Marathon: 章設計→章生成→決定論アセンブル）
- `scripts/ops/a_text_marathon_compose.py`
- “全文LLMで直す” をやらず、**章単位の生成/差し替え**で収束させる（全文コンテキストを避ける）。
- 生成物はデフォルトで `content/analysis/longform/` に集約（dry-run）。`--apply` で canonical を上書きする。
  - ブロック骨格（章の箱）は `configs/longform_block_templates.json` を正本にできる（`--block-template` または `channel_overrides`）。
  - canonical: `content/chapters/` + `content/assembled.md` + `content/assembled_human.md`
  - analysis: `content/analysis/longform/plan.json`, `chapters/chapter_XXX.md`, `assembled_candidate.md`, `validation__latest.json` など
- 現状の“繋ぎ”は **直前章末尾（~320字）** のみ参照（超長尺の全体メモリは Phase 1.1 で拡張予定）。

---

## 1) “2〜3時間級”で必要になる前提（要件）

### 1.1 品質要件（中身）
- 主題は増やさない（企画タイトルの痛み/問いを1つに固定）。
- 反復/水増しを構造で抑える（同義の言い換え連打、抽象語の連打、終盤のまとめ連打）。
- 長尺でもテンポが死なない（「小さな決着」が章ごとに連発されない）。
- 視聴者の理解が積み上がる（定義→例→見立て→手順→落とし穴→回収が自然に循環）。

### 1.2 工学要件（壊れない）
- 全文をLLMの入力に入れない（コンテキスト/コスト/事故を回避）。
- 生成物/ログが巨大化しても **追跡・差し戻し**できる（diff可能、入力フィンガープリント保持）。
- 失敗時は“最小範囲だけ”やり直せる（章/セグメント単位）。

---

## 2) 現状でスケールするもの（良いところ）

### 2.1 章分割生成 + 決定論アセンブル
- `script_draft` が章ごとに生成 → `script_review` が結合（LLMで全文編集しない）。
- 長尺にしても「構造的に破綻しにくい」骨格が既にある。

### 2.2 決定論バリデータ（機械禁則）
- `packages/script_pipeline/validator.py` は全文が長くても判定できる（URL/箇条書き/区切り/記号上限/字数など）。
- これは超長尺でも **安全ガードとして有効**。

### 2.3 アラインメント（企画↔台本の整合）
- `script_review` で alignment stamp を書き、下流（音声）が誤入力で走らない。
- 長尺ほど「いつの企画/どの本文で作ったか」が重要なので、ここは強い。

---

## 3) 現状の課題（2〜3時間級で破綻するポイント）

### 3.1 Quality gate が “全文LLM” 前提
現状:
- `script_validation` の LLM Judge/Fix は `<<A_TEXT>>` に全文を入れる。
- `quality_check` も `<<SCRIPT_CONTENT>>` に全文を入れる。

問題:
- 超長尺ではコンテキスト超過、または「一部しか読めない」状態になり、**判定の信頼性が落ちる**。
- Fixerが全文を再生成すると、局所修正のつもりが **全体を壊す**（チグハグ化）リスクが急増する。

### 3.2 章数を増やすと、章生成プロンプトが肥大化しコストが跳ねる
現状:
- `chapter_prompt.txt` は `<<OUTLINE_TEXT>>`（アウトライン全文）を毎章プロンプトに含める。

問題:
- 60章ならアウトライン全文を60回送ることになる（コスト/速度が厳しい）。

### 3.3 章ごとの「小さな締め」が反復しやすい
現状:
- 章プロンプトは「導入→展開→まとめ・余韻で締める」を要求する。

問題:
- 超長尺でこれを各章に適用すると、「終わりっぽい段落」が連発され、視聴者が疲れる。

### 3.4 `a_text_section_compose` は超長尺に不向き
現状:
- 組み上げプロンプトが草稿を truncate しており、超長尺では入力欠落が起きる。

問題:
- “整合の取れた編集”をする前提が崩れる（事故要因）。

---

## 4) 推奨設計: Marathonモード（超長尺専用）

### 4.1 基本方針
- **全文をLLMに渡さない**（判定も修正も）。
- “章/セグメント”を最小単位として、失敗した箇所だけ直す。
- 一貫性は「全体の要約メモ（Memory）」で担保し、本文を毎回見返させない。

### 4.2 データ構造（SoT / I/O）
既存の `content/chapters/chapter_{n}.md` を流用してよい（章=セグメント）。

追加（推奨）:
- `content/analysis/longform/plan.json`
  - 目標文字数、章数、章グルーピング（大セクション→章範囲）、各章の目的/禁止/キー概念
- `content/analysis/longform/memory.json`
  - `core_message`（1-2文）
  - `definitions`（用語の定義）
  - `covered_points`（既に言った重要点の箇条書き。繰り返し防止）
  - `no_repeat_phrases`（繰り返しやすい言い回しのブラックリスト）
- `content/analysis/longform/chapter_summaries.json`
  - 各章を1文で要約（判定/整合のための軽量表現）

### 4.3 生成フロー（決定論×推論の分割統治）
1) **大枠設計（決定論）**
   - 目標文字数（例: 60,000〜90,000）と章数（例: 48〜72）を決める。
   - 章を「大セクション（例: 6〜8ブロック）」にグルーピングし、各ブロックの目的を固定する。
2) **章ブリーフ（軽量）**
   - 各章の `goal / must_include / avoid / tone` を短いJSONで持つ（長文化禁止）。
3) **章本文生成**
   - 入力は “全文” ではなく、以下のみ:
     - 企画タイトル、章の目的、直前章の末尾（200〜400字）、Memory（800字程度）、章ブリーフ
   - 章ごとに決定論バリデーション → NGなら **その章だけ**再生成（最大N回）。
4) **アセンブル（決定論）**
   - `script_review` と同様に、章をそのまま結合。
   - `---` は大セクション境界にだけ入れる（等間隔禁止）。
5) **品質ゲート（チャンク化）**
   - 判定は全文ではなく、章要約（chapter_summaries）＋サンプル抜粋（各ブロック先頭/末尾だけ）で実施。
   - NGなら「該当章番号」を返させ、そこだけ修正する（全文Fix禁止）。

### 4.4 判定/修正の設計（重要）
やってはいけない:
- 2〜3時間の本文をまとめて Judge/Fix に投げる（破綻する）。

推奨:
- Judge: ブロック単位（例: 6〜8ブロック）で **要約＋抜粋**を評価し、問題章を特定する。
- Fix: 問題章だけを再生成/差し替え（前後200〜400字 + Memory だけ参照）。

---

## 5) 直近の実装方針（最小リスクで段階導入）

### Phase 0（今すぐ可能: 運用で回避）
- 超長尺では `quality_check` を参考程度にし、`script_validation` の LLMゲートは **無効化**して決定論lint中心に止める（暫定）。
  - env: `SCRIPT_VALIDATION_LLM_QUALITY_GATE=0`（詳細は runner 実装を参照）
- 章数/目標文字数は `status.json metadata` で上書きして実験できる（ただし手作業で事故りやすい）。

### Phase 1（小さな追加: opsツールでMarathon生成）
- `scripts/ops/a_text_marathon_compose.py`（新規）として、上記 4.3/4.4 を実装。
- 既存の `content/chapters/` を生成し、`script_review` 相当の結合を行い、`assembled_human.md` を更新する。
  - 実装上のデフォルト（運用の事故を減らすため）:
  - 章ドラフトの `「」`/`『』` と `（）`/`()` は **0個（禁止）**（強調/引用は地の文で言い換える）
    - 必要なら `--chapter-quote-max` / `--chapter-paren-max` で緩和
  - 章の字数は「章予算×(0.7〜1.6)」の範囲で判定し、最終的には全文を `target_chars_min/max` で確定チェックする（章だけで収束させない）
  - 全文が `target_chars_max` を超えた場合は `--balance-length`（デフォルトON）で **長い章だけ短縮**して収束させる（全文をLLMに渡さない）
  - `--title ... --apply` を使う場合、`status.json` の `sheet_title/expected_title/title` も上書きして下流の整合チェックを壊さない

  - 生成物（I/O; Marathon v1 現行）:
    - `content/analysis/longform/plan.json`（再開時はこれを優先的に再利用）
    - `content/analysis/longform/plan_raw_attempt_*.json`（plan JSON の不正出力を保存）
    - `content/analysis/longform/chapters/chapter_XXX.md`（章本文）
    - `content/analysis/longform/chapters/chapter_XXX__attempt_YY__invalid.md`（章の不正出力を保存）
    - `content/analysis/longform/assembled_candidate.md`（結合候補）
    - `content/analysis/longform/validation__latest.json`（全文の決定論検証レポート）
    - `content/analysis/longform/memory.json`（既出キーワード/既出must_includeのスナップショット）
    - `content/analysis/longform/chapter_summaries.json`（章ごとの1行要約/字数/必須観点）

### Phase 1.1（実装済み: v1.1）: Memory / 要約（全文LLMなし）
Marathon v1 は “全文LLM” を避けるため、基本は **plan + 直前章末尾** で整合を担保する。  
ただし 2〜3時間級で「反復」「微妙なズレ」をさらに減らすために、全文を渡さずに次を追加した:

- `content/analysis/longform/chapter_summaries.json`: 各章の1行要約/字数/必須観点を保存（監査/差し替えの足場）
- `content/analysis/longform/memory.json`: 既出キーワード/既出must_include をスナップショット化し、次章の指示パックへ投入
  - 既定: `--use-memory`（ON）/ 必要なら `--no-memory` でOFF

Phase 1.2（実装済み: v1.2）: チャンク品質ゲート（全文LLMなし）
- Marathon に「**要約＋抜粋**でブロック単位Judge → 問題章だけ差し替え」を追加した。
  - 全文をLLMへ渡さない（コンテキスト超過/部分改変事故を回避）。
  - Judgeは最大2ラウンドで収束（Judge→差し替え→Judge）。
  - 差し替え履歴/判定結果は `content/analysis/longform/quality_gate/` に保存（差し戻し可能）。
- 実装: `scripts/ops/a_text_marathon_compose.py`
  - 既定: 有効（`--no-quality-gate` で無効化）
  - 調整パラメータ（必要時のみ）:
    - `--quality-max-rounds`（default: 2）
    - `--quality-max-fix-per-block`（default: 2）
    - `--quality-max-fix-chapters-per-round`（default: 4）
    - `--quality-judge-max-tokens`（default: 1600）
  - タスク名（override可）:
    - Judge: `MARATHON_QUALITY_JUDGE_TASK`（default: `script_a_text_quality_judge`）
    - Rewrite: `MARATHON_QUALITY_REWRITE_TASK`（default: `script_a_text_quality_fix`）

### Phase 2（本命: script_pipelineにMarathonモード統合）
- `configs/sources.yaml` に “profile/length_mode” を導入し、エピソード単位で `chapter_count/target_chars` を切替可能にする。
- `script_validation` を「全文LLM」から「チャンク判定→章単位Fix」に置換/分岐する。

---

## 6) DoD（Definition of Done）: “超長尺でも壊れない”の合格条件

- 生成:
  - 目標文字数レンジを満たす（`validate_a_text` pass）
  - 禁則（URL/脚注/箇条書き/区切り/記号上限）を満たす
- 品質:
  - ブロック単位Judgeが `pass`（または must_fix が 0）
  - 反復検知（`a_text_lint`）で重大警告が閾値以下
- 運用:
  - どの章を直したか履歴が残る（analysis/longform/ 以下に差分・入力指紋）
  - rerunしても同じ入力/同じ章は無駄に再生成しない（キャッシュ/スキップ可能）
