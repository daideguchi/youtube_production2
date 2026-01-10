# OPS_SYSTEM_OVERVIEW — このプロダクトの仕組み（全体像SSOT）

目的:
- **新規参加者/低知能エージェントでも迷わない**ように、「何が正本（SoT）で、何を叩けば何が起きるか」を1枚で理解できる状態にする。
- リファクタリング/ゴミ判定/運用変更の前に、**全員が同じ前提**を参照できる状態にする。

前提:
- 詳細な確定フロー（観測ベースの正本）は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`。
- 本書は **全体像のSSOT**（overview）。ドメイン個別の細部はリンク先のSSOTを正とする。

---

## TL;DR（1分で掴む）

- これは「**YouTube量産**（企画→台本→音声→動画→公開）」を **SSOT中心**で再現性高く回す工場です。
- 主線（Happy Path）:
  1) Planning（入力SoT）→ 2) Script（A-text/台本）→ 3) TTS（Bテキスト/voicevox_kana）→ 4) Video（CapCut）→ 5) Publish
- 事故防止の固定ルール（最重要）:
  - **台本（`script_*`）は LLM API（Fireworks/DeepSeek）固定**。**Codex/THINK/AGENT に流さない**（失敗時も停止して原因を直す）。
  - **TTS（`tts_*`）は AIエージェント（Codex）主担当**（THINK/AGENT の pending 運用で止めて直す）。
    - 推奨: `./scripts/think.sh --tts -- python -m script_pipeline.cli audio --channel CHxx --video NNN`
    - 注: ここで言う「Codex」は **codex exec（非対話CLI）ではない**（別物）。TTSは codex exec へ寄せない。
    - 読み修正の辞書運用は **D-014** に従う（ユニーク誤読のみ辞書へ / 曖昧語は動画ローカルで修正）。
  - **勝手なモデル/プロバイダ切替や自動ローテは禁止**（切替はコード/スロットで明示）。
- 迷ったら（まずここ）:
  - 実行入口: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
  - 証跡/ログ: `ssot/ops/OPS_LOGGING_MAP.md`
  - “今の正解”: `ssot/DECISIONS.md`

最重要リンク（迷ったらここから）
- 入口: `START_HERE.md`
- 確定フロー/I-O/SoT: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 実行入口（CLI/UI）: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- SSOT=UI(view)（全処理可視化の設計）: `ssot/ops/OPS_SSOT_SYSTEM_MAP.md`
- ディレクトリ正本: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
- ログ正本: `ssot/ops/OPS_LOGGING_MAP.md`
- 生成物の保持/削除: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- 低知能エージェント運用（lock/SSOT/削除/パッチ）: `ssot/ops/OPS_AGENT_PLAYBOOK.md`（repo全体ルールは `AGENTS.md`）
- Aテキスト技法（差し込みパッケージ）: `ssot/ops/OPS_A_TEXT_TECHNIQUE_PACKAGES.md`
- 画像API（進捗/運用メモ）: `ssot/ops/IMAGE_API_PROGRESS.md`
- LLM usage/cost 検証計画: `ssot/plans/PLAN_LLM_USAGE_MODEL_EVAL.md`
- AI依頼テンプレ（質問の粒度を揃える）: `ssot/reference/CHAT_AI_QUESTION_TEMPLATE.md`

追加リンク（深掘り）
- 直書きパス/旧名参照の監査入口: `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`
- UI整理（ワークスペース整流）: `ssot/plans/PLAN_UI_WORKSPACE_CLEANUP.md`
- LLMモデル使い分け: `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md`
- LLM usage 集計ツール仕様: `ssot/ops/TOOLS_LLM_USAGE.md`
- Video runs の archive/restore: `ssot/ops/OPS_VIDEO_RUNS_ARCHIVE_RESTORE.md`
- CH02 CapCutドラフト SOP: `ssot/ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`
- artifact駆動パイプライン設計: `ssot/ops/OPS_ARTIFACT_DRIVEN_PIPELINES.md`
- master styles（チャンネル別スタイル定義）: `ssot/ops/master_styles.json`

---

## 0) 用語（このSSOTで固定）

- **SoT（Single Source of Truth / 正本）**: その工程の唯一の真実。以降の処理はここを読む。
- **Mirror（ミラー）**: UIや互換のための写し。原則手動編集しない（編集する場合は同期ルールに従う）。
- **Artifacts（生成物）**: 中間/最終成果物。保持/削除の規約に従う（`PLAN_OPS_ARTIFACT_LIFECYCLE`）。
- **Episode**: `{channel}/{video}`（例: `CH07/009`）。Workspacesの粒度は基本これ。
- **run_dir**: 動画（画像/帯/CapCut等）を **run単位**で閉じるディレクトリ（例: `workspaces/video/runs/CH07-009/`）。

---

## 1) 何がどこにあるか（Repo構造とSoT）

### 1.1 トップレベル（役割の固定）

- `apps/`: UI/サーバ/Remotion等のアプリ実装（生成物は `workspaces/` へ）
- `packages/`: Pythonパッケージ（恒久ロジック。生成物を置かない）
- `scripts/`: 運用CLI（入口。恒久ロジックは `packages/` に寄せる）
- `configs/`: 設定正本（機密は入れない）
- `credentials/`: OAuth等の機密（git管理しない）
- `workspaces/`: **SoT + 生成物の唯一の置き場**（巨大ファイルはgitignoreされるがディスク上の正本）
- `asset/`: **静的素材のL0（git管理の正本）**（BGM/ロゴ/素材）
- `ssot/`: 設計/運用の正本（この文書含む）
- `backups/`: archive-first の退避（復元目的のみ。実行入口にしない。旧資産/試作も常駐させずここへ退避する）

### 1.2 フェーズ別SoT（工程の“唯一の正本”）

| フェーズ | SoT（正本） | 主な生成物/ミラー | 主な入口 |
| --- | --- | --- | --- |
| Planning（企画/進捗） | `workspaces/planning/channels/CHxx.csv` / `workspaces/planning/personas/CHxx_PERSONA.md` | — | UI `/planning` / `python3 scripts/ops/planning_lint.py` |
| Script（台本） | `workspaces/scripts/{CH}/{NNN}/status.json` + `content/assembled_human.md`（あれば優先） | `content/assembled.md`（ミラー）/ `content/analysis/**` | `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py <MODE> ...` / UI `/channels/:channelCode/videos/:video` |
| Audio（音声/SRT） | `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav/.srt` | `workspaces/scripts/**/audio_prep/**`（中間） | `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli audio ...` / UI `/audio-tts` |
| Video（画像/CapCut） | `workspaces/video/runs/{run_id}/` | `workspaces/video/input/**` / CapCut draft / images / belt | UI `/capcut-edit/*` / `python3 -m video_pipeline.tools.*` |
| Thumbnails（独立） | `workspaces/thumbnails/projects.json` | `workspaces/thumbnails/templates.json` / `workspaces/thumbnails/assets/**` | UI `/thumbnails` |
| Publish（外部SoT） | Google Sheets（`YT_PUBLISH_SHEET`） | 実行ログ: `workspaces/logs/**` | `python3 scripts/youtube_publisher/publish_from_sheet.py --run` |
| Benchmarks（チャンネル勝ちパターン） | `packages/script_pipeline/channels/CHxx-*/channel_info.json` の `benchmarks` | UI編集/監査: `/channel-settings` | UI `/channel-settings` / `python -m script_pipeline.tools.channel_registry ...` |

---

## 2) 確定E2Eフロー（何がどう流れるか）

> 詳細は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md` を正とし、本節は「迷わないための最短説明」を提供する。

### 2.1 Planning（企画）

1. 人間/UIで `workspaces/planning/channels/CHxx.csv` を更新（1行=1動画）
2. 汚染/欠落を決定論で検出:
   - `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
3. 必要ならL3混入クリーナ（慎重に）:
   - `python3 scripts/ops/planning_sanitize.py --channel CHxx --write-latest`（dry-run）→ 必要時 `--apply`

### 2.2 Script（台本生成）

- 正規入口（入口固定）: `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py <MODE> ...`（詳細: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` / `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`）
- 低レベルCLI（内部/詳細制御。通常運用では使わない）: `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli ...`
- ステージは概ね以下の順（SoTは `status.json`）:
  - `topic_research` → `script_outline` → `script_master_plan` → `chapter_brief` → `script_draft` → `script_review` → `script_validation`

重要（A/Bテキスト）
- Aテキスト（表示用）:
  - `content/assembled_human.md` が存在する場合は **これが正本**（人間編集版）
  - それ以外は `content/assembled.md` が正本
- Bテキスト（音声用）は `audio_prep/` 側に反映され、最終的に `workspaces/audio/final/**` が下流の正本になる

### 2.3 人間が台本を直した場合（Redoの盤石化）

編集の入口（推奨）
- UI: `/channels/:channelCode/videos/:video` の Script タブで A/B を編集して保存

保存時に必ず起こること（事故防止の固定ロジック）
- `assembled_human.md` を更新したら `assembled.md` を同内容でミラー同期する（分岐脳を防ぐ）
- `redo_audio=True` / `audio_reviewed=False` に戻す（音声は作り直し前提）
- `script_validation` を `pending` に戻す（古いvalidationのまま音声に進む事故を防ぐ）

詳細運用: `ssot/ops/OPS_SCRIPT_GUIDE.md`

### 2.4 Audio（TTS/SRT）

正規入口
- `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli audio --channel CHxx --video NNN`

正本の固定
- 下流（CapCut/AutoDraft/UI）は **必ず** `workspaces/audio/final/{CH}/{NNN}/` を読む
- `audio_prep/` は中間（残骸は規約に沿って削除してよい）

### 2.5 Video（SRT→画像→CapCut）

正規入口（代表）
- `PYTHONPATH=\".:packages\" python3 -m video_pipeline.tools.factory ...`
- `PYTHONPATH=\".:packages\" python3 -m video_pipeline.tools.auto_capcut_run ...`

正本の固定
- run単位の正本: `workspaces/video/runs/{run_id}/`
- run_dirは下流参照されるため、フロー確定前は削除禁止（整理は archive-first）

### 2.6 Thumbnails（独立動線）

- UI `/thumbnails` を主線とする（台本/音声/動画と独立）
- SoT: `workspaces/thumbnails/projects.json`
- Template SoT: `workspaces/thumbnails/templates.json`
- ローカル合成SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`（Compiler/retake/QC/明るさ補正）
- 入口CLI: `python scripts/thumbnails/build.py --help`
- Layer Specs（任意/段階導入）: 画像レイヤ・文字レイヤの設計はYAMLで管理できる（例: `workspaces/thumbnails/compiler/layer_specs/image_prompts_v3.yaml`, `workspaces/thumbnails/compiler/layer_specs/text_layout_v3.yaml`）。

### 2.7 Publish（外部SoT）

- `YT_PUBLISH_SHEET` を正本として、ローカルは参照/実行のみ
- `python3 scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`

### 2.8 Remotion（実験/未主線）

- 現状はCapCut主線の代替候補（未本番）
- 運用入口としては `python3 scripts/ops/render_remotion_batch.py` を固定

---

## 3) UIでできること（どのページが何のSoTを触るか）

起動（推奨）
- `bash scripts/start_all.sh start`
- ガード込みヘルスチェック: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards`
- UI配線（route↔API）: `ssot/ops/OPS_UI_WIRING.md`

主要ページ（抜粋）
- `/planning`: Planning CSV を閲覧/編集（SoT: `workspaces/planning/channels/**`）
- `/channels/:channelCode/videos/:video`: 台本/音声/SRTの確認と人間編集（SoT: `workspaces/scripts/**`, `workspaces/audio/final/**`）
- `/capcut-edit/*`: run_dir（動画生成）を扱う（SoT: `workspaces/video/runs/**`）
- `/image-management`: 画像variants/差し替え支援（SoT: `workspaces/video/runs/**`）
- `/thumbnails`: サムネ動線（SoT: `workspaces/thumbnails/projects.json`）
- `/image-model-routing`: 画像モデル（サムネ/動画内画像）をチャンネル別に指定（SoT: `workspaces/thumbnails/templates.json`, `packages/video_pipeline/config/channel_presets.json`）
- `/channel-settings`: チャンネル登録/監査/ベンチマーク編集（SoT: `channel_info.json` の `benchmarks` 等）
- `/agent-org` `/agent-board`: エージェント協調（board/locks/memos）
- `/llm-usage` `/reports`: ログ可視化

---

## 4) LLM/Agent運用（THINK MODE と artifact駆動）

方針
- APIが落ちても作業を止めないため、LLM呼び出しは **artifact駆動**で継続できるようにしている。

仕組み（要点）
- LLMが必要なステージは `workspaces/scripts/{CH}/{NNN}/artifacts/llm/*.json`（等）に **pending/ready** を残せる
- pending の場合は、Runbookに沿って人間/別エージェントが `content` を埋めて `ready` にして再実行する
- 申し送り/共同編集は `scripts/agent_org.py board ...` を使う（単一ファイルのboard）
- 並列衝突は lock で防ぐ（`python3 scripts/agent_org.py lock ...`）

Runbook入口
- `ssot/agent_runbooks/README.md`
- 共同運用のピン留め: `ssot/agent_runbooks/OVERVIEW_PINNED.md`

---

## 5) ログとクリーンアップ（散らからない仕組み）

ログの正本
- `workspaces/logs/`
- ログが「どこに溜まり、何が生成しているか」は `ssot/ops/OPS_LOGGING_MAP.md` が正本

重要な方針（例）
- L1（長期保持）: `llm_usage.jsonl`, `image_usage.log`, `tts_voicevox_reading.jsonl` など
- L3（短期保持）: UIプロセスログ、回帰ログ、swapキャッシュ等（ローテ対象）

クリーンアップ（統合入口）
- `python -m scripts.cleanup_workspace --dry-run ...` → OKなら `--run`
- 音声中間物: `python3 scripts/cleanup_audio_prep.py --dry-run` → OKなら `--run`
- ログローテ: `python3 scripts/ops/cleanup_logs.py --run`

削除の鉄則
- **“確実ゴミ”のみ削除**（基準: `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`）
- tracked削除は **archive-first**（`backups/graveyard/`）→ 削除 → `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に証跡

---

## 6) 標準運用テンプレ（新規/やり直し）

### 6.1 新規に1本作る（最短の主線）

1. Planning更新（CSV）→ lint
2. Script生成（必要なところまで）
   - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py new --channel CHxx --video NNN`
3. 人間チェック（必要ならUIで A/B 修正 → 保存）
4. `script_validation` を通す
   - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py resume --channel CHxx --video NNN`
5. Audio生成
   - `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli audio --channel CHxx --video NNN`
6. Video run を作る（CapCut）
7. Publish（外部SoTの承認後）

### 6.2 台本だけやり直す（事故らない）

- UIで A/B を編集して保存（redo/validationが自動で戻る）
- `script_validation` を再実行 → OKなら audio へ

### 6.3 音声以降だけやり直す（残骸も整理）

- 台本が変わっていないことを確認（Aが変わった場合は 6.2 を先に）
- `redo_audio=True` の対象を audio 再生成
- prep/chunks 等の残骸は規約に沿って削除（finalが揃っていることが条件）

---

## 7) リファクタリング/整理整頓のルール（壊さない）

- SSOTを先に更新（フロー/I-O/置き場の誤解を防ぐ）
- パス直書き禁止（必ず `factory_common.paths` を使う）
- 入口（CLI/UI）を守る（ルート直下の互換symlinkは作らない）
- 削除は archive-first + 証跡（`OPS_CLEANUP_EXECUTION_LOG`）
- 変更は小さくコミット（1コミット=1目的）
