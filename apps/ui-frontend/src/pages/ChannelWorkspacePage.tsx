import { useMemo } from "react";
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
    reloadWorkspace,
  } = useOutletContext<ShellOutletContext>();

  const sortedChannels = useMemo(
    () => [...channels].sort((a, b) => a.code.localeCompare(b.code)),
    [channels]
  );
  const channelPickerDisabled = channelsLoading || Boolean(channelsError);
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
        <section className="channel-top-switcher" aria-label="チャンネル切替">
          <div className="channel-top-switcher__header">
            <span className="muted">チャンネル切替:</span>
            {channelsLoading ? <span className="status-chip">読み込み中…</span> : null}
            {channelsError ? <span className="status-chip status-chip--danger">{channelsError}</span> : null}
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              disabled={channelsLoading}
              onClick={() => {
                void reloadWorkspace();
              }}
            >
              再読み込み
            </button>
          </div>
          {sortedChannels.length ? (
            <div className="channel-projects__filters" role="list" aria-label="チャンネル一覧">
              {sortedChannels.map((channel) => {
                const active = channel.code === selectedChannel;
                const displayName = channel.youtube_title ?? channel.name ?? channel.branding?.title ?? channel.code;
                const handle = (channel.youtube_handle ?? channel.branding?.handle ?? "").trim();
                const normalizedHandle = handle ? (handle.startsWith("@") ? handle : `@${handle}`) : null;
                const avatarUrl = (channel.branding?.avatar_url ?? "").trim();
                const tooltipId = `channel-chip-tooltip-${channel.code}`;
                return (
                  <div key={channel.code} className="channel-chip-tooltip" role="listitem">
                    <button
                      type="button"
                      className={`filter-chip${active ? " filter-chip--active" : ""}`}
                      aria-pressed={active}
                      aria-describedby={tooltipId}
                      disabled={channelPickerDisabled}
                      onClick={() => navigateToChannel(channel.code)}
                      title={displayName ?? channel.code}
                    >
                      <span className="filter-chip__label">{channel.code}</span>
                    </button>
                    <div id={tooltipId} role="tooltip" className="channel-chip-tooltip__content">
                      <span className="channel-chip-tooltip__avatar" aria-hidden="true">
                        {avatarUrl ? (
                          <img
                            src={avatarUrl}
                            alt=""
                            loading="lazy"
                            onError={(event) => {
                              event.currentTarget.style.display = "none";
                            }}
                          />
                        ) : null}
                        <span className="channel-chip-tooltip__avatar-fallback">{channel.code}</span>
                      </span>
                      <div className="channel-chip-tooltip__meta">
                        <div className="channel-chip-tooltip__row">
                          <span className="channel-chip-tooltip__code">{channel.code}</span>
                          {displayName && displayName !== channel.code ? (
                            <span className="channel-chip-tooltip__name">{displayName}</span>
                          ) : null}
                        </div>
                        {normalizedHandle ? (
                          <div className="channel-chip-tooltip__handle">{normalizedHandle}</div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="muted small-text" style={{ marginTop: 8 }}>
              チャンネル一覧を取得できていません（<code>/api/channels</code>）。ui-backend を起動して{" "}
              <code>npm start</code> を再起動（proxy反映）してください。
            </p>
          )}
        </section>
        <DashboardOverviewPanel
          overview={dashboardOverview}
          loading={dashboardLoading}
          error={dashboardError}
          channels={channels}
          onSelectChannel={navigateToChannel}
          selectedChannel={selectedChannel}
          onFocusAudioBacklog={handleFocusAudioBacklog}
          onFocusNeedsAttention={handleFocusNeedsAttention}
          onReload={() => {
            void reloadWorkspace();
          }}
          title="台本・音声字幕管理"
          titleIcon="🎛️"
          subtitle="既存の案件を俯瞰し、台本・音声・字幕の滞留を可視化します。"
        />
      </section>
    </>
  );
}
