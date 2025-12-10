import React from "react";
import { AbsoluteFill, Audio, Sequence, Video, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Scene } from "./components/Scene";
import { TitleCard } from "./components/TitleCard";
import { BeltConfig, LayoutConfig, Position, SubtitleCue } from "./lib/types";
import { SubtitleLayer } from "./components/SubtitleLayer";

export type SceneItem = {
  imgPath: string;
  start: number; // seconds
  end: number; // seconds
  position?: Position;
  idx?: number;
};

type Props = {
  scenes: SceneItem[];
  belt: BeltConfig;
  subtitles: SubtitleCue[];
  title?: string;
  crossfade?: number; // seconds
  openingOffset?: number;
  layout?: LayoutConfig;
  bgm?: {
    src: string;
    volume?: number;
    fadeSec?: number;
  };
};

export const Timeline: React.FC<Props> = ({
  scenes,
  belt,
  subtitles,
  title,
  crossfade = 0.5,
  openingOffset: openingOffsetProp,
  layout,
  bgm,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const openingOffset = Math.max(0, openingOffsetProp ?? belt?.opening_offset ?? 0);
  const sceneTime = t - openingOffset;
  const beltTime = t - openingOffset;
  const sceneDuration = scenes.length ? Math.max(...scenes.map((s) => s.end)) : 0;
  const beltDuration =
    belt?.total_duration ?? (belt?.belts?.length ? Math.max(...belt.belts.map((b) => b.end)) : 0) ?? 0;
  const subtitleDuration = subtitles.length ? Math.max(...subtitles.map((s) => s.end)) : 0;
  const contentDuration = Math.max(sceneDuration, beltDuration, subtitleDuration);
  const mainLabel = title || belt?.episode || "";
  const subBelts = belt?.belts ?? [];
  const mainScale = layout?.beltMainScale ?? 1.3;
  const subScale = layout?.beltSubScale ?? 1.25;
  const gapScale = layout?.beltGapScale ?? 1.15;
  const beltTopPct = layout?.beltTopPct ?? 82;
  const beltHeightPct = layout?.beltHeightPct ?? 16;
  const firstSubtitle = React.useMemo(() => {
    if (!subtitles || subtitles.length === 0) return "";
    const sorted = [...subtitles].sort((a, b) => a.start - b.start);
    return sorted[0]?.text ?? "";
  }, [subtitles]);

  const current = scenes.find((s) => sceneTime >= s.start && sceneTime < s.end);
  const next = scenes.find((s) => s.start > sceneTime - crossfade && s.start > (current?.start ?? -Infinity));
  const shouldBlend = next && current && crossfade > 0 && sceneTime >= next.start - crossfade && sceneTime < next.start;
  let currentAlpha = 1;
  let nextAlpha = 0;
  if (shouldBlend && next && current) {
    const progress = (sceneTime - (next.start - crossfade)) / crossfade;
    nextAlpha = Math.min(Math.max(progress, 0), 1);
    currentAlpha = 1 - nextAlpha;
  }
  const blurCurrent = shouldBlend
    ? interpolate(sceneTime, [next.start - crossfade, next.start], [0, 8], { extrapolateRight: "clamp" })
    : 0;
  const blurNext = shouldBlend
    ? interpolate(sceneTime, [next.start - crossfade, next.start], [8, 0], { extrapolateRight: "clamp" })
    : 0;

  const formatBeltLabel = (label: string | undefined) => {
    const base = (label ?? "").trim();
    if (!base) return "";
    const limit = 28;
    return base.length > limit ? `${base.slice(0, limit)}…` : base;
  };

  return (
    <AbsoluteFill style={{ backgroundColor: "#f7f2ea" }}>
      {bgm?.src ? (
        <Sequence from={Math.max(0, Math.round(openingOffset * fps))}>
          <Audio
            src={
              bgm.src.startsWith("http://") || bgm.src.startsWith("https://")
                ? bgm.src
                : staticFile(bgm.src.replace(/^\/+/, ""))
            }
            volume={(audioFrame) => {
              const audioT = Math.max(0, audioFrame / fps - openingOffset);
              const fade = bgm.fadeSec ?? 1.5;
              const fadeIn = Math.min(1, audioT / fade);
              const fadeOut = contentDuration > 0 ? Math.min(1, Math.max(0, (contentDuration - audioT) / fade)) : 1;
              const base = bgm.volume ?? 0.4;
              return base * Math.min(fadeIn, fadeOut);
            }}
          />
        </Sequence>
      ) : null}
      {/* Belt chips overlay */}
      <div
        style={{
          position: "absolute",
          top: `${beltTopPct}%`,
          height: `${beltHeightPct}%`,
          left: 0,
          right: 0,
          zIndex: 4,
          display: "flex",
          flexDirection: "column",
          gap: 8 * gapScale,
          pointerEvents: "none",
          justifyContent: "flex-start",
          alignItems: "flex-start",
          padding: "0 12px",
        }}
      >
        {t >= openingOffset && (
          <div
            style={{
              alignSelf: "flex-start",
              padding: `${12 * mainScale}px ${22 * mainScale}px`,
              position: "relative",
              display: "inline-flex",
              background: "linear-gradient(180deg, #f3b25a 0%, #d37726 100%)",
              borderRadius: 14 * mainScale,
              boxShadow: "0 6px 14px rgba(0,0,0,0.36)",
              border: `${1.8 * mainScale}px solid #e59500`,
            }}
          >
            <span
              style={{
                position: "relative",
                zIndex: 1,
                display: "inline-block",
                padding: `${8 * mainScale}px ${18 * mainScale}px`,
                borderRadius: 12 * mainScale,
                fontFamily: "\"RocknRoll One\", \"Yomogi\", \"Noto Sans JP\", system-ui, sans-serif",
                fontWeight: 900,
                fontSize: 36 * mainScale,
                lineHeight: 1.05,
                letterSpacing: "0.06em",
                color: "#2d1a00",
                WebkitTextStroke: "0.8px rgba(0,0,0,0.45)",
                textShadow: [
                  "0 0 2px rgba(245,241,231,0.9)",
                  "0 0 4px rgba(245,241,231,0.9)",
                  "0 0 6px rgba(202,255,122,0.9)",
                  "0 3px 0 rgba(120,60,0,0.95)",
                  "0 6px 10px rgba(0,0,0,0.6)",
                ].join(", "),
              }}
            >
              {mainLabel || " "}
            </span>
          </div>
        )}
        {(() => {
          const active = subBelts.find((b) => beltTime >= b.start && beltTime < b.end);
          if (!active || t < openingOffset) return null;
          return (
            <div
              style={{
                display: "flex",
                gap: 6 * gapScale,
                padding: 0,
              }}
            >
              <div
                style={{
                  padding: `${16 * subScale}px ${22 * subScale}px`,
                  borderRadius: 14 * subScale,
                  background: "linear-gradient(180deg, #f9a600 0%, #d87a1f 100%)",
                  color: "#1b0e00",
                  fontSize: 32 * subScale,
                  fontWeight: 820,
                  boxShadow: "0 4px 10px rgba(0,0,0,0.3)",
                  border: "1.6px solid rgba(200, 110, 25, 0.95)",
                  lineHeight: 1.08,
                  letterSpacing: "0.02em",
                  textShadow: [
                    "0 0 2px rgba(255,255,255,0.9)",
                    "0 0 4px rgba(255,255,255,0.6)",
                    "0 1px 2px rgba(0,0,0,0.35)",
                    "0 3px 6px rgba(0,0,0,0.4)",
                  ].join(", "),
                  fontFamily: "\"RocknRoll One\", \"Yomogi\", \"Noto Sans JP\", system-ui, sans-serif",
                }}
              >
                {formatBeltLabel(active.text)}
              </div>
            </div>
          );
        })()}
      </div>

      {current && (
        <Scene
          key={`curr-${current.idx ?? current.start}`}
          imgPath={current.imgPath}
          opacity={currentAlpha}
          position={current.position}
          blur={blurCurrent}
        />
      )}
      {shouldBlend && next && (
        <Scene
          key={`next-${next.idx ?? next.start}`}
          imgPath={next.imgPath}
          opacity={nextAlpha}
          position={next.position}
          blur={blurNext}
        />
      )}

      <SubtitleLayer
        cues={subtitles}
        bottomPx={layout?.subtitleBottomPx}
        maxWidthPct={layout?.subtitleMaxWidthPct}
        fontSize={layout?.subtitleFontSize}
      />

      {openingOffset > 0 && t < openingOffset && (
        <Video
          startFrom={0}
          endAt={Math.floor(openingOffset * fps)}
          src={staticFile("asset/ch01_opening.mp4")}
          muted
          onError={(e) => {
            console.warn("Opening clip playback error", e);
          }}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", zIndex: 2 }}
        />
      )}
      {/* Overlay: show the first subtitle line during opening instead of the title */}
      {openingOffset > 0 && t < openingOffset && (firstSubtitle || title) && (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            pointerEvents: "none",
            zIndex: 4,
            opacity: Math.min(1, t / Math.max(0.001, openingOffset * 0.4)),
          }}
        >
          <div
            style={{
              padding: "24px 36px",
              borderRadius: 24,
              background:
                "linear-gradient(135deg, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0.65) 55%, rgba(0,0,0,0.45) 100%)",
              color: "#fffefa",
              fontSize: 64,
              fontWeight: 900,
              textShadow: "0 4px 16px rgba(0,0,0,0.65)",
              letterSpacing: "0.03em",
              fontFamily: "\"RocknRoll One\", \"Yomogi\", \"Noto Sans JP\", system-ui, sans-serif",
              boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
            }}
          >
            {firstSubtitle || title}
          </div>
        </AbsoluteFill>
      )}
      {title && t >= openingOffset && t < openingOffset + 3 && <TitleCard title={title} />}
    </AbsoluteFill>
  );
};
