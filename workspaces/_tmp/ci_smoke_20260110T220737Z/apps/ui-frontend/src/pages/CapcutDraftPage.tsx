import { Link } from "react-router-dom";
import { AutoDraftPage } from "./AutoDraftPage";

export function CapcutDraftPage() {
  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>新規ドラフト作成</h1>
          <p className="page-lead">素材からCapCutドラフトを生成します。完了後にCapCutで開いて編集できます。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit">
            ← CapCut編集メニューへ戻る
          </Link>
        </div>
      </header>
      <section className="capcut-edit-page__section">
        <AutoDraftPage />
      </section>
    </div>
  );
}
