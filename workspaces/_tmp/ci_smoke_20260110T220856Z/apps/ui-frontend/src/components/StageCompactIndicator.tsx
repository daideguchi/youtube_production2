import { translateStatus } from "../utils/i18n";
import { normalizeStageStatusKey } from "../utils/stage";

export type StageCompactItem = {
  key: string;
  label: string;
  short: string;
  status?: string | null;
  title?: string | null;
};

type StageCompactIndicatorProps = {
  items: StageCompactItem[];
  ariaLabel?: string;
  className?: string;
};

export function StageCompactIndicator({
  items,
  ariaLabel,
  className,
}: StageCompactIndicatorProps) {
  return (
    <div className={["stage-compact", className].filter(Boolean).join(" ")} role="group" aria-label={ariaLabel ?? "制作進捗"}>
      {items.map((item) => {
        const statusKey = normalizeStageStatusKey(item.status);
        const statusLabel =
          statusKey === "unknown" ? translateStatus(item.status) : translateStatus(statusKey);
        return (
          <span
            key={item.key}
            className={`stage-compact__item stage-compact__item--${statusKey}`}
            title={item.title ?? `${item.label}: ${statusLabel}`}
          >
            {item.short}
          </span>
        );
      })}
    </div>
  );
}
