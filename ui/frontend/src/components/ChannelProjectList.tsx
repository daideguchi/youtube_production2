import type { KeyboardEvent } from "react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { VideoSummary } from "../api/types";
import { translateStatus, translateStage } from "../utils/i18n";
import { resolveAudioSubtitleState } from "../utils/video";
import { pickCurrentStage, resolveStageStatus } from "./StageProgress";

type ReadyFilterOption = "all" | "ready" | "not_ready";
type SummaryFilterOption = "blocked" | "review" | "pendingAudio" | null;

interface ChannelProjectListProps {
  channelCode: string | null;
  videos: VideoSummary[];
  filteredVideos: VideoSummary[];
  selectedVideo: string | null;
  keyword: string;
  readyFilter: ReadyFilterOption;
  summaryFilter: SummaryFilterOption;
  onKeywordChange: (value: string) => void;
  onReadyFilterChange: (value: ReadyFilterOption) => void;
  onSummaryFilterChange: (value: SummaryFilterOption) => void;
  onClearSummaryFilter: () => void;
  onSelectVideo: (video: string) => void;
  onOpenScript: (video: string) => void;
  onOpenAudio: (video: string) => void;
}

function formatDate(value?: string | null): string {
  if (!value) {
    return "未更新";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

export function ChannelProjectList({
  channelCode,
  videos,
  filteredVideos,
  selectedVideo,
  keyword,
  readyFilter,
  summaryFilter,
  onKeywordChange,
  onReadyFilterChange,
  onSummaryFilterChange,
  onClearSummaryFilter,
  onSelectVideo,
  onOpenScript,
  onOpenAudio,
}: ChannelProjectListProps) {
  const navigate = useNavigate();
  const totals = useMemo(() => ({
    total: videos.length,
    filtered: filteredVideos.length,
  }), [filteredVideos.length, videos.length]);

  const summaryCounts = useMemo(() => {
    let ready = 0;
    let notReady = 0;
    let blocked = 0;
    let review = 0;
    for (const video of videos) {
      const audioState = resolveAudioSubtitleState(video);
      if (audioState === "pending") {
        notReady += 1;
      } else {
        ready += 1;
      }
      const stageStatuses = Object.values(video.stages ?? {});
      if (stageStatuses.some((status) => status === "blocked")) {
        blocked += 1;
      }
      if (stageStatuses.some((status) => status === "review")) {
        review += 1;
      }
    }
    return {
      total: videos.length,
      ready,
      notReady,
      blocked,
      review,
    };
  }, [videos]);

  const navigateToDetail = (videoId: string, options?: { tab?: string }) => {
    if (!channelCode) {
      return;
    }
    const params = new URLSearchParams();
    if (options?.tab) {
      params.set("tab", options.tab);
    }
    const query = params.toString();
    navigate(
      `/channels/${encodeURIComponent(channelCode)}/videos/${encodeURIComponent(videoId)}${query ? `?${query}` : ""}`
    );
  };

  const handleRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, videoId: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectVideo(videoId);
      navigateToDetail(videoId);
    }
  };

  return (
    <section className="channel-projects">
      <header className="channel-projects__header">
        <div>
          <h2>案件一覧</h2>
          <p className="muted">
            {totals.filtered === totals.total
              ? `全 ${totals.total} 件`
              : `${totals.total} 件中 ${totals.filtered} 件を表示`}
          </p>
        </div>
        <div className="channel-projects__actions">
          <input
            type="search"
            className="channel-projects__search"
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="タイトル・番号・ステータスで検索"
          />
        </div>
      </header>

      <div className="channel-projects__filters" role="group" aria-label="フィルター">
        <button
          type="button"
          className={`filter-chip${readyFilter === "all" && summaryFilter === null ? " filter-chip--active" : ""}`}
          onClick={() => {
            onReadyFilterChange("all");
            onSummaryFilterChange(null);
          }}
        >
          <span className="filter-chip__label">すべて</span>
          <span className="filter-chip__count">{summaryCounts.total}</span>
        </button>
        <button
          type="button"
          className={`filter-chip${readyFilter === "ready" ? " filter-chip--active" : ""}`}
          onClick={() => {
            onReadyFilterChange("ready");
            onSummaryFilterChange(null);
          }}
        >
          <span className="filter-chip__label">音声・字幕準備済</span>
          <span className="filter-chip__count">{summaryCounts.ready}</span>
        </button>
        <button
          type="button"
          className={`filter-chip${readyFilter === "not_ready" ? " filter-chip--active" : ""}`}
          onClick={() => {
            onReadyFilterChange("not_ready");
            onSummaryFilterChange("pendingAudio");
          }}
        >
          <span className="filter-chip__label">音声・字幕未準備</span>
          <span className="filter-chip__count">{summaryCounts.notReady}</span>
        </button>
        <button
          type="button"
          className={`filter-chip${summaryFilter === "blocked" ? " filter-chip--active" : ""}`}
          onClick={() => onSummaryFilterChange(summaryFilter === "blocked" ? null : "blocked")}
        >
          <span className="filter-chip__label">要対応</span>
          <span className="filter-chip__count">{summaryCounts.blocked}</span>
        </button>
        <button
          type="button"
          className={`filter-chip${summaryFilter === "review" ? " filter-chip--active" : ""}`}
          onClick={() => onSummaryFilterChange(summaryFilter === "review" ? null : "review")}
        >
          <span className="filter-chip__label">検証待ち</span>
          <span className="filter-chip__count">{summaryCounts.review}</span>
        </button>
        {(readyFilter !== "all" || summaryFilter !== null) && (
          <button type="button" className="filter-chip filter-chip--clear" onClick={onClearSummaryFilter}>
            フィルター解除
          </button>
        )}
      </div>

      <div className="channel-projects__table-wrapper">
        <table className="channel-projects__table">
          <thead>
            <tr>
              <th scope="col">番号</th>
              <th scope="col">タイトル</th>
              <th scope="col">工程状況</th>
              <th scope="col">状態</th>
              <th scope="col">文字数</th>
              <th scope="col">最終更新</th>
              <th scope="col" className="channel-projects__actions-column">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {filteredVideos.length === 0 ? (
              <tr>
                <td colSpan={7} className="channel-projects__empty">
                  条件に一致する案件はありません。
                </td>
              </tr>
            ) : (
              filteredVideos.map((video) => {
                const isSelected = selectedVideo === video.video;
                const audioState = resolveAudioSubtitleState(video);
                const derivedStatus = audioState === "completed" ? "completed" : video.status ?? "unknown";
                const statusLabel =
                  audioState === "completed"
                    ? "台本・音声・字幕 完了"
                    : audioState === "ready"
                      ? "台本チェック済み（音声待ち）"
                      : translateStatus(derivedStatus);
                const charNumber =
                  typeof video.character_count === "number" && Number.isFinite(video.character_count)
                    ? video.character_count
                    : null;
                const charLabel = charNumber !== null ? `${charNumber} 文字` : "—";
                return (
                  <tr
                    key={video.video}
                    className={`channel-projects__row${isSelected ? " channel-projects__row--active" : ""}`}
                    onClick={() => {
                      onSelectVideo(video.video);
                      navigateToDetail(video.video);
                    }}
                    onKeyDown={(event) => handleRowKeyDown(event, video.video)}
                    tabIndex={0}
                  >
                    <th scope="row">{video.video}</th>
                    <td>
                      <div className="channel-projects__title">{video.title ?? "タイトル未設定"}</div>
                      <div className="channel-projects__meta">{formatDate(video.updated_at)}</div>
                    </td>
                    <td>
                      <div className="status-cell">
                        {(() => {
                          const currentStage = pickCurrentStage(video.stages ?? {});
                          if (!currentStage) {
                            return (
                              <>
                                <span className="status-badge status-badge--completed">全工程完了</span>
                                <div className="status-cell__hint">音声・字幕まで完了</div>
                              </>
                            );
                          }
                          const stageStatus = resolveStageStatus(currentStage, video.stages ?? {});
                          const stageLabel = translateStage(currentStage);
                          return (
                            <>
                              <span className={`status-badge status-badge--${stageStatus ?? "default"}`}>
                                {stageLabel}
                              </span>
                              <div className="status-cell__hint">{translateStatus(stageStatus)}</div>
                            </>
                          );
                        })()}
                      </div>
                    </td>
                    <td>
                      <div className="status-cell">
                        <span className={`status-badge status-badge--${video.status ?? "default"}`}>{statusLabel}</span>
                      </div>
                    </td>
                    <td>{charLabel}</td>
                    <td>{formatDate(video.updated_at)}</td>
                    <td className="channel-projects__actions-cell">
                      <button
                        type="button"
                        className="link-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenScript(video.video);
                          navigateToDetail(video.video, { tab: "script" });
                        }}
                      >
                        台本を開く
                      </button>
                      <button
                        type="button"
                        className="link-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenAudio(video.video);
                          navigateToDetail(video.video, { tab: "audio" });
                        }}
                      >
                        音声・字幕
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
