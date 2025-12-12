import type { SubtitleCue } from "./types";

function timeToSeconds(t: string): number {
  const m = t.match(/(\d+):(\d+):(\d+),(\d+)/);
  if (!m) return 0;
  const [, hh, mm, ss, ms] = m;
  return Number(hh) * 3600 + Number(mm) * 60 + Number(ss) + Number(ms) / 1000;
}

function sanitize(s: string): string {
  // drop HTML-ish tags and SRT style residues, collapse whitespace and strip leading numbers if present
  return s
    .replace(/<\/?[^>]+>/g, "")
    .replace(/\{\\.*?\}/g, "")
    .replace(/\u3000+/g, " ") // full-width spaces
    .replace(/^\[[^\]]+\]\s*/g, "") // leading [Music] style tags
    .replace(/^\([^)]*\)\s*/g, "") // leading (Music) style tags
    .replace(/^\d+\s+/, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function parseSrt(text: string): SubtitleCue[] {
  const cleaned = text.replace(/\r/g, "");
  const normalized = cleaned.replace(/(\d{2}:\d{2}:\d{2})\.(\d{3})/g, "$1,$2");
  const blocks = normalized.split(/\n\s*\n/);
  const cues: SubtitleCue[] = [];
  for (const block of blocks) {
    const lines = block.replace(/\r/g, "").split("\n").filter(Boolean);
    if (lines.length < 2) continue;
    const timeLine = lines[1].includes("-->") ? lines[1] : lines[0];
    const match = timeLine.match(/([\d:,]+)\s*-->\s*([\d:,]+)/);
    if (!match) continue;
    const start = timeToSeconds(match[1]);
    const end = timeToSeconds(match[2]);
    const textLines = lines.slice(lines.indexOf(timeLine) + 1).join("\n");
    const norm = sanitize(textLines);
    if (!norm) continue;
    cues.push({ start, end, text: norm });
  }
  // merge overlapping cues with identical text to reduce flicker
  const merged: SubtitleCue[] = [];
  for (const cue of cues.sort((a, b) => a.start - b.start)) {
    const last = merged[merged.length - 1];
    if (last && last.text === cue.text && cue.start <= last.end + 0.05) {
      last.end = Math.max(last.end, cue.end);
    } else {
      merged.push({ ...cue });
    }
  }
  return merged;
}
