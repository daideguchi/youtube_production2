# OPS_CAPCUT_DRAFT_STORAGE_STRATEGY — CapCutドラフト（編集）資産の置き場と共有運用

目的:
- CapCutのドラフト操作（編集・シーク・プレビュー）が遅くならないように、**作業領域（Hot）** を確保する。
- ただし Mac 内蔵SSDの容量には限りがあるため、**共有/保管（Warm/Cold）** と分離して永続運用できる形にする。
- 「別PCはURLで見る」運用（UI/Files）と両立させる。

結論（推奨）:
- CapCutの“編集中”資産は **Macローカル（できれば外付けNVMe）** に置く（Hot）。
- 共有ストレージ（Acer `/files`）は **受け渡し/保管庫** として使う（Warm/Cold）。
- 編集中プロジェクトを `/files`（SMB/SSHFS/Tailscale越し）から直接参照してCapCutを動かす運用は、原則やらない（体感が悪化しやすい）。

---

## 0) レイヤー分け（Hot/Warm/Cold）

### Hot（編集用・最優先で速い）
- 置き場: Macローカル or 外付けNVMe SSD
- 対象:
  - CapCutプロジェクト（ドラフト）本体
  - タイムライン編集で頻繁に参照されるメディア（動画/音声/画像）
  - CapCutのキャッシュ/プレビュー/プロキシ（設定で移せるならここ）
- 理由:
  - 連続シーク + ランダムI/O が多く、ネットワーク越しだと “MB/s差以上に” 体感が落ちるため。

### Warm（共有SoT・運用UIが見る正本）
- 置き場: Acer `/files/ytm_workspaces/`（実体: `/srv/workspace/media/ytm_workspaces`）
- 対象:
  - planning/scripts/thumbnails/audio など `workspaces/**`（SoT）
- 理由:
  - UI/APIはAcer上でローカルI/Oとして読めるため、共有だから遅いわけではない。

### Cold（保管庫・完了案件の退避）
- 置き場: Acer `/files/media/` など（大容量向け）
- 対象:
  - 完了したCapCutのプロジェクトパック/素材束
  - 原素材（再編集の可能性があるもの）

---

## 1) 具体運用（おすすめフロー）

### A) 編集（CapCut）はHotだけで完結させる
- 編集開始時に必要な素材だけ Hot に集める（作業セット）。
- 編集中は Hot 以外に参照しない（ネットワーク参照を混ぜない）。

### B) 受け渡し（書き出し）は `/files` を使う
SSOT（運用導線）:
- `https://acer-dai.tail8c523e.ts.net/files/PLAN_FACTORY_COMMENTARY_REMOTE.md`

例（すでにある導線）:
- CapCut書き出し投入: `/files/lenovo_share/capcut_exports/`
- 変換/処理出力: `/files/lenovo_share/uploads/` や `/files/lenovo_share/outbox/`

### C) 完了したらColdへ退避してHotを空ける
- 完了案件は `/files/media/...` にまとめて退避（例: 日付/チャンネル/回番号で整理）。
- “また編集する可能性がある案件だけ” を Hot に戻す。

---

## 2) 注意（速度が落ちる典型パターン）

- 編集中のCapCutプロジェクト/素材を `/files` から直接読み書きする（特にTailscale越し）:
  - レイテンシと小ファイルI/Oで体感が悪化しやすい。
- MacローカルのSoTと共有SoTを二重運用して巻き戻す:
  - UIで更新したplanningを、ローカル同期で上書きしない（SoTは1箇所に固定）。

---

## 3) パス未確定でも “先に決める” 情報（メモ項目）

CapCutは「どこにドラフト/キャッシュがあるか」で運用が変わる。パスが未確定でも、以下を “決める/記録する” だけで後工程が詰まらない。

- ドラフト正本（CapCutアプリ側の標準root）:
  - 既定（macOS）: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`
  - SSOT参照: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`
- キャッシュ/プロキシ（CapCut設定で移せるなら、Hot側へ寄せる）:
  - “移せる/移せない” の可否だけ先に確認して記録する（パスは後でOK）
- 書き出し（Exports）の置き場:
  - “作業用ローカル” と “受け渡し（/files）” のどちらを正とするか
  - 受け渡し正本は `/files/lenovo_share/capcut_exports/`（既存導線）

移設する場合の方針（買わない前提でも使える）:
- 「CapCutが見る標準root（上記）」は維持し、必要なら **symlinkで中身だけ別ボリュームへ逃がす**。
  - 例: `com.lveditor.draft` を外付け/別ボリュームへ移動 → 元の場所に symlink（CapCut/既存ツールの互換を崩さない）
  - 注意: CapCutを完全終了してから作業する（編集中に移動しない）

---

## 4) “退避（Cold）” の仕様（パス未確定でも固定できる）

狙い:
- 編集が終わった案件を Mac から消して容量を空けるが、必要なら “復帰して再編集” できる状態にする。
- 共有（/files）へ置くのは **アーカイブ（tgz/zip）** とし、CapCutがネットワーク越しに直接編集する運用はしない。

推奨フォーマット:
- 1案件=1アーカイブ: `capcut_draft_pack__CHxx-NNN__YYYYMMDD__<tag>.tgz`
- 収録対象（基本は丸ごと）:
  - `com.lveditor.draft/<draft_dir>/`（`draft_content.json`, `draft_info.json`, `draft_meta_info.json`, `materials/` などを含む）
- 置き場（推奨）:
  - 大容量があるなら: `/files/media1tb/capcut_archives/CHxx/NNN/`
  - それ以外: `/files/media/capcut_archives/CHxx/NNN/`
- 最低限のメタ（同ディレクトリに小さく置く）:
  - `README.md`（復帰手順/注意点）
  - `sha256.txt`（改ざん/転送ミス検出用）

---

## 5) 退避/復帰の流れ（コマンド化は後でOK）

退避（Mac → /files）:
1) CapCutを閉じる（完全終了）
2) 対象ドラフトをアーカイブ化（tgz/zip）
3) `/files/media.../capcut_archives/...` に置く
4) アーカイブが開けることを確認したら、Macローカルの元ドラフトを削除（容量回収）

復帰（/files → Mac）:
1) アーカイブをMacのHot（ローカル/外付け）へコピー
2) 展開して、CapCut標準root配下（またはsymlink先）に戻す
3) CapCutで開いて再編集

---

## 6) 受け入れ基準

- CapCut編集が引っかからずに操作できる（Hot）。
- 進捗/台本/サムネは `https://acer-dai.tail8c523e.ts.net/ui/` でどの端末から見ても一致（Warm）。
- 完了した案件を退避しても、必要なら復帰できる（Cold）。

---

## 関連SSOT

- 共有Workspaces（SoT）運用: `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`
- 共有ストレージ（L1退避）: `ssot/ops/OPS_SHARED_ASSET_STORE.md`
- CapCut運用（既存）: `ssot/ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`
