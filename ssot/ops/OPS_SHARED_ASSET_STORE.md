# OPS_SHARED_ASSET_STORE — 共有ストレージ（Tailscale常駐）へ生成資産を置く（保存/退避）

目的:
- この repo で生成した最終成果物（L1）を **共有ストレージへ保存**し、複数マシン/複数人で再利用できる状態を作る。
- Mac の空き容量を保つため、重いL1は **共有側へ保存**し、ローカルは明示操作で軽量化する（例: `--symlink-back`）。

非目的:
- 中間生成物（L2/L3）まで無差別に保存すること。
- “共有⇄ローカルの双方向同期” をすること（本書は **保存/退避** の契約）。
- 共有が未マウントでも “サイレントに” ローカルへフォールバックして続行すること（フォールバックするなら **明示/記録** する）。

重要（混同防止）:
- 本書は **「共有へ保存/退避する」工程の契約**。制作（生成/編集=Hot）は Mac ローカルで完結させ、共有は optional（落ちたら保存工程は止めて報告し、復旧後に後追いする）。
- 共有が不安定な期間は、参照パス（manifest/log/json）は `workspaces/**` 等の安定パス（または run_dir 内のローカルコピー）を指し、共有マウントの絶対パスを埋め込まない。
- ハイブリッド運用（推奨）: 共有が落ちていても **Macの生成/編集は止めない**。L1 bytes store はローカルoutboxへ退避し、共有復旧後に後追い同期する。

---

## 0) 正本（SoT）

- **パス契約（正本）**: `workspaces/**`（パイプラインが参照する固定パス）
- **L1の実体（bytes）**: 共有ストレージへ保存し、ローカルは明示操作で symlink に置換して容量を空ける（= “同期” ではなく “保存/退避”）。

---

## 0.1) 迷子防止: “SoT” と “L1 bytes store” は別物

混乱の主因は「最終資産」という言葉が **(A) SoT（進捗/台本/サムネ等の状態）** と **(B) L1成果物（mp4等のbytes）** を混ぜてしまうこと。

- SoT（工場の状態）: `YTM_WORKSPACE_ROOT` が指す `ytm_workspaces/`（例: `<SHARE_ROOT>/ytm_workspaces/`）
  - ここは UI/API が読み書きする “正本”
- L1 bytes store（本書）: `YTM_SHARED_STORAGE_ROOT/uploads/<namespace>/...`
  - ここは “最終成果物の保管庫”（manifest/hashで監査できるようにする）
- CapCutは例外ルールがある:
  - 編集（Hot）: Macローカル（ドラフト直編集は共有でしない）
  - 受け渡し（Exports）: `<SHARE_ROOT>/capcut_exports/...`
  - 投稿後のドラフト: 原則削除（ユーザー方針）。必要回のみ `<SHARE_ROOT>/archive/capcut_drafts/...` に Draft Pack 退避

この区別が守られていれば「どのエージェントでも保存先情報に困らない」状態になる。

---

## 1) 共有ストレージ root（必須: env）

共有ストレージのマウント先は環境変数で指定する。

- `YTM_SHARED_STORAGE_ROOT`（必須）: 共有ストレージ root（絶対パス）
  - 例: `/Volumes/ytm_share`（例。各マシンで mount 先は異なるが、パイプラインは env だけを見る）
  - 例（現状のインフラ想定）:
    - Mac: `/Volumes/lenovo_share`（SMBでLenovo外付けをマウント）
    - Linux(Acer): `/srv/workspace/doraemon/workspace/lenovo_share`（SMB/CIFSマウント）

命名空間（repo識別）は次の順で決める:

1. `YTM_SHARED_STORAGE_NAMESPACE`（指定時のみ。未指定なら2へ）
2. `repo_root().name`（既定: `factory_commentary`）

共有側のベース（固定）:
`$YTM_SHARED_STORAGE_ROOT/uploads/$YTM_SHARED_STORAGE_NAMESPACE/`

### 1.1) オフライン（共有ダウン）時のフォールバック（停止しないための必須ルール）

判定:
- `YTM_SHARED_STORAGE_ROOT/README_MOUNTPOINT.txt` が存在する（mountpoint stub） or 共有が未マウント
- 共有がマウントされていても、`$YTM_SHARED_STORAGE_ROOT/uploads/$YTM_SHARED_STORAGE_NAMESPACE/` が見つからない（degraded mount / export違い・外付け未接続・権限不整合など）

方針（ハイブリッド）:
- **L1 bytes store の保存は継続**する（ただし宛先はローカル）。
- フォールバック先は **ローカルoutbox**（例: `~/doraemon_hq/magic_files/_fallback_storage/lenovo_share_unavailable/`）。
  - 上書き（任意）: `YTM_SHARED_STORAGE_FALLBACK_ROOT`（フォールバック先rootを明示する）
  - 既定（自動）: mountpoint stub 運用で `YTM_SHARED_STORAGE_ROOT/ytm_workspaces` がローカル退避先へ symlink の場合、
    その symlink の target 親（例: `.../outbox/ytm_share/`）をフォールバック先として採用する（= stubへ書かない）。
- 共有が復旧したら、ローカルoutbox → 共有へ後追い同期する（同期が完了するまで outbox は消さない）。

禁止（事故防止）:
- 共有ダウン中は `--symlink-back` / `--move` を禁止（ローカルSoTを “共有依存” にしない）。
- 未投稿（Hot/Freeze）は `--symlink-back` / `--move` を禁止（Macローカル実体を保持する）。投稿済み（publish_lock）以外で破壊操作が必要な場合は break-glass（`--allow-unposted` / `YTM_EMERGENCY_OVERRIDE=1`）を使い、必ずSSOT/Notion/Slackに理由を残す。

---

## 2) 共有側のディレクトリ設計（固定）

共有側は “ドメイン別” に置く（= 人が探せる / 自動化しやすい）。

```
<shared_base>/  （= $YTM_SHARED_STORAGE_ROOT/uploads/$YTM_SHARED_STORAGE_NAMESPACE/）
  scripts/CHxx/NNN/assembled_human.md
  episode_asset_pack/CHxx/episode_asset_pack__CHxx-NNN.tgz
  remotion_mp4/CHxx/NNN/<run_id>.mp4
  thumbnails/CHxx/NNN/<variant_id>/*.png
  audio_final/CHxx/NNN/CHxx-NNN.wav
  audio_final/CHxx/NNN/CHxx-NNN.srt
  capcut_exports/CHxx/NNN/<run_id>.mp4
  capcut_draft_packs/CHxx/NNN/capcut_draft_pack__CHxx-NNN__YYYYMMDD__<tag>.tgz
  manifests/<kind>/<timestamp>__<id>.json
```

固定ルール:
- **SoTと同じ階層をそのままコピーしない**（探索ノイズを増やす）。
- 共有側の `manifests/` に「何を・どこへ・どのhashで置いたか」を残す（復元と監査のため）。
 - CapCutドラフトは “編集用（Hot）” と “保管（Draft Pack）” を分け、共有上で直接編集しない（関連: `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`）。

---

## 3) 保存対象（固定）

保存するのは “最終成果物（L1）” のみ。

例（L1）:
- 台本: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（存在する場合のみ）
- 音声: `workspaces/audio/final/{CH}/{NNN}/*`
- 動画:
  - Remotion: `workspaces/video/runs/{run_id}/remotion/output/final.mp4`
  - CapCut: 書き出しmp4（最終動画ファイルが確定した場合のみ）
- サムネ: 採用された最終出力（projects.jsonで採用判定できる形）
- 画像束: Episode Asset Pack（`scripts/ops/archive_episode_asset_pack.py`）
 - CapCutドラフト（再編集用）: **ドラフト本体ではなく Draft Pack（tgz/zip）** を保存する（編集はHotで行う）

保存しない（L2/L3）:
- `workspaces/video/runs/**/images/**`（再生成可能）
- `apps/remotion/out/**` のチャンク/一時生成物（再生成可能）
- logs/cache（ローテ対象）

---

## 4) 実行ポリシー（固定）

- 共有ストレージ保存は **明示実行**（サイレントで走らない）。
- `YTM_SHARED_STORAGE_ROOT` が未設定なら停止（設定ミス）。
- 共有が未マウント/stub の場合は **ローカルoutboxへフォールバック**し、ログ/manifest に明示する（生成/編集を止めない）。
- 破壊操作（ローカル削除/置換）は **既定でしない**（必要な場合のみ `--move` / `--symlink-back` を明示し、hash検証後に実施）。
  - ただし共有ダウン中は `--move` / `--symlink-back` は禁止（事故防止）。

---

## 5) 整合性（固定）

共有側へ置いた後は、最低限これを満たす:
- サイズ > 0
- SHA256 が一致（manifestに保存）
- コピーは atomic（tmp → rename）

---

## 6) 入口（固定）

実装入口は `./ops` に固定する（迷子防止）。

- 正本: `./ops shared store -- --src <path> [--kind <kind>] [--channel CHxx --video NNN] [--run] [--symlink-back]`
  - 互換: `./ops shared sync -- --src <path> ...`
  - default は dry-run（`--run` 指定時のみコピー+manifest作成）
  - `YTM_SHARED_STORAGE_ROOT` 未設定/未マウントなら停止
  - 容量を空ける（明示）: `--symlink-back`（共有へ保存→ローカルを symlink に置換）
- エピソード一括（L1のみ; 明示）: `./ops shared episode -- --channel CHxx --video NNN [--run] [--symlink-back]`
  - Remotion mp4 も含める（明示）: `--include-remotion --run-id <run_id>`
- Remotion レンダー直後に自動で保存/退避（本線）:
  - `YTM_SHARED_STORAGE_ROOT=... ./ops remotion render-batch -- --channel CHxx --videos NNN --run --shared-store`
  - 容量を空ける（明示）: `--shared-symlink-back`
- 既存の入口（当面）:
  - Episode Asset Pack: `python3 scripts/ops/archive_episode_asset_pack.py --help`
  - Remotion: `./ops remotion help`（同等: `python3 scripts/ops/render_remotion_batch.py --help`）

---

## 7) 関連（SSOT）

- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`（生成物分類L0-L3）
- `ssot/ops/OPS_GH_RELEASES_ARCHIVE.md`（GitHub Releases書庫: 共有ストレージの“次段バックアップ”）
- `ssot/ops/OPS_VIDEO_ASSET_PACK.md`（Episode Asset Pack）
- `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`（共有Workspaces（SoT）の置き場固定とリモートUI閲覧）
- `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`（CapCut編集運用: Mac Hot + 共有は受け渡し/退避）
