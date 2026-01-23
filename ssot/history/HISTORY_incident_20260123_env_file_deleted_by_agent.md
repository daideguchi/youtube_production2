# INCIDENT — 2026-01-23 repo 直下 `.env` をエージェントが削除し、全実行入口が停止した（factory_commentary）

作成: 2026-01-23T10:00:00+0900（UTC: 2026-01-23T01:00:00Z）
対象repo: `/Users/dd/10_YouTube_Automation/factory_commentary`

方針:
- 事実（ファイル/ログ/ユーザー指摘）と、当該エージェントの自己申告を分ける
- APIキー等の機密は記載しない（値は書かない）

---

## 1) 事象（要約 / 事実）

- リポジトリ直下の `.env`（gitignore / ローカル運用の秘密情報ファイル）が削除され、`./scripts/with_ytm_env.sh` が `❌ .env not found ...` で停止する状態になった。
- `./ops` は内部で `./scripts/with_ytm_env.sh` を呼ぶため、結果として **ops 系の入口が広範囲に停止**し、運用が詰まった。

## 2) 影響範囲（事実）

- 影響: `./ops ...` / `./scripts/with_ytm_env.sh ...` を前提にしたコマンドが実行不能（= 事前点検/再開/動画生成などの入口が止まる）。
- 復旧の難しさ: `.env` は **git 管理されない**ため `git checkout` 等で戻せない。復元はローカルバックアップ（TimeMachine/エディタ履歴/手元控え）依存になる。

## 3) 原因（当該エージェント自己申告）

- “API 429 を出したキーは物理削除”の要求に対して、キー文字列探索の延長で **`.env` 自体を「キー痕跡」と誤認し削除**した。
- 並列運用ルール（`agent_org` lock）を **取らずに破壊的操作**をした（重大な手順違反）。
- `.env` が “ローカルにだけ存在する正本”である点（= 失うと戻せない）への配慮が不足していた。

## 4) 復旧（事実）

- `.env` の復元元として、ローカルのエディタ履歴（Windsurf/Cursor の History）に **dotenv 形式（KEY=VALUE が多数）のスナップショットが残っている**ことを確認。
- 最も新しい dotenv 形式候補（mtime最大）を復元元として採用し、repo 直下へ `.env` を復元した。
- 検証:
  - `./scripts/with_ytm_env.sh python3 scripts/check_env.py` が `✅ All required environment variables are set` を返すこと
  - `./ops doctor` が `.env missing` で落ちないこと
  - 補足（混同防止）: Codex の MCP `brave-search` は `~/.codex/config.toml` 側の設定で起動する（repo の `.env` とは別系統）

## 4.1) これまでの経緯（時系列 / 事実）

時刻は原則 JST。`.env` は git 管理外のため「削除した瞬間」の正確な時刻は残らない（ユーザー指摘/ログ/mtime で追う）。

- ユーザー指摘: `.env` 消失により `./scripts/with_ytm_env.sh` が停止し、`./ops` 系入口が停止した。
- 復旧: エディタ履歴（Windsurf/Cursor の History）から dotenv 形式のスナップショットを探索し、最新候補を採用して `.env` を復元した。
  - 参照元（事実）: Windsurf History 内の dotenv 形式候補（例: `~/Library/Application Support/Windsurf/User/History/...`）
  - 以後の検証は「値を表示しない」コマンドで実施（上記の `check_env.py` / `./ops doctor`）。
- 再発防止: SSOT と `./ops` に `.env` 保護/復旧の入口を追加した（`./ops env ...`）。
  - 実装（事実）: `69c52990`
- CI: GitHub Actions `LLM Smoke` が `ssot_audit` の曖昧語検出で失敗したため、該当文言を「既定: dry-run」へ変更して通過させた。
  - 修正（事実）: `d3e8667b`
- Codex起動時の `brave-search` MCP: repo の `.env` ではなく `~/.codex/config.toml` に依存することを確認し、起動コマンドを `npx` からローカルの `mcp-server-brave-search` 直起動へ変更した（設定値は変更しない）。
  - 重要（事実）: `BRAVE_API_KEY` は Codex 側の設定で渡す必要がある（repo の `.env` とは別）。

## 5) 再発防止（SSOT / 仕組み）

### A) 運用ルール（強制）

- `.env` は **最重要のローカル正本（秘密）**として扱う。
  - 削除/作成/書き換えは **原則: 人間のみ**（エージェント単独判断は禁止）
  - `.env` を触る必要がある作業は、必ず `agent_org` lock を置き、board/memo で合意を残す
- “ゴミ削除”は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` に従う。`.env` はゴミではない。

### B) 復旧手順のSSOT化

- `.env` が消えた場合の復旧手順（復元元の優先順位、検証手順）を `ssot/ops/OPS_ENV_VARS.md` に追記する。
- 入口索引に “env 復旧/保護” を追加し、迷子を防ぐ（`ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）。

### C) 技術的な保護（任意）

- macOS のファイル保護（例: `chflags uchg .env`）等で **誤削除を物理的に防ぐ**運用を推奨する。
  - 解除が必要な場合のみ `chflags nouchg .env`。
  - これらの操作は **明示コマンド化**し、手順をSSOTへ固定する。

## 6) アクションアイテム（実装タスク）

- [x] `./ops env ...`（status/protect/unprotect/recover）を追加し、`.env` の保護と復元を “入口” として固定する（実装: `main` / `69c52990`, 文言のCI修正: `d3e8667b`）
- [x] `OPS_AGENT_PLAYBOOK.md` に `.env` を含む “critical local secrets” の扱い（削除禁止/lock必須/復旧手順）を追記する（`69c52990`）
- [ ] 今回の生ログ/断片は `log_research/` → `backups/_incident_archives/.../` へ退避し、`ssot/history/` には本ファイルのみ残す
