import { useOutletContext } from "react-router-dom";
import { ChannelListSection } from "../components/ChannelListSection";
import type { ShellOutletContext } from "../layouts/AppShell";

export function DashboardPage() {
  const {
    channels,
    channelsLoading,
    channelsError,
    dashboardError,
    dashboardOverview,
    selectedChannel,
    selectChannel,
  } = useOutletContext<ShellOutletContext>();

  const handleSelect = (code: string | null) => {
    selectChannel(code);
  };

  return (
    <>
      {channelsLoading || channelsError || dashboardError ? (
        <div className="main-status">
          {channelsLoading ? <span className="status-chip">チャンネル読み込み中…</span> : null}
          {channelsError ? <span className="status-chip status-chip--danger">{channelsError}</span> : null}
          {dashboardError ? <span className="status-chip status-chip--danger">{dashboardError}</span> : null}
        </div>
      ) : null}
      <section className="main-content main-content--dashboard">
        <ChannelListSection
          variant="dashboard"
          channels={channels}
          channelStats={dashboardOverview?.channels}
          selectedChannel={selectedChannel}
          loading={channelsLoading}
          error={channelsError}
          onSelectChannel={handleSelect}
        />
      </section>
    </>
  );
}
