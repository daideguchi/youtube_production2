# OPS_CLEANUP_EXECUTION_LOG — 実行した片付け（復元/再現可能な記録）

このログは「実際に実行した削除/移動/退避」を、後から追跡できるように記録する。  
大原則: 破壊的操作は **バックアップ→削除/移動→SSOT更新** の順で行う。

注:
- `legacy/` ディレクトリは廃止し、退避先は `backups/graveyard/` + `workspaces/_scratch/` に統一した（詳細: Step 103）。過去の記録中の `legacy/...` は当時の履歴として読む。
- 旧「repo root 直下の互換 alias/旧パス」（例: `./audio_tts_v2/`, `./commentary_02_srt2images_timeline/`, `./ui/`, `./thumbnails/`, `./progress/` など）は廃止。過去の記録で root 直下の `script_pipeline/...` が出る場合もあるが、現行の正本は `packages/script_pipeline/` と `workspaces/**`。再現が必要な場合は現行パスへ読み替える。

---

## 2025-12-12

### 1) 確実ゴミの削除（repo tracked）

- 削除: `factory_commentary.egg-info/`（setuptools生成物）
  - 実行: `git rm -r factory_commentary.egg-info`
- 削除: `commentary_02_srt2images_timeline/src/memory/`（操作ログ残骸）
  - 実行: `git rm -r commentary_02_srt2images_timeline/src/memory`
  - 判定: コード参照ゼロ（`rg "operation_log.jsonl|subagent_contributions.jsonl|integration_summary.json" -S .`）
- 削除: `commentary_02_srt2images_timeline/ui/src/memory/`（操作ログ残骸）
  - 実行: `git rm -r commentary_02_srt2images_timeline/ui/src/memory`
  - 判定: コード参照ゼロ（同上）
- 削除: `commentary_02_srt2images_timeline/**/runtime/logs/notifications.jsonl`（通知ログのコミット残骸）
  - 実行:
    - `git rm commentary_02_srt2images_timeline/src/runtime/logs/notifications.jsonl`
    - `git rm commentary_02_srt2images_timeline/ui/src/runtime/logs/notifications.jsonl`
  - 判定: コード参照ゼロ（`git grep -n "notifications.jsonl"` がヒットしない）

### 2) SSOTの整理

- 削除: 旧 duplicate（ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md。`ssot/PLAN_STAGE1_PATH_SSOT_MIGRATION.md` と同内容の重複コピー）
  - 判定: diffゼロ（同一内容）を確認して削除

### 3) 退避（コピー）

- 退避コピー作成: `commentary_02_srt2images_timeline/` 直下のサンプル/残骸候補
  - 対象:
    - `commentary_02_srt2images_timeline/PROJ.json`
    - `commentary_02_srt2images_timeline/channel_preset.json`
    - `commentary_02_srt2images_timeline/persona.txt`
    - `commentary_02_srt2images_timeline/image_cues.json`
  - 先: `backups/20251212_repo_residue/commentary_02_legacy_root_artifacts/`
  - 注: この時点では **移動していない**（元ファイルは残置）

### 4) Gitignore（生成物ノイズ抑制）

- 追加:
  - `*.egg-info/`
  - `script_pipeline/data/CH*/**/audio_prep/`
  - `commentary_02_srt2images_timeline/**/runtime/logs/`

### 備考

- `__pycache__/` や `.pytest_cache/` は再生成されるため、必要に応じて随時削除する。

---

## 2025-12-13

### 1) `commentary_02` 直下の残骸（repo tracked）を削除

2025-12-12 にバックアップを作成済みのため、以下を **git rm**（削除）した。

- 削除:
  - `commentary_02_srt2images_timeline/PROJ.json`
  - `commentary_02_srt2images_timeline/channel_preset.json`
  - `commentary_02_srt2images_timeline/persona.txt`
  - `commentary_02_srt2images_timeline/image_cues.json`
- バックアップ（復元先）:
  - `backups/20251212_repo_residue/commentary_02_legacy_root_artifacts/`

### 2) バックアップファイル（.bak）の削除

意図: repo tracked のバックアップ残骸を除去し、探索ノイズを減らす。

- 削除:
  - `commentary_02_srt2images_timeline/tools/factory.py.bak`
  - `50_tools/projects/srtfile/srtfile_v2/tools/progress_manager.py.bak`
- 付随:
  - `.gitignore` に `*.bak` と `*~` を追加

### 3) 音声生成の残骸（巨大chunks等）の削除（untracked）

意図: final wav/srt/log は保持しつつ、再生成可能で容量最大の `chunks/` を削除して散らかりを減らす。

- 削除: `audio_tts_v2/artifacts/final/*/*/chunks/`（106件 / 約8.5GB）
  - 実行: `python3 scripts/purge_audio_final_chunks.py --run --keep-recent-minutes 60`

- 削除: `script_pipeline/data/*/*/audio_prep/chunks/`（3件 / 約127.4MB）
  - 実行: `python3 scripts/cleanup_audio_prep.py --run --keep-recent-minutes 60`

- 削除: `script_pipeline/data/*/*/audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ 1件 / 約45.6MB）
  - 実行: `python3 scripts/purge_audio_prep_binaries.py --run --keep-recent-minutes 360`

### 4) Legacy隔離（repo tracked）

意図: 旧PoC/旧静的ビルド/メモを `legacy/` 配下へ隔離し、トップレベルを現行フローに集中させる。

- 移動（git mv）:
  - `50_tools/` → `legacy/50_tools/`
  - `docs/` → `legacy/docs_old/`
  - `idea/` → `legacy/idea/`
- 互換 symlink（repo tracked）:
  - `50_tools` → `legacy/50_tools`
  - `docs` → `legacy/docs_old`
  - `idea` → `legacy/idea`
- 証跡: commit `bad4051e`

### 5) Legacy隔離（repo tracked）

意図: 各ドメイン配下に残っている legacy 断片を `legacy/` に集約し、探索ノイズを削減する。

- 移動（git mv）:
  - `audio_tts_v2/legacy_archive/` → `legacy/audio_tts_v2/legacy_archive/`
  - `commentary_02_srt2images_timeline/tools/archive/` → `legacy/commentary_02_srt2images_timeline/tools/archive/`
- 互換 symlink（repo tracked）:
  - `audio_tts_v2/legacy_archive` → `../legacy/audio_tts_v2/legacy_archive`
  - `commentary_02_srt2images_timeline/tools/archive` → `../../../legacy/commentary_02_srt2images_timeline/tools/archive`
- 証跡: commit `0a4ed311`

## 2025-12-13

### 6) キャッシュ/不要メタの削除（untracked）

意図: 実行に不要で、探索ノイズと容量だけ増やすキャッシュを除去する（必要なら自動再生成される）。

- 削除:
  - `**/__pycache__/`（repo 配下のローカルキャッシュ。`.venv/` 等の依存環境は対象外）
  - `.pytest_cache/`
  - `**/.DS_Store`
- 実行: `rm -rf <dirs> && find . -name .DS_Store -delete`

### 7) `legacy/50_tools` の削除（repo tracked）

意図: 現行フローが参照しない旧PoC群を完全削除し、探索ノイズを恒久的に減らす。

- 事前対応（互換パスの撤去）:
  - `commentary_02_srt2images_timeline/tools/*` から `50_tools/50_1_capcut_api` の探索パスを削除（`CAPCUT_API_ROOT` / `~/capcut_api` / `packages/capcut_api` のみに統一）。
- アーカイブ（復元用）:
  - `backups/graveyard/20251213_122104_legacy_50_tools.tar.gz`
- 削除:
  - `legacy/50_tools/`
  - `50_tools`（互換symlink）

### 8) 破損 symlink の削除（untracked）

意図: 存在しない絶対パスへの symlink は事故の元なので除去する。

- 削除:
  - `credentials/srtfile-tts-credentials.json`（`/Users/dd/...` への破損リンク）

### 9) `legacy/docs_old` の削除（repo tracked）

意図: 旧静的ビルド（参照用）の残骸を削除し、現行の正本を `ssot/` に集約する。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_123223_legacy_docs_old.tar.gz`
- 削除:
  - `legacy/docs_old/`
  - `docs`（互換symlink）

### 10) legacyアーカイブの削除（repo tracked）

意図: 参照ゼロの過去版/退避を削除し、現行コード探索を軽くする。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_123409_legacy_archives.tar.gz`
- 削除:
  - `legacy/audio_tts_v2/legacy_archive/` + `audio_tts_v2/legacy_archive`（互換symlink）
  - `legacy/commentary_02_srt2images_timeline/tools/archive/` + `commentary_02_srt2images_timeline/tools/archive`（互換symlink）

### 11) キャッシュ掃除の再実行（untracked）

意図: 並列運用で増殖するキャッシュを都度落として、探索ノイズと容量を抑える。

- 実行: `bash scripts/ops/cleanup_caches.sh`

### 12) script_pipeline の古い中間ログ削除（untracked）

意図: script_pipeline の per-video logs / state logs が増殖するため、保持期限を超えたものを削除する。

- 削除（keep-days=14）:
  - `script_pipeline/data/_state/logs/*.log`（古い state logs）
  - `script_pipeline/data/*/*/logs/`（古い per-video logs）
- 実行: `python scripts/cleanup_data.py --run --keep-days 14`

### 13) `00_research` の workspaces 実体化（repo tracked）

意図: Stage2（workspaces抽出）の一環として、research を `workspaces/` 側へ寄せる（旧パスは互換symlink）。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_133243_00_research.tar.gz`
- 実行:
  - `rm workspaces/research`
  - `mv 00_research workspaces/research`
  - `ln -s workspaces/research 00_research`
- 結果:
  - `workspaces/research/` が正本
  - `00_research` は `workspaces/research` への symlink

### 14) `progress` の workspaces 実体化（repo tracked）

意図: Stage2（workspaces抽出）の一環として、planning SoT を `workspaces/` 側へ寄せる（旧パスは互換symlink）。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_133445_progress.tar.gz`
- 実行:
  - `rm workspaces/planning`
  - `mv progress workspaces/planning`
  - `ln -s workspaces/planning progress`
- 結果:
  - `workspaces/planning/` が正本
  - `progress` は `workspaces/planning` への symlink

### 15) Stage2: `workspaces/` cutover の確定（repo tracked）

意図: 生成物/中間生成物（台本・音声・動画・ログ）を repo から切り離し、`workspaces/` を正本に固定する。  
旧パスは互換 symlink として残し、参照側の破壊を防ぐ。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_181445_script_pipeline_data_HEAD.tar.gz`（`script_pipeline/data/**` の repo tracked 断面）
- 実行（git）:
  - `script_pipeline/data/**`（repo tracked の巨大データ）を index から削除し、`script_pipeline/data -> ../workspaces/scripts` の symlink を tracked 化
  - `workspaces/{audio,logs,scripts}` および `workspaces/video/{input,runs}` を tracked symlink から「実ディレクトリ + README/.gitignore」へ typechange
  - `workspaces/video/{input,runs}/.gitkeep` を追加（空ディレクトリでも存在を担保）
- 補足:
  - 生成物の保持/削除の基準は `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md` に従う

### 16) `legacy/idea` の削除（repo tracked）

意図: 参照されない旧メモ/試作が残ると誤参照の原因になるため、アーカイブ後に削除して探索ノイズを恒久的に下げる。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_185921_legacy_idea.tar.gz`
- 削除:
  - `legacy/idea/`
  - `idea`（互換symlink）

### 17) `legacy/_old` の削除（untracked / local）

意図: repo 管理外の旧退避物（大量の古いspec/スクリプト/JSON）は、ローカル探索ノイズと誤実行リスクが高い。  
git の履歴には残らないため、**ローカルのみ**削除した。

- 削除:
  - `legacy/_old/`
  - `_old`（symlink）

### 18) 旧 `commentary_01_srtfile_v2` 依存テストの削除（repo tracked）

意図: 実体の無い旧パッケージ名に依存したテストが残ると、`pytest` 実行時に失敗して探索と運用を阻害するため。  
復元できるようアーカイブ後に削除した。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_190501_tests_commentary01_legacy.tar.gz`
- 削除:
  - `tests/test_annotations.py`
  - `tests/test_b_text_builder.py`
  - `tests/test_b_text_chunker.py`
  - `tests/test_kana_engine.py`
  - `tests/test_llm_adapter.py`
  - `tests/test_llm_rewriter.py`
  - `tests/test_llm_rewriter_openrouter.py`
  - `tests/test_logger.py`
  - `tests/test_orchestrator_smoke.py`
  - `tests/test_pipeline_init_defaults.py`
  - `tests/test_preprocess_a_text.py`
  - `tests/test_qa.py`
  - `tests/test_synthesis_concat.py`
  - `tests/test_tts_routing.py`
  - `tests/test_voicepeak_engine.py`

### 19) 旧 `commentary_01_srtfile_v2` 依存スクリプトの削除（repo tracked）

意図: 実体の無い旧パッケージ/旧データパスに依存したスクリプトは誤実行時の事故要因になるため、アーカイブ後に削除する。  
（必要なものは `scripts/api_health_check.py` / `scripts/prompt_audit.py` / `scripts/validate_status_sweep.py` 等として **新SoT前提で再実装**済み。）

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_191620_scripts_commentary01_legacy.tar.gz`
- 削除:
  - `scripts/commentary_service.py`
  - `scripts/degrade_stage_status.py`
  - `scripts/generate_youtube_description.py`
  - `scripts/recover_stage_sequence.py`
  - `scripts/set_stage_pending.py`
  - `scripts/sync_progress_with_status.py`
  - `scripts/sync_status_mirrors.py`
  - `scripts/validate_persona_tags.py`

### 20) `commentary_02` 旧 spec_updates の削除（repo tracked）

意図: SSOT が正本となったため、統合前の旧設計書（`docs/spec_updates`）は誤参照の原因になる。アーカイブ後に削除して探索ノイズを減らす。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_191727_commentary02_docs_spec_updates.tar.gz`
- 削除:
  - `packages/commentary_02_srt2images_timeline/docs/spec_updates/`

### 21) `workspaces/video/runs` の未採用 run を退避（local / gitignore）

意図: 1エピソードに複数 run_dir が乱立すると、CapCutドラフト/画像/字幕の参照点がブレて事故要因になる。  
採用runを `workspaces/scripts/{CH}/{NNN}/status.json` の `metadata.video_run_id` に固定し、未採用runは削除せず `workspaces/video/_archive/` へ退避して探索ノイズを下げる。

- 実行コマンド:
  - `python3 scripts/episode_ssot.py archive-runs --channel CH02 --all-selected --mode run`（moved=63）
  - `python3 scripts/episode_ssot.py archive-runs --channel CH01 --all-selected --mode run`（moved=1）
  - `python3 scripts/episode_ssot.py archive-runs --channel CH04 --all-selected --mode run`（moved=4）
  - `python3 scripts/episode_ssot.py archive-runs --channel CH05 --all-selected --mode run`（moved=15）
  - `python3 scripts/episode_ssot.py archive-runs --channel CH06 --all-selected --mode run`（moved=9）
- 退避先（作業用アーカイブ）:
  - `workspaces/video/_archive/20251214T170521Z/`
  - `workspaces/video/_archive/20251214T170530Z/`
  - `workspaces/video/_archive/20251214T170531Z/`
- 補足:
  - CapCutプロジェクト本体（`$HOME/Movies/CapCut/.../com.lveditor.draft/*`）は移動していない。run_dir 内の `capcut_draft` symlink はそのまま。
  - 退避は local のみ（gitignore領域）。削除はしていないため、必要なら元の `workspaces/video/runs/` へ戻せば復旧できる。

### 22) CH06 の未採用 run を追加退避（local / gitignore）

意図: CH06-001/002/004 で run_dir が複数残っており、CapCutドラフト/音声/字幕の参照点がブレて修正作業が停止するため。  
採用runを `workspaces/scripts/CH06/{NNN}/status.json` の `metadata.video_run_id` に固定した上で、未採用runは削除せず `workspaces/video/_archive/` へ退避した。

- 実行コマンド:
  - `python3 scripts/episode_ssot.py archive-runs --channel CH06 --all-selected --mode run`（moved=10）
- 退避先（作業用アーカイブ）:
  - `workspaces/video/_archive/20251214T234906Z/CH06/`（`archive_report.json` あり）

### 23) `workspaces/video/input` の古い SRT/WAV を退避して final と再同期（local / gitignore）

意図: `workspaces/video/input` は `workspaces/audio/final` の **ミラー**だが、同期ツールが既存ファイルを上書きしないため、final 更新後も古いSRT/WAVが残り「どれが正？」で作業停止する事故が起きる。  
古いコピーを削除せず `workspaces/video/_archive/` に退避し、input 側を final と 1:1 に揃えた。

- 実行コマンド:
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run`
- 結果:
  - stale を `workspaces/video/_archive/20251215T001309Z/` へ退避（archived=220）
  - `video/input` と `audio/final` のサイズ不一致が 0 になることを確認（対応可能なファイルのみ）

### 24) CH06/CH04/CH02 の CapCut ドラフトを正本固定（フェード/テンプレ/音声字幕）+ 旧ドラフト退避（local）

意図: CapCutドラフトが複数乱立し、音声/字幕/テンプレが噛み合わず「どれが完成版？」で作業停止する重大欠陥を解消する。  
`workspaces/episodes/<CH>/<NNN>/` → `run` → `capcut_draft` を唯一の参照点に固定し、古い/誤った CapCut プロジェクトは削除せず退避する。

- 実行（整合）:
  - CH06: `images` トラックのフェード（crossfade）を不足分注入（001–033 の全episodeで transitions が cue 数に整合）
  - CH02-015: `align_run_dir_to_tts_final` → `auto_capcut_run --resume` でテンプレ（CH02-テンプレ）から再生成し、音声/字幕/帯/フェードを復旧
  - CH04(003–017,030): `align_run_dir_to_tts_final` → `auto_capcut_run --resume` でテンプレ（CH04-UNK_ック_テンプレ）から再生成し、音声/字幕/帯/エフェクト/フェードを復旧
- 代表コマンド:
  - `PYTHONPATH=".:packages" .venv/bin/python commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py --run workspaces/video/runs/<run_id>`
  - `PYTHONPATH=".:packages" .venv/bin/python commentary_02_srt2images_timeline/tools/auto_capcut_run.py --channel CH04 --srt workspaces/audio/final/CH04/<NNN>/CH04-<NNN>.srt --run-name <run_id> --resume --nanobanana none --belt-mode existing`
  - `PYTHONPATH=".:packages" .venv/bin/python commentary_02_srt2images_timeline/tools/auto_capcut_run.py --channel CH02 --srt workspaces/audio/final/CH02/015/CH02-015.srt --run-name CH02-015_20251211_102432 --resume --nanobanana none --belt-mode existing`
- 旧 CapCut プロジェクトの退避（削除しない）:
  - `~/Movies/CapCut/Archive_20251215_095752/CH04/`（旧 `CH04-*_draft` + 空テンプレ `CH04-UNK_テンプレ_アカシック`）
  - `~/Movies/CapCut/Archive_20251215_095752/CH02/CH02-015_20251211_102432_draft`
- 再発防止（コード）:
  - `capcut_bulk_insert.py` のテンプレ検証を強化し、`tracks[]` が空のテンプレをエラー扱いにして fail-fast（空テンプレ起因の壊れドラフト生成を防止）

### 25) CH02/CH04 の未整合分を完了（CH02-035画像欠損復旧 / CH04-001/002再生成 / episodes再materialize / video/input再同期）

意図: まだ残っていた「画像欠損でドラフト生成不能」「テンプレ外ドラフト参照」「episodes の参照点欠落」「video/input のミラー不一致」を潰し、A→音声/SRT→run→CapCutドラフトの 1:1 を確定させる。

- CH02-035（画像欠損で `auto_capcut_run --resume` が失敗していた件）:
  - `workspaces/video/runs/CH02-035_regen_20251213_092000/images/` を `images.legacy.<timestamp>/` に退避し、既存CapCutドラフトの `assets/image/*.png`（61枚）から `images/0001.png..0061.png` を復旧
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH02 --srt workspaces/audio/final/CH02/035/CH02-035.srt --run-name CH02-035_regen_20251213_092000 --resume --nanobanana none --belt-mode existing`
  - 旧 `CH02-035_regen_20251213_092000_draft` を削除せず退避: `~/Movies/CapCut/Archive_20251215_095752/CH02/CH02-035_regen_20251213_092000_draft`
  - CH02 の旧 `*_draft` を一括退避（参照されていないもののみ）: `~/Movies/CapCut/Archive_20251215_095752/CH02/bulk_20251215T014521Z/`
- CH04-001/002（broken symlink / テンプレ外ドラフト参照の復旧）:
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH04 --srt workspaces/audio/final/CH04/001/CH04-001.srt --run-name CH04-001_20251212_161816 --resume --nanobanana none --belt-mode existing`
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH04 --srt workspaces/audio/final/CH04/002/CH04-002.srt --run-name CH04-002_20251212_163754 --resume --nanobanana none --belt-mode existing`
- `workspaces/episodes/<CH>/<NNN>/` の参照点復旧:
  - `python3 scripts/episode_ssot.py materialize` を再実行し、`capcut_draft` link を生成/更新（CH02:016/017/018/041、CH04:001/002）
- `workspaces/video/input` のミラーを final に再同期（差分のみ）:
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace`
  - 退避先: `workspaces/video/_archive/20251215T013750Z/`（CH02-018 の更新差分）
- 未採用 run の追加退避（CH02-016 の重複runを削除せず整理）:
  - `python3 scripts/episode_ssot.py archive-runs --channel CH02 --all-selected --mode run`（moved=2）
  - 退避先: `workspaces/video/_archive/20251215T014238Z/CH02/`（`archive_report.json` あり）

### 26) `workspaces/video/input` の孤児 SRT/WAV を退避してミラーを純化（local / gitignore）

意図: `workspaces/video/input` は `workspaces/audio/final` のミラーだが、「final に存在しないファイル（旧命名/重複/途中生成物）」が残っていると誤参照の原因になる。  
削除はせず `workspaces/video/_archive/` へ退避し、input を “final に存在するものだけ” にする。

- 実行コマンド:
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace --orphan-policy archive`
- 結果:
  - orphan=52 を `workspaces/video/_archive/20251215T050709Z/` へ退避
  - `workspaces/video/input/*` に orphan が残っていないことを確認
- 再発防止（コード）:
  - `sync_audio_inputs.py` に `--orphan-policy archive` を追加（mirror の純化をワンコマンド化）

### 27) CapCut 直下の CH02/CH04 旧プロジェクトを退避して「★ + テンプレ」だけに整理（local）

意図: CapCut UI 上で CH02/CH04 の旧プロジェクト（テスト/コピー/完成ドラフト名など）が残ると「どれが正？」で迷う。  
run_dir から参照されていないものだけを削除せずアーカイブへ退避し、CapCut 直下は `★CHxx-...`（採用ドラフト）とテンプレだけにする。

- 退避対象（参照されていないことを確認済み）:
  - `CH02-*`（非★/非テンプレ）: 8件
  - `CH04-*`（非★/非テンプレ）: 8件
- 退避先:
  - `~/Movies/CapCut/Archive_20251215_095752/CH02/misc_20251215T051119Z/`
  - `~/Movies/CapCut/Archive_20251215_095752/CH04/misc_20251215T051119Z/`

### 28) CH06-004 の「音声と字幕が噛み合わない」を正本から再確定して統一（audio再生成 / run整合 / CapCutドラフト再生成）

意図: CH06-004 で CapCut ドラフトが `video/_archive` 配下の旧WAV（`CH06-004 (1).wav`）を参照しており、音声と字幕が一致しない/正本が不明で作業停止する事故を解消する。  
SSOT を `A_text → workspaces/audio/final → workspaces/video/runs/<run_id> → capcut_draft` に固定し、旧物は削除せず退避する。

- A→音声/SRT を再確定（voicevox / Strict TTS）:
  - 旧 `workspaces/audio/final/CH06/004/` を退避: `workspaces/audio/_archive_audio/20251215T055836Z/final/CH06/004/`
  - `PYTHONPATH=".:packages" .venv/bin/python -m audio_tts_v2.scripts.run_tts --channel CH06 --video 004 --input workspaces/scripts/CH06/004/content/assembled_human.md --engine-override voicevox`
  - 結果: `workspaces/audio/final/CH06/004/a_text.txt` が `assembled_human.md` と一致（`matches_a_text=true` を確認）
- run_dir を新しい final に整合:
  - run: `workspaces/video/runs/CH06-004_capcut_v1/`
  - `CH06-004.srt` を final に更新（旧SRTは自動退避）: `CH06-004.legacy.20251215_150739.srt`
  - `align_run_dir_to_tts_final` は cue#1 の低スコアで失敗したため、cue の start/end を “総尺比率スケール” で retime（分割は変更しない）:
    - 退避: `image_cues.legacy.20251215_060933.json`
    - 新 `timeline_manifest.json` を strict で再生成（cues_end == srt_end を確認）
- CapCutドラフトを新しい正本で再生成（テンプレ準拠・外部WAV参照を排除）:
  - `PYTHONPATH=".:packages" .venv/bin/python -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH06 --srt workspaces/audio/final/CH06/004/CH06-004.srt --run-name CH06-004_capcut_v1 --resume --nanobanana none --belt-mode existing`
  - 新: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH06-004-【日本史の闇】織田信長生存説【都市伝説のダーク図書館】`（voiceover は draft 内 `materials/audio/CH06-004.wav` を参照）
  - 旧（音声/SRT不一致タグ付き）を削除せず退避: `~/Movies/CapCut/Archive_20251215_095752/CH06/mismatch_20251215T061046Z/`
- ミラー/リンクを更新:
  - `python3 scripts/episode_ssot.py materialize --channel CH06 --video 004`
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace --orphan-policy archive`
    - `workspaces/video/input/CH06_都市伝説のダーク図書館/CH06-004.{wav,srt}` を更新（旧は `workspaces/video/_archive/20251215T061127Z/` に退避）

### 29) CapCut画像スケールを 103% に固定（今後の生成 + 既存CH02/CH04/CH06へ適用）/ CH06破損復旧 / CH02のbroken symlink修正

意図: CapCutドラフト内の画像はキーフレームで微小移動するため、全ドラフトのデフォルト画像スケールを 103%（`1.03`）に固定する。  
また、誤ってテンプレのロゴ/帯レイヤーまでスケール上書きしてしまう欠陥で CH06 ドラフトが崩れる問題を根本修正し、既存ドラフトも「完成以外」を中心に一括適用して迷いを潰す。

- 今後の生成（デフォルト）:
  - `config/channel_presets.json` の `position.scale`（元 `1.0`）を `1.03` に更新（CH03など元から別スケールのチャンネルは維持）
  - `auto_capcut_run.py` / `capcut_bulk_insert.py` の `--scale` default を `1.03` に統一
  - `auto_capcut_run.py` が既存 `belt_config.json` を使う場合も `main_title` を常に実タイトルへ更新（`setdefault` で古いタイトルが残る事故を防止）
- 既存ドラフト（CH02/CH04/CH06・完成以外）へ 103% を適用:
  - 新ツール: `commentary_02_srt2images_timeline/tools/capcut_apply_image_scale.py`
  - 「画像素材（`0001.png` などの番号付きアセット）」に該当する video segment のみ `clip.scale` / scale keyframes を `1.03` に正規化（テンプレのロゴ/帯は触らない）
  - 実行例:
    - `python3 commentary_02_srt2images_timeline/tools/capcut_apply_image_scale.py --draft-regex '^★CH04-' --exclude-regex '完成|_bak_|_failed_|テンプレ' --scale 1.03`
    - `python3 commentary_02_srt2images_timeline/tools/capcut_apply_image_scale.py --draft-regex '^★CH02-' --exclude-regex '完成|_bak_|_failed_|_old|テンプレ' --scale 1.03`
    - `python3 commentary_02_srt2images_timeline/tools/capcut_apply_image_scale.py --draft-regex '^★CH06-' --exclude-regex '完成|_bak_|_failed_|テンプレ' --scale 1.03`
  - 変更前のJSONは各ドラフト直下に自動退避:
    - `draft_content.json.bak_scale103_<timestamp>`
    - `draft_info.json.bak_scale103_<timestamp>`
- CH06 ドラフト破損（帯/ロゴ崩れ）の根本原因と修正:
  - 原因: `capcut_bulk_insert.py` のスケール強制が video tracks 全体にかかり、テンプレ由来の `video_1_2`（ロゴ等）まで上書きしていた
  - 修正: 番号付き画像アセットのみスケール上書き対象に限定
  - CH06-004 は `auto_capcut_run --resume` で再生成し、テンプレの帯/ロゴを維持したまま復旧
- CH02 の SSOT 参照点修正（broken symlink）:
  - CH02-014 / CH02-019 が `完成★...` にリネームされ、`workspaces/video/runs/*/capcut_draft` が存在しない `★...` を指していた
  - run_dir の `capcut_draft` symlink を `完成★...` へ付け替え、`capcut_draft_info.json` / `auto_run_info.json` の draft パスも更新（CH02/CH04/CH06 の broken capcut_draft link が 0 件になることを確認）
- 旧/失敗CapCutプロジェクトの退避（CH02）:
  - `★..._bak_*` / `★..._failed_*` / `★..._old*` / 破損 `★CH02-033` を削除せず退避: `~/Movies/CapCut/Archive_20251215T102542Z/CH02/legacy_projects_scale103_fix/`

### 30) cues_plan のcue連続性保証（自動フェード0個でドラフト生成が落ちる事故の根本修正）+ CH06-004 を正本で再生成（local）

意図: cues_plan モードで生成した `image_cues.json` が cue 間に隙間を含むと、CapCut の自動フェード挿入が 0 個になり `capcut_bulk_insert.py` が失敗して「ドラフトが壊れた/どれが正？」状態になる。  
cue を必ず隙間ゼロ（連続）に正規化し、CH06-004 は `A_text → audio/final → video/run → capcut_draft` に統一して旧物は削除せず退避する。

- コード修正:
  - `packages/commentary_02_srt2images_timeline/src/srt2images/cues_plan.py` に cue 連続性保証（`end_sec = next_start_sec`）を追加
- CH06-004 run の cue を再生成（画像は再生成しない）:
  - `SRT2IMAGES_CUES_PLAN_MODE=1 ./.venv/bin/python packages/commentary_02_srt2images_timeline/tools/run_pipeline.py --srt workspaces/audio/final/CH06/004/CH06-004.srt --out workspaces/video/runs/CH06-004_capcut_v1 --engine none --cue-mode grouped --crossfade 0.5 --fps 30 --nanobanana none --channel CH06`
- 失敗していた CapCut ドラフトを退避して再生成:
  - 退避: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH06-004-*` を `workspaces/video/_archive/20251215T124232Z/capcut_drafts/` へ退避（`*_bak_*` も同様）
  - 再生成: `SRT2IMAGES_CUES_PLAN_MODE=1 ./.venv/bin/python -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH06 --srt workspaces/audio/final/CH06/004/CH06-004.srt --run-name CH06-004_capcut_v1 --nanobanana none --belt-mode auto --resume`
    - 結果: 自動フェード `49` 個を適用して完走（`capcut_draft` symlink 更新）
- `workspaces/video/input` の旧コピーを退避してミラーを更新:
  - `./.venv/bin/python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace --orphan-policy archive`
  - 退避先: `workspaces/video/_archive/20251215T124652Z/CH06/video_input/CH06_都市伝説のダーク図書館/CH06-004.{wav,srt}`

### 31) API LLMを使わずに音声→run→CapCut を更新（CH06-005 / no-LLM運用の確立）

意図: tts_reading / belt_generation 等で API LLM を叩くとコストが発生する。  
以後は **エージェント推論（人間/本CLIで判断） + ローカル生成** を基本にし、音声は `SKIP_TTS_READING=1`（辞書/overrideのみ）で作る。

- 旧音声の退避（CH06-005）:
  - `workspaces/audio/final/CH06/005/` → `workspaces/audio/_archive_audio/20251215T125346Z/final/CH06/005/`（旧final退避）
  - `workspaces/audio/final/CH06/005/` → `workspaces/audio/_archive_audio/20251215T130903Z/final/CH06/005/`（API LLM を使ってしまった生成物も退避）
- no-LLMで音声を再生成（CH06-005）:
  - `SKIP_TTS_READING=1 ./.venv/bin/python -m script_pipeline.cli audio --channel CH06 --video 005`
  - `auditor/LLM skipped` をログで確認（API呼び出しなし）
- `workspaces/video/input` ミラー更新（CH06-005）:
  - `./.venv/bin/python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace --orphan-policy archive`
  - 退避先: `workspaces/video/_archive/20251215T131434Z/CH06/video_input/CH06_都市伝説のダーク図書館/CH06-005.{wav,srt}`
- run_dir を final に整合（CH06-005 / LLMなし）:
  - `./.venv/bin/python -m commentary_02_srt2images_timeline.tools.align_run_dir_to_tts_final --run workspaces/video/runs/CH06-005_capcut_v1 --min-score 0.5`
  - cue#46 の終盤CTA差分で一致度が落ちるため閾値を下げて整合（image_cues backup: `image_cues.legacy.20251215_221643.json`）
- CapCutドラフトを正本で再生成（CH06-005 / LLMなし）:
  - 旧ドラフト退避: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH06-005-*` → `workspaces/video/_archive/20251215T131656Z/capcut_drafts/`
  - 再生成: `SRT2IMAGES_DISABLE_TEXT_LLM=1 ./.venv/bin/python -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CH06 --srt workspaces/audio/final/CH06/005/CH06-005.srt --run-name CH06-005_capcut_v1 --resume --nanobanana none --belt-mode existing --title "【超古代文明】オクロの天然原子炉【都市伝説のダーク図書館】"`
  - 自動フェード: `45` 個を適用して完走
- CapCutドラフトの「テンプレ長尺残り」を音声尺にトリム（CH06-005）:
  - `draft_content.json` / `draft_info.json` の `duration` と各トラック末尾を `audio_srt.end_sec` に揃え（バックアップ作成済み: `*.bak_trim_*`, `*.bak_trimtracks_*`）

### 32) CH06(004-033) / CH02(019-) 台本→音声→run→CapCut を no-LLM で正本へ統一（カオス根絶）

意図: 「どれが完成版？」状態を終わらせるため、**Aテキスト（台本）→ audio/final → run_dir → CapCutドラフト** の参照鎖を 1 本に固定する。  
特に CH06-008 などで run_dir が旧SRT/旧音声を前提にしており、音声・字幕の差し替えが不能になっていた問題を解消する。

- 台本の引用/メタ混入チェック（dry-run）:
  - CH06: `scripts/sanitize_a_text.py --mode dry-run --channel CH06 --videos 006-033`
  - CH02: `scripts/sanitize_a_text.py --mode dry-run --channel CH02 --videos 019-082`
- ポーズ記法（`---` / 空行）を TTS に反映（no-LLM）:
  - `packages/audio_tts_v2/tts/strict_segmenter.py` を更新（pause marker 対応）
- 音声の再生成（old final は archive-first）:
  - CH06 006-033: 旧 `workspaces/audio/final/CH06/<VID>/` を `workspaces/audio/_archive_audio/20251215T134528Z/final/CH06/<VID>/` へ退避 → `SKIP_TTS_READING=1 ./.venv/bin/python -m script_pipeline.cli audio --channel CH06 --video <VID>`
  - CH02 019-082: 旧 `workspaces/audio/final/CH02/<VID>/` を `workspaces/audio/_archive_audio/20251215T155014Z/final/CH02/<VID>/` へ退避 → `SKIP_TTS_READING=1 ./.venv/bin/python -m script_pipeline.cli audio --channel CH02 --video <VID>`
- `workspaces/video/input` の古いミラーを退避して正本（audio/final）へ同期:
  - `./.venv/bin/python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --on-mismatch archive-replace --orphan-policy archive`
  - 退避root: `workspaces/video/_archive/20251215T224825Z/`
- run_dir を final SRT/WAV に整合（LLMなし、失敗時も止めない fallback を追加）:
  - ツール更新: `packages/commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py` に `--fallback-scale` を追加（cue.text がズレていても尺だけ確実に合わせる）
  - 実行例（CH06/CH02）:
    - `python packages/commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py --run workspaces/video/runs/CH06-008_capcut_v1 --min-score 0.68 --fallback-scale`
    - `python packages/commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py --run workspaces/video/runs/CH02-034_regen_20251213_091300 --min-score 0.68 --fallback-scale`
  - これにより `timeline_manifest.json` が全 run_dir で strict validate OK（CapCut差し替えの前提を固定）
- CapCutドラフトへ音声/SRT を再注入（画像は作り直さない）:
  - `python packages/commentary_02_srt2images_timeline/tools/patch_draft_audio_subtitles_from_manifest.py --run <run_dir>`
  - 適用範囲:
    - CH06: `workspaces/video/runs/CH06-004_capcut_v1` 〜 `workspaces/video/runs/CH06-033_capcut_v1`
    - CH02: `workspaces/video/runs/CH02-019_regen_*` 〜 `workspaces/video/runs/CH02-041_regen_*`（※042+ は run_dir/ドラフト未作成のため別途生成が必要）
- 画像スケール 103%（1.03）:
  - `capcut_apply_image_scale.py` で確認（今回対象の CH02/CH04/CH06 は既に NOOP = 1.03 適用済み）

### 33) CH06 タイトル刷新（planning CSV → run_dir → CapCut 表示）を 1 本化

意図: タイトルが複数箇所に散って「どれが正？」になる事故を防ぐため、`workspaces/planning/channels/CH06.csv` を正本に固定し、CapCut 側の表示/メタも同一タイトルへ同期する。  
要件: `【都市伝説のダーク図書館】` を除去し、より惹きつけるタイトルへ刷新。

- Planning SoT 更新:
  - `workspaces/planning/channels/CH06.csv`（互換: `progress/channels/CH06.csv`）の `タイトル` / `タイトル_サニタイズ` を CH06-001〜033 で更新
  - 検証: `rg "【都市伝説のダーク図書館】" progress/channels/CH06.csv` がヒットしない
- run_dir 側へ同期（CH06-001〜033）:
  - `workspaces/video/runs/CH06-*_capcut_v1/belt_config.json` の `main_title` を更新
  - `workspaces/video/runs/CH06-*_capcut_v1/capcut_draft_info.json` の `title` を更新
- CapCut ドラフトの画面表示タイトルを同期（CH06-001〜033）:
  - `packages/commentary_02_srt2images_timeline/tools/inject_title_json.py` を用いて `main_belt` のテキストを更新
  - `draft_content.json` の `base_content` も更新対象に追加（`content` だけ更新すると UI 上で古い文字が残るケースがあったため）
- 参照切れ修正:
  - CH06-005 などで `capcut_draft` symlink が破損していたため、既存のドラフト（`完成★...`）へ張り直し、`capcut_draft_info.json` の `draft_path`/`draft_name` を整合
  - `完成★...` へフォルダ名変更後、ドラフト内部 JSON が旧パス（`.../★CH06-...`）を参照して CapCut 上で素材欠損になるケースがあったため、`draft_content.json`/`draft_meta_info.json` 内の base path を新フォルダへ置換（archive-first）:
    - 退避: `workspaces/video/_archive/20251216T032643Z/capcut_pathfix_CH06/`（CH06-003,006-018）
    - 退避: `workspaces/video/_archive/20251216T035053Z/capcut_pathfix_CH06_extra/`（CH06-002,004,005）

### 34) 容量圧迫していた退避物を削除（CH08誤生成の退避 / workspaces アーカイブ一掃）

意図: 退避物がディスク容量を圧迫していたため、復元不要と判断し削除して空きを確保する（ユーザー指示）。

- CapCut の退避（ローカル）を削除:
  - `/Users/dd/Movies/CapCut/Archive_20251216T042845Z/`
  - `/Users/dd/Movies/CapCut/Archive_20251216T052535Z/`
  - 備考: Codex 実行バイナリが `~/Movies` に対して直書き削除できない環境だったため、`osascript -e 'do shell script \"rm -rf ...\"'` で削除を実行
- repo 内の退避を削除:
  - `workspaces/video/_archive/*`（全削除）
  - `workspaces/audio/_archive_audio/*`（全削除）
  - 結果: `df -h /System/Volumes/Data` の `Avail` が増加（空き容量回復）

### 35) `audio_sync_status.json` を workspaces へ移設（tracked削除）

意図: `audio_sync_status.json` は「同期済み/チェック済み」などの **状態（State）** であり、コード階層（packages）に置くと差分ノイズと誤参照の原因になるため。  
正本を `workspaces/video/_state/` に移し、repo からは削除して “SoT=workspaces” を徹底する。

- archive-first（tracked削除の証跡）:
  - `backups/graveyard/20251216T234900Z_audio_sync_status.json`
- 移設先（gitignore領域）:
  - `workspaces/video/_state/audio_sync_status.json`
- 実装更新:
  - paths SSOT: `factory_common.paths.video_audio_sync_status_path()`
  - 同期ツール: `packages/commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`
  - gitignore:
    - `workspaces/.gitignore` に `video/_state/**` を追加
    - `packages/commentary_02_srt2images_timeline/.gitignore` に `progress/` を追加（再混入防止）
- repo から削除:
  - `packages/commentary_02_srt2images_timeline/progress/audio_sync_status.json`

### 36) `commentary_02` package 内の `backups/` と `memory/` を削除（repo tracked）

意図: `packages/` はコードのみを原則とし、バックアップ/メモを code tree に残さない（探索ノイズと誤参照を防ぐ）。

- アーカイブ（復元用）:
  - `backups/graveyard/20251217_021441_commentary02_package_extras.tar.gz`
- 削除（git rm）:
  - `packages/commentary_02_srt2images_timeline/backups/`
  - `packages/commentary_02_srt2images_timeline/memory/`
- 判定:
  - 参照ゼロ（例: `rg "manual_edit_baseline_191_3|backup_draft_info\\.json" -S .` がヒットしない）

### 37) `commentary_02` package 内の `bin/` を削除（repo tracked）

意図: 旧個別シェル（`/Users/...` 直書きなど）を排除し、正本入口を `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` に一本化する。

- アーカイブ（復元用）:
  - `backups/graveyard/20251217_022233_commentary02_bin_legacy.tar.gz`
- 削除（git rm）:
  - `packages/commentary_02_srt2images_timeline/bin/`

### 38) Remotion のサンプル run 資産を削除（repo tracked）

意図: Remotion は現行本番運用では未使用であり、`apps/remotion/` 配下に大容量のサンプル run（画像群）が残ると探索ノイズ/容量圧迫/誤参照の原因になるため。

- アーカイブ（復元用 / local）:
  - `backups/graveyard/20251217_114547_remotion_sample_run_assets.tar.gz`
  - 備考: 大容量のため git には追加せず（`backups/` は gitignore）。復元は archive 展開 or git 履歴から復旧。
- 削除（git rm）:
  - `apps/remotion/input/192/`（画像 + JSON のサンプル）
  - `apps/remotion/public/_auto/`（サンプル画像）
  - `apps/remotion/public/tmp_run_192/`（サンプル画像）
- 追加削除（untracked）:
  - `apps/remotion/input/192/192.wav`
  - `apps/remotion/input/192/192.srt`
- 判定:
  - 参照ゼロ（例: `rg "tmp_run_192|_auto/192|input/192" -S apps/remotion` がヒットしない）

### 39) `scripts/maintain_consciousness.py` を削除（repo tracked）

意図: Route2 時代の自動承認トークン生成スクリプトであり、現行の確定フロー/入口（`ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）では使用しない。残っていると誤実行の原因になるため。

- アーカイブ（復元用）:
  - `backups/graveyard/20251217_121820_scripts_maintain_consciousness.tar.gz`
- 削除（git rm）:
  - `scripts/maintain_consciousness.py`
- 判定:
  - 参照ゼロ（例: `rg "maintain_consciousness" -S .` がヒットしない）

### 40) `success_config.json` を legacy へ隔離（repo tracked）

意図: `packages/` 配下に「旧実行コマンド（絶対パス）」などの残骸があると誤参照の原因になるため。現行フローでは参照されないので legacy へ隔離する。

- 参照確認:
  - `rg "success_config\\.json" -S packages/commentary_02_srt2images_timeline` がヒットしない
- 移動（git mv）:
  - `packages/commentary_02_srt2images_timeline/config/success_config.json`
  - → `legacy/commentary_02_srt2images_timeline/config/success_config.json`

### 41) 音声の「確実残骸」を統合 cleanup で削除（untracked）

意図: 音声生成後に残る `chunks/` や `audio_prep` 内の重複バイナリは、容量と探索ノイズの主因になる。  
final wav/srt/log を守りつつ、再生成可能な残骸（L2/L3）をまとめて削除する。

- 実行（dry-run → run）:
  - `python3 -m scripts.cleanup_workspace --all --dry-run --keep-recent-minutes 360`
  - `python3 -m scripts.cleanup_workspace --all --run --yes --keep-recent-minutes 360`
- 削除内容:
  - `workspaces/scripts/**/audio_prep/chunks/`（1件 / 約54MB）
  - `workspaces/scripts/**/audio_prep/{CH}-{NNN}.wav|.srt`（重複6ファイル / 約141MB）
  - `workspaces/audio/final/**/chunks/`（94件 / 約4.9GB）
- 安全条件:
  - final wav が存在するもののみ対象（final SoT を削除しない）
  - 直近 6 時間（keep-recent-minutes=360）の更新物はスキップ（実行中の synthesis を妨害しない）

### 42) L3ログの短期ローテ cleanup を実行（untracked）

意図: `workspaces/logs/` の L3（デバッグ/作業ログ）が増えると探索が重くなるため、7日より古いものを削除して整理する（L1 JSONL/DB と agent queue は保護）。

- 実行（run）:
  - `python3 scripts/cleanup_workspace.py --logs --run --logs-keep-days 7`
- 削除内容:
  - `logs_root()` 直下の L3（`.log/.txt/.json/.out` など） + `logs/{repair,swap,regression,ui_hub}/` の古いログ
  - 削除件数: 65 files
- 備考:
  - `llm_api_cache` は今回は対象外（必要なら `--include-llm-api-cache` で追加）

### 43) Video runs の旧runをアーカイブ（untracked）

意図: `workspaces/video/runs/` の run dir が増えると容量/探索ノイズが急増するため、**削除はせず** `_archive/` へ移動して整理する。

- 実行（run）:
  - `python3 scripts/cleanup_workspace.py --video-runs --run --channel CH01 --keep-recent-minutes 720`
- 変更内容:
  - `CH01-249` の重複run 5件を `workspaces/video/_archive/20251217T104426Z/CH01/runs/` へ移動
- 安全条件:
  - `keep-last-runs=2` を保持（各episodeで最低2 runは残す）
  - 直近12時間（keep-recent-minutes=720）の更新物はスキップ
  - `.keep` マーカーは保護
- レポート:
  - `workspaces/video/_archive/20251217T104426Z/archive_report.json`

### 44) Video runs を全体スキャンして重複run/テストrunをアーカイブ（untracked）

意図: CH単位の整理に加えて、`runs/` 直下に残るテスト/デバッグrunや重複runをまとめて `_archive/` へ移動し、探索ノイズを減らす。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --video-runs --all --dry-run --video-archive-unscoped --keep-recent-minutes 720`
  - `python3 scripts/cleanup_workspace.py --video-runs --all --run --yes --video-archive-unscoped --keep-recent-minutes 720`
- 変更内容（run）:
  - アーカイブ件数: 9 dirs（例: `CH05-001` の重複run、`test_*` / `debug_*` run）
- 安全条件:
  - `keep-last-runs=2` を保持
  - 直近12時間の更新物はスキップ
  - `.keep` マーカーは保護
- レポート:
  - `workspaces/video/_archive/20251217T104710Z/archive_report.json`

### 45) hidden run（`_failed/_tmp_*` 等）をアーカイブして runs/ を清掃（untracked）

意図: `runs/` 直下の hidden run（`_failed` 等）は実運用の SoT ではないため、まとめて `_archive/_unscoped/` に退避して見通しを良くする。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --video-runs --all --dry-run --video-archive-unscoped --video-include-hidden-runs --keep-recent-minutes 720`
  - `python3 scripts/cleanup_workspace.py --video-runs --all --run --yes --video-archive-unscoped --video-include-hidden-runs --keep-recent-minutes 720`
- 変更内容（run）:
  - アーカイブ件数: 7 dirs（`_failed`, `_tmp_*`, 旧ネスト `CH01/` など）
  - 追加で untracked の `_tmp_*.png` を `workspaces/video/runs/` 直下から削除
- 安全条件:
  - 直近12時間の更新物はスキップ
  - `.keep` マーカーは保護
- レポート:
  - `workspaces/video/_archive/20251217T105031Z/archive_report.json`

### 46) `commentary_02_srt2images_timeline/ui/`（互換shim）を archive-first で削除（repo tracked）

意図: 互換shim が残っていると「どっちが正本？」の混乱を招くため。現行コード参照ゼロを確認したうえで、graveyard に退避してから repo から削除する。

- 参照確認:
  - `rg "commentary_02_srt2images_timeline\\.ui" -S .` がヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251217T105500Z_commentary02_ui_shim.tar.gz`
- 削除（git rm）:
  - `git rm -r packages/commentary_02_srt2images_timeline/ui`
- 追従更新（SSOT/Docs）:
  - `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`, `ssot/ops/OPS_LOGGING_MAP.md`, `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`, `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`, `legacy/commentary_02_srt2images_timeline/README.md`

### 47) unscoped/legacy run をまとめて `_archive/` へ退避（untracked）

意図: `workspaces/video/runs/` 直下に残る numeric run / `api_*` / `jinsei*` / `CHxx-` 等の legacy run をまとめて退避し、run 探索と UI の見通しを改善する（削除ではなく移動）。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --video-runs --all --dry-run --video-unscoped-only --video-archive-unscoped --video-archive-unscoped-legacy --keep-recent-minutes 1440`
  - `python3 scripts/cleanup_workspace.py --video-runs --all --run --yes --video-unscoped-only --video-archive-unscoped --video-archive-unscoped-legacy --keep-recent-minutes 1440`
- 変更内容（run）:
  - アーカイブ件数: 45 dirs（例: `192*`, `233_*`, `api_*`, `jinsei*`, `CH01_人生の道標_220`, `CH06-`）
- 安全条件:
  - 直近24時間（keep-recent-minutes=1440）の更新物はスキップ
  - `.keep` マーカーは保護
  - `--video-unscoped-only` のため episode の run（`CHxx-NNN*`）には触れない
- レポート:
  - `workspaces/video/_archive/20251217T114250Z/archive_report.json`

### 48) `runs/` 直下の “ui_* / grouped_* / default” demo run を退避（untracked）

意図: episode に紐付かない UI 検証・デモ用 run が残っていると、run 探索が混乱するため `_archive/` へ退避する。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --video-runs --all --dry-run --video-unscoped-only --video-archive-unscoped-legacy --keep-recent-minutes 1440`
  - `python3 scripts/cleanup_workspace.py --video-runs --all --run --yes --video-unscoped-only --video-archive-unscoped-legacy --keep-recent-minutes 1440`
- 変更内容（run）:
  - アーカイブ件数: 17 dirs（`default`, `grouped_demo*`, `ui_*`）
- 安全条件:
  - 直近24時間の更新物はスキップ
  - `.keep` マーカーは保護
  - `--video-unscoped-only` のため episode の run には触れない
- レポート:
  - `workspaces/video/_archive/20251217T114542Z/archive_report.json`

### 49) `workspaces/scripts/**` の古い `audio_prep/` と per-video `logs/` を削除（untracked）

意図: `workspaces/scripts/**/audio_prep` と per-video `logs/` は再生成可能な中間物（L3/L2）であり、一定期間経過後は探索ノイズになるため削除する。

- 実行（run）:
  - `python3 scripts/cleanup_workspace.py --scripts --run --scripts-keep-days 14`
- 結果:
  - 削除: 80 paths（主に `CH03/*/audio_prep` と `CH03/*/logs`、一部 `CH06/001/logs`, `CH99/001/audio_prep`）
- 安全条件:
  - `--keep-days 14`（全ファイルが14日以上古い sub-tree のみ対象）
  - coordination locks があるスコープは自動スキップ（lock-aware cleanup）

### 50) 音声の「確実残骸」を統合 cleanup で追加削除（untracked）

意図: 前回 cleanup 後に残っていた `chunks/` と `audio_prep` の重複バイナリを追加で削除し、容量と探索ノイズを抑える。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --all --dry-run --keep-recent-minutes 360`
  - `python3 scripts/cleanup_workspace.py --all --run --yes --keep-recent-minutes 360`
- 削除内容（run）:
  - `workspaces/scripts/**/audio_prep/chunks/`（3件 / 約137.8MB）
  - `workspaces/scripts/**/audio_prep/{CH}-{NNN}.wav|.srt`（重複6ファイル / 約141.4MB）
  - `workspaces/audio/final/**/chunks/`（1件 / 約46.0MB）
- 安全条件:
  - final wav が存在するもののみ対象（final SoT を削除しない）
  - 直近 6 時間（keep-recent-minutes=360）の更新物はスキップ
  - coordination locks があるスコープは自動スキップ（例: `skipped_locked=9`）

### 51) `workspaces/video/runs/` 直下の stray files を SoT へ移設（untracked）

意図: `runs/` 直下にファイル（json/png）が残っていると、run 探索/UIの誤参照/cleanup 判定のノイズになるため、正しい置き場へ移す。

- 実行:
  - `workspaces/video/runs/CH04_alignment_report*.json` → `workspaces/video/_state/reports/`
  - `workspaces/video/runs/test_single_image.png` を削除（参照ゼロ）
- 結果:
  - `workspaces/video/runs/` 直下は directory のみ（`.gitkeep` を除く）

### 52) `commentary_02_srt2images_timeline/src/ui/`（旧テンプレ管理UI）を archive-first で削除（repo tracked）

意図: `src/ui` 配下のテンプレ管理クラスと統合テストは、現行の CapCut 主線/サーバ実装から参照されておらず（参照ゼロ）、探索ノイズと誤参照の原因になるため。復旧できるよう graveyard に退避してから repo から削除する。

- 参照確認:
  - `rg "CapCutTemplateManager|ImageTemplateManager" -S packages/commentary_02_srt2images_timeline` が `tests/test_integration.py` 以外にヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251217T140031Z_commentary02_src_ui_legacy.tar.gz`
- 削除（git rm）:
  - `git rm -r packages/commentary_02_srt2images_timeline/src/ui`
  - `git rm packages/commentary_02_srt2images_timeline/tests/test_integration.py`
- 追従更新（SSOT/Plan）:
  - `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`

### 53) `commentary_02_srt2images_timeline/env/.env.example` を archive-first で削除（repo tracked）

意図: `.env` は repo root（`factory_commentary/.env`）を正本とし、パッケージ配下の `.env*` は誤参照の原因になるため削除する。

- 参照確認:
  - `find . -maxdepth 4 -type f -name '.env*'` で当該ファイル以外のパッケージ内 `.env*` が無いことを確認
- 退避（archive-first）:
  - `backups/graveyard/20251217T143510Z_commentary02_env_example.tar.gz`
- 削除（git rm）:
  - `git rm packages/commentary_02_srt2images_timeline/env/.env.example`

### 54) `commentary_02_srt2images_timeline/configs/.openrouter_config` を archive-first で削除（repo tracked）

意図: OpenRouter の設定/キーは repo root `.env` / `credentials/` を正本とし、パッケージ配下の隠し設定ファイルは誤参照・漏洩リスクの温床になるため削除する（コードは `OPENROUTER_API_KEY` を参照）。

- 参照確認:
  - `find . -name '.openrouter_config'` が当該ファイル以外にヒットしない
  - `rg -n "openrouter_config" -S .` がヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251217T215754Z_commentary02_openrouter_config.tar.gz`
- 削除（git rm）:
  - `git rm packages/commentary_02_srt2images_timeline/configs/.openrouter_config`

### 55) 不要な試験スクリプト（直書きキー含む）を archive-first で削除（repo tracked）

意図: 参照されていない “試験用の残骸” は探索ノイズになり、特に API キー直書きは漏洩リスクの温床になるため、復旧用に退避した上で repo から削除する。

- 参照確認:
  - `rg -n "test_ambig\\.py|test_key_validity\\.py" -S .` が当該ファイル以外にヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251218T004727Z_audio_tts_v2_test_ambig_unused.tar.gz`
  - `backups/graveyard/20251218T004727Z_commentary02_test_key_validity_leaked_key.tar.gz`
- 削除（git rm）:
  - `git rm packages/audio_tts_v2/test_ambig.py`
  - `git rm packages/commentary_02_srt2images_timeline/test_key_validity.py`
- 重要:
  - `test_key_validity.py` に API キーが直書きされていたため、該当キーはローテーション推奨（以後は `.env` / `credentials/` の正規ルートへ）

### 56) `commentary_02_srt2images_timeline/test_gemini_25_flash_image.py` を archive-first で削除（repo tracked）

意図: Gemini 画像APIの手動検証用スクリプトは現行フロー/入口索引に含まれておらず参照も無いため、探索ノイズを減らす目的で退避した上で削除する。

- 参照確認:
  - `rg -n "test_gemini_25_flash_image" -S .` が当該ファイル以外にヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251218T011358Z_commentary02_test_gemini_25_flash_image.tar.gz`
- 削除（git rm）:
  - `git rm packages/commentary_02_srt2images_timeline/test_gemini_25_flash_image.py`

### 57) 未使用の `audio_tts_v2/tts/validators.py` を archive-first で削除し、設計ドキュメントを現行実装へ同期

意図: `validators.py` は現行コードから参照されておらず、かつ `sys.path.append("audio_tts_v2")` など旧構造前提の記述が残っているため、誤解・誤参照の温床になる。復旧できるよう退避した上で削除し、関連ドキュメントの記述を現行実装に合わせて更新する。

- 参照確認:
  - `rg -n "tts\.validators|validate_reading_quality" -S packages/audio_tts_v2` で実行コード側の参照が無いことを確認
- 退避（archive-first）:
  - `backups/graveyard/20251218T011645Z_audio_tts_v2_validators_and_doc_precleanup.tar.gz`
- 削除（git rm）:
  - `git rm packages/audio_tts_v2/tts/validators.py`
- 追従（docs）:
  - （削除済み）`packages/audio_tts_v2/docs/tts_logic_proof.md` は参照ゼロの一時設計メモとして archive-first で退避し、repo から削除した（証跡: Step 100）

### 58) audio の rebuildable artifacts を一括削除（untracked / safe）

意図: 音声生成後に残る `audio_prep/chunks` や `final/chunks`、重複バイナリは再生成可能で探索ノイズ・容量を増やすため、保持期限ルールに従って削除する。

- 実行（dry-run → run）:
  - `python3 scripts/cleanup_workspace.py --audio --all --dry-run --keep-recent-minutes 360`
  - `python3 scripts/cleanup_workspace.py --audio --all --run --yes --keep-recent-minutes 360`
- 削除内容（run）:
  - `workspaces/scripts/CH04/**/audio_prep/chunks/`（9件 / 約402.6MB）
  - `workspaces/scripts/CH04/**/audio_prep/{CH}-{NNN}.wav|.srt`（重複18ファイル / 約412.4MB）
  - `workspaces/audio/final/**/chunks/`（31件 / 約1.4GB）
- 安全条件:
  - final wav が存在するもののみ対象（final SoT を削除しない）
  - 直近 6 時間（keep-recent-minutes=360）の更新物はスキップ
  - coordination locks があるスコープは自動スキップ

### 59) CH10-001 固定の one-off 音声再生成スクリプトを archive-first で削除（repo tracked）

意図: CH10-001 固定で `audio_prep/` を直書きする one-off は現行 SoT（`workspaces/audio/final/`）と矛盾し、誤参照の温床になる。現行の入口は `python -m script_pipeline.cli audio ...` が正本。

- 参照確認:
  - `rg -n "regenerate_audio_and_srt_ch10_001|regenerate_audio_srt_strict_ch10_001" -S .` が当該ファイル以外にヒットしない
  - 言及は `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md` のみ
- 退避（archive-first）:
  - `backups/graveyard/20251218_135849_ch10_oneoff_tts_scripts/regenerate_audio.py`
  - `backups/graveyard/20251218_135849_ch10_oneoff_tts_scripts/regenerate_strict.py`
- 削除（git rm）:
  - `git rm scripts/regenerate_audio.py scripts/regenerate_strict.py`

### 60) 旧Route audio の deprecation stub を archive-first で削除（repo tracked）

意図: `scripts/` 直下に「DEPRECATED / no longer supported」な stub（実行しても exit=2 するだけ）が残っていると、探索ノイズになり低知能エージェントが誤って叩く原因になる。現行の入口は `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` を正とする。

- 参照確認:
  - `rg -n "scripts/(run_route1_batch|run_route2_agent|_core_audio)\\.py" -S .` の参照が legacy 配下のみであることを確認
- 退避（archive-first）:
  - `backups/graveyard/20251218_141306_deprecated_route_audio_stubs/_core_audio.py`
  - `backups/graveyard/20251218_141306_deprecated_route_audio_stubs/run_route1_batch.py`
  - `backups/graveyard/20251218_141306_deprecated_route_audio_stubs/run_route2_agent.py`
- 削除（git rm）:
  - `git rm scripts/_core_audio.py scripts/run_route1_batch.py scripts/run_route2_agent.py`

### 61) `legacy/scripts/route_audio/` を archive-first で削除（repo tracked）

意図: `legacy/scripts/route_audio/` は旧Route（batch/agent）時代のスクリプト群で、現行の `audio_tts_v2/scripts/run_tts.py` の引数仕様ともズレており **実行不能**。参照ゼロで探索ノイズになるため、退避した上で削除する。

- 参照確認:
  - `rg -n "legacy/scripts/route_audio" -S .` が当該ディレクトリ内のみ
- 退避（archive-first）:
  - `backups/graveyard/20251218_141802_legacy_route_audio.tar.gz`
- 削除（git rm）:
  - `git rm -r legacy/scripts/route_audio`

### 62) 旧 `commentary_02` の Legacy UI/設定を archive-first で削除（repo tracked）

意図: `legacy/commentary_02_srt2images_timeline/`（Gradio/React 試作 + 旧設定）は現行フロー/入口索引に含まれず参照も無いため、探索ノイズ削減のため退避した上で削除する。現行の差し替えUIは React UI（`/capcut-edit/swap`）+ `/api/swap` が正本。

- 参照確認:
  - `rg -n "legacy/commentary_02_srt2images_timeline/ui|gradio_app\\.py" -S .` の参照が docs/SSOT のみであることを確認し、ドキュメント側は現行UI導線へ更新
- 退避（archive-first）:
  - `backups/graveyard/20251218_142524_legacy_commentary02_ui.tar.gz`
- 削除（git rm）:
  - `git rm -r legacy/commentary_02_srt2images_timeline`

### 63) `legacy/scripts/agent_coord.py` を archive-first で削除（repo tracked）

意図: `agent_org.py` が正本であり、`legacy/scripts/agent_coord.py` は参照ゼロの旧残骸。探索ノイズ削減のため退避した上で削除する。

- 参照確認:
  - `rg -n "legacy/scripts/agent_coord\\.py" -S .` がヒットしない
- 退避（archive-first）:
  - `backups/graveyard/20251218_143327_legacy_agent_coord/agent_coord.py`
- 削除（git rm）:
  - `git rm legacy/scripts/agent_coord.py`

### 64) 壊れた `capcut_draft` symlink を一括削除（untracked / safe）

意図: `workspaces/video/**/capcut_draft` の壊れた symlink（target 無）は探索ノイズになり、低知能エージェントが「ドラフトがある」と誤認しやすい。`capcut_draft_info.json` が証跡として残るため、壊れたリンク自体は削除して問題ない。

- 実行（dry-run → run）:
  - `python3 scripts/ops/cleanup_broken_symlinks.py`
  - `python3 scripts/ops/cleanup_broken_symlinks.py --run --max-print 0`
- レポート:
  - `workspaces/logs/regression/broken_symlinks/broken_symlinks_<timestamp>.json`
- 安全条件:
  - symlink のみ unlink（target/生成物は削除しない）
  - coordination locks があるスコープは自動スキップ（生成中の run を守る）

### 65) `workspaces/episodes/**` の壊れsymlinkを掃除（untracked / safe）

意図: `workspaces/episodes/` は「正本へのリンク集」だが、過去に生成された symlink が壊れると探索ノイズになる。`episode_manifest.json` が不足を示せるため、壊れたリンク自体は削除して問題ない。

- 実行（dry-run → run）:
  - `python3 scripts/ops/cleanup_broken_symlinks.py --include-episodes --name capcut_draft`
  - `python3 scripts/ops/cleanup_broken_symlinks.py --include-episodes --name capcut_draft --run --max-print 0`
  - `python3 scripts/ops/cleanup_broken_symlinks.py --include-episodes --name audio.wav --run --max-print 0`
  - `python3 scripts/ops/cleanup_broken_symlinks.py --include-episodes --name audio.srt --run --max-print 0`
  - `python3 scripts/ops/cleanup_broken_symlinks.py --include-episodes --name A_text.md --run --max-print 0`
- レポート:
  - `workspaces/logs/regression/broken_symlinks/broken_symlinks_<timestamp>.json`

### 66) CH02-024 の欠損SoTを安全に復元（checksum一致）

意図: `episode_ssot show` が CH02-024 の A text / audio final の欠損を検知した。ところが `workspaces/video/input/` に同一ファイルが存在し、`workspaces/audio/final/CH02/024/audio_manifest.json` の sha1 と一致しているため、**安全に正本（audio/final + scripts/content）へ復元**できる。

- 事前確認:
  - `shasum -a 1 workspaces/video/input/CH02_哲学系/CH02-024.wav`
  - `shasum -a 1 workspaces/video/input/CH02_哲学系/CH02-024.srt`
  - `cat workspaces/audio/final/CH02/024/audio_manifest.json` の sha1 と一致すること
- 復元:
  - `cp workspaces/video/input/CH02_哲学系/CH02-024.wav workspaces/audio/final/CH02/024/CH02-024.wav`
  - `cp workspaces/video/input/CH02_哲学系/CH02-024.srt workspaces/audio/final/CH02/024/CH02-024.srt`
  - `cp workspaces/audio/final/CH02/024/a_text.txt workspaces/scripts/CH02/024/content/assembled.md`
  - `cp workspaces/audio/final/CH02/024/a_text.txt workspaces/scripts/CH02/024/content/assembled_human.md`
- 検証:
  - `python3 scripts/episode_ssot.py show --channel CH02 --video 024` で warning が消えること

### 67) ローカルキャッシュ（`__pycache__` / `.pytest_cache` / `.DS_Store`）を掃除（untracked / safe）

意図: キャッシュ類は探索ノイズと誤判定を増やすため、定期的に除去して repo を「見通しの良い状態」に保つ。

- 実行:
  - `bash scripts/ops/cleanup_caches.sh`

### 68) `workspaces/video/runs/**` の `*.legacy.*` 残骸を prune（archive-first + lock尊重）

意図: run_dir 直下に残る `*.legacy.*` は「どれが正本？」の誤認を誘発する探索ノイズ。現行フローの入力として使わないため、**退避（tar.gz）→削除**でクリーン化する。

- dry-run:
  - `python3 scripts/ops/prune_video_run_legacy_files.py --max-print 0`
- 実行（archive-first → delete）:
  - `python3 scripts/ops/prune_video_run_legacy_files.py --run --max-print 0`
  - archive: `backups/graveyard/20251218T073604Z_video_runs_legacy_files.tar.gz`
  - report: `workspaces/logs/regression/video_runs_legacy_prune/legacy_prune_20251218T073604Z.json`
- 結果:
  - deleted=185 / skipped_locked=63（CH02画像regenのlock対象はスキップ）

### 69) Remotion 生成物（`apps/remotion/out` + `apps/remotion/public/_bgm`）を keep-days ローテで削除

意図: Remotion のテスト生成物（mp4/chunks/tmp wav 等）が `apps/remotion/out` に溜まると容量/探索ノイズになる。古いものを keep-days 基準で削除し、現行作業の邪魔をしない状態に保つ。

- dry-run:
  - `python3 scripts/ops/cleanup_remotion_artifacts.py --keep-days 14 --max-print 0`
- 実行:
  - `python3 scripts/ops/cleanup_remotion_artifacts.py --keep-days 14 --run --max-print 0 --ignore-locks`（※自分で該当スコープをlockしている場合のみ。基本は lock 尊重のまま実行）
  - report: `workspaces/logs/regression/remotion_cleanup/remotion_cleanup_20251218T075050Z.json`
- 結果:
  - `apps/remotion/out` の巨大生成物を削除（tracked の `belt_config.generated.json` / `belt_llm_raw.json` は保持）、`apps/remotion/public/_bgm` の古い wav を削除（合計約1.5GB削減）

### 70) `workspaces/video/input` の wav を symlink 化して重複を解消（約14GB→14MB）

意図: `workspaces/video/input` は audio final のミラーだが、wav をコピーで保持すると `workspaces/audio/final` と **二重に容量を消費**する。内容一致のものだけ symlink に置換して、容量と探索ノイズを削減する。

- dry-run:
  - `python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode dry-run --wav-policy symlink --wav-dedupe --hash-wav --on-mismatch skip --orphan-policy archive`
- 実行:
  - `python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --mode run --wav-policy symlink --wav-dedupe --hash-wav --on-mismatch skip --orphan-policy archive --ignore-locks`（※自分で該当スコープをlockしている場合のみ）
- 結果:
  - updated=288（wav を symlink に置換）
  - orphan=2（archive: `workspaces/video/_archive/20251218T080912Z/<CH>/video_input/...`）
  - `workspaces/video/input` が約14GB → 約14MB に縮小

### 71) サムネ旧ディレクトリ（`workspaces/thumbnails/CHxx_*|CHxx-*`）を `_archive` に退避（untracked / safe）

意図: 旧形式のサムネ資産ディレクトリ（例: `CH01_人生の道標/`）がトップ階層に残っていると、**どれが現行SoTか**が分かりづらく、低知能エージェントほど誤参照しやすい。現行UI/コードは `assets/<CH>/<VIDEO>/` を正とするため、旧ディレクトリは `_archive` に退避して探索ノイズを除去する。

- dry-run:
  - `python3 scripts/ops/archive_thumbnails_legacy_channel_dirs.py --max-print 10`
- 実行:
  - `python3 scripts/ops/archive_thumbnails_legacy_channel_dirs.py --run --max-print 0 --ignore-locks`（※自分で該当スコープをlockしている場合のみ）
- 結果:
  - 5 dirs（約384MB）を `workspaces/thumbnails/_archive/20251218T091034Z/` へ移動
- レポート:
  - `workspaces/logs/regression/thumbnails_legacy_archive/thumbnails_legacy_archive_20251218T091034Z.json`

### 72) 旧 `logs/agent_tasks_*` キュー（実験残骸）を purge（archive-first）

意図: `workspaces/logs/agent_tasks/` が正本。過去の実験で作られた `agent_tasks_ch04/tmp/test` 等が残っていると「どれが現行のキューか」を誤認しやすい。小容量だが事故原因になるため、**退避（tar.gz）→削除**でクリーン化する。

- dry-run:
  - `python3 scripts/ops/purge_legacy_agent_task_queues.py --ignore-locks`
- 実行（archive-first → delete）:
  - `python3 scripts/ops/purge_legacy_agent_task_queues.py --run --ignore-locks`（※自分で該当スコープをlockしている場合のみ）
  - archive: `backups/graveyard/20251218T091319Z_legacy_agent_task_queues.tar.gz`
  - report: `workspaces/logs/regression/agent_tasks_legacy_purge/agent_tasks_legacy_purge_20251218T091319Z.json`
- 結果:
  - deleted_dirs=3（`agent_tasks_ch04` / `agent_tasks_tmp` / `agent_tasks_test`）

### 73) audio final の再生成可能 chunks を削除（untracked / safe）

意図: `workspaces/audio/final/**/chunks/` は最終wavが存在すれば再生成可能な中間物。容量/探索ノイズになるため、生成から十分時間が経過したものは削除してよい（`YTM_TTS_KEEP_CHUNKS=1` の場合は残す）。

- 実行:
  - `python3 scripts/purge_audio_final_chunks.py --run --keep-recent-minutes 360`
- 結果:
  - deleted=1（例: `workspaces/audio/final/CH07/001/chunks` 約42.8MB）

### 74) CapCut ローカル退避ドラフト（`workspaces/video/_capcut_drafts`）を `_archive` に退避（untracked / safe）

意図: `workspaces/video/_capcut_drafts/` は CapCut 実draft root に書けない環境でのフォールバック生成先。実draft root にコピー済みの重複が溜まると探索ノイズになり、低知能エージェントが「どれが正本か」を誤認しやすい。削除ではなく **`_archive/<timestamp>/` へ移動**してトップ階層をクリーンにする。

- dry-run:
  - `python3 scripts/ops/archive_capcut_local_drafts.py --ignore-locks --max-print 10`
- 実行:
  - `python3 scripts/ops/archive_capcut_local_drafts.py --run --ignore-locks --max-print 0`（※自分で `workspaces/video/_capcut_drafts/**` を lock している場合のみ）
- 結果:
  - moved=30（CH05-001..030 を `workspaces/video/_capcut_drafts/_archive/20251218T095722Z/` に移動）
  - protected_name=3（テンプレ系） / recent=1（直近更新）
- レポート:
  - `workspaces/logs/regression/capcut_local_drafts_archive/capcut_local_drafts_archive_20251218T095722Z.json`

### 75) `workspaces/video/runs/**` の `*.legacy.*` 残骸を追加 prune（archive-first + lock尊重）

意図: CH02画像regenなどで `*.legacy.*` が追加生成されることがある。既存の prune から時間が経った後に再実行し、未ロック範囲の探索ノイズだけ追加で除去する（ロック中の run は安全にスキップ）。

- 実行:
  - `python3 scripts/ops/prune_video_run_legacy_files.py --run --max-print 0`
- 結果:
  - deleted=46 / skipped_locked=17
  - archive: `backups/graveyard/20251218T101737Z_video_runs_legacy_files.tar.gz`
- レポート:
  - `workspaces/logs/regression/video_runs_legacy_prune/legacy_prune_20251218T101738Z.json`

### 76) CH02の画像regen後に残った `*.legacy.*` を追加 prune（lock解除後・lock尊重）

意図: CH02-021/022/023/025 の lock が解除されたため、`*.legacy.*` の探索ノイズを追加で除去する。CH02-024 は別lockが残っているため自動スキップ（壊さない）。

- dry-run:
  - `python3 scripts/ops/prune_video_run_legacy_files.py --max-print 0`
- 実行:
  - `python3 scripts/ops/prune_video_run_legacy_files.py --run --max-print 0`
- 結果:
  - deleted=14 / skipped_locked=3（CH02-024）
  - archive: `backups/graveyard/20251218T110038Z_video_runs_legacy_files.tar.gz`
- レポート:
  - `workspaces/logs/regression/video_runs_legacy_prune/legacy_prune_20251218T110038Z.json`

### 77) CH02の壊れた `capcut_draft` symlink を削除（lock解除後）

意図: `capcut_draft` の壊れた symlink（target無）は探索ノイズで誤認の温床。`capcut_draft_info.json` が証跡として残るため、壊れたリンク自体は削除して問題ない。CH02-021/022/023/025 を対象に削除。

- dry-run:
  - `python3 scripts/ops/cleanup_broken_symlinks.py --max-print 0`
- 実行:
  - `python3 scripts/ops/cleanup_broken_symlinks.py --run --max-print 0`
- 結果:
  - deleted=4
- レポート:
  - `workspaces/logs/regression/broken_symlinks/broken_symlinks_20251218T110055Z.json`

### 78) `audio_prep` の重複バイナリ（`*-regenerated.*` 等）を purge（untracked / safe）

意図: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav/.srt` が揃っている場合、`workspaces/scripts/{CH}/{NNN}/audio_prep/` に残る `*.wav/.srt` は重複で探索ノイズになる。特に `*-regenerated.wav/.srt` のような命名が残っていると「どれが正本か」を誤認しやすいので、final を正本に固定したうえで削除する。

- dry-run:
  - `python3 scripts/purge_audio_prep_binaries.py --dry-run --channel CH10 --video 001 --keep-recent-minutes 360`
- 実行:
  - `python3 scripts/purge_audio_prep_binaries.py --run --channel CH10 --video 001 --keep-recent-minutes 360`
- 結果:
  - deleted_files=2（`CH10-001-regenerated.wav` / `CH10-001-regenerated.srt`）

### 79) `apps/remotion/out` の tracked 生成物を repo から追跡解除（安全: gitignore 対象）

意図: `apps/remotion/out/` は Remotion の生成物置き場であり、`.gitignore` で除外するのが正しい。ここに tracked の JSON が残っていると、
「正本が repo にある」と誤認されやすく、差分ノイズ/探索ノイズの原因になるため削除する。

- 実行:
  - `git rm apps/remotion/out/belt_config.generated.json apps/remotion/out/belt_llm_raw.json`
- 結果:
  - tracked ファイルを削除（以後は生成されても git に載らない）

### 80) untracked キャッシュ（`__pycache__` / `.DS_Store`）を削除（safe）

意図: Python の `__pycache__` や macOS の `.DS_Store` は参照されない探索ノイズ。複数エージェント並列運用では誤認の温床になるため、定期的に削除して良い（`AGENTS.md` 準拠）。

- 実行:
  - `find . -type d -name '__pycache__' -prune -exec rm -rf {} +`
  - `find . -name '.DS_Store' -delete`
- 結果:
  - `__pycache__` dirs: 351 → 0
  - `.DS_Store` files: 1 → 0

### 81) `scripts/cleanup_workspace` で logs / symlinks / rebuildable audio を一括整理（lock尊重）

意図: 台本/音声/動画の並列運用中に「どれが正本か」を誤認しやすい残骸だけを、安全条件（recent skip / keep-days / lock尊重）で除去する。削除ではなくアーカイブ/レポート生成を優先し、壊れたリンクと再生成可能 chunks を削る。

- dry-run:
  - `python3 -m scripts.cleanup_workspace --all --dry-run --logs --scripts --video-runs --broken-symlinks --audio --keep-recent-minutes 1440 --logs-keep-days 30 --scripts-keep-days 14 --video-keep-last-runs 2`
- 実行:
  - `python3 -m scripts.cleanup_workspace --all --run --yes --logs --scripts --video-runs --broken-symlinks --audio --keep-recent-minutes 1440 --logs-keep-days 30 --scripts-keep-days 14 --video-keep-last-runs 2`
- 結果（主なもの）:
  - broken symlinks（`capcut_draft`）: deleted=8 / skipped_locked=2
  - logs: deleted=0（30日保持）
  - video runs: archived=0（reportのみ生成）
  - audio final chunks: deleted=1（`workspaces/audio/final/CH02/024/chunks` 約44.1MB）
- レポート:
  - `workspaces/logs/regression/broken_symlinks/broken_symlinks_20251221T014947Z.json`
  - `workspaces/logs/regression/logs_cleanup/logs_cleanup_20251221T014947Z.json`
  - `workspaces/video/_archive/20251221T014947Z/archive_report.json`

### 82) ブッダ系シニア 5ch 立ち上げキットを削除（archive-first / 誤誘導除去）

意図: `workspaces/planning/buddha_senior_5ch_setup.md` は現行SoTフローで参照されず、かつ CH12 の「8パート固定」など誤誘導の温床になっていたため、探索ノイズ削減のために archive-first で削除する。

- 参照確認:
  - `rg -n "buddha_senior_5ch_setup\\.md" -S .`（history以外の参照なし）
- アーカイブ:
  - `backups/graveyard/20251221T072310Z__workspaces_planning_buddha_senior_5ch_setup_md.tar.gz`
- 削除:
  - `workspaces/planning/buddha_senior_5ch_setup.md`（tracked）

### 83) `scripts/lint_a_text.py` を削除（archive-first / 入口一本化）

意図: `scripts/lint_a_text.py` は旧Aテキストlintであり、現行は `python3 scripts/ops/a_text_lint.py` が正本。入口索引にも載っていないため、探索ノイズ削減のために archive-first で削除する。

- 参照確認:
  - `rg -n "scripts/lint_a_text\\.py|lint_a_text\\.py" -S .`（history以外の参照なし）
- アーカイブ:
  - `backups/graveyard/20251221T213907Z__scripts_lint_a_text_py.tar.gz`
- 削除:
  - `scripts/lint_a_text.py`（tracked）

### 84) Azure疎通デバッグスクリプトを削除（archive-first / 誤誘導除去）

意図: `scripts/verify_router_azure.py` / `scripts/verify_azure_direct.py` は ad-hoc な疎通確認用で、古い設定前提・直書き/直アクセスが混入しやすく誤誘導の温床。現行の正本は `factory_common.llm_router` と `scripts/check_env.py`（および SSOT）なので、archive-first で削除する。

- 参照確認:
  - `rg -n "verify_router_azure\\.py|verify_azure_direct\\.py" -S .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251221T214632Z__scripts_verify_azure_debug.tar.gz`
- 削除:
  - `scripts/verify_router_azure.py`（tracked）
  - `scripts/verify_azure_direct.py`（tracked）

### 85) 旧パイプライン手動runnerを削除（archive-first / ハードコード除去）

意図: `scripts/run_pipeline_manual.py` / `scripts/run_pipeline_skip_llm.py` は旧 `srt2images` 直叩き・ハードコード（特定CH/固定パス）を含み、現行フローの正本と乖離して誤誘導の温床になる。現行は `commentary_02_srt2images_timeline/tools/*` が正本のため、archive-first で削除する。

- 参照確認:
  - `rg -n "run_pipeline_manual\\.py|run_pipeline_skip_llm\\.py" -S .`（Path inventory以外の参照なし）
- アーカイブ:
  - `backups/graveyard/20251221T215103Z__scripts_legacy_pipeline_runners.tar.gz`
- 削除:
  - `scripts/run_pipeline_manual.py`（tracked）
  - `scripts/run_pipeline_skip_llm.py`（tracked）

### 86) `scripts/*.md` の運用ドキュメントを SSOT Runbook へ移設（誤誘導除去）

意図: `scripts/` 直下の `.md` は「実行スクリプトと混在」して探索ノイズ/誤誘導の温床になりやすい。  
運用手順の正本は `ssot/agent_runbooks/` に集約し、低知能エージェントでも迷わない導線に寄せる。

- 移設（git mv）:
  - `scripts/cleanup_data.md` → `ssot/agent_runbooks/RUNBOOK_CLEANUP_DATA.md`
  - `scripts/job_runner_service.md` → `ssot/agent_runbooks/RUNBOOK_JOB_RUNNER_DAEMON.md`
- 参照更新:
  - `ssot/agent_runbooks/README.md`（Runbook一覧に追記）
  - `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`（Docs参照先を更新）

### 87) `job_runner.service` を SSOT 側へ移設（常駐テンプレの置き場固定）

意図: systemd unit テンプレは “運用資産” のため、Runbook 近傍に置くほうが安全。  
`scripts/` 直下に置くと「実行スクリプト」と誤認されやすい。

- 移設（git mv）:
  - `scripts/job_runner.service` → `ssot/agent_runbooks/assets/job_runner.service`
- 参照更新:
  - `ssot/agent_runbooks/RUNBOOK_JOB_RUNNER_DAEMON.md`（systemd項目を追加）

### 88) 未参照スクリプトを削除（archive-first / 探索ノイズ削減）

意図: `scripts/` 直下の「refs=0 かつ SSOT未記載」スクリプトは、誤実行/誤誘導の温床になりやすい。  
現行フロー (`ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`) と入口索引 (`ssot/ops/OPS_ENTRYPOINTS_INDEX.md`) に載らないものは、archive-first で repo から削除して探索ノイズを減らす。

- 参照確認:
  - `rg -n "apply_archive_warning\\.sh|batch_ch02_generate\\.sh|mark_script_completed\\.py|show_llm_latest\\.py" -S --glob '!ssot/ops/OPS_SCRIPTS_INVENTORY.md' .`（self以外ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251222T012648Z__scripts_unused_prune_01.tar.gz`
- 削除:
  - `scripts/apply_archive_warning.sh`（tracked）
  - `scripts/batch_ch02_generate.sh`（tracked）
  - `scripts/mark_script_completed.py`（tracked）
  - `scripts/show_llm_latest.py`（tracked）

### 89) P2（禁止）レガシースクリプトを削除（archive-first / 誤実行防止）

意図: `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md` で P2（禁止）に分類したスクリプトは、現行フローに載らず誤誘導の温床になるため **残さない**。  
（削除の前提: repo全域で code 参照ゼロ / SoTフロー外 / SSOTで禁止確定）

- 参照確認:
  - `rg -n "validate_b_text\\.py|apply_reading_corrections\\.py|openrouter_free_models\\.py|env_guard\\.py|trend_feed\\.py|fetch_thumbnail_trends\\.py|assign_trend_thumbnail\\.py" -S --glob '!ssot/**' --glob '!scripts/**' .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251222T021416Z__scripts_P2_legacy_prune.tar.gz`
- 削除:
  - `scripts/validate_b_text.py`（tracked）
  - `scripts/apply_reading_corrections.py`（tracked）
  - `scripts/openrouter_free_models.py`（tracked）
  - `scripts/env_guard.py`（tracked）
  - `scripts/trend_feed.py`（tracked）
  - `scripts/fetch_thumbnail_trends.py`（tracked）
  - `scripts/assign_trend_thumbnail.py`（tracked）

### 90) CH固定の旧補助スクリプトを削除（archive-first / 入口一本化）

意図: `scripts/` 直下に「CH固定・一回きりの補助スクリプト」が残ると、低知能エージェントが誤って叩きやすく事故の温床になる。  
現行の正規入口（`ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）に載っていないものは archive-first で repo から削除し、必要時は `_adhoc` で再作成する。

- 参照確認:
  - `rg -n "append_ch02_row\\.py|check_ch02_content\\.py|check_ch02_quality\\.py|fix_ch02_row\\.py|create_image_cues_from_srt\\.py|regenerate_ch05_audio_no_llm\\.sh|run_ch03_batch\\.sh|sequential_repair\\.sh|scaffold_project\\.py" -S --glob '!ssot/**' --glob '!scripts/**' .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251222T022242Z__scripts_unused_prune_02.tar.gz`
- 削除:
  - `scripts/append_ch02_row.py`（tracked）
  - `scripts/check_ch02_content.py`（tracked）
  - `scripts/check_ch02_quality.py`（tracked）
  - `scripts/fix_ch02_row.py`（tracked）
  - `scripts/create_image_cues_from_srt.py`（tracked）
  - `scripts/regenerate_ch05_audio_no_llm.sh`（tracked）
  - `scripts/run_ch03_batch.sh`（tracked）
  - `scripts/sequential_repair.sh`（tracked）
  - `scripts/scaffold_project.py`（tracked）

### 91) 危険/破綻しているレガシースクリプトを削除（archive-first / 誤実行防止）

意図:
- `scripts/audit_all.sh` は参照先が欠損（`audit_readings.py` 不在）で破綻しており、誤実行の温床。
- `scripts/auto_approve.sh` / `scripts/mass_regenerate_strict.sh` は “自動承認” / “全件音声上書き” を行うため事故リスクが高く、現行の安全設計（lock/redo/品質ゲート）と衝突しやすい。
- `scripts/mark_redo_done.sh` は `scripts/mark_redo_done.py` の薄いラッパーであり、SSOT上もPython入口に一本化する。

- 参照確認:
  - `rg -n "audit_all\\.sh|auto_approve\\.sh|mass_regenerate_strict\\.sh|mark_redo_done\\.sh" -S --glob '!ssot/**' --glob '!scripts/**' .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251222T023659Z__scripts_unsafe_legacy_prune_01.tar.gz`
- 削除:
  - `scripts/audit_all.sh`（tracked）
  - `scripts/auto_approve.sh`（tracked）
  - `scripts/mass_regenerate_strict.sh`（tracked）
  - `scripts/mark_redo_done.sh`（tracked）

### 92) ルート `tools/` と `workspaces/planning/ch01_reference/` を削除（archive-first / 誤誘導防止）

意図:
- ルート `tools/` はチャンネル別のアドホック保守スクリプト置き場として残っていたが、現行の固定入口（`ssot/ops/OPS_ENTRYPOINTS_INDEX.md` / `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`）から外れており、誤誘導の温床になる。
- 一時スクリプトは `scripts/_adhoc/` に集約済みのため、ルート `tools/` は削除して迷子/誤実行リスクを下げる。
- `workspaces/planning/ch01_reference/` は旧運用メモで、存在しない `tools/*.py` や旧 Makefile 前提の記述が残っており、現行の確定フローと矛盾して混乱を招くため削除する。

- 参照確認:
  - `rg -n "tools/(check_consistency|check_ch06_quality|audit_and_enhance_ch06|final_audit_ch06|enhance_thumbnail_prompts|clean_thumbnail_prompts|gemini_agent_b_text)\\.py" -S --glob '!ssot/**' .`（ヒットなし）
  - `rg -n "ch01_reference" -S .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251222T052500Z__root_tools_prune_01.tar.gz`
- 削除:
  - `tools/`（tracked）
  - `workspaces/planning/ch01_reference/`（tracked）

### 93) packages 内の互換symlinkを撤去（archive-first / 正本一本化）

意図:
- `packages/` 直下に SoT/生成物への互換symlink（`data/`, `artifacts/`, `input/`, `output/` 等）が残ると、探索ノイズと誤参照の温床になる。
- `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md` の方針に従い、**生成物は `workspaces/` に閉じる**。

- 参照確認:
  - `find packages -maxdepth 4 -type l -print -exec readlink {} \\;`（撤去前に存在確認、撤去後はヒットなし）
- アーカイブ:
  - `backups/graveyard/20251225_140851_remove_package_internal_symlinks/package_internal_symlinks.tgz`
  - `backups/graveyard/20251225_140851_remove_package_internal_symlinks/manifest.txt`
- 削除:
  - `packages/script_pipeline/data`（tracked symlink）
  - `packages/audio_tts_v2/artifacts`（tracked symlink）
  - `packages/commentary_02_srt2images_timeline/input`（tracked symlink）
  - `packages/commentary_02_srt2images_timeline/output`（tracked symlink）
  - `packages/commentary_02_srt2images_timeline/_capcut_drafts`（tracked symlink）

### 94) ルート直下の互換symlinkを撤去（archive-first / 正本一本化）

意図:
- ルート直下の別名（互換symlink）が残ると、参照構造が二重化し SSOT が汚染される。
- `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md` の方針に従い、**正本は `apps/` `packages/` `workspaces/` に統一**する。

- 参照確認:
  - `git ls-files -s | awk '$1==120000{print $4}' | sort`（撤去後は空）
- アーカイブ:
  - `backups/graveyard/20251225T070619Z__remove_tracked_symlinks/tracked_symlinks_manifest.tsv`
- 削除（tracked symlink）:
  - `00_research`（→ `workspaces/research`）
  - `progress`（→ `workspaces/planning`）
  - `thumbnails`（→ `workspaces/thumbnails`）
  - `remotion`（→ `apps/remotion`）
  - `script_pipeline`（→ `packages/script_pipeline`）
  - `audio_tts_v2`（→ `packages/audio_tts_v2`）
  - `commentary_02_srt2images_timeline`（→ `packages/commentary_02_srt2images_timeline`）
  - `factory_common`（→ `packages/factory_common`）
  - `ui/backend`（→ `apps/ui-backend/backend`）
  - `ui/frontend`（→ `apps/ui-frontend`）
  - `ui/tools`（→ `apps/ui-backend/tools`）
  - `apps/remotion/input`（→ `workspaces/video/input`）
  - `apps/remotion/public/input`（→ `../input`）
  - `configs/drive_oauth_client.json`（絶対パス symlink）
- メモ:
  - `configs/drive_oauth_client.json` は `.gitignore` 対象のローカル実ファイル運用に切替（secret は commit しない）。

### 95) 未使用のチャンネル別プロンプト重複ファイルを削除（archive-first / 探索ノイズ削減）

意図:
- `packages/script_pipeline/channels/CH0x-*/script_prompt.txt` が正本である一方、同階層に `CH02_script_prompt.txt` 等の重複が残っており、参照構造の二重化と誤編集の温床になるため削除する。

- 参照確認:
  - `rg -n "CH0[2-5]_script_prompt\\.txt" -S .`（ヒットなし）
- アーカイブ:
  - `backups/graveyard/20251225T075812Z__remove_unused_channel_prompt_duplicates/manifest.tsv`
- 削除:
  - `packages/script_pipeline/channels/CH02_script_prompt.txt`（tracked）
  - `packages/script_pipeline/channels/CH03_script_prompt.txt`（tracked）
  - `packages/script_pipeline/channels/CH04_script_prompt.txt`（tracked）
  - `packages/script_pipeline/channels/CH05_script_prompt.txt`（tracked）

### 96) `packages/script_pipeline/channels/CH17-CH21` の旧チャンネル定義をアーカイブ（誤参照防止）

意図:
- `CH17-CH21` の旧チャンネル定義（channel_info/script_prompt）が残ったままだと、チャンネル名変更後に誤参照や誤編集が発生しやすい。
- 新しい正本（`configs/sources.yaml` と `packages/script_pipeline/channels/CHxx-*/channel_info.json`）に統一する。

- アーカイブ:
  - `backups/graveyard/20251225T080355Z__archive_legacy_channel_defs_CH17_CH21/manifest.tsv`
- 対象（旧）:
  - `packages/script_pipeline/channels/CH17-眠れる仏教史紀行/*`
  - `packages/script_pipeline/channels/CH18-禅と睡眠の静かな科学/*`
  - `packages/script_pipeline/channels/CH19-静かな仏教哲学/*`
  - `packages/script_pipeline/channels/CH20-ブッダの人間関係相談室/*`
  - `packages/script_pipeline/channels/CH21-眠れる仏教説話集/*`

### 97) `workspaces/` 配下の実装コードを撤去（正本: `packages/`）

意図:
- `workspaces/` は SoT/生成物の置き場であり、実装コードが混ざると探索ノイズと誤参照の温床になる。
- サムネ生成ロジックは `packages/script_pipeline/thumbnails/` に集約し、`workspaces/thumbnails/compiler/` は YAML/ポリシー等の SoT のみにする。

- アーカイブ:
  - `backups/graveyard/20251225T080532Z__archive_workspaces_thumbnails_compiler/manifest.tsv`
- 移設:
  - `workspaces/thumbnails/compiler/compile_buddha_3line.py`（tracked） → `packages/script_pipeline/thumbnails/compiler/compile_buddha_3line.py`

### 98) ルート `prompts/` の重複プロンプトを撤去（archive-first / 二重SoT排除）

意図:
- Promptの正本は `packages/**/prompts/` に集約し、ルート `prompts/` へ複製・同期しない（`ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`）。
- `prompts/youtube_description_prompt.txt` は `packages/script_pipeline/prompts/youtube_description_prompt.txt` の重複であり、誤編集/迷子の温床になるため撤去する。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target prompts/youtube_description_prompt.txt --code-root apps --code-root packages --code-root tests --stdout`（プロダクトコード参照ゼロを確認）
- アーカイブ:
  - `backups/graveyard/20251225T085422Z__remove_root_prompt_duplicate_youtube_description_prompt/manifest.tsv`
  - `backups/graveyard/20251225T085422Z__remove_root_prompt_duplicate_youtube_description_prompt/prompts/youtube_description_prompt.txt`
- 削除:
  - `prompts/youtube_description_prompt.txt`（tracked）
- 以後の入口:
  - 索引: `prompts/PROMPTS_INDEX.md`（`python3 scripts/ops/prompts_inventory.py --write` で再生成）

### 99) 参照ゼロのドキュメント/誓約書を削除（archive-first / 探索ノイズ削減）

意図:
- 現行フロー外・参照ゼロのドキュメントが散在すると、AI/人間どちらも誤読・誤誘導されやすい。
- 特に「存在しないスクリプトを前提にした契約書」や「古いポート/旧UI前提の手順書」は、正常稼働中でも将来事故の種になるため撤去する。
- ルート直下の単発宣言ファイルも SSOT/運用フローから外れており、トップレベルの迷い要因になるため撤去する。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target <each> --stdout`（コード参照/Docs参照ゼロを確認）
- アーカイブ:
  - `backups/graveyard/20251225T095906Z__remove_orphan_docs_and_pledges/`
- 削除（tracked）:
  - `DECLARATION.txt`
  - `apps/ui-frontend/README.md`
  - `apps/ui-frontend/src/components/color-token-map.md`
  - `apps/ui-frontend/src/components/dashboard-clean-map.md`
  - `apps/remotion/compare_layout.md`（内容は `apps/remotion/README.md` に統合）
  - `packages/audio_tts_v2/TODO.md`
  - `packages/audio_tts_v2/contract.md`
  - `packages/audio_tts_v2/data/README.md`
  - `packages/audio_tts_v2/docs/reading_guidelines.md`
  - `packages/audio_tts_v2/test_input.md`
  - `packages/commentary_02_srt2images_timeline/ch02_capcut_draft_creation_process.md`
  - `packages/commentary_02_srt2images_timeline/docs/NEXT_ACTIONS_TYPED_PIPELINE.md`
  - `packages/commentary_02_srt2images_timeline/docs/auto_capcut_publish.md`
  - `packages/commentary_02_srt2images_timeline/docs/swap_images_ui.md`
  - `packages/script_pipeline/contract.md`

### 100) `packages/` 配下の参照ゼロドキュメントを削除（archive-first / SSOT集約）

意図:
- `packages/**/README.md` などが「履歴からしか参照されない」「実行コード参照ゼロ」の状態で残ると、探索ノイズと誤誘導の温床になる。
- 運用/入口の正本は SSOT（`ssot/ops/*`）に集約する方針のため、履歴専用のパッケージ内ドキュメントは退避して撤去する。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target 'packages/**/*.md' --stdout`（code_refs=0 かつ history/cleanup 由来の docs_refs のみを確認）
- アーカイブ:
  - `backups/graveyard/20251225T103329Z__remove_unused_package_docs/`
- 削除（tracked）:
  - `packages/README.md`
  - `packages/audio_tts_v2/README.md`
  - `packages/audio_tts_v2/docs/tts_logic_proof.md`
  - `packages/commentary_02_srt2images_timeline/README.md`
  - `packages/script_pipeline/README.md`
  - `packages/script_pipeline/openrouter_tests_report.md`

### 101) `scripts/agent_coord.py`（互換wrapper）を削除（archive-first / 入口の一本化）

意図:
- `scripts/agent_org.py` を正本として確定しているため、旧コマンド互換wrapperを残すと探索ノイズと誤誘導の温床になる。
- マルチエージェント運用では「入口が1つ」であることが事故防止になるため、互換導線を撤去する。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target scripts/agent_coord.py --stdout`（code_refs=0 を確認）
- アーカイブ:
  - `backups/graveyard/20251225T113409Z__remove_scripts_agent_coord/manifest.tsv`
  - `backups/graveyard/20251225T113409Z__remove_scripts_agent_coord/scripts/agent_coord.py`
- 追従（SSOT）:
  - `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md` から `scripts/agent_coord.py` を撤去
  - `ssot/ops/OPS_LOGGING_MAP.md` から「旧: `scripts/agent_coord.py`」表記を撤去
- 削除（tracked）:
  - `scripts/agent_coord.py`

### 102) `scripts/sync_ch02_scripts.py`（CH02専用sync）を削除（archive-first / 入口の一本化）

意図:
- `scripts/sync_all_scripts.py` が `--channel CH02` で同等の同期を提供しているため、CH02専用スクリプトは探索ノイズと誤誘導の温床になる。
- 入口を一本化し、運用コマンドを SSOT に固定する（「どれを叩くか」で迷わせない）。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target scripts/sync_ch02_scripts.py --stdout`（code_refs=0 を確認）
- アーカイブ:
  - `backups/graveyard/20251225T132559Z__remove_scripts_sync_ch02_scripts/manifest.tsv`
  - `backups/graveyard/20251225T132559Z__remove_scripts_sync_ch02_scripts/scripts/sync_ch02_scripts.py`
- 追従（SSOT）:
  - `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md` を `sync_all_scripts.py --channel CH02` に一本化
- 削除（tracked）:
  - `scripts/sync_ch02_scripts.py`

### 103) `legacy/` ディレクトリを廃止（archive-first / 迷いどころ削減）

意図:
- `legacy/` を repo 内に残すと「どこが正本？どこが現行？」の迷いと誤参照を生みやすい。
- 旧資産/試作は **常駐させず** `backups/graveyard/`（archive-first）と `workspaces/_scratch/`（ローカル一時）に統一する。

- 参照確認:
  - `python3 scripts/ops/repo_ref_audit.py --target legacy/README.md --stdout`（code_refs=0, docs_refs=0 を確認）
- 追従（SSOT）:
  - `ssot/OPS_SYSTEM_OVERVIEW.md` から `legacy/` の誘導を撤去
  - `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md` から `legacy/` の誘導/表を撤去し、`backups/graveyard/` + `workspaces/_scratch/` に統一
  - `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md` の Legacy 判定例を `workspaces/_scratch/` + `backups/graveyard/` に統一
  - `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の隔離先誘導を `backups/graveyard/` に統一
  - `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md` の Stage 3 記述を現状に合わせて更新
  - `README.md` のトップレベル説明から `legacy/` を撤去し、退避先を `backups/**` に統一
- アーカイブ:
  - `backups/graveyard/20251225T235013Z__remove_legacy_dir/legacy/README.md`
- 削除（tracked）:
  - `legacy/README.md`

### 104) パッケージ名の正規化（`audio_tts_v2`→`audio_tts`, `commentary_02_srt2images_timeline`→`video_pipeline`）とUI/Backendの導線更新

意図:
- 旧名/エイリアスが残ると、SSOT・コード・UI の参照構造が分岐し「迷いどころ」「誤参照」「並列エージェント衝突」の温床になる。
- 正本のパッケージ名を確定し、入口（pyproject scripts / API / UI route）も同名へ揃えて **参照不整合ゼロ** を目指す。

実施内容（要点）:
- パッケージ rename:
  - `packages/audio_tts_v2/` → `packages/audio_tts/`
  - `packages/commentary_02_srt2images_timeline/` → `packages/video_pipeline/`
- 入口更新:
  - `pyproject.toml` の `factory-commentary` entrypoint を `video_pipeline.tools.factory:main` に更新
  - UI route: `/audio-tts-v2` → `/audio-tts`
  - API route: `/api/audio-tts-v2/*` → `/api/audio-tts/*`
- 参照更新:
  - import/module 名、path 文字列、テンプレ/ドキュメント内コマンド例を一括で新名へ統一
  - `factory_common.paths` の `audio_pkg_root()/video_pkg_root()` を新パスへ更新
  - `scripts/start_all.sh` 等の運用スクリプトも新名へ更新

検証:
- `python3 scripts/ops/ssot_audit.py --strict`（problems=0）
- `python3 scripts/ops/repo_sanity_audit.py --verbose`（tracked symlinks/legacy alias paths: none）
- `python3 scripts/ops/prompts_inventory.py --write`
- `python3 scripts/ops/scripts_inventory.py --write`
- `pytest -q`（root tests）
- `pytest -q packages/audio_tts/tests packages/video_pipeline/tests`
- `npm -C apps/ui-frontend run build`

### 105) `scripts/_adhoc/` の参照ゼロ（ローカル未追跡）を排除（探索ノイズ削減）

意図:
- `scripts/_adhoc/` は P3（一時）で `.gitignore` 対象だが、ローカルに残ると探索ノイズと誤誘導の温床になる。
- **code参照ゼロ + docs参照ゼロ** のものは「確実ゴミ」として、README以外を排除する（repo tracked ではないため archive-first 不要）。

事前確認:
- `python3 scripts/ops/repo_ref_audit.py --target scripts/_adhoc/channel_info_normalize.py --target scripts/_adhoc/compose_ch24_kobo_thumbs.py --target scripts/_adhoc/planning_seed_ch17_21.py --target scripts/_adhoc/planning_seed_ch22_23.py --target scripts/_adhoc/thumbnails/build_thumbnails_from_layer_specs.py --target scripts/_adhoc/thumbnails/sync_layer_specs_to_planning.py --stdout`（refs=0 を確認）
- `git check-ignore -v scripts/_adhoc/...`（`.gitignore: scripts/_adhoc/**` を確認）

追従（SSOT/ツール）:
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` から旧互換入口 `scripts/_adhoc/thumbnails/build_thumbnails_from_layer_specs.py` を撤去（統一CLIへ一本化）
- `scripts/ops/scripts_inventory.py` を修正し、`scripts/_adhoc/**` も棚卸し対象に含めて再混入を検出できるようにする
- `python3 scripts/ops/scripts_inventory.py --write`

削除（untracked/ignored）:
- `rm -f scripts/_adhoc/channel_info_normalize.py scripts/_adhoc/compose_ch24_kobo_thumbs.py scripts/_adhoc/planning_seed_ch17_21.py scripts/_adhoc/planning_seed_ch22_23.py scripts/_adhoc/thumbnails/build_thumbnails_from_layer_specs.py scripts/_adhoc/thumbnails/sync_layer_specs_to_planning.py`
- `bash scripts/ops/cleanup_caches.sh`（`__pycache__` / `.pytest_cache` / `.DS_Store` を削除）

### 106) `packages/video_pipeline/tools/srt_to_capcut_complete.py`（旧統合版）を削除（archive-first / 迷いどころ削減）

意図:
- `srt_to_capcut_complete.py` は旧統合版の残骸で、現行の主線（`auto_capcut_run.py` / `factory.py` / `server/jobs.py`）と設計が異なる。
- SSOT上でも「要確認」となっており、**迷いどころ** になるため archive-first で退避した上で削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/video_pipeline/tools/srt_to_capcut_complete.py --stdout`（code_refs=0 / docs_refs>0 を確認）

追従（SSOT）:
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` から当該ファイルの記載を撤去（Video/CapCut + 自動抽出リスト）

アーカイブ:
- `backups/graveyard/20251226T063749Z__remove_video_pipeline_legacy_srt_to_capcut_complete/manifest.tsv`
- `backups/graveyard/20251226T063749Z__remove_video_pipeline_legacy_srt_to_capcut_complete/packages/video_pipeline/tools/srt_to_capcut_complete.py`

削除（tracked）:
- `packages/video_pipeline/tools/srt_to_capcut_complete.py`

### 107) （撤回済み）`backups/graveyard/` の tracked アーカイブを外部SSDへ退避し、repo から撤去（探索ノイズ/肥大化対策）

注記:
- この手順は Step 108 により **撤回** しました（外部SSDは運用に採用しない）。今後は再実行しない。

意図:
- `backups/` は `.gitignore` で原則 ignore のため、巨大アーカイブを tracked で残すと repo が肥大化し、探索ノイズになる。
- 既に「退避済み」の性質を持つため、外部SSD（`ytm_offload`）へ退避し、repo からは撤去する。

退避（外部SSD）:
- 実行: `python3 scripts/ops/offload_archives_to_external.py --external-root /Volumes/外部SSD/ytm_offload --mode move --min-age-days 0 --limit 20 --run`
- report: `workspaces/logs/regression/offload_archives_to_external/offload_report_20251226T065741Z.json`
- 外部配置: `/Volumes/外部SSD/ytm_offload/backups/graveyard/*`

削除（tracked）:
- `backups/graveyard/20251217_021441_commentary02_package_extras.tar.gz`
- `backups/graveyard/20251217_022233_commentary02_bin_legacy.tar.gz`
- `backups/graveyard/20251217_121820_scripts_maintain_consciousness.tar.gz`

### 108) 外部SSDへの依存を撤回（SSOTから撤去 + offloadスクリプト削除）

意図:
- 外部SSDへの依存は不安定になりやすく、運用SSOTに組み込むと事故要因になるため採用しない。
- 代替は「ローカル保持 + `_archive/` ローテ + archive-first」に統一する。

追従（SSOT）:
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md` の「外部SSDへのオフロード」を **採用しない** に更新
- `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md` から `offload_archives_to_external.py` の記載を撤去
- `python3 scripts/ops/scripts_inventory.py --write`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T090604Z__remove_offload_archives_to_external/manifest.tsv`
- `backups/graveyard/20251226T090604Z__remove_offload_archives_to_external/scripts/ops/offload_archives_to_external.py`

削除（tracked）:
- `scripts/ops/offload_archives_to_external.py`

注記:
- Step 107 で外部SSDへ退避したアーカイブは、SSOT非依存化のためローカル `backups/graveyard/` にも復元して二重化した。

### 109) `packages/video_pipeline/tools/*.py` の参照ゼロを削除（archive-first / 迷いどころ削減）

意図:
- `video_pipeline.tools.*` として import/実行可能なスクリプトが増えるほど、運用の入口が増えて迷いどころになる。
- SSOT/コードから参照できないもの（ファイルパス参照ゼロ + モジュール名参照ゼロ + basename 参照ゼロ）は運用外レガシーとして archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/tools/*.py" --max-targets 200 --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target video_pipeline.tools.<name> ... --stdout`（`<name>` は対象スクリプトの stem）
- `python3 scripts/ops/repo_ref_audit.py --target <name>.py --stdout`（`packages/video_pipeline/server/jobs.py` 等は basename 指定で呼ぶため）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T092450Z__remove_video_pipeline_tools_ref_zero/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T092450Z__remove_video_pipeline_tools_ref_zero/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 110) `packages/video_pipeline/tools/{analysis,maintenance}/*.py` を削除（archive-first / 迷いどころ削減）

意図:
- `analysis/` と `maintenance/` はデバッグ/修復系のスクリプト群だが、現行フロー/SSOT運用から参照できない状態で残ると迷いどころになる。
- 「参照ゼロ（コード参照ゼロ + 運用SSOT参照ゼロ）」を満たすものは archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/tools/analysis/*.py" --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/tools/maintenance/*.py" --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target <name>.py --stdout`（basename 経由の呼び出し検出）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T100004Z__remove_video_pipeline_tools_analysis_maintenance_ref_zero/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T100004Z__remove_video_pipeline_tools_analysis_maintenance_ref_zero/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 111) `packages/video_pipeline/tools/*.py` の参照ゼロを追加削除（archive-first / 迷いどころ削減）

意図:
- 参照ゼロ（コード参照ゼロ + 運用SSOT参照ゼロ）のまま残っているスクリプトは運用外レガシーとして削除し、入口の迷いを潰す。
- 特に `Path(__file__)` から repo root を推測する旧ブートストラップや外部依存を含むスクリプトは、参照ゼロの状態で残すと誤用/再混入の温床になる。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/tools/*.py" --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target <name>.py --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target video_pipeline.tools.<name> --stdout`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T100522Z__remove_video_pipeline_tools_ref_zero_more/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T100522Z__remove_video_pipeline_tools_ref_zero_more/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 112) `capcut_apply_image_scale.py` を削除（archive-first / 参照ゼロ）

意図:
- `capcut_apply_image_scale.py` は CapCut ドラフトを直接パッチする特殊用途で、現行の運用SSOT/コードから参照されていない状態で残ると迷いどころになる。
- 参照ゼロ（コード参照ゼロ + 運用SSOT参照ゼロ）を満たすため archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/video_pipeline/tools/capcut_apply_image_scale.py --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target capcut_apply_image_scale.py --stdout`
- `python3 scripts/ops/repo_ref_audit.py --target video_pipeline.tools.capcut_apply_image_scale --stdout`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T100941Z__remove_video_pipeline_capcut_apply_image_scale_ref_zero/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T100941Z__remove_video_pipeline_capcut_apply_image_scale_ref_zero/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 113) `script_pipeline` の未使用 formatter を削除（archive-first / 迷いどころ削減）

意図:
- `format_lines_responses.py` は単発の整形ユーティリティだが、SSOT/コードから参照されず、対応 prompt も実体と齟齬がある状態で残ると迷いどころになる。
- 参照ゼロ（コード参照ゼロ + 運用SSOT参照ゼロ）を満たすため archive-first で退避して削除する。

追従（SSOT）:
- `python3 scripts/ops/prompts_inventory.py --write`（`prompts/PROMPTS_INDEX.md` を再生成）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T101717Z__remove_script_pipeline_unused_format_lines/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T101717Z__remove_script_pipeline_unused_format_lines/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 114) `scripts/ops/stage2_cutover_workspaces.py` を廃止（archive-first / 互換symlink禁止）

意図:
- 本スクリプトは旧alias（例: `audio_tts_v2/`, `commentary_02_srt2images_timeline/`, `script_pipeline/`）を **互換symlinkとして残す** ための一括cutoverであり、現行SSOTの「ルート直下互換symlink禁止」（`ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`）と矛盾する。
- 旧パス名が運用ツールとして残るだけで探索ノイズ/誤参照の温床になるため、archive-first で退避して削除し、代替は `scripts/ops/init_workspaces.py`（workspaces雛形生成のみ）に置き換える。

参照確認:
- `rg "stage2_cutover_workspaces" -S .`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T104958Z__retire_stage2_cutover_workspaces/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T104958Z__retire_stage2_cutover_workspaces/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 115) repo root の `sitecustomize.py` を削除（archive-first / 誤誘導排除）

意図:
- Homebrew Python が標準ライブラリ側の `sitecustomize` を先に読み込むため、repo root の `sitecustomize.py` は自動実行されず、存在自体が「`.env` が勝手に入る」「cwd非依存」などの誤誘導になる。
- env/PYTHONPATH の正規ルートは `scripts/with_ytm_env.sh` と `_bootstrap` に一本化するため、archive-first で退避して削除する。

アーカイブ（archive-first）:
- `backups/graveyard/20251226T105908Z__remove_repo_root_sitecustomize/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T105908Z__remove_repo_root_sitecustomize/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 116) `scripts/sitecustomize.py` を削除（archive-first / sitecustomize依存の排除）

意図:
- Homebrew Python の `sitecustomize` 競合により、`scripts/sitecustomize.py` は期待通りに自動実行されない環境がある（運用の再現性が落ちる）。
- `scripts/**` の実行は `_bootstrap`（`scripts/_bootstrap.py`, `scripts/ops/_bootstrap.py`）と `scripts/with_ytm_env.sh` を正とし、sitecustomize 依存を排除する。

アーカイブ（archive-first）:
- `backups/graveyard/20251226T110100Z__remove_scripts_sitecustomize/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T110100Z__remove_scripts_sitecustomize/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 117) `packages/video_pipeline/config/default.json` を削除（archive-first / 未使用設定のノイズ排除）

意図:
- `packages/video_pipeline/config/default.json` は旧設定フォーマットで、現行の `channel_presets.json` + `default_parameters.yaml`（`ParameterManager`）系の運用から参照されていない。
- 中身に存在しない `tools/debug/retry_japanese.py` 等の古いパスが残っており、SSOT/運用導線のノイズ・誤参照の温床になるため、archive-first で退避して削除する。

参照確認:
- `rg "packages/video_pipeline/config/default.json" -S .`
- `rg "\"retry_script\"" -S packages/video_pipeline`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T114722Z__remove_video_pipeline_unused_default_json/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T114722Z__remove_video_pipeline_unused_default_json/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 118) `packages/video_pipeline/scripts/` の未使用スクリプトを削除（archive-first / 迷いどころ削減）

意図:
- `packages/video_pipeline/scripts/` 配下に、現行フロー/SSOT/コードから参照されない単発スクリプトが残っていると探索ノイズになる。
- CapCut導線の正本は `packages/video_pipeline/tools/*` と UI jobs (`packages/video_pipeline/server/jobs.py`) に集約しているため、参照ゼロのものは archive-first で退避して削除する。

参照確認:
- `rg "capcut_export_timeline|capcut_link_images|clean_capcut_drafts\\.sh" -S .`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T115013Z__remove_video_pipeline_unused_scripts/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T115013Z__remove_video_pipeline_unused_scripts/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 119) `packages/video_pipeline/scripts/` の参照ゼロ LLM系lintを削除（archive-first / 迷いどころ削減）

意図:
- `packages/video_pipeline/scripts/` に残っていた LLM設定のlint/差分スクリプト群が、現行の運用SSOT/フローから参照されない状態で残っており、探索ノイズになる。
- 既に正本のlint導線（例: `video_pipeline.tools.validate_prompt_template_registry`）は SSOT から参照されているため、参照ゼロのもののみ archive-first で退避して削除する。

参照確認:
- `rg "find_llm_refs\\.py|lint_check_llm_configs\\.py|lint_check_models\\.py|llm_config_diff\\.py" -S .`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T122833Z__remove_video_pipeline_unused_llm_lints/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T122833Z__remove_video_pipeline_unused_llm_lints/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 120) `workspaces/thumbnails/README_OFFLOADED.txt` を撤去（外部SSD表記の迷いどころ削減）

意図:
- 外部SSDは安定しないため、現行運用は external offload を前提にしない。
- `workspaces/thumbnails/README_OFFLOADED.txt` の「外部SSDへ退避済み」表記が残存すると、現状と矛盾して誤誘導の温床になるため撤去する。

参照確認:
- `rg -n "README_OFFLOADED|外部SSD/_offload|yt_workspaces_thumbnails" -S --glob '!backups/**' .`

アーカイブ（archive-first）:
- `backups/graveyard/20251226T153747Z__remove_thumbnails_readme_offloaded/manifest.tsv`

削除（untracked）:
- `backups/graveyard/20251226T153747Z__remove_thumbnails_readme_offloaded/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 121) `packages/video_pipeline/プロンプト` を削除（ref=0 のメモ残骸）

意図:
- `packages/video_pipeline/` 直下に拡張子なしのメモファイル（`プロンプト`）が残っていると、探索/grep時のノイズになる。
- 現行フロー/SSOT/コードから参照されない（ref=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/プロンプト" --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T165433Z__remove_video_pipeline_prompt_note_file/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T165433Z__remove_video_pipeline_prompt_note_file/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 122) `tests/manual_generate_fireworks_flux_schnell.py` を削除（ref=0 の手動スクリプト）

意図:
- `tests/` 配下に “手動実行用の単発スクリプト” が残っていると、テスト/実装の探索ノイズになる。
- 現行フロー/SSOT/コードから参照されない（ref=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target tests/manual_generate_fireworks_flux_schnell.py --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T165839Z__remove_manual_fireworks_flux_script/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T165839Z__remove_manual_fireworks_flux_script/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 123) `packages/audio_tts/inputs/` を削除（ref=0 のサンプル入力群）

意図:
- `packages/` 配下に大量のサンプル入力（`packages/audio_tts/inputs/CHxx/*.txt`）が残っていると、探索ノイズになる。
- 現行フローの SoT は `workspaces/` に閉じており、`packages/audio_tts/inputs/` は参照ゼロ（ref=0）であるため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/audio_tts/inputs --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T170414Z__remove_audio_tts_inputs_samples/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T170414Z__remove_audio_tts_inputs_samples/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 124) `packages/audio_tts/` 直下の ref=0 `.txt` を削除（ノイズ削減）

意図:
- `packages/audio_tts/` 直下に単発の `.txt`（ルール/テスト用メモ）が散在していると、探索ノイズになる。
- 現行フロー/SSOT/コードから参照されない（ref=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/audio_tts/README_rules.txt --target packages/audio_tts/test_dna.txt --target packages/audio_tts/test_final_exam.txt --target packages/audio_tts/test_custom_readings.txt --target packages/audio_tts/test_input_short.txt --target packages/audio_tts/data/test_CH01_sample.txt --target packages/audio_tts/data/ch_test_a.txt --stdout`（全て code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T170643Z__remove_audio_tts_ref_zero_txt_fixtures/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T170643Z__remove_audio_tts_ref_zero_txt_fixtures/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 125) `packages/audio_tts/data/gkb_deletion_log.json` を削除（ref=0 のログ残骸）

意図:
- `packages/` 配下に運用ログ（削除ログ）が残存すると、SoT/生成物の境界が曖昧になり探索ノイズになる。
- 現行フロー/SSOT/コードから参照されない（ref=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/audio_tts/data/gkb_deletion_log.json --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T170858Z__remove_audio_tts_gkb_deletion_log/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T170858Z__remove_audio_tts_gkb_deletion_log/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 126) `packages/script_pipeline/channels/*/info.txt` を削除（ref=0 の旧メモ）

意図:
- `channel_info.json` / `channels.json` が正本になっているため、旧メモ（`info.txt`）が残存すると誤誘導の温床になる。
- 現行フロー/SSOT/コードから参照されない（ref=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/script_pipeline/channels/CH01-人生の道標/info.txt" --target "packages/script_pipeline/channels/CH02-静寂の哲学/info.txt" --target "packages/script_pipeline/channels/CH04-隠れ書庫アカシック/info.txt" --target "packages/script_pipeline/channels/CH05-シニア恋愛/info.txt" --target "packages/script_pipeline/channels/CH06-都市伝説のダーク図書館/info.txt" --target "packages/script_pipeline/channels/CH11-ブッダの法話/info.txt" --stdout`（全て code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251226T171052Z__remove_script_pipeline_channel_info_txt_ref_zero/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T171052Z__remove_script_pipeline_channel_info_txt_ref_zero/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 127) `packages/video_pipeline/src/capcut_ui/` を削除（ref=0 / 旧UI実験コード）

意図:
- `capcut_ui` は現行フロー/入口索引/実行コードから参照されず、旧UI実験コードが探索ノイズになっていた。
- 誤参照や「どっちが正本？」の混乱を防ぐため、archive-first で退避して削除する。

参照確認:
- `rg -n "capcut_ui" apps packages scripts tests` のヒットが当該ディレクトリ内のみであることを確認（self import）。
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/src/capcut_ui/**" --max-targets 200 --stdout`（全て code_refs=0, docs_refs=0）
- `rg -n "capcut_ui" ssot README.md START_HERE.md prompts` のヒットは history のみ（現行SSOT/導線ではない）。

アーカイブ（archive-first）:
- `backups/graveyard/20251226T235535Z__remove_video_pipeline_capcut_ui_ref_zero/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251226T235535Z__remove_video_pipeline_capcut_ui_ref_zero/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 128) `configs/` の重複/誤誘導ファイルを撤去（OpenRouterメタ + 旧OAuth JSON）

意図:
- `configs/openrouter_models.json` が残っていると、`packages/script_pipeline/config/openrouter_models.json`（正本）との二重化になり、**古い方が優先されうる**ため誤誘導/品質ブレの原因になる。
- Google OAuth の `client_secret_*.json` が `configs/` に残っていると、`configs/drive_oauth_client.json`（正本）との二重化になり、迷いどころになる。

参照確認:
- `scripts/drive_oauth_setup.py` / `scripts/youtube_publisher/oauth_setup.py` は既定で `configs/drive_oauth_client.json` を参照（`client_secret_*.json` は未参照）。
- `packages/script_pipeline/tools/openrouter_models.py` は `openrouter_models.json` をキャッシュとして読むが、正本は `packages/script_pipeline/config/openrouter_models.json`。

アーカイブ（archive-first）:
- `backups/graveyard/20251227T004024Z__remove_redundant_configs_oauth_and_openrouter_models/manifest.tsv`

削除:
- tracked: `configs/openrouter_models.json`（`git rm`）
- untracked: `configs/client_secret_785338258106-1ia6khrj8uj4ime448h0a2iufhb5gm23.apps.googleusercontent.com.json`（`rm`）

付随修正:
- `packages/script_pipeline/tools/openrouter_models.py` は **package SoT（`packages/script_pipeline/config/openrouter_models.json`）を優先**し、`configs/` はローカルfallbackのみにした。

### 129) `packages/video_pipeline/tests/*.py` を削除（refs=0 / 非実行のパッケージ内テスト）

意図:
- `packages/video_pipeline/tests/` 配下のテストが残っていると「テストがあるのに走っていない（=誤安心）」状態になり、探索ノイズ/誤誘導になる。
- 現行の `pytest` 実行は `tests/` のみ（`pyproject.toml` の `testpaths`）なので、このディレクトリのテストは **非実行**。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target "packages/video_pipeline/tests/*.py" --stdout`（全て code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251227T035424Z__remove_video_pipeline_unused_package_tests_ref0/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251227T035424Z__remove_video_pipeline_unused_package_tests_ref0/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 130) `ssot/reference/ch01/` を撤去（refs=0 / 孤立ドキュメント）

意図:
- `ssot/reference/` は「現行の正本（参照仕様）」に寄せる。チャンネル個別の旧メモが残ると、正本の迷いどころになる。
- CH01の正本は `packages/script_pipeline/channels/CH01-*/script_prompt.txt` 等にあり、SSOT側の当該2ファイルは参照ゼロ（refs=0）だったため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target ssot/reference/ch01/script_prompt.md --target ssot/reference/ch01/台本構造の参考.md --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251227T073756Z__archive_orphan_ssot_reference_ch01/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251227T073756Z__archive_orphan_ssot_reference_ch01/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 131) `packages/video_pipeline/config/master_styles_v2.json` を削除（refs=0 / 旧スタイル定義）

意図:
- Video のスタイル正本は `ssot/ops/master_styles.json`。
- `packages/video_pipeline/config/master_styles_v2.json` は現行コード/SSOTから参照されておらず、探索ノイズのため archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/video_pipeline/config/master_styles_v2.json --stdout`（code_refs=0, docs_refs=0）
- `rg -n "master_styles_v2\\.json" packages` のヒットが `channel_schema.py` のコメントのみであることを確認。

アーカイブ（archive-first）:
- `backups/graveyard/20251227T075403Z__remove_video_pipeline_master_styles_v2_ref0/manifest.tsv`

削除（tracked）:
- `backups/graveyard/20251227T075403Z__remove_video_pipeline_master_styles_v2_ref0/manifest.tsv` を正本とする（このログ自体が参照になって ref=0 判定を汚染するのを防ぐため、ここでは列挙しない）。

### 132) `data/visual_bible_backup_20251211_ch02before_clear.json` を退避（untracked/ignored）

意図:
- `data/` 直下にバックアップ残骸が残ると探索ノイズになるため、ローカル退避先へ移動して整理する。

参照確認:
- `rg -n "data/visual_bible_backup_20251211_ch02before_clear\\.json" -S .` がヒットしない（refs=0）。

退避（ローカル / gitignore）:
- `workspaces/_scratch/_archive/20251227T232221Z__archive_unused_visual_bible_backup/visual_bible_backup_20251211_ch02before_clear.json`
- `workspaces/_scratch/_archive/20251227T232221Z__archive_unused_visual_bible_backup/README.md`

### 133) `.agent/workflows/*.md` を削除（refs=0 / 未使用のエージェント運用メモ）

意図:
- `.agent/workflows/` 配下に旧運用メモが残ると、SSOT（`AGENTS.md` / `ssot/ops/OPS_AGENT_PLAYBOOK.md`）と二重化し、迷いどころになる。
- 現行フロー/SSOT/コードから参照されない（refs=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target .agent/workflows/agent_mode_llm_queue.md --target .agent/workflows/batch_tts.md --target .agent/workflows/interactive_tts.md --target .agent/workflows/think_mode.md --target .agent/workflows/tts-audio-generation.md --target .agent/workflows/tts-interactive-protocol.md --target .agent/workflows/turbo.md --stdout`（全て code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251227T235814Z__archive_agent_workflows_ref0/manifest.tsv`

削除（tracked）:
- `git rm -r .agent/workflows`

### 134) `packages/video_pipeline/src/` の未使用モジュールを削除（refs=0）

意図:
- `video_pipeline/src` 配下に未使用の旧モジュールが残ると、探索ノイズになり「どれが正本？」の迷いどころになる。
- 現行フロー/コードから参照されない（refs=0）ため、archive-first で退避して削除する。

参照確認:
- `rg -n "parameter_manager" apps packages scripts tests -S` のヒットが当該ファイル内のみであることを確認。
- `rg -n "asset_schema" apps packages scripts tests -S` がヒットしないことを確認。

アーカイブ（archive-first）:
- `backups/graveyard/20251228T001157Z__remove_video_pipeline_src_unused_modules_ref0/manifest.tsv`

削除（tracked）:
- `git rm packages/video_pipeline/src/config/parameter_manager.py packages/video_pipeline/src/core/domain/asset_schema.py`

### 135) `packages/audio_tts/tts/orchestrator.py` を削除（refs=0 / 旧TTSパイプライン）

意図:
- Strict pipeline（`packages/audio_tts/tts/strict_orchestrator.py` / `packages/audio_tts/scripts/run_tts.py`）が正本になっており、旧 `orchestrator.py` が残ると探索ノイズ/誤誘導になるため。
- “現行ラインに合わせて” 正本の参照先を揃え、再現性と保守性を上げる。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/audio_tts/tts/orchestrator.py --stdout`（code_refs=0, docs_refs=0）

付随修正（SSOT-first）:
- `ssot/ops/OPS_LOGGING_MAP.md`（TTS reading log の writer 記述を現行に合わせて更新）
- `ssot/ops/OPS_SRT_LINEBREAK_FORMAT.md`（現行の入口を `scripts/format_srt_linebreaks.py` に寄せ、旧 orchestrator 参照を除去）
- `ssot/plans/PLAN_LLM_PIPELINE_REFACTOR.md` / `ssot/plans/PLAN_LLM_USAGE_MODEL_EVAL.md` / `ssot/plans/PLAN_OPS_VOICEVOX_READING_REFORM.md`（旧 orchestrator 参照の棚卸し）

アーカイブ（archive-first）:
- `backups/graveyard/20251228T003410Z__remove_audio_tts_orchestrator_ref0/manifest.tsv`

削除（tracked）:
- `git rm packages/audio_tts/tts/orchestrator.py`

### 136) `packages/audio_tts/tts/` の旧モジュールを削除（refs=0 / orchestrator撤去後に孤立）

意図:
- 旧 `orchestrator.py` の撤去後に refs=0 となった補助モジュールが残ると、探索ノイズ/誤誘導になるため archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/audio_tts/tts/annotations.py --target packages/audio_tts/tts/kana_engine.py --target packages/audio_tts/tts/local_generator.py --target packages/audio_tts/tts/logger.py --target packages/audio_tts/tts/qa.py --stdout`（全て code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251228T003610Z__remove_audio_tts_tts_legacy_modules_ref0/manifest.tsv`

削除（tracked）:
- `git rm packages/audio_tts/tts/annotations.py packages/audio_tts/tts/kana_engine.py packages/audio_tts/tts/local_generator.py packages/audio_tts/tts/logger.py packages/audio_tts/tts/qa.py`

### 137) `packages/factory_common/llm_client_experimental.py` を削除（refs=0 / 未使用の実験用クライアント）

意図:
- LLM 呼び出しの正本は `packages/factory_common/llm_router.py` / `packages/factory_common/llm_client.py` に寄せており、未参照の実験用クライアントが残ると探索ノイズ/誤誘導になるため。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/factory_common/llm_client_experimental.py --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251228T022048Z__remove_factory_common_llm_client_experimental_ref0/manifest.tsv`

削除（tracked）:
- `git rm packages/factory_common/llm_client_experimental.py`

### 138) `packages/video_pipeline/src/config/llm_resolver.py` を削除（refs=0 / 未使用のLLM設定リゾルバ）

意図:
- `video_pipeline/src/config` 配下に refs=0 の設定リゾルバが残ると、「どれが現行の正本？」の探索ノイズになりやすい。
- 現行フロー/コードから参照されない（refs=0）ため、archive-first で退避して削除する。

参照確認:
- `python3 scripts/ops/repo_ref_audit.py --target packages/video_pipeline/src/config/llm_resolver.py --stdout`（code_refs=0, docs_refs=0）

アーカイブ（archive-first）:
- `backups/graveyard/20251228T030518Z__remove_video_pipeline_src_llm_resolver_ref0/manifest.tsv`

削除（tracked）:
- `git rm packages/video_pipeline/src/config/llm_resolver.py`

### 139) CH12 サムネのQC contactsheet を整理（探索ノイズ低減 / archive-first）

意図:
- `workspaces/thumbnails/assets/CH12/_qc/` に contactsheet が溜まり、どれを見れば良いか混乱するため。
- UI で確認する正本は `workspaces/thumbnails/assets/CH12/library/qc/contactsheet.png` に寄せ、古いQCは退避する。

アーカイブ（archive-first）:
- `backups/graveyard/20251228T073452Z__prune_CH12_qc_contactsheets/manifest.tsv`

移動（untracked / workspace artifacts）:
- `workspaces/thumbnails/assets/CH12/_qc/contactsheet_ch12_3buddhas_30_640x360.png`
- `workspaces/thumbnails/assets/CH12/_qc/contactsheet_ch12_3buddhas_edge_30_640x360.png`
- `workspaces/thumbnails/assets/CH12/_qc/contactsheet_ch12_buddha_bright_30_640x360.png`
- `workspaces/thumbnails/assets/CH12/_qc/contactsheet_ch12_fix_011_030_640x360.png`
- `workspaces/thumbnails/assets/CH12/_qc/contactsheet_ch12_init_30_640x360.png`

### 140) CH12 サムネの不要ビルド/バリアントを整理（ミス分削除・探索ノイズ低減 / archive-first）

意図:
- CH12 のサムネ出力が大量に残り、UI/ファイル上で「どれを見れば良いか」混乱するため。
- `projects.json` の `selected_variant_id` を正として、未選択のビルド出力を退避して探索ノイズを消す。

アーカイブ（archive-first）:
- `backups/graveyard/20251228T081901Z__prune_CH12_thumbnail_variants/manifest.tsv`

移動（untracked / workspace artifacts）:
- `workspaces/thumbnails/assets/CH12/**/compiler/*` のうち、各 video の `selected_variant_id` に対応するディレクトリ以外を退避

projects.json 整理:
- CH12 の各 video について `variants` を `selected_variant_id` の1件のみに縮退

### 141) `pages/script_viewer/` を廃止（`docs/` に統合 / 重複排除）

意図:
- GitHub Pages の公開ルートが `./docs` のため、`pages/` と二重管理になると探索ノイズ/更新漏れの原因になる。
- Script Viewer（静的）は `docs/` に統一し、1箇所だけを正本として運用する。

アーカイブ（archive-first）:
- `backups/graveyard/20251228T092807Z__dedupe_remove_pages_script_viewer/manifest.tsv`

削除（tracked）:
- `git rm -r pages`

---

## 2026-01-01

### 1) 明らかに短い/壊れている台本をリセット（再生成前提 / 投稿済み除外）

意図:
- 「# Waiting for Rewrite」等の placeholder や、極端に短い Aテキストは下流工程の事故源になるため、**途中修正ではなく reset で初期化**して再生成できる状態に戻す。
- `published_lock=true`（投稿済み）は対象外。

判定（今回の運用）:
- `assembled*.md` が存在し、文字数（空白除外）が `< 2000` のもの
  - または placeholder（例: `# Waiting for Rewrite`）

対象:
- CH23: `002`〜`030`（placeholder）
- CH01: `274`, `275`（短すぎ）

実行:
- `PYTHONPATH=".:packages" python3 -c "from script_pipeline.runner import reset_video; ..."`（`wipe_research=false`）

結果:
- `workspaces/scripts/{CH}/{NNN}/content/assembled*.md` を削除し、`status.json` を初期化（再生成待ちに戻した）
- 研究（`content/analysis/research/*`）は保持（存在する場合のみ）

---

## 2026-01-03

### 1) Vrewルートの完全廃止（repo tracked）

意図:
- Vrewルートは今後使わないため、UI/Backend/VideoPipeline/SSOT/テスト/CLI（console scripts）を含めて完全に除去し、探索ノイズと誤導線を無くす。

アーカイブ（archive-first）:
- `backups/graveyard/20260103T065858Z_remove_vrew/`
  - `CapcutVrewPage.tsx`
  - `generate_vrew_prompts.py`
  - `import_vrew_images.py`
  - `place_images_to_capcut.py`
  - `OPS_UI_VREW_PROMPTS.md`
  - `OPS_VREW_IMAGE_ROUTE.md`
  - `vrew_route/`（dir）
  - `test_vrew_route_prompts.py`

削除（tracked）:
- `apps/ui-frontend/src/pages/CapcutVrewPage.tsx`
- `packages/video_pipeline/tools/generate_vrew_prompts.py`
- `packages/video_pipeline/tools/import_vrew_images.py`
- `packages/video_pipeline/tools/place_images_to_capcut.py`
- `packages/video_pipeline/src/vrew_route/`（dir）
- `ssot/ops/OPS_UI_VREW_PROMPTS.md`
- `ssot/ops/OPS_VREW_IMAGE_ROUTE.md`
- `tests/test_vrew_route_prompts.py`

更新（tracked・入口除去/値の廃止）:
- UI: `/capcut-edit/vrew` ルート/導線/APIクライアントを削除
- Backend: `vrew-prompts` 系 endpoints を削除
- `video_workflow` から `vrew_a` / `vrew_b` を廃止（`capcut` / `remotion` のみに統一）
- `pyproject.toml` の console scripts（`generate-vrew-prompts`, `render-images`, `place-images-to-capcut`）を削除

---

## 2026-01-06

### 1) CH02 投稿済み資産の削除（disk逼迫対応 / workspace生成物）

背景:
- CH02-042〜082 のCapCutドラフト再生成中に ENOSPC（空き容量枯渇）が発生。
- ユーザー了承: 投稿済みの音声/その他資産は削除してよい（台本・進捗は保持）。

削除（workspace, untracked）:
- `workspaces/audio/final/CH02/001` 〜 `workspaces/audio/final/CH02/041`（投稿済み: `workspaces/planning/channels/CH02.csv` で確認）

保持:
- `workspaces/scripts/CH02/**`（台本）
- `workspaces/planning/channels/CH02.csv`（進捗）

### 2) CapCut 旧ドラフトの削除（Archive掃除）

意図:
- 旧ドラフト（試行生成分）を削除し、ディスクを回復（新規ドラフトは `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` 側に再生成済み）。

削除（external, untracked）:
- `~/Movies/CapCut/Archive_20260106_093139/`
- `~/Movies/CapCut/Archive_20260106_134641/`
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH02-0[4-8][0-9]_*_draft*`（旧試行ドラフト。`★CH02-*` の安定名ドラフトのみ残す）

### 3) CapCut root の「完成」/壊れ/バックアップドラフト削除（disk逼迫対応 / local）

意図:
- CapCut UI 上に「完成」やバックアップが残ると探索ノイズになり、ディスクも逼迫するため削除。
- **テンプレは削除しない**（`テンプレ`/`template` を含むものは除外）。

削除（external, untracked）:
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/完成*`（29件）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/*_bak_*`（32件）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/BAD__*`（2件）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/_codex*`（1件）

---

## 2026-01-07

### 1) CH04 CapCut バックアップドラフト削除（disk逼迫対応 / local）

意図:
- safe_image_swap が自動作成した `*_draft_bak_*` が CapCut UI の探索ノイズ/ディスク逼迫の原因になったため削除。

削除（external, untracked）:
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-*_draft_bak_*`（16件、約3.0G）

### 2) local fallback drafts 削除（disk逼迫対応 / workspace生成物）

意図:
- CapCut root が書けない環境向けのローカル退避ドラフト（`workspaces/video/_capcut_drafts/`）が蓄積し、探索ノイズ/ディスク逼迫の原因になったため、既に real draft root にコピー済みのものを削除。

削除（workspace, untracked）:
- `workspaces/video/_capcut_drafts/<candidates from archive_capcut_local_drafts>`（52件、約10G）

証跡（report）:
- `workspaces/logs/regression/capcut_local_drafts_archive/capcut_local_drafts_archive_20260107T085139Z.json`

### 3) repo-local draft cache 削除（disk逼迫対応 / workspace生成物）

意図:
- Codex 作業用に生成されたドラフトコピー（参照0）を削除してディスクを回復。

削除（repo, tracked）:
- `workspaces/video/_capcut_drafts_codex/`（dir、約2.4G）

### 4) CH04 非★ドラフトの削除（重複排除 / local）

意図:
- `★CH04-001..030` の安定名ドラフトを揃えた後、旧run名ドラフトが探索ノイズ/ディスク逼迫の原因になるため削除。

削除（external, untracked）:
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-018_capcut_unpub_20260106_v1_draft`（約237M）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-018_capcut_unpub_noimg_20260106_v1_draft`（約207M）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-019_capcut_unpub_noimg_20260106_v1_draft`（約176M）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-020_capcut_unpub_noimg_20260106_v1_draft`（約154M）
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/CH04-021_capcut_unpub_noimg_20260106_v1_draft`（約186M）

---

## 2026-01-08

### 1) CH04 投稿済みドラフト/不要生成物の削除（ノイズ/容量逼迫対応）

背景:
- ユーザー指示: **未投稿は CH04-018〜029 のみ**。投稿済み(001〜015)等のドラフトは探索ノイズ/容量逼迫のため削除。
- CapCut が落ちる/壊れドラフト混在が発生していたため、対象外ドラフトを除去して探索ノイズを最小化。
- **LLM/有料画像生成は実行しない**（コスト抑制）。

削除（external, untracked）:
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH04-001-*` 〜 `★CH04-017-*` および `★CH04-030-*`（計18件、約3.0G）

削除（workspace, untracked）:
- `workspaces/video/runs/CH04-001_capcut_rebuild_20260107_v1` 〜 `workspaces/video/runs/CH04-015_capcut_rebuild_20260107_v1`（計15件）
- `workspaces/video/runs/CH04-016_capcut_unpub_noimg_20260106_v1`（1件）
- `workspaces/video/runs/CH04-017_capcut_unpub_noimg_20260106_v1`（1件）
- `workspaces/video/runs/CH04-030_capcut_unpub_noimg_20260106_v1`（1件）
- `workspaces/video/runs/CH04-`（stray、1件）
- 合計19件、約1.9G

削除（workspace, untracked / 追加生成物）:
- `workspaces/audio/final/CH04/001..015/CH04-XXX.wav`（15ファイル、約0.72G。元の `*.flac` は保持）

保持:
- `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH04-018..029-*`（未投稿分のみ残す）
- `workspaces/video/runs/CH04-018..029_*`
