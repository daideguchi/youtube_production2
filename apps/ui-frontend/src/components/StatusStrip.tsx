type StatusTone = "info" | "warning" | "danger";

export interface StatusStripItem {
  id: string;
  label: string;
  tone?: StatusTone;
}

const toneClassMap: Record<StatusTone, string> = {
  info: "border-slate-200 bg-slate-50 text-slate-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  danger: "border-rose-200 bg-rose-50 text-rose-700",
};

interface StatusStripProps {
  items: StatusStripItem[];
  className?: string;
}

export function StatusStrip({ items, className }: StatusStripProps) {
  if (!items.length) {
    return null;
  }

  const containerClass = [
    "rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-panel",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={containerClass}>
      <div className="flex flex-wrap gap-2 text-sm font-medium">
        {items.map((item) => (
          <span
            key={item.id}
            className={`rounded-full border px-3 py-1.5 ${toneClassMap[item.tone ?? "info"]}`}
          >
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}
