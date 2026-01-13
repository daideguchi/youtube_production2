# OPS_FACT_CHECK_RUNBOOK — 完成台本（Aテキスト）のファクトチェック運用

目的:
- 台本（Aテキスト）が「それっぽい嘘」を混入したまま音声/字幕に進む事故を止める。
- Aテキスト本文に URL/脚注を入れず、`topic_research` の中間生成物（検索/Wikipedia/refs）を “足場” にして検証する。

重要制約（必須）:
- **本文を書き換える文章生成は禁止**（このRunbookは「検証」と「修正方針の提示」まで）。
- 憶測は禁止。**証拠（抜粋）に基づく判定のみ**。

---

## 1) 仕組み（どこで走るか）

- 実行箇所: `packages/script_pipeline/runner.py` の `script_validation`
- 出力: `workspaces/scripts/{CH}/{NNN}/content/analysis/research/fact_check_report.json`
- 判定対象: `content/assembled_human.md`（優先）→ `content/assembled.md`

`script_validation` は以下の順で止血する:
1. 機械チェック（禁則/字数/記号など）
2. LLM品質ゲート（Judge→Fixer→不足時は Extend/Expand/Shrink）
3. 意味整合（semantic alignment）
4. **ファクトチェック（本Runbook）**

ファクトチェックの前提:
- Aテキスト全体を丸ごと検証するのではなく、**検証可能な claim（客観要素のある文）**だけを抽出してチェックする。
  - 例: 数字/年/割合、研究/統計/出典の言及、仏陀/経典/引用（「」）など
- 検証可能な claim が 0 件の場合は `note: no_checkable_claims` として **pass 扱い**（= 止めない）。

---

## 2) チャンネル別ポリシー（SoT）

SoT: `configs/sources.yaml`
- `channels.CHxx.fact_check_policy`（オプション; 未設定時は `web_search_policy` から導出）
  - `disabled`: 実行しない（reportは `verdict=skipped` を書く）
  - `auto`: `fail` のときのみ停止（`warn` は通すがreportは残る）
  - `required`: `pass` 以外は停止（`warn/fail` で止める）

既定の導出:
- `web_search_policy=disabled` → `fact_check_policy=disabled`
- `web_search_policy=required` → `fact_check_policy=required`
- それ以外 → `auto`

注:
- CH05/CH22/CH23 はファクトチェック不要（`web_search_policy=disabled` のため既定で `disabled`）。

---

## 3) 手動で1本だけ回す（単発）

```
./scripts/with_ytm_env.sh python3 scripts/ops/fact_check_codex.py --channel CH01 --video 251
```

出力:
- `workspaces/scripts/CH01/251/content/analysis/research/fact_check_report.json`

---

## 4) 失敗したときの対処（停止→直す→再ゲート）

方針:
- report の `claims[].status` が `unsupported/uncertain` の箇所を “事実として断言しない” 形へ修正する。
- 出典が無い/弱い場合は、**数字/固有名詞/断言を弱める**（例: 断言→「〜とされる」「一部では」へ）。

手順:
1. `fact_check_report.json` を開き、`status != supported` の claims を確認
2. `content/assembled_human.md` を修正（本文は人間/別LLMで直す。Codexで本文生成はしない）
3. `script_validation` を再実行して合格させる
   - `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

---

## 5) 環境変数（省略可）

（詳細は `ssot/ops/OPS_ENV_VARS.md` を正とする）

- `YTM_FACT_CHECK_POLICY`（override）: `disabled|auto|required`
- `YTM_FACT_CHECK_MAX_CLAIMS`（default: 12）: 抽出するclaim上限
- `YTM_FACT_CHECK_MIN_CLAIM_SCORE`（default: 4）: claim抽出の最小スコア（客観要素が弱い文を除外する）
- `YTM_FACT_CHECK_MAX_URLS`（default: 8）: 参照URL上限
- `YTM_FACT_CHECK_MAX_SOURCES_PER_CLAIM`（default: 2）: claimごとに渡す抜粋の上限
- `YTM_FACT_CHECK_EXCERPT_MAX_CHARS`（default: 1400）: 抜粋の最大長
- `YTM_FACT_CHECK_FETCH_TIMEOUT_S`（default: 20）: URL本文取得timeout
- `YTM_FACT_CHECK_FETCH_MAX_CHARS`（default: 20000）: URL本文の最大文字数
- `YTM_FACT_CHECK_CODEX_TIMEOUT_S`（default: 180）: `codex exec` のtimeout
- `YTM_FACT_CHECK_CODEX_MODEL`（省略可）: codex exec に渡すモデル名
- `YTM_FACT_CHECK_FORCE=1`（省略可）: fingerprint一致でも再実行
- `YTM_FACT_CHECK_LLM_FALLBACK=0`（省略可）: Codex失敗時のAPIフォールバックを禁止
- `YTM_FACT_CHECK_LLM_TASK`（default: `script_a_text_quality_judge`）: フォールバックで使う LLMRouter task key

---

## 6) 安全性（事故を起こさないための約束）

- `codex exec` は `--sandbox read-only` で実行し、repo/workspaces を書き換えない（レポート作成はPython側で書く）。
- モデルの出力は必ずJSONパースし、引用（quote）が抜粋内に存在するか機械検証する。
  - 引用が一致しない場合、そのclaimは `supported/unsupported` にできない（自動で `uncertain` に降格）。
