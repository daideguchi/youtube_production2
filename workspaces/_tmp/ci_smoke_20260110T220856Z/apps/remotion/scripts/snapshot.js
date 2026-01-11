#!/usr/bin/env node
import "ts-node/register/transpile-only";
import { bundle } from "@remotion/bundler";
import { getCompositions, renderStill } from "@remotion/renderer";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { loadRunData } from "../src/lib/loadRunData.ts";
import { createRequire } from "module";
import fetch from "node-fetch";
import { AbortController } from "abort-controller";
import { sortMissing, summarizeMissing } from "./missing_util.js";

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REMOTION_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(REMOTION_ROOT, "..", "..");
const REMOTION_PUBLIC_DIR = path.join(REMOTION_ROOT, "public");
const VIDEO_PIPELINE_ROOT = path.join(REPO_ROOT, "packages", "video_pipeline");

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--run") opts.run = args[++i];
    else if (a === "--channel") opts.channel = args[++i];
    else if (a === "--title") opts.title = args[++i];
    else if (a === "--fps") opts.fps = Number(args[++i] || 30);
    else if (a === "--size") opts.size = args[++i];
    else if (a === "--crossfade") opts.crossfade = Number(args[++i] || 0.5);
    else if (a === "--frame") opts.frame = Number(args[++i] || 0);
    else if (a === "--out") opts.out = args[++i];
    else if (a === "--tx") opts.tx = Number(args[++i]);
    else if (a === "--ty") opts.ty = Number(args[++i]);
    else if (a === "--scale") opts.scale = Number(args[++i]);
    else if (a === "--belt-top") opts.beltTop = Number(args[++i]);
    else if (a === "--belt-height") opts.beltHeight = Number(args[++i]);
    else if (a === "--subtitle-bottom") opts.subtitleBottom = Number(args[++i]);
    else if (a === "--subtitle-maxwidth") opts.subtitleMaxWidth = Number(args[++i]);
    else if (a === "--subtitle-fontsize") opts.subtitleFontSize = Number(args[++i]);
    else if (a === "--opening-offset") opts.openingOffset = Number(args[++i]);
    else if (a === "--check-remote") opts.checkRemote = true;
    else if (a === "--remote-timeout-ms") opts.remoteTimeoutMs = Number(args[++i]);
    else if (a === "--remote-retries") opts.remoteRetries = Number(args[++i]);
    else if (a === "--fail-on-missing") opts.failOnMissing = true;
  }
  if (!opts.run) throw new Error("--run <run_dir> is required");
  return opts;
}

async function main() {
  const opts = parseArgs();
  const runDir = path.resolve(REPO_ROOT, opts.run);
  const size = (opts.size || "1920x1080").split("x").map(Number);
  const [width, height] = size;
  const fps = opts.fps || 30;

  const runData = loadRunData(runDir, opts.srt);
  const crossfade = Number.isFinite(opts.crossfade) ? Number(opts.crossfade) : 0.5;
  const presetPosition = resolvePosition(opts, loadPresetPosition(opts.channel));
  const presetLayout = loadPresetLayout(opts.channel);
  const layout = resolveLayout(opts, presetLayout);
  const presetOpeningOffset = loadPresetOpeningOffset(opts.channel);
  const openingOffset = resolveOpeningOffset(opts, runData.belt, presetOpeningOffset);
  const belt = { ...runData.belt, opening_offset: openingOffset };
  const scenes = [];
  const missingImages = [];
  for (const [idx, c] of runData.imageCues.cues.entries()) {
    if (!c) continue;
    const start = c.start_sec ?? c.start ?? 0;
    const end = c.end_sec ?? c.end ?? 0;
    if (end <= start) continue;
    const imgPath = await resolveImgPath(runDir, c.path, opts.checkRemote, opts.remoteTimeoutMs, opts.remoteRetries, missingImages, idx);
    if (!imgPath) continue;
    scenes.push({ imgPath, start, end, position: presetPosition, idx });
  }
  fixOverlaps(scenes, crossfade);
  const sceneDuration = scenes.length ? Math.max(...scenes.map((s) => s.end)) : 0;
  const subtitleDuration = runData.srtCues?.length ? Math.max(...runData.srtCues.map((s) => s.end)) : 0;
  const baseDuration = Math.max(sceneDuration, subtitleDuration, belt?.total_duration ?? 0);
  const duration = baseDuration + openingOffset;
  if (scenes.length === 0) {
    console.warn("⚠️ No scenes to render (all cues were invalid or missing images).");
    if (opts.failOnMissing) {
      process.exit(1);
    }
  }

  const entry = path.join(REMOTION_ROOT, "src", "index.ts");
  const bundled = await bundle({
    entryPoint: entry,
    enableCaching: true,
    publicDir: REMOTION_PUBLIC_DIR,
    webpackOverride: (config) => {
      config.resolve ??= {};
      config.resolve.extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"];
      return config;
    },
  });

  const comps = await getCompositions(bundled, { inputProps: {} });
  const comp = comps.find((c) => c.id === "Main");
  if (!comp) throw new Error("Composition 'Main' not found");

  const frame = Number.isFinite(opts.frame) ? opts.frame : 0;
  const outputLocation = path.resolve(process.cwd(), opts.out || path.join(runDir, `snapshot_f${frame}.png`));
  await renderStill({
    composition: {
      ...comp,
      width,
      height,
      fps,
      durationInFrames: Math.ceil(Math.max(1, duration * fps)),
    },
    frame,
    serveUrl: bundled,
    output: outputLocation,
    inputProps: {
      scenes,
      belt,
      subtitles: runData.srtCues,
      title: opts.title || runData.episode?.title || "",
      crossfade,
      openingOffset,
      layout,
    },
  });
  console.log("✅ Snapshot saved:", outputLocation);
  if (missingImages.length > 0) {
    const sorted = sortMissing(missingImages);
    const summary = summarizeMissing(sorted);
    const logPath = path.join(runDir, "remotion_missing_images_snapshot.json");
    fs.writeFileSync(logPath, JSON.stringify(sorted, null, 2), "utf-8");
    console.warn(
      `⚠️ Missing images: total=${summary.total}, local=${summary.local}, remote=${summary.remote} (logged to ${logPath})`
    );
  }
}

main().catch((err) => {
  console.error("Snapshot failed:", err);
  process.exit(1);
});

async function resolveImgPath(runDir, p, checkRemote = false, remoteTimeoutMs = 4000, remoteRetries = 2, missingList = [], idx = -1) {
  if (!p) return "";
  if (p.startsWith("tmp_run_") || p.startsWith("/tmp_run_")) {
    return p.startsWith("/") ? p : `/${p}`;
  }
  if (p.startsWith("http://") || p.startsWith("https://")) {
    if (checkRemote) {
      const ok = await headWithRetry(p, remoteRetries, remoteTimeoutMs, true);
      if (!ok) {
        console.warn(`⚠️ image url not reachable: ${p}`);
        missingList.push({ path: p, idx, type: "remote" });
        return "";
      }
    }
    return p;
  }
  if (path.isAbsolute(p)) return p;
  const candidate = path.resolve(runDir, p);
  if (!fs.existsSync(candidate)) {
    console.warn(`⚠️ image not found: ${candidate}`);
    missingList.push({ path: candidate, idx, type: "local" });
    return "";
  }
  return candidate;
}
function loadPresetPosition(channel) {
  try {
    if (!channel) return { tx: 0, ty: 0, scale: 1 };
    const cfg = JSON.parse(
      fs.readFileSync(path.join(VIDEO_PIPELINE_ROOT, "config", "channel_presets.json"), "utf-8"),
    );
    const preset = cfg?.channels?.[channel];
    const pos = preset?.position;
    if (pos && typeof pos.tx === "number" && typeof pos.ty === "number" && typeof pos.scale === "number") {
      return pos;
    }
  } catch {}
  return { tx: 0, ty: 0, scale: 1 };
}

function resolvePosition(opts, preset) {
  const tx = Number.isFinite(opts.tx) ? opts.tx : preset.tx;
  const ty = Number.isFinite(opts.ty) ? opts.ty : preset.ty;
  const scale = Number.isFinite(opts.scale) ? opts.scale : preset.scale;
  return { tx, ty, scale };
}

function fixOverlaps(scenes, crossfade) {
  const sorted = scenes.sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const curr = sorted[i];
    const minStart = prev.end - Math.max(crossfade, 0);
    if (curr.start < minStart) {
      curr.start = minStart;
    }
    if (curr.end < curr.start) {
      curr.end = curr.start + 0.01;
    }
  }
}

function loadPresetLayout(channel) {
  try {
    const defaultsPath = path.join(REMOTION_ROOT, "preset_layouts.json");
    const defaults = fs.existsSync(defaultsPath) ? JSON.parse(fs.readFileSync(defaultsPath, "utf-8")) : {};

    let layout = {};
    if (channel) {
      const cfg = JSON.parse(
        fs.readFileSync(path.join(VIDEO_PIPELINE_ROOT, "config", "channel_presets.json"), "utf-8"),
      );
      const preset = cfg?.channels?.[channel];
      const fromPreset = preset?.layout;
      if (fromPreset && typeof fromPreset === "object") {
        layout = fromPreset;
      } else if (defaults[channel]) {
        layout = defaults[channel];
      }
    }
    if (!layout || Object.keys(layout).length === 0) {
      layout = defaults["default"] || {};
    }
    return {
      beltTopPct: toNum(layout.beltTopPct),
      beltHeightPct: toNum(layout.beltHeightPct),
      subtitleBottomPx: toNum(layout.subtitleBottomPx),
      subtitleMaxWidthPct: toNum(layout.subtitleMaxWidthPct),
      subtitleFontSize: toNum(layout.subtitleFontSize),
    };
  } catch {}
  return {};
}

function loadPresetOpeningOffset(channel) {
  try {
    if (!channel) return undefined;
    const cfg = JSON.parse(
      fs.readFileSync(
        path.join(VIDEO_PIPELINE_ROOT, "config", "channel_presets.json"),
        "utf-8",
      ),
    );
    const preset = cfg?.channels?.[channel];
    const val = preset?.belt?.opening_offset;
    if (typeof val === "number" && Number.isFinite(val)) return val;
  } catch {}
  return undefined;
}

function resolveOpeningOffset(opts, belt, preset) {
  if (Number.isFinite(opts?.openingOffset)) return Number(opts.openingOffset);
  if (belt && Number.isFinite(belt.opening_offset)) return Number(belt.opening_offset);
  if (Number.isFinite(preset)) return Number(preset);
  return 0;
}

function resolveLayout(opts, presetLayout = {}) {
  const beltTopPct = Number.isFinite(opts.beltTop) ? opts.beltTop : toNum(presetLayout.beltTopPct);
  const beltHeightPct = Number.isFinite(opts.beltHeight) ? opts.beltHeight : toNum(presetLayout.beltHeightPct);
  const subtitleBottomPx = Number.isFinite(opts.subtitleBottom) ? opts.subtitleBottom : toNum(presetLayout.subtitleBottomPx);
  const subtitleMaxWidthPct = Number.isFinite(opts.subtitleMaxWidth) ? opts.subtitleMaxWidth : toNum(presetLayout.subtitleMaxWidthPct);
  const subtitleFontSize = Number.isFinite(opts.subtitleFontSize) ? opts.subtitleFontSize : toNum(presetLayout.subtitleFontSize);
  return { beltTopPct, beltHeightPct, subtitleBottomPx, subtitleMaxWidthPct, subtitleFontSize };
}

async function headWithRetry(url, retries = 1, timeoutMs = 4000) {
  for (let i = 0; i <= retries; i++) {
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), timeoutMs);
      let res = await fetch(url, { method: "HEAD", redirect: "follow", signal: controller.signal });
      if (!res.ok) {
        res = await fetch(url, { method: "GET", redirect: "follow", signal: controller.signal });
      }
      clearTimeout(t);
      if (res.ok) return true;
    } catch (e) {
      if (i === retries) return false;
    }
  }
  return false;
}

function toNum(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}
