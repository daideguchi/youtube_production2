# OPS_CAPCUT_DRAFT_EDITING_WORKFLOW — CapCutドラフト作成/編集の運用（Macメイン + 共有ストレージ連携）

目的:
- CapCut編集は **Macだけ** で成立させつつ、他端末からは **URL（UI/Files）で資産確認できる**運用にする。
- 容量制約（Mac内蔵SSD）と速度（編集体感）を両立させるため、編集（Hot）と保管/受け渡し（Shared/Cold）を分離する。

非目的:
- Acer/Ubuntu側でCapCut編集をすること（GUI前提のため）。
- CapCutドラフトをネットワーク越し（SMB/Tailscale）で“直接編集”できるようにすること（体感/事故率が悪化しやすい）。

前提:
- CapCutの標準ドラフトroot（macOS）: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`
  - SSOT（内部仕様/復旧/事故防止）: `ssot/ops/OPS_CAPCUT_DRAFT_SOP.md`
  - 実装側メモ（補助）: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`
- 工場側のSoT（台本/サムネ/進捗/音声等）: `workspaces/**`（`YTM_WORKSPACE_ROOT`）
- 共有ストレージ候補: Lenovo外付け（SMB共有）など “容量が大きく常時稼働できる” 置き場

---

## 0) 結論（推奨）

- **編集（CapCut）はMacローカル（内蔵SSD）で完結**（外付けSSDは任意。使わないなら無視でOK）。
- 共有ストレージは **受け渡し（Exports）** と **退避（Draft Pack/素材保管）** に使う。
- “どこでもURLで同じ資産が見える” を成立させるには、UIが見るSoT（`workspaces/**`）と、Exports/Archiveが置かれる共有ストレージを **一貫して同じ正本**に揃える。

---

## 0.1) 決裁（迷子防止: 最終資産の置き場を固定）

「最終資産がどこか」で迷子が発生すると、参照紐付け・復旧・引き継ぎが破綻する。  
パスが未確定でも **ディレクトリ名と役割** はここで固定し、実パス/URLは `image-ddd` の翻訳表で管理する。

- SoT（工場の正本）: `<SHARE_ROOT>/ytm_workspaces/`（= `YTM_WORKSPACE_ROOT` が指す先）
  - 台本/サムネ/進捗/音声/動画run など、Factoryが参照する “状態” はすべてここ
- 納品mp4（受け渡し）: `<SHARE_ROOT>/capcut_exports/CHxx/NNN/`
  - 別端末/スマホからURLで確認できる置き場
- 投稿後のドラフト: **原則削除**（ユーザー決裁）
  - 例外: “後で再編集の可能性が高い回だけ” Draft Pack（tgz/zip）で退避して `<SHARE_ROOT>/archive/capcut_drafts/CHxx/NNN/` に置く
- L1退避（任意・長期保管/監査）: `<SHARE_ROOT>/uploads/<namespace>/...`（`./ops shared store`）
  - “SoTの置き場” ではなく “最終成果物のbytes置き場”

重要:
- 共有上にドラフト本体を置いて “直接編集” はしない（速度/事故率の観点でNG）
- Acer は「ゲートウェイ（/ui /api /files）」として扱い、Acer手作業は前提にしない（セットアップはコードで完結させる）

---

## 0.2) “素材だけ共有” は有効か？（結論: はい、ただし条件あり）

結論:
- 「Lenovoで編集したいのに、BGM/画像がMacにしか無い」問題は、**素材（BGM/SE/画像/フォント/テンプレ）を共有する**ことで解消できる。
- ただし「ドラフトを別PCで開いたら参照切れ」は、素材共有だけでは防げない。  
  → **ドラフト（プロジェクト）側に素材が取り込まれていること**（CapCutが内部に保持/同期していること）が別条件。

推奨運用:
- “共有素材庫（Asset Vault）” を1つ作る（例: `<LENOVO_SHARE_ROOT>/asset_vault/`）
  - BGM/SE/画像/フォント/テンプレは「まずここへ入れる」→ CapCutに取り込む
  - こうすると Lenovo編集時に「あの素材はMacにあるから挿入できない」が起きない
- CapCutプロジェクトに使った素材は、プロジェクト内に取り込まれる形に寄せる（参照切れ防止）
  - CapCut Space（共有）を使う場合も、素材が “ローカル参照のまま” だと他マシンで欠けることがあるので注意

---

## 0.3) “参照先を動的にする” の正解（推奨: Workset方式）

CapCutの「参照先を動的にする」とは、OSの絶対パスをコロコロ変えることではなく、
**編集に必要な素材を“作業セット（Workset）”として切り出し、どのマシンでも再現できる**状態にすること。

補足（JSON/manifestの参照表現）:
- 工場側の manifest/log/json は “ホスト固有の絶対パス混入” を禁止し、動的参照（PathRef）を使う（正本: `ssot/ops/OPS_PATHREF_CONVENTION.md`）。

構成（パスは未確定でもOK）:
- Asset Vault（共有・正本）: `<LENOVO_SHARE_ROOT>/asset_vault/`
  - 再利用する素材（BGM/SE/画像/フォント/テンプレ）の置き場
- Workset（Hot・回ごと）: `<HOT_ROOT>/capcut_worksets/<CHxx-NNN>/`
  - “この回に必要な素材だけ” を集めた作業フォルダ（Mac/Lenovoそれぞれのローカル/外付けに置く）
  - ここからCapCutに読み込む（= 他マシンへ移った時も同じ構成を再現できる）

運用ルール（これだけでカオスが止まる）:
1) 新規素材はまず Asset Vault へ入れる（Macローカルに“だけ”置かない）
2) 編集する回の Workset を作る（Asset Vault → Workset へ必要分だけコピー）
3) 生成画像（例: Fluxで作った画像）は `workspaces/video/runs/<run_id>/images/` を正とし、Worksetへ必要分だけコピーする
4) 音声/WAVと字幕/SRTは `workspaces/audio/final/<CHxx>/<NNN>/` を正とし、Worksetへ必要分だけコピーする
5) CapCutには Workset から取り込む（参照切れしにくい）
   - run_dir（`video/runs`）は工程の証跡/再現性のために残す（位置を動かさない）
6) 投稿完了後は Draft/Workset を原則削除（例外のみDraft Pack退避）

重要（SRT/WAVの“差し替えできない”事故を潰す）:
- `workspaces/audio/final/**` は **共有SoT（`YTM_WORKSPACE_ROOT`）を正本**にする（Macだけに置かない）。
- Lenovoで編集するときは、SRT/WAVを **共有SoTから参照して取り込む**（ブラウザDLはしない。マウント/コピーのみ）。

メリット:
- Lenovoで編集するときも「素材がMacにしか無い」が起きない
- Mac容量は “今触る回のWorksetだけ” で済む（不要になったら削除できる）

注意:
- Worksetをネットワークマウントに置いて“直接編集”はしない（Hotの意味が消える）

---

## 1) 外付けSSD（任意）とは（運用上の意味）

外付けSSD（任意）:
- SSDをケースで外付けにしたもの。**必須ではない**（買わない/使わないなら無視でOK）。
- 体感上の位置づけ:
- **Mac内蔵SSDに近い速度**（少なくともネットワーク共有より速いことが多い）
  - “容量を増やす” というより **編集Hot領域を拡張する**ための道具

---

## 2) レイヤー分け（Hot / Shared / Cold）

### Hot（Mac編集用: 速さ最優先）
- 置くもの:
  - CapCutドラフト（編集中プロジェクト）
  - 編集で頻繁に参照されるメディア（必要分だけ）
  - CapCutキャッシュ/プロキシ（移せるならHotに寄せる）
- 置かない（推奨）:
  - ネットワークマウント（SMB/Tailscale）上のドラフトを“直接編集”

### Shared（受け渡し/閲覧/自動処理）
- 置くもの:
  - 書き出し動画（mp4）: 他端末/常駐サーバーが拾える場所
  - 工場UIが見るSoT（`workspaces/**`）: どの端末から見ても同じ状態にする
- 注意:
  - Sharedは “閲覧/受け渡し” が主目的。編集体感を要求しない設計に寄せる。

### Cold（完了案件の退避: 容量回収）
- 置くもの:
  - 完了したCapCutドラフトを `tgz/zip` でパックしたもの（Draft Pack）
  - 再編集の可能性がある原素材束
- 参照: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`（フォーマット/復帰手順）

---

## 3) 推奨フロー（実務）

### A) Macでドラフト作成/編集（Hot）
1. 必要素材をHotへ集めて編集する（ネットワーク参照を混ぜない）
2. 書き出し（mp4）は “SharedのExports” へ置く（受け渡し）

### B) 工場run_dirとの参照紐付け（リンク切れ防止）
別Codexが作業中の「run_dir ↔ capcut_draft」紐付けが正確に機能するために:
- ドラフト名に “後で変わる要素（planning title等）” を混ぜない（`(1)`増殖/リンク切れを減らす）
- 詳細: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`（`--draft-name-policy run` 等）

### C) 投稿完了後: 原則ドラフト削除（必要回のみDraft Pack退避）
1. 投稿が完了し、今後の再編集が不要なら **Hot側の元ドラフトを削除**（ユーザー方針）
2. 再編集の可能性が高い回だけ、例外として Draft Pack（`tgz/zip`）を作り、Shared/Coldへ退避する
3. 退避した場合は、アーカイブが開けること（hash一致）を確認してから Hot 側の元ドラフトを削除

---

## 4) 速度の考え方（ざっくり）

編集体感は「帯域（MB/s）」より **レイテンシ + 小ファイルI/O** の影響が大きい。

傾向:
- Mac内蔵SSD（ローカル）: 速い（編集向き）
- 同一LAN内のSMB共有: “閲覧/受け渡し” には十分なことが多いが、編集用途は不安定になりやすい
- Tailscale越しSMB: 回線/経路で変動が大きく、編集用途はさらに避ける

---

## 5) 共有ストレージの契約（パス未確定でも決められる）

ここで言う「共有ストレージ」は **Lenovo外付けをSMB共有したもの**を第一候補とする（Acerはそれをマウントして `/ui`/`/api`/`/files` の入口になる）。

### 必須の性質（これだけ守ればパス名は自由）
- Mac と Acer の両方から読める（マウントできる）
- “SoT（workspaces）” と “受け渡し/退避” を同じ共有に置ける（URL閲覧の一貫性のため）
- 途中でマシンが落ちても壊れない（NAS的に運用できる）

### 推奨ディレクトリ（例: 共有root直下に作る）
（既に `capcut_exports/` や `archive/` がある前提に合わせる）

```
<SHARE_ROOT>/                     # Lenovo外付けの共有root（Mac/Acerでマウント先は違ってOK）
  ytm_workspaces/                 # 工場SoT（workspaces/** の正本）
  capcut_exports/                 # CapCut書き出しmp4の受け渡し
  archive/
    capcut_drafts/                # CapCutドラフトの退避（Draft Pack）
```

用語（重要）:
- `<SHARE_ROOT>` は「Lenovo外付けの共有のマウント先」。例:
  - Mac: `/Volumes/lenovo_share`（例）
  - Acer: `/srv/workspace/doraemon/workspace/lenovo_share`（例）

---

## 6) セットアップ（Lenovo / Acer / Mac）

### 6.1 Lenovo（共有の中身を用意）
- 共有の中に `ytm_workspaces/` / `capcut_exports/` / `archive/capcut_drafts/` を作る
- 読み書き権限は「AcerのUIプロセス」と「Macの作業ユーザ」が書けること（権限がブレると事故る）

### 6.2 Acer（UI/Filesのゲートウェイ）
目的: Acer上のUIが **共有の `ytm_workspaces/` を正本として読む/書く**。

- `YTM_WORKSPACE_ROOT=<acer_mount>/ytm_workspaces` を systemd `factory_ui_hub.service` に設定
- Tailscale Serve/Reverse proxy は次を満たす（正確なコマンドは環境依存）:
  - `/ui/*` → frontend（3000）
  - `/api/*` と `/thumbnails/*` → backend（8000）
  - `/files/*` → 共有のファイル閲覧（必要なら）
- 確認（ブラウザ）:
  - `https://<acer>.ts.net/ui/`（React UI）
  - `https://<acer>.ts.net/api/healthz`
  - `https://<acer>.ts.net/files/`（ファイル閲覧）

### 6.3 Mac（CapCut編集の母艦）
目的: Macで生成/編集した資産が、URL側（Acer UI/Files）でも見えるようにする。

- 共有（Lenovo外付け）をMacへマウント（SMB推奨）
- 工場SoTを共有に揃える（推奨）:
  - `export YTM_WORKSPACE_ROOT="<mac_mount>/ytm_workspaces"`
  - これで `./ops ...` / `auto_capcut_run` の SoT が共有に出る（同期忘れが消える）
- 共有素材庫（Asset Vault）を共有に揃える（推奨）:
  - `export YTM_ASSET_VAULT_ROOT="<mac_mount>/asset_vault"`（未設定なら `YTM_SHARED_STORAGE_ROOT/asset_vault` が既定）
  - BGM/SE/画像/フォント/テンプレは「必ずここ経由」（Macローカル専用素材を作らない）
- Workset（編集Hot）をローカルに固定（推奨）:
  - `export YTM_CAPCUT_WORKSET_ROOT="<fast_local>/capcut_worksets"`（例: `~/capcut_worksets`）
  - Workset作成（dry-run→実行）:
    - `./ops video capcut-workset -- --channel CHxx --video NNN`
    - `./ops video capcut-workset -- --channel CHxx --video NNN --run`
  - 注意: Worksetをネットワークマウント（SMB/Tailscale）に置かない（編集体感が落ちる）

CapCutドラフトrootをHotへ寄せたい場合（外付けSSDがあるとき・任意）:
- CapCutを完全終了してから、`~/Movies/CapCut/.../com.lveditor.draft` を外付けへ移して symlink で戻す（互換維持）。
  - 注意: 先にコピー/バックアップしてからやる（事故ると復旧が面倒）。

---

## 7) 1本あたりの実運用SOP（CHxx-NNN）

### 7.1 事前条件（UIで“資産が見える”）
- SoT正本（`ytm_workspaces/`）に最低限これがある:
  - `workspaces/scripts/**`（台本）
  - `workspaces/thumbnails/assets/**`（サムネ資産）
  - `workspaces/planning/**`（進捗/企画）

### 7.2 ドラフト生成（Macで実行）
基本は `auto_capcut_run`（詳細SSOT: `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md`）。

安定運用の推奨（参照紐付け事故を減らす）:
- `--draft-name-policy run`
- `--no-draft-name-with-title`

例（SRT final から run_dir + draft を作る）:
```bash
PYTHONPATH=\".:packages\" python3 -m video_pipeline.tools.auto_capcut_run \\
  --channel CH02 \\
  --srt \"$YTM_WORKSPACE_ROOT/audio/final/CH02/034/CH02-034.srt\" \\
  --run-name CH02-034_capcut_v1 \\
  --title \"<belt_title>\" \\
  --draft-name-policy run \\
  --no-draft-name-with-title
```

CH02の必須検査（テンプレ破壊検知）:
```bash
PYTHONPATH=\".:packages\" python3 -m video_pipeline.tools.validate_ch02_drafts \\
  --channel CH02 \\
  --videos 034
```

### 7.3 CapCutで編集（Mac）
- 生成されたドラフトをCapCutで開いて編集
- 編集中の素材参照はHot（ローカル/外付け）に寄せる（共有参照を混ぜない）
  - 推奨: 先に Workset を作り、CapCutには Workset から素材を取り込む（SRT/WAV/画像など）

### 7.4 書き出し（mp4）の置き場（受け渡し）
推奨:
- 出力先: `<SHARE_ROOT>/capcut_exports/CHxx/NNN/`
- ファイル名: `CHxx-NNN__capcut__v1__YYYYMMDD.mp4`（または run_name を含める）

注意:
- 共有が遅い/不安定なら「一旦ローカルへ書き出し → 共有へコピー」でよい（書き出し自体は連続書き込みで大きい）。

### 7.5 退避（Draft Pack）して容量回収
- 完了後は **原則ドラフト削除**（ユーザー方針）
- 例外（再編集の可能性が高い回）だけ “共有へDraft Packで退避” → Hot側を削除してMac容量を空ける
- 退避仕様は次節（8）を参照（※例外運用）

### 7.6 投稿完了後のクリーンアップ（容量対策; SSOT連動）
目的:
- 「投稿済みになったら、生成物（例: WAV / video runs）を消す」を **再現可能なコマンド**に固定する。
- 進捗（planning）が共有SSOTなら、どの端末から投稿済みを付けても同じ結果になる。

前提（推奨）:
- planning は一本化する:
  - 方式A: `YTM_WORKSPACE_ROOT=<SHARE_ROOT>/ytm_workspaces`
  - 方式B-1: `YTM_PLANNING_ROOT=<SHARE_ROOT>/ytm_workspaces/planning`（workspaces自体はMacローカルでも可）
- 点検: `./ops storage doctor`

実行（例: 1本だけ / 削除 / run）:
```bash
# 例: 投稿済み=YES の回だけを対象に、video runs と CapCut下書きを削除する
./ops archive published --channel CHxx --video NNN --video-runs --capcut-drafts --delete --run --yes

# 例: WAVも消す（再生成できる前提なら）
./ops archive published --channel CHxx --video NNN --audio --delete --run --yes
```

注意:
- `--delete` は不可逆。迷うなら `--delete` を外して “_archive へ移動” を先に使う。
- 指示: **台本（scripts）とサムネ（thumbnails/assets）は消さない**（`archive published` でも対象にしない）。

---

## 8) Draft Pack（退避）手順（Macで実行）

注:
- 本節は「例外運用（再編集の可能性が高い回）」のための手順。投稿後は原則ドラフト削除とする。

### 8.1 退避のルール
- 必ずCapCutを完全終了してからパックする（編集中パック禁止）
- 1案件=1アーカイブ（復帰しやすくする）
- 共有に置いた後に hash を残す（転送/破損検知）

### 8.2 コマンド例（tgz）
（`<SHARE_ROOT>` は実マウント先に置換）

```bash
set -euo pipefail

EP=\"CH02-034\"
CH=\"CH02\"
NNN=\"034\"
RUN=\"CH02-034_capcut_v1\"
TAG=\"after_edit\"
TS=\"$(date +%Y%m%d)\"

DRAFT_ROOT=\"$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft\"
DRAFT_DIR=\"$DRAFT_ROOT/${RUN}_draft\"   # 実際のディレクトリ名に合わせる

DEST_DIR=\"<SHARE_ROOT>/archive/capcut_drafts/$CH/$NNN\"
PACK=\"$DEST_DIR/capcut_draft_pack__${EP}__${RUN}__${TS}__${TAG}.tgz\"

mkdir -p \"$DEST_DIR\"
tar -C \"$DRAFT_ROOT\" -czf \"${PACK}.tmp\" \"$(basename \"$DRAFT_DIR\")\"
mv \"${PACK}.tmp\" \"$PACK\"
shasum -a 256 \"$PACK\" > \"${PACK}.sha256\"
```

### 8.3 退避後の容量回収（安全手順）
1) `*.tgz` が共有に存在し、`tar -tzf` で一覧が出ることを確認  
2) 必要なら `sha256` も検証（`shasum -a 256 -c`）  
3) OKなら Hot側の元ドラフトを削除（or 一旦 `_trash/<date>/` へ移動して様子見）

---

## 9) 復帰（再編集）手順

1) 共有から対象 `capcut_draft_pack__...tgz` をHotへコピー（ローカル/外付け）
2) 既存の同名ドラフトがあれば、先に退避（`.bak_<date>` へリネーム）
3) `tar -xzf` で `com.lveditor.draft/` 配下へ展開
4) CapCutで開いて再編集 → 再書き出し → 必要なら再度Draft Pack化

---

## 10) “URLで見える” と “編集が速い” を同時に満たす条件

- URL側（Acer UI/Files）が見るのは **共有の `ytm_workspaces/` と `capcut_exports/` と `archive/`**。
- 編集（CapCut）が見るのは **Mac Hot**（ローカル/外付け）。
- よって「他端末はURLで見る」「編集はMacで速い」が同時に成立する。

---

## 11) よくある事故と対策

- 事故: `draft` 名にタイトルを含めて `(1)` が増殖 / 紐付けが壊れる  
  対策: `--draft-name-policy run --no-draft-name-with-title`
- 事故: 共有上のドラフトを直接編集してCapCutが重い/壊れる  
  対策: 編集はHotのみ。共有はDraft Pack/Exportsに限定。
- 事故: 背景自動化（`export_mover` / `capcut_purge_archived`）で **未投稿（Hot/Freeze）のドラフトが消える**  
  対策:
  - `--capcut-archive-mode copy` は「アーカイブ（コピー）のみ」で、削除キューに入れない（削除しない）
  - purge は queue に `allow_purge=true` が明示されたものだけ実行する（= `move` など “削除の明示” がある場合のみ）
  - 迷ったら purge 系 LaunchAgent を止める（CapCut編集を守るのが最優先）
- 事故: “共有SoT” と “Macローカルworkspaces” が二重になり、進捗が巻き戻る  
  対策: `YTM_WORKSPACE_ROOT` を共有に揃える（どうしても同期するなら一方向・手順固定）。

## 関連（SSOT）

- 共有SoTでどこでも同じUI: `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`
- 共有ストレージへL1退避: `ssot/ops/OPS_SHARED_ASSET_STORE.md`
- CapCutドラフト資産（Hot/Warm/Cold）: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`
