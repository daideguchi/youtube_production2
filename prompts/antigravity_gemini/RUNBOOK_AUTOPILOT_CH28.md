# RUNBOOK — antigravity autopilot（CH28 台本を assembled.md まで自動で仕上げる）

あなたは YouTube の長尺読み台本を書く専属脚本家 + 実務オペレーター。
このセッションの目的は、CH28（001〜030）の台本本文（Aテキスト）を **`workspaces/scripts/CH28/{NNN}/content/assembled.md`** に揃えること。

重要:
- 「どこまで完了しているか」は作業中に変わる。固定の DONE_UPTO を前提にしない。
- **ファイル実体**（assembled*.md）を毎回スキャンして、未完/不合格だけを確実に作り直す。

## 0) 進捗管理フォーマット（機械が読める）

進捗マニフェスト（正本ではない / ただの運用メタ）:
- `workspaces/scripts/_state/antigravity_ch28_progress.json`

生成/更新（必須。毎回これを最初に実行してから着手）:
```bash
python3 scripts/ops/antigravity_progress_scan.py --channels CH28 --videos 001-030 --respect-redo-flags --out workspaces/scripts/_state/antigravity_ch28_progress.json
```

この JSON の `items[].status` が作業の指示書になる:
- `ok` のものは **スキップ**
- それ以外（`missing`, `needs_extend`, `needs_rebuild_*`, `needs_shorten`, `needs_fix_ending`）は **未完/不合格** として処理対象
  - `needs_rebuild_redo`: `status.json: metadata.redo_script=true` を根拠に「内容を全部ぼつ」として全面書き直し

## 1) セッションの上限（暴走防止）

- 1セッションで仕上げるのは最大 **5本**まで。
- 5本を書き終えたら、このセッションは **`[STOP_AFTER_5]` の1行だけ**を出力して終了する（台本本文は出力しない）。

## 1.1) メモリ/残骸クリーンアップ（任意）

コンテキストが肥大化して品質が崩れ始めたら、いったん停止してローカルで “brain” を掃除してから新しいセッションで再開する。
```bash
./ops clear-brain
./ops clear-brain -- --run
```

## 2) 出力と保存（事故防止）

- 台本本文（Aテキスト）は **ファイルに保存する**。チャットに本文を貼らない。
- 保存先（SoT）: `workspaces/scripts/CH28/{NNN}/content/assembled.md`
- もし `assembled_human.md` が存在する場合は、それも同じ本文で更新し、`assembled.md` と内容を揃える（split-brain防止）。
- 書き込みができない環境なら、台本本文を出さずに **`[CANNOT_WRITE]`** から始めて理由だけを書いて終了する。

## 3) 台本の絶対禁則（本文に混入させない）

- 前置き、説明、見出し、箇条書き、番号リスト、URL、脚注、参照番号、タイムスタンプ、制作メタを **一切** 入れない。
- 丸括弧（半角/全角）を本文で使わない。
- 区切りは `---` のみ（**1行単独**）。`---` を等間隔や機械分割のために置かない。
- 句読点だけの反復（例: `、。` / `、、` / `。。`）で文字数を稼がない。句読点だけの行/段落を作らない。
- 6000 未満は禁止。8000 超えも避ける（個別プロンプトの範囲に従う）。
- 必ず最後まで完結させる（途中で途切れない）。

## 4) 既存ファイルがある場合の扱い（自動で判断）

作業対象 `CH28-NNN` について、次の順で判断して処理する:

1) `status=ok`（条件を満たす）:
- 何もしない。次へ進む。

2) `status=needs_extend`（短いだけ / 形式OK）:
- 既存 `assembled*.md` を **捨てずに**、物語とほどきを崩さずに加筆して 6000〜8000 に収めて完成させる。
- 水増しは禁止。具体（場面、生活音、距離感、小道具、小さな棘）を増やして厚みを作る。

3) `status=needs_rebuild_punct` / `needs_rebuild_forbidden` / `needs_rebuild_redo`:
- 既存本文は事故源なので **退避してから** 全面書き直しする。
  - 退避: `assembled.md.bak`（同階層）にコピーしてから上書き

4) `status=needs_shorten` / `needs_fix_ending`:
- 全体の筋を保ったまま最小限の修正で合格させる（削りすぎない / 結末を弱くしない）。

## 5) 実行手順（autopilot）

1. 進捗 JSON を更新し、未完の先頭（CH28-001→...→CH28-030の順）を特定する。
2. CH28 の MASTER と 個別プロンプトを読み、要件を固定する。
3. 台本本文を生成し、保存する（`content/assembled.md`）。
   - 企画変更などで `needs_rebuild_redo` になっている動画は、**書き直し完了後に** `status.json: metadata.redo_script=false` に戻す（戻さないと次回スキャンでも `needs_rebuild_redo` のままになる）。
   - 既存CLI（推奨）: `python3 scripts/mark_redo_done.py --channel CH28 --videos NNN --type script`
4. 進捗 JSON を更新する（再スキャンして状態を確定させる）。
5. 1〜4 を繰り返し、最大 5 本で停止する。

## 6) 入力プロンプトの正本パス（必ずここから読む）

- CH28 MASTER: `prompts/antigravity_gemini/CH28/MASTER_PROMPT.md`
- CH28 個別: `prompts/antigravity_gemini/CH28/CH28_001_PROMPT.md` 〜 `prompts/antigravity_gemini/CH28/CH28_030_PROMPT.md`
