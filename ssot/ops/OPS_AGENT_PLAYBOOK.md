# OPS_AGENT_PLAYBOOK — 低知能エージェントでも迷わない運用ガイド（SSOT）

目的: 7+エージェントが並列で動いても **処理フローを壊さず**、かつ **不要物を増やさず** 作業できる状態を作る。

このドキュメントは「実装方針」ではなく **運用ルール**（やり方の固定）です。

---

## 1. 正本（SSOT）と優先順位

エージェントは必ず以下を正本として扱う。矛盾があれば、**先にSSOTを直してから実装**する。

- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 確定ロジック（最終チェック）: `ssot/reference/【消さないで！人間用】確定ロジック.md`
- 入口索引: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- 処理パターン（CLIレシピ索引）: `ssot/ops/OPS_EXECUTION_PATTERNS.md`（CLI: `./ops patterns list/show`）
- 全体TODO（次に何をやるか）: `ssot/ops/OPS_GLOBAL_TODO.md`
- I/Oスキーマ: `ssot/ops/OPS_IO_SCHEMAS.md`
- ログ配置: `ssot/ops/OPS_LOGGING_MAP.md`
- 生成物の保持/削除: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- 片付けの実行記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`
- 変更履歴: `ssot/history/HISTORY_codex-memory.md`

---

## 2. 作業開始の儀式（必須）

### 2.1 自己識別（必須: agent_org の write系）
- 並列作業では **各Codex/ターミナルごと**に agent name が必須（名前が無いと attribution が壊れて事故る）。
- `LLM_AGENT_NAME` をセットしない場合でも、`scripts/agent_org.py` の write系は **自動で agent name を生成→端末/host_pidごとに記憶**する（以後は自動）。上書きする場合は `LLM_AGENT_NAME` / `--agent-name` を使う。
- 命名規則（固定）: `<owner>-<area>-<nn>`（例: `dd-capcut-01`, `dd-ui-02`, `dd-tts-01`）
```bash
export LLM_AGENT_NAME=dd-ui-01
python scripts/agent_org.py agents start --name "$LLM_AGENT_NAME" --role worker
```
（入口固定: heartbeat + board を同時に更新）:
```bash
python3 scripts/ops/agent_bootstrap.py --name "$LLM_AGENT_NAME" --role worker --doing "ui: ..." --next "..." --tags ui
```

### 2.2 Orchestrator/lock を確認（強制）
1) 触るファイル/ディレクトリに lock がないか確認:
```bash
python scripts/agent_org.py locks --path ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md
python scripts/agent_org.py locks --path packages/video_pipeline/tools/auto_capcut_run.py
```

2) 触る範囲に lock を置く（zshのglob展開事故防止のため **必ずクォート**）:
```bash
python scripts/agent_org.py lock 'packages/video_pipeline/tools/**' --mode no_touch --ttl-min 60 --note 'working'
```
※ `lock` は既存の active lock とスコープが交差する場合、作成を拒否する（衝突を作らないため）。  
  どうしても必要な場合のみ `--force` で上書きし、必ず board/memo で合意を残す。
  lock は既定で board note を自動投稿する（不要なら `--no-announce`）。
  lock の作成/解除は `locks/lease.lock`（flock）で直列化され、レースで二重取得しにくい（UI/API/Orchestrator/CLI 共通）。

3) lock の履歴（JSON）が増えすぎたら（オプション: 整理）:
```bash
python scripts/agent_org.py locks-prune --older-than-days 30 --dry-run
python scripts/agent_org.py locks-prune --older-than-days 30
```
期限切れ lock を `workspaces/logs/agent_tasks/coordination/locks/_archive/YYYYMM/` に退避する（active/no-expiry は触らない）。

4) “解除し忘れ” がないか点検（入口固定）:
```bash
# expires_at が無い（= auto-expire しない）lock を一覧
python scripts/agent_org.py locks-audit

# 古い no-expiry lock だけ見たい場合（例: 6時間以上）
python scripts/agent_org.py locks-audit --older-than-hours 6
```

lock がある範囲は **触らない**。調整は memo/request で行う（`ssot/plans/PLAN_AGENT_ORG_COORDINATION.md`）。

### 2.2.5 Gitロールバック遮断（強制）
並列運用では、Codex からの `git restore/checkout/reset` 等で作業ツリーが巻き戻る事故を **仕組みで封じる**。

仕組み（優先順）:
- Codex Git Guard: Codex shell では `git restore/checkout/reset/clean/revert/switch/stash` を常に BLOCK（`~/.codex/bin/git`）
- Codex execpolicy: 上記コマンドを `forbidden`（バイパス防止）
- `.git` write-lock（オプション）: `.git/` を lock して metadata 書き換え系を失敗させる  
  ※ `git restore` のように worktree だけ触るコマンドは、これ **単体では止まらない**

コマンド（`.git` write-lock）:
```bash
python3 scripts/ops/git_write_lock.py status
python3 scripts/ops/git_write_lock.py lock
python3 scripts/ops/git_write_lock.py unlock
```

push直前の一時解除（Orchestrator向け・安全）:
```bash
python3 scripts/ops/git_write_lock.py unlock-for-push
```

通常は unlocked のまま運用し、事故が多い期間だけ lock を使う（詳細: `ssot/ops/OPS_GIT_SAFETY.md`）。

補足（視界制限 / 事故防止）:
- Codex 環境では `git status`/`git diff`（パス指定なし）は、**自分の lock scopes に自動スコープ**される（他人の差分を見て誤って消す事故を減らす）
- ロックが無い場合は失敗するので、先に lock を取るか、明示スコープで実行する: `git status -- <PATH...>`

### 2.2.5.1 Dirty（未コミット）運用（強制）
目的: `git status` に大量の差分が出る状態でも、別エージェントの実装を「何これ？」で消さない。

- worker が作る未コミット差分は **自分の lock scopes 内だけ**許可（同一ツリーでも “担当外” を汚さない）
- lock 外に差分が見えても **整合化/cleanup をしない**（board/memo で担当へ連絡）
- 切り替え/引き継ぎ前に `save_patch.sh` で差分をパッチ化する（スコープ限定）
  - Codex 環境では `--path` 未指定時、**自分の active lock scopes に自動スコープ**される
  - 全体パッチは `--all` 明示時のみ（Orchestrator/人間の判断）

### 2.2.6 kill/pkill/killall 遮断（強制）
並列運用では、Codex からの `kill/pkill/killall` によるプロセス破壊（UI/バッチ/生成の途中停止）事故が起きやすい。  
したがって Codex 環境では **kill系を仕組みで封じる**（人間が明示OKした時だけ例外解除）。

仕組み:
- `~/.codex/bin/kill`（ターミナルに大きな警告を出して `exit 42` で停止）
  - `~/.codex/bin/pkill` / `~/.codex/bin/killall` は symlink で同じガードに集約
- zsh: Codex セッションでは `kill` builtin を `disable` して PATH 側のガードを必ず通す（`.zshenv`）
- bash: Codex から起動した非対話 bash でも `kill` builtin を無効化する（`BASH_ENV=~/.codex/bash_env_guard.sh`）

例外（Break-glass。人間がOKした時だけ）:
- `CODEX_KILL_BREAKGLASS=1 kill ...`（または `pkill/killall`）
- **非対話実行（TTYなし）では例外解除できない**（=暴走/バッチから守る）
- 実行直前に表示されるワンタイム文字列 `ALLOW <cmd> <CODE>` を完全一致入力できた場合のみ `/bin/kill` 等へ通す

注意:
- エージェント（Codex）は kill を使わない。手順・原因を提示して **人間判断**へエスカレーションする。

### 2.3 共同メモ（単一ファイル / Shared Board）
複数エージェントで「今なにをやっているか / 何が詰まっているか / 申し送り」を1枚に集約したい場合は `board` を使う。
内部は **1ファイル（JSON）** で、更新はファイルロック付きの read-modify-write なので並列でも壊れにくい。

```bash
python scripts/agent_org.py board show
python scripts/agent_org.py board template   # 共通記法（BEP-1）テンプレを表示
python scripts/agent_org.py board set --doing "cleanup: logs整理" --next "ssot更新" --tags cleanup,ssot
```

ファイル実体: `workspaces/logs/agent_tasks/coordination/board.json`（= `logs_root()/agent_tasks/coordination/board.json`）

#### BEP-1（共通記法ルール）
**目的**: “何が起きた/何が必要/次に何をする” を誰でも即時に判断できるようにする（低知能エージェントでも事故らない）。

- `topic` の先頭に必ず種別を付ける: `[Q]` / `[DECISION]` / `[BLOCKER]` / `[FYI]` / `[REVIEW]` / `[DONE]`
- 必須の情報（note本文に含める）:
  - `scope`: 触った/触る予定のパス（repo-relative）
  - `locks`: lock_id or “(none)”（該当する場合は「lock作成コマンド」も併記）
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

#### 標準タグ（tags）
`refactor,cleanup,ssot,ui,llm,tts,capcut,remotion,video,review,blocking,decision,question,done`

---

## 3. “壊さない”ための不変条件（強制）

### 3.1 SoT（正本）の定義（固定）
- Planning SoT: `workspaces/planning/channels/{CH}.csv`
- Script SoT: `workspaces/scripts/{CH}/{NNN}/status.json` + `content/assembled*.md`
- Audio SoT: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav|.srt`
- Video run SoT: `workspaces/video/runs/{run_id}/`
- Thumbnail SoT: `workspaces/thumbnails/projects.json` と `workspaces/thumbnails/assets/{CH}/{NNN}/`

### 3.2 パス直書き禁止（移設耐性）
- `Path(__file__).resolve().parents[...]` を新規に増やさない。
- `factory_common.paths` の `repo_root()/logs_root()/video_pkg_root()/...` を使う。

### 3.3 機械分割禁止（契約/品質）
- cues/セクション分割は等間隔にしない（文脈ベースで切る）。
- THINK/AGENT時は `visual_image_cues_plan` を優先し、stop/resume ループを減らす。

### 3.4 SSOT変更時のUI反映（強制）
SSOT は **UI（read-only表示）** と一体です。SSOTだけ更新してUI側のSSOT表示（System Map/Catalog）が古いままになると、全員が迷って事故る。

- `ssot/**` を更新したら、**必ず** UI側SSOT（`/ssot/map` / Docs Browser）の整合も確認し、差分があれば修正する
- 典型パターン:
  - 新しい運用レバー/設定キー/ロジックを追加した → `packages/factory_common/ssot_catalog.py`（System Map）に追記
  - catalogの返却JSONに新しいフィールドを足した → `apps/ui-frontend/src/api/types.ts` と表示コンポーネントを更新
- 最低限の確認（必須）:
  - `python3 scripts/ops/build_ssot_catalog.py --check`
  - `python3 scripts/ops/pre_push_final_check.py`（UIのSSOTチェックも含む）

---

## 4. よくある作業（迷わない手順）

### 4.1 台本→音声→動画（主線）
入口は `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` を正とする。

- 台本（入口固定）: `./ops api script <MODE> -- --channel CHxx --video NNN`（台本はAPI固定）
- 音声: `./ops audio --llm think -- --channel CHxx --video NNN`
- 動画/CapCut: `./ops video auto-capcut -- --channel CHxx --video NNN`
- 迷ったら:
  - `./ops patterns list`（パターン索引）
  - `./ops reconcile --channel CHxx --video NNN`（issue→固定復帰の配線; dry-run）

### 4.2 THINK MODE（APIなしで止めて続行）
```bash
./ops think audio -- --channel CH06 --video 033
./ops agent list
./ops agent prompt <TASK_ID>
# 生成 → ./ops agent complete <TASK_ID> --content-file /path/to/content.txt
# pendingが消えたら、同じ ./ops think ... を再実行して続行
```
注:
- 台本（`script_*`）は LLM API（Fireworks）固定のため `./ops think script ...` は禁止（policyで停止する）。
- `script_*` は **API失敗時に自動でTHINKへ落ちない**（即停止・記録が正）。

### 4.3 “確実ゴミ”の削除（強制手順）
1) `rg` で参照ゼロ確認（Docs言及は除外してよい）  
2) tracked の場合は `backups/graveyard/` にアーカイブしてから削除  
3) `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に記録  

untracked キャッシュはいつでも削除してよい:
```bash
bash scripts/ops/cleanup_caches.sh
```

補足:
- `cleanup_*` 系スクリプトは coordination locks を尊重し、lock 下のパスは自動でスキップする（安全優先）。
- 例外的に無視する場合は各スクリプトの `--ignore-locks` を使う（危険。Orchestrator 合意が前提）。

### 4.4 台本カオス（複数エージェント競合）の止血・復帰（強制）
前提:
- codex / windsurf / antigravity 等、**どのエージェントでも同じルール**で動く（UI差分は関係ない）。
- 1エピソードを複数エージェントが同時に触ると、正本が揺れてコストが爆増する。

正本（この手順だけ見れば復旧できる）:
- `ssot/ops/OPS_SCRIPT_INCIDENT_RUNBOOK.md`

要点（暗記用）:
- まず lock（止血）→ 正本宣言 → 候補隔離 → 採用→`script_validation` → lock解除
- 候補エージェントは SoT を直接書き換えない（候補ファイルを出すだけ）

---

## 5. 変更の残し方（小さく刻む）

### 5.1 固定ルール: 小コミット
- 1コミット = 1目的（1つの不具合/1つの移行/1つのcleanup）

### 5.2 git が使えない場合（パッチ運用）
環境によって `.git` が書けず `git add/commit` が失敗することがある。  
その場合はパッチを保存し、Orchestrator/人間が apply→commit する。

```bash
# 標準: スコープを明示してパッチを切る（事故率が低い）
bash scripts/ops/save_patch.sh --label stage2_paths --path 'packages/<area>/**'

# Codex 並列運用: lock を取っているなら、未指定でも自分の lock scopes に自動スコープされる
bash scripts/ops/save_patch.sh --label stage2_paths

# 全体スナップショット（明示）: Orchestrator/人間のみ
bash scripts/ops/save_patch.sh --label snapshot --all
```

出力: `backups/patches/YYYYMMDD_HHMMSS_<label>.patch`

---

## 6. 完了条件（DoD）
- 触った範囲の lock を解除（または TTL を短くして終了）。
- 必要なSSOT（フロー/ロジック/索引/履歴/cleanupログ）を更新。
- テスト/ビルド/スモーク（該当範囲のみ）を実行し、結果を `ssot/history/HISTORY_codex-memory.md` に残す。
