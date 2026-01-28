# OPS_CAPCUT_DRAFT_STORAGE_STRATEGY — CapCutドラフト（編集）資産の置き場と共有運用

目的:
- CapCutのドラフト操作（編集・シーク・プレビュー）が遅くならないように、**作業領域（Hot）** を確保する。
- ただし Mac 内蔵SSDの容量には限りがあるため、**共有/保管（Warm/Cold）** と分離して永続運用できる形にする。
- 「別PCはURLで見る」運用（UI/Files）と両立させる。

結論（推奨）:
- CapCutの“編集中”資産は **Macローカル（内蔵SSD。外付けSSDは任意）** に置く（Hot）。
- 共有ストレージ（実体は Lenovo 外付け共有。Acer の `/files` は“閲覧ゲートウェイ”）は **受け渡し/保管庫** として使う（Warm/Cold）。
- 編集中プロジェクトを `/files`（SMB/SSHFS/Tailscale越し）から直接参照してCapCutを動かす運用は、原則やらない（体感が悪化しやすい）。

絶対ルール（運用の前提）:
- メインは **Mac**。Hot（未投稿のCapCutドラフト/Workset/編集中素材）は **必ず Mac ローカルに実体を持つ**（「Lenovoにしか無い」はNG）。
- 共有ストレージは容量/受け渡し/保管/ミラー用途。**落ちていても Mac 側の編集/生成を止めない**（共有への同期は復旧後に追随）。
- 参照パスは **実体へ**: manifest/log/json 等に外部マウントの絶対パスを `resolve()` して書かない（`workspaces/**` ベースの安定パス or run_dir 内のローカルコピーを指す）。動的参照（PathRef）の規約は `ssot/ops/OPS_PATHREF_CONVENTION.md` を正とする。
- **未投稿のドラフトは自動で消さない**: 背景自動化（`export_mover` / `capcut_purge_archived` 等）があっても、削除は “明示” があるときだけ（queue の `allow_purge=true` など）。迷ったら purge は停止して Mac の編集を守る。

---

## 0.1) 決裁（迷子防止: “最終資産” の置き場）

他エージェントが迷いがちなポイントは「最終資産＝どれのこと？どこに置く？」が曖昧なこと。  
CapCut運用では “編集の快適さ” を優先するため、最終資産を次のように固定する（パスは未確定でもOK）。

- Hot（編集）: **Macローカル（内蔵SSD）**
  - 外付けSSDは “容量を増やす” ための任意オプション（使わないなら無視でOK）
  - 置く: ドラフト本体・編集中素材・キャッシュ/プロキシ
  - 置かない: 共有（SMB/Tailscale）上のドラフト直編集
- Warm（SoT）: `<SHARE_ROOT>/ytm_workspaces/`（= `YTM_WORKSPACE_ROOT`）
  - 置く: 台本/サムネ/進捗/音声/動画run 等の “Factoryの状態”
- Shared exports（納品mp4）: `<SHARE_ROOT>/capcut_exports/CHxx/NNN/`
  - 置く: CapCut書き出しmp4（受け渡し/閲覧/自動処理）
- 投稿後のドラフト: **原則削除**（ユーザー決裁）
  - 例外: 再編集の可能性が高い回だけ Draft Pack（tgz/zip）で退避（置き場: `<SHARE_ROOT>/archive/capcut_drafts/CHxx/NNN/`）
- L1長期保管（任意）: `<SHARE_ROOT>/uploads/<namespace>/...`（`./ops shared store`）
  - SoTではなく「最終成果物のbytes置き場」（監査/復元用）

補足:
- `/files` は「AcerがWeb公開しているファイル閲覧」であり、実体がAcer内蔵とは限らない（Lenovo外付けのマウントでも良い）。

---

## 0.2) “素材だけ共有” はどこまで効くか？

結論:
- BGM/画像など “追加したい素材が別マシンにしか無い” 問題には効く（素材庫を共有すれば挿入できる）。
- ただし「既存ドラフトを別PCで開いたときの参照切れ」は、素材庫共有だけでは防げない。  
  → プロジェクト（ドラフト）側が素材を内包/同期している必要がある。

推奨:
- “共有素材庫（Asset Vault）” を作る（例: `<LENOVO_SHARE_ROOT>/asset_vault/`）。
- 追加素材は必ず Asset Vault 経由で取り込む（Macだけのローカルに置かない）。
- 使い終わったドラフトは投稿後に削除し、必要なら mp4 と最小メタだけ残す（必要回のみDraft Pack退避）。

補足（SRT/WAVの痛点）:
- Lenovoで「字幕（SRT）/音声（WAV）を差し替えたい」のに Mac にしか無いと詰む。
- したがって、確定データ（`workspaces/audio/final/**`）は **共有SoT（`YTM_WORKSPACE_ROOT`）に置く**（= どのマシンでも挿入できる前提を作る）。

---

## 0.3) 参照先を動的にしたい場合の整理（Workset方式）

「どのPCでもドラフトを編集したい」を成立させるには、素材の所在がバラけないようにする必要がある。  
推奨は、共有の Asset Vault と、各マシンの Hot 上の Workset（作業セット）を分ける方式。

- Asset Vault（共有・正本）: `<LENOVO_SHARE_ROOT>/asset_vault/`
  - BGM/SE/画像/フォント/テンプレなど “再利用資産” を集約
- Workset（Hot・回ごと）: `<HOT_ROOT>/capcut_worksets/<CHxx-NNN>/`
  - “その回に必要な素材だけ” を集めたフォルダ（Mac/Lenovoそれぞれのローカル/外付けに置く）

運用ルール:
1) 新規素材は必ず Asset Vault へ入れる
2) 編集前に Workset を作る（Asset Vault から必要分だけコピー）
3) 生成画像（例: Fluxで作った画像）は `workspaces/video/runs/<run_id>/images/` を正とし、Worksetへ必要分だけコピーする
4) 音声/WAVと字幕/SRTは `workspaces/audio/final/<CHxx>/<NNN>/` を正とし、Worksetへ必要分だけコピーする
5) CapCutは Workset から取り込む（参照切れしにくい）
   - run_dir（`video/runs`）は工程の証跡/再現性のために残す（位置を動かさない）
6) 投稿後は Draft/Workset を原則削除（例外のみDraft Pack退避）

この方式だと「素材がMacにしか無いからLenovoで挿入できない」が構造的に起きなくなる。

作業セット生成（コード化済み / dry-runが既定）:
- `./ops video capcut-workset -- --channel CHxx --video NNN`
- `./ops video capcut-workset -- --channel CHxx --video NNN --run`
- 置き場の既定: `YTM_CAPCUT_WORKSET_ROOT` → `YTM_OFFLOAD_ROOT/capcut_worksets` → `~/capcut_worksets`

---

## 0) レイヤー分け（Hot/Warm/Cold）

### Hot（編集用・最優先で速い）
- 置き場: **Macローカル（内蔵SSD）**
- 対象:
  - CapCutプロジェクト（ドラフト）本体
  - タイムライン編集で頻繁に参照されるメディア（動画/音声/画像）
  - CapCutのキャッシュ/プレビュー/プロキシ（設定で移せるならここ）
- 理由:
  - 連続シーク + ランダムI/O が多く、ネットワーク越しだと “MB/s差以上に” 体感が落ちるため。

### Warm（共有SoT・運用UIが見る正本）
- 置き場: **共有ストレージ上**の `ytm_workspaces/`（SoT）
  - Acerは共有をマウントしてUI/API/Filesを提供する（Acerローカル固定ではない）
- 対象:
  - planning/scripts/thumbnails/audio など `workspaces/**`（SoT）
- 理由:
  - UI/APIはAcer上でローカルI/Oとして読めるため、共有だから遅いわけではない。

### Cold（保管庫・完了案件の退避）
- 置き場: **Lenovo外付け（共有ストレージ実体）** を正本にする
  - 推奨: `<SHARE_ROOT>/archive/capcut_drafts/`
  - 補足: Acer の `/files/...` は “Web閲覧の見え方” であり、実体パスとは別（翻訳表は Notion `image-ddd` で管理）
- 対象:
  - 完了したCapCutのプロジェクトパック/素材束
  - 原素材（再編集の可能性があるもの）

---

## 1) 具体運用（おすすめフロー）

### A) 編集（CapCut）はHotだけで完結させる
- 編集開始時に必要な素材だけ Hot に集める（作業セット）。
- 編集中は Hot 以外に参照しない（ネットワーク参照を混ぜない）。

### A-2) ドラフト命名（参照紐付け事故を減らす）
CapCutドラフトは参照紐付け（run_dir ↔ draft）で事故りやすいので、**“後から変わる要素（planning title）” をドラフト名に入れない**のが安全。

- 推奨（`auto_capcut_run`）:
  - `--draft-name-policy run`（planningではなくrun名ベース）
  - `--no-draft-name-with-title`（タイトルsuffixを付けない）
- 期待する性質:
  - フォルダ名に `CHxx-NNN` が必ず含まれる
  - title変更でドラフト名が変わらない（`(1)` 増殖やリンク切れを減らす）

### B) 受け渡し（書き出し）は `/files` を使う
SSOT（運用導線）:
- `https://acer-dai.tail8c523e.ts.net/files/PLAN_FACTORY_COMMENTARY_REMOTE.md`

例（すでにある導線）:
- CapCut書き出し投入: `<SHARE_ROOT>/capcut_exports/`
- 変換/処理出力: `<SHARE_ROOT>/uploads/` や `<SHARE_ROOT>/outbox/`

### C) 完了したらColdへ退避してHotを空ける
- 投稿完了後は **原則ドラフト削除**（ユーザー方針）。
- 例外（再編集が高確率の回）のみ、**共有ストレージ（Lenovo外付け）** の `archive/capcut_drafts/` に Draft Pack 退避する（例: 日付/チャンネル/回番号で整理）。
- “また編集する可能性がある案件だけ” を Hot に戻す（= Draft Packから復帰）。

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
  - SSOT参照: `ssot/ops/OPS_CAPCUT_DRAFT_SOP.md`
  - 実装側メモ（補助）: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`
- キャッシュ/プロキシ（CapCut設定で移せるなら、Hot側へ寄せる）:
  - “移せる/移せない” の可否だけ先に確認して記録する（パスは後でOK）
- 書き出し（Exports）の置き場:
  - “作業用ローカル” と “受け渡し（/files）” のどちらを正とするか
- 受け渡し正本は `<SHARE_ROOT>/capcut_exports/`（既存導線）

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
  - 正本: `<SHARE_ROOT>/archive/capcut_drafts/CHxx/NNN/`
  - 例外（Lenovo shareが無い/落ちているときのみ）: `/files/media/capcut_drafts/CHxx/NNN/`
- 最低限のメタ（同ディレクトリに小さく置く）:
  - `README.md`（復帰手順/注意点）
  - `sha256.txt`（改ざん/転送ミス検出用）

---

## 5) 退避/復帰の流れ（コマンド化は後でOK）

退避（Mac → /files）:
1) CapCutを閉じる（完全終了）
2) 対象ドラフトをアーカイブ化（tgz/zip）
3) `<SHARE_ROOT>/archive/capcut_drafts/...` に置く（= 実体はLenovo外付け共有）
4) アーカイブが開けることを確認したら、Macローカルの元ドラフトを削除（容量回収）

復帰（/files → Mac）:
1) アーカイブをMacのHot（ローカル/外付け）へコピー
2) 展開して、CapCut標準root配下（またはsymlink先）に戻す
3) CapCutで開いて再編集

---

## 6) 受け入れ基準

- CapCut編集が引っかからずに操作できる（Hot）。
- 未投稿（Hot）について `scripts/ops/capcut_draft_integrity_doctor.py --all-channels` が `bad=0`（参照切れなし）。
- 進捗/台本/サムネは `https://acer-dai.tail8c523e.ts.net/ui/` でどの端末から見ても一致（Warm）。
- 完了した案件を退避しても、必要なら復帰できる（Cold）。

---

## 関連SSOT

- 共有Workspaces（SoT）運用: `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`
- 共有ストレージ（L1退避）: `ssot/ops/OPS_SHARED_ASSET_STORE.md`
- CapCut運用（既存）: `ssot/ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`
