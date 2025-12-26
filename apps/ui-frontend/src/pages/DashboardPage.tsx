import { useNavigate, useOutletContext } from "react-router-dom";
import { ChannelListSection } from "../components/ChannelListSection";
import type { ShellOutletContext } from "../layouts/AppShell";

export function DashboardPage() {
  const navigate = useNavigate();
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
    if (code) {
      navigate(`/channels/${encodeURIComponent(code)}`);
    }
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
        <div className="main-status" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <span className="status-chip">新規チャンネル追加は「チャンネル設定」から（ハンドルで一意特定）</span>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" className="workspace-button" onClick={() => navigate("/agent-org")}>
              AI Org
            </button>
            <button
              type="button"
              className="workspace-button workspace-button--primary"
              onClick={() => navigate("/channel-settings?add=1")}
            >
              ＋ 新規チャンネル追加
            </button>
          </div>
        </div>

        <div className="dashboard-focus" aria-label="制作フロー I/O">
          <div className="dashboard-focus-card dashboard-focus-card--clickable" onClick={() => navigate("/planning")}>
            <div className="dashboard-focus-card__header">
              <h2>🗂️ 企画CSV</h2>
            </div>
            <div className="dashboard-focus-card__body">
              <p className="dashboard-focus-card__channel">
                SoT: <code>workspaces/planning/channels/CHxx.csv</code>
              </p>
              <p className="dashboard-focus-card__footnote">企画/タイトル/タグ/進捗の正本（チャンネルはページ内で選択）</p>
            </div>
            <button
              type="button"
              className="dashboard-focus-card__action"
              onClick={(event) => {
                event.stopPropagation();
                navigate("/planning");
              }}
            >
              開く
            </button>
          </div>

          <div className="dashboard-focus-card dashboard-focus-card--clickable" onClick={() => navigate("/projects")}>
            <div className="dashboard-focus-card__header">
              <h2>📝 台本作成</h2>
            </div>
            <div className="dashboard-focus-card__body">
              <p className="dashboard-focus-card__channel">
                SoT: <code>workspaces/scripts/{"{CH}"}/{"{NNN}"}/status.json</code>
              </p>
              <p className="dashboard-focus-card__footnote">出力: assembled.md / status.json（チャンネル選択→行選択）</p>
            </div>
            <button
              type="button"
              className="dashboard-focus-card__action"
              onClick={(event) => {
                event.stopPropagation();
                navigate("/projects");
              }}
            >
              開く
            </button>
          </div>

          <div className="dashboard-focus-card dashboard-focus-card--clickable" onClick={() => navigate("/audio-tts")}>
            <div className="dashboard-focus-card__header">
              <h2>🔊 音声生成</h2>
            </div>
            <div className="dashboard-focus-card__body">
              <p className="dashboard-focus-card__channel">
                SoT: <code>workspaces/audio/final/{"{CH}"}/{"{NNN}"}/</code>
              </p>
              <p className="dashboard-focus-card__footnote">下流は final の WAV/SRT だけ参照（ページ内でチャンネル選択）</p>
            </div>
            <button
              type="button"
              className="dashboard-focus-card__action"
              onClick={(event) => {
                event.stopPropagation();
                navigate("/audio-tts");
              }}
            >
              開く
            </button>
          </div>

          <div className="dashboard-focus-card dashboard-focus-card--clickable" onClick={() => navigate("/capcut-edit")}>
            <div className="dashboard-focus-card__header">
              <h2>🎬 動画（CapCut）</h2>
            </div>
            <div className="dashboard-focus-card__body">
              <p className="dashboard-focus-card__channel">
                SoT: <code>workspaces/video/runs/{"{run_id}"}/</code>
              </p>
              <p className="dashboard-focus-card__footnote">入力: final SRT / 出力: images + capcut_draft</p>
            </div>
            <button
              type="button"
              className="dashboard-focus-card__action"
              onClick={(event) => {
                event.stopPropagation();
                navigate("/capcut-edit");
              }}
            >
              開く
            </button>
          </div>

          <div className="dashboard-focus-card dashboard-focus-card--clickable" onClick={() => navigate("/thumbnails")}>
            <div className="dashboard-focus-card__header">
              <h2>🖼️ サムネ</h2>
            </div>
            <div className="dashboard-focus-card__body">
              <p className="dashboard-focus-card__channel">
                SoT: <code>workspaces/thumbnails/projects.json</code>
              </p>
              <p className="dashboard-focus-card__footnote">案の管理・割当・反映（チャンネル別フィルタはUIで操作）</p>
            </div>
            <button
              type="button"
              className="dashboard-focus-card__action"
              onClick={(event) => {
                event.stopPropagation();
                navigate("/thumbnails");
              }}
            >
              開く
            </button>
          </div>
        </div>

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
