import type { KeyboardEvent } from "react";
import { useMemo } from "react";
import { VideoSummary } from "../api/types";
import { translateStatus, translateStage } from "../utils/i18n";
import { normalizeStageStatusKey } from "../utils/stage";
import { resolveAudioSubtitleState } from "../utils/video";
import { pickCurrentStage, resolveStageStatus } from "./StageProgress";
import { StageCompactIndicator } from "./StageCompactIndicator";

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

  const handleRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, videoId: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectVideo(videoId);
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
	              <th
	                scope="col"
	                title="音声=音声合成 / 字幕=字幕生成 / TL=タイムライン反映 / 画P=画像プロンプト / 画像=画像生成 / サム=サムネ作成 / 承認=サムネQC（色: 灰=未着手, 青=進行中, 緑=完了, 黄=レビュー, 赤=要対応）"
	              >
	                工程状況
	              </th>
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
                const publishedLocked = Boolean(video.published_lock);
	                const audioState = resolveAudioSubtitleState(video);
	                const audioStageStatus = resolveStageStatus("audio_synthesis", video.stages ?? {});
	                const subtitleStageStatus = resolveStageStatus("srt_generation", video.stages ?? {});
	                const timelineStageStatus = resolveStageStatus("timeline_copy", video.stages ?? {});
	                const derivedStatus = publishedLocked ? "published" : audioState === "completed" ? "completed" : video.status ?? "unknown";
	                const statusLabel =
	                  publishedLocked
	                    ? "投稿済み（ロック）"
	                    : audioState === "completed"
                    ? "台本・音声・字幕 完了"
                    : audioState === "ready"
                      ? "台本チェック済み（音声待ち）"
                      : translateStatus(derivedStatus);
                const charNumber =
                  typeof video.character_count === "number" && Number.isFinite(video.character_count)
                    ? video.character_count
                    : 0;
                const charLabel = `${charNumber.toLocaleString("ja-JP")} 文字`;
                return (
                  <tr
                    key={video.video}
                    className={`channel-projects__row${isSelected ? " channel-projects__row--active" : ""}`}
                    onClick={() => {
                      onSelectVideo(video.video);
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
                      <div className="stage-inline">
                        {(() => {
                          const currentStage = pickCurrentStage(video.stages ?? {});
                          if (!currentStage) {
                            return (
                              <>
                                <span className="status-badge status-badge--completed">音声・字幕完了</span>
                              </>
                            );
                          }
                          const stageStatus = resolveStageStatus(currentStage, video.stages ?? {});
                          const stageLabel = translateStage(currentStage);
                          const stageStatusKey = normalizeStageStatusKey(stageStatus);
                          const stageStatusLabel =
                            stageStatusKey === "unknown" ? translateStatus(stageStatus) : translateStatus(stageStatusKey);
                          return (
                            <>
                              <span
                                className={`status-badge status-badge--${stageStatusKey}`}
                                title={stageStatusLabel}
                              >
                                {stageLabel}
                              </span>
                            </>
                          );
                        })()}
                        {(() => {
                          const thumbProgress = video.thumbnail_progress ?? null;
                          const imageProgress = video.video_images_progress ?? null;

                          const cueCount =
                            typeof imageProgress?.cue_count === "number" && Number.isFinite(imageProgress.cue_count)
                              ? imageProgress.cue_count
                              : null;
                          const promptCount =
                            typeof imageProgress?.prompt_count === "number" && Number.isFinite(imageProgress.prompt_count)
                              ? imageProgress.prompt_count
                              : null;
                          const imagesCount =
                            typeof imageProgress?.images_count === "number" && Number.isFinite(imageProgress.images_count)
                              ? imageProgress.images_count
                              : 0;
                          const promptReadyAt = imageProgress?.prompt_ready_at ?? null;
                          const imagesUpdatedAt = imageProgress?.images_updated_at ?? null;

                          const imagePromptStatus = imageProgress?.prompt_ready
                            ? "completed"
                            : promptReadyAt && (cueCount ?? 0) > 0
                              ? "in_progress"
                              : "pending";
                          const imagesStatus =
                            imagesCount > 0 ? ((cueCount ?? 0) > 0 && imagesCount >= (cueCount ?? 0) ? "completed" : "in_progress") : "pending";
                          const thumbnailCreatedStatus = thumbProgress?.created ? "completed" : "pending";
                          const thumbnailQcStatus = thumbProgress?.qc_cleared
                            ? "completed"
                            : thumbProgress?.created
                              ? "review"
                              : "pending";

                          const imagePromptTitleParts = [
                            promptCount !== null && cueCount !== null ? `${promptCount}/${cueCount}` : null,
                            promptReadyAt,
                            imageProgress?.run_id ? `run=${imageProgress.run_id}` : null,
                          ].filter(Boolean);
                          const imagesTitleParts = [
                            cueCount !== null ? `${imagesCount}/${cueCount}` : `${imagesCount}枚`,
                            imagesUpdatedAt,
                            imageProgress?.run_id ? `run=${imageProgress.run_id}` : null,
                          ].filter(Boolean);
                          const thumbTitleParts = [
                            thumbProgress?.variant_count ? `${thumbProgress.variant_count}枚` : null,
                            thumbProgress?.created_at ?? null,
                          ].filter(Boolean);
                          const thumbQcTitleParts = [
                            thumbProgress?.status ? `status=${thumbProgress.status}` : null,
                            thumbProgress?.qc_cleared_at ?? null,
                          ].filter(Boolean);

                          return (
                            <StageCompactIndicator
                              items={[
                                { key: "audio", label: "音声合成", short: "音声", status: audioStageStatus },
                                { key: "subtitle", label: "字幕生成", short: "字幕", status: subtitleStageStatus },
                                { key: "timeline", label: "タイムライン反映", short: "TL", status: timelineStageStatus },
                                {
                                  key: "imagePrompt",
                                  label: "画像プロンプト",
                                  short: "画P",
                                  status: imagePromptStatus,
                                  title: `画像プロンプト: ${translateStatus(imagePromptStatus)}${
                                    imagePromptTitleParts.length ? ` / ${imagePromptTitleParts.join(" / ")}` : ""
                                  }`,
                                },
                                {
                                  key: "images",
                                  label: "画像生成",
                                  short: "画像",
                                  status: imagesStatus,
                                  title: `画像生成: ${translateStatus(imagesStatus)}${
                                    imagesTitleParts.length ? ` / ${imagesTitleParts.join(" / ")}` : ""
                                  }`,
                                },
                                {
                                  key: "thumb",
                                  label: "サムネ作成",
                                  short: "サム",
                                  status: thumbnailCreatedStatus,
                                  title: `サムネ作成: ${translateStatus(thumbnailCreatedStatus)}${
                                    thumbTitleParts.length ? ` / ${thumbTitleParts.join(" / ")}` : ""
                                  }`,
                                },
                                {
                                  key: "thumbQc",
                                  label: "サムネQC",
                                  short: "承認",
                                  status: thumbnailQcStatus,
                                  title: `サムネQC: ${translateStatus(thumbnailQcStatus)}${
                                    thumbQcTitleParts.length ? ` / ${thumbQcTitleParts.join(" / ")}` : ""
                                  }`,
                                },
                              ]}
                            />
                          );
                        })()}
                      </div>
                    </td>
                    <td>
                      <div className="status-cell">
                        <span className={`status-badge status-badge--${derivedStatus ?? "default"}`}>{statusLabel}</span>
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
