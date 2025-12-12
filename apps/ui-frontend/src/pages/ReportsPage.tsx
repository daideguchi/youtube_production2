import { useOutletContext } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ReportsPage() {
  const { placeholderPanel } = useOutletContext<ShellOutletContext>();

  return (
    <section className="main-content main-content--placeholder">
      <div className="shell-panel shell-panel--placeholder">
        <h2>{placeholderPanel?.title ?? "準備中"}</h2>
        <p className="shell-panel__subtitle">
          {placeholderPanel?.description ?? "この画面は現在開発中です。ダッシュボードをご利用ください。"}
        </p>
      </div>
    </section>
  );
}
