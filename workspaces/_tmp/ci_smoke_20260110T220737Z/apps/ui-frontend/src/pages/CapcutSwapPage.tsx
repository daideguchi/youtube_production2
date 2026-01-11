import { Link } from "react-router-dom";
import { SwapImagesPage } from "./SwapImagesPage";

export function CapcutSwapPage() {
  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>既存ドラフトの画像差し替え</h1>
          <p className="page-lead">既存のCapCutドラフトを選び、差し替え画像で更新します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit">
            ← CapCut編集メニューへ戻る
          </Link>
        </div>
      </header>
      <section className="capcut-edit-page__section">
        <SwapImagesPage />
      </section>
    </div>
  );
}
