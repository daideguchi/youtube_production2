import { useCallback, useMemo } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { VideoDetailPanel } from "../components/VideoDetailPanel";
import { InstructionPanel } from "../components/InstructionPanel";
import { ActivityLog } from "../components/ActivityLog";
import type { ShellOutletContext } from "../layouts/AppShell";
import type { VideoSummary } from "../api/types";

function videoSortKey(videoId: string): number {
  const trimmed = (videoId ?? "").trim();
  if (!trimmed) {
    return Number.POSITIVE_INFINITY;
  }
  if (/^\d+$/.test(trimmed)) {
    try {
      return Number(trimmed);
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }
  return Number.POSITIVE_INFINITY;
}

function sortVideosByNumber(videos: VideoSummary[]): VideoSummary[] {
  return [...videos].sort((a, b) => {
    const diff = videoSortKey(a.video) - videoSortKey(b.video);
    if (diff !== 0) {
      return diff;
    }
    return a.video.localeCompare(b.video);
  });
}

export function ChannelDetailPage() {
  const navigate = useNavigate();
  const {
    channels,
    channelsLoading,
    channelsError,
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
    selectChannel,
    navigateToChannel,
    detailError,
    detailLoading,
    shouldShowDetailPanel,
    detailHandlers,
    videoDetail,
    detailTab,
    setDetailTab,
    hasUnsavedChanges,
    setHasUnsavedChanges,
    activityItems,
    videos,
  } = useOutletContext<ShellOutletContext>();

  const orderedVideos = useMemo(() => sortVideosByNumber(videos), [videos]);
  const sortedChannels = useMemo(
    () => [...channels].sort((a, b) => a.code.localeCompare(b.code)),
    [channels]
  );
  const channelPickerDisabled = channelsLoading || Boolean(channelsError);

  const handleSelectChannelFromTop = useCallback(
    (code: string) => {
      if (!code) {
        return;
      }
      if (code === selectedChannel) {
        return;
      }
      if (hasUnsavedChanges) {
        const ok = window.confirm("未保存の変更があります。このまま別チャンネルへ移動しますか？");
        if (!ok) {
          return;
        }
        setHasUnsavedChanges(false);
      }
      selectChannel(code);
    },
    [hasUnsavedChanges, selectChannel, selectedChannel, setHasUnsavedChanges]
  );

  const { previousVideo, nextVideo, positionLabel } = useMemo(() => {
    const currentVideo = videoDetail?.video ?? null;
    if (!currentVideo || orderedVideos.length === 0) {
      return { previousVideo: null, nextVideo: null, positionLabel: null as string | null };
    }
    const index = orderedVideos.findIndex((item) => item.video === currentVideo);
    if (index < 0) {
      return { previousVideo: null, nextVideo: null, positionLabel: null as string | null };
    }
    const previous = index > 0 ? orderedVideos[index - 1] : null;
    const next = index < orderedVideos.length - 1 ? orderedVideos[index + 1] : null;
    return {
      previousVideo: previous,
      nextVideo: next,
      positionLabel: `${index + 1} / ${orderedVideos.length}`,
    };
  }, [orderedVideos, videoDetail?.video]);

  const handleNavigateVideo = useCallback(
    (targetVideo: string) => {
      if (!selectedChannel) {
        return;
      }
      if (hasUnsavedChanges) {
        const ok = window.confirm("未保存の変更があります。このまま別の台本へ移動しますか？");
        if (!ok) {
          return;
        }
        setHasUnsavedChanges(false);
      }
      const params = new URLSearchParams();
      if (detailTab !== "script") {
        params.set("tab", detailTab);
      }
      const query = params.toString();
      navigate(`/channels/${encodeURIComponent(selectedChannel)}/videos/${encodeURIComponent(targetVideo)}${query ? `?${query}` : ""}`);
    },
    [detailTab, hasUnsavedChanges, navigate, selectedChannel, setHasUnsavedChanges]
  );

  return (
    <>
      {hasUnsavedChanges ? (
        <div className="main-status">
          <span className="status-chip status-chip--warning">未保存の変更あり</span>
        </div>
      ) : null}
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
                      onClick={() => handleSelectChannelFromTop(channel.code)}
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

        {selectedChannel && selectedChannelSummary && selectedChannelSnapshot ? (
          <ChannelOverviewPanel
            channel={selectedChannelSummary}
            snapshot={selectedChannelSnapshot}
            onBackToDashboard={() => navigateToChannel(selectedChannel)}
            backLabel="⬅ チャンネル一覧へ"
          />
        ) : (
          <div className="shell-panel shell-panel--placeholder">
            <h2>チャンネル情報を取得できませんでした</h2>
            <p className="shell-panel__subtitle">サイドバーから別のチャンネルを選択するか、ダッシュボードへ戻ってください。</p>
          </div>
        )}
      </section>
      <section className="main-content">
        {detailError ? <div className="main-alert main-alert--error">{detailError}</div> : null}
        <div className="detail-pane">
          {detailLoading ? (
            <div className="detail-pane__overlay" role="status" aria-live="polite">
              <span className="detail-pane__spinner" aria-hidden />
              <span>詳細を読み込み中です…</span>
            </div>
          ) : null}
          {shouldShowDetailPanel && detailHandlers && videoDetail ? (
            <VideoDetailPanel
              detail={videoDetail}
              refreshing={detailLoading}
              previousVideo={previousVideo ? { video: previousVideo.video, title: previousVideo.title ?? null } : null}
              nextVideo={nextVideo ? { video: nextVideo.video, title: nextVideo.title ?? null } : null}
              positionLabel={positionLabel}
              onNavigateVideo={handleNavigateVideo}
              onSaveAssembled={detailHandlers.onSaveAssembled}
              onSaveTts={detailHandlers.onSaveTts}
              onValidateTts={detailHandlers.onValidateTts}
              onSaveSrt={detailHandlers.onSaveSrt}
              onVerifySrt={detailHandlers.onVerifySrt}
              onUpdateStatus={detailHandlers.onUpdateStatus}
              onUpdateReady={detailHandlers.onUpdateReady}
              onUpdateStages={detailHandlers.onUpdateStages}
              onReplaceTts={detailHandlers.onReplaceTts}
              onDirtyChange={setHasUnsavedChanges}
              activeTab={detailTab}
              onTabChange={setDetailTab}
            />
          ) : (
            <InstructionPanel />
          )}
        </div>
      </section>
      <section className="main-content main-content--log">
        <ActivityLog items={activityItems} />
      </section>
    </>
  );
}
