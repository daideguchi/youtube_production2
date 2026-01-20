# OPS_SHARED_ASSET_STORE — 共有ストレージ（Tailscale常駐）へ生成資産を置く

目的:
- この repo で生成した最終成果物（L1）を **共有ストレージ**へ集約し、複数マシン/複数人で再利用できる状態を作る。
- Mac の空き容量を保つため、重いL1は **共有側へ保存**し、ローカルは明示操作で軽量化する（例: `--symlink-back`）。

非目的:
- 中間生成物（L2/L3）まで無差別に保存すること。
- 共有が未マウントでも “勝手にローカルへフォールバックして続行” すること（停止して報告）。

---

## 0) 正本（SoT）

- **パス契約（正本）**: `workspaces/**`（パイプラインが参照する固定パス）
- **L1の実体（bytes）**: 共有ストレージへ保存し、ローカルは明示操作で symlink に置換して容量を空ける。

---

## 1) 共有ストレージ root（固定）

共有ストレージのマウント先は環境変数で指定する。

- `YTM_SHARED_STORAGE_ROOT`（必須）: 共有ストレージ root（絶対パス）
  - 例: `/Volumes/ytm_share`（Tailscale経由の常駐ストレージをマウント）

命名空間（repo識別）は次の順で決める:

1. `YTM_SHARED_STORAGE_NAMESPACE`（指定時のみ。未指定なら2へ）
2. `repo_root().name`（既定: `factory_commentary`）

共有側のベース:
`$YTM_SHARED_STORAGE_ROOT/$YTM_SHARED_STORAGE_NAMESPACE/`

---

## 2) 共有側のディレクトリ設計（固定）

共有側は “ドメイン別” に置く（= 人が探せる / 自動化しやすい）。

```
<shared_base>/
  episode_asset_pack/CHxx/episode_asset_pack__CHxx-NNN.tgz
  remotion_mp4/CHxx/NNN/<run_id>.mp4
  thumbnails/CHxx/NNN/<variant_id>/*.png
  audio_final/CHxx/NNN/CHxx-NNN.wav
  audio_final/CHxx/NNN/CHxx-NNN.srt
  manifests/<kind>/<timestamp>__<id>.json
```

固定ルール:
- **SoTと同じ階層をそのままコピーしない**（探索ノイズを増やす）。
- 共有側の `manifests/` に「何を・どこへ・どのhashで置いたか」を残す（復元と監査のため）。

---

## 3) 同期対象（固定）

同期するのは “最終成果物（L1）” のみ。

例（L1）:
- 台本: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（存在する場合のみ）
- 音声: `workspaces/audio/final/{CH}/{NNN}/*`
- 動画:
  - Remotion: `workspaces/video/runs/{run_id}/remotion/output/final.mp4`
  - CapCut: （最終動画ファイルが確定した場合のみ）
- サムネ: 採用された最終出力（projects.jsonで採用判定できる形）
- 画像束: Episode Asset Pack（`scripts/ops/archive_episode_asset_pack.py`）

同期しない（L2/L3）:
- `workspaces/video/runs/**/images/**`（再生成可能）
- `apps/remotion/out/**` のチャンク/一時生成物（再生成可能）
- logs/cache（ローテ対象）

---

## 4) 実行ポリシー（固定）

- 共有ストレージ同期は **明示実行**（サイレントで走らない）。
- `YTM_SHARED_STORAGE_ROOT` が未設定/未マウントなら **停止して報告**（勝手に別経路へ逃げない）。
- 破壊操作（ローカル削除/置換）は **既定でしない**（必要な場合のみ `--move` / `--symlink-back` を明示し、hash検証後に実施）。

---

## 5) 整合性（固定）

共有側へ置いた後は、最低限これを満たす:
- サイズ > 0
- SHA256 が一致（manifestに保存）
- コピーは atomic（tmp → rename）

---

## 6) 入口（固定）

実装入口は `./ops` に固定する（迷子防止）。

- `./ops shared sync -- --src <path> [--kind <kind>] [--channel CHxx --video NNN] [--run]`
  - default は dry-run（`--run` 指定時のみコピー+manifest作成）
  - `YTM_SHARED_STORAGE_ROOT` 未設定/未マウントなら停止
  - 容量を空ける（明示）: `--symlink-back`（共有へ保存→ローカルを symlink に置換）
- 既存の入口（当面）:
  - Episode Asset Pack: `python3 scripts/ops/archive_episode_asset_pack.py --help`
  - Remotion: `./ops remotion help`（同等: `python3 scripts/ops/render_remotion_batch.py --help`）

---

## 7) 関連（SSOT）

- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`（生成物分類L0-L3）
- `ssot/ops/OPS_GH_RELEASES_ARCHIVE.md`（GitHub Releases書庫: 共有ストレージの“次段バックアップ”）
- `ssot/ops/OPS_VIDEO_ASSET_PACK.md`（Episode Asset Pack）
