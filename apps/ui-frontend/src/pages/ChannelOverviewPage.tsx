import { useOutletContext } from "react-router-dom";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { ChannelProjectList } from "../components/ChannelProjectList";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ChannelOverviewPage() {
  const {
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
    videos,
    filteredVideos,
    selectedVideo,
    selectChannel,
    videoKeyword,
    readyFilter,
    summaryFilter,
    setVideoKeyword,
    setReadyFilter,
    applySummaryFilter,
    clearSummaryFilter,
    selectVideo,
    openScript,
    openAudio,
  } = useOutletContext<ShellOutletContext>();

  if (!selectedChannel || !selectedChannelSummary || !selectedChannelSnapshot) {
    return (
      <section className="main-content main-content--channel">
        <div className="shell-panel shell-panel--placeholder">
          <h2>チャンネルを選択してください</h2>
          <p className="shell-panel__subtitle">サイドバーからチャンネルを選ぶと案件一覧が表示されます。</p>
        </div>
      </section>
    );
  }

  return (
    <section className="main-content main-content--channel">
      <ChannelOverviewPanel
        channel={selectedChannelSummary}
        snapshot={selectedChannelSnapshot}
        onBackToDashboard={() => selectChannel(null)}
      />
      <ChannelProjectList
        channelCode={selectedChannel}
        videos={videos}
        filteredVideos={filteredVideos}
        selectedVideo={selectedVideo}
        keyword={videoKeyword}
        readyFilter={readyFilter}
        summaryFilter={summaryFilter}
        onKeywordChange={setVideoKeyword}
        onReadyFilterChange={setReadyFilter}
        onSummaryFilterChange={applySummaryFilter}
        onClearSummaryFilter={clearSummaryFilter}
        onSelectVideo={selectVideo}
        onOpenScript={openScript}
        onOpenAudio={openAudio}
      />
    </section>
  );
}
