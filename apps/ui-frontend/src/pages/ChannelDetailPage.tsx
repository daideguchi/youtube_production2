import { useOutletContext } from "react-router-dom";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { VideoDetailPanel } from "../components/VideoDetailPanel";
import { InstructionPanel } from "../components/InstructionPanel";
import { ActivityLog } from "../components/ActivityLog";
import type { ShellOutletContext } from "../layouts/AppShell";

export function ChannelDetailPage() {
  const {
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
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
  } = useOutletContext<ShellOutletContext>();

  return (
    <>
      {hasUnsavedChanges ? (
        <div className="main-status">
          <span className="status-chip status-chip--warning">未保存の変更あり</span>
        </div>
      ) : null}
      <section className="main-content main-content--channel">
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
