# OPS_DIALOG_AI_SCRIPT_AUDIT — 対話AIによる台本監査（LLM API禁止）

目的:
- 台本（Aテキスト）の **企画整合（Planning SoT）** と **流れ/自然さ** を、安易な機械判定（字数だけ等）に依存せずに担保する。
- 「おかしい台本を後工程へ流さない」ことを最優先に、`redo_script` を **確実に運用**できる状態にする。
- 台本が修正された後も、同じ品質で再査定して **状態A→B→再確定** が回るように、監査の入出力を固定する。

絶対制約（この監査の前提）:
- **LLM API を呼ばない**（コスト暴発の防止）。この監査は **対話型AIエージェントが判定**する（オーナーのレビューは必須ではない）。
- 台本本文を **機械的にテコ入れして合格扱いにしない**（末尾トリム/追記挿入/重複削除/末尾補完などは禁止）。
  - 内容に触れる決定論修正は禁止（正本: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md` / `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`）。
- `redo_script` / `redo_note`（要対応の編集判断）は **この監査の出力**として扱い、整合スタンプ（`metadata.alignment`）や他の機械処理で自動上書きしない。

関連:
- Planning SoT: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- Script SoT: `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`
- Aテキスト共通禁則（機械ルール）: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

---

## 0. スコープ（対象/非対象）

対象（基本）:
- **未投稿** の動画（Planning CSV の `進捗=投稿済み/公開済み` ではない）
- かつ `workspaces/scripts/{CH}/{NNN}/status.json` が存在するもの
- 監査の主対象は **`script_validation` 済み（Aテキスト完成扱い）** の台本

非対象（監査不要）:
- Planning CSV が `進捗=投稿済み/公開済み` の行
- `status.json.metadata.published_lock=true` のもの（投稿済みロック）

注意:
- 未完成（`script_validation` 未完了）の台本は「監査で合否を決める」のではなく、まず完成させる。

---

## 1. 監査の出力（Script SoT に書くパラメータ）

Script SoT = `workspaces/scripts/{CH}/{NNN}/status.json`

### 1.1 UI が見るフラグ（既存）
- `metadata.redo_script`（bool）
  - `false`: **監査OK（台本はこのまま下流へ進めてよい）**
  - `true`: **要対応（止める）**
  - `null/未設定`: UI上は `true` 扱い（= 未確定）
- `metadata.redo_note`（str）
  - 要対応理由を短く、修正者が次の一手を打てる形で書く（例: `企画ズレ/導入が別テーマ/締めが不自然`）。

### 1.2 監査メタ（新規・更新可能）
監査の再現性を担保するため、次のメタを `metadata.dialog_ai_audit` に保存する。

スキーマ（固定）:
```json
{
  "schema": "ytm.dialog_ai_script_audit.v1",
  "audited_at": "2026-01-01T00:00:00Z",
  "audited_by": "dd-dialog-audit-01",
  "verdict": "pass|fail|grey",
  "reasons": ["planning_misalignment", "flow_break", "..."],
  "notes": "短い補足（省略可）",
  "script_hash_sha1": "<assembled.md の sha1>",
  "planning_snapshot": {
    "title": "...",
    "intent": "...",
    "audience": "...",
    "outline_notes": "..."
  }
}
```

`verdict` の意味:
- `pass`: `redo_script=false`（監査OK）
- `fail`: `redo_script=true`（明確にNG）
- `grey`: `redo_script=true`（グレー。対話型AIエージェントが追加精査し、オーナー指示が必要な場合はエスカレーション）

---

## 2. 判断基準（チェックリスト / 文字数だけで決めない）

### 2.1 企画整合（Planning SoT との一致）
- タイトルの約束（例: `【焦り】焦るほど空回りする`）と、本文の主題が一致している
- `企画意図` と `具体的な内容（話の構成案）` の流れに沿っている
- 重要な “視聴者の得” が本文の中盤〜終盤で回収されている

### 2.2 流れ（自然さ / 破綻がない）
- 導入→問題→理解→対処→締め の流れが破綻していない
- 章またぎ/区切り（`---`）の前後で、話が飛んでいない
- 終わり方が自然（説明が落ちずに消える / いきなり祝福だけになる、等はNG）

### 2.3 禁止事項（即 fail）
- **非フィクション系**（チャンネル SoT / 企画で「物語/体験談/寓話」が要求されていない場合）に、架空の現代人物を立てる
  - 例: `田村幸子、六十七歳。` のような “人物紹介→物語” 導入
  - CH01 はバリデータでも停止（ただし通常文の年齢言及はOK。例: `ブッダが29歳のとき…`）
  - 例外: CH05（恋愛体験談）、CH06（都市伝説）、CH12（寓話）など **SoTがフィクションを要求するチャンネル**はこの限りではない（その場合は 2.4 を優先）
- “機械的に増やした/削った” と疑える不自然な挿入（急な箇条書き、突然のテンプレ挿入、など）

### 2.4 チャンネル別の構成要件（例）
- CH12（寓話型）: 「物語パート→『物語は以上です。』→解説→定型締め」が満たされているか
- CH05（体験談/物語型）: 冒頭の掴み→主人公紹介→出会い→余韻の流れが崩れていないか（SoTに従う）

---

## 3. 運用（状態A→B→再確定）

### 3.1 状態A: 監査OK
- `metadata.redo_script=false`
- `metadata.dialog_ai_audit.verdict=pass`

### 3.2 状態B: 監査が古い（stale）
台本（`content/assembled.md`）が更新され、`script_hash_sha1` が変わったら監査は古い。
- 運用（標準）:
  - 対話AIが `stale` を検出 → `redo_script=true` に戻す（または `grey` 扱い） → 再監査して再確定
  - **自動で本文を直して合格扱いにしない**

---

## 4. 手順（対話AIが実行する）

1) lock（強制）
- `python3 scripts/agent_org.py locks --path workspaces/scripts/CHxx/NNN/status.json`
- `python3 scripts/agent_org.py lock 'workspaces/scripts/CHxx/**' --mode no_touch --ttl-min 60 --note 'dialog audit'`

2) 対象抽出（未投稿のみ）
- Planning CSV の `進捗=投稿済み/公開済み` は除外（`ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`）
- 便利コマンド（LLMなし）: `python3 scripts/ops/dialog_ai_script_audit.py scan`

3) 機械チェック（必須 / LLMなし）
- Aテキスト共通禁則（`validate_a_text`）で “形式破綻” を先に止める（対話AIが本文を読む前の前提チェック）。
  - 単体: `python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN`
  - バッチ（ログ1つ・ランキング付き）: `python3 scripts/ops/a_text_quality_scan.py --all --write-latest`
  - 運用: `validate_a_text` の **severity=error** が 1 つでも出たら **監査は fail**（reason=`a_text_rule_violation`）として `redo_script=true` にする（本文は触らない）。
    - ※ warning（例: `forbidden_statistics` / `too_many_quotes` / `too_many_parentheses` / 軽微な `length_too_short`）は自動failにしない。内容とバランスを人間が判断する。
  - 補足: 定型締め（例: `皆様の心が穏やかでありますように`）を **手動で追加して直す**場合は、末尾を `。` で終える（`incomplete_ending` を回避）。
- バッチ（redo付与だけ先にやる）:
  - scan: `python3 scripts/ops/dialog_ai_script_audit.py scan --include-locked`
  - apply: `python3 scripts/ops/dialog_ai_script_audit.py mark-batch --decisions <DECISIONS>.jsonl`
  - 例（hard-fail抽出→redo付与）:
    - `python3 scripts/ops/a_text_quality_scan.py --all --write-decisions`
    - `python3 scripts/ops/dialog_ai_script_audit.py mark-batch --decisions workspaces/logs/regression/a_text_quality_scan/<...>.decisions.jsonl`

4) 監査（対話AIの目視）
- Planning（タイトル/企画意図/構成案）と台本本文をセットで確認し、`pass/fail/grey` を決める。

5) Script SoT を更新
- `redo_script` と `redo_note`、`dialog_ai_audit` を更新する（本文は触らない）。

6) レポートを残す（標準）
- `workspaces/scripts/_reports/dialog_ai_script_audit/<timestamp>/` に監査結果を保存する。

---

## 5. 理由コード（固定）

- `planning_misalignment`: 企画と主題がズレている
- `flow_break`: つながりが飛ぶ/説明が落ちる
- `ending_unnatural`: 締めが不自然/唐突
- `tone_mismatch`: 口調/距離感がチャンネル想定とズレる
- `fictional_person`: 架空人物を立てている（禁止）
- `mechanical_edit_artifact`: 機械テコ入れの痕跡（不自然な挿入/削除）
- `a_text_rule_violation`: Aテキスト禁則（箇条書き/番号/区切り記号など）に抵触
- `required_structure_missing`: チャンネル SoT の必須構成要件が欠落（例: CH12 の「物語は以上です。」/定型締め）
- `policy_violation`: チャンネル SoT の禁止事項に抵触（例: 実在の店名/学校名など特定につながる固有名詞）
- `needs_human_review`: グレー（追加レビューが必要）
