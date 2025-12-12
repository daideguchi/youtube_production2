export const INTEGRITY_LABEL = "整合チェック";

const INTEGRITY_STATUS_LABELS: Record<string, string> = {
  ok: "整合OK",
  fail: "要修正",
  unknown: "未チェック",
};

export function getIntegrityStatusLabel(status: string | null): string {
  if (!status) {
    return INTEGRITY_STATUS_LABELS.unknown;
  }
  return INTEGRITY_STATUS_LABELS[status] ?? INTEGRITY_STATUS_LABELS.unknown;
}

export const INTEGRITY_NO_DETAILS = `${INTEGRITY_LABEL}からは詳細が返っていません。Quick Job で再実行を試してください。`;

export function getIntegrityAutoMessage(auto: boolean): string {
  return auto
    ? `差し替え後は ${INTEGRITY_LABEL} を自動で再実行し、整合を確認します。`
    : `画像を差し替えた後は ${INTEGRITY_LABEL} を再実行してから CapCut を実行してください。`;
}

export const INTEGRITY_AUTO_RUNNING = `${INTEGRITY_LABEL} を再実行中…`;
