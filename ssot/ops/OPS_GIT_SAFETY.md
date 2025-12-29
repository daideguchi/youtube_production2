# OPS_GIT_SAFETY — Gitロールバック事故の再発防止（仕組み）

目的: 複数エージェント並列運用でも、`git restore/checkout/reset` 等で作業ツリーが巻き戻る事故を防ぐ。

前提:
- git操作（commit/push/rollback）は **人間が実施**する（エージェントは実施しない）
- 変更は必要に応じて `bash scripts/ops/save_patch.sh` でパッチ保存し、Orchestrator/人間が apply→commit する

---

## 1) 仕組み（物理ガード）: `.git` write-lock

`.git/` を write-lock することで、破壊的git操作が即失敗する状態を作る。

コマンド:
- 状態確認: `python3 scripts/ops/git_write_lock.py status`
- ロック: `python3 scripts/ops/git_write_lock.py lock`
- アンロック（push時のみ）: `python3 scripts/ops/git_write_lock.py unlock`

運用ルール:
- **通常運用は常に lock**（並列エージェントが動く間は特に）
- pushの直前だけ unlock し、push後はすぐ lock に戻す

補足:
- `status` が `locked (external)` の場合、環境側（sandbox/OS制約）で `.git/` が保護されている状態。Codexからの破壊的git操作は既に通りにくいが、execpolicy（後述）も併用して二重化する。

---

## 2) push前の最終チェック（SSOT整合）

push前に、SSOT↔実装の不整合がないかを点検する（詳細は `scripts/ops/pre_push_final_check.py` を参照）。

※ サムネ作成・編集周りは調整中のため、該当領域の仕様確定までは「警告は出るが運用で判断」する。
