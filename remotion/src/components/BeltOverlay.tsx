import { AbsoluteFill } from "remotion";
import React from "react";
import { BeltConfig } from "../lib/types";

type Props = {
  belt: BeltConfig;
  duration: number; // seconds
  topPct?: number;
  heightPct?: number;
};

const beltColor = "rgba(0, 0, 0, 0.55)";
const textColor = "#f5f5f0";

export const BeltOverlay: React.FC<Props> = ({ belt, duration, topPct = 82, heightPct = 16 }) => {
  const belts = belt.belts ?? [];
  const total = duration || belt.total_duration || 0;
  const totalWidthPct = belts.reduce((acc, b) => acc + (total > 0 ? ((b.end - b.start) / total) * 100 : 0), 0);
  const weightCorrected = totalWidthPct > 100.5 || totalWidthPct < 99.5;
  return (
    <AbsoluteFill
      style={{
        top: `${topPct}%`,
        height: `${heightPct}%`,
        background: "linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.45) 35%, rgba(0,0,0,0.45) 100%)",
        display: "flex",
        flexDirection: "row",
        padding: "6px 12px 12px 12px",
        gap: "10px",
        boxSizing: "border-box",
        alignItems: "flex-end",
      }}
    >
      {belts.map((b, i) => {
        const widthPct = total > 0 ? ((b.end - b.start) / total) * 100 : 25;
        return (
          <div
            key={`${b.text}-${i}`}
            style={{
              flex: `${widthPct} 0 auto`,
              background: beltColor,
              borderRadius: 8,
              padding: "8px 10px",
              color: textColor,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 20,
              fontWeight: 700,
              lineHeight: 1.2,
              fontFamily: "Noto Sans JP, sans-serif",
              minWidth: "15%",
              border: "1px solid rgba(255,255,255,0.08)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
              ...(weightCorrected ? { flexBasis: `${100 / belts.length}%` } : {}),
            }}
          >
            {b.text}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
