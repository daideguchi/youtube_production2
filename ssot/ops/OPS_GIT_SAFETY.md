# OPS_GIT_SAFETY — Gitロールバック事故の再発防止（仕組み）

目的: 複数エージェント並列運用でも、`git restore/checkout/reset` 等で作業ツリーが巻き戻る事故を防ぐ。

前提:
- 通常のエージェントは git の破壊的操作（ロールバック/履歴改変）を実行しない。
- Orchestrator は **commit/push のみ** 実施してよい（ただしロールバック系は絶対に実行しない）。
- 変更を細かく共有したい場合は `bash scripts/ops/save_patch.sh` でパッチ保存し、Orchestrator が apply→commit→push する。

---

## 1) 仕組み（第一）: Codex execpolicy（ロールバック遮断）

`git restore/checkout/reset/clean/revert/switch` を `forbidden` にして、事故の根本原因を仕組みで遮断する。

補足:
- macOS では git 実体が複数ある場合がある（例: `/usr/bin/git` と CLT git）。
- **どの git 実体でも遮断が効くこと** を確認し、ルールに含める（事故のバイパス防止）。

---

## 2) 仕組み（第二・任意）: `.git` write-lock（物理ガード）

`.git/` を write-lock することで、破壊的git操作が即失敗する状態を作る。

コマンド:
- 状態確認: `python3 scripts/ops/git_write_lock.py status`
- ロック: `python3 scripts/ops/git_write_lock.py lock`
- アンロック（push時のみ・人間限定）: `python3 scripts/ops/git_write_lock.py unlock`

運用ルール:
- **通常運用は常に lock**（並列エージェントが動く間は特に）
- pushの直前だけ人間が unlock し、push後はすぐ lock に戻す（Orchestrator は unlock しない）

補足:
- `status` が `locked (external)` の場合、環境側（sandbox/OS制約）で `.git/` が保護されている状態。Codexからの破壊的git操作は既に通りにくいが、execpolicy（後述）も併用して二重化する。

---

## 3) push前の最終チェック（SSOT整合）

push前に、SSOT↔実装の不整合がないかを点検する（詳細は `scripts/ops/pre_push_final_check.py` を参照）。

※ サムネ作成・編集周りは調整中のため、該当領域の仕様確定までは「警告は出るが運用で判断」する。
