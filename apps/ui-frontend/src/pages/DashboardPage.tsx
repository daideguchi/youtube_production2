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

  const selectedChannelParam = selectedChannel ? encodeURIComponent(selectedChannel) : null;

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
            <button type="button" className="workspace-button" onClick={() => navigate("/ssot")}>
              SSOT（read-only）
            </button>
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

        <div className="dashboard-flow" aria-label="制作フロー I/O">
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate(selectedChannelParam ? `/planning?channel=${selectedChannelParam}` : "/planning")}
            title={`SoT: workspaces/planning/channels/CHxx.csv\n企画/タイトル/タグ/進捗の正本（チャンネルはページ内で選択）`}
          >
            🗂️ 企画CSV
          </button>
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate(selectedChannelParam ? `/projects?channel=${selectedChannelParam}` : "/projects")}
            title={`SoT: workspaces/scripts/{CH}/{NNN}/status.json\n出力: assembled.md / status.json（チャンネル選択→行選択）`}
          >
            📝 台本作成
          </button>
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate(selectedChannelParam ? `/audio-tts?channel=${selectedChannelParam}` : "/audio-tts")}
            title={`SoT: workspaces/audio/final/{CH}/{NNN}/\n下流は final の WAV/SRT だけ参照（ページ内でチャンネル選択）`}
          >
            🔊 音声生成
          </button>
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate("/capcut-edit")}
            title={`SoT: workspaces/video/runs/{run_id}/\n入力: final SRT / 出力: images + capcut_draft`}
          >
            🎬 動画（CapCut）
          </button>
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate(selectedChannelParam ? `/thumbnails?channel=${selectedChannelParam}` : "/thumbnails")}
            title={`SoT: workspaces/thumbnails/projects.json\n案の管理・割当・反映（チャンネル別フィルタはUIで操作）`}
          >
            🖼️ サムネ
          </button>
          <button
            type="button"
            className="action-chip dashboard-flow__chip"
            onClick={() => navigate("/ssot/map")}
            title={`SSOT=UI（read-only）\nFlow/Runbook/Trace をコードから自動生成したカタログで確認します`}
          >
            📌 SSOT Map
          </button>
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
