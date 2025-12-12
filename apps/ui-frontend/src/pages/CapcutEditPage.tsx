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
          <Link className="button button--ghost" to="/capcut-edit/production">
            プロジェクト管理
          </Link>
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
          「プロジェクト管理」は SRT解析/画像/帯/CapCut配置まで一括で扱うワークスペースです。戻るときは各ページ上部のリンクからこのメニューに戻れます。
        </p>
      </section>
    </div>
  );
}
