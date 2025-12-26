#!/usr/bin/env node
import "ts-node/register/transpile-only";
import { bundle } from "@remotion/bundler";
import { renderMedia } from "@remotion/renderer";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { loadRunData } from "../src/lib/loadRunData.ts";
import { createRequire } from "module";
import fetch from "node-fetch";
import { AbortController } from "abort-controller";
import { sortMissing, summarizeMissing } from "./missing_util.js";
import { spawn } from "child_process";

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REMOTION_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(REMOTION_ROOT, "..", "..");
const REMOTION_PUBLIC_DIR = path.join(REMOTION_ROOT, "public");
const REMOTION_OUT_DIR = path.join(REMOTION_ROOT, "out");
const VIDEO_PIPELINE_ROOT = path.join(REPO_ROOT, "packages", "video_pipeline");

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--run") opts.run = args[++i];
    else if (a === "--srt") opts.srt = args[++i];
    else if (a === "--title") opts.title = args[++i];
    else if (a === "--fps") opts.fps = Number(args[++i] || 30);
    else if (a === "--size") opts.size = args[++i];
    else if (a === "--crossfade") opts.crossfade = Number(args[++i] || 0.5);
    else if (a === "--out") opts.out = args[++i];
    else if (a === "--channel") opts.channel = args[++i];
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
    else if (a === "--bgm") opts.bgm = args[++i];
    else if (a === "--bgm-volume") opts.bgmVolume = Number(args[++i]);
    else if (a === "--bgm-fade") opts.bgmFade = Number(args[++i]);
    else if (a === "--chunk-sec") opts.chunkSec = Number(args[++i]);
    else if (a === "--chunk-dir") opts.chunkDir = args[++i];
    else if (a === "--resume-chunks") opts.resumeChunks = true;
    else if (a === "--max-chunks-per-run") opts.maxChunksPerRun = Number(args[++i]);
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
  if (!width || !height) {
    throw new Error("--size must be like 1920x1080");
  }
  const start = Date.now();

  const missingImages = [];
  const runData = loadRunData(runDir, opts.srt);
  const presetOpeningOffset = loadPresetOpeningOffset(opts.channel);
  const openingOffset = resolveOpeningOffset(opts, runData.belt, presetOpeningOffset);
  const belt = { ...runData.belt, opening_offset: openingOffset };
  const warnBeltAscii = belt?.belts?.some((b) => /[A-Za-z]/.test(b.text));
  if (warnBeltAscii) {
    console.warn("⚠️ belt labels contain ASCII; expected Japanese labels.");
  }
  const crossfade = Number.isFinite(opts.crossfade) ? Number(opts.crossfade) : 0.5;
  const presetPosition = resolvePosition(opts, loadPresetPosition(opts.channel));
  const presetLayout = loadPresetLayout(opts.channel);
  const layout = resolveLayout(opts, presetLayout);
  const bgm = resolveAudioRequired(opts, runDir);

  // Normalize/patch image paths and copy to public for static serving
  const scenes = [];
  const cues = ensureCuesHavePaths(runData.imageCues, runDir);
  const publicPaths = ensureImagesCopiedToPublic(cues, runDir, missingImages);
  for (const [idx, c] of publicPaths.entries()) {
    if (!c) continue;
    const start = c.start_sec ?? c.start ?? 0;
    const end = c.end_sec ?? c.end ?? 0;
    if (end <= start) continue;
    scenes.push({ imgPath: c.path, start, end, position: presetPosition, idx });
  }
  fixOverlaps(scenes, crossfade);
  warnOverlaps(scenes);
  warnGaps(scenes);
  const sceneDuration = scenes.length ? Math.max(...scenes.map((s) => s.end)) : 0;
  const subtitleDuration = runData.srtCues?.length ? Math.max(...runData.srtCues.map((s) => s.end)) : 0;
  const baseDuration = Math.max(sceneDuration, subtitleDuration, belt?.total_duration ?? 0);
  const totalDuration = baseDuration + openingOffset;
  if (scenes.length === 0) {
    console.warn("⚠️ No scenes to render (all cues were invalid or missing images).");
    process.exit(1);
  }

  // Bundle Remotion entry
  const entry = path.join(REMOTION_ROOT, "src", "index.ts");
  const bundled = await bundle({
    entryPoint: entry,
    enableCaching: true,
    publicDir: REMOTION_PUBLIC_DIR,
    // ensure ts/tsx are handled
    webpackOverride: (config) => {
      config.resolve ??= {};
      config.resolve.extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"];
      return config;
    },
  });

  const outputLocation = path.resolve(process.cwd(), opts.out || path.join(runDir, "remotion_output.mp4"));

  const comps = await (await import("@remotion/renderer")).getCompositions(bundled, {
    inputProps: {
      scenes,
      belt,
      subtitles: runData.srtCues,
      title: opts.title || runData.episode?.title || "",
      crossfade,
      openingOffset,
      layout,
      bgm,
    },
  });
  const comp = comps.find((c) => c.id === "Main");
  if (!comp) {
    throw new Error("Composition 'Main' not found in bundle");
  }

  const composition = {
    ...comp,
    width,
    height,
    fps,
    durationInFrames: Math.ceil(Math.max(1, totalDuration * fps)),
  };
  const inputProps = {
    scenes,
    belt,
    subtitles: runData.srtCues,
    title: opts.title || runData.episode?.title || "",
    crossfade,
    openingOffset,
    layout,
    bgm,
  };

  let chunkInfo = null;
  if (Number.isFinite(opts.chunkSec) && opts.chunkSec > 0) {
    const chunkDir =
      opts.chunkDir && opts.chunkDir.length > 0
        ? path.resolve(process.cwd(), opts.chunkDir)
        : path.join(REMOTION_OUT_DIR, `chunks_${path.basename(runDir)}`);
    chunkInfo = await renderInChunks({
      composition,
      serveUrl: bundled,
      outputLocation,
      inputProps,
      chunkSec: opts.chunkSec,
      chunkDir,
      resume: Boolean(opts.resumeChunks),
      maxChunksPerRun: Number.isFinite(opts.maxChunksPerRun) ? opts.maxChunksPerRun : Infinity,
    });
  } else {
    await renderMedia({
      composition,
      serveUrl: bundled,
      codec: "h264",
      outputLocation,
      inputProps,
      x264Preset: "ultrafast",
      crf: 20,
    });
  }

  const elapsed = (Date.now() - start) / 1000;
  const log = {
    run: runDir,
    channel: opts.channel || "",
    fps,
    size: { width, height },
    out: outputLocation,
    elapsed_sec: elapsed,
    images: runData.imageCues?.cues?.length || 0,
    belt_labels: belt?.belts?.map((b) => b.text) || [],
    duration_sec: totalDuration,
    content_duration_sec: baseDuration,
    opening_offset: openingOffset,
    missing_images: sortMissing(missingImages),
    missing_count: missingImages.length,
    missing_summary: summarizeMissing(missingImages),
    bgm,
    layout,
    chunk: chunkInfo,
  };
  fs.writeFileSync(path.join(runDir, "remotion_run_info.json"), JSON.stringify(log, null, 2), "utf-8");
  if (missingImages.length > 0) {
    fs.writeFileSync(path.join(runDir, "remotion_missing_images.json"), JSON.stringify(sortMissing(missingImages), null, 2), "utf-8");
  }
  console.log("✅ Remotion render complete:", outputLocation);
  console.log("Log:", log);
  if (missingImages.length > 0) {
    console.warn(
      `⚠️ Missing images: total=${log.missing_summary.total}, local=${log.missing_summary.local}, remote=${log.missing_summary.remote}`
    );
  }
  if (opts.failOnMissing && missingImages.length > 0) {
    console.error(`❌ Missing images detected (${missingImages.length}); exiting due to --fail-on-missing`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Render failed:", err);
  process.exit(1);
});

async function renderInChunks({
  composition,
  serveUrl,
  outputLocation,
  inputProps,
  chunkSec,
  chunkDir,
  resume = false,
  maxChunksPerRun = Infinity,
}) {
  const totalFrames = composition.durationInFrames;
  if (!Number.isFinite(totalFrames) || totalFrames <= 0) {
    throw new Error("Invalid durationInFrames for chunked render");
  }
  const framesPerChunk = Math.max(1, Math.round(chunkSec * composition.fps));
  const chunkCount = Math.ceil(totalFrames / framesPerChunk);
  fs.mkdirSync(chunkDir, { recursive: true });
  const chunkFiles = [];
  let renderedThisRun = 0;

  for (let i = 0; i < chunkCount; i++) {
    const startFrame = i * framesPerChunk;
    const endFrame = Math.min(totalFrames - 1, (i + 1) * framesPerChunk - 1);
    const chunkOut = path.join(chunkDir, `chunk_${String(i).padStart(3, "0")}.mp4`);
    chunkFiles.push(chunkOut);
    if (resume && fs.existsSync(chunkOut) && fs.statSync(chunkOut).size > 0) {
      console.log(`⏩ Skipping existing chunk ${i + 1}/${chunkCount}: ${chunkOut}`);
      continue;
    }
    if (renderedThisRun >= maxChunksPerRun) {
      console.log(`⏹️ Reached max-chunks-per-run=${maxChunksPerRun}, stopping early.`);
      break;
    }
    console.log(`▶️ Rendering chunk ${i + 1}/${chunkCount} frames ${startFrame}-${endFrame} -> ${chunkOut}`);
    await renderMedia({
      composition,
      serveUrl,
      codec: "h264",
      outputLocation: chunkOut,
      inputProps,
      x264Preset: "ultrafast",
      crf: 20,
      frameRange: [startFrame, endFrame],
      overwrite: true,
    });
    renderedThisRun++;
  }

  // Only consider chunks up to the last rendered/available chunk (avoid "phantom" missing entries
  // when maxChunksPerRun stops early).
  let lastExisting = -1;
  for (let i = 0; i < chunkFiles.length; i++) {
    try {
      if (fs.existsSync(chunkFiles[i]) && fs.statSync(chunkFiles[i]).size > 0) {
        lastExisting = i;
      } else {
        break;
      }
    } catch {
      break;
    }
  }
  const stitchedChunks = lastExisting >= 0 ? chunkFiles.slice(0, lastExisting + 1) : [];
  const missingWithinStitched = stitchedChunks.filter((f) => !fs.existsSync(f) || fs.statSync(f).size === 0);
  const missingAll = chunkFiles.filter((f) => !fs.existsSync(f) || fs.statSync(f).size === 0);

  if (missingAll.length === 0) {
    await stitchChunks(chunkFiles, outputLocation);
    return {
      chunkSec,
      chunkCount,
      chunkDir,
      chunks: chunkFiles,
      stitchedTo: outputLocation,
      resumed: resume,
      renderedThisRun,
      finished: true,
      missingChunks: [],
    };
  }

  if (stitchedChunks.length > 0 && missingWithinStitched.length === 0) {
    const partialOut = outputLocation.replace(/\.mp4$/i, "") + "_partial.mp4";
    await stitchChunks(stitchedChunks, partialOut);
    console.warn(
      `⚠️ Chunk render incomplete: missing ${missingAll.length}/${chunkFiles.length} chunks. Stitched partial preview: ${partialOut}`,
    );
    return {
      chunkSec,
      chunkCount,
      chunkDir,
      chunks: chunkFiles,
      stitchedTo: partialOut,
      resumed: resume,
      renderedThisRun,
      finished: false,
      missingChunks: missingAll,
    };
  }

  console.warn(`⚠️ Chunk render incomplete: missing ${missingAll.length}/${chunkFiles.length} chunks. Skipping stitch.`);
  return {
    chunkSec,
    chunkCount,
    chunkDir,
    chunks: chunkFiles,
    stitchedTo: null,
    resumed: resume,
    renderedThisRun,
    finished: false,
    missingChunks: missingAll,
  };
}

async function stitchChunks(chunkFiles, outFile) {
  if (chunkFiles.length === 1) {
    fs.copyFileSync(chunkFiles[0], outFile);
    return;
  }
  const listFile = path.join(path.dirname(outFile), `chunks_${Date.now()}.txt`);
  fs.writeFileSync(listFile, chunkFiles.map((f) => `file '${f.replace(/'/g, "'\\''")}'`).join("\n"), "utf-8");
  try {
    await runFfmpeg(["-y", "-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", outFile]);
  } finally {
    try {
      fs.unlinkSync(listFile);
    } catch {}
  }
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const p = spawn("ffmpeg", args);
    p.stdout.on("data", (d) => process.stdout.write(d));
    p.stderr.on("data", (d) => process.stderr.write(d));
    p.on("error", reject);
    p.on("close", (code) => {
      if (code === 0) return resolve();
      reject(new Error(`ffmpeg exited with code ${code}`));
    });
  });
}

async function resolveImgPath(runDir, p, missingList = [], checkRemote = false, idx = -1, remoteTimeoutMs = 4000, remoteRetries = 2) {
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
      fs.readFileSync(path.join(VIDEO_PIPELINE_ROOT, "config", "channel_presets.json"), "utf-8")
    );
    const preset = cfg?.channels?.[channel];
    const pos = preset?.position;
    if (pos && typeof pos.tx === "number" && typeof pos.ty === "number" && typeof pos.scale === "number") {
      return pos;
    }
  } catch {
    // ignore and fallback
  }
  return { tx: 0, ty: 0, scale: 1 };
}

function resolvePosition(opts, preset) {
  const tx = Number.isFinite(opts.tx) ? opts.tx : preset.tx;
  const ty = Number.isFinite(opts.ty) ? opts.ty : preset.ty;
  const scale = Number.isFinite(opts.scale) ? opts.scale : preset.scale;
  return { tx, ty, scale };
}

function loadPresetLayout(channel) {
  try {
    const defaultsPath = path.join(REMOTION_ROOT, "preset_layouts.json");
    const defaults = fs.existsSync(defaultsPath) ? JSON.parse(fs.readFileSync(defaultsPath, "utf-8")) : {};

    let layout = {};
    if (channel) {
      const cfg = JSON.parse(
        fs.readFileSync(path.join(VIDEO_PIPELINE_ROOT, "config", "channel_presets.json"), "utf-8")
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
      beltInsetPx: toNum(layout.beltInsetPx),
      subtitleBottomPx: toNum(layout.subtitleBottomPx),
      subtitleMaxWidthPct: toNum(layout.subtitleMaxWidthPct),
      subtitleFontSize: toNum(layout.subtitleFontSize),
      beltMainScale: toNum(layout.beltMainScale),
      beltSubScale: toNum(layout.beltSubScale),
      beltGapScale: toNum(layout.beltGapScale),
    };
  } catch {}
  return {};
}

function warnOverlaps(scenes) {
  const sorted = [...scenes].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const curr = sorted[i];
    if (curr.start < prev.end) {
      console.warn(`⚠️ Scene overlap: prev [${prev.start}-${prev.end}] curr [${curr.start}-${curr.end}]`);
    }
  }
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

function warnGaps(scenes) {
  const sorted = [...scenes].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i++) {
    const prevEnd = sorted[i - 1].end;
    const gap = sorted[i].start - prevEnd;
    if (gap > 0.2) {
      console.warn(`⚠️ Scene gap detected: gap=${gap.toFixed(2)}s between [${prevEnd}] and [${sorted[i].start}]`);
    }
  }
}

function resolveLayout(opts, presetLayout = {}) {
  const beltTopPct = Number.isFinite(opts.beltTop) ? opts.beltTop : toNum(presetLayout.beltTopPct);
  const beltHeightPct = Number.isFinite(opts.beltHeight) ? opts.beltHeight : toNum(presetLayout.beltHeightPct);
  const beltInsetPx = toNum(presetLayout.beltInsetPx);
  const subtitleBottomPx = Number.isFinite(opts.subtitleBottom) ? opts.subtitleBottom : toNum(presetLayout.subtitleBottomPx);
  const subtitleMaxWidthPct = Number.isFinite(opts.subtitleMaxWidth) ? opts.subtitleMaxWidth : toNum(presetLayout.subtitleMaxWidthPct);
  const subtitleFontSize = Number.isFinite(opts.subtitleFontSize) ? opts.subtitleFontSize : toNum(presetLayout.subtitleFontSize);
  const beltMainScale = toNum(presetLayout.beltMainScale);
  const beltSubScale = toNum(presetLayout.beltSubScale);
  const beltGapScale = toNum(presetLayout.beltGapScale);
  return {
    beltTopPct,
    beltHeightPct,
    beltInsetPx,
    subtitleBottomPx,
    subtitleMaxWidthPct,
    subtitleFontSize,
    beltMainScale,
    beltSubScale,
    beltGapScale,
  };
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

async function headWithRetry(url, retries = 1, timeoutMs = 4000, fallbackGet = false) {
  for (let i = 0; i <= retries; i++) {
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), timeoutMs);
      let res = await fetch(url, { method: "HEAD", redirect: "follow", signal: controller.signal });
      if (fallbackGet && !res.ok) {
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

// Voice-over audio is mandatory. If not found, throw.
function resolveAudioRequired(opts, runDir) {
  let p = opts.bgm;

  // Auto-pick audio if not explicitly specified: try <run>/<basename>.{wav,mp3,m4a,flac}
  if (!p) {
    const base = path.basename(runDir);
    const candidates = ["wav", "mp3", "m4a", "flac"].map((ext) => path.join(runDir, `${base}.${ext}`));
    const foundByName = candidates.find((c) => fs.existsSync(c));
    if (foundByName) {
      p = foundByName;
      console.log(`🎵 auto-picked audio: ${p}`);
    } else {
      try {
        const firstAudio = (fs.readdirSync(runDir) || []).find((f) => /\.(wav|mp3|m4a|flac)$/i.test(f));
        if (firstAudio) {
          p = path.join(runDir, firstAudio);
          console.log(`🎵 auto-picked audio: ${p}`);
        }
      } catch {}
    }
  }

  if (!p) {
    throw new Error(`Audio not found in runDir: expected ${path.basename(runDir)}.(wav|mp3|m4a|flac) or specify --bgm`);
  }

  let resolved = p;
  const isRemote = p.startsWith("http://") || p.startsWith("https://");
  if (!isRemote && !path.isAbsolute(p)) {
    resolved = path.resolve(runDir, p);
  }
  if (!isRemote && !fs.existsSync(resolved)) {
    console.warn(`⚠️ BGM file not found: ${resolved}`);
    return undefined;
  }
  // Copy local BGM into public so Remotion can load it via staticFile
  if (!isRemote) {
    const pubDir = path.join(REMOTION_PUBLIC_DIR, "_bgm", path.basename(runDir));
    fs.mkdirSync(pubDir, { recursive: true });
    const dest = path.join(pubDir, path.basename(resolved));
    try {
      fs.copyFileSync(resolved, dest);
      // remap to public-relative path for staticFile
      resolved = `_bgm/${path.basename(runDir)}/${path.basename(resolved)}`;
    } catch (e) {
      console.warn(`⚠️ Failed to copy BGM: ${resolved} -> ${dest}`, e);
      return undefined;
    }
  }
  return {
    src: resolved,
    volume: Number.isFinite(opts.bgmVolume) ? opts.bgmVolume : 0.32,
    fadeSec: Number.isFinite(opts.bgmFade) ? opts.bgmFade : 1.5,
  };
}

// If cues have no path, try to assign from images/*.png in runDir
function ensureCuesHavePaths(imageCues, runDir) {
  const cues = imageCues.cues || [];
  const needsPath = cues.some((c) => !c.path);
  if (!needsPath) return cues;
  const imgDir = path.join(runDir, "images");
  if (!fs.existsSync(imgDir)) return cues;
  const files = fs
    .readdirSync(imgDir)
    .filter((f) => f.toLowerCase().endsWith(".png") || f.toLowerCase().endsWith(".jpg") || f.toLowerCase().endsWith(".jpeg"))
    .sort();
  if (files.length === 0) return cues;
  const patched = cues.map((c, i) => ({ ...c, path: c.path || path.join(imgDir, files[i % files.length]) }));
  imageCues.cues = patched;
  return patched;
}

// Copy all images to remotion/public/_auto/<run> and return patched cues with public-relative paths
function ensureImagesCopiedToPublic(cues, runDir, missingList) {
  const rels = [];
  const pubDir = REMOTION_PUBLIC_DIR;
  const autoDir = path.join(pubDir, "_auto", path.basename(runDir));
  fs.mkdirSync(autoDir, { recursive: true });
  for (const c of cues) {
    if (!c || !c.path) continue;
    const p = c.path;
    if (p.startsWith("http://") || p.startsWith("https://")) {
      rels.push(c);
      continue;
    }
    let abs = p;
    if (!path.isAbsolute(p)) {
      abs = path.resolve(runDir, p);
    }
    if (!fs.existsSync(abs)) {
      missingList.push({ path: abs, idx: c.idx ?? c.index ?? -1, type: "local" });
      continue;
    }
    const basename = path.basename(abs);
    const dest = path.join(autoDir, basename);
    try {
      fs.copyFileSync(abs, dest);
    } catch (e) {
      missingList.push({ path: abs, idx: c.idx ?? c.index ?? -1, type: "copy_failed" });
      continue;
    }
    rels.push({ ...c, path: `_auto/${path.basename(runDir)}/${basename}` });
  }
  return rels;
}
