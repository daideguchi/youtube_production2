import { VideoProductionWorkspace } from "../components/VideoProductionWorkspace";
import { Link } from "react-router-dom";

export function ProductionPage() {
  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>プロジェクト管理</h1>
          <p className="page-lead">SoT（run_dir）を基準に、SRT解析→画像/帯→CapCut配置までを一画面で回します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit">
            ← CapCut編集メニュー
          </Link>
        </div>
      </header>
      <VideoProductionWorkspace />
    </div>
  );
}
