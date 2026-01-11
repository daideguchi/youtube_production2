# OPS_PREPRODUCTION_FRAME — 入口〜量産投入直前の参照フレーム（SSOT）

目的:
- 入口（Planning/任意入力）〜量産投入直前までの「どれが正本で、どれが拡張か」を整理し、品質の安定と再現性を上げる。
- 企画の上書き/追加/一部設定差し替えが発生しても、**判断キー + 差分ログ**で追跡できる状態にする。

前提:
- これは“完全な仕様”ではなく、現行ラインに自然接続するための **参照フレーム**。
- 詳細はリンク先SSOTを正とし、本書は「迷わないための地図」に徹する。

関連（正本）:
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 入口索引: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- 入力カタログ（必須/任意/上書きの一覧）: `ssot/ops/OPS_PREPRODUCTION_INPUTS_CATALOG.md`
- 修復導線（issue→直す場所）: `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md`
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 企画差分（Patch）: `ssot/ops/OPS_PLANNING_PATCHES.md`
- Production Pack: `ssot/ops/OPS_PRODUCTION_PACK.md`
- 入力契約（タイトル=正）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 整合チェック: `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- ログ配置: `ssot/ops/OPS_LOGGING_MAP.md`
- lock運用: `AGENTS.md`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`

---

## 0) まず固定する：SoT（正本）と“拡張”の線引き

このラインでは「入力が無くても破綻しない」ことを最優先にし、追加入力は **拡張（品質が上がる）**として扱う。

| 区分 | 例 | 位置づけ | 正本（SoT） |
| --- | --- | --- | --- |
| 必須入力 | `タイトル` | 無いと止める（投入不可） | `workspaces/planning/channels/CHxx.csv` |
| 拡張入力 | persona / ベンチマーク / サムネ参照 | 無くても進む（あると精度↑） | 各SoT（下記参照） |
| 設定（テンプレ） | script_prompt / video preset | 再現性の核（スナップショットで固定） | `packages/**` / `workspaces/**` |

---

## 1) “入口〜量産投入前”で参照する主なSoT

### 1.1 Planning（企画/進捗）
- SoT: `workspaces/planning/channels/CHxx.csv`
- 判断キー（最小）: `channel` + `video (NNN)`（episode単位）
- 安全弁: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`（タイトル=正、矛盾するテーマヒントは捨てる）

### 1.2 企画の上書き/追加/部分更新（Planning Patch）
- Patch置き場（tracked）: `workspaces/planning/patches/*.yaml`
- 適用ツール: `python3 scripts/ops/planning_apply_patch.py --patch ... [--apply]`
- 差分ログ（生成物）: `workspaces/logs/regression/planning_patch/`

### 1.3 Production Pack（量産投入前スナップショット）
- 生成ツール: `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`
- 出力: `workspaces/logs/regression/production_pack/`（pack + diff）
- 役割: SoTを置き換えずに「この時点の入力/設定/ゲート結果」を固定する

### 1.4 チャンネル設定（ベンチマーク/説明文/タグ等）
- SoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json`
  - ベンチマークの正本: `benchmarks`（詳細: `ssot/ops/OPS_CHANNEL_BENCHMARKS.md`）

### 1.5 テンプレ（台本/動画/サムネ）
- 台本プロンプト（運用）: `packages/script_pipeline/channels/CHxx-*/script_prompt.txt`
- 台本テンプレ/ステージ: `packages/script_pipeline/templates.yaml`, `packages/script_pipeline/stages.yaml`
- 動画preset（チャンネル別）: `packages/video_pipeline/config/channel_presets.json`
- サムネSoT: `workspaces/thumbnails/projects.json` と `workspaces/thumbnails/assets/{CH}/{NNN}/`

---

## 2) 変更の入れ方（迷わないための運用ルール）

### 2.1 企画（Planning）を変えるときの基本
- CSVを直接編集してもよいが、**追跡したい変更はPatchで残す**（差分ログが目的）。
- 下流（台本/音声/動画）へ反映する場合は、原則「reset → 再生成」で揃える（混在が事故源）。

### 2.2 Patch適用の安全手順（推奨）
1) lock確認/取得（並列衝突防止）
2) dry-run（差分ログ生成）→ 内容確認
3) apply（CSVへ反映）→ 自動で lint/証跡が残る

---

## 3) 差分ログ思想（何が変わったかを必ず残す）

最低限、次の2系統で「変化」を追えるようにする:
- Planning Patchログ: `workspaces/logs/regression/planning_patch/`
- Production Pack diff: `workspaces/logs/regression/production_pack/`

※ 1つの巨大な正本ログに寄せず、「工程に近い場所へ機械ログを出す」方針で散らかりを防ぐ（ログ配置の正本は `ssot/ops/OPS_LOGGING_MAP.md`）。

---

## 4) QAゲート（最小の合否/警告）

本ラインの“最小ゲート”は Production Pack を正とする（詳細: `ssot/ops/OPS_PRODUCTION_PACK.md`）。

運用の考え方:
- Fail（投入禁止）: Planning行が無い/タイトル空/チャンネル定義破損 など
- Warn（投入はできるがリスク高）: persona欠落/Planning lint warning/公開済みロック疑い など
- Pass: 上記に該当しない

---

## 5) 段階導入（現行ラインを壊さない）

導入順（推奨 / 最小→拡張）:

Phase 0（今すぐ / 破壊なし）:
- `preproduction_audit` と `planning_lint` を “眺めるだけ” で回し、抜け漏れを可視化する（現行ラインは変えない）。

Phase 1（差分運用の習慣）:
- “追跡したい企画変更” は Patch に寄せる（dry-run→apply→差分ログ）。
- 変更後に `production_pack` を生成し、diff で「何が変わったか」を固定する。

Phase 2（投入判断の標準化）:
- 量産投入前に `production_pack` を儀式化し、`pass/warn/fail` を判断キーにする。
- `fix_hints` / `OPS_PREPRODUCTION_REMEDIATION` を見て **必要最小の修正**で収束させる（過剰な仕様化はしない）。

Phase 3（最終: Pack駆動）:
- UI で「Pack生成→Gate表示→修復導線」を統合する。
- runner/job が Pack を入力として参照解決を固定し、再現性を最終固定する。

詳細は `ssot/ops/OPS_PRODUCTION_PACK.md` の「段階導入プラン」を正とする（実装の位置づけはこちらに集約）。

---

## 6) 機械監査（“抜け漏れ”を決定論で見える化）

「完全に漏れなく」を人間の目だけで担保すると事故るため、**監査コマンドで不足/矛盾を列挙**し、修正と差分ログを回せる状態にする。

入口（推奨）:
- `python3 scripts/ops/preproduction_audit.py --all --write-latest`
- 終了コード（自動化向け）: `0=pass`, `1=warn`, `2=fail`

出力（ログ）:
- `workspaces/logs/regression/preproduction_audit/`

判定の考え方:
- `error`: SoT不足や壊れ（投入前に必ず直す）
- `warning`: 任意入力の欠落や品質リスク（投入はできるが、後段で詰まりやすい）
- `ok`: 問題なし

運用メモ:
- JSON は `channels[].gate`（チャンネル別の pass/warn/fail）を持つ。`gate.issues[].channel` で横断集計もできる（修正対象の切り分けに使う）。
- `gate.issues[*].fix_hints`（任意）がある場合は、最短の修復導線。体系は `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md` を正とする。

※ Audit は「仕様を固定する」ためではなく、「現行ラインの都合に合わせた欠落検知」を目的とする。
