# PLAN_CAPCUT_HOT_VAULT_ROLLOUT — CapCut運用（Hot=Mac / Vault=Lenovo外付け / UI= Acer常駐）移行手順

目的:
- **CapCut編集体験を落とさない**（編集/生成は Mac ローカル = Hot）
- しかし Lenovo 作業時の「素材がMacにしか無い」を無くす（= **素材/確定データの正本をVaultへ**）
- 進捗（planning）は **共有SSOT 1本（mainブランチ）**
- だれが見ても迷わないように、**保存先/配線/削除ポリシー/入口コマンド**を固定し、記録を残す

SSOT（正本）:
- 保存先/配線/決裁（image-dddの鏡）: `ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md`
- CapCut編集フロー（Workset/Exports/退避）: `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- CapCutストレージ戦略（Hot/Warm/Cold）: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`
- Hot（未投稿）/Freeze（未投稿だが当面触らない）の確定ルール: `ssot/ops/OPS_HOTSET_POLICY.md`
- 投稿済み成果物の整理（archive/delete）: `ssot/ops/OPS_ARCHIVE_PUBLISHED_EPISODES.md`
- UI Hub常駐化（Acer）: `ssot/agent_runbooks/RUNBOOK_UI_HUB_DAEMON.md`
- 実行履歴: `ssot/history/HISTORY_20260124_capcut_vault_mirror.md`

Notion（同一情報の入口）:
- `image-ddd`（保存先迷子になったらここ）

---

## 0) 用語（このPlan内）

- Hot: 編集/生成の作業領域（**Macローカル**）
- Vault: 保管庫（**Lenovo外付け共有 = 容量の正本**）
- SoT（正本）: UI/APIが参照する「真の状態」
- planning: 進捗SSOT（mainブランチ）。UIの「投稿済み」チェックがここを書き換える

## 0.1) 絶対ルール（Mac Hot を失わない）

- メインは **Mac**。Hot（未投稿のCapCutドラフト/Workset/編集中素材）は **必ず Mac ローカルに実体がある**こと。
- Vault/共有は容量/閲覧/UI/ミラー/保管用途。**落ちていても Mac 側の編集/生成を止めない**（復旧後に後追い同期でよい）。
- 参照パス（manifest/log/json）は **実体へ**。共有マウントの絶対パスを `resolve()` して埋め込まない（`workspaces/**` ベース or run_dir 内のローカルコピー）。

## 0.2) 緊急時（Vault不安定/未マウント）: オフライン継続

判定（簡易）:
- `YTM_SHARED_STORAGE_ROOT/README_MOUNTPOINT.txt` が存在する場合は「共有ダウン」扱い（mountpoint stub）。
- `./ops storage doctor` の warnings に `OFFLINE/STUB` が出る（共有依存で遅い/壊れる前に気づく）。

対応:
- 実行は `./scripts/with_ytm_env.sh ...` を使う（共有ダウン時はオフライン補助が走り、`workspaces/video/input` の壊れた symlink をローカル実体化していく）。
- 手動で補助だけ実行したい場合:
  - dry-run: `./scripts/with_ytm_env.sh python3 scripts/ops/offline_shared_fallback.py`
  - 本番: `./scripts/with_ytm_env.sh python3 scripts/ops/offline_shared_fallback.py --run`
  - レポート: `--json`

## 0.2.1) 検証ログ（2026-01-26 / 共有ダウン=stub）

- `shared_storage_sync.py`:
  - offline中に `--symlink-back/--move` が **拒否**されることを確認（ローカルSoTを共有依存にしない）
  - offline中に `--run` が **ローカルoutboxへ保存**できることを確認（manifest作成までOK）
- `shared_storage_offload_episode.py`:
  - offline中に `--symlink-back` が **拒否**されることを確認
  - `--skip-audio`（小さいscriptのみ）で outbox へ保存できることを確認（manifest作成までOK）
- `script_pipeline/runner.py`:
  - `sources.yaml` の `workspaces/planning/**` が `planning_root()`（`YTM_PLANNING_ROOT`）基準で解決されることを確認（planning/persona SoTの分岐を防止）
- `offline_shared_fallback.py`（dry-run）:
  - `workspaces/video/input` の壊れsymlinkについて `video_input_broken_no_archive` が残る（= **Hot対象は別途“実体化”が必要**）
  - `.symlink_shared_backup_*` がスキャンに混ざってノイズになりやすい（運用/実装での除外を検討）
  - `vault_workspaces_root()/video/_archive` に `video_input` の復元ソースがあるケースがある（例: CH02）
- `offline_shared_fallback.py`（改修後 / 自動run）:
  - archive が無い壊れsymlinkでも **空ディレクトリで実体化**し、`README_OFFLINE_FALLBACK.txt` を書く（Hot作業が止まらない）
  - 適用後は `workspaces/video/input` に **非backup symlink が残らない**（= 共有依存が消える）。元symlinkは `*.symlink_shared_backup_*` へ退避される

---

## 0.2.2) Hot資産doctor 実行ログ（2026-01-26）

- 入口（read-only）:
  - `python3 scripts/ops/hot_assets_doctor.py --all-channels --json`
- レポート:
  - `workspaces/logs/ops/hot_assets_doctor/report__20260126T065739Z.json`
  - `workspaces/logs/ops/hot_assets_doctor/candidates__20260126T070401Z.json`
- 結果（P0）:
  - violations_total=82（episodes=41）: `run_dir_symlink_broken` + `draft_missing_locally`
  - 主なch: CH13(29ep), CH22(4ep), CH28(4ep), CH09(3ep), CH26(1ep)
  - candidates は全件あり（zero_candidates=0）→ **実体はあるが参照が古い/別名** の可能性が高い
- 次アクション（破壊禁止）:
  - episode毎に candidate を人間が確定し、`run_dir/capcut_draft` と `capcut_draft_info.json` の参照を整合させる
  - 入口（safe; dry-run既定 / 明示`--run`のみ適用）:
    - 候補表示: `python3 scripts/ops/relink_capcut_draft.py --episode CHxx-NNN`
    - 適用（候補を明示して実行）: `python3 scripts/ops/relink_capcut_draft.py --episode CHxx-NNN --draft-dir \"/path/to/draft\" --run`

---

## 0.2.3) relink 適用ログ（2026-01-26）

- 入口（safe; dry-run既定 / 明示`--run`のみ適用）:
  - `python3 scripts/ops/relink_capcut_draft.py --episode CHxx-NNN`
- 適用ログ:
  - relink計画（single-candidate）: `workspaces/logs/ops/hot_assets_doctor/relink_plan__20260126T071200Z.json`
  - relink計画（残り; ヒューリスティック）: `workspaces/logs/ops/hot_assets_doctor/relink_plan_remaining__20260126T072100Z.json`
  - 実行ログ（全件追記）: `workspaces/logs/ops/hot_assets_doctor/relink_applied__20260126.jsonl`
- 再スキャン:
  - `workspaces/logs/ops/hot_assets_doctor/report__20260126T072611Z__post_relink.json`
- 結果（P0）:
  - violations_total=0（Hotが共有/外部only・壊れsymlink・ローカル欠損を解消）
  - warnings_total=647 は `video_run_missing`（未着手/未生成の回）で、P0違反ではない

---

## 0.3) 2026-01-26: 現状（Lenovo外付け復旧中）と、このフェーズのゴール

このフェーズは **「MacのHot運用を止めない」** の止血フェーズ。
外部（Lenovo共有）が落ちていても、参照が外部に引っ張られて **作業が止まる/HotがMacに無い** 状態を作らない。

### 現状スナップショット（Mac / 2026-01-26）
- 共有（マウント）: `YTM_SHARED_STORAGE_ROOT=/Users/dd/mounts/lenovo_share_real`（`README_MOUNTPOINT.txt` があり stub = 共有ダウン）
  - `ytm_workspaces -> /Users/dd/mounts/lenovo_share_real__LOCAL_BACKUP__.../outbox/ytm_share/ytm_workspaces`（ローカル退避へのsymlink）
- ディスク空き（重要）: `/System/Volumes/Data` 空き約 `48Gi`（使用 `89%`）
- 壊れsymlink（参考・2026-01-26時点の観測）:
  - `workspaces/video/input`: 79（`.symlink_shared_backup_*` など「退避symlink」も含む）
  - `workspaces/video/runs`: 116（多くが共有アーカイブ向け）
  - `workspaces/episodes`: 372（共有側への退避リンクが多数）

### ゴール（このフェーズで“ブレない”ための確定）
- **G1: Hot資産の実体は必ずMac**（未投稿のCapCutドラフト/作業中素材/次に触る入力は「Macに無い」は禁止）
- **G2: 外部（共有/Vault）は“加点要素”**（落ちていてもMac側の処理/編集/生成を止めない）
- **G3: 参照パスは常に“実体”へ**（フォールバックでもOKだが「存在しないパス」を参照させない。stubマウントの絶対パスを `resolve()` して埋め込まない）
- **G4: planning/persona参照を1本化**（script/video/UI で SoT がズレない）

### 課題（徹底洗い出し / 優先度つき）

P0（止血・最優先）
- **P0-1: planning/persona の参照SoTが分岐**（対策済み: `planning_root()` に統一）
  - `configs/sources.yaml` は `workspaces/planning/**` をパス契約として保持（実パスはホスト依存でよい）
  - `packages/script_pipeline/runner.py` は `workspaces/planning/**` を検知すると `factory_common.paths.planning_root()` 基準で解決する（`YTM_PLANNING_ROOT` を尊重）
  - video 側も `factory_common.paths.planning_root()` を使うため、planning/persona の参照が1本化される（ドリフト抑止）
- **P0-2: Hotに必要な `video/input` が共有symlink前提になり得る**
  - 共有ダウン時は `offline_shared_fallback.py` が `workspaces/video/input` をローカル実体化するが、
    ローカルアーカイブ未整備のチャンネルは復旧できず “Macに実体が無い” が起き得る
- **P0-3: ローカル空き容量が薄い**（生成/CapCut/キャッシュで一気に破綻する）
- **P0-4: “共有へ書き込む系”ツールが stub を未検知のまま走り得る**（対策済み）
  - `shared_storage_sync.py` / `shared_storage_offload_episode.py` は mountpoint stub（`README_MOUNTPOINT.txt`）や SMB未マウントを検知し、ローカルoutboxへフォールバックする
  - 共有ダウン中の `--symlink-back/--move` は拒否し、Mac側SoTが共有依存になる事故を防ぐ
  - 未投稿（Hot/Freeze）の `--symlink-back/--move` も既定で拒否する（Macローカル実体を保持。必要なら break-glass `--allow-unposted` / `YTM_EMERGENCY_OVERRIDE=1`）

P1（次点・事故の再発防止）
- **P1-1: 壊れsymlink/退避symlinkの残骸が増えやすい**（人間が「どれが正？」で迷子になりやすい）
- **P1-2: `workspaces/video/runs` の共有退避が多い**（必要時に “外部だけにある” が起きる可能性）
- **P1-3: `asset_vault` が未整備/未マウントだと入口が曖昧**（素材追加の運用がブレる）
- **P1-4: LaunchAgent / 外部スクリプト（repo外）でCapCut資産が動く可能性**（purge/auto export 等の干渉リスク）

P2（観測・運用の強化）
- **P2-1: “共有ダウン/容量逼迫/ドリフト”の通知が弱い**（気づいた時には詰んでる）
- **P2-2: 複数端末/複数エージェント並列での「どこが正本？」が揺れやすい**（SSOT/環境変数/入口コマンドの統一が必要）

### 次アクション（このPlanで進捗管理する）
- [x] P0-1: planning/persona の参照を 1本化（`sources.yaml` と実装の責務分離を確定）
- [ ] P0-2: “今触るチャンネル/エピソード”のHot定義を決め、`video/input` のローカル実体を保証（不足時の取得/退避手順を固定）
- [ ] P0-3: 容量の安全域（例: 空き < 150Gi でWARN、< 80Gi でSTOP）を決め、回収手順を固定（削除は dry-run → 承認 → run）
- [ ] P1-4: LaunchAgent（CapCut purge/auto export）を棚卸しし、Hot資産が消える経路が無いことを確認

Slack通知:
- `scripts/ops/slack_notify.py` はこの環境で `slack.com` のDNS解決に失敗し送信できなかった（復旧後にリトライ）。

## 1) まず固定する（翻訳表）

だれが見ても迷わないために、**役割と入口だけ先に固定**する（実パスはホスト毎に違ってよい）。

### 1.1 役割（固定）
- Mac: 編集/生成（Hot）
- Lenovo（Windows）: 外付けが刺さっている = Vault（共有の実体）
- Acer（Ubuntu）: ゲートウェイ（常駐URL）。ユーザーは触らない前提（Macからコードで設定する）

### 1.2 URL（固定）
- UI: `https://acer-dai.tail8c523e.ts.net/ui/`
- API: `https://acer-dai.tail8c523e.ts.net/api/healthz`
- Files: `https://acer-dai.tail8c523e.ts.net/files/`

### 1.3 共有ストレージ（マウント先: 現在値）
- Mac: `YTM_SHARED_STORAGE_ROOT=/Users/dd/mounts/lenovo_share_real`
- Acer: `YTM_SHARED_STORAGE_ROOT=/srv/workspace/doraemon/workspace/lenovo_share`

---

## 2) 受け入れ基準（この状態になったら「運用できる」）

- どの端末でも URL で同じ状態が見える:
  - `.../ui/` で planning/台本/サムネが見える
  - `.../files/ytm_workspaces/planning/` が見える
- Lenovo作業時に「SRT/WAV/画像がMacにしか無い」が発生しない（= SoTがVault）
- Macは Hot（ローカル）で編集でき、体感が落ちない（共有直編集しない）
- 投稿後の容量回収が「進捗=投稿済み」を根拠に再現できる（dry-run→runの手順が明確）

---

## 3) ロールアウト手順（記録を残しながら進める）

> 以降の各ステップは「実行ログ + SSOT追記 + Notion追記」をセットで行う。

### Step A: SSOT/Notionの入口を固定（迷子停止）
1. `OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL` に「役割/URL/マウント」を確定記載（実施済み）
2. Notion `image-ddd` に同内容を追記（実施済み）
3. 変更が出たら必ず `ssot/history/HISTORY_20260124_capcut_vault_mirror.md` に追記

### Step B: planning（進捗）を共有SSOT 1本化（mainブランチ化）
1. 共有側 `ytm_workspaces/planning/` を正本にする
2. 既に存在するローカルplanningは **seed**（上書き事故を避けつつ投入）
3. 以後、planningは「共有が正」。ローカルはcache扱いに寄せる

### Step C: Mac Hot → Vault への 1:1 ミラー常駐
1. Vault に sentinel を作成（delete-sync事故防止）
2. Vault(共有)内の symlink を **portable化**（Acerでも壊れない）
   - `python3 scripts/ops/vault_workspaces_doctor.py --run`
3. `workspaces/**` を Vault `ytm_workspaces/**` に 1:1 ミラー（作成/更新 + 削除同期）
4. 例外（強制）: Vault側で削除しない
   - `scripts/`（台本）
   - `thumbnails/assets/`（サムネ）
   - `video/runs/`（生成画像/中間。保管庫に残す）

### Step D: Acer UI Hub を Vault 参照で常駐（ユーザーは触らない）
1. `bootstrap_remote_ui_hub.py` を Macから実行して systemd + tailscale serve を整える
2. UI/API/Files の疎通確認

### Step E: CapCut編集（Mac/Lenovo）で迷子が起きない運用にする
1. 追加素材は Asset Vault（共有）へ入れる（Macローカル“だけ”に置かない）
2. 編集は Workset（Hot）を作ってそこから取り込む（参照切れを減らす）
3. SRT/WAV は `workspaces/audio/final/**` を正本に置く（Lenovoで挿入できる状態を作る）
4. 生成画像は `workspaces/video/runs/**` を正本に置き、必要分だけ Workset へコピー

### Step F: 投稿済み（進捗=投稿済み）をトリガーに容量回収する
1. UIで「投稿済み」を付ける → planning更新（main）
2. `./ops archive published` を実行（まず dry-run）
3. ポリシー:
   - delete可: `audio/final`, `capcut-drafts`
   - delete禁止: `thumbnails/assets`, `video/runs`（archiveのみ）
4. Exports（mp4）は「受け渡し領域」なので、**保持期間（例: 24h）** を別途決裁し、コード化する

---

## 4) 未決裁（ここを確定するとブレなくなる）

- audio/final（WAV/SRT）を投稿後に削除するか（delete/keep/retain N days）
- capcut_exports（mp4）を「何日で削除」するか（安全策: archive→猶予→delete）

---

## 5) チェックリスト（進捗）

- [x] Step A: SSOT/Notion入口固定
- [x] Step B: planning 共有SSOT seed
- [x] Step C: ミラー常駐導入（初回同期は時間がかかる。現在のinterval: 600sec）
- [x] Step D: Acer UI Hub 常駐（URL疎通 / Vault SoT参照に切替済み）
- [ ] Step E: Workset運用を現場に浸透（素材追加の入口=asset_vault）
- [ ] Step F: 投稿済みトリガーの容量回収（Exports保持期間の決裁→自動化）
