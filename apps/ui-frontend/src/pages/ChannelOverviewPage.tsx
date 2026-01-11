import { useMemo } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { ChannelProjectList } from "../components/ChannelProjectList";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ChannelOverviewPage() {
  const navigate = useNavigate();
  const {
    channels,
    channelsLoading,
    channelsError,
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
    videos,
    filteredVideos,
    selectedVideo,
    selectChannel,
    videoKeyword,
    readyFilter,
    unpublishedOnly,
    summaryFilter,
    setVideoKeyword,
    setReadyFilter,
    setUnpublishedOnly,
    applySummaryFilter,
    clearSummaryFilter,
    selectVideo,
    openScript,
    openAudio,
  } = useOutletContext<ShellOutletContext>();

  const sortedChannels = useMemo(
    () => [...channels].sort((a, b) => a.code.localeCompare(b.code)),
    [channels]
  );
  const channelPickerDisabled = channelsLoading || Boolean(channelsError);

  return (
    <section className="main-content main-content--channel">
      {sortedChannels.length ? (
        <section className="channel-top-switcher" aria-label="チャンネル切替">
          <div className="channel-top-switcher__header">
            <span className="muted">チャンネル切替:</span>
            {channelsLoading ? <span className="status-chip">読み込み中…</span> : null}
            {channelsError ? <span className="status-chip status-chip--danger">{channelsError}</span> : null}
          </div>
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
                    onClick={() => selectChannel(channel.code)}
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
        </section>
      ) : null}

      {!selectedChannel || !selectedChannelSummary || !selectedChannelSnapshot ? (
        <div className="shell-panel shell-panel--placeholder">
          <h2>チャンネルを選択してください</h2>
          <p className="shell-panel__subtitle">サイドバーからチャンネルを選ぶと案件一覧が表示されます。</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
            <button
              type="button"
              className="workspace-button workspace-button--primary"
              onClick={() => navigate("/channel-settings?add=1#channel-register")}
            >
              ＋ 新規チャンネル登録
            </button>
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => navigate("/channel-settings")}
            >
              チャンネル設定を開く
            </button>
          </div>
        </div>
      ) : (
      <ChannelOverviewPanel
        channel={selectedChannelSummary}
        snapshot={selectedChannelSnapshot}
        onBackToDashboard={() => selectChannel(null)}
      />
      )}

      {selectedChannel && selectedChannelSummary && selectedChannelSnapshot ? (
      <ChannelProjectList
        channelCode={selectedChannel}
        videos={videos}
        filteredVideos={filteredVideos}
        selectedVideo={selectedVideo}
        keyword={videoKeyword}
        readyFilter={readyFilter}
        unpublishedOnly={unpublishedOnly}
        summaryFilter={summaryFilter}
        onKeywordChange={setVideoKeyword}
        onReadyFilterChange={setReadyFilter}
        onUnpublishedOnlyChange={setUnpublishedOnly}
        onSummaryFilterChange={applySummaryFilter}
        onClearSummaryFilter={clearSummaryFilter}
        onSelectVideo={selectVideo}
        onOpenScript={openScript}
        onOpenAudio={openAudio}
      />
      ) : null}
    </section>
  );
}
