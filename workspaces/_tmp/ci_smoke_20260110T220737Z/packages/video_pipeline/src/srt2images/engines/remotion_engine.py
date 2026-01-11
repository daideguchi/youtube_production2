from __future__ import annotations
import json
import math
import os
import shutil
from pathlib import Path
from typing import List, Dict


def _compute_schedule(cues: List[Dict], fps: int, crossfade: float) -> Dict:
    overlap_frames = max(0, int(round(crossfade * fps)))
    display_frames = [max(1, int(round(c["duration_sec"] * fps))) for c in cues]

    starts = []
    cur = 0
    for i, dur in enumerate(display_frames):
        starts.append(cur)
        if i < len(display_frames) - 1:
            cur += dur - overlap_frames
        else:
            cur += dur

    total_frames = cur
    schedule = []
    for i, c in enumerate(cues):
        image_path = c.get("image_path") or f"images/{i+1:04d}.png"
        schedule.append(
            {
                "index": c.get("index", i + 1),
                "start": starts[i],
                "duration": display_frames[i],
                "summary": c.get("summary", ""),
                "image": f"images/{Path(image_path).name}",
            }
        )
    return {
        "overlap_frames": overlap_frames,
        "total_frames": total_frames,
        "items": schedule,
    }


def _write_remotion_project(root: Path, size: Dict, fps: int, sched: Dict, subtitles: List[Dict], fit: str = "cover", margin_px: int = 0):
    (root / "src" / "data").mkdir(parents=True, exist_ok=True)
    (root / "public" / "images").mkdir(parents=True, exist_ok=True)

    # package.json
    pkg = {
        "name": "srt2images-remotion",
        "private": True,
        "type": "module",
        "scripts": {
            "render": "remotion render src/index.ts RemotionVideo ./output/final.mp4 --overwrite",
            "preview": "remotion preview"
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "remotion": "^4.0.0",
            "@remotion/transitions": "^4.0.0"
        },
        "devDependencies": {
            "@remotion/cli": "^4.0.0",
            "@types/react": "^18.2.0",
            "@types/react-dom": "^18.2.0",
            "typescript": "^5.4.0"
        }
    }
    (root / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    (root / "output").mkdir(parents=True, exist_ok=True)

    # remotion.config.ts
    (root / "remotion.config.ts").write_text(
        "export const Config = {};\n",
        encoding="utf-8",
    )

    # index.ts
    (root / "src" / "index.ts").write_text(
        "import {registerRoot} from 'remotion';\nimport {RemotionRoot} from './root';\nregisterRoot(RemotionRoot);\n",
        encoding="utf-8",
    )

    # root.tsx
    width, height = size["width"], size["height"]
    root_tsx = """
import {Composition} from 'remotion';
import React from 'react';
import { ImageTimeline } from './ImageTimeline';
import data from './data/cues';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="RemotionVideo"
      component={ImageTimeline}
      durationInFrames={data.timeline.total_frames}
      fps={data.config.fps}
      width={data.config.size.width}
      height={data.config.size.height}
      defaultProps={data}
    />
  );
};
"""
    (root / "src" / "root.tsx").write_text(root_tsx, encoding="utf-8")

    # data/cues.ts
    cues_ts = {
        "config": {"size": size, "fps": fps, "fit": fit, "margin_px": margin_px},
        "timeline": sched,
    }
    (root / "src" / "data" / "cues.ts").write_text(
        "export default " + json.dumps(cues_ts, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    # data/subtitles.ts (expects frames)
    subs = []
    for s in subtitles:
        if "start_frame" in s and "end_frame" in s:
            subs.append({
                "start": int(s["start_frame"]),
                "end": int(s["end_frame"]),
                "text": s.get("text", ""),
            })
        else:
            subs.append({
                "start": int(round(s["start"] * fps)),
                "end": int(round(s["end"] * fps)),
                "text": s.get("text", ""),
            })
    (root / "src" / "data" / "subtitles.ts").write_text(
        "export default " + json.dumps({"fps": fps, "items": subs}, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    # ImageTimeline.tsx
    img_timeline_tsx = """
import React from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame} from 'remotion';
import subtitles from './data/subtitles';

type Data = {
  config: {size: {width: number; height: number}; fps: number};
  timeline: {overlap_frames: number; total_frames: number; items: {index: number; start: number; duration: number; summary: string; image: string}[]};
};

const FadeImage: React.FC<{src: string; start: number; duration: number; overlap: number; z: number}> = ({src, start, duration, overlap, z}) => {
  const frame = useCurrentFrame();
  const localRaw = frame - start;
  if (localRaw < 0 || localRaw >= duration) {
    return null;
  }
  const local = Math.max(0, Math.min(duration, localRaw));
  const fadeIn = overlap > 0 ? Math.min(1, Math.max(0, local / overlap)) : 1;
  const fadeOut = overlap > 0 ? Math.min(1, Math.max(0, (duration - local) / overlap)) : 1;
  const opacity = Math.max(0, Math.min(1, Math.min(fadeIn, fadeOut)));
  const cfg:any = (data as any)?.config || {fit:'cover', margin_px:0};
  const fit = cfg.fit || 'cover';
  const pad = cfg.margin_px || 0;
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', backgroundColor: 'black', zIndex: z, pointerEvents: 'none'}}>
      <div style={{position:'absolute', inset: pad}}>
        <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: fit as any, opacity}} />
      </div>
    </AbsoluteFill>
  );
};

export const ImageTimeline: React.FC<Data> = (data) => {
  const {items, overlap_frames} = data.timeline as any;
  return (
    <AbsoluteFill>
      {items.map((it: any) => (
        <FadeImage key={it.index} src={it.image} start={it.start} duration={it.duration} overlap={overlap_frames} z={it.index} />
      ))}
      <SubtitlesOverlay />
    </AbsoluteFill>
  );
};

const SubtitlesOverlay: React.FC = () => {
  const frame = useCurrentFrame();
  const active = subtitles.items.filter((s) => frame >= s.start && frame < s.end);
  const current = active.length ? active[active.length - 1] : null;
  const text = current ? current.text : '';
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', pointerEvents: 'none', zIndex: 999999}}>
      <div style={{
        width: '92%',
        marginBottom: 72,
        textAlign: 'center',
        color: 'white',
        fontSize: 52,
        lineHeight: 1.32,
        fontWeight: 800,
        padding: '10px 18px',
        borderRadius: 8,
        backgroundColor: 'rgba(0,0,0,0.25)',
        textShadow: '0 0 6px rgba(0,0,0,0.85), 2px 2px 0 rgba(0,0,0,0.9)'
      }}>
        {text}
      </div>
    </AbsoluteFill>
  );
};
"""
    (root / "src" / "ImageTimeline.tsx").write_text(img_timeline_tsx, encoding="utf-8")


def setup_and_render_remotion(out_dir: Path, size: Dict, fps: int, crossfade: float, cues: List[Dict], subtitles: List[Dict], fit: str = "cover", margin_px: int = 0):
    remotion_dir = out_dir / "remotion"
    remotion_dir.mkdir(parents=True, exist_ok=True)

    # Copy images to remotion/public/images
    src_images = out_dir / "images"
    dst_images = remotion_dir / "public" / "images"
    dst_images.mkdir(parents=True, exist_ok=True)
    for p in sorted(src_images.glob("*.png")):
        shutil.copy2(p, dst_images / p.name)

    schedule = _compute_schedule(cues, fps=fps, crossfade=crossfade)
    # Align subtitles strictly to the image schedule to avoid drift due to overlaps
    subs_aligned = []
    ov = schedule["overlap_frames"]
    prev_end = 0
    for i, item in enumerate(schedule["items"]):
        text = cues[i].get("text") or cues[i].get("summary", "")
        start = item["start"] + (ov // 2 if i > 0 else 0)
        end = item["start"] + item["duration"] - (ov // 2 if i < len(schedule["items"]) - 1 else 0)
        # avoid overlap and enforce monotonicity
        if start < prev_end:
            start = prev_end
        if end <= start:
            end = start + 1
        subs_aligned.append({"start_frame": start, "end_frame": end, "text": text})
        prev_end = end

    _write_remotion_project(remotion_dir, size=size, fps=fps, sched=schedule, subtitles=subs_aligned, fit=fit, margin_px=margin_px)

    # Also write a manifest in out/remotion for reference
    (remotion_dir / "timeline.json").write_text(
        json.dumps({"config": {"size": size, "fps": fps}, "timeline": schedule}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
