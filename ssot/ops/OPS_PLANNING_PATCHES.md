# OPS_PLANNING_PATCHES — 企画の上書き/追加/部分更新（Planning Patch）運用SSOT

目的:
- 企画の上書き/追加/一部設定差し替えを「行き当たりばったりのCSV手編集」にせず、**判断キー + 差分ログ** で運用できる形にする。
- マルチエージェント運用でも衝突しにくい（= 何が変わったかを追跡できる）更新手段を提供する。

関連:
- Planning SoT: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 入力契約（タイトル=正）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 整合チェック: `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- ロック運用: `ssot/ops/OPS_AGENT_PLAYBOOK.md` / `AGENTS.md`
- Production Pack: `ssot/ops/OPS_PRODUCTION_PACK.md`

---

## 0. 基本方針（壊さないためのルール）

- Planning の正本（SoT）は `workspaces/planning/channels/CHxx.csv` のまま。
- Patch は「変更の意図」と「差分」を残すための運用レイヤ。
- patch 適用は **必ず lock を確認/取得** してから行う（並列衝突防止）。

---

## 1. 判断キー（どの単位で差し替えるか）

最小の運用単位（推奨）:
- **episode 単位**: `channel` + `video (NNN)`

将来の拡張（必要になってから）:
- series 単位: `series_id` や `企画ID` 等の列をキーにする
- template 単位: `template_id` 等で一括差し替え

まずは episode 単位のみを確定運用とし、複雑化（範囲指定/条件指定）は段階導入する。

---

## 2. Patch の保存場所（迷わないための正本）

- Patch ファイル（tracked）: `workspaces/planning/patches/*.yaml`
- 差分ログ（生成物）: `workspaces/logs/regression/planning_patch/`

※ Patch は小さく、差分ログは監査用（大量でも良い）。

---

## 3. Patch ファイル形式（最小スキーマ）

### 3.1 `set`（既存行の上書き/部分更新）

例（episode 単位の `set`）:

```yaml
schema: ytm.planning_patch.v1
patch_id: CH02-024__retitle_20251226
target:
  channel: CH02
  video: "024"
apply:
  set:
    タイトル: "【老後の不安】友人関係が壊れる本当の理由"
notes: |
  企画上書き。タイトルだけ差し替え。
```

### 3.2 `add_row`（新規行の追加）

例（episode を増やす / 新規行を追加）:

```yaml
schema: ytm.planning_patch.v1
patch_id: CH02-024__add_20251227
target:
  channel: CH02
  video: "024"
apply:
  add_row:
    タイトル: "【老後の不安】友人関係が壊れる本当の理由"
    企画意図: "..."
    ターゲット層: "..."
    具体的な内容（話の構成案）: "..."
notes: |
  新規エピソードを追加する。
```

挙動（CLI: `planning_apply_patch.py`）:
- 既に該当行が存在する場合は **エラー**（重複追加防止）
- CSVに `No.` / `チャンネル` / `動画番号` / `動画ID` / `台本番号` の列がある場合は、未指定なら **自動補完** する
- 未知列を追加したい場合は `--allow-new-columns` が必要（ヘッダを汚染しないため）

### 3.3 将来の拡張（必要になってから）
- `unset`（列を空にする）
- `append_tags` 等（列の追記ルール）

---

## 4. 適用と差分ログ（運用）

推奨フロー:
1) 対象 CSV の lock を確認し、自分の作業範囲に lock を置く
2) Patch を適用（dry-run → run）
3) 適用後に Planning lint を実行し、危険な汚染/欠落が無いことを確認
4) 差分ログ（before/after + 要約）を `workspaces/logs/regression/planning_patch/` に残す

CLI:
- dry-run: `python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/<PATCH>.yaml`
- apply: `python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/<PATCH>.yaml --apply`

※ CSV を直接編集して上書きする場合でも、Patch で差分を残してから行う（追跡可能性のため）。

---

## 5. “上書き/追加/部分更新” の意味（運用で迷わない）

- **上書き**: 既存行の一部の列を差し替える（例: `タイトル` の更新）
- **追加**: 新規行を追加する（episode を増やす）
- **部分更新**: `進捗` や補助列だけを更新する（下流 SoT の破壊を避ける）

下流（台本/音声/動画）に反映させたい変更がある場合は、原則「reset → 再生成」で揃える。
（混在は事故源。詳細は `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`）
