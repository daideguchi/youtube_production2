import fs from "fs";
import path from "path";
import type { BeltConfig, Chapters, EpisodeInfo, ImageCues, Position, RunData, SubtitleCue } from "./types";
import { parseSrt } from "./parseSrt.ts";

const isAscii = (text: string) => /^[\x00-\x7F]*$/.test(text);

// Normalize belt labels to avoid noisy punctuation or trailing spaces.
function tidyBeltText(text: string): string {
  if (!text) return "";
  let t = String(text);
  t = t.replace(/[\s\u3000]+/g, " ").trim(); // collapse spaces (including full-width)
  t = t.replace(/[･·•]/g, "・");
  t = t.replace(/[!！?？。、，,.]+$/g, ""); // drop trailing punctuation
  // keep length reasonable for chips; UI側でさらに省略するが、ここで軽く刈り込む
  const max = 24;
  if (t.length > max) t = `${t.slice(0, max - 1)}…`;
  return t;
}

function readJSON<T>(p: string): T | null {
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

export function loadRunData(runDir: string, srtPath?: string): RunData {
  const imageCues = readJSON<ImageCues>(path.join(runDir, "image_cues.json"));
  if (!imageCues || !Array.isArray(imageCues.cues)) {
    throw new Error("image_cues.json missing or invalid");
  }

  const belt = readJSON<BeltConfig>(path.join(runDir, "belt_config.json"));
  if (!belt || !Array.isArray(belt.belts)) {
    throw new Error("belt_config.json missing or invalid");
  }

  const episode = readJSON<EpisodeInfo>(path.join(runDir, "episode_info.json")) ?? undefined;
  const chapters = readJSON<Chapters>(path.join(runDir, "chapters.json")) ?? undefined;

  const srtFile = srtPath ?? findSrt(runDir);
  if (!srtFile) {
    throw new Error("SRT file not found in runDir (provide --srt)");
  }
  const srtText = fs.readFileSync(srtFile, "utf-8");
  const srtCues = parseSrt(srtText);

  // belt normalization: prefer SRT-derived label; fallback to cleaned original; ASCII-onlyは禁止
  // clean + number; keep LLM生成ラベルを尊重
  const seen = new Set<string>();
  belt.belts = belt.belts.map((b, idx) => {
    const clean = tidyBeltText(b.text);
    let base = clean && !isAscii(clean) ? clean : `第${idx + 1}章`;
    if (seen.has(base)) {
      base = `第${idx + 1}章`;
    }
    seen.add(base);
    return {...b, text: `${idx + 1}. ${base}`};
  });

  // resolve image paths to absolute if needed
  const resolvedCues = imageCues.cues.map((c) => {
    const p = c.path;
    if (!p) return c;
    if (p.startsWith("http://") || p.startsWith("https://") || p.startsWith("tmp_run_") || p.startsWith("/tmp_run_")) {
      return c;
    }
    const abs = path.isAbsolute(p) ? p : path.resolve(runDir, p);
    return { ...c, path: abs };
  });
  imageCues.cues = resolvedCues;

  return {
    runDir,
    imageCues,
    belt,
    episode,
    chapters,
    srtText,
    srtCues,
  };
}

function findSrt(runDir: string): string | null {
  const entries = fs.readdirSync(runDir);
  for (const e of entries) {
    if (e.toLowerCase().endsWith(".srt")) {
      return path.join(runDir, e);
    }
  }
  return null;
}

// pick a concise snippet from SRT cues overlapping the belt window (hooky, numbered later)
function makeLabelFromSrt(cues: SubtitleCue[], start: number | undefined, end: number | undefined, idx: number): string {
  if (!Array.isArray(cues) || cues.length === 0) return "";
  const s = Number.isFinite(start) ? Number(start) : 0;
  const e = Number.isFinite(end) ? Number(end) : Math.max(...cues.map((c) => c.end), 0);
  const mid = (s + e) / 2;
  const window = cues.filter((c) => c.start < e && c.end > s);
  const source = window.length > 0 ? window : cues;

  const toSentences = (raw: string) =>
    raw
      .split(/[。！？!?\n]/)
      .map((p) => normalizeSentence(p))
      .filter(Boolean);

  type Cand = { text: string; dist: number };
  const candidates: Cand[] = [];
  for (const c of source) {
    const sentences = toSentences(c.text);
    const center = (c.start + c.end) / 2;
    for (const snt of sentences) {
      candidates.push({ text: snt, dist: Math.abs(center - mid) });
    }
  }
  if (candidates.length === 0) {
    const merged = normalizeSentence(source.map((c) => c.text).join(" "));
    if (merged) candidates.push({ text: merged, dist: 0 });
  }
  if (candidates.length === 0) return "";

  const badStarts = ["導入", "まとめ", "説明", "要約", "ポイント", "気づき", "苦悩", "物語", "日常", "解説", "シーン", "パート", "チャプター"];
  const strongKeywords = ["縁", "切", "距離", "離", "解放", "再生", "決断", "選択", "運気", "守る", "逃げ", "断つ", "遮断", "環境", "変える", "変わる"];
  const idealLen = 12;
  let best = "";
  let bestScore = Number.POSITIVE_INFINITY;
  for (const cand of candidates) {
    const text = cand.text;
    const len = text.length;
    const penaltyStart = badStarts.some((w) => text === w || text.startsWith(w + " ")) ? 10 : 0;
    const penaltyQuote = /[「」『』"']/g.test(text) ? 8 : 0;
    const penaltyComma = /、/.test(text) ? 2 : 0;
    const penaltyGeneric = /(かもしれません|と思います|と思う|でしょう|でした|です)$/.test(text) ? 6 : 0;
    const bonusHook = strongKeywords.some((k) => text.includes(k)) ? -2 : 0;
    const score = Math.abs(len - idealLen) + cand.dist / 8 + penaltyStart + penaltyQuote + penaltyComma + penaltyGeneric + bonusHook;
    if (score < bestScore) {
      best = text;
      bestScore = score;
    }
  }
  if (!best) return "";
  const maxLen = 18;
  let trimmed = best.length > maxLen ? `${best.slice(0, maxLen - 1)}…` : best;
  trimmed = trimmed.replace(/(かもしれません|と思います|と思う|でしょう|でした|です)$/g, "").replace(/[、。]+$/g, "").trim();
  if (!trimmed) {
    trimmed = best.length > maxLen ? `${best.slice(0, maxLen - 1)}…` : best;
  }
  if (badStarts.some((w) => trimmed === w || trimmed.startsWith(w + " "))) {
    return `第${idx + 1}章`;
  }
  return trimmed;
}

function normalizeSentence(text: string): string {
  if (!text) return "";
  let t = text
    .replace(/<\/?[^>]+>/g, "")
    .replace(/\{\\.*?\}/g, "")
    .replace(/\u3000+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  t = tidyBeltText(t);
  t = t.replace(/[「」『』"']/g, "");
  t = t.replace(/^\d+\s+/, "");
  t = t.replace(/^(あら|まあ|ええ|いや|いいえ|でも|しかし|そして|だから|それで|そう)[:：、,\s]+/, "");
  return t;
}

// legacy helper kept for compatibility; now normalizeSentence is used in makeLabelFromSrt
function cleanCueText(text: string): string {
  return normalizeSentence(text);
}
