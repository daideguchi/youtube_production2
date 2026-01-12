# OPS_FIXED_RECOVERY_COMMANDS — 復帰コマンドの固定（SSOT）

目的:
- 途中で落ちた/迷子になった時の「復帰コマンド」を **完全に固定**する。
- エージェントが独自導線（直叩き/独自ロジック/モデル変更/勝手なAPI消費）に逸れないようにする。

前提:
- 統一入口: `./ops`（`ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）
- 進捗ビュー（read-only）: `./ops progress --channel CHxx`
- “最新の実行” の把握（keep-latest pointer）: `./ops latest --channel CHxx --video NNN`
- 実行タイムライン（レジャー）: `./ops history --tail 50 --channel CHxx --video NNN`

LLMコスト制御（重要）:
- 外部LLM APIコストを使わない: `--llm think`
- 外部LLM APIを使う: `--llm api`
- `codex exec` を使う（明示した時だけ）: `--llm codex`
- 迷わない短縮（強制）:
  - `./ops think <cmd> ...`（`--llm think` を強制）
  - `./ops api <cmd> ...`（`--llm api` を強制）
  - `./ops codex <cmd> ...`（`--llm codex` を強制）

> 以降、復帰は **ここに書かれたコマンドだけ**で行う（例外は `YTM_EMERGENCY_OVERRIDE=1` のデバッグ時のみ）。

---

## 0) 最初に叩く（共通）

- `./ops doctor`
  - env/前提の点検（失敗したら、先に直す）

> `./ops resume ...` は既定で `doctor` を先に実行する（不要なら `--skip-doctor`）。
> 注: `--skip-doctor` は `./ops resume <target> -- --skip-doctor ...` のように `--` の後ろ（転送引数側）で渡す。

---

## 1) Episode（SoTの迷子を止血: run選択/リンク集）

症状:
- `video_run_unselected`, `video_run_missing`, `capcut_draft_broken` 等が出る
- 「どの run_dir が最新か」がわからない

復帰コマンド（固定）:
- `./ops resume episode -- --channel CHxx --video NNN`
  - 内部: `scripts/episode_ssot.py ensure`
  - 正本は増やさず、`status.json` の `metadata.video_run_id` を更新し、`workspaces/episodes/...` のリンク集を再生成する

同義（明示したい場合）:
- `./ops episode ensure -- --channel CHxx --video NNN`

注意（WARN扱い）:
- `scripts/episode_ssot.py ensure/materialize` は `episode_manifest.json` に warnings があると `exit=2` を返す（例: audio未生成）。
- `./ops resume episode` はこれを **WARN（継続可）** として扱う（失敗扱いにしない）。warnings の内容は `workspaces/episodes/<CH>/<NNN>/episode_manifest.json` を参照する。

---

## 2) Script（台本）

症状:
- 途中で落ちた/途中から再開したい
- status.json の stages が pending/failed/processing のまま

復帰コマンド（固定）:
- `./ops resume script -- --llm api --channel CHxx --video NNN`

注意（固定ルール）:
- 台本（`script_*`）は **LLM API（Fireworks）固定**。THINK/AGENT（pending代行）で台本を書かない。
  - `./ops think script ...` / `./ops resume script -- --llm think ...` は policy で停止する（誤運用防止）。

---

## 3) Audio/TTS（音声・SRT）

症状:
- `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav/.srt` が揃っていない
- TTS が途中で落ちた

復帰コマンド（固定）:
- `./ops resume audio -- --llm think --channel CHxx --video NNN`
- `./ops resume audio -- --llm api --channel CHxx --video NNN`

---

## 4) Video（CapCutドラフト）

症状:
- CapCutドラフト生成が途中で落ちた
- run_dir はあるがドラフトが壊れている/未生成

復帰コマンド（固定）:
- `./ops resume video -- --llm think --channel CHxx --video NNN`
- `./ops resume video -- --llm api --channel CHxx --video NNN`

仕様（固定）:
- 入力SRTは **audio final の SoT**（`workspaces/audio/final/.../*.srt`）を自動で選ぶ
- `video_pipeline.tools.factory ... draft` でドラフト再生成（最新run_dirを自動選択）
- 実行後に `./ops episode ensure -- --channel CHxx --video NNN` を自動で走らせ、run選択/リンク集を確定させる

注意:
- audio final が無いと復帰できない → 先に `./ops resume audio -- --llm <MODE> --channel CHxx --video NNN`

---

## 5) Thumbnails（サムネ）

症状:
- サムネ生成が途中で落ちた（`projects.json` が `in_progress` のまま）

復帰コマンド（固定）:
- `./ops resume thumbnails -- --llm think --channel CHxx`
- `./ops resume thumbnails -- --llm api --channel CHxx`

備考:
- 内部は `scripts/thumbnails/build.py retake`（in_progress のものを再ビルドして done に寄せる）

---

## 6) 迷子確認（復帰後のチェック）

- `./ops progress --channel CHxx --videos NNN --format summary`
- `./ops history --tail 50 --channel CHxx --video NNN --failed-only`

---

## 7) Reconcile（復帰の“自動配線” / 固定）

目的:
- `episode_progress` の issues を根拠に、固定復帰コマンド（`./ops resume ...`）だけを順に実行する。
- デフォルトは **dry-run**（事故防止）。`--run` を付けた時だけ実行する。

入口（固定）:
- `./ops reconcile --channel CHxx --video NNN`（dry-run）
- `./ops reconcile --channel CHxx --video NNN --llm think --run`

運用SSOT:
- `ssot/ops/OPS_RECONCILE_RUNBOOK.md`

注意:
- `--run` で `--video/--videos` を省略すると拒否する（暴発防止）。全体実行が必要なら `--all` を明示する。
