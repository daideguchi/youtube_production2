import type { FC } from "react";

type Props = {
  note?: string | null;
  label?: string;
};

export const RedoBadge: FC<Props> = ({ note, label = "リテイク" }) => {
  return (
    <span
      className="planning-page__badge planning-page__badge--redo"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 6px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        background: "#fff1e6",
        color: "#b45309",
        border: "1px solid #f59e0b",
      }}
      title={note || label}
    >
      {label}
    </span>
  );
};
