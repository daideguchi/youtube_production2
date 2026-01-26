# OPS_HOTSET_POLICY — Hot(未投稿) / Freeze(未投稿だが当面触らない) の確定ルール

目的:
- 「Hot資産はMacに実体がある」を **事故らず運用**するために、Hot(未投稿)とFreeze(未投稿だが当面触らない)を明示管理する。
- 過去の事故（未投稿のデータが削除された）を二度と起こさないため、**未投稿の削除/外部依存**を禁止する。

前提（不変条件）:
- Hot = **未投稿（未公開）**。
- Freeze = 未投稿だが「当面触らない」扱いに **明示**したもの（＝Hotから外す）。  
  ※ Freeze は “削除許可” ではない。未投稿の削除は常に禁止。
- Published（投稿済み）= Planning の `進捗` が `投稿済み/公開済み`（または `status.json: metadata.published_lock=true`）。

---

## 1) 定義（固定）

### 1.1 Hot（未投稿 / Active）
- 条件:
  - Planning 行が `投稿済み/公開済み` ではない
  - かつ Freeze リストに入っていない
- 要求:
  - **Hotに必要な資産の実体はMacローカル**（外部のみはNG）
  - 外部（Lenovo/NAS）が落ちても作業が止まらない（オフライン継続）

### 1.2 Freeze（未投稿 / Inactive）
- 条件:
  - 未投稿だが「当面触らない」と **人間が明示**したもの
- 重要:
  - Freeze は “Hotから外す” だけで、**削除/破壊の許可ではない**
  - 外部が不安定な間は「外部にしか無い」状態を作らない（原則）

### 1.3 Published（投稿済み）
- 条件（どちらか）:
  - Planning 行が `投稿済み/公開済み`
  - または `status.json: metadata.published_lock=true`
- ここから先だけ、容量回収（archive/delete）の検討が可能（SSOT参照）
  - `hotset.py list` の Published 判定も上記に従う（進捗 + `published_lock`）。

---

## 2) Freeze リスト（正本）

保存先（Planning SoT 配下に置く）:
- `planning_root()/hotset_freeze.json`
  - 例: `workspaces/planning/hotset_freeze.json`（`YTM_PLANNING_ROOT` を使う運用ではその配下）

スキーマ（v1; 変更はSSOT優先で管理）:
```json
{
  "schema": "ytm.hotset_freeze.v1",
  "updated_at": "2026-01-26T03:00:00Z",
  "items": [
    {
      "channel": "CH04",
      "video": "031",
      "reason": "当面触らない（企画保留）",
      "created_at": "2026-01-26T03:00:00Z",
      "created_by": "dd"
    }
  ]
}
```

固定ルール:
- Freeze 追加/削除は **人間の明示**（自動推論で入れない）
- reason は必須（後で必ず迷子になるため）
- これは “作業優先度の分類” であって、削除/移動の根拠にしない

---

## 3) 入口（固定）

ホットセットの一覧/Freeze管理:
- `python3 scripts/ops/hotset.py --help`
  - 一覧: `python3 scripts/ops/hotset.py list --channel CHxx`
  - Freeze追加（明示）: `python3 scripts/ops/hotset.py freeze-add --channel CHxx --video NNN --reason \"...\"`

---

## 4) 禁止（事故防止 / 強制）

- 未投稿（Hot/Freeze）の成果物を削除しない（自動/手動ともに禁止）
- 未投稿を外部に依存させない（外部のみの状態を作らない）
- 推論でFreezeを決めない（必ず人間の明示）

---

## 5) 関連SSOT

- `ssot/plans/PLAN_CAPCUT_HOT_VAULT_ROLLOUT.md`（移行フェーズの進捗/検証ログ）
- `ssot/ops/OPS_ARCHIVE_PUBLISHED_EPISODES.md`（投稿済みの容量回収）
- `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`（CapCut: Hot/Warm/Cold）
