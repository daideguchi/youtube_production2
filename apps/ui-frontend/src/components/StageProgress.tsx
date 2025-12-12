import { STAGE_LABELS, STAGE_ORDER, STATUS_COLORS, translateStatus } from "../utils/i18n";

export interface StageProgressProps {
  stages: Record<string, string>;
}

export function resolveStageStatus(stageKey: string, stages: Record<string, string>): string {
  const raw = stages[stageKey];
  if (!raw) {
    return "pending";
  }
  return raw;
}

export function pickCurrentStage(stages: Record<string, string>): string | null {
  // 音声と字幕が完了していれば前段のステージは完了として扱う
  const audioDone = resolveStageStatus("audio_synthesis", stages) === "completed";
  const srtDone = resolveStageStatus("srt_generation", stages) === "completed";
  if (audioDone && srtDone) {
    return null;
  }
  for (const stage of STAGE_ORDER) {
    const status = resolveStageStatus(stage, stages);
    if (status !== "completed" && status !== "skipped") {
      return stage;
    }
  }
  return null;
}

export function StageProgress({ stages }: StageProgressProps) {
  const currentStage = pickCurrentStage(stages);

  return (
    <div className="stage-progress">
      {STAGE_ORDER.map((stageKey) => {
        const status = resolveStageStatus(stageKey, stages);
        const color = STATUS_COLORS[status] ?? STATUS_COLORS.unknown;
        const isCurrent = currentStage === stageKey;
        return (
          <div key={stageKey} className="stage-progress__item">
            <span
              className={isCurrent ? "stage-progress__dot stage-progress__dot--current" : "stage-progress__dot"}
              style={{ backgroundColor: color }}
            />
            <div className="stage-progress__labels">
              <span className="stage-progress__stage">{STAGE_LABELS[stageKey] ?? stageKey}</span>
              <span className="stage-progress__status">{translateStatus(status)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
