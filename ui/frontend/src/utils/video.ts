import { VideoDetail, VideoSummary } from "../api/types";

export type AudioSubtitleState = "completed" | "ready" | "pending";

const COMPLETED_STATUSES = new Set(["completed", "skipped"]);
const FAILURE_STATUSES = new Set(["failed", "blocked"]);

function isCompleted(status?: string | null): boolean {
  return status != null && COMPLETED_STATUSES.has(status);
}

function isFailed(status?: string | null): boolean {
  return status != null && FAILURE_STATUSES.has(status);
}

type AudioStateSource =
  | Pick<VideoSummary, "ready_for_audio" | "status" | "stages"> & {
      metadata?: { ready_for_audio?: boolean } | null;
    }
  | Pick<VideoDetail, "ready_for_audio" | "status" | "stages"> & {
      metadata?: { ready_for_audio?: boolean } | null;
    };

export function resolveAudioSubtitleState(video: AudioStateSource): AudioSubtitleState {
  const stages = video.stages ?? {};
  const audioStatus = stages?.audio_synthesis ?? null;
  const subtitleStatus = stages?.srt_generation ?? null;

  const audioCompleted = isCompleted(audioStatus);
  const subtitleCompleted = isCompleted(subtitleStatus);

  if (video.status === "completed" || (audioCompleted && subtitleCompleted)) {
    return "completed";
  }

  if (isFailed(audioStatus) || isFailed(subtitleStatus)) {
    return "pending";
  }

  const metadataReady =
    typeof video.metadata === "object" && video.metadata !== null && typeof video.metadata.ready_for_audio === "boolean"
      ? Boolean(video.metadata.ready_for_audio)
      : false;

  if (video.ready_for_audio || metadataReady) {
    return "ready";
  }

  return "pending";
}
