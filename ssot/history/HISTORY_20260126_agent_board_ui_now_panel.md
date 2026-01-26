# HISTORY_20260126_agent_board_ui_now_panel — Shared BoardをUIで直感的に見る

目的:
- board note / locks / doing を「CLIを叩かずに」まずUIで直感的に把握できる状態にする。
- Lenovo共有が落ちていても **Macローカルの board.json** を正として、運用を止めない。

変更点（UI）:
- `/agent-board` に **Now（agents一覧）** を追加:
  - agent / doing / blocked / next / tags / age / updated を一覧表示
  - `blocked only` フィルタと検索を追加
  - blocked 行はハイライト
- `/agent-board` 上部に「見る順」の説明を追加（Now→blocked→Threads）。

使い方:
- UI → `/agent-board`
  - まず `Now → blocked only` をON（詰まりが一発で見える）
  - 詳細は `Threads` / `Thread` を参照

実装:
- commit: `256db7c1`
- 変更ファイル:
  - `apps/ui-frontend/src/components/AgentBoardPanel.tsx`
  - `apps/ui-frontend/src/components/AgentBoardPanel.css`
  - `apps/ui-frontend/src/pages/AgentBoardPage.tsx`

