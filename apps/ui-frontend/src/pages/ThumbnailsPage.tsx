import { ThumbnailWorkspace } from "../components/ThumbnailWorkspace";
import { Link } from "react-router-dom";

export function ThumbnailsPage() {
  return (
    <section className="thumbnail-page workspace--thumbnail-clean">
      <header className="thumbnail-page__header">
        <div>
          <h1 className="thumbnail-page__title">サムネ</h1>
          <p className="thumbnail-page__subtitle">「チャンネルの型」と「案件の案」を分けて整理し、AI生成は手動実行のみで運用します。</p>
        </div>
        <div className="thumbnail-page__header-actions">
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
      <ThumbnailWorkspace />
    </section>
  );
}
