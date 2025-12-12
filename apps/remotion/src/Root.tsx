import React, { useEffect, useMemo, useState } from "react";
import { Composition, staticFile } from "remotion";
import "./font.css";
import { Timeline } from "./Timeline";
import { BeltConfig, SubtitleCue } from "./lib/types";

export type SceneItem = {
  imgPath: string;
  start: number;
  end: number;
  idx?: number;
};

export type TimelineProps = {
  scenes: SceneItem[];
  belt: BeltConfig;
  subtitles: SubtitleCue[];
  title?: string;
  crossfade?: number;
  openingOffset?: number;
  layout?: {
    beltTopPct?: number;
    beltHeightPct?: number;
    subtitleBottomPx?: number;
    subtitleMaxWidthPct?: number;
    subtitleFontSize?: number;
    beltMainScale?: number;
    beltSubScale?: number;
    beltGapScale?: number;
  };
  bgm?: {
    src: string;
    volume?: number;
    fadeSec?: number;
  };
};

const DEFAULT_RUN = "192";
const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

export const RemotionRoot: React.FC = () => {
  const [ready, setReady] = useState(false);
  const [inputProps, setInputProps] = useState<TimelineProps>({
    scenes: [],
    belt: { belts: [] },
    subtitles: [],
    title: "",
    crossfade: 0.5,
    openingOffset: 0,
    layout: {},
    bgm: undefined,
  });
  const [durationSec, setDurationSec] = useState(10);

  useEffect(() => {
    void (async () => {
      try {
        const run = new URLSearchParams(window.location.search).get("run") || DEFAULT_RUN;
        const base = `input/${run}`;

        const fetchJson = async (rel: string) => {
          const res = await fetch(staticFile(rel));
          if (!res.ok) throw new Error(`fetch failed: ${rel}`);
          return res.json();
        };

        const fetchTextOptional = async (rel: string) => {
          try {
            const res = await fetch(staticFile(rel));
            if (!res.ok) return null;
            return res.text();
          } catch {
            return null;
          }
        };

        // load belt
        let belt: BeltConfig = { belts: [] };
        try {
          belt = await fetchJson(`${base}/belt_config.json`);
        } catch {
          try {
            belt = await fetchJson(`${base}/belt_config.generated.json`);
          } catch {
            belt = { belts: [] };
          }
        }

        // load image cues
        const cuesJson = await fetchJson(`${base}/image_cues.json`);
        const cuesArray: SceneItem[] = Array.isArray(cuesJson?.cues) ? cuesJson.cues : Array.isArray(cuesJson) ? cuesJson : [];
        const scenes: SceneItem[] = cuesArray
          .map((c: any, idx: number) => {
            const start = Number(c.start ?? c.start_sec ?? 0);
            const end = Number(c.end ?? c.end_sec ?? (c.duration_sec ? start + c.duration_sec : c.duration ? start + c.duration : start + 4));
            const raw = c.path || "";
            let imgPath: string;
            if (raw.startsWith("http://") || raw.startsWith("https://")) {
              imgPath = raw;
            } else {
              const basename = raw.split("/").filter(Boolean).pop() ?? raw;
              imgPath = `${base}/images/${basename}`;
            }
            return { imgPath, start, end, idx };
          })
          .sort((a, b) => a.start - b.start);

        const srtText = await fetchTextOptional(`${base}/${run}.srt`);
        let subtitles: SubtitleCue[] = [];
        if (srtText) {
          subtitles = parseSrt(srtText);
        }

        const qs = new URLSearchParams(window.location.search);
        const subtitleFontSize = Number(qs.get("sub_fs") || qs.get("subtitle_fs") || "") || undefined;
        const subtitleBottomPx = Number(qs.get("sub_bottom") || qs.get("subtitle_bottom") || "") || undefined;
        const subtitleMaxWidthPct = Number(qs.get("sub_width") || qs.get("subtitle_width") || "") || undefined;
        const beltMainScale = Number(qs.get("belt_main_scale") || "") || undefined;
        const beltSubScale = Number(qs.get("belt_sub_scale") || "") || undefined;
        const beltGapScale = Number(qs.get("belt_gap") || "") || undefined;
        const openingOffset =
          Number(qs.get("opening_offset") || qs.get("open_offset") || qs.get("opening") || "") ||
          belt.opening_offset ||
          0;

        const total = Math.max(
          belt.total_duration ?? 0,
          subtitles.length ? Math.max(...subtitles.map((s) => s.end)) : 0,
          scenes.length ? Math.max(...scenes.map((s) => s.end)) : 0,
        );
        const durationWithOpening = (total > 1 ? total : 10) + (openingOffset > 0 ? openingOffset : 0);
        setDurationSec(durationWithOpening);

        setInputProps({
          scenes,
          belt: { ...belt, opening_offset: belt.opening_offset ?? openingOffset },
          subtitles,
          title: belt.episode ?? "",
          crossfade: 0.5,
          openingOffset,
          layout: {
            subtitleFontSize,
            subtitleBottomPx,
            subtitleMaxWidthPct,
            beltMainScale,
            beltSubScale,
            beltGapScale,
          },
          bgm: {
            src: `${base}/${run}.wav`,
            volume: 0.35,
            fadeSec: 1.5,
          },
        });
        setReady(true);
      } catch (e) {
        console.error("Failed to load run data for preview:", e);
        setReady(false);
      }
    })();
  }, []);

  const durationInFrames = useMemo(() => Math.max(1, Math.round(durationSec * FPS)), [durationSec]);

  if (!ready) {
    return null;
  }

  return (
    <>
      <Composition
        id="Main"
        component={Timeline}
        width={WIDTH}
        height={HEIGHT}
        fps={FPS}
        durationInFrames={durationInFrames}
        defaultProps={inputProps}
      />
    </>
  );
};

export default RemotionRoot;

function parseSrt(text: string): SubtitleCue[] {
  const blocks = text.split(/\r?\n\r?\n/);
  const cues: SubtitleCue[] = [];
  for (const b of blocks) {
    const lines = b.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) continue;
    const tm = (lines[1] || lines[0]).match(/(.+)\s-->\s(.+)/);
    if (!tm) continue;
    const start = parseTime(tm[1].trim());
    const end = parseTime(tm[2].trim());
    const content = lines.slice(2).join(" ").trim();
    if (!Number.isFinite(start) || !Number.isFinite(end) || !content) continue;
    cues.push({ start, end, text: content });
  }
  return cues.sort((a, b) => a.start - b.start);
}

function parseTime(t: string): number {
  const m = t.match(/(\d+):(\d+):(\d+),(\d+)/);
  if (!m) return 0;
  const [, h, mnt, s, ms] = m;
  return Number(h) * 3600 + Number(mnt) * 60 + Number(s) + Number(ms) / 1000;
}
