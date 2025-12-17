# OPS_SEMANTIC_ALIGNMENT — タイトル/サムネ訴求 ↔ 台本コア の「意味整合」修復（運用SoT）

目的:
- タイトル/サムネが約束する「訴求」と、Aテキスト（読み台本）のコアメッセージがズレて作業が止まる事故を防ぐ。
- 文字一致のガチガチ判定ではなく、**定性的な意味整合**で「明らかなズレ」だけを検出し、必要なら **最小限のリライト**で修正する。

関連:
- Aテキスト規約（必須）: `ssot/OPS_A_TEXT_GLOBAL_RULES.md`
- Planning SoT: `workspaces/planning/channels/CHxx.csv`
- Script SoT: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（優先）→ `assembled.md`

---

## 1) 生成物（どこが正？）

### 1.1 出力（レポート）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/alignment/semantic_alignment.json`
  - 企画の訴求（タイトル/サムネ）と台本のコアが一致しているかを、LLM が **定性的に判定**した結果。

### 1.2 status.json への記録
- `workspaces/scripts/{CH}/{NNN}/status.json: metadata.semantic_alignment`
  - `verdict` / `rewrite_required` などの判定、LLMメタ（provider/model/request_id）、レポートパスを保持する。

### 1.3 台本の正本
- 台本本文は `assembled_human.md` が存在すればそれが正本。なければ `assembled.md` が正本。
- 修正時は split-brain を防ぐため、**canonical（正本）と `assembled.md`（ミラー）を同内容に揃える**。

---

## 2) 使い方（CLI）

### 2.1 チェック（書き換えなし）
```
python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023
```

### 2.2 明らかなズレだけ修正（最小リライト）
```
python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023 --apply
```
※ デフォルトでは `verdict: major`（明らかなズレ）のときだけ書き換えます。`minor` も直す場合は `--also-fix-minor`。

### 2.3 「minor も直す」運用（必要時のみ）
```
python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023 --apply --also-fix-minor
```

### 2.4 実行後の状態
- `--apply` 時:
  - 台本のバックアップを `workspaces/scripts/_archive/semantic_alignment_fix_<timestamp>/...` に保存
  - `script_validation`（決定論ゲート）を自動実行して、TTS へ即進める状態に戻す

---

## 3) 判定ポリシー（重要）

- 文字一致チェックは目的ではない:
  - **「タイトルの語句が本文に出るか」は必須要件ではない**（意味として回収できているかで判定）。
- ただし、サムネ上/下が約束する「視聴後ベネフィット」は回収する:
  - 例: `守れる` / `ほどける` / `静まる` など。
- サムネが強い断言（例: `放置は危険`）の場合:
  - 本文側で「脅しではなく、放置すると何が起きやすいか」を短く根拠付きで補い、トーン衝突を解消する。

---

## 4) バッチ運用の目安

- コストを抑えるため、まず `semantic-align` でレポートを作り、`verdict: major` から優先して `--apply` する。
- 例（手動バッチ）:
```
for v in 023 024 025; do
  python3 -m script_pipeline.cli semantic-align --channel CH13 --video $v --apply
done
```
