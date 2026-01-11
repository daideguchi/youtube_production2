import { STAGE_LABELS, STATUS_COLORS, translateStatus } from "../utils/i18n";
import { pickCurrentStage, resolveStageStatus } from "./StageProgress";

interface StageBadgeProps {
  stages: Record<string, string>;
}

const STAGE_GROUPS: {
  key: string;
  label: string;
  stageKeys: string[];
}[] = [
  {
    key: "script",
    label: "台本",
    stageKeys: ["topic_research", "script_outline", "script_master_plan", "chapter_brief", "script_draft", "script_review", "script_validation"],
  },
  { key: "polish", label: "仕上げ", stageKeys: ["script_polish_ai", "script_audio_ai", "script_tts_prepare"] },
  { key: "audio", label: "音声", stageKeys: ["audio_synthesis"] },
  { key: "subtitle", label: "字幕", stageKeys: ["srt_generation"] },
  { key: "finishing", label: "素材", stageKeys: ["timeline_copy", "image_generation"] },
];

function groupStatus(stageKeys: string[], stages: Record<string, string>): string {
  const statuses = stageKeys.map((key) => resolveStageStatus(key, stages));
  if (statuses.every((s) => s === "completed" || s === "skipped")) return "completed";
  if (statuses.some((s) => s === "blocked")) return "blocked";
  if (statuses.some((s) => s === "in_progress")) return "in_progress";
  if (statuses.some((s) => s === "review")) return "review";
  if (statuses.some((s) => s === "pending")) return "pending";
  return "pending";
}

function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith("#")) {
    return color;
  }
  const hex = color.slice(1);
  const normalize = (value: string) =>
    value.length === 1 ? parseInt(value.repeat(2), 16) : parseInt(value, 16);
  if (hex.length !== 3 && hex.length !== 6) {
    return color;
  }
  const r = normalize(hex.length === 3 ? hex[0]! : hex.slice(0, 2));
  const g = normalize(hex.length === 3 ? hex[1]! : hex.slice(2, 4));
  const b = normalize(hex.length === 3 ? hex[2]! : hex.slice(4, 6));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function getBadgeInfo(stages: Record<string, string>): { label: string; status: string; color: string } {
  const current = pickCurrentStage(stages);
  if (!current) {
    return {
      label: "全ステージ完了",
      status: "completed",
      color: STATUS_COLORS.completed,
    };
  }
  const status = resolveStageStatus(current, stages);
  return {
    label: STAGE_LABELS[current] ?? current,
    status,
    color: STATUS_COLORS[status] ?? STATUS_COLORS.unknown,
  };
}

export function StageBadge({ stages }: StageBadgeProps) {
  const info = getBadgeInfo(stages ?? {});
  const accentColor = info.color;
  const background = withAlpha(accentColor, 0.12);
  const border = withAlpha(accentColor, 0.24);

  // 全ステージ完了ならシンプル表示
  if (info.status === "completed") {
    return (
      <div className="stage-badge" style={{ color: accentColor, backgroundColor: background, borderColor: border }}>
        <div className="stage-badge__legend">
          <span className="stage-badge__dot" style={{ backgroundColor: accentColor }} />
          <span>台本・音声・字幕 完了</span>
        </div>
      </div>
    );
  }

  return (
    <div className="stage-badge" style={{ color: accentColor, backgroundColor: background, borderColor: border }}>
      <div className="stage-badge__bar">
        {STAGE_GROUPS.map((group) => {
          const status = groupStatus(group.stageKeys, stages);
          const color = STATUS_COLORS[status] ?? STATUS_COLORS.unknown;
          return (
            <div
              key={group.key}
              className="stage-badge__segment"
              style={{ backgroundColor: withAlpha(color, 0.7), borderColor: withAlpha(color, 0.9), width: `${100 / STAGE_GROUPS.length}%` }}
              title={`${group.label}: ${translateStatus(status)}`}
            />
          );
        })}
      </div>
      <div className="stage-badge__legend">
        <span className="stage-badge__dot" style={{ backgroundColor: accentColor }} />
        <span>{translateStatus(info.status)}</span>
      </div>
    </div>
  );
}
