export type NormalizedStageStatusKey =
  | "pending"
  | "in_progress"
  | "review"
  | "blocked"
  | "completed"
  | "unknown";

export function normalizeStageStatusKey(value: string | null | undefined): NormalizedStageStatusKey {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!normalized || normalized === "pending") {
    return "pending";
  }
  if (normalized === "completed" || normalized === "skipped" || normalized === "done" || normalized === "ok") {
    return "completed";
  }
  if (normalized === "blocked" || normalized === "failed" || normalized === "error") {
    return "blocked";
  }
  if (normalized === "review") {
    return "review";
  }
  if (
    normalized === "in_progress" ||
    normalized === "processing" ||
    normalized === "running" ||
    normalized === "rerun_in_progress" ||
    normalized === "rerun_requested"
  ) {
    return "in_progress";
  }
  return "unknown";
}
