import { ChangeEvent } from "react";
import { VideoSummary, LockMetricSample } from "../api/types";
import { VideoList } from "./VideoList";

type ReadyFilterValue = "all" | "ready" | "not_ready";
type SummaryFilterValue = "blocked" | "review" | "pendingAudio" | null;

interface VideoListSectionProps {
  videos: VideoSummary[];
  filteredVideos: VideoSummary[];
  selectedVideo: string | null;
  loading: boolean;
  error: string | null;
  keyword: string;
  readyFilter: ReadyFilterValue;
  summaryFilter: SummaryFilterValue;
  lockHistory: LockMetricSample[];
  channelName?: string | null;
  channelSummary?: {
    total: number;
    scriptCompleted: number;
    audioCompleted: number;
    subtitleCompleted: number;
    readyForAudio: number;
    audioBacklog: number;
    subtitleBacklog: number;
  } | null;
  onKeywordChange: (value: string) => void;
  onReadyFilterChange: (value: ReadyFilterValue) => void;
  onClearSummaryFilter: () => void;
  onSelectVideo: (video: string) => void;
}

const SUMMARY_FILTER_LABELS: Record<Exclude<SummaryFilterValue, null>, string> = {
  blocked: "要対応",
  review: "レビュー待ち",
  pendingAudio: "音声未準備",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString("ja-JP");
}

export function VideoListSection({
  videos,
  filteredVideos,
  selectedVideo,
  loading,
  error,
  keyword,
  readyFilter,
  summaryFilter,
  lockHistory,
  channelName,
  channelSummary,
  onKeywordChange,
  onReadyFilterChange,
  onClearSummaryFilter,
  onSelectVideo,
}: VideoListSectionProps) {
  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onKeywordChange(event.target.value);
  };

  const handleReadyChange = (event: ChangeEvent<HTMLSelectElement>) => {
    onReadyFilterChange(event.target.value as ReadyFilterValue);
  };

  const summaryFilterLabel = summaryFilter ? SUMMARY_FILTER_LABELS[summaryFilter] : null;

  return (
    <section className="shell-panel shell-panel--sidebar">
      <header className="shell-panel__header">
        <div>
          <h2 className="shell-panel__title">案件一覧</h2>
          <p className="shell-panel__subtitle">
            {channelName ?? "全体"} / {filteredVideos.length} 件表示（全 {videos.length} 件）
          </p>
        </div>
        {summaryFilterLabel ? (
          <button type="button" className="shell-chip" onClick={onClearSummaryFilter}>
            {summaryFilterLabel}のみ表示中<span aria-hidden>×</span>
          </button>
        ) : null}
      </header>

      {channelSummary ? (
        <div className="video-summary-bar" aria-label="チャンネル概要">
          <div className="video-summary-bar__item">
            <span className="video-summary-bar__label">台本完了</span>
            <span className="video-summary-bar__value">
              {channelSummary.scriptCompleted}/{channelSummary.total}
            </span>
          </div>
          <div className="video-summary-bar__item">
            <span className="video-summary-bar__label">音声完了</span>
            <span className="video-summary-bar__value">
              {channelSummary.audioCompleted}/{channelSummary.total}
            </span>
          </div>
          <div className="video-summary-bar__item">
            <span className="video-summary-bar__label">字幕完了</span>
            <span className="video-summary-bar__value">
              {channelSummary.subtitleCompleted}/{channelSummary.total}
            </span>
          </div>
          <div className="video-summary-bar__item video-summary-bar__item--accent">
            <span className="video-summary-bar__label">音声未完</span>
            <span className="video-summary-bar__value">{channelSummary.audioBacklog}</span>
          </div>
          <div className="video-summary-bar__item video-summary-bar__item--accent">
            <span className="video-summary-bar__label">字幕未完</span>
            <span className="video-summary-bar__value">{channelSummary.subtitleBacklog}</span>
          </div>
          <div className="video-summary-bar__item">
            <span className="video-summary-bar__label">音声原稿準備済み</span>
            <span className="video-summary-bar__value">{channelSummary.readyForAudio}</span>
          </div>
        </div>
      ) : null}

      <div className="video-filters">
        <label className="input-with-icon" htmlFor="video-search">
          <span aria-hidden role="img">
            🔍
          </span>
          <input
            id="video-search"
            type="text"
            value={keyword}
            onChange={handleSearchChange}
            placeholder="タイトル・番号・ステータスで検索"
          />
        </label>
        <div className="video-filters__row">
          <label className="video-filters__field" htmlFor="ready-filter">
            <span>音声準備</span>
            <select id="ready-filter" value={readyFilter} onChange={handleReadyChange}>
              <option value="all">すべて</option>
              <option value="ready">準備済みのみ</option>
              <option value="not_ready">未準備のみ</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? <p className="shell-panel__message">動画リストを読み込み中です…</p> : null}
      {error ? <p className="shell-panel__message shell-panel__message--error">取得に失敗しました: {error}</p> : null}

      {!loading && !error && lockHistory.length > 0 ? (
        <div className="lock-card">
          <header className="lock-card__header">
            <span className="lock-card__title">直近のロック競合</span>
          </header>
          <ul className="lock-card__list">
            {lockHistory
              .slice()
              .reverse()
              .slice(0, 4)
              .map((entry) => (
                <li key={entry.timestamp} className="lock-card__item">
                  <span className="lock-card__time">{formatTimestamp(entry.timestamp)}</span>
                  <span className="lock-card__badge">timeout {entry.timeout}</span>
                  {entry.unexpected ? (
                    <span className="lock-card__badge lock-card__badge--danger">unexpected {entry.unexpected}</span>
                  ) : null}
                </li>
              ))}
          </ul>
        </div>
      ) : null}

      {!loading && !error ? (
        filteredVideos.length > 0 ? (
          <div className="video-card-container">
            <VideoList videos={filteredVideos} selectedVideo={selectedVideo} onSelect={onSelectVideo} />
          </div>
        ) : (
          <div className="empty-state">
            <span className="empty-state__icon" aria-hidden>
              🔍
            </span>
            <p className="empty-state__title">条件に一致する案件が見つかりません。</p>
            <p className="empty-state__hint">検索語句やフィルターを見直して再度お試しください。</p>
            <div className="empty-state__actions">
              {summaryFilterLabel ? (
                <button type="button" className="empty-state__button" onClick={onClearSummaryFilter}>
                  絞り込みを解除する
                </button>
              ) : null}
              {keyword && keyword.trim().length > 0 ? (
                <button type="button" className="empty-state__button" onClick={() => onKeywordChange("")}>検索ワードをクリア</button>
              ) : null}
              {readyFilter !== "all" ? (
                <button type="button" className="empty-state__button" onClick={() => onReadyFilterChange("all")}>音声準備フィルターを解除</button>
              ) : null}
            </div>
          </div>
        )
      ) : null}
    </section>
  );
}
