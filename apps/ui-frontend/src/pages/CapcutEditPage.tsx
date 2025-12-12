import React from "react";
import { Link } from "react-router-dom";

export function CapcutEditPage() {
  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>CapCut編集</h1>
          <p className="page-lead">用途に応じてモードを選択してください。ページが分岐します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button" to="/capcut-edit/draft">
            新規ドラフト作成
          </Link>
          <Link className="button" to="/capcut-edit/swap">
            既存ドラフトの画像差し替え
          </Link>
        </div>
      </header>
      <section className="capcut-edit-page__section">
        <p style={{ marginTop: 0 }}>
          「新規ドラフト作成」と「既存ドラフトの画像差し替え」を選ぶと、専用ページに遷移します。戻るときは各ページ上部のリンクからこのメニューに戻れます。
        </p>
      </section>
    </div>
  );
}
