# DECISIONS — 意思決定（SSOTトップ）

このファイルは「**SSOT=UI（read-only）** を成立させるために、運用/実装を **どちらに寄せるか決める必要がある点**」を、**選択肢 + 推奨案 + 根拠** の形で並べた“意思決定台帳”です。

- 方針: **決まったら SSOT → 実装 の順で固定**し、`ops/OPS_GAPS_REGISTER.md` / 関連SSOTを更新していきます。
- 目的: 人間/AIの認識ズレ（=事故とコスト）をゼロにする。

---

## 0) 決め方（最短）

各 Decision は **推奨（Recommended）** を提示しています。  
もし違う運用にしたい場合は、代替案（Alternatives）から選び、理由があれば追記してください。

---

## 1) 一覧（P0→P2）

| ID | 重要度 | テーマ | Recommended | 状態 |
| --- | --- | --- | --- | --- |
| D-001 | P0 | redoフラグの正本 | **`status.json` 正本**（CSVは派生ビュー） | Proposed |
| D-002 | P0 | サイレント降格禁止（モデル/品質） | **明示モデル選択時はfallback禁止**（止めて報告） | Proposed |
| D-003 | P0 | Publish→ローカル投稿済みロック | **publisherに“任意フラグで”同期**（忘れ事故を防ぐ） | Proposed |
| D-004 | P1 | `script_validation` 品質ゲートround | **既定=3**（必要時のみ明示で増やす） | Proposed |
| D-005 | P1 | 意味整合の自動修正範囲 | **outlineのみbounded / validationは手動適用** | Proposed |
| D-006 | P2 | Video入口の一本化 | **`auto_capcut_run` 主線固定**（capcut engine stubは非推奨） | Proposed |
| D-007 | P2 | AudioのBテキスト例外 | **例外導線（CLI/明示入力）として固定** | Proposed |
| D-008 | P2 | Publish一時DLの置き場/保持 | **`workspaces/tmp/publish/` へ寄せ、成功後削除（任意保持）** | Proposed |
| D-009 | P2 | “ゾンビ候補”の扱い | **隔離→入口索引から除外→archive-first削除**（確実ゴミのみ） | Proposed |

---

## D-001（P0）redoフラグの正本はどこか（CSV vs status.json）

### Decision
- **`workspaces/scripts/{CH}/{NNN}/status.json: metadata.redo_*` を正本**とし、Planning CSV は “派生ビュー” とする。

### Recommended（推奨）
1) redo正本 = `status.json`  
2) UIの進捗ビューは **CSV行 + status.json をmerge**（現行実装に合わせる）  
3) CSV側に redo を書き戻さない（必要なら “表示用export” を別ファイルで生成）

### Rationale（根拠）
- redo は **制作状態（pipeline state）** であり、企画CSV（Planning facts）と責務が異なる。
- CSVは人間が編集しやすい一方、並列編集/列追加で **衝突・破壊が起きやすい**。
- `status.json` は下流ガード（validation/alignment/redo_audio等）と一体で、事故防止に向く。

### Alternatives（代替案）
- A) CSVを正本にする（非推奨）: 実装を大きく変え、書戻し/競合/監査の設計が必要。
- B) 双方向同期: “どちらが正か” が崩れやすく、事故ポイントが増える（非推奨）。

### Impact（影響/作業）
- `ops/OPS_CONFIRMED_PIPELINE_FLOW.md` 等のSSOT記述を `status.json` 正本に統一する。

---

## D-002（P0）サイレント降格禁止（モデル/品質）

### Decision
- **明示的にモデル/品質を指定した場合、fallback（別モデル/別tier/別provider）は禁止**。失敗したら **停止して報告**し、代替案を提示する。

### Recommended（推奨）
- LLM（`factory_common.llm_router`）/画像（`factory_common.image_client`）ともに:
  - **明示選択（env/override/call-time）= strict** とみなし、`allow_fallback=true` を明示しない限り代替を試さない。
  - 例外的に代替を許す場合は **許可の根拠をSSOTに残す**（taskごとに `allow_fallback=true`）。

### Rationale（根拠）
- 品質の“勝手な妥協”は、後工程のやり直しで **コストが最も増える**。
- “止めて相談” に寄せると、判断の責任所在（人間）と実行（AI）が分離できる。

### Alternatives（代替案）
- A) fallbackを常時許可（非推奨）: 目先の完了を優先し、品質が崩れる。
- B) provider内だけ許可: provider差分が大きい場合、結局品質差が出る（慎重に）。

---

## D-003（P0）Publish（外部SoT）→ローカル投稿済みロックを同期する？

### Decision
- 外部SoT（Sheet）が `uploaded` になったとき、ローカル側も “投稿済みロック” を **同期できる**ようにする。

### Recommended（推奨）
- publisherに `--also-lock-local` のような **任意フラグ**を追加し、以下を同期:
  - `status.json: published_lock=true`（以後の破壊的操作をガード）
  - Planning CSV: `進捗=投稿済み`（人間の一覧性のため。ただし“正本は外部”）

### Rationale（根拠）
- 「Sheetは更新されたがローカルが未ロック」事故が最も起きやすい（忘れ/並列作業）。
- 任意フラグなら、初期は手動運用も残しつつ段階導入できる。

### Alternatives（代替案）
- A) UIで手動ロック固定: “忘れ” が残る（非推奨）。
- B) 常時自動同期: 安全だが、誤ったSheet更新時にローカルも巻き込む（導入は慎重に）。

---

## D-004（P1）`script_validation` 品質ゲート round 上限

### Decision
- 既定は **最大3** に揃える（必要時のみ明示で増やす）。

### Recommended（推奨）
- 既定=3（SSOT側で固定）  
- 例外は “明示スイッチ（env/flag）” で 5 にできる（緊急時/長尺のみ）

### Rationale（根拠）
- round増はコスト/時間に直結するため、既定は抑えるべき。
- “必要な回だけ上げる” は意思決定の可視化（監査）に向く。

---

## D-005（P1）意味整合の自動修正（auto-fix）範囲

### Decision
- auto-fixは **outlineのみ bounded**。`script_validation` は **手動適用** に固定する。

### Recommended（推奨）
- outline段階: 章立ての崩れを軽く直す（bounded）  
- validation段階: Aテキストは下流（TTS/Video）へ直結するため、勝手な書換えを避ける

### Rationale（根拠）
- 早期修正は被害が小さいが、最終稿の自動書換えは事故影響が大きい。

---

## D-006（P2）Video 入口一本化（CapCut）

### Decision
- “主線” は `auto_capcut_run` + `capcut_bulk_insert` に固定する。

### Recommended（推奨）
- `run_pipeline --engine capcut` は **stub/非推奨** として明記し、誤用導線を消す。

---

## D-007（P2）Audio “Bテキスト” 例外運用

### Decision
- Bテキストは **例外導線（明示入力）** として残す（デフォルトはAテキストSoT強制）。

### Recommended（推奨）
- 例外は CLI/明示入力のみ（暗黙fallback禁止）  
- split-brain/alignment stamp/stale guard を崩さない

---

## D-008（P2）Publishの一時DL保持

### Decision
- 一時DLは repo直下ではなく `workspaces/tmp/publish/` に寄せ、成功後削除を基本にする。

### Recommended（推奨）
- 成功後削除（既定）  
- 監査/再送が必要な場合のみ保持（保持期間/容量上限をSSOT化）

---

## D-009（P2）ゾンビコードの整理方針

### Decision
- “確実ゴミ” 以外は、まず **隔離（入口索引から外す）→監査→archive-first削除**。

### Recommended（推奨）
- `ops/OPS_ZOMBIE_CODE_REGISTER.md` に根拠付きで列挙
- 削除時は `plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` に従う

