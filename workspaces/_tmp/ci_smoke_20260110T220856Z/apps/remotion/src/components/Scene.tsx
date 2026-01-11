import { AbsoluteFill, Img, staticFile, useVideoConfig } from "remotion";
import React from "react";
import { Position } from "../lib/types";

type Props = {
  imgPath: string;
  opacity?: number;
  position?: Position;
  blur?: number;
  progress?: number; // 0..1
  seed?: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function hash32(n: number): number {
  // Deterministic 32-bit hash (xorshift-like mix).
  let x = (n | 0) + 0x6d2b79f5;
  x = Math.imul(x ^ (x >>> 15), x | 1);
  x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
  return (x ^ (x >>> 14)) >>> 0;
}

function hashToUnitFloat(hash: number): number {
  return (hash >>> 0) / 4294967296;
}

export const Scene: React.FC<Props> = ({ imgPath, opacity = 1, position, blur = 0, progress, seed }) => {
  const { width, height } = useVideoConfig();
  // tx/ty are normalized offsets (CapCut preset). Assume they were relative deltas; clamp small range.
  const tx = position?.tx ?? 0;
  const ty = position?.ty ?? 0;
  const scale = position?.scale ?? 1.0;
  const clampedTx = Math.max(-1, Math.min(1, tx));
  const clampedTy = Math.max(-1, Math.min(1, ty));
  const p = clamp(typeof progress === "number" ? progress : 0, 0, 1);
  const localSeed =
    typeof seed === "number"
      ? seed
      : Array.from(imgPath || "").reduce((acc, ch) => (acc * 31 + ch.charCodeAt(0)) >>> 0, 7);
  // Slow, one-direction drift per scene (deterministic "random" by seed).
  // Prefer a single axis to reduce eye fatigue and keep it CapCut-like.
  const h1 = hash32(localSeed);
  const dirPick = h1 % 8;
  const dirs = [
    { x: 1, y: 0 },
    { x: -1, y: 0 },
    { x: 0, y: 1 },
    { x: 0, y: -1 },
    { x: 0.7, y: 0.7 },
    { x: 0.7, y: -0.7 },
    { x: -0.7, y: 0.7 },
    { x: -0.7, y: -0.7 },
  ] as const;
  const dir = dirs[dirPick] ?? dirs[0];
  const axisX = dir.x;
  const axisY = dir.y;
  const zoomStrength = 0.016 + 0.01 * hashToUnitFloat(hash32(h1 ^ 0x9e3779b9));
  const zoom = 1 + zoomStrength * p;
  const effectiveScale = scale * zoom;
  const overscanX = Math.max(0, (effectiveScale - 1) * (width / 2));
  const overscanY = Math.max(0, (effectiveScale - 1) * (height / 2));
  const margin = 2;
  const panStrength = 0.75 + 0.15 * hashToUnitFloat(hash32(h1 ^ 0xa5a5a5a5));
  const maxPanX = Math.max(0, overscanX - margin) * panStrength;
  const maxPanY = Math.max(0, overscanY - margin) * panStrength;
  const drift = p; // 0..1 across the scene (one-direction)
  const panX = axisX * maxPanX * drift;
  const panY = axisY * maxPanY * drift;
  let src = "";
  if (!imgPath) {
    src = "";
  } else if (imgPath.startsWith("http://") || imgPath.startsWith("https://")) {
    src = imgPath;
  } else if (imgPath.startsWith("/tmp_run_") || imgPath.startsWith("tmp_run_")) {
    src = staticFile(imgPath.replace(/^\//, ""));
  } else if (imgPath.startsWith("/")) {
    // treat as absolute filesystem path
    src = `file://${imgPath}`;
  } else {
    src = staticFile(imgPath);
  }
  const baseFilter = "contrast(1.03) saturate(1.02)";
  const sceneFilter = blur > 0 ? `blur(${blur}px) ${baseFilter}` : baseFilter;
  return (
    <AbsoluteFill style={{ backgroundColor: "#f7f2ea", opacity, filter: sceneFilter }}>
      <Img
        src={src}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `translate(${clampedTx * 100}%, ${clampedTy * 100}%) translate3d(${panX}px, ${panY}px, 0) scale(${scale * zoom})`,
        }}
      />
    </AbsoluteFill>
  );
};
