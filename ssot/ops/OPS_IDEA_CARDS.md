# OPS_IDEA_CARDS — 企画カード運用（追加/整理/評価/配置）SSOT

目的:
- 「企画を増やす＝毎回同じ手順で“整理→評価→配置”まで終わる」状態を作り、量産の安定性を上げる。
- 企画を “文章の塊” ではなく **必須フィールド付きのカード（1件=1レコード）** として扱い、削除/移動/選別を **操作** にする。

関連:
- Planning SoT（本番の企画/進捗）: `workspaces/planning/channels/CHxx.csv`
- Planning運用: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 入口索引: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- ロック運用: `ssot/ops/OPS_AGENT_PLAYBOOK.md`

---

## 0. SoT（正本）と保存形式

### 0.1 SoT（企画カード在庫）
- **SoT**: `workspaces/planning/ideas/CHxx.jsonl`
  - 1行=1カード（JSON object）
  - “現時点のカード状態” を保存する（履歴はカード内 `history` に残す）

### 0.2 アーカイブ（物理退避）
- **Archive**: `workspaces/planning/ideas/_archive/*.jsonl`
  - `KILL` にしたカードを一定期間後に物理退避する（削除はしない）

---

## 1. 企画カードの最小スキーマ（v1）

必須フィールド（最小）:
- `working_title`
- `hook`（1〜2文）
- `promise`（視聴価値）
- `angle`（切り口）

推奨スキーマ（最小）:
```json
{
  "idea_id": "CH01-IDEA-20251231-0007",
  "channel": "CH01",
  "series": "仏教×人間関係",
  "theme": "距離感/言葉/執着",
  "working_title": "優しい言葉が人を傷つける理由",
  "hook": "あなたの何気ない一言が、刃のように残ることがあります。",
  "promise": "最後まで聞くと『言葉で傷つけない判断軸』が手に入ります。",
  "angle": "口業/業/相手の受け取り方",
  "length_target": "6000-10000",
  "format": "3章/例回し/王道",
  "status": "INBOX",
  "score": { "novelty": 0, "retention": 0, "feasibility": 0, "brand_fit": 0, "total": 0 },
  "tags": ["寝落ち", "朗読", "ブッダ"],
  "source_memo": "視聴者コメント起点",
  "history": []
}
```

---

## 2. ステータス（固定）

ステータスはこの8つに固定する:
- `INBOX`（入っただけ）
- `BACKLOG`（作る候補）
- `BRUSHUP`（改善待ち）
- `READY`（制作可）
- `PRODUCING`（制作中）
- `DONE`（公開済み）
- `ICEBOX`（保管）
- `KILL`（廃棄扱い：ただし履歴として残す）

ルール:
- 移動は必ず理由を1行残す（`history` に記録）。
- `DONE` は原則ロック（編集は別レコード運用推奨）。

---

## 3. 運用フロー（固定）

### 3.1 追加（Capture → Normalize → De-dup → Triage → Score → Slot）
1) Capture: `INBOX` に追加（情報が薄くてもOK）
2) Normalize: 必須4点（title/hook/promise/angle）だけ埋める
3) De-dup:
   - 完全同一は **重複として KILL**（削除はしない）
   - 近いものは MERGE候補としてログに出す（手動で整理）
4) Triage: `ICEBOX/BACKLOG/BRUSHUP/KILL` に必ず確定
5) Score: 4軸（0〜5点、合計20）
   - `total>=14` → READY候補
   - `total 10〜13` → BRUSHUP
   - `total<=9` → ICEBOX（またはKILL）
6) Slot:
   - READY候補は制作キューへ（= `READY` へ移動）
   - それ以外は所定の棚へ

### 3.2 ブラッシュアップ（順番固定）
1. Hook強化
2. Promise明確化
3. Angle尖らせ
4. 構成割り当て（`format`）

### 3.3 選別（SELECT：スコア＋偏り制御）
- total が高い順で選ぶ
- ただし偏り制御:
  - 同テーマ連続は最大2本まで
  - 同フォーマット連続は最大2本まで
  - “重い話” が続いたら “軽め” を挟む（題材だけ軽く、トーンは維持）
- 出力は「次のN本」だけ確定して `READY` へ移動し、それ以外は `BACKLOG` に残す

### 3.4 削除（DELETEではなく2段階）
1) `KILL` へ移動（論理削除、理由必須）
2) アーカイブ（物理退避）

---

## 4. CLI（実行入口）

入口:
- `python3 scripts/ops/idea.py --help`

代表コマンド:
```bash
# 追加（INBOX）
python3 scripts/ops/idea.py add --channel CH01 --working-title "..." --hook "..." --promise "..." --angle "..."

# 一覧
python3 scripts/ops/idea.py list --channel CH01 --status INBOX

# 仕分け（理由必須）
python3 scripts/ops/idea.py triage --channel CH01 --idea-id CH01-IDEA-... --to BACKLOG --reason "素材が良い"

# 採点（任意で auto-status）
python3 scripts/ops/idea.py score --channel CH01 --idea-id CH01-IDEA-... --novelty 4 --retention 3 --feasibility 4 --brand-fit 4 --auto-status

# 重複チェック（reportのみ）
python3 scripts/ops/idea.py dedup --channel CH01

# 選別（report→OKなら apply）
python3 scripts/ops/idea.py select --channel CH01 --n 10
python3 scripts/ops/idea.py select --channel CH01 --n 10 --apply

# Planningへ投入（READY→Planning CSVへ追加行。patch生成→lint→任意でapply）
python3 scripts/ops/idea.py slot --channel CH01 --n 10
python3 scripts/ops/idea.py slot --channel CH01 --n 10 --apply

# KILLの物理退避（report→OKなら apply）
python3 scripts/ops/idea.py archive --channel CH01 --older-than-days 30
python3 scripts/ops/idea.py archive --channel CH01 --older-than-days 30 --apply
```

`slot` の挙動:
- `workspaces/planning/patches/` に `add_row` patch を生成する（差分ログは `planning_apply_patch` 側に残る）
- `--apply` で Planning CSV（`workspaces/planning/channels/CHxx.csv`）へ反映する
- 反映後、対象カードは `status=PRODUCING` に移動し、`planning_ref` に `script_id/patch_path` を記録する

ログ:
- `workspaces/logs/regression/idea_manager/<op>/...` に report を出す（SoTは `workspaces/planning/ideas/**`）。
