# OPS_GIT_BRANCH_POLICY — ブランチ運用ルール（迷わないためのSSOT）

目的:
- 「どのブランチが正本か」「どこにマージすべきか」を固定し、GitHub Pages/CI/並列エージェント運用で迷子にならないようにする。

関連:
- Gitロールバック事故の再発防止: `ssot/ops/OPS_GIT_SAFETY.md`
- 複数エージェント運用: `ssot/ops/OPS_AGENT_PLAYBOOK.md`

---

## 0) TL;DR（これだけ守れば迷わない）

1. **`main` が唯一の正本ブランチ**（GitHub Pages/CIの基準。PRのベースも `main`）
2. 作業は必ず **短命ブランチ**（例: `feat/...`, `fix/...`, `codex/...`）で行い、**PRで `main` へ**。
3. 並列運用の統合は **`snapshot/<UTCタイムスタンプ>`**（統合用の一時ブランチ）で受け、確認後 **`main` へ戻して削除**。
4. `new-main` のような **「第2のmain」系は作らない**（迷いの原因になるため）。
5. マージ済みブランチは **速やかに削除**（GitHub設定で自動削除をON推奨）。

---

## 1) ブランチの役割（固定）

### 1.1 `main`（唯一の正本）

- 役割: 公開/運用の正本（GitHub Pages / CI / 参照の基準）。
- ルール:
  - 原則「直接push禁止」。PR経由で取り込む。
  - 例外: Orchestrator が **統合済み `snapshot/*` を fast-forward で `main` に反映**（事故復旧や統合作業のため）。

### 1.2 短命ブランチ（作業用）

- 役割: 変更の単位を切り、レビュー/差分を明確にする。
- 命名:
  - 人間/通常: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`
  - AI提案/実験: `codex/<topic>`
- ルール:
  - `main` から作る。
  - PRで `main` に入れたら **削除**（GitHubの “Automatically delete head branches” を推奨）。

### 1.3 `snapshot/<UTCタイムスタンプ>`（統合・復旧用）

- 役割: **複数エージェント並列運用**での統合点／事故復旧の退避先。
- ルール:
  - `main` から作る（または復旧時に合流点として作る）。
  - 確認が終わったら `main` に反映し、**snapshotブランチは削除**する（不要な分岐を残さない）。
  - 「この時点」を残したいだけなら、ブランチではなく **tag** でも良い（運用で統一する）。

---

## 2) 迷ったときの判断フロー（固定）

1) 「GitHub Pages/CIに反映したい」→ **`main` に入れる**（`new-main` 等は使わない）  
2) 「複数の変更をまとめてから入れたい」→ `snapshot/*` に統合 → 検証 → `main`  
3) 「1機能/1修正」→ 短命ブランチ → PR → `main`  

---

## 3) 運用の注意（並列エージェント前提）

- Codex環境ではロールバック系Git操作がガードされる（`OPS_GIT_SAFETY` 参照）。
- commit/pushが不安定な環境では、まず `bash scripts/ops/save_patch.sh` でパッチ保存し、Orchestrator/人間が apply→commit→push する。
- `main` 反映（push）直前のみ `.git` の write-lock を一時解除する（`python3 scripts/ops/git_write_lock.py unlock-for-push`）。

---

## 4) 安全に「完全push」する手順（推奨）

目的:
- 他エージェントの変更を混ぜず、事故なく `main` まで反映する（= “完全push”）。

手順（おすすめの最小ルート）:

1. 変更範囲を lock する（並列衝突防止）
2. **スコープ限定パッチ**を作る（差分を“持ち運び”できる形に）
   - 例: `bash scripts/ops/save_patch.sh --label model_policy_ui --path 'apps/ui-frontend/src/pages/ChannelModelPolicyPage.*' --path 'ssot/ops/OPS_*MODEL_ROUTING.md' --path 'ssot/ops/OPS_GIT_*.md'`
3. Orchestrator/人間が clean な `main` から短命ブランチを作り、パッチを apply
   - ブランチ例: `codex/model-policy-ui`
4. チェックを通す（最低限）
   - `npm -C apps/ui-frontend run build`
   - `python3 scripts/ops/build_ssot_catalog.py --check`
5. push前に `.git` を一時アンロック → push後すぐ再ロック
   - エラー `.../.git/index.lock: Operation not permitted` が出る場合は `ssot/ops/OPS_GIT_SAFETY.md` の「5) よくあるエラー」を参照
6. PR → `main` へ（merge後、短命ブランチは削除）

補足:
- 「自分の作業ツリーに他人の差分が大量にある」状態で、直接 `git add` すると混ぜやすい。**パッチ運搬**を標準にする。
