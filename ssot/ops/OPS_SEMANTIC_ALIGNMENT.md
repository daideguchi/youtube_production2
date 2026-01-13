# OPS_SEMANTIC_ALIGNMENT — タイトル/サムネ訴求 ↔ 台本コア の「意味整合」修復（運用SoT）

目的:
- タイトル/サムネが約束する「訴求」と、Aテキスト（読み台本）のコアメッセージがズレて作業が止まる事故を防ぐ。
- 文字一致のガチガチ判定ではなく、**定性的な意味整合**で「明らかなズレ」だけを検出し、**最小限のリライト**で修正する。

関連:
- Aテキスト規約（必須）: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`
- Planning SoT: `workspaces/planning/channels/CHxx.csv`
- Script SoT: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（優先）→ `assembled.md`

---

## 1) 生成物（どこが正？）

### 1.1 出力（レポート）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/alignment/semantic_alignment.json`
  - 企画の訴求（タイトル/サムネ）と台本のコアが一致しているかを、LLM が **定性的に判定**した結果。
- `workspaces/scripts/{CH}/{NNN}/content/analysis/alignment/outline_semantic_alignment.json`
  - アウトライン段階の事前ゲート（章草稿=高コストに入る前に、明らかな逸脱を止める）。

### 1.2 status.json への記録
- `workspaces/scripts/{CH}/{NNN}/status.json: metadata.semantic_alignment`
  - `verdict` / `rewrite_required` などの判定、LLMメタ（provider/model/request_id）、レポートパスを保持する。

### 1.3 台本の正本
- 台本本文は `assembled_human.md` が存在すればそれが正本。なければ `assembled.md` が正本。
- 修正時は split-brain を防ぐため、**canonical（正本）と `assembled.md`（ミラー）を同内容に揃える**。

---

## 2) 使い方（CLI）

注: 既定ではパイプラインが `script_outline` と `script_validation` で意味整合ゲートを実行します。  
`script_validation` は **`verdict: major`（明らかなズレ）のみ停止**します。本文の自動書き換え（auto-fix）は行いません（事故防止）。  
`minor`（軽微）は「芯は回収しているが微妙にぼやける/解釈ゆれ」の扱いで、既定では停止しません（記録は残る）。  
運用者は基本 **`major` だけ**気にすればOKです（`minor` はログ）。
修正が必要な場合、CLI を「レポート閲覧」と「最小リライト適用（手動）」に使います。

判定定義（人間向け）:
- `ok`: タイトル/サムネが約束する主題とベネフィットを、本文が最後まで回収している（芯がブレない）。
- `minor`: 主題は合っているが、焦点が少しズレる/回収が薄い/別解釈が入りそう等の改善余地がある（既定では停止しない）。
- `major`: 主題が外れている（別テーマに寄る/タイトルの問いに答えていない/企画意図の柱が違う）。量産ではここだけ止める。

### 2.1 チェック（書き換えなし）
```
./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023
```

### 2.2 明らかなズレだけ修正（最小リライト）
```
./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023 --apply
```
※ デフォルトでは `verdict: major`（明らかなズレ）のときだけ書き換えます。`minor` も直す場合は `--also-fix-minor`。

### 2.3 「minor も直す」運用（オプション）
```
./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CH13 --video 023 --apply --also-fix-minor
```

### 2.4 実行後の状態
- `--apply` 時:
  - 台本のバックアップを `workspaces/scripts/_archive/semantic_alignment_fix_<timestamp>/...` に保存
  - `script_validation`（機械チェック; LLMなし）を自動実行して、TTS へ即進める状態に戻す

### 2.5 ゲート制御（環境変数）
- `SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE=0` でアウトライン事前ゲートを無効化（用途: デバッグ。運用で使わない）
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE=0` で `script_validation` の意味整合ゲートを無効化（用途: デバッグ。運用で使わない）
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK`（既定 `0`）で意味整合ゲートの合格条件を制御（`script_outline` と `script_validation` の両方に影響）:
  - `0`: `verdict: major` のみ停止（ok/minor は合格; 量産のデフォルト）
  - `1`: `verdict: ok` 以外は停止（minor/major は停止; ズレをより厳密にブロック）
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX` は deprecated/ignored（`script_validation` 内で本文は自動書き換えしない）。修正は `--apply` を手動で実行する。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR` / `_MAJOR` / `_MAX_FIX_ATTEMPTS` も deprecated/ignored（自動書き換えはしない）。
- `SCRIPT_SEMANTIC_ALIGNMENT_MAX_A_TEXT_CHARS` は「判定に渡す最大文字数」。超える場合は **先頭+末尾の抜粋で判定**し、auto-fix は安全のためスキップする。上限を上げる場合は値を増やす（長尺は Marathon 運用へ）。

### 2.6 auto-fix の安全設計（重要）
※ 現運用では `script_validation` の auto-fix は無効化（本文の自動書き換えをしない）。この節は “手動 `--apply` 実行時の安全設計” として読む。
- `script_validation` の auto-fix は **Aテキストの機械ルール（LLMなし）に合格した場合のみ適用**します（不合格の草稿は書き込みません）。
  - 例: `length_too_short` / `too_many_quotes` / `too_many_parentheses` などが残る場合は停止。
- ただし例外（自動で収束させる）:
  - 置換後に「長すぎ/短すぎ」だけが残る場合は、**最大1回**だけ LLM Shrink / Extend を挟んでレンジに戻す（コスト暴走を防ぐため無限ループ禁止）。
- `semantic-align --apply` は、`SCRIPT_VALIDATION_AUTO_LENGTH_FIX=1` が有効で、違反が `length_too_long` のみのときに限り、いったん書き込み→直後の `script_validation` で自動 shrink してレンジに戻す（他の違反が残る草稿は書き込まない）。
- `minor` と `major` で **別プロンプト**を使い分けます（minor は “局所修正・短くしない” を強制）。
- 文字数の下限（`<<CHAR_MIN>>`）は `target_chars_min` を採用する（現状本文の長さを下限にしない）。狙い: タイトル/サムネ回収のための差し替え・追記を許容しつつ、`max` 超過は shrink で収束させるため。
- 生成草稿が機械ルール（LLMなし）に不合格だった場合、`status.json` の `script_validation.details.error` は `semantic_alignment_auto_fix_invalid_a_text` になり、`error_codes` に詳細が残ります。
- タイトル/サムネに「Nつ」等の数が含まれる場合は、判定の取りこぼしを防ぐために **数の回収（例: `一つ目〜七つ目`）を機械的にサニティチェック（LLMなし）**します。
  - 台本側で `Nつ目` が揃っているのに、LLM 判定が「数が回収されていない」と言っているケースを自動で補正します（誤検知で止まる事故を防ぐ）。

---

## 3) 判定ポリシー（重要）

- 文字一致チェックは目的ではない:
  - **「タイトルの語句が本文に出るか」は必須要件ではない**（意味として回収できているかで判定）。
- ただし、サムネ上/下が約束する「視聴後ベネフィット」は回収する:
  - 例: `守れる` / `ほどける` / `静まる` など。
- SoTの優先順位（誤判定防止）:
  - **タイトル + サムネ上/下 が最優先（絶対に正）**。
  - `企画意図` / `悩みタグ` / `ベネフィット` は補助。タイトル/サムネと矛盾する場合は **無視** して判定する（=「主題の芯」をタイトル側に寄せる）。
- サムネが強い断言（例: `放置は危険`）の場合:
  - 本文側で「脅しではなく、放置すると何が起きやすいか」を短く根拠付きで補い、トーン衝突を解消する。

---

## 4) バッチ運用（手順）

- コストを抑えるため、まず `semantic-align` でレポートを作り、`verdict: major` から優先して `--apply` する。
- 例（手動バッチ）:
```
for v in 023 024 025; do
  ./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CH13 --video $v --apply
done
```
