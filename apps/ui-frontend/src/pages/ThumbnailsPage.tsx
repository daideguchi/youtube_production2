import { ThumbnailWorkspace } from "../components/ThumbnailWorkspace";
import { Link, useOutletContext } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ThumbnailsPage() {
  const { channels } = useOutletContext<ShellOutletContext>();
  return (
    <section className="thumbnail-page workspace--thumbnail-clean">
      <header className="thumbnail-page__header">
        <div>
          <h1 className="thumbnail-page__title">サムネ</h1>
          <p className="thumbnail-page__subtitle">まず「量産（Canva）」でコピーを整え、CSVで一括生成。採用画像の紐付けやAI生成は必要なときだけ。</p>
        </div>
        <div className="thumbnail-page__header-actions">
          <Link className="action-chip" to="/thumbnails/mobile">
            モバイル確認
          </Link>
          <Link className="action-chip" to="/dashboard">
            ダッシュボード
          </Link>
          <Link className="action-chip" to="/channel-settings">
            チャンネル設定
          </Link>
        </div>
      </header>
      <details className="thumbnail-page__details">
        <summary>データソース（SoT）</summary>
        <div className="thumbnail-page__details-body">
          <span className="status-chip">
            projects: <code>workspaces/thumbnails/projects.json</code>
          </span>
          <span className="status-chip">
            templates: <code>workspaces/thumbnails/templates.json</code>
          </span>
        </div>
      </details>
      <ThumbnailWorkspace channelSummaries={channels} />
    </section>
  );
}
