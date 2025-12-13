# OPS_AGENT_PLAYBOOK — 低知能エージェントでも迷わない運用ガイド（SSOT）

目的: 7+エージェントが並列で動いても **処理フローを壊さず**、かつ **不要物を増やさず** 作業できる状態を作る。

このドキュメントは「実装方針」ではなく **運用ルール**（やり方の固定）です。

---

## 1. 正本（SSOT）と優先順位

エージェントは必ず以下を正本として扱う。矛盾があれば、**先にSSOTを直してから実装**する。

- 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 確定ロジック（最終チェック）: `ssot/【消さないで！人間用】確定ロジック`
- 入口索引: `ssot/OPS_ENTRYPOINTS_INDEX.md`
- I/Oスキーマ: `ssot/OPS_IO_SCHEMAS.md`
- ログ配置: `ssot/OPS_LOGGING_MAP.md`
- 生成物の保持/削除: `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- 片付けの実行記録: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`
- 変更履歴: `ssot/history/HISTORY_codex-memory.md`

---

## 2. 作業開始の儀式（必須）

### 2.1 自己識別（推奨）
```bash
export LLM_AGENT_NAME=Mike
python scripts/agent_org.py agents start --name Mike --role worker
```

### 2.2 Orchestrator/lock を確認（強制）
1) 触るファイル/ディレクトリに lock がないか確認:
```bash
python scripts/agent_org.py locks --path ssot/OPS_CONFIRMED_PIPELINE_FLOW.md
python scripts/agent_org.py locks --path commentary_02_srt2images_timeline/tools/auto_capcut_run.py
```

2) 触る範囲に lock を置く（zshのglob展開事故防止のため **必ずクォート**）:
```bash
python scripts/agent_org.py lock 'commentary_02_srt2images_timeline/tools/**' --mode no_touch --ttl-min 60 --note 'working'
```

lock がある範囲は **触らない**。必要なら memo/request で調整する（`ssot/PLAN_AGENT_ORG_COORDINATION.md`）。

---

## 3. “壊さない”ための不変条件（強制）

### 3.1 SoT（正本）の定義（固定）
- Planning SoT: `workspaces/planning/channels/{CH}.csv`（互換: `progress/channels/{CH}.csv`）
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json` + `content/assembled*.md`（互換: `script_pipeline/data/...`）
- Audio SoT: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav|.srt`（互換: `audio_tts_v2/artifacts/final/...`）
- Video run SoT: `workspaces/video/runs/{run_id}/`（互換: `commentary_02_srt2images_timeline/output/...`）
- Thumbnail SoT: `thumbnails/projects.json` と `thumbnails/assets/{CH}/{NNN}/`

### 3.2 パス直書き禁止（移設耐性）
- `Path(__file__).resolve().parents[...]` を新規に増やさない。
- `factory_common.paths` の `repo_root()/logs_root()/video_pkg_root()/...` を使う。

### 3.3 機械分割禁止（契約/品質）
- cues/セクション分割は等間隔にしない（文脈ベースで切る）。
- THINK/AGENT時は `visual_image_cues_plan` を優先し、stop/resume ループを減らす。

---

## 4. よくある作業（迷わない手順）

### 4.1 台本→音声→動画（主線）
入口は `ssot/OPS_ENTRYPOINTS_INDEX.md` を正とする。

- 台本: `python -m script_pipeline.cli ...`
- 音声: `python audio_tts_v2/scripts/run_tts.py ...`
- 動画/CapCut: `python commentary_02_srt2images_timeline/tools/auto_capcut_run.py ...`

### 4.2 THINK MODE（APIなしで止めて続行）
```bash
./scripts/think.sh --all-text -- python -m script_pipeline.cli run-all --channel CH06 --video 033
python scripts/agent_runner.py list
python scripts/agent_runner.py prompt <TASK_ID>
```

### 4.3 “確実ゴミ”の削除（強制手順）
1) `rg` で参照ゼロ確認（Docs言及は除外してよい）  
2) tracked の場合は `backups/graveyard/` にアーカイブしてから削除  
3) `ssot/OPS_CLEANUP_EXECUTION_LOG.md` に記録  

untracked キャッシュはいつでも削除してよい:
```bash
bash scripts/ops/cleanup_caches.sh
```

---

## 5. 変更の残し方（小さく刻む）

### 5.1 原則: 小コミット
- 1コミット = 1目的（1つの不具合/1つの移行/1つのcleanup）

### 5.2 git が使えない場合（パッチ運用）
環境によって `.git` が書けず `git add/commit` が失敗することがある。  
その場合はパッチを保存し、Orchestrator/人間が apply→commit する。

```bash
bash scripts/ops/save_patch.sh --label stage2_paths
```

出力: `backups/patches/YYYYMMDD_HHMMSS_<label>.patch`

---

## 6. 完了条件（DoD）
- 触った範囲の lock を解除（または TTL を短くして終了）。
- 必要なSSOT（フロー/ロジック/索引/履歴/cleanupログ）を更新。
- テスト/ビルド/スモーク（該当範囲のみ）を実行し、結果を `ssot/history/HISTORY_codex-memory.md` に残す。
