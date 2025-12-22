# OPS_GLOBAL_TODO — 全体TODO（SSOT）

目的:
- 「次に何をやるか」「誰が何を担当しているか」「どこを触るか」を **1枚で迷わない** 状態にする。
- 日々のやり取りは board の thread に寄せ、SSOTは **タスクの正本**として固定する（ログで散らからない）。

関連:
- 協調運用: `ssot/OPS_AGENT_PLAYBOOK.md`, `ssot/PLAN_AGENT_ORG_COORDINATION.md`
- リポジトリ再編: `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/OPS_REPO_DIRECTORY_SSOT.md`
- 入口: `ssot/OPS_ENTRYPOINTS_INDEX.md`
- ログ: `ssot/OPS_LOGGING_MAP.md`

---

## 1) 運用ルール（強制）

### 1.1 SSOT vs Notes（分離）
- **SSOT（このファイル）**: タスクID / 優先度 / 期限 / DoD / スコープ（触る範囲）を固定する。
- **Notes（board thread）**: 進捗・相談・レビュー・意思決定ログを残す（SSOTに長文を置かない）。

### 1.2 進め方（低知能エージェントでも事故らない）
1. `ssot/OPS_GLOBAL_TODO.md` からタスクIDを選ぶ
2. 触る範囲に lock を置く（`python3 scripts/agent_org.py lock 'path/**' --ttl-min 60 --note 'TODO-...'`）
3. 作業を開始したら thread に `[UPDATE]` を投稿（再現コマンド/成果物/次アクションまで書く）
4. SSOTのステータスを更新（原則: Orchestratorが更新）

### 1.3 タスク表記（固定）
- 優先度: `P0` (停止/事故) / `P1` (主線) / `P2` (改善) / `P3` (後回し)
- 状態: `todo` / `doing` / `blocked` / `done`
- タスクID: `TODO-<AREA>-###`（例: `TODO-SSOT-001`）

---

## 2) Thread アンカー（board note へのリンク）

※ `thread-show` は note_id を渡しても追えます。

- coordination: `note__20251222T145419Z__c13676e2`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145419Z__c13676e2`
- ssot: `note__20251222T145431Z__c67da380`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145431Z__c67da380`
- repo: `note__20251222T145443Z__3cbe0e2a`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145443Z__3cbe0e2a`
- logging: `note__20251222T145500Z__a391c617`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145500Z__a391c617`
- script: `note__20251222T145515Z__663aa912`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145515Z__663aa912`
- audio: `note__20251222T145528Z__dda0643b`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145528Z__dda0643b`
- video: `note__20251222T145542Z__e1c3ca6c`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145542Z__e1c3ca6c`
- ui: `note__20251222T145553Z__1a607eb2`
  - `python3 scripts/agent_org.py board thread-show note__20251222T145553Z__1a607eb2`

---

## 3) P0（停止/事故）— まず潰す

- [ ] `TODO-COORD-001` stale/no-expiry lock の点検を定期化（P0, todo）
  - scope: `workspaces/logs/agent_tasks/coordination/locks/**`
  - thread: `note__20251222T145419Z__c13676e2`
  - DoD: `python3 scripts/agent_org.py locks-audit --older-than-hours 6` が常に 0 件

- [ ] `TODO-LOG-001` ログ爆増ポイントの“入口”を特定してSSOTに固定（P0, todo）
  - scope: `ssot/OPS_LOGGING_MAP.md`, `workspaces/logs/**`, `scripts/**`
  - thread: `note__20251222T145500Z__a391c617`
  - DoD: 「増える場所/生成者/ローテ対象/保持期間」が `OPS_LOGGING_MAP` で追える

- [ ] `TODO-UI-001` UIの致命的エラー（操作不能）を最優先で潰す（P0, todo）
  - scope: `apps/ui-frontend/**`, `apps/ui-backend/**`
  - thread: `note__20251222T145553Z__1a607eb2`
  - DoD: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards` が green

---

## 4) P1（主線）— 収益ラインを“迷わず回せる”状態へ

### 4.1 Script（企画→台本）
- [ ] `TODO-SCRIPT-001` 台本量産の“確定ロジック”を運用で固定（P1, doing）
  - scope: `packages/script_pipeline/**`, `scripts/ops/a_text_*.py`, `ssot/OPS_*SCRIPT*.md`
  - thread: `note__20251222T145515Z__663aa912`
  - DoD: 8000字級でも「反復/水増し/逸脱」が `script_validation` で止まり、最小修正で収束する

- [ ] `TODO-SCRIPT-002` 2〜3時間級（Marathon）の作業分割・収束条件を明文化（P1, todo）
  - scope: `scripts/ops/a_text_marathon_compose.py`, `ssot/OPS_LONGFORM_SCRIPT_SCALING.md`
  - thread: `note__20251222T145515Z__663aa912`
  - DoD: “全文LLM”を禁止したまま、破綻なく `assembled_human.md` が組み上がる

### 4.2 Audio（台本→音声/SRT）
- [ ] `TODO-AUDIO-001` 生成後の残骸削除（audio_prep/chunks/重複）を安全に自動化（P1, todo）
  - scope: `workspaces/scripts/**/audio_prep/**`, `scripts/cleanup_audio_prep.py`
  - thread: `note__20251222T145528Z__dda0643b`
  - DoD: `workspaces/audio/final/**` だけ見れば下流が動き、prepは規約に沿って減る

### 4.3 Video（SRT→画像→CapCut）
- [ ] `TODO-VIDEO-001` run_dir 正本の I/O 契約を固定し、古いrunをarchive-firstで整理（P1, todo）
  - scope: `workspaces/video/runs/**`, `packages/commentary_02_srt2images_timeline/**`
  - thread: `note__20251222T145542Z__e1c3ca6c`
  - DoD: “どのrunが正本か”が迷わず追え、cleanupしても参照切れしない

---

## 5) P2（改善）— 散らかりを増やさない

### 5.1 SSOT/Docs
- [ ] `TODO-SSOT-001` SSOT索引を常に最新化（P2, todo）
  - scope: `ssot/DOCS_INDEX.md`, `ssot/PLAN_STATUS.md`
  - thread: `note__20251222T145431Z__c67da380`
  - DoD: `python3 scripts/ops/ssot_audit.py --write` が常にOK

### 5.2 Repo構造
- [ ] `TODO-REPO-001` Stage6（互換symlink縮退 + cleanup自動化 + pyproject整備）を完了（P2, todo）
  - scope: `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`, `pyproject.toml`, `scripts/ops/**`
  - thread: `note__20251222T145443Z__3cbe0e2a`
  - DoD: `pip install -e .` が通り、symlink無しでもimportが崩れない（移行期間後）

### 5.3 UI/UX
- [ ] `TODO-UI-002` Episode Studio（統合導線）へ段階統合（P2, todo）
  - scope: `ssot/PLAN_UI_EPISODE_STUDIO.md`, `apps/ui-*`
  - thread: `note__20251222T145553Z__1a607eb2`
  - DoD: UIだけで「企画→台本→音声→動画(run)→確認」まで完走できる

---

## 6) Done（最近）

- [x] `TODO-SCRIPT-DONE-001` “タイトル語句が本文に無い”誤判定（token overlap）を撤廃（done）
  - ref: commit `01797805` / `scripts/audit_alignment_semantic.py`

- [x] `TODO-SCRIPT-DONE-002` 超長尺の全文LLMゲートを自動スキップ（done）
  - ref: commit `d689b55d` / `packages/script_pipeline/runner.py`

- [x] `TODO-REPO-DONE-001` CH17–CH21 のスキャフォールド追加（done）
  - ref: commit `0c2f0fa2` / `configs/sources.yaml` commit `e1cda6a0`
