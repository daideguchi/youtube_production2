import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchWorkflowPrecheck } from "../api/client";
import type {
  WorkflowPrecheckPendingSummary,
  WorkflowPrecheckReadyEntry,
  WorkflowPrecheckResponse,
} from "../api/types";

const MAX_READY_DISPLAY = 8;

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

function PendingChannelCard({
  summary,
  highlight,
}: {
  summary: WorkflowPrecheckPendingSummary;
  highlight: boolean;
}) {
  return (
    <div
      className={
        "workflow-precheck-panel__card" +
        (highlight ? " workflow-precheck-panel__card--highlight" : "")
      }
    >
      <div className="workflow-precheck-panel__card-header">
        <span className="workflow-precheck-panel__channel-code">{summary.channel}</span>
        <span className="workflow-precheck-panel__count">{summary.count}</span>
      </div>
      {summary.count === 0 ? (
        <p className="workflow-precheck-panel__empty">pending はありません。</p>
      ) : (
        <ul className="workflow-precheck-panel__list">
          {summary.items.map((item) => (
            <li key={`${summary.channel}-${item.video_number}-${item.script_id}`}>
              <div className="workflow-precheck-panel__list-label">
                {item.script_id}
              </div>
              <div className="workflow-precheck-panel__list-meta">
                {item.progress ?? "未設定"}
              </div>
              {item.title ? (
                <div className="workflow-precheck-panel__list-title">{item.title}</div>
              ) : null}
            </li>
          ))}
          {summary.count > summary.items.length ? (
            <li className="workflow-precheck-panel__list-more">
              +{summary.count - summary.items.length} more
            </li>
          ) : null}
        </ul>
      )}
    </div>
  );
}

function ReadyList({
  entries,
  highlightChannel,
}: {
  entries: WorkflowPrecheckReadyEntry[];
  highlightChannel: string | null;
}) {
  if (!entries.length) {
    return <p className="workflow-precheck-panel__empty">ready_for_audio は検出されていません。</p>;
  }
  return (
    <ul className="workflow-precheck-panel__ready-list">
      {entries.slice(0, MAX_READY_DISPLAY).map((entry) => (
        <li
          key={`${entry.channel}-${entry.video_number}`}
          className={
            highlightChannel && entry.channel === highlightChannel
              ? "workflow-precheck-panel__ready-item workflow-precheck-panel__ready-item--highlight"
              : "workflow-precheck-panel__ready-item"
          }
        >
          <span className="workflow-precheck-panel__ready-channel">{entry.channel}</span>
          <span className="workflow-precheck-panel__ready-video">{entry.video_number}</span>
          <span className="workflow-precheck-panel__ready-audio">{entry.audio_status ?? "pending"}</span>
        </li>
      ))}
      {entries.length > MAX_READY_DISPLAY ? (
        <li className="workflow-precheck-panel__list-more">
          +{entries.length - MAX_READY_DISPLAY} more ready jobs
        </li>
      ) : null}
    </ul>
  );
}

export function WorkflowPrecheckPanel({ selectedChannel }: { selectedChannel: string | null }) {
  const [data, setData] = useState<WorkflowPrecheckResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchWorkflowPrecheck(undefined, 5);
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const pending = data?.pending ?? [];
  const ready = data?.ready ?? [];
  const generatedAt = useMemo(() => formatTimestamp(data?.generated_at), [data]);

  return (
    <section className="workflow-precheck-panel">
      <div className="workflow-precheck-panel__header">
        <div>
          <h2>Audio Precheck</h2>
          <p className="workflow-precheck-panel__subtitle">
            Stage1〜9 の pending と ready_for_audio を一覧表示します。音声処理を開始する前にここを確認してください。
          </p>
        </div>
        <div className="workflow-precheck-panel__actions">
          {generatedAt ? <span className="workflow-precheck-panel__timestamp">更新: {generatedAt}</span> : null}
          <button type="button" className="button" onClick={load} disabled={loading}>
            {loading ? "更新中…" : "再取得"}
          </button>
        </div>
      </div>
      {error ? <p className="workflow-precheck-panel__error">{error}</p> : null}
      <div className="workflow-precheck-panel__body">
        <div className="workflow-precheck-panel__pending-grid">
          {pending.map((summary) => (
            <PendingChannelCard
              key={summary.channel}
              summary={summary}
              highlight={!!selectedChannel && summary.channel === selectedChannel}
            />
          ))}
        </div>
        <div className="workflow-precheck-panel__ready">
          <h3>ready_for_audio（未処理）</h3>
          <ReadyList entries={ready} highlightChannel={selectedChannel} />
        </div>
      </div>
    </section>
  );
}
