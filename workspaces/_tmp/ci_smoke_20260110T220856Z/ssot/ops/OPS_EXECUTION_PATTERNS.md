# OPS_EXECUTION_PATTERNS — 処理パターン × CLIレシピ（SSOT）

目的:
- 「この処理パターン＝この処理フロー」「このパターンはこのCLIをこう組み合わせる」を **索引付きの正本**として固定する。
- エージェントが勝手な導線（直叩き/独自ロジック/モデル変更/無断API消費）へ逸れないようにする。
- 途中で落ちても **復帰コマンドが完全固定**されており、再開判断で迷わない状態を作る。

参照（入口）:
- 統一入口（P0）: `./ops list`（正本: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）
- 復帰コマンド固定（SSOT）: `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`
- Reconcile（issue→復帰コマンドを配線）: `ssot/ops/OPS_RECONCILE_RUNBOOK.md`
- 進捗ビュー（read-only）: `./ops progress --channel CHxx`
- 実行タイムライン（opsレジャー）: `./ops history --tail 50 --channel CHxx --video NNN`

このSSOTの運用ルール（重要）:
- 新しい“運用パターン”が発生したら、**必ずこのSSOTに追記する**（追記しない運用は「存在しない」扱い）。
- 追加時は:
  - `PAT-...` のIDを採番し、索引テーブルに追加する
  - 「前提」「手順（コマンド列）」「検証」「復帰」を必ず埋める
  - 既存パターンの置換/統合があるなら、古いパターン側に `DEPRECATED:` を明記してリンクを張る

LLMコスト制御（最重要）:
- 外部LLM APIコストを使わない（エージェントが思考して埋める）: `--llm think`
- 外部LLM APIを使う: `--llm api`
- `codex exec` を使う（明示した時だけ）: `--llm codex`
- 迷わない短縮（強制; `--llm` 付け忘れ防止）:
  - `./ops think <cmd> ...`（常に THINK MODE）
  - `./ops api   <cmd> ...`（常に API）
  - `./ops codex <cmd> ...`（常に codex exec。明示した時だけ）

---

## 0) 索引（まずここから）

| Pattern ID | ドメイン | 目的/症状 | 入口（代表コマンド） |
| --- | --- | --- | --- |
| `PAT-OPS-THINK-001` | OPS | 外部LLMを使わず進めたい | `./ops think ...` / `./ops agent ...` |
| `PAT-OPS-RECOVER-001` | OPS | 落ちた/迷子/最新不明 | `./ops progress` → `./ops reconcile` → `./ops resume ...` |
| `PAT-AUDIO-TTS-001` | AUDIO | 音声+SRTを作る/復帰 | `./ops audio --channel CHxx --video NNN` |
| `PAT-VIDEO-DRAFT-001` | VIDEO | SRT→画像→CapCutドラフト | `./ops video auto-capcut -- ...` |
| `PAT-VIDEO-REGEN-001` | VIDEO | 画像が欠損/失敗/差し替え | `./ops video regen-images -- ...` |
| `PAT-VIDEO-AUDIT-FIX-DRAFTS-001` | VIDEO | placeholder/重複/プロンプト事故の修復 | `./ops video audit-fix-drafts -- ...` |
| `PAT-VIDEO-VARIANTS-001` | VIDEO | 画像バリエーション生成 | `./ops video variants -- ...` |
| `PAT-VIDEO-REFRESH-PROMPTS-001` | VIDEO | プロンプトだけ最新化 | `./ops video refresh-prompts -- ...` |
| `PAT-VIDEO-SOURCE-MIX-CH02-001` | VIDEO | CH02の画像ソースmix適用 | `./ops video apply-source-mix -- ...` |
| `PAT-THUMB-BUILD-001` | THUMB | サムネ量産（指定動画） | `./ops thumbnails build --channel CHxx --videos ...` |
| `PAT-THUMB-RETAKE-001` | THUMB | in_progress再ビルド | `./ops thumbnails retake --channel CHxx` |
| `PAT-THUMB-QC-001` | THUMB | QC（in_progress確認） | `./ops thumbnails qc --channel CHxx --videos ...` |
| `PAT-OPS-SLACK-001` | OPS | 完了通知（Slack） | `./ops doctor` + `YTM_SLACK_*` |

---

## 1) テンプレ（新パターン追加時）

以下をコピーして追加する（`PAT-...` は重複禁止）。

```md
## PAT-<DOMAIN>-<TOPIC>-<NNN> — <短い説明>

目的:
- ...

適用条件（症状/トリガ）:
- ...

入力:
- `--channel CHxx`
- `--video NNN`（必要なら）
- `--run <run_dir>`（必要なら）

前提:
- `./ops doctor` が通る
- lock（並列衝突防止）: `python3 scripts/agent_org.py lock ...`

手順（固定）:
1) ...
2) ...

検証:
- `./ops progress --channel CHxx --videos NNN --format summary`
- `./ops history --tail 50 --channel CHxx --video NNN --failed-only`

復帰（固定）:
- `./ops resume <target> ...`
- `./ops reconcile --channel CHxx --video NNN`（dry-run→`--run`）

注意:
- LLMコスト: `./ops think ...` を推奨 / ここだけ `./ops api ...` 必須 など
- 禁止: 機械的分割/heuristicによる品質劣化/直叩き

関連SSOT:
- `ssot/ops/...`
```

---

## 2) OPS（迷子防止・復帰・コスト制御）

## PAT-OPS-THINK-001 — THINK MODE（外部LLM APIコストを使わず進める）

目的:
- 「外部LLM APIを使いたくない時」に、処理を止めずに前進させる（pendingを作り、エージェントが埋める）。

入口（固定）:
- `./ops think <cmd> ...`

典型手順:
1) まずTHINKで走らせる（例）:
   - `./ops think script new --channel CHxx --video NNN`
   - `./ops think audio --channel CHxx --video NNN`
   - `./ops think video auto-capcut -- --channel CHxx --video NNN`
2) pending が出たら、キューを見る:
   - `./ops agent list`
3) 1つずつ埋める（品質重視。局所heuristicは禁止）:
   - `./ops agent prompt <TASK_ID>`
   - 生成 → `./ops agent complete <TASK_ID> --content-file /path/to/content.txt`
4) 同じ `./ops think ...` を再実行して先へ進める

検証:
- `./ops history --tail 30 --only-cmd agent`

注意:
- THINK MODEは「外部LLM APIを呼ばない」ための運用レイヤ。品質を落とすための “簡略化” ではない。

関連SSOT:
- `ssot/agent_runbooks/README.md`
- `ssot/ops/OPS_AGENT_PLAYBOOK.md`

## PAT-OPS-RECOVER-001 — 落ちた/迷子/最新不明（progress→reconcile→resume）

目的:
- “どれが最新か” を憶測で決めず、derived view（progress）→issue→固定復帰コマンドの順で復帰する。

手順（固定）:
1) 進捗とissuesを見る（read-only）:
   - `./ops progress --channel CHxx --videos NNN --format summary --issues-only`
2) Reconcile（dry-run）で「何を叩くべきか」を確定する:
   - `./ops reconcile --channel CHxx --video NNN`
3) 実行する（ここでllmモードを明示。通常は THINK 推奨）:
   - `./ops think reconcile --channel CHxx --video NNN --run`
4) まだ直らない場合は、固定の復帰コマンドを直接叩く:
   - `./ops resume episode --channel CHxx --video NNN`
   - `./ops resume script --llm think --channel CHxx --video NNN`
   - `./ops resume audio  --llm think --channel CHxx --video NNN`
   - `./ops resume video  --llm think --channel CHxx --video NNN`

検証:
- `./ops history --tail 50 --channel CHxx --video NNN --failed-only`

関連SSOT:
- `ssot/ops/OPS_EPISODE_PROGRESS_VIEW.md`
- `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`
- `ssot/ops/OPS_RECONCILE_RUNBOOK.md`

## PAT-OPS-SLACK-001 — 完了通知（Slack）

目的:
- 長時間処理の完了/失敗/THINK pending 発生を Slack で受け取り、運用を止めない。

前提（SSOT）:
- `ssot/ops/OPS_ENV_VARS.md`（`YTM_SLACK_WEBHOOK_URL` / `SLACK_WEBHOOK_URL` など）

確認手順:
1) 設定の有無は `./ops doctor`（env/前提点検）で確認
2) 通知対象cmd（デフォルト）: `script,audio,video,thumbnails,publish,resume,reconcile`
   - 変更する場合: `YTM_SLACK_NOTIFY_CMDS=...`

注意:
- `./ops` は終了時に best-effort で通知する（失敗しても処理自体は落とさない）。
- THINK MODEは「pendingが出た」こと自体が重要イベントなので、pendingがあれば通知される（allowlistに無くても）。
- 通知本文には、原則として「次に見るべき場所」を含める:
  - `ops_latest`: `workspaces/logs/ops/ops_cli/latest/<episode>.json`（または `latest.json`）
  - `run_log`: `workspaces/logs/ops/ops_cli/runs/<run_id>/...log`（内側コマンドのstdout/stderr）
- `exit=2` は「警告（WARN）」の意味で返ることがある（例: `scripts/episode_ssot.py ensure` の warnings）。その場合は `FAILED` ではなく `WARN` として通知し、warnings を添付する（継続可）。

関連:
- `scripts/ops/slack_notify.py`
- `ssot/ops/OPS_ENV_VARS.md`

---

## 3) AUDIO（音声/TTS）

## PAT-AUDIO-TTS-001 — 音声+SRT生成（または復帰）

目的:
- `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav/.srt` を揃える。

入口（固定）:
- `./ops audio --channel CHxx --video NNN`

推奨（外部LLM APIコストを使わない）:
- `./ops think audio --channel CHxx --video NNN`

失敗/途中落ちの復帰（固定）:
- `./ops resume audio --llm think --channel CHxx --video NNN`

検証:
- `./ops progress --channel CHxx --videos NNN --format summary`

関連SSOT:
- `ssot/ops/OPS_AUDIO_TTS.md`
- `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`

---

## 4) VIDEO（SRT→画像→CapCut）

## PAT-VIDEO-DRAFT-001 — audio final SRT→画像→CapCutドラフト（固定: resume video）

目的:
- narration最終SRT（audio final）を入力に、run_dir を作り、画像を生成し、CapCutドラフトを組む。

入口（固定; episode向け）:
- `./ops resume video --llm think --channel CHxx --video NNN`

推奨（付け忘れ防止）:
- `./ops think resume video --channel CHxx --video NNN`

補足（SRTを明示したい場合）:
- `./ops video factory -- <channel> </path/to/input.srt> draft`
- `./ops video auto-capcut -- --channel CHxx --srt </path/to/input.srt> ...`（詳細: `python3 -m video_pipeline.tools.auto_capcut_run --help`）

途中で落ちた/ドラフトが壊れた（固定復帰）:
- `./ops resume video --llm think --channel CHxx --video NNN`

検証:
- `./ops episode ensure --channel CHxx --video NNN`
- `./ops history --tail 50 --channel CHxx --video NNN --failed-only`

注意（品質）:
- 機械的な等間隔分割/heuristic劣化で “とりあえず進める” のは禁止。落ちたらTHINK/復帰で治す。

関連SSOT:
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`
- `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`

## PAT-VIDEO-REGEN-001 — 欠損/失敗画像の再生成（regen-images）

目的:
- run_dir の cues を根拠に、欠損/失敗（fallback）画像を再生成する。

入口（固定）:
- `./ops video regen-images -- <args>`

推奨:
- `./ops think video regen-images -- <args>`

例:
- `./ops think video regen-images -- --run <run_dir> --only-missing`
- 範囲指定（例: CH02の 43-82 をやり直す）:
  - `./ops think video regen-images -- --run <run_dir> --indices 43-82 --overwrite`

次にやる（ドラフトへ反映）:
- `./ops resume video --llm think --channel CHxx --video NNN`

関連SSOT:
- `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`

## PAT-VIDEO-AUDIT-FIX-DRAFTS-001 — Draft監査+placeholder/重複修復（audit-fix-drafts）

目的:
- placeholder/欠損/近似重複を検出し、可能なら sibling run からコピーして修復する。
- 残るものは cue を根拠に再生成し、CapCut draft の assets に同期する。
- 画像プロンプトは `--refine-prompts` を THINK MODE で **エージェントが推論して埋める**（局所heuristicで単調量産しない）。

入口（固定）:
- `./ops video audit-fix-drafts -- <args>`

推奨（外部LLM APIコストを使わない）:
- `./ops think video audit-fix-drafts -- <args> --refine-prompts`

例:
- CH02 43-82（プロンプト事故/単調量産の復旧）:
  - `./ops think video audit-fix-drafts -- --channel CH02 --min-id 43 --max-id 82 --refine-prompts --regen-refined-subject-dupes --refined-subject-dupe-min-count 2`
- fallback（LLM無し）:
  - `./ops video audit-fix-drafts -- --channel CH02 --min-id 43 --max-id 82 --refine-prompts-local --regen-refined-subject-dupes --refined-subject-dupe-min-count 2`

検証:
- `./ops history --tail 50 --channel CHxx --failed-only`
- 画像を再生成したら `./ops resume video --llm think --channel CHxx --video NNN` でドラフトを再構築する。

注意:
- `refresh-prompts` は “テンプレ最新化” 用（LLM無し）。多様性を増やしたい場合は `audit-fix-drafts --refine-prompts` を使う。

## PAT-VIDEO-VARIANTS-001 — 画像バリエーション生成（variants）

目的:
- 同じcueに対する画像バリエーションを作る（比較/差し替え用）。

入口（固定）:
- `./ops video variants -- <args>`

例:
- `./ops video variants -- --run <run_dir> --preset watercolor_washi --preset cyberpunk_neon --model-key f-1`

注意:
- variants は `<run_dir>/image_variants/` 配下に別ディレクトリとして出力する（本体runは書き換えない）。
- “特定範囲だけやり直したい” 場合は `regen-images --indices ...` を使う（variantsでは範囲指定できない）。

## PAT-VIDEO-REFRESH-PROMPTS-001 — 既存run_dirのプロンプトだけ最新化（refresh-prompts）

目的:
- 画像生成テンプレ/ガードレール更新を、既存run_dirへ反映する（LLM呼び出し無し）。

入口（固定）:
- `./ops video refresh-prompts -- <args>`

例:
- `./ops video refresh-prompts -- --run <run_dir>`

次にやる:
- 必要なら `./ops think video regen-images -- --run <run_dir> --only-missing` → `./ops resume video --llm think --channel CHxx --video NNN`

## PAT-VIDEO-SOURCE-MIX-CH02-001 — CH02 画像ソースmix適用（apply-source-mix）

目的:
- 画像ソースmix（例: gemini:schnell:broll=4:3:3）を run_dir に適用する。

入口（固定）:
- `./ops video apply-source-mix -- <args>`

例（SSOT値を尊重しつつ、run単位で適用）:
- dry-run: `./ops video apply-source-mix -- <run_dir> --weights 4:3:3 --gemini-model-key g-1 --schnell-model-key f-1 --broll-provider pexels --dry-run`
- apply: `./ops video apply-source-mix -- <run_dir> --weights 4:3:3 --gemini-model-key g-1 --schnell-model-key f-1 --broll-provider pexels`

注意:
- 画像を再生成する場合は `regen-images --only-missing` を実行し、最後に `./ops resume video ...` でドラフトを再構築する。

関連SSOT:
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`（CH02の既定mix説明）

---

## 5) THUMB（サムネ）

## PAT-THUMB-BUILD-001 — サムネ量産（指定動画）

入口（固定）:
- `./ops thumbnails build --channel CHxx --videos 001 002 ...`

推奨（外部LLM APIコストを使わない）:
- `./ops think thumbnails build --channel CHxx --videos 001 002 ...`

検証:
- `./ops thumbnails qc --channel CHxx --videos 001 002 ...`

関連SSOT:
- `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`

## PAT-THUMB-RETAKE-001 — in_progress の再ビルド（retake）

目的:
- 途中で落ちた/止まったサムネを、in_progress の対象だけ再ビルドして done に寄せる。

入口（固定）:
- `./ops thumbnails retake --channel CHxx`

復帰（固定）:
- `./ops resume thumbnails --llm think --channel CHxx`

## PAT-THUMB-QC-001 — QC（in_progress確認）

入口（固定）:
- `./ops thumbnails qc --channel CHxx --videos 001 002 ...`

## PAT-THUMB-SYNC-001 — Inventory同期（sync-inventory）

目的:
- サムネ在庫（inventory）を最新に同期し、探索ノイズを減らす。

入口（固定）:
- `./ops thumbnails sync-inventory --channel CHxx`

---

## 6) 付録: “迷い”の典型と禁止事項（再掲）

禁止（SSOT）:
- `--llm` を省略して “なんとなく” 実行する（必ず `./ops think/api/codex ...` で明示）
- 落ちた時に独自の復帰導線を作る（`./ops reconcile` / `./ops resume` 以外で復帰しない）
- 品質を落とす機械的等間隔分割（文脈ベース以外は契約違反）
- “局所heuristicで済ませる” ことで単調な画像/台本を量産する（THINK/AGENTで必ず思考を入れる）
