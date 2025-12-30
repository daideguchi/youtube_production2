# OPS_CONFIRMED_PIPELINE_FLOW — 確定処理ロジック / 確定処理フロー（実態SoT）

この文書は「今このリポジトリで実際に動いている処理フロー」と「削除/移動の判定に必要な入出力（I/O）とSoT」を、コード実態とSSOTを突き合わせて確定したもの。  
リファクタリング/ゴミ判定は **必ず本フローとI/Oを正として行う**。

I/Oスキーマ（観測ベース）: `ssot/ops/OPS_IO_SCHEMAS.md`  
実行入口（CLI/スクリプト/UI）: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`  
Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`  
整合チェック: `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`  
ログ: `ssot/ops/OPS_LOGGING_MAP.md`

---

## 0. 用語 / SoT（Single Source of Truth）

### 0.1 SoTの階層
- **SoT（正本）**: そのフェーズの唯一の真実。以降の全処理はこれを参照する。
- **Mirror（ミラー）**: SoTをUI/集計用に写したもの。手動編集は原則禁止（手動時は同期ルールに従う）。
- **Artifacts（生成物）**: 中間/最終成果物。保持/削除ルールは `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md` が正本。

### 0.2 ルートの実体（現行）
- **Planning SoT**: `workspaces/planning/channels/CHxx.csv`
  - 企画/タイトル/タグ/リテイクフラグ等の正本。  
  - `packages/script_pipeline/tools/planning_store.py` が常にこれを都度読み込み。
- **Script SoT**: `workspaces/scripts/{CH}/{NNN}/status.json`
  - 台本生成のステージ状態とメタデータ正本。
  - `packages/script_pipeline/cli.py` / `packages/script_pipeline/runner.py` が更新。
- **Audio SoT**:
  - 生成中間: `workspaces/scripts/{CH}/{NNN}/audio_prep/`（strict run_tts の作業領域）
  - 最終参照正本: `workspaces/audio/final/{CH}/{NNN}/`
    - CapCut/AutoDraft/UI は **必ず final 配下を読む**。
- **Video SoT（CapCut/画像）**: `workspaces/video/runs/{run_id}/`
  - `image_cues.json` / `images/` / `belt_config.json` / `capcut_draft/` 等を含むrun単位の正本。
- **Remotion SoT（レンダリング/実験ライン）**: `apps/remotion/out/` + run_dir内の remotion関連JSON
  - コード/UI/preview は存在するが、**現行の本番運用では未使用（将来/研究用）**。CapCut主線の代替候補。
- **Thumbnail SoT**: `workspaces/thumbnails/projects.json`
  - サムネ案の追跡正本。UIはこれを読み書きする。
- **Assets SoT（静的素材 / git管理）**: `asset/`
  - BGM/ロゴ/オーバーレイ/チャンネル別 role assets 等の **静的素材の正本（L0）**。
  - 参照例: `apps/remotion`（`staticFile("asset/...")`）, `packages/video_pipeline` の role asset attach。
- **Publish SoT（Google Sheets）**: `YT_PUBLISH_SHEET`（外部SoT）
  - ローカル側は参照/反映のみ。  
  - 実装: `scripts/youtube_publisher/publish_from_sheet.py`

### 0.3 旧名/参照の注意
- 旧リポジトリ名/旧パスの参照が Docs/履歴に残ることがあるが、**現行コード/テストへ再導入しない**。  
- 棚卸し/監査の入口（正本）: `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`

---

## 1. グローバル確定ルール（全フェーズ共通）

### 1.1 .env / 環境変数ロード
- 秘密鍵・モデル設定は **リポジトリ直下 `.env` が正本**。  
- Python起動時: `scripts/with_ytm_env.sh <cmd>`（推奨）で `.env` を export する。`_bootstrap` は repo root/`packages` を sys.path に入れ、`.env` を fail-soft でロードする（CWD非依存）。  
- シェル/Node等: `scripts/with_ytm_env.sh <cmd>` を通して `.env` をexportして実行。
- 例外的に各パッケージ内 `.env` / `credentials/*` へ複製は禁止（`ssot/ops/OPS_ENV_VARS.md` 参照）。

### 1.2 run_id / run_dir
- Video/画像/CapCut/Remotion は **run単位で完結**。  
- run_dirは `workspaces/video/runs/{run_id}/`。  
  `{run_id}` は `CHxx-<video>` もしくは `jinsei220` のような人間が判別可能な名前を推奨。
- run_dirは **次フェーズの正本入力になるため、フローが確定するまで削除禁止**。

### 1.3 ステージ同期
- Planning CSV と status.json は別SoTだが **連動前提**。
- 同期ツールは旧名依存が残るため、現状は **人間がCSV更新/ステージリセットを運用で担保**。  
  ここは `PLAN_REPO_DIRECTORY_REFACTOR.md` の Stage 1–3 で統一予定。

---

## 2. フェーズ別 確定処理フロー / I/O

### Phase A. Planning（企画）

**Entry points**
- 人間が `workspaces/planning/channels/CHxx.csv` を更新。
- UI `/planning` でCSVを閲覧/編集（UIはCSVを直接読む）。
- 推奨（決定論lint）:
  - `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
    - タイトル/概要の `【tag】` 不一致や必須列欠落など、後工程で致命傷になる混入を早期に検出する。

**Inputs**
- `workspaces/planning/channels/CHxx.csv`（正本）
- `workspaces/planning/personas/CHxx_PERSONA.md`（人格/トーン）
- `configs/sources.yaml`（CSV/Persona/Promptの解決表。local override: `packages/script_pipeline/config/sources.yaml`）

**Outputs**
- 企画行の更新（タイトル/動画番号/タグ/リテイク/ステータス列 等）
- Scriptフェーズで使用する「最新企画コンテキスト」。

**Downstream dependencies**
- Script生成はCSVから動画番号/タイトル/タグ等を取り込むため、企画更新後は **必要ならScriptステージをresetして再生成**。

---

### Phase B. Script Pipeline（台本生成）

**Entry points**
- CLI（正規）:  
  - `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli init --channel CHxx --video NNN --title "<title>"`
  - `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli run --channel CHxx --video NNN --stage <stage>`
  - `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli next/run-all --channel CHxx --video NNN`
- UI（補助）: `/api/script-*` 系（main.py側でrunner呼び出し）

**補助ツール（推奨 / 事故防止）**
- Aテキスト決定論lint（反復/禁則混入の早期検出）:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`
- 長尺Aテキスト（セクション分割→合成）:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN`（dry-run。出力: `content/analysis/section_compose/`）
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`（反映＋`script_validation`をpending化して安全に再ゲート）
  - セクション単位の決定論バリデーションでNGなら **そのセクションだけ**自動再生成（最大N回、既定3）
  - 組み上げ後に禁則違反が残る場合は **組み上げのみ**再試行（最大M回、既定1）
  - 設計詳細: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`
- 超長尺Aテキスト（2〜3時間級 / 全文LLM禁止: Marathon）:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --plan-only`（planのみ）
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120`（dry-run: `content/analysis/longform/`）
  - `./scripts/with_ytm_env.sh python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`（canonical を上書き）
  - 設計詳細: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`

**SoT / Inputs**
- `workspaces/scripts/{CH}/{NNN}/status.json`（正本）
- `workspaces/scripts/{CH}/{NNN}/script_manifest.json`（契約。UI表示/検証の基礎）
  - schema: `ytm.script_manifest.v1`
- LLMテキスト出力SoT: `workspaces/scripts/{CH}/{NNN}/artifacts/llm/*.json`
  - schema: `ytm.llm_text_output.v1`
  - `status=pending` は未完（埋めて `ready` にしてから再実行）
- `workspaces/planning/channels/{CH}.csv`（企画SoT）
- `workspaces/planning/personas/{CH}_PERSONA.md`
- `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`（チャンネル固有の台本プロンプト）
- LLM設定:
  - `packages/factory_common/llm_router.py` が `.env` と `configs/llm_router*.yaml` / `configs/llm_task_overrides.yaml` を参照して task→tier→model を解決。
  - `LLM_MODE=api|think|agent` の2ルート（API実行 or pendingキュー）。

**Stages と Outputs（現行 stages.yaml）**
1. `topic_research`
   - Outputs:
     - `content/analysis/research/search_results.json` (required)  ※Web検索結果（hits=[] も許容）
     - `content/analysis/research/research_brief.md` (required)
     - `content/analysis/research/references.json` (required)
   - 注:
     - `references.json` は **空配列を許容**（検索無効/失敗時）。無関係なフォールバック出典は注入しない。
2. `script_outline`
   - Outputs:
     - `content/outline.md` (required)
3. `script_master_plan`
   - Outputs:
     - `content/analysis/master_plan.json` (required)
   - 注:
     - デフォルトは決定論（SSOT patterns）で作成する（LLMなし）。
     - 任意で高コスト推論（例: Opus）を **ここで1回だけ**使い、設計図サマリを補強できる（コスト暴走防止ガードあり）。
4. `chapter_brief`
   - Outputs:
     - `content/chapters/chapter_briefs.json` (required)
5. `script_draft`
   - Outputs:
     - `content/chapters/chapter_1.md` (required, 以後章数分増える想定)
6. `script_enhancement`
   - Outputs: なし（内容改善のみ）
7. `script_review`
   - Outputs:
     - `content/assembled.md` (required)  ※最終Aテキスト
     - `content/final/cta.txt` (optional)
     - `content/final/scenes.json` (optional)
8. `quality_check`
   - Outputs:
     - `content/analysis/research/quality_review.md` (required)
9. `script_validation`
   - Outputs: なし（status.json の stage details に結果を記録）
   - 役割: **Aテキスト品質ゲート**（SSOT: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`, `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`）
     - チェック対象: `content/assembled_human.md`（優先）→ `content/assembled.md`
     - ハード禁則（URL/脚注/箇条書き/番号リスト/見出し/区切り記号など）を検出したら **pending のまま停止**（事故防止のため自動修正しない）。修正後に再実行する。
     - 追加ゲート（必須）: **LLM Judge による品質判定**（機械判定ではなく推論で合否）。
       - NG の場合は **LLM Fixer** が “必要最小限の加除修正” を行い、再度 Judge を回す（既定: 最大3回。`SCRIPT_VALIDATION_LLM_MAX_ROUNDS=3`、コード上限=3）。
         - コスト優先なら `SCRIPT_VALIDATION_LLM_MAX_ROUNDS=2` に下げる（ただし不合格率が上がる）。
       - 目的: 「字数だけ満たす低品質」「不自然な流れ」「同趣旨の水増し」を構造的に排除する。
       - 証跡:
         - `status.json: stages.script_validation.details`（合否/統計/レポートパス）
         - `content/analysis/quality_gate/`（judge/fix レポート）
         - `content/analysis/quality_gate/judge_latest.json`（LLM Judgeの要点: summary/must_fix/nice_to_fix）
         - `content/analysis/alignment/semantic_alignment.json`（意味整合の要点: mismatch_points/fix_actions）
     - 企画↔台本の整合も検証する（alignment freshness gate）
       - `status.json: metadata.alignment` が missing/suspect/不一致（Planning行 or Aテキストが変更された）なら **pending のまま停止**
       - 修復: `./scripts/with_ytm_env.sh python3 scripts/enforce_alignment.py --channels CHxx --apply`（または `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli reconcile --channel CHxx --video NNN`）
     - タイトル/サムネ↔台本の意味整合も検証する（semantic alignment gate）
       - レポート: `content/analysis/alignment/semantic_alignment.json`
       - 既定では `verdict: major` のみ停止（ok/minor は合格）
         - minor/major は可能なら `script_validation` が最小リライトを自動適用して収束させる（収束しなければ停止）
         - 厳密にする場合（ok 以外は停止）: `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK=1`（コスト優先なら `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR=0` も推奨）
         - 数の約束（例: 「7つ」）は、台本側の `一つ目〜Nつ目` を決定論でサニティチェックし、LLMの誤判定で止まる事故を防ぐ。
       - 手動修復（最小リライト）:
         - major のみ修復: `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CHxx --video NNN --apply`
         - minor も修復: `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CHxx --video NNN --apply --also-fix-minor`
     - OKなら `status.json: stages.script_validation=completed` にし、Scriptフェーズ完了（`status=script_validated`）へ進む。
10. `audio_synthesis`（Audioフェーズ呼び出し口）
   - Outputs（参照先はAudio側で確定）:
     - `audio_prep/script_sanitized.txt` (required)
     - `audio_prep/chunks/` (optional)
     - `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav` (required, 正本)
     - `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt` (required, 正本)

**Downstream dependencies**
- Audioフェーズは `content/assembled.md` を入力とするため、Script確定後に進む。

---

### Phase C. Audio / TTS（音声・字幕生成）

**Entry points**
- CLI（正規）:
  - `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --channel CHxx --video NNN --input workspaces/scripts/CHxx/NNN/content/assembled.md`
  - `python -m script_pipeline.cli audio --channel CHxx --video NNN`（run_tts wrapper）
- UI（補助）:
  - `/api/redo` / `/api/channels/{ch}/videos/{no}/redo`（リテイク管理）
  - `/api/auto-draft/srt` でSRTをUI修正（final配下のみ許可）

**Inputs**
- Aテキスト: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - もし `assembled_human.md` が存在し内容差分があれば、run_tts が自動で `assembled.md` に同期（human版が正本）。
  - Planning ↔ Script の整合スタンプ（必須）:
    - `workspaces/scripts/{CH}/{NNN}/status.json: metadata.alignment.schema == "ytm.alignment.v1"`
    - `run_tts` は **無い/不一致なら停止**（誤台本で音声を作らないため）
    - 修復: `python scripts/enforce_alignment.py --channels CHxx --apply`（または `python -m script_pipeline.cli reconcile --channel CHxx --video NNN`）
  - Script品質ゲート（推奨=主線の安全ガード）:
    - `workspaces/scripts/{CH}/{NNN}/status.json: stages.script_validation.status == "completed"`
    - `run_tts` / `script_pipeline.cli audio` は未完了なら停止（例外が必要な場合のみ `--allow-unvalidated`）
  - **出典/脚注/URLなどのメタ情報を混入させない**（字幕に出る/読み上げる事故の根本原因）
    - 禁止例: `([戦国ヒストリー][13])` / `[13]` / `https://...` / `Wikipedia/ウィキペディア` を出典として直接書く表現
    - 出典は本文ではなく `content/analysis/research/references.json` 等へ集約する
    - 混入している場合は `scripts/sanitize_a_text.py` で退避→除去→同期してから再生成する
- LLM（読み/分割/ポーズ等）:
  - `packages/audio_tts/tts/llm_adapter.py` → `packages/factory_common/llm_router.py`（tasks: `tts_*`）
- Voiceエンジン:
  - VOICEVOX / Voicepeak / ElevenLabs を `packages/audio_tts/tts/routing.py` で決定。

**Outputs（確定）**
- 作業領域（中間正本）: `workspaces/scripts/{CH}/{NNN}/audio_prep/`
  - `{CH}-{NNN}.wav`, `{CH}-{NNN}.srt`, `log.json`, `chunks/` 等
  - **スクリプトが新しい場合は audio_prep を自動 purge**（run_ttsの確定ルール）
- 最終参照正本: `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`
  - `{CH}-{NNN}.srt`
  - `log.json`
  - `a_text.txt`（**実際に合成したTTS入力（=Bテキスト）のスナップショット**）
  - `audio_manifest.json`（契約。schema: `ytm.audio_manifest.v1`）
  - run_tts が必ず最新を同期するため、**下流はここだけ読めばよい**。

**CSV更新（運用）**
- `workspaces/planning/channels/{CH}.csv` の該当行を手動で更新:
  - 音声整形/検証/生成/品質の列を `済/完了 YYYY-MM-DD` に更新（`ssot/reference/【消さないで！人間用】確定ロジック.md` が正本）。

**Downstream dependencies**
- Video/CapCutは `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt` を入力SRTとして使う。

---

### Phase D. Video（SRT→画像→ベルト→CapCutドラフト）

**Entry points**
- CLI（正規/推奨）:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.factory ...`
- CLI（詳細制御）:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run --channel CHxx --srt <srt> --run-name <run_id> ...`
- UI:
  - `/api/auto-draft/*`（SRT選択→ドラフト生成）
  - `/api/video-production/*`（プロジェクト管理/画像再生成/ベルト編集/設定更新）

**Inputs**
- SRT正本: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt`
- チャンネルプリセット:
  - `packages/video_pipeline/config/channel_presets.json`
  - presetには capcut_template / layout / opening_offset / prompt_template / position / belt が定義。
- CapCutテンプレ:
  - `$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<template_dir>`
- 画像生成LLM/モデル:
  - `packages/video_pipeline/src/srt2images/orchestration/pipeline.py` が `SRT2IMAGES_IMAGE_MODEL` を channelで決定。

**内部順序（確定, auto_capcut_run）**
1. `run_pipeline` 実行（cue生成＋画像生成）
   - cues 計画の分岐:
     - 通常: Visual Bible → `LLMContextAnalyzer`（文脈分割）→ `PromptRefiner`（近傍整合）
     - THINK/AGENT（`LLM_MODE=think|agent`）または `SRT2IMAGES_CUES_PLAN_MODE=plan`:
       - `visual_image_cues_plan`（single-task）で区間計画→ cues 化
       - `PromptRefiner` はスキップ（stop/resume ループ回避）
		   - Outputs:
		     - `workspaces/video/runs/{run_id}/srt_segments.json`（SRTを決定論でパースしたsegments。plan/retimeの前提）
		     - `workspaces/video/runs/{run_id}/image_cues.json`
		     - `workspaces/video/runs/{run_id}/images/0001.png ...`
	     - `workspaces/video/runs/{run_id}/persona.txt` / `channel_preset.json`（存在時）
	     - `workspaces/video/runs/{run_id}/visual_cues_plan.json`（cues_plan 経路のみ。THINK/AGENT では status=pending の骨格が先に出る）
     - Quota失敗時: `RUN_FAILED_QUOTA.txt` を出力して明示停止。
	1.5. （任意）フリー素材B-roll注入（`--broll-provider`）
	   - 既定はOFF（`configs/sources.yaml: channels.CHxx.video_broll.enabled=false`）。ONにする場合の既定は provider=`pexels` / ratio=`0.2`（= 画像:フリー素材 8:2）。
	   - CLI指定（`--broll-provider/--broll-ratio`）がある場合は sources.yaml より優先される。
	   - 目的: “画像だけ”の単調さを避けるため、文脈に合う stock video（mp4）を全体の約20%だけ差し込む。
	   - 選定は `image_cues.json` の `visual_focus/summary` を使ったスコアリング（等間隔ではない）。
	   - CapCut挿入は `asset_relpath` があれば mp4 を優先し、動画はミュートで挿入する。
	   - Outputs:
	     - `workspaces/video/runs/{run_id}/broll/<provider>/*.mp4`
	     - `workspaces/video/runs/{run_id}/broll_manifest.json`（クレジット/デバッグ）
	   - 必要env（.envでOK）:
	     - `PEXELS_API_KEY`（pixel/pexels）
	     - `PIXABAY_API_KEY`（pixabay）
	     - `COVERR_API_KEY`（coverr）
	2. ベルト生成（belt_mode既定=auto）
	   - Outputs:
	     - `workspaces/video/runs/{run_id}/belt_config.json`（日本語4本が正）
	3. CapCut draft生成
	   - Outputs:
	     - `workspaces/video/runs/{run_id}/capcut_draft/`（テンプレ複製＋字幕/画像挿入）
	     - `capcut_draft_info.json`
4. タイトルJSON注入
   - タイトル優先順位: `--title` → `workspaces/planning/channels/{CH}.csv`（該当行の「タイトル」）→ LLM生成（最後のfallback）
   - Outputs:
     - `auto_run_info.json`（実行メタ/モデル/パラメータ）
	5. `timeline_manifest.json` 生成（可能な場合）
	   - 目的: run_dir の入力/出力/依存を “契約” として固定し、将来の移設・検証・UI表示の基礎にする。
	   - 生成条件: `workspaces/audio/final` の SRT/WAV が解決できる場合（失敗しても pipeline は止めない）
   - Outputs:
     - `timeline_manifest.json`

**確認ポイント（run_dir完成条件）**
- 最低限（CapCutドラフトとして成立）:
  - `image_cues.json`
  - `images/`
  - `capcut_draft`（CapCut projects への symlink）
  - `capcut_draft_info.json`
- 推奨（再現性/監査）:
  - `auto_run_info.json`（実行メタ）
  - `channel_preset.json`, `persona.txt`（存在する場合）
  - `srt_segments.json`（SRT→segments のSoT。入力取り違え検知に使う）
  - `visual_cues_plan.json`（cues plan のSoT。status=pending の場合は埋めてから再実行）
  - `timeline_manifest.json`（存在する場合）
- belt_mode を使う場合:
  - `belt_config.json`

**ズレ修正（保守ツール）**
- もし run_dir の `image_cues.json` が古いSRTから生成されていて、後から final の音声/字幕を入れるとタイムラインがズレる場合:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.align_run_dir_to_tts_final --run workspaces/video/runs/<run_id>`
  - LLMは呼ばず、cue.text と final SRT セグメントを文字列アラインして retime する（厳格チェック付き）

**CH06固有（CH06-テンプレ固定）**
- CH06 はテンプレのレイヤ構造（BGM多段・メイン帯・ドリーミー紙吹雪）を壊さないことが最優先。
- 推奨手順（画像タイムライン→音声/字幕の順でSoT反映）:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.rebuild_ch06_drafts_from_template --draft-root "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft" --template "CH06-テンプレ" --runs-root workspaces/video/runs --channel-csv workspaces/planning/channels/CH06.csv --videos 2-30`
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.patch_draft_audio_subtitles_from_manifest --run workspaces/video/runs/CH06-002_capcut_v1`（002〜030を同様に実行）
  - 仕上げ: `timeline_manifest.json`（wav/srt/cues整合）を確認し、CapCutで目視確認して mp4 書き出し。

**Downstream dependencies**
- **本番主線は CapCut ドラフト → CapCut側で mp4 書き出し**。
- Remotionラインも同run_dirを入力できるが、現行は実験/未使用扱い。
- 最終動画のアップロード/公開はPublishフェーズへ。

**After CapCut draft（手動/運用）**
- CapCutで draft を開き、必要な手動調整・画像差し替え・帯/字幕の目視確認を実施（詳細SOP: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`）。
- CapCutから最終 mp4 を書き出し（ローカル保存先は任意）。
- 完成 mp4 を Drive の `uploads/final` フォルダへアップロードし、URLを Publish Sheet の `Drive (final)` 列へ貼付する。
  - アップロード補助CLI: `python3 scripts/drive_upload_oauth.py --file <mp4>`（フォルダ変更時のみ `--folder <id>`）。

---

### Phase E. Remotion Render（実験/未使用ライン）

> 現行運用ではこのラインは使っていない。次フェーズは CapCut 書き出しが正規。Remotion は将来の自動レンダリング候補として保持。

**Entry points**
- CLI:
  - `node apps/remotion/scripts/render.js --run workspaces/video/runs/<run_id> --channel CHxx --title "<title>" --out apps/remotion/out/<name>.mp4`
  - `node apps/remotion/scripts/snapshot.js --run workspaces/video/runs/<run_id> ...`

**Inputs**
- run_dir:
  - `workspaces/video/runs/<run_id>/image_cues.json`
  - `workspaces/video/runs/<run_id>/images/`
  - `workspaces/video/runs/<run_id>/belt_config.json`（存在時）
  - `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt`（run_dirにコピー済みでない場合はCLI指定が必要。）
- レイアウト:
  - `packages/video_pipeline/config/channel_presets.json` の layout/position/belt
  - `apps/remotion/preset_layouts.json`（preset欠損時のフォールバック）

**Outputs**
- `apps/remotion/out/*.mp4`（最終動画）
- 欠損画像情報:
  - `workspaces/video/runs/<run_id>/remotion_missing_images.json`
  - `workspaces/video/runs/<run_id>/remotion_missing_images_snapshot.json`

---

### Phase F. Thumbnails（サムネ案生成・レビュー / 独立動線）

> サムネ動線は音声/CapCut/Remotionとは独立に運用する。Planning CSV は表示補助・在庫同期の補助情報にのみ利用。

**Entry points**
- UI `/thumbnails` タブ（React）  
- Backend:
  - `GET/PUT /api/thumbnails/*`
  - `POST /api/thumbnails/{ch}/{video}/assets`

**Inputs**
- `workspaces/thumbnails/projects.json`（正本）
- `workspaces/thumbnails/assets/{CH}/{video}/*`（画像実体, UIが配置）
- 企画CSVのタイトル/タグはUI表示補助に利用（正本はprojects.json）。

**Outputs**
- `projects.json` の status / variants / selected_variant_id 更新
- assets配下の画像保存

---

### Phase G. Publish（Drive→YouTube投稿）

**Entry points**
- `python3 scripts/youtube_publisher/publish_from_sheet.py [--max-rows N] [--run]`
  - `--run` 無しは dry-run。

**Inputs**
- 外部SoT:
  - Google Sheet `YT_PUBLISH_SHEET_ID` / `YT_PUBLISH_SHEET_NAME`
  - Status == `ready` かつ YouTube Video ID 空の行が対象。
- Drive(final) URL（シート列 `Drive (final)`）  
- OAuth:
  - `configs/drive_oauth_client.json`
  - `credentials/drive_oauth_token.json`
  - `credentials/youtube_publisher_token.json`

**Outputs**
- 一時DL: ローカル `tmp/yt_upload_*.bin`
- YouTubeアップロード（--run時のみ）
- Sheet書き戻し:
  - Status=`uploaded`
  - YouTube Video ID
  - UpdatedAt
- ローカル側の最終固定（推奨）:
  - Planning CSV の該当行を `進捗=投稿済み` にして **投稿済みロック**（以後は原則触らない指標）
  - UI: `Progress` 画面の `投稿済みにする（ロック）`（内部API: `POST /api/channels/{CH}/videos/{NNN}/published`）

---

### Phase H. Analytics / Ops（運用・監視）

**Entry points**
- LLM/音声/画像の利用集計: `scripts/aggregate_llm_usage.py`, `scripts/llm_usage_report.py`
- SRT整合/品質監査: `scripts/check_all_srt.sh`, `scripts/verify_srt_sync.py`, `scripts/audio_integrity_report.py`
- 旧/臨時ツール群: `scripts/*`（用途は個別README/ファイルヘッダ参照）

**Outputs**
- `workspaces/logs/` / `workspaces/scripts/llm_sessions.jsonl` / `workspaces/audio/final/*/log.json` などへ追記。

---

## 3. リテイク（redo）確定運用

- redoフラグは Planning CSV（正本）で管理し、Script/Audioの再実行対象を決める。
- デフォルト運用:
  - `redo_script=true`, `redo_audio=true`（未処理扱い）
  - 再生成完了後に false へ落とす。
- API/UI/CLI は `ssot/reference/【消さないで！人間用】確定ロジック.md` の規約に従う。

---

## 4. Legacy / 旧フローの扱い（ゴミ判定の基準）

### 4.1 Legacyとみなす根拠
- 実体の無い旧名/旧パス参照（現在は主に Docs/履歴に残存。コード/テストからは削除済み）。
- `workspaces/_scratch/`（ローカル作業）や `backups/graveyard/`（archive-first 退避）配下の試作/履歴。
- 既に削除済みの旧資産（旧PoC/旧静的ビルド等）は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` を正として扱う（復元は `backups/graveyard/`）。

### 4.2 ただし削除禁止のもの
- 現行コードやUIから参照されるディレクトリ/ファイルは **Legacyでも即削除不可**。
  - 例: `packages/video_pipeline/server/` は `apps/ui-backend/backend/video_production.py` が参照するため現行依存あり。

### 4.3 確実ゴミの定義（削除許可条件）
- ① 現行SoTフローのどのフェーズにも入力/参照されない  
- ② `rg` 等で参照ゼロが確認できる  
- ③ 人間が「不要」と明示確認  
→ この3条件が揃ったもののみ「確実ゴミ」として削除/legacy移動する。

---

## 5. 次アクション（本フローを前提にしたリファクタリング）

- `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md` の Stage 0–6 をこのSoTに沿って実施。  
- 最初にやるべきは **Path SSOT（packages/factory_common/paths.py）導入** と旧名参照の逐次消し込み。
