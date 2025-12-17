import { useNavigate, useOutletContext } from "react-router-dom";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { ChannelProjectList } from "../components/ChannelProjectList";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ChannelOverviewPage() {
  const navigate = useNavigate();
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
