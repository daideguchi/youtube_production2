export const STAGE_LABELS: Record<string, string> = {
  topic_research: "リサーチ",
  script_outline: "アウトライン",
  script_master_plan: "設計図",
  chapter_brief: "章ブリーフ",
  script_draft: "台本ドラフト",
  script_review: "ドラフト統合",
  script_validation: "台本検証",
  script_polish_ai: "台本仕上げ",
  script_audio_ai: "音声用調整",
  script_tts_prepare: "音声原稿整備",
  audio_synthesis: "音声合成",
  srt_generation: "字幕生成",
  timeline_copy: "タイムライン反映",
  image_generation: "画像生成",
};

export const STAGE_ORDER: string[] = [
  "topic_research",
  "script_outline",
  "script_master_plan",
  "chapter_brief",
  "script_draft",
  "script_review",
  "script_validation",
  "script_polish_ai",
  "script_audio_ai",
  "script_tts_prepare",
  "audio_synthesis",
  "srt_generation",
  "timeline_copy",
  "image_generation",
];

export const STATUS_LABELS: Record<string, string> = {
  pending: "未着手",
  in_progress: "進行中",
  blocked: "要対応",
  review: "レビュー待ち",
  completed: "完了",
  published: "投稿済み",
  script_ready: "台本準備済み",
  script_validated: "台本チェック済み",
  unknown: "未設定",
};

export const STATUS_COLORS: Record<string, string> = {
  pending: "#9ca3af",
  in_progress: "#2563eb",
  blocked: "#dc2626",
  review: "#7c3aed",
  completed: "#16a34a",
  published: "#0f766e",
  script_ready: "#0369a1",
  script_validated: "#4338ca",
  unknown: "#9ca3af",
};

export function translateStage(stageKey: string): string {
  return STAGE_LABELS[stageKey] ?? stageKey;
}

export function translateStatus(statusKey?: string | null): string {
  if (!statusKey) return STATUS_LABELS.unknown;
  return STATUS_LABELS[statusKey] ?? statusKey;
}

const STATUS_HINTS: Record<string, string> = {
  script_ready: "台本の整備が完了し、音声準備を進めています。",
  script_validated: "台本チェック完了。音声生成に進める状態です。",
  completed: "音声・字幕まで生成済みです。",
};

export function getStatusHint(statusKey?: string | null): string | null {
  if (!statusKey) {
    return null;
  }
  return STATUS_HINTS[statusKey] ?? null;
}
