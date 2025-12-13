# PLAN_OPS_ARTIFACT_LIFECYCLE — 生成物/中間生成物のライフサイクルと自動クリーンアップ方針

## Plan metadata
- **Plan ID**: PLAN_OPS_ARTIFACT_LIFECYCLE
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `script_pipeline/data`, `audio_tts_v2/artifacts`, `commentary_02_srt2images_timeline/input|output`, `remotion/input|out`, `logs/**`, `output/**` 等の生成物と中間生成物
- **非対象 (Out of Scope)**: 生成アルゴリズムそのものの変更、UI/CLIの新機能追加（cleanup 導線の薄い追加は含む）
- **関連 SoT/依存**: `ssot/DATA_LAYOUT.md`, `ssot/REFERENCE_ssot_このプロダクト設計について`, `scripts/cleanup_data.py`, `scripts/ops/cleanup_logs.py`, `audio_tts_v2/scripts/run_tts.py`
- **最終更新日**: 2025-12-12

## 1. 背景と目的
- 現状、各工程の**中間生成物が長期的に溜まり続ける**ため、ディスク肥大・探索ノイズ・誤参照が発生している。
- 既に `scripts/cleanup_data.py --run` で `audio_prep` と一部ログの削除はあるが、
  - audio_tts_v2 の `chunks/` や `artifacts/audio/`、
  - commentary_02 の `output/<run>/` の大量 run、
  - remotion の `out/`,
  - ルート `logs/` 直下の長期蓄積
  などは無秩序に残る。
- 目標は「**SoT/最終成果物は絶対に守りつつ**、再生成可能な中間物と一時ログを自動的に整理できる状態」を作ること。

## 2. 生成物の分類（Taxonomy）
全生成物を以下 4 層に分類し、保存/削除基準を固定する。

1. **L0: SoT / 永続正本**
   - 進捗・最終台本・最終音声・最終画像/動画・サムネ等。
   - **削除禁止**。移設/アーカイブ時は必ずバックアップ + 履歴ログ。

2. **L1: Final Artifacts（最終成果物）**
   - パイプラインが下流に渡す「使われる成果物」。
   - 原則無期限保持。ただし published 後の一部は圧縮/移設可。

3. **L2: Rebuildable Intermediates（再生成可能な中間物）**
   - Outline/chapters/segments/chunks/画像の中間 JSON など。
   - 再生成のコストと実務上の必要性を見て、**ステータス/期間で削除 or 圧縮**。

4. **L3: Ephemeral Logs/Caches（一時ログ/キャッシュ）**
   - jsonl, *.log, temp ファイル, node_modules/out など。
   - **日数ローテーションで削除**。重要ログのみ L1 として残す。

## 3. ドメイン別ライフサイクル

### 3.1 企画/進捗（planning）
- **L0/SoT**
  - `progress/channels/CHxx.csv`
  - `progress/personas/*`
  - `progress/templates/*`
  - `progress/analytics/*`
- **ルール**
  - CSV/Persona/Template/Analytics は **無期限保持**。
  - `planning_updates_preview.csv` 等の preview は L2 として 30 日で削除可。

### 3.2 台本（script_pipeline/data）
`script_pipeline/stages.yaml` の生成物を基準に保存レベルを定義。

- **L0/SoT（絶対保持）**
  - `status.json`
  - `content/final/assembled.md`（存在しない場合は `content/assembled.md` を正本とみなす）
- **L1/Final**
  - `content/assembled.md`
  - `content/final/scenes.json`, `content/final/cta.txt`
- **L2/Intermediate**
  - `content/analysis/research/*`（research_brief, references, quality_review 等）
  - `content/outline.md`
  - `content/chapters/*`
  - `content/chapters/chapter_briefs.json`
  - `content/chapters_formatted/*`（もし残っている場合）
- **L3/Logs**
  - `logs/`（動画別）
  - `data/_state/logs/*.log`

**削除/圧縮基準**
- `status.json.stage >= script_validation`（台本最終化）になったら:
  - L2 の `analysis/research` と `chapters*` を
    - `data/_archive/<timestamp>/content/*` に**丸ごと移して圧縮**（zip）  
      もしくは
    - `--purge-intermediate` 指定時に削除。
- `status.json.stage >= audio_synthesis`（音声生成済み）になったら:
  - `content/outline.md` は L2→L1 に昇格（台本の再編集で参照価値が高い）。
- L3 ログは `scripts/cleanup_data.py --run --keep-days 14` を基準（script_pipeline の state logs）。

### 3.3 音声/TTS（script_pipeline/audio_prep + audio_tts_v2/artifacts）

- **L0/SoT**
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/CHxx-NNN.wav`
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/CHxx-NNN.srt`
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/log.json`
- **L1/Final**
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/a_text.txt`
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/b_text_with_pauses.txt`
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/kana_engine.json`
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/srt_blocks.json` / `tokens.json` 等（存在するもの）
- **L2/Intermediate**
  - `audio_tts_v2/artifacts/audio/<engine>/<CH>/<VIDEO>/`（中間 run）
  - `audio_tts_v2/artifacts/final/<CH>/<VIDEO>/chunks/*.wav`
  - `script_pipeline/data/<CH>/<VIDEO>/audio_prep/*`（tokens, chunks, pause_map, srt_entries 等）
- **L3/Logs**
  - `logs/tts_voicevox_reading.jsonl`
  - `logs/tts_llm_usage.log`
  - `audio_tts_v2/logs/*.log`（観測される。現行コード参照は薄く、legacy/adhoc の可能性が高い）

**削除/圧縮基準**
- `status.json.stage >= audio_synthesis` かつ `progress/channels` の該当行が `audio: ready` になったら:
  - `script_pipeline/.../audio_prep/` は削除（現行 cleanup と同じ、ただし **ready 確認後に限定**）。
  - `artifacts/final/.../chunks/` は削除（再生成可能・サイズ最大）。
  - `artifacts/audio/...` は last-run だけ残し、古い run は `artifacts/_archive_audio/<timestamp>/` に移動。
- `audio: published` になったら:
  - `artifacts/final/<CH>/<VIDEO>/` を `final.zip` に圧縮し、chunks 等は保持しない。

**現行の補助スクリプト（安全ガード付き）**
- `scripts/sync_audio_prep_to_final.py`: prep→final の不足ファイルのみ同期（上書きしない）
- `scripts/purge_audio_prep_binaries.py`: final が揃っている動画の audio_prep 直下 wav/srt を削除
- `scripts/cleanup_audio_prep.py`: audio_prep/chunks を削除（recent window で生成中を保護）
- `scripts/purge_audio_final_chunks.py`: final/chunks を削除（recent window で生成中を保護）

**現行の自動cleanup（UI/Backend 経由の TTS 成功時）**
- backend (`apps/ui-backend/backend/main.py:_run_audio_tts_v2`) は成功時にベストエフォートで以下を実行する:
  - `script_pipeline/.../audio_prep/chunks/` を削除
  - `script_pipeline/.../audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ）を削除
  - `audio_tts_v2/artifacts/final/.../chunks/` を削除
    - 無効化: `YTM_TTS_KEEP_CHUNKS=1`

### 3.4 画像/動画ドラフト（commentary_02_srt2images_timeline）

- **L0/SoT**
  - `commentary_02_srt2images_timeline/output/<run>/image_cues.json`
  - `commentary_02_srt2images_timeline/output/<run>/capcut_draft/`（採用ドラフト）
  - `commentary_02_srt2images_timeline/output/<run>/episode_info.json`
- **L1/Final**
  - `output/<run>/belt_config.json`
  - `output/<run>/auto_run_info.json`
  - `output/<run>/capcut_draft_info.json`
- **L2/Intermediate**
  - `output/<run>/assets/image/*`（draft 用コピー）
  - `output/<run>/chapters.json`, `sections.json`, `prompt_dump.json` 等
  - `input/<channel>/<video>.*`（SRT/音声同期済みのコピー）
- **L3/Logs**
  - `commentary_02_srt2images_timeline/logs/*`
  - `commentary_02_srt2images_timeline/output/<run>/logs/*`（run単位ログ。run_dir と一緒に管理する）
  - `logs/llm_context_analyzer.log`

**削除/圧縮基準**
- 1 video に対し output run が複数ある場合:
  - `progress/channels` で採用 run を `video_run_id` として SoT に記録する。
  - 採用 run 以外は L2 として 30 日後に `output/_archive/<timestamp>/` へ移動。
- `video: published` になったら:
  - 採用 run は L1→L0 に昇格、不要な `assets/image/` と L2 JSON を削除。

### 3.5 サムネ（thumbnails）
- **L0/SoT**
  - `thumbnails/projects.json`
  - `thumbnails/assets/<CH>/<VIDEO>/*`
- **L2**
  - 未採用バリアント（projects.json で `archived` 扱い）  
    → 90 日後に `thumbnails/_archive/<timestamp>/` に移動。

### 3.6 Remotion（remotion）
- **L1/Final**
  - `remotion/out/*`（書き出し mp4/manifest）
- **L2**
  - `remotion/input/*`（同期された入力コピー）
- **L3**
  - `remotion/node_modules/`（gitignore）

**削除/圧縮基準**
- `video: published` で `out/<project>/` を zip 化、入力は削除。

### 3.7 ルート logs/output
- **L3（原則）**
  - ルート `logs/*.log|jsonl|db`
  - ルート `output/*` のテスト成果物
- **例外的に L1 として残すログ**
  - `logs/audit_global_execution.log`
  - `logs/llm_usage.jsonl`（コスト分析）
  - `logs/tts_voicevox_reading.jsonl`（読みの追跡）

**削除基準**
- script_pipeline の state logs は 14 日（`scripts/cleanup_data.py --run --keep-days 14`）。
- ルート `logs/` の L3 は 30 日（`scripts/ops/cleanup_logs.py --run --keep-days 30`）。
- 例外 L1 は無期限保持。

## 4. 自動クリーンアップ実装方針

### 4.1 コマンド設計（案）
新設: `python -m scripts.cleanup_workspace`
```
cleanup_workspace \
  --channel CH06 --video 033 \
  --mode {dry-run|run} \
  --purge-level {L2,L3,all} \
  --respect-status \
  --keep-last-runs 1 \
  --archive-dir workspaces/_archive/<date>
```

### 4.2 ステータス連動
- `status.json.stage` と `progress/channels` の進捗を組み合わせて安全判定。
- **削除条件が満たされない限り purge しない**（dry-run で差分確認）。

### 4.3 既存 cleanup の置き換え
- `scripts/cleanup_data.py` は L3/一部L2の簡易版として残すが、
  - 新 cleanup が安定したら **本体に統合**し、cron も新コマンドへ移行。

## 5. 既存ディレクトリ再編との整合（PLAN_REPO_DIRECTORY_REFACTOR 連動）
- `workspaces/` へ移設後のパスをこの計画の正本にする。
- cleanup は `workspaces/*` のみを対象にし、`packages/` 配下には触れない設計にする。

## 6. リスクと対策
- **誤削除リスク**  
  → stage/CSV によるガード、dry-run 既定、archive-first。
- **再生成コスト増**  
  → L2 の purge は published/ready 後に限定。必要時は keep-last-runs を増やす。
- **パス移設中の二重管理**  
  → 互換 symlink 期間中は cleanup 対象を新パスに限定する。

## 7. 次のアクション
1. `cleanup_workspace` の設計確定（flag/対象/ガード）
2. `status.json` と CSV の判定ロジックを共通 util 化
3. dry-run で全チャンネルを走査し、削除候補のサイズ/件数をレポート
