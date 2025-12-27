# workspaces/planning/patches — 企画上書き（Planning Patch）

ここは Planning SoT（`workspaces/planning/channels/CHxx.csv`）に対する「上書き/部分更新」を、差分ログ付きで運用するための置き場です。

正本SSOT:
- `ssot/ops/OPS_PLANNING_PATCHES.md`

## 使い方（最小）

### 1) patch を作る

#### `set`（既存行の上書き/部分更新）

例: `CH02-024__retitle.yaml`

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

#### `add_row`（新規行の追加）

例: `CH02-024__add.yaml`

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

### 2) dry-run（差分ログ生成のみ）

`python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/CH02-024__retitle.yaml`

### 3) apply（CSVへ反映 + 差分ログ）

`python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/CH02-024__retitle.yaml --apply`

ログ:
- `workspaces/logs/regression/planning_patch/`
