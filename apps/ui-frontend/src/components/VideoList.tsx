import { VideoSummary } from "../api/types";
import { translateStatus } from "../utils/i18n";
import { resolveAudioSubtitleState } from "../utils/video";
import { StageBadge } from "./StageBadge";

interface VideoListProps {
  videos: VideoSummary[];
  selectedVideo: string | null;
  onSelect: (video: string) => void;
}

function formatUpdatedAt(value?: string | null): string {
  if (!value) {
    return "未更新";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

export function VideoList({ videos, selectedVideo, onSelect }: VideoListProps) {
  if (videos.length === 0) {
    return <p className="shell-panel__message">案件が登録されていません。</p>;
  }

  return (
    <ul className="video-card-list">
      {videos.map((video) => {
        const isActive = selectedVideo === video.video;
        const audioState = resolveAudioSubtitleState(video);
        const audioLabel = audioState === "completed" ? "完了" : audioState === "ready" ? "音声生成待ち" : "未準備";
        const audioClass =
          audioState === "completed"
            ? "video-card__ready video-card__ready--done"
            : audioState === "ready"
              ? "video-card__ready video-card__ready--ok"
              : "video-card__ready video-card__ready--pending";
        return (
          <li key={video.video}>
            <button
              type="button"
              className={`video-card${isActive ? " video-card--active" : ""}`}
              onClick={() => onSelect(video.video)}
            >
              <div className="video-card__header">
                <span className="video-card__id">#{video.video}</span>
                <span className="video-card__title">{video.title ?? "タイトル未設定"}</span>
              </div>
              <div className="video-card__meta">
                <StageBadge stages={video.stages ?? {}} />
                <span className={`video-card__status`}>
                  {translateStatus(video.status)}
                </span>
                <span className={audioClass}>{audioLabel}</span>
                <span className="video-card__time">最終更新 {formatUpdatedAt(video.updated_at)}</span>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
