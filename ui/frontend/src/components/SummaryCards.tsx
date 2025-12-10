export interface SummaryCard {
  id: string;
  title: string;
  value: string;
  helper?: string;
  tone?: "primary" | "success" | "info" | "warning" | "danger" | "neutral";
  onClick?: () => void;
  active?: boolean;
}

interface SummaryCardsProps {
  items: SummaryCard[];
}

export function SummaryCards({ items }: SummaryCardsProps) {
  return (
    <div className="summary-cards" aria-label="ダッシュボードの主要指標">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`summary-card summary-card--${item.tone ?? "neutral"}${item.active ? " summary-card--active" : ""}`}
          onClick={item.onClick}
          aria-pressed={item.active ?? false}
        >
          <span className="summary-card__title">{item.title}</span>
          <span className="summary-card__value">{item.value}</span>
          {item.helper ? <span className="summary-card__helper">{item.helper}</span> : null}
        </button>
      ))}
    </div>
  );
}
