# commentary_02 React UI リファクタ計画（2025-11-04）

## 目的
- Streamlit/Gradio ベースの UI を React に置き換え、動画制作パイプラインをブラウザから操作できるモダンなフロントエンドに再構築する。
- サイドバーは commentary_01 側の共通 UI から iframe/リンクで呼び出せるようにしつつ、動画制作機能自体は commentary_02 ディレクトリ内で完結させる。

## 現状
- `ui/streamlit_app.py` など Python/Streamlit に依存した UI が存在。CLI 実行は直接 `subprocess` 経由で呼び出し。
- `src/data/projects.py` など Python ユーティリティを追加済みだが、React から直接扱えない。
- React プロジェクトは未作成。バックエンド API も整備されていない。

## 方針概要
1. `ui/react-app/`（仮称）に Vite + React + TypeScript のフロントエンドを新規作成。
2. `ui/server/` に FastAPI ベースの軽量バックエンドを配置し、以下の REST API を提供：
   - `GET /api/projects` → `src/data/projects.py` を利用
   - `GET /api/projects/{id}` → プロジェクト詳細
   - `POST /api/jobs` → CLI 実行（srt2images, capcut 等）。`src/runtime/jobs.py` を再利用
   - `GET /api/jobs/{id}` / `GET /api/jobs/{id}/log`
3. React フロントは上記 API を用いて以下の画面を構築：
   - ダッシュボード（カード + フィルタ付きテーブル + 詳細パネル）
   - パイプライン Runner（SRT 選択、テンプレ設定、CLI 実行、進捗表示）
   - 出力管理（画像グリッド、再生成ボタン等）
4. 開発・起動スクリプト：`npm run dev`（React）と `uvicorn`（FastAPI）を同時起動する `start_react_ui.sh` を作成。
5. Streamlit/Gradio は残しつつ、React 版に移行できるよう README を更新。

## 実施ステップ
1. **環境セットアップ**
   - `ui/react-app/` を `npm create vite@latest` 等で初期化（React + TS）。
   - VSCode/ESLint/Prettier 設定を追加。
2. **バックエンド API**
   - `ui/server/main.py` に FastAPI 実装。`src/data/projects.py` と `src/runtime/jobs.py` をインポートして API 化。
   - CLI 実行時の作業ディレクトリ・環境変数（`PYTHONPATH=src` 等）を設定。
3. **React アプリ構築**
   - ページ構成：`DashboardPage`, `PipelinePage`, `ManagePage` 等。
   - コンポーネント：`ProjectTable`, `ProjectDetail`, `JobConsole` など。
   - API クライアント：Fetch wrapper + Typescript 型定義。
4. **デザイン/UX**
   - Tailwind か CSS Modules で UI デザイン。必要なら Ant Design 等検討。
   - 音声レビュー等の将来拡張ポイントを考慮し、共通レイアウト/テーマを設定。
5. **ビルド/デプロイ**
   - `npm run build` + `uvicorn` を想定した本番ビルド手順。
   - commentary_01 側からの iframe/リンク用 URL を決め、共有文書に追記。
6. **テスト**
   - Unit test（React Testing Library）で主要コンポーネントを検証。
   - バックエンドの API テスト（pytest + FastAPI TestClient）。

## リスク/注意
- CLI 実行はローカル環境依存（CapCut 等）。安全にハンドリングするためにジョブキューのロックとログ収集が必要。
- React 化に伴い、大量画像表示によるパフォーマンス懸念あり。Lazy load や pagination を検討。
- Streamlit 側との並行運用期間を考慮し、両 UI の起動ポートやログ出力場所を明確に分離する。

---

## 7. 実装スプリント計画（2025-11-05 更新）

### 7.1 スプリント目標
- commentary_02 React UI を **Pipeline 実行**・**素材管理**・**ジョブ履歴** まで動作させ、commentary_01 サイドバーから閲覧した際にも完全動作する状態を作る。
- 既存 Streamlit UI と同じ CLI 群（`srt2images`, `generate_belt_layers`, `capcut_bulk_insert`, `capcut_title_updater` など）を FastAPI 経由で安全に呼び出す。

### 7.2 フェーズ別タスク
1. **Backend 強化**
   - [ ] `ui/server/main.py` に以下の REST API を追加  
     - `POST /api/projects/{project_id}/jobs` : CLI 実行キュー投入（`action`, `options`, `note` を受領）  
     - `GET /api/jobs` : 最新ジョブ一覧（`project_id`・`limit` フィルタ対応）  
     - `GET /api/jobs/{job_id}` : 個別ジョブ状態  
     - `GET /api/jobs/{job_id}/log` : ログ全文ストリーミング  
   - [ ] `ui/src/runtime/jobs.py` をサーバー用に再利用し、永続キュー・ログファイル配置（`ui/logs/react/jobs/<job_id>.log`）を実装。  
   - [ ] CLI 実行時の環境変数テンプレートを整理し、`PYTHONPATH=PROJECT_ROOT/src` 等を設定。  
   - [ ] 長時間ジョブでも応答がタイムアウトしないよう `BackgroundTasks` と組み合わせた非同期化を行う。

2. **React UI 実装**
   - [ ] `PipelinePage` にフォーム送信 → `createJob` API 呼び出し → ジョブ結果モーダル表示を追加。  
   - [ ] `ManagePage` で画像再生成・ベルト再生成・CapCut メタ更新のショートカットボタンを提供（`VideoJobRequest` の `action` を切替）。  
   - [ ] `JobsPage` にジョブリスト表・フィルタ・詳細パネル・ログビューアを実装。初期実装はポーリング更新（5s interval）とし、WebSocket は後続検討。  
   - [ ] 共通 UI コンポーネント（`JobStatusBadge`, `ProjectSelector`, `JobLogViewer` など）を `src/components` に整理。

3. **統合・検証**
   - [ ] （旧 `start_react_stack.sh` は削除済み。以降の要件は `youtube_master/ui` へ移行）  
   - [ ] commentary_01 側の `VideoProductionWorkspace` iframe から React UI (デフォルト `http://127.0.0.1:5174`) が開くことを再確認。  
   - [ ] 実案件データ（例: `output/jinsei186`）でダッシュボード表示 → Pipeline 実行（ドライラン）→ ジョブ履歴反映のエンドツーエンド動作を記録。  
   - [ ] `UI_INTEGRATION_GUIDE.md` に新規追加 API と起動手順を追記。

### 7.3 成果物
- 完成した FastAPI エンドポイント群（型定義付き）とジョブ永続化ノート（`ui/logs/react/README.md`）。
- React ページ 3 枚（Pipeline / Manage / Jobs）と共有コンポーネント。
- 検証ログ (`logs/ui/react_stack_validation.md`) および更新済みガイド (`UI_INTEGRATION_GUIDE.md`)。

### 7.4 品質確認チェックリスト
- [ ] 旧 `./ui/start_react_stack.sh` フローは廃止。現在は `youtube_master/start.sh` を利用する。  
- [ ] FastAPI `/healthz` が 200 ({"status": "ok"}) を返す。  
- [ ] `/api/projects` 応答が 500ms 以内。  
- [ ] `/api/projects/{id}` で画像サンプル・ログ抜粋・SRT プレビューが表示される。  
- [ ] 新規ジョブ投入後、`JobsPage` に 5 秒以内に反映され、ログ閲覧が成功する。  
- [ ] エラー時に UI がユーザーへ警告を表示し、サーバーログにスタックトレースが残る。

### 7.5 既知リスクと対策
- **CapCut CLI の不安定さ** → 初期リリースでは `dry_run` オプションをデフォルト付与し、実出力前に確認フローを組み込む。  
- **大量ログ読み込み** → API 側で既定 200 行までに制限し、全量取得は `/log` エンドポイントで必要時のみ行う。  
- **環境差異** → 主要パスはサーバー側で解決し、フロントフォームでは相対パス入力を受け付けない（選択式 UI を導入）。

> 本節は最新の実装計画。進捗に合わせて更新し、作業前に必ずここで段取りを確認すること。

---
以降はこの計画に従って React 化を進める。
