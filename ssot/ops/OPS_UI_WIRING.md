# OPS_UI_WIRING — UI(React) ↔ Backend(FastAPI) 配線SSOT

目的:
- UIの「どのページがどのAPIを叩き、どのSoTを触るか」を確定し、迷いどころ/死んだ配線を作らない。
- UI改修時に「更新すべきファイル/SSOT」を固定して再現性を上げる。

関連SSOT:
- 実行入口: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- 全体像: `ssot/OPS_SYSTEM_OVERVIEW.md`
- ディレクトリ正本: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`

実装の正本（配線の根）:
- Frontend routes: `apps/ui-frontend/src/App.tsx`
- Frontend typed client: `apps/ui-frontend/src/api/client.ts`, `apps/ui-frontend/src/api/types.ts`
- Frontend API base: `apps/ui-frontend/src/api/baseUrl.ts`（`apiUrl()` / `REACT_APP_API_BASE_URL`）
- Backend app: `apps/ui-backend/backend/main.py`
- Backend routers: `apps/ui-backend/backend/routers/*.py`
- VideoProduction router: `apps/ui-backend/backend/video_production.py`

---

## 1) URL/環境変数ルール（迷わないための固定）

- 原則: **相対パス**（同一origin）で `/api/...` を叩く（dev proxy / start_all 前提）。
- 例外: GitHub Pages / 別originで動かす場合は `REACT_APP_API_BASE_URL` を設定する。
  - 正本実装: `apps/ui-frontend/src/api/baseUrl.ts`
  - 注意: `REACT_APP_API_BASE_URL` は末尾 `/` なしに正規化される。
  - fetch直叩きが残る場合でも、URL組み立ては `apiUrl()`（or `client.ts` の `resolveApiUrl()`）を通す。

---

## 2) Backend ルータ一覧（prefix → 実装ファイル）

| prefix | file | 主用途 |
| --- | --- | --- |
| `/api/agent-org` | `apps/ui-backend/backend/routers/agent_org.py` / `apps/ui-backend/backend/routers/agent_board.py` | Agents/Locks/Memos/Board |
| `/api/jobs` | `apps/ui-backend/backend/routers/jobs.py` | ジョブ一覧/削除 |
| `/api/tts-progress` | `apps/ui-backend/backend/routers/tts_progress.py` | TTS進捗 |
| `/api/auto-draft` | `apps/ui-backend/backend/routers/auto_draft.py` | AutoDraft |
| `/api/research` | `apps/ui-backend/backend/routers/research_files.py` | research ファイル操作 |
| `/api/swap` | `apps/ui-backend/backend/routers/swap.py` | 画像差し替え（Swap UI） |
| `/api/params` | `apps/ui-backend/backend/routers/params.py` | UIパラメータ/設定 |
| `/api/video-production` | `apps/ui-backend/backend/video_production.py` | run_dir/画像/CapCut関連 |
| `/llm-usage` | `apps/ui-backend/backend/routers/llm_usage.py` | LLM使用量ログ/override |

注:
- `apps/ui-backend/backend/main.py` には router 経由ではない endpoint も存在する（例: channels/planning/thumbnails 等）。
- 追加/変更したら、この表と `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` のUI節を更新する。

---

## 3) Frontend ページ（route → 主なAPI/SoT）

| route | page | 主なAPI | 主なSoT |
| --- | --- | --- | --- |
| `/dashboard` | `apps/ui-frontend/src/pages/DashboardPage.tsx` | dashboard系（`client.ts`） | `workspaces/**` |
| `/workflow` | `apps/ui-frontend/src/pages/WorkflowPage.tsx` | workflow系（`client.ts`） | `workspaces/**` |
| `/studio` | `apps/ui-frontend/src/pages/EpisodeStudioPage.tsx` | scripts/audio/video系（`client.ts`） | `workspaces/scripts/**`, `workspaces/audio/**`, `workspaces/video/**` |
| `/projects` | `apps/ui-frontend/src/pages/ScriptFactoryPage.tsx` | scripts系（`client.ts`） | `workspaces/scripts/**` |
| `/planning` | `apps/ui-frontend/src/pages/PlanningPage.tsx` | planning系（`client.ts`） | `workspaces/planning/**` |
| `/channel-workspace` | `apps/ui-frontend/src/pages/ChannelWorkspacePage.tsx` | channel系（`client.ts`） | `workspaces/**` |
| `/channels/:channelCode` | `apps/ui-frontend/src/pages/ChannelOverviewPage.tsx` | channel overview（`client.ts`） | `workspaces/**` |
| `/channels/:channelCode/portal` | `apps/ui-frontend/src/pages/ChannelPortalPage.tsx` | portal系（`client.ts`） | `workspaces/**` |
| `/channels/:channelCode/videos/:video` | `apps/ui-frontend/src/pages/ChannelDetailPage.tsx` | script/audio/manifest系（`client.ts`） | `workspaces/scripts/**`, `workspaces/audio/final/**` |
| `/audio-tts` | `apps/ui-frontend/src/pages/AudioTtsPage.tsx` | `/api/tts-progress` 等 | `workspaces/audio/**` |
| `/audio-integrity` | `apps/ui-frontend/src/pages/AudioIntegrityPage.tsx` | audio integrity系（`client.ts`） | `workspaces/audio/final/**` |
| `/capcut-edit` | `apps/ui-frontend/src/pages/CapcutEditPage.tsx` | `/api/video-production/*`, `/api/swap/*` | `workspaces/video/runs/**` |
| `/capcut-edit/production` | `apps/ui-frontend/src/pages/ProductionPage.tsx` | `/api/video-production/*` | `workspaces/video/runs/**` |
| `/capcut-edit/draft` | `apps/ui-frontend/src/pages/CapcutDraftPage.tsx` | `/api/video-production/*` | `workspaces/video/runs/**` |
| `/capcut-edit/swap` | `apps/ui-frontend/src/pages/CapcutSwapPage.tsx` | `/api/swap/*` | `workspaces/video/runs/**` |
| `/image-management` | `apps/ui-frontend/src/pages/ImageManagementPage.tsx` | `/api/video-production/*`（画像variants含む） | `workspaces/video/runs/**` |
| `/thumbnails` | `apps/ui-frontend/src/pages/ThumbnailsPage.tsx` | `/api/workspaces/thumbnails/*` | `workspaces/thumbnails/**` |
| `/channel-settings` | `apps/ui-frontend/src/pages/ChannelSettingsPage.tsx` | `/api/channels/register` 等 | `packages/script_pipeline/channels/**`, `workspaces/planning/**` |
| `/prompts` | `apps/ui-frontend/src/pages/PromptManagerPage.tsx` | `/api/prompts*` | `packages/**/prompts/**` |
| `/agent-org` | `apps/ui-frontend/src/pages/AgentOrgPage.tsx` | `/api/agent-org/*` | `workspaces/logs/agent_tasks/**`（board/locks/memos） |
| `/agent-board` | `apps/ui-frontend/src/pages/AgentBoardPage.tsx` | `/api/agent-org/*` | `workspaces/logs/agent_tasks/**` |
| `/llm-usage` | `apps/ui-frontend/src/pages/LlmUsagePage.tsx` | `/llm-usage/*` | `workspaces/logs/**` |

補足:
- fetch直叩きが残っている箇所は、原則 `apps/ui-frontend/src/api/client.ts` へ集約して“配線”を減らす（例外: streaming / blob 等）。
  - 直叩きが必要な場合でも `apiUrl()` / `resolveApiUrl()` を必ず通し、base URL の不整合を作らない。
