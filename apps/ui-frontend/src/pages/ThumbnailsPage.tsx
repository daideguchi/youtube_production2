import { ThumbnailWorkspace } from "../components/ThumbnailWorkspace";
import { Link } from "react-router-dom";

export function ThumbnailsPage() {
  return (
    <section className="thumbnail-page workspace--thumbnail-clean">
      <div className="main-status" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span className="status-chip">
            SoT: <code>workspaces/thumbnails/projects.json</code>
          </span>
          <span className="status-chip">
            templates: <code>workspaces/thumbnails/templates.json</code>
          </span>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <Link className="action-chip" to="/dashboard">
            ダッシュボード
          </Link>
          <Link className="action-chip" to="/channel-settings">
            チャンネル設定
          </Link>
        </div>
      </div>
      <ThumbnailWorkspace />
    </section>
  );
}
