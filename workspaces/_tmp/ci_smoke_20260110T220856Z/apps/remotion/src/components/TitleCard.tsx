import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import React from "react";

type Props = {
  title: string;
  durationInFrames?: number;
};

export const TitleCard: React.FC<Props> = ({ title, durationInFrames = 120 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const opacity = interpolate(frame, [0, fps * 0.5, fps], [0, 1, 1], { extrapolateRight: "clamp" });
  const translateY = interpolate(frame, [0, fps], [20, 0], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        background: "linear-gradient(135deg, #fdf6e3 0%, #f0e1d0 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <div
        style={{
          fontSize: 64,
          fontWeight: 700,
          color: "#2d2a32",
          textAlign: "center",
          padding: "40px",
          lineHeight: 1.2,
          fontFamily: "Noto Sans JP, sans-serif",
        }}
      >
        {title}
      </div>
    </AbsoluteFill>
  );
};
