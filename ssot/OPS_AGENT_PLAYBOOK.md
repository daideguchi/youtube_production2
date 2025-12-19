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

3) lock の履歴（JSON）が増えすぎたら（任意: 整理）:
```bash
python scripts/agent_org.py locks-prune --older-than-days 30 --dry-run
python scripts/agent_org.py locks-prune --older-than-days 30
```
期限切れ lock を `logs/agent_tasks/coordination/locks/_archive/YYYYMM/` に退避する（active/no-expiry は触らない）。

lock がある範囲は **触らない**。必要なら memo/request で調整する（`ssot/PLAN_AGENT_ORG_COORDINATION.md`）。

### 2.3 共同メモ（単一ファイル / Shared Board）
複数エージェントで「今なにをやっているか / 何が詰まっているか / 申し送り」を1枚に集約したい場合は `board` を使う。
内部は **1ファイル（JSON）** で、更新はファイルロック付きの read-modify-write なので並列でも壊れにくい。

```bash
python scripts/agent_org.py board show
python scripts/agent_org.py board template   # 共通記法（BEP-1）テンプレを表示
python scripts/agent_org.py board set --doing "cleanup: logs整理" --next "ssot更新" --tags cleanup,ssot
```

ファイル実体: `logs/agent_tasks/coordination/board.json`（= `logs_root()/agent_tasks/coordination/board.json`）

#### BEP-1（共通記法ルール）
**目的**: “何が起きた/何が必要/次に何をする” を誰でも即時に判断できるようにする（低知能エージェントでも事故らない）。

- `topic` の先頭に必ず種別を付ける: `[Q]` / `[DECISION]` / `[BLOCKER]` / `[FYI]` / `[REVIEW]` / `[DONE]`
- 必須の情報（note本文に含める）:
  - `scope`: 触った/触る予定のパス（repo-relative）
  - `locks`: lock_id or “(none)”（必要なら「lock作成コマンド」も併記）
  - `now`: いまの状態（何が起きたか）
  - `options`: 選択肢（1,2,3…）
  - `ask`: 何を決めてほしいか / 何をしてほしいか（明示）
  - `commands`: 再現/実行コマンド（plain text）

#### note 投稿の“安全な書き方”（zsh展開事故を防ぐ）
`--message "..."` 直書きだと `` `...` `` や `$(...)` がシェルに食われるので、基本は **heredoc（<<'EOF'）** を使う。

```bash
python scripts/agent_org.py board note --topic "[Q][remotion] render_remotion_batch.py が無い" <<'EOF'
scope:
- scripts/ops/render_remotion_batch.py
locks:
- lock__2025... (or none)
now:
- スクリプトが見当たらず再レンダ開始できない
options:
1) scripts/ops/render_remotion_batch.py を作り直して再開
2) node apps/remotion/scripts/render.js を直接ループで回す
ask:
- どちらで進めるか指示ください
commands:
- node apps/remotion/scripts/render.js ...
EOF
```

#### note の参照（正確に追えるように）
- `python scripts/agent_org.py board show --tail 20` で `note_id` を確認
- `python scripts/agent_org.py board note-show <note_id>` で全文を表示
- `python scripts/agent_org.py board note --reply-to <note_id> ...` で返信（同スレッド）
- `python scripts/agent_org.py board threads` / `python scripts/agent_org.py board thread-show <thread_id|note_id>` でスレッド単位に追える
- `note_id` が `-` の legacy 投稿が混じっていたら: `python scripts/agent_org.py board normalize`（一度だけ実行でOK）

#### 担当（Ownership）の共有（誰がどこを持つか）
- `python scripts/agent_org.py board area-set <AREA> --owner <AGENT> --reviewers <csv>` で担当/レビュー担当を固定
- `python scripts/agent_org.py board areas` で一覧化（「誰が何を担当？」の正本）

#### 推奨タグ（tags）
`refactor,cleanup,ssot,ui,llm,tts,capcut,remotion,video,review,blocking,decision,question,done`

---

## 3. “壊さない”ための不変条件（強制）

### 3.1 SoT（正本）の定義（固定）
- Planning SoT: `workspaces/planning/channels/{CH}.csv`（互換: `progress/channels/{CH}.csv`）
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json` + `content/assembled*.md`（互換: `script_pipeline/data/...`）
- Audio SoT: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav|.srt`（互換: `audio_tts_v2/artifacts/final/...`）
- Video run SoT: `workspaces/video/runs/{run_id}/`（互換: `commentary_02_srt2images_timeline/output/...`）
- Thumbnail SoT: `workspaces/thumbnails/projects.json` と `workspaces/thumbnails/assets/{CH}/{NNN}/`（互換: `thumbnails/*` は symlink）

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

- 台本: `PYTHONPATH=".:packages" python3 -m script_pipeline.cli ...`
- 音声: `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts ...`
- 動画/CapCut: `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run ...`

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

補足:
- `cleanup_*` 系スクリプトは coordination locks を尊重し、lock 下のパスは自動でスキップする（安全優先）。
- 例外的に無視する場合は各スクリプトの `--ignore-locks` を使う（危険。Orchestrator 合意が前提）。

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
