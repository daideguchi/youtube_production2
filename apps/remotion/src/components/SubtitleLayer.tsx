import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import React from "react";

type Props = {
  cues: { start: number; end: number; text: string }[];
  bottomPx?: number;
  maxWidthPct?: number;
  fontSize?: number;
};

function normalizeText(text: string) {
  // Preserve cue text exactly (only normalize line endings).
  return text.replace(/\r/g, "");
}

export const SubtitleLayer: React.FC<Props> = ({ cues, bottomPx = 50, maxWidthPct = 94, fontSize = 64 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let current: { start: number; end: number; text: string } | undefined;
  // Prefer the latest-starting cue in case of overlaps, and avoid lingering past end to prevent perceived "fade".
  for (let i = cues.length - 1; i >= 0; i--) {
    const c = cues[i];
    if (t >= c.start && t < c.end) {
      current = c;
      break;
    }
  }

  if (!current) return null;

  const opacity = 1;

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: bottomPx,
        pointerEvents: "none",
        paddingLeft: 40,
        paddingRight: 40,
        zIndex: 3,
      }}
    >
      <div
        style={{
          background: "rgba(0,0,0,0.6)",
          color: "#f7f7f2",
          padding: "24px 38px",
          borderRadius: 26,
          fontSize: fontSize,
          lineHeight: 1.5,
          textAlign: "center",
          whiteSpace: "pre-line",
          maxWidth: `${maxWidthPct}%`,
          opacity,
          transition: "none",
          fontFamily: "\"Yomogi\", \"RocknRoll One\", \"Noto Sans JP\", system-ui, sans-serif",
          fontWeight: 800,
          letterSpacing: "0.02em",
          textShadow: "0 6px 16px rgba(0,0,0,0.7)",
          border: "1px solid rgba(255,255,255,0.16)",
        }}
      >
        {normalizeText(current.text)}
      </div>
    </AbsoluteFill>
  );
};
