# OPS_GIT_SAFETY — Gitロールバック事故の再発防止（仕組み）

目的: 複数エージェント並列運用でも、`git restore/checkout/reset` 等で作業ツリーが巻き戻る事故を防ぐ。

前提:
- 通常のエージェントは git の破壊的操作（ロールバック/履歴改変）を実行しない。
- Orchestrator は **commit/push のみ** 実施してよい（ただしロールバック系は絶対に実行しない）。
- 変更を細かく共有したい場合は `bash scripts/ops/save_patch.sh` でパッチ保存し、Orchestrator が apply→commit→push する。
- ブランチ運用（どこにマージするか）: `ssot/ops/OPS_GIT_BRANCH_POLICY.md`

---

## 1) 仕組み（第一）: Codex Git Guard（ハードブロック）

Codex shell では `git restore/checkout/reset/clean/revert/switch/stash` を **常に失敗**させる（rollback 事故を物理的に遮断する）。
該当コマンドを叩いた瞬間に、**ターミナルへ超目立つアラート（AA + bell）** を出して `exit 42` で止める（＝Codexにも失敗として通知される）。

実体:
- `~/.codex/bin/git`（PATH先頭に置くラッパー）
- `~/.zprofile` / `~/.zshenv`（Codexセッションのみ PATH を prepend）

挙動:
- 既定: 該当サブコマンドは **常にブロック**（rollback は絶対に走らない）
- `restore` / `checkout` は特に事故率が高いため **MAXIMUM ALERT**（より強いアラート）で止める
- 例外（Break-glass）: **人間がOKした時だけ**、対話的確認を通過した場合に限り実行を許可する
  - `CODEX_GIT_ROLLBACK_BREAKGLASS=1 git <subcmd> ...`
  - **非対話実行（TTYなし）では例外解除できない**（=暴走/バッチから守る）
  - 実行直前に表示されるワンタイム文字列 `ALLOW <subcmd> <CODE>` を完全一致入力できた場合のみ `/usr/bin/git` に通す

補足:
- `python -c 'subprocess.run([\"git\", ...])'` のような “python 経由のバイパス” も PATH を経由する限り遮断できる。
- 人間がやむを得ず実行する場合は **Codex外**で `/usr/bin/git ...` を使う（ただし運用上は原則禁止）。

---

## 2) 仕組み（第二）: Codex execpolicy（ロールバック遮断）

`git restore/checkout/reset/clean/revert/switch` を `forbidden` にして、事故の根本原因を仕組みで遮断する。

補足:
- macOS では git 実体が複数ある場合がある（例: `/usr/bin/git` と CLT git）。
- **どの git 実体でも遮断が効くこと** を確認し、ルールに含める（事故のバイパス防止）。

---

## 3) 仕組み（第三・任意）: `.git` write-lock（メタデータ保護）

`.git/` を write-lock して、`checkout/reset` など **`.git` を書き換える系**を即失敗させる。

注意:
- `git restore <file>` のように worktree だけを書き換える操作は `.git` write-lock **だけでは止まらない**。
- そのため、rollback遮断の主戦力は「Git Guard + execpolicy」で、`.git` write-lock は補助とする。

コマンド:
- 状態確認: `python3 scripts/ops/git_write_lock.py status`
- ロック: `python3 scripts/ops/git_write_lock.py lock`
- アンロック（push時のみ）: `python3 scripts/ops/git_write_lock.py unlock-for-push`

運用ルール:
- **通常運用は常に lock**（並列エージェントが動く間は特に）
- pushの直前だけ Orchestrator が一時解除し、push後はすぐ lock に戻す（`unlock-for-push` は Orchestrator lease 必須）

補足:
- `status` が `locked (external)` の場合、環境側（sandbox/OS制約）で `.git/` が保護されている状態。Codexからの破壊的git操作は既に通りにくいが、execpolicy（後述）も併用して二重化する。
- macOS: Codex環境では `chflags` コマンドがブロックされることがあるため、`git_write_lock.py` は `os.chflags`（Python）で immutable を操作して write-lock を成立させる。

---

## 4) push前の最終チェック（SSOT整合）

push前に、SSOT↔実装の不整合がないかを点検する（詳細は `scripts/ops/pre_push_final_check.py` を参照）。

※ サムネ作成・編集周りは調整中のため、該当領域の仕様確定までは「警告は出るが運用で判断」する。
