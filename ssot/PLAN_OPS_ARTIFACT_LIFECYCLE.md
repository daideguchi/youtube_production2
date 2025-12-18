# PLAN_OPS_ARTIFACT_LIFECYCLE — 生成物/中間生成物のライフサイクルと自動クリーンアップ方針

## Plan metadata
- **Plan ID**: PLAN_OPS_ARTIFACT_LIFECYCLE
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `workspaces/scripts`（互換: `script_pipeline/data`）, `workspaces/audio`（互換: `audio_tts_v2/artifacts`）, `workspaces/video/{input,runs}`（互換: `commentary_02_srt2images_timeline/{input,output}`）, `apps/remotion/{input,out}`, `workspaces/logs/**`（互換: `logs/**`）等の生成物と中間生成物
- **非対象 (Out of Scope)**: 生成アルゴリズムそのものの変更、UI/CLIの新機能追加（cleanup 導線の薄い追加は含む）
- **関連 SoT/依存**: `ssot/DATA_LAYOUT.md`, `ssot/REFERENCE_ssot_このプロダクト設計について`, `scripts/cleanup_data.py`, `scripts/ops/cleanup_logs.py`, `audio_tts_v2/scripts/run_tts.py`
- **最終更新日**: 2025-12-12

## 1. 背景と目的
- 現状、各工程の**中間生成物が長期的に溜まり続ける**ため、ディスク肥大・探索ノイズ・誤参照が発生している。
- 既に `scripts/cleanup_data.py --run` で `audio_prep` と一部ログの削除はあるが、
  - `workspaces/audio/final/**/chunks/`（互換: `audio_tts_v2/artifacts/final/**/chunks/`）、
  - `workspaces/video/runs/<run>/`（互換: `commentary_02_srt2images_timeline/output/<run>/`）の大量 run、
  - remotion の `out/`,
  - `workspaces/logs/`（互換: `logs/`）直下の長期蓄積
  などは無秩序に残る。
- 目標は「**SoT/最終成果物は絶対に守りつつ**、再生成可能な中間物と一時ログを自動的に整理できる状態」を作ること。

## 2. 生成物の分類（Taxonomy）
全生成物を以下 4 層に分類し、保存/削除基準を固定する。

1. **L0: SoT / 永続正本**
   - 進捗・最終台本・最終音声・最終画像/動画・サムネ等。
   - 静的素材（git管理）: `asset/`（BGM/ロゴ/オーバーレイ/role assets 等）。
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
  - `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
  - `workspaces/planning/personas/*`
  - `workspaces/planning/templates/*`
  - `workspaces/planning/analytics/*`
- **ルール**
  - CSV/Persona/Template/Analytics は **無期限保持**。
  - `planning_updates_preview.csv` 等の preview は L2 として 30 日で削除可。

### 3.2 台本（workspaces/scripts）
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
  - `workspaces/scripts/{CH}/{VIDEO}/logs/`（動画別。互換: `script_pipeline/data/.../logs/`）
  - `workspaces/scripts/_state/logs/*.log`（互換: `script_pipeline/data/_state/logs/*.log`）

**削除/圧縮基準**
- `status.json.stage >= script_validation`（台本最終化）になったら:
  - L2 の `analysis/research` と `chapters*` を
    - `workspaces/scripts/_archive/<timestamp>/...` に**丸ごと移して圧縮**（zip）  
      もしくは
    - `--purge-intermediate` 指定時に削除。
- `status.json.stage >= audio_synthesis`（音声生成済み）になったら:
  - `content/outline.md` は L2→L1 に昇格（台本の再編集で参照価値が高い）。
- L3 ログは `scripts/cleanup_data.py --run --keep-days 14` を基準（workspaces/scripts の state logs）。

### 3.3 音声/TTS（workspaces/scripts/audio_prep + workspaces/audio/final）

- **L0/SoT**
  - `workspaces/audio/final/<CH>/<VIDEO>/CHxx-NNN.wav`（互換: `audio_tts_v2/artifacts/final/...`）
  - `workspaces/audio/final/<CH>/<VIDEO>/CHxx-NNN.srt`（互換: `audio_tts_v2/artifacts/final/...`）
  - `workspaces/audio/final/<CH>/<VIDEO>/log.json`（互換: `audio_tts_v2/artifacts/final/...`）
- **L1/Final**
  - `workspaces/audio/final/<CH>/<VIDEO>/a_text.txt`（互換: `audio_tts_v2/artifacts/final/...`）
  - `workspaces/audio/final/<CH>/<VIDEO>/b_text_with_pauses.txt`
  - `workspaces/audio/final/<CH>/<VIDEO>/kana_engine.json`
  - `workspaces/audio/final/<CH>/<VIDEO>/srt_blocks.json` / `tokens.json` 等（存在するもの）
- **L2/Intermediate**
  - `workspaces/scripts/<CH>/<VIDEO>/audio_prep/*`（tokens, chunks, pause_map, srt_entries 等。互換: `script_pipeline/data/...`）
  - `workspaces/audio/final/<CH>/<VIDEO>/chunks/*.wav`（互換: `audio_tts_v2/artifacts/final/.../chunks/`）
  - （未整備/将来）`workspaces/audio/runs/<engine>/<CH>/<VIDEO>/`（中間 run。旧: `audio_tts_v2/artifacts/audio/...`）
- **L3/Logs**
  - `workspaces/logs/tts_voicevox_reading.jsonl`（互換: `logs/tts_voicevox_reading.jsonl`）
  - `workspaces/logs/tts_llm_usage.log`（互換: `logs/tts_llm_usage.log`）
  - `audio_tts_v2/logs/*.log`（観測される。現行コード参照は薄く、legacy/adhoc の可能性が高い）

**削除/圧縮基準**
- `status.json.stage >= audio_synthesis` かつ `workspaces/planning/channels` の該当行が `audio: ready` になったら:
  - `workspaces/scripts/.../audio_prep/` は削除（現行 cleanup と同じ、ただし **ready 確認後に限定**）。
  - `workspaces/audio/final/.../chunks/` は削除（再生成可能・サイズ最大）。
  - `workspaces/audio/runs/...`（存在する場合）は last-run だけ残し、古い run は `workspaces/audio/_archive_audio/<timestamp>/` に移動。
- `audio: published` になったら:
  - `workspaces/audio/final/<CH>/<VIDEO>/` を `final.zip` に圧縮し、chunks 等は保持しない。

**現行の補助スクリプト（安全ガード付き）**
- `scripts/sync_audio_prep_to_final.py`: prep→final の不足ファイルのみ同期（上書きしない）
- `scripts/purge_audio_prep_binaries.py`: final が揃っている動画の audio_prep 直下 wav/srt を削除
- `scripts/cleanup_audio_prep.py`: audio_prep/chunks を削除（recent window で生成中を保護）
- `scripts/purge_audio_final_chunks.py`: final/chunks を削除（recent window で生成中を保護）

**現行の自動cleanup（UI/Backend 経由の TTS 成功時）**
- backend (`apps/ui-backend/backend/main.py:_run_audio_tts_v2`) は成功時にベストエフォートで以下を実行する:
  - `workspaces/scripts/.../audio_prep/chunks/` を削除（互換: `script_pipeline/data/...`）
  - `workspaces/scripts/.../audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ）を削除
  - `workspaces/audio/final/.../chunks/` を削除（互換: `audio_tts_v2/artifacts/final/...`）
    - 無効化: `YTM_TTS_KEEP_CHUNKS=1`

### 3.4 画像/動画ドラフト（workspaces/video/runs + commentary_02_srt2images_timeline）

- **L0/SoT**
  - `workspaces/video/runs/<run>/image_cues.json`（互換: `commentary_02_srt2images_timeline/output/<run>/...`）
  - `workspaces/video/runs/<run>/capcut_draft/`（採用ドラフト。互換: `commentary_02_srt2images_timeline/output/<run>/...`）
    - 備考: `capcut_draft` は symlink のことがある（target無=ドラフト未生成）。壊れたリンクは `scripts/ops/cleanup_broken_symlinks.py` で除去。
  - `workspaces/video/runs/<run>/episode_info.json`（互換: `commentary_02_srt2images_timeline/output/<run>/...`）
- **L1/Final**
  - `workspaces/video/runs/<run>/belt_config.json`
  - `workspaces/video/runs/<run>/auto_run_info.json`
  - `workspaces/video/runs/<run>/capcut_draft_info.json`
- **L2/Intermediate**
  - `workspaces/video/runs/<run>/assets/image/*`（draft 用コピー）
  - `workspaces/video/runs/<run>/chapters.json`, `sections.json`, `prompt_dump.json` 等
  - `workspaces/video/input/<CH>_<PresetName>/<CH>-<NNN>.{srt,wav}`（Audio final の**ミラー**。互換: `commentary_02_srt2images_timeline/input/...`）
    - 正本は `workspaces/audio/final/<CH>/<NNN>/`。`video/input` は **手動編集禁止**（混乱の原因）。
    - 同期: `python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs`
    - 不一致が見つかった場合は、古いコピーを `workspaces/video/_archive/<timestamp>/<CH>/video_input/` へ退避し、final を再同期して 1:1 を維持する。
- **L3/Logs**
  - `commentary_02_srt2images_timeline/logs/*`
  - `workspaces/video/runs/<run>/logs/*`（run単位ログ。run_dir と一緒に管理する。互換: `commentary_02_srt2images_timeline/output/<run>/logs/*`）
  - `workspaces/logs/llm_context_analyzer.log`（互換: `logs/llm_context_analyzer.log`）

**削除/圧縮基準**
- 1 video に対し run が複数ある場合:
  - 採用 run は `workspaces/scripts/{CH}/{NNN}/status.json` の `metadata.video_run_id` に記録する（SoT）
    - 補助: `python3 scripts/episode_ssot.py auto-select-run --channel CHxx --videos ...`
    - 未採用 run の退避: `python3 scripts/episode_ssot.py archive-runs --channel CHxx --all-selected --mode run`
  - 採用 run 以外は L2 として 30 日後に `workspaces/video/_archive/<timestamp>/` へ移動
- `video: published` になったら:
  - 採用 run は L1→L0 に昇格、不要な `assets/image/` と L2 JSON を削除。

### 3.5 サムネ（thumbnails）
- **L0/SoT**
  - `workspaces/thumbnails/projects.json`（互換: `thumbnails/projects.json`）
  - `workspaces/thumbnails/assets/<CH>/<VIDEO>/*`（互換: `thumbnails/assets/...`）
- **L2**
  - 未採用バリアント（projects.json で `archived` 扱い）  
    → 90 日後に `workspaces/thumbnails/_archive/<timestamp>/` に移動。

### 3.6 Remotion（remotion）
- **L1/Final**
  - `remotion/out/*`（書き出し mp4/manifest）
- **L2**
  - `remotion/input/*`（同期された入力コピー）
- **L3**
  - `remotion/node_modules/`（gitignore）

**削除/圧縮基準**
- `video: published` で `out/<project>/` を zip 化、入力は削除。

### 3.7 ルート logs/output（workspaces/logs 正本）
- **L3（原則）**
  - `workspaces/logs/*.log|jsonl|db`（互換: `logs/*`）
  - ルート `output/*` のテスト成果物（存在する場合。Legacy）
- **例外的に L1 として残すログ**
  - `workspaces/logs/audit_global_execution.log`（互換: `logs/audit_global_execution.log`）
  - `workspaces/logs/llm_usage.jsonl`（コスト分析。互換: `logs/llm_usage.jsonl`）
  - `workspaces/logs/tts_voicevox_reading.jsonl`（読みの追跡。互換: `logs/tts_voicevox_reading.jsonl`）

**削除基準**
- script_pipeline の state logs は 14 日（`scripts/cleanup_data.py --run --keep-days 14`）。
- `workspaces/logs/`（互換: `logs/`）の L3 は 30 日（`scripts/ops/cleanup_logs.py --run --keep-days 30`）。
- 例外 L1 は無期限保持。

### 3.8 静的アセット（asset/）
- **L0/SoT（絶対保持）**
  - `asset/**`（BGM/ロゴ/オーバーレイ/チャンネル別素材）
- **ルール**
  - `asset/` は生成物ではなく **gitで管理する静的素材の正本**。cleanup 対象外。
  - 参照例:
    - Remotion: `staticFile("asset/...")`
    - 画像run: role asset attach（`RoleAssetRouter`）

## 4. 自動クリーンアップ実装方針

### 4.1 コマンド設計（案）
新設: `python -m scripts.cleanup_workspace`

実装状況（2025-12-17）:
- `scripts/cleanup_workspace.py` を追加（audio/logs/scripts/video を統合）。
  - `workspaces/scripts/**/audio_prep/chunks/` の削除
  - `workspaces/scripts/**/audio_prep/{CH}-{NNN}.wav|.srt`（finalと重複するバイナリ）の削除
  - `workspaces/audio/final/**/chunks/` の削除
  - `--video-runs` で `scripts/ops/cleanup_video_runs.py` を呼び出し（run dir は削除せず `_archive/` へ移動）
  - `--logs` で `scripts/ops/cleanup_logs.py` を呼び出し（L3ログのローテ）
  - `--scripts` で `scripts/cleanup_data.py` を呼び出し（workspaces/scripts の古い中間生成物/ログ）
  - 既定は dry-run（`--run` 指定で実行、`--all --run` は `--yes` 必須）

```
python -m scripts.cleanup_workspace --channel CH06 --video 033 --dry-run
python -m scripts.cleanup_workspace --channel CH06 --video 033 --run

# video runs（runディレクトリ整理。削除ではなくアーカイブ）
python -m scripts.cleanup_workspace --video-runs --channel CH06 --video 033 --dry-run
python -m scripts.cleanup_workspace --video-runs --channel CH06 --video 033 --run

# video runs（unscoped/legacy run をまとめて退避する場合）
python -m scripts.cleanup_workspace --video-runs --all --dry-run --video-unscoped-only --video-archive-unscoped --video-archive-unscoped-legacy --keep-recent-minutes 1440
python -m scripts.cleanup_workspace --video-runs --all --run --yes --video-unscoped-only --video-archive-unscoped --video-archive-unscoped-legacy --keep-recent-minutes 1440

# logs（L3ローテ）
python -m scripts.cleanup_workspace --logs --dry-run
python -m scripts.cleanup_workspace --logs --run --logs-keep-days 30

# scripts（workspaces/scripts の L3/一部L2）
python -m scripts.cleanup_workspace --scripts --dry-run
python -m scripts.cleanup_workspace --scripts --run --scripts-keep-days 14

# 全体走査（危険なので --run は --yes 必須）
python -m scripts.cleanup_workspace --all --dry-run
python -m scripts.cleanup_workspace --all --run --yes
```

※ `--purge-level/--respect-status/--archive-dir` 等の高度なフラグは今後拡張（下の 4.2/7 を参照）。

### 4.2 ステータス連動
- `status.json.stage` と `workspaces/planning/channels` の進捗を組み合わせて安全判定。
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
1. `cleanup_workspace` の対象拡張（Video）とフラグ設計の確定
2. `status.json` と CSV の判定ロジックを共通 util 化（採用run/公開済み判定など）
3. dry-run で全チャンネルを走査し、削除候補のサイズ/件数をレポート（UIに表示できる形へ）
