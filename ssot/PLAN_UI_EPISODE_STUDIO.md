# PLAN_UI_EPISODE_STUDIO — 企画→台本→音声→動画をUIだけで完結させる統合スタジオ計画

## Plan metadata
- **Plan ID**: PLAN_UI_EPISODE_STUDIO
- **ステータス**: Active
- **担当/レビュー**: AI agent / Owner review
- **対象範囲 (In Scope)**: UI（React）+ UI Backend（FastAPI）の「エピソード中心」統合。既存API/既存ページを活かしつつ、一本道UX（企画→台本→音声→動画）を構築する。
- **非対象 (Out of Scope)**: 生成品質ロジックの大改造、Remotion本番化、既存SoTの破壊的変更（物理移動は別Planで段階実施）。
- **関連 SoT/依存**:
  - 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
  - I/Oスキーマ: `ssot/OPS_IO_SCHEMAS.md`
  - 入口一覧: `ssot/OPS_ENTRYPOINTS_INDEX.md`
  - ログ: `ssot/OPS_LOGGING_MAP.md`
  - パス統一: `ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`
  - ディレクトリ再編: `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`
- **最終更新日**: 2025-12-17

---

## 1. 背景（ユーザー課題）
- UIが散在し「どこで何をやるべきか」が分からない。
- エピソード（CHxx + video）単位での状態/成果物/ログが分断され、処理に戸惑う。
- 生成物・ログが散らかりやすく、SoTが曖昧になりやすい。

本Planの狙いは「**エピソード中心（Episode-centric）**」で、UIだけで“次に押すべきボタン”が分かり、実行/確認/復旧ができる状態を作ること。

---

## 2. 成果物 / Definition of Done

### 2.1 UIのDoD（ユーザー体験）
- **UIだけで** 企画→台本→音声→動画（CapCutドラフト）まで完結できる（サムネは別動線として同UI内で“独立タブ”で完結）。
- エピソード画面（Episode Studio）で以下が一画面（または同一ページ内タブ）で揃う:
  - 状態（ステージ/完了条件/ブロッカー）
  - 実行ボタン（安全な順序で）
  - 成果物プレビュー（Aテキスト/Bテキスト/音声/SRT/動画プロジェクト）
  - ログ（ジョブログ/最終log.json/失敗原因）
  - “次の一手”の明示（推奨アクション）
- 既存ページは残しつつ、Episode Studio から最短導線で到達できる（破壊的変更なし）。

### 2.2 BackendのDoD（I/O整合）
- UIが読む音声/SRT/ログの正本は **`workspaces/audio/final`**（互換: `audio_tts_v2/artifacts/final`。確定フロー準拠）。
- 動画プロジェクト/ジョブは **`/api/video-production/*`** を正として、UIが実行/ログ閲覧できる。
- “相対パス直組み”を極力排除し、パス解決は `factory_common/paths.py` を経由する。

---

## 3. UIの情報設計（最重要）

### 3.1 画面構成（最終形）
- **Episode Studio（新設/主戦場）**
  - URL案: `/studio?channel=CHxx&video=NNN`（または `/channels/:ch/videos/:video` の中に統合タブとして実装）
  - 目的: エピソードの“一本道”を提供し、実行/確認/復旧まで完結。
  - タブ（推奨）:
    1) Planning（企画CSV）
    2) Script（台本）
    3) Audio（TTS/字幕/品質/辞書）
    4) Video（CapCut主線: AutoDraft + VideoProduction）
    5) Logs（エピソード単位に集約表示）
    6) Thumbnail（独立動線）

既存 `WorkflowPage` は「入口のハブ」として残す（一本道の概観）。  
既存 `ChannelDetailPage` / `VideoDetailPanel` は段階的に Episode Studio へ吸収（互換維持）。

### 3.2 “次の一手”のロジック（UIの核）
エピソードに対し、UIは常に以下を出す:
- **Now（現状）**: どこまで出来ているか（Script/Audio/Video/Thumbnail）
- **Blockers（阻害要因）**: 何が無いと次へ行けないか（例: final SRTが無い）
- **Next（推奨アクション）**: いま押すべき 1–3 個のボタン
- **Recover（復旧）**: 失敗時の再試行/手動チェック/ログへの直リンク

判定の正本は `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md` と `ssot/OPS_ALIGNMENT_CHECKPOINTS.md`。

---

## 4. ステップ別 UI/SoT/I/O 契約（超重要）

### Phase A: Planning（企画）
- **SoT**: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
- **UI操作**:
  - 行の作成/編集（タイトル/タグ/作成フラグ/メモ等）
  - “Script初期化”の前提チェック（必須列が埋まっているか）
- **完了条件**:
  - 対象行が存在し、必要列が埋まっている

### Phase B: Script（台本）
- **SoT**: `workspaces/scripts/{CH}/{NNN}/status.json` + `content/assembled*.md`（互換: `script_pipeline/data/...`）
- **UI操作**:
  - ステージ状態の閲覧/更新（必要最小限）
  - assembled（人間編集版）編集・保存
  - 生成/再生成の実行（将来: UIから job_runner 経由で stage 実行）
- **完了条件**:
  - `content/assembled.md`（または human）が存在し、次のAudioへ進める

### Phase C: Audio（TTS/SRT）
- **SoT（参照正本）**: `workspaces/audio/final/{CH}/{NNN}/`（互換: `audio_tts_v2/artifacts/final/...`）
  - `{CH}-{NNN}.wav`, `{CH}-{NNN}.srt`, `log.json` 等
- **UI操作**:
  - 音声再生/更新時刻/品質メタ表示
  - SRTプレビュー（必要なら修正）
  - 辞書登録（誤読→即登録）
  - 整合性チェック（音声/SRT duration）
- **完了条件**:
  - final wav + final srt が存在

### Phase D: Video（CapCut主線）
動画は2導線をUIで統合して提示する。

#### D-1: AutoDraft（超高速・最短導線）
- **入力SoT**: final SRT（上記）
- **出力SoT**: `workspaces/video/runs/<run_id>/`（互換: `commentary_02_srt2images_timeline/output/...`）
- **UI操作**:
  - テンプレ/プロンプトの選択（通常はチャンネルpresetで自動）
  - 実行→stdout/stderr表示→run_dir表示
- **完了条件**:
  - run_dir が生成され、CapCutで開ける状態

#### D-2: VideoProduction（管理/編集/再実行に強い）
- **SoT**: `workspaces/video/runs/<project_id>/`（project単位。互換: `commentary_02_srt2images_timeline/output/...`）
- **UI操作**:
  - Project作成（SRTを指定してSoT dirを作成）
  - Job実行（analyze_srt → regenerate_images → validate_capcut → build_capcut_draft）
  - Jobログ閲覧（/jobs/{id}/log）
  - 画像差し替え、帯編集、CapCut設定微調整
- **完了条件**:
  - guard ok（整合通過）+ CapCut draft生成済み

### Phase F: Thumbnail（独立動線）
- **SoT**: `workspaces/thumbnails/projects.json`（独立・別動線。互換: `thumbnails/projects.json`）
- **UI操作**:
  - 候補表示、override設定、資産管理
- **完了条件**:
  - 対象エピソードに適切なサムネが紐付く

---

## 5. API設計（既存活用 + 最小追加）

### 5.1 既存の主なAPI（現状）
- エピソード詳細: `GET /api/channels/{ch}/videos/{video}`
- 音声/字幕取得: `GET /api/channels/{ch}/videos/{video}/audio|srt|log`
- Script pipeline（運用補助 / pipeline-boxes）:
  - `GET /api/channels/{ch}/videos/{video}/script-manifest`
  - `GET|PUT /api/channels/{ch}/videos/{video}/llm-artifacts/*`
  - `POST /api/channels/{ch}/videos/{video}/script-pipeline/reconcile`
  - `POST /api/channels/{ch}/videos/{video}/script-pipeline/run/script_validation`
- AutoDraft: `/api/auto-draft/*`
- VideoProduction: `/api/video-production/*`

### 5.2 “Episode Studio”向けに追加したい統合API（提案）
UIの複雑さを減らすため、フロントが複数APIを繋ぎ合わせる代わりに「統合状態API」を追加する。

- `GET /api/episode/{ch}/{video}`
  - 戻り値（例）:
    - planning summary（CSVの主要フィールド）
    - script summary（assembledの有無/更新時刻）
    - audio summary（final wav/srt/log の有無/更新時刻/品質）
    - video summary（関連project一覧、最新job、guard、capcut draft）
    - thumbnail summary（候補/override）
    - next_actions（UI表示用の推奨アクション配列）

このAPIは“表示専用”から開始し、実行系は既存 `/api/jobs`, `/api/auto-draft/create`, `/api/video-production/projects/*` を使う。

---

## 6. ログ統合（UIで迷わないための設計）

正本: `ssot/OPS_LOGGING_MAP.md`

### 6.1 UIが見せるべきログ（エピソード単位）
- Script: stage runner のlog（あれば）
- Audio: `workspaces/audio/final/.../log.json`（互換: `audio_tts_v2/artifacts/final/...`）
- AutoDraft: create の stdout/stderr（UIに保存するなら run_dir/logs に追記）
- VideoProduction: `/api/video-production/jobs/{jobId}/log`（ジョブごと）
- “どこに溜まるか”を UI に明示（パスと最終更新時刻）

### 6.2 収束先（Target）
- すべて `logs_root()/ui_hub/<domain>/...` へ集約（paths SSOT）
- run_dir にも “run固有ログ” は残す（再現性/監査用）

---

## 7. 実装フェーズ（小さく壊さず進める）

### Phase 0（Done）
- VideoProduction Workspace をUI導線に復帰し、プロジェクト作成UIを追加。
- UI Backend のパス解決を `factory_common.paths` に寄せ、音声/SRT/log の参照正本を final に統一。

### Phase 1（進行中）
- Episode Studio（リンク集 + 状態表示）を実装し、パイプラインの詰まりを UI で可視化/復旧できるようにする
  - ステージ詳細（error_codes / issues / fix_hints）表示
  - `Reconcile（status補正）` と `script_validation` の UI 実行ボタン

### Phase 2
- Episode Studio 内で実行系を統合（安全な順序）
  - Audio: run_tts のUI実行（jobs経由 or 専用API）
  - Video: create project → jobs一括実行

### Phase 3
- 統合状態API `GET /api/episode/{ch}/{video}` の導入（UXの単純化）

### Phase 4
- ログビュー統合（Episode Logs タブ）
- Artifact lifecycle（成功時の残骸削除/アーカイブ）をUIからも操作可能に（ガード付き）

---

## 8. リスクと対策
- **リスク**: SoTが複数箇所になりズレる（audio_prep vs final 等）
  - **対策**: “参照正本”をfinalに統一し、UIはfinalのみ読む（本Planの必須条件）。
- **リスク**: 旧ページ/旧APIが残り混乱
  - **対策**: Episode Studio を“唯一の推奨導線”にし、旧ページは「詳細/例外対応」と明示。
- **リスク**: 大規模移設で壊れる
  - **対策**: `factory_common/paths.py` で吸収し、物理移設は段階実施（`PLAN_REPO_DIRECTORY_REFACTOR.md`）。

---

## 9. 参照リンク（実装の現状ポイント）
- 入口: `apps/ui-frontend/src/pages/WorkflowPage.tsx`
- CapCutライン: `apps/ui-frontend/src/pages/CapcutEditPage.tsx`
- VideoProduction（UI）: `apps/ui-frontend/src/pages/ProductionPage.tsx`
- VideoProduction（中核）: `apps/ui-frontend/src/components/VideoProductionWorkspace.tsx`
- Backend（UI API）: `apps/ui-backend/backend/main.py`
- Backend（AutoDraft）: `apps/ui-backend/backend/routers/auto_draft.py`
- Backend（VideoProduction）: `apps/ui-backend/backend/video_production.py`
