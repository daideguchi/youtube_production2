import { AbsoluteFill, Img, staticFile } from "remotion";
import React from "react";
import { Position } from "../lib/types";

type Props = {
  imgPath: string;
  opacity?: number;
  position?: Position;
  blur?: number;
};

export const Scene: React.FC<Props> = ({ imgPath, opacity = 1, position, blur = 0 }) => {
  // tx/ty are normalized offsets (CapCut preset). Assume they were relative deltas; clamp small range.
  const tx = position?.tx ?? 0;
  const ty = position?.ty ?? 0;
  const scale = position?.scale ?? 1.0;
  const clampedTx = Math.max(-1, Math.min(1, tx));
  const clampedTy = Math.max(-1, Math.min(1, ty));
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
  return (
    <AbsoluteFill style={{ backgroundColor: "#f7f2ea", opacity, filter: blur > 0 ? `blur(${blur}px)` : "none" }}>
      <Img
        src={src}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `translate(${clampedTx * 100}%, ${clampedTy * 100}%) scale(${scale})`,
        }}
      />
    </AbsoluteFill>
  );
};
