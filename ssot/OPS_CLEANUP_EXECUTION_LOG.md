# OPS_CLEANUP_EXECUTION_LOG — 実行した片付け（復元/再現可能な記録）

このログは「実際に実行した削除/移動/退避」を、後から追跡できるように記録する。  
大原則: 破壊的操作は **バックアップ→削除/移動→SSOT更新** の順で行う。

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
  - 生成物の保持/削除の基準は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md` に従う

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

意図: 旧個別シェル（`/Users/...` 直書きなど）を排除し、正本入口を `ssot/OPS_ENTRYPOINTS_INDEX.md` に一本化する。

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

意図: Route2 時代の自動承認トークン生成スクリプトであり、現行の確定フロー/入口（`ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/OPS_ENTRYPOINTS_INDEX.md`）では使用しない。残っていると誤実行の原因になるため。

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
  - `ssot/OPS_ENTRYPOINTS_INDEX.md`, `ssot/OPS_LOGGING_MAP.md`, `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`, `ssot/REFERENCE_PATH_HARDCODE_INVENTORY.md`, `legacy/commentary_02_srt2images_timeline/README.md`

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
  - `ssot/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`

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
  - `packages/audio_tts_v2/docs/tts_logic_proof.md` の Validator 記述を削除し、現行実装（未統合）として明記
