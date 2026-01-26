# OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL — 保存先/配線/決裁（CapCut快適運用）

目的:
- **保存先カオス**（Mac/Lenovo外付け/Acer/URL）が起きないように、「どこに何があるか」を **1枚**に固定する。
- CapCutの編集体験（Macがメイン）を落とさずに、Lenovoでも「素材が無いから挿入できない」を防ぐ。
- “進捗はmainブランチ” として **SSOT（正本）を一本化**し、どの端末からUIを開いても同じ進捗を見れるようにする。

SSOTリンク:
- CapCut編集（Hot/Workset/Exports/Archive）: `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- CapCutストレージ戦略（Hot/Warm/Cold）: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`
- Hot（未投稿）/Freeze（未投稿だが当面触らない）の確定ルール: `ssot/ops/OPS_HOTSET_POLICY.md`
- ロールアウト手順（移行の正本）: `ssot/plans/PLAN_CAPCUT_HOT_VAULT_ROLLOUT.md`
- 共有Workspaces（UIで資産を見る）: `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`
- 共有ストレージ（L1 bytes store）: `ssot/ops/OPS_SHARED_ASSET_STORE.md`
- env定義: `ssot/ops/OPS_ENV_VARS.md`

非目的:
- Linux(Acer)でCapCut編集を成立させる（GUI前提のため）。
- CapCutドラフトをネットワーク共有上に置いて“直編集”する（体感/事故率が悪化しやすい）。

---

## 1) 物理/配線（現実の前提）

登場人物:
- **Mac**: 主要編集機（CapCut/生成/投稿）
- **Lenovo**: **外付けストレージが刺さっている**ホスト（共有ストレージの実体）
- **Acer**: **ゲートウェイ**（`/ui` `/api` `/files` をTailscale公開する。ユーザーは触らない前提）

配線（論理）:
1. Lenovo外付け（物理ストレージ）が **共有ストレージの実体（正本）**
2. Lenovoがそれを共有（例: SMB）
3. Mac/Acer が共有を **マウント**
4. Acer がマウント内容を `/files` として Web公開し、同じSoTを読むUI/APIを常駐させる

重要:
- `/files` は「AcerがWeb公開しているファイル閲覧」。実体がAcer内蔵とは限らない（マウントでOK）。

なぜ Acer が関係するか（結論）:
- `https://<host>.ts.net/...` は **その host が `tailscale serve` で公開しているURL**。常時起動の **Acer（Ubuntu）** をゲートウェイにする。
- Lenovo は **Windows + 外付けが刺さっている**ので、役割は「保管庫（共有ストレージの実体）」に寄せる（UI常駐をWindowsに寄せない）。

現状の公開URL（正）:
- UI: `https://acer-dai.tail8c523e.ts.net/ui/`
- API: `https://acer-dai.tail8c523e.ts.net/api/healthz`
- Files: `https://acer-dai.tail8c523e.ts.net/files/`

緊急フォールバック（Acer不調でも止めない）:
- UI（Mac / fallback）: `https://deguchimacbook-pro.tail8c523e.ts.net:8444/`
- Files（Mac / magic_files）: `https://deguchimacbook-pro.tail8c523e.ts.net/files/`

状態の書き方（誤判定を減らす）:
- 「Acerが死んでる」禁止。必ず **どこから見て何がダメか** を書く（例: “tailnetから `/ui` がtimeout” / “LAN pingはOKだがSSHがbannerを返さない”）。
- ping/ポートopenだけで生死を断定しない（SSH banner / SMB応答 / HTTPヘルスも見る）。

フォールバック（考え方）:
- Acer が落ちた場合は、**同じ Vault（`ytm_workspaces/`）を参照する別ホスト**で UI Hub を常駐させれば復旧できる（ただし URL は host が変わる）。
  - 既に Mac に fallback UI（:8444）を用意している（上記）。
- Lenovo（Windows）をゲートウェイ化する場合は、WSL等で Linux スタックを動かす設計が必要（本書の主線ではない）。

---

## 2) 論理ルート（“どのディレクトリが何か”）

### 2.1 マウント先（決定）

保管庫（共有ストレージ）の実体は **Lenovo外付け**。  
各ホストは「同じ共有」をそれぞれのパスへマウントし、以降は env で固定する。

- Lenovo（物理ストレージ）: SMB共有 `doraemon`（Tailscale IP: `100.127.188.120`）
- Mac（編集機）: `/Users/dd/mounts/lenovo_share_real` にマウント（`YTM_SHARED_STORAGE_ROOT=/Users/dd/mounts/lenovo_share_real`）
- Acer（UIゲートウェイ）: `/srv/workspace/doraemon/workspace/lenovo_share` にマウント（`YTM_SHARED_STORAGE_ROOT=/srv/workspace/doraemon/workspace/lenovo_share`）

補足（Macからの自動実行 / 接続口）:
- **Lenovo（Windows）**: `ssh lenovo-doraemon`（cmd/PowerShell実行の入口）
- **Acer（Ubuntu）**: `ssh acer`（Tailscale）/ `ssh acer-lan`（LAN）

重要（Lenovo側: 実体は外付けDへ / パスは固定）:
- **共有の入口（URL/他端末が参照するパス）は `C:\\doraemon_share` で固定**する（Acerのマウントもここを参照）。
- 実体（容量/速度）は **Dドライブ外付け**へ逃がす。
  - `C:\\doraemon_share\\ytm_workspaces` は `D:\\doraemon_ext\\ytm_workspaces` への **Junction**。
  - `C:\\doraemon_share\\asset_vault` は `D:\\doraemon_ext\\asset_vault` への **Junction**。
  - これにより **参照パスは変えずに**、実体だけ外付けへ移動できる。

以降、実パスは未確定でもOK。**ディレクトリ名と役割**だけ固定する。

- `LENOVO_SHARE_ROOT`（共有ストレージroot）
  - 実体: Lenovo外付け
  - Mac/Acerでマウント先は違ってよい（翻訳表で吸収）
- `WORKSPACES_ROOT`（Factory SoT）
  - `LENOVO_SHARE_ROOT/ytm_workspaces/`
  - 台本/サムネ/進捗/音声/動画run 等、Factoryが参照する “状態”
- `ASSET_VAULT_ROOT`（共有素材庫）
  - `LENOVO_SHARE_ROOT/asset_vault/`
  - BGM/SE/画像/フォント/テンプレなど “再利用素材”
- `CAPCUT_EXPORTS_ROOT`（mp4受け渡し）
  - `LENOVO_SHARE_ROOT/capcut_exports/CHxx/NNN/`
- `CAPCUT_DRAFT_ARCHIVE_ROOT`（ドラフト退避: 例外のみ）
  - `LENOVO_SHARE_ROOT/archive/capcut_drafts/CHxx/NNN/`
- `UPLOADS_ROOT`（L1 bytes store / 監査・退避）
  - `LENOVO_SHARE_ROOT/uploads/<namespace>/...`（`YTM_SHARED_STORAGE_ROOT` + `YTM_SHARED_STORAGE_NAMESPACE`）

---

## 3) env（コードが見る “切替点”）

まず共有ストレージrootだけは全員で揃える:
- `YTM_SHARED_STORAGE_ROOT=LENOVO_SHARE_ROOT`

事故防止（重要）:
- `LENOVO_SHARE_ROOT`（例: `/Users/dd/mounts/lenovo_share_real`）は **Lenovo外付け共有のマウントポイント専用**。Acerの `workspace` など別ホスト配下へ **symlink/付け替え禁止**（Acerが落ちるとSSOTも落ちる）。
- 共有が未マウントの時は、ミラー/同期は **ローカルに誤書き込みせずSKIP** する（“動いてるつもり”で進捗がズレるのを防ぐ）。

推奨（CapCut快適運用 = Mac Hot + 保管庫ミラー）:
- **Mac（編集機）**
  - `YTM_WORKSPACE_ROOT=<MAC_LOCAL_WORKSPACES>`（Hot: いつもの `<repo>/workspaces` 相当でもOK）
  - `YTM_VAULT_WORKSPACES_ROOT=LENOVO_SHARE_ROOT/ytm_workspaces`（保管庫ミラーの宛先）
  - `YTM_PLANNING_ROOT=LENOVO_SHARE_ROOT/ytm_workspaces/planning`（進捗SSOTは1箇所 = mainブランチ）
  - `YTM_ASSET_VAULT_ROOT=LENOVO_SHARE_ROOT/asset_vault`（共有素材庫）
  - `YTM_CAPCUT_WORKSET_ROOT=<MAC_LOCAL>/capcut_worksets`（Hot。共有は禁止）
- **Acer（UIゲートウェイ）**
  - `YTM_WORKSPACE_ROOT=LENOVO_SHARE_ROOT/ytm_workspaces`（UI/API が見る正本は保管庫）
  - `YTM_PLANNING_ROOT=LENOVO_SHARE_ROOT/ytm_workspaces/planning`（同じSSOTを読む）

注意:
- Mac側の `YTM_WORKSPACE_ROOT` を保管庫（ネットワークマウント）にすると、編集/生成が遅くなりやすい。HotはMacローカルを推奨。

点検:
- `./ops storage doctor`
- Vault(共有)のパス整合（Acerでも壊れない）:
  - dry-run: `python3 scripts/ops/vault_workspaces_doctor.py`
  - 適用: `python3 scripts/ops/vault_workspaces_doctor.py --run`
- 欠損サムネの見える化（ローカルUIで broken になる時）:
  - **禁止**: placeholder生成（`thumbnails_placeholders.py --run`）は運用/契約違反（“存在するはずの実体”を隠して事故る）。
  - 正攻法（=パスを整える）:
    1) **正本（Vault）へ実体を同期**して 404 を止める（delete無し）。
       - 例（Mac→Lenovo; ssh入口）:
         - `scp workspaces/thumbnails/projects.json lenovo-doraemon:'C:/doraemon_share/ytm_workspaces/thumbnails/projects.json'`
         - `scp -r workspaces/thumbnails/assets lenovo-doraemon:'C:/doraemon_share/ytm_workspaces/thumbnails/'`
    2) 同期後に 404 が残る場合は、「projects.json が指すパス」と「実体の所在（archive等）」の差分を調査し、**実体を正規パスへ集約**する。
  - 追加の止血（任意・既定OFF）:
    - `YTM_THUMBNAILS_MISSING_PLACEHOLDER=1` の時のみ、欠損 `00_thumb*.png` を **placeholder画像(200)** で返す（`X-YTM-Placeholder: 1`）

強制ルール（破綻防止）:
- Vault（`ytm_workspaces/**`）内の symlink は **共有内の相対 symlink のみ**（ホスト固有の絶対パスは禁止）

---

## 3.1) ミラー契約（曖昧さ禁止: “作成=コピー / 削除=削除同期”）

確定ルール:
- Macローカルの `workspaces/**` に **新規/更新** があれば、保管庫の `ytm_workspaces/**` へ **同一相対パスでコピー**される。
- Macローカルで **削除** されたら、保管庫側も **同一相対パスを削除**する（= 1:1ミラー）。
- planning（進捗）は “mainブランチ” なので、既定ではミラー対象から除外する（上書き事故防止）。
  - 進捗は共有SSOT（`WORKSPACES_ROOT/planning`）を直接参照/更新する（`YTM_PLANNING_ROOT`）。
- 指示: **台本（scripts）とサムネ（thumbnails/assets）と生成画像（video/runs）は消さない**（cleanupでも削除しない）。

実装（コードで完結）:
- 初回（安全策: delete-syncの事故防止）
  - 保管庫のsentinel作成: `./ops mirror workspaces -- --bootstrap-dest --ensure-dirs`
  - 1回同期（dry-run→本番）:
    - dry-run: `./ops mirror workspaces`
    - 本番: `./ops mirror workspaces -- --run`
- 常駐（Mac launchd）:
  - `./ops mirror install-workspaces-launchd -- --interval-sec 600`

Macの作業を止めない（強制方針）:
- CapCut編集/生成は Hot（Macローカル）で完結させ、共有（SMB）を直参照しない（Worksetへコピーして使う）。
- Hot→Vault ミラーはバックグラウンド。共有が不調でも固まらないように “低優先度 + fail-fast” を既定にする。
  - `YTM_MIRROR_NICE=10`（rsyncを低優先度で実行。0で無効）
  - `YTM_RSYNC_TIMEOUT_SEC=60`（hang回避: I/O stall で fail-fast）
  - `YTM_RSYNC_BWLIMIT_KBPS=<KB/s>`（任意: 帯域を絞る）
  - `YTM_RSYNC_WHOLE_FILE=1`（既定ON: CPU節約。0で無効）

## 4) データ種別ごとの “正本/削除” ポリシー（運用の要）

| 種別 | 正本 | 編集中の所在 | 投稿後 | 備考 |
|---|---|---|---|---|
| 進捗（planning CSV） | `WORKSPACES_ROOT/planning/` | 共有SSOT（必須） | 残す | “mainブランチ” |
| 台本（assembled*.md） | `WORKSPACES_ROOT/scripts/` | 共有 | 残す | UIで見れることが最重要 |
| サムネ資産 | `WORKSPACES_ROOT/thumbnails/assets/` | 共有 | **残す（消さない）** | 指示: サムネは消さない |
| 音声WAV/SRT（final） | `WORKSPACES_ROOT/audio/final/` | 共有 + Worksetへコピー | **要決裁** | Lenovoで差し替えが起きるので “消しすぎ注意” |
| 生成画像（video/runs） | `WORKSPACES_ROOT/video/runs/<run_id>/images/` | Worksetへコピー | **残す（保管庫に残す）** | せっかく生成したものは保管庫に残す（Macローカルは容量のため削除OK） |
| CapCutドラフト | MacのCapCutローカル | Hotのみ | 原則削除 | 例外のみ Draft Pack退避 |
| CapCut書き出しmp4 | `CAPCUT_EXPORTS_ROOT` | 共有 | 期限付き削除（別SSOT） | “受け渡し” が主 |

---

## 5) 実務フロー（最短で回る形）

### 5.1 Macで編集（基本）
1. SoT（`WORKSPACES_ROOT`）へ必要な素材がある状態を維持（WAV/SRT/画像など）
2. 編集前に Workset を作る（ローカルに必要分だけコピー）
   - `./ops video capcut-workset -- --channel CHxx --video NNN --run`
3. CapCutは Workset から素材を取り込んで編集（ネットワーク参照を混ぜない）
4. mp4を書き出し → `CAPCUT_EXPORTS_ROOT` へ置く

### 5.2 Lenovoで“ちょい編集/差し替え”が必要な時
- 共有ストレージがLenovoローカルにある前提なので、`WORKSPACES_ROOT/audio/final/**` や `ASSET_VAULT_ROOT/**` から挿入できる。
- 「Macにしか無いから挿入できない」を潰す狙いはここ。

### 5.3 投稿後（クリーンアップ）
- 投稿済みを進捗（planning）に反映（UI）
- cleanup（SSOT化済み）:
  - `./ops archive published --channel CHxx --video NNN --video-runs --capcut-drafts --delete --run --yes`
  - WAVも消すなら: `--audio`（要決裁）

---

## 6) 決裁欄（オーナー承認）

決裁したい論点（チェックして確定）:
- [ ] D1: `WORKSPACES_ROOT` は Lenovo外付け共有を正本にする（二重SoTを作らない）
- [ ] D2: “進捗はmainブランチ” とし、planningは共有SSOT（`YTM_WORKSPACE_ROOT` or `YTM_PLANNING_ROOT`）に固定する
- [ ] D3: CapCut編集はHotローカル（内蔵SSD）で行い、共有上のドラフト直編集はしない
- [ ] D4: 投稿後のCapCutドラフトは原則削除（例外のみ Draft Pack退避）
- [ ] D5: 投稿後に削除する領域:
  - [ ] D5-a: video/runs（画像・中間）を削除する（※現状は「保管庫に残す」推奨）
  - [ ] D5-b: audio/final（WAV/SRT）も削除する（再生成前提）
  - [ ] D5-c: thumbnails/assets は削除しない（固定）
- [ ] D6: `asset_vault` を “追加素材の入口” として固定する（Macローカル専用素材を作らない）

決裁メモ:
- 承認者:
- 承認日:
- 例外（残す回/残す素材）:
