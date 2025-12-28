import { Link, useOutletContext, useSearchParams } from "react-router-dom";

import { ChannelAuditPanel } from "../components/ChannelAuditPanel";
import { WorkflowPrecheckPanel } from "../components/WorkflowPrecheckPanel";
import type { ShellOutletContext } from "../layouts/AppShell";

function normalizeChannel(value: string | null): string | null {
  const trimmed = (value ?? "").trim().toUpperCase();
  return trimmed ? trimmed : null;
}

export function AuditPage() {
  const { channels } = useOutletContext<ShellOutletContext>();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedChannel = normalizeChannel(searchParams.get("channel"));

  const applyChannel = (next: string | null) => {
    const params = new URLSearchParams(searchParams);
    const normalized = normalizeChannel(next);
    if (normalized) {
      params.set("channel", normalized);
    } else {
      params.delete("channel");
    }
    setSearchParams(params, { replace: true });
  };

  const channelPortalLink = selectedChannel ? `/channels/${encodeURIComponent(selectedChannel)}/portal` : "/dashboard";
  const channelSettingsLink = selectedChannel
    ? `/channel-settings?channel=${encodeURIComponent(selectedChannel)}`
    : "/channel-settings";
  const planningLink = selectedChannel ? `/planning?channel=${encodeURIComponent(selectedChannel)}` : "/planning";
  const thumbnailsLink = selectedChannel ? `/thumbnails?channel=${encodeURIComponent(selectedChannel)}` : "/thumbnails";

  return (
    <div className="page audit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">Audit / Precheck</p>
          <h1>監査（欠損チェック / Precheck）</h1>
          <p className="page-lead">欠損チェックと、音声処理前のPrecheckをまとめて確認します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/dashboard">
            ← ダッシュボード
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>対象チャンネル</h2>
          <p className="shell-panel__subtitle">チャンネルを選ぶと、各ビューでハイライトされます。</p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span>CH</span>
              <select value={selectedChannel ?? ""} onChange={(event) => applyChannel(event.target.value || null)}>
                <option value="">(未選択)</option>
                {channels.map((channel) => (
                  <option key={channel.code} value={channel.code}>
                    {channel.code}
                  </option>
                ))}
              </select>
            </label>
            <Link className="button button--ghost" to={channelPortalLink}>
              チャンネルポータル
            </Link>
            <Link className="button button--ghost" to={planningLink}>
              企画CSV
            </Link>
            <Link className="button button--ghost" to={thumbnailsLink}>
              サムネ
            </Link>
            <Link className="button button--ghost" to={channelSettingsLink}>
              チャンネル設定
            </Link>
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <WorkflowPrecheckPanel selectedChannel={selectedChannel} />
      </section>

      <section className="capcut-edit-page__section">
        <ChannelAuditPanel selectedChannel={selectedChannel} onSelectChannel={(code) => applyChannel(code)} />
      </section>
    </div>
  );
}
