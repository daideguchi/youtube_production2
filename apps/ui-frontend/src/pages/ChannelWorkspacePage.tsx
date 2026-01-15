import { useOutletContext } from "react-router-dom";
import { DashboardOverviewPanel } from "../components/DashboardOverviewPanel";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ChannelWorkspacePage() {
  const {
    channels,
    channelsLoading,
    channelsError,
    dashboardOverview,
    dashboardLoading,
    dashboardError,
    selectedChannel,
    navigateToChannel,
    handleFocusAudioBacklog,
    handleFocusNeedsAttention,
  } = useOutletContext<ShellOutletContext>();

  const showStatus = channelsLoading || channelsError || dashboardLoading || dashboardError;

  return (
    <>
      {showStatus ? (
        <div className="main-status">
          {channelsLoading ? <span className="status-chip">チャンネル読み込み中…</span> : null}
          {channelsError ? <span className="status-chip status-chip--warning">{channelsError}</span> : null}
          {dashboardLoading ? <span className="status-chip">ダッシュボード読み込み中…</span> : null}
          {dashboardError ? <span className="status-chip status-chip--danger">{dashboardError}</span> : null}
        </div>
      ) : null}
      <section className="main-content main-content--dashboard">
        <DashboardOverviewPanel
          overview={dashboardOverview}
          loading={dashboardLoading}
          error={dashboardError}
          channels={channels}
          onSelectChannel={navigateToChannel}
          selectedChannel={selectedChannel}
          onFocusAudioBacklog={handleFocusAudioBacklog}
          onFocusNeedsAttention={handleFocusNeedsAttention}
          title="台本・音声字幕管理"
          titleIcon="🎛️"
          subtitle="既存の案件を俯瞰し、台本・音声・字幕の滞留を可視化します。"
        />
      </section>
    </>
  );
}
