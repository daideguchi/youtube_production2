#!/usr/bin/env node
/**
 * Belt generator that uses Python LLMRouter (slot-based routing).
 * - No direct provider/model selection in JS (prevents model-name drift).
 * - --no-fallback: fail on LLM error (no even split).
 */
import fs from "fs";
import path from "path";
import dotenv from "dotenv";
import { spawnSync } from "child_process";

const findRepoRoot = (startDir) => {
  let dir = startDir;
  for (let i = 0; i < 12; i++) {
    if (fs.existsSync(path.join(dir, "pyproject.toml"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
};

const REPO_ROOT = findRepoRoot(process.cwd()) || process.cwd();
const REMOTION_ROOT = path.join(REPO_ROOT, "apps", "remotion");

(() => {
  const candidates = [];
  if (REPO_ROOT) candidates.push(path.join(REPO_ROOT, ".env"));
  candidates.push(path.join(process.cwd(), ".env"));

  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) candidates.push(path.join(home, ".env"));

  const extra = process.env.YTM_ENV_PATH;
  if (extra) candidates.push(extra);

  for (const p of candidates) {
    if (!p) continue;
    if (fs.existsSync(p)) dotenv.config({ path: p, override: false });
  }
})();

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = { labels: 4, noFallback: false, openingOffset: 0.0 };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--srt") opts.srt = args[++i];
    else if (a === "--out") opts.out = args[++i];
    else if (a === "--labels") opts.labels = Math.max(1, Number(args[++i] || 4));
    else if (a === "--title") opts.title = args[++i];
    else if (a === "--no-fallback") opts.noFallback = true;
    else if (a === "--opening-offset") opts.openingOffset = Number(args[++i] || 0.0);
  }
  if (!opts.srt) throw new Error("--srt <file> is required");
  return opts;
}

function parseTime(t) {
  const m = t.match(/(\d+):(\d+):(\d+),(\d+)/);
  if (!m) return 0;
  const [_, h, mnt, s, ms] = m;
  return Number(h) * 3600 + Number(mnt) * 60 + Number(s) + Number(ms) / 1000;
}

function parseSrt(text) {
  const blocks = text.split(/\r?\n\r?\n/);
  const cues = [];
  for (const b of blocks) {
    const lines = b.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) continue;
    const tm = (lines[1] || lines[0]).match(/(.+)\s-->\s(.+)/);
    if (!tm) continue;
    const start = parseTime(tm[1].trim());
    const end = parseTime(tm[2].trim());
    const textLines = lines.slice(2).join(" ").trim();
    cues.push({ start, end, text: textLines });
  }
  return cues;
}

async function callLLM(prompt, maxTokens = 200) {
  const python = process.env.PYTHON_BIN || process.env.PYTHON || "python3";
  const routerTask = process.env.BELT_LLM_TASK || "belt_generation";
  const timeout = Number(process.env.BELT_LLM_TIMEOUT_SEC || 120);
  const temperature = Number(process.env.BELT_LLM_TEMPERATURE || 0.2);

  const pyCode = `
import json
import os
import sys

from factory_common.llm_router import get_router

payload = json.loads(sys.stdin.read() or "{}")
prompt = str(payload.get("prompt") or "").strip()
task = str(payload.get("task") or "belt_generation").strip() or "belt_generation"
timeout = int(payload.get("timeout") or 120)
max_tokens = payload.get("max_tokens")
temperature = payload.get("temperature")
if max_tokens is not None:
    try:
        max_tokens = int(max_tokens)
    except Exception:
        max_tokens = None
if temperature is not None:
    try:
        temperature = float(temperature)
    except Exception:
        temperature = None

router = get_router()
content = router.call(
    task=task,
    messages=[{"role": "user", "content": prompt}],
    temperature=temperature,
    max_tokens=max_tokens,
    response_format="json_object",
    timeout=timeout,
)
if isinstance(content, list):
    text = " ".join(str(part.get("text", "")).strip() for part in content if isinstance(part, dict)).strip()
else:
    text = str(content or "").strip()
sys.stdout.write(text)
`;

  const env = { ...process.env };
  env.PYTHONPATH = env.PYTHONPATH || `${REPO_ROOT}:${path.join(REPO_ROOT, "packages")}`;

  const payload = {
    prompt,
    task: routerTask,
    timeout,
    max_tokens: maxTokens,
    temperature,
  };
  const res = spawnSync(python, ["-c", pyCode], {
    input: JSON.stringify(payload),
    encoding: "utf-8",
    env,
    cwd: REPO_ROOT,
    maxBuffer: 10 * 1024 * 1024,
  });
  if (res.error) throw res.error;

  const stdout = String(res.stdout || "").trim();
  const stderr = String(res.stderr || "").trim();
  if (res.status !== 0) {
    throw new Error(stderr || stdout || `python exited with code ${res.status}`);
  }
  if (!stdout) {
    throw new Error("LLMRouter returned empty output");
  }
  return stdout;
}

function clampBelts(belts, total) {
  const cleaned = [];
  for (const b of belts) {
    if (!b) continue;
    const start = Math.max(0, Number(b.start ?? 0));
    const end = Math.min(total, Number(b.end ?? 0));
    const label = typeof b.label === "string" ? b.label.trim() : typeof b.text === "string" ? b.text.trim() : "";
    if (!label) continue;
    if (end > start) cleaned.push({ text: label, start, end });
  }
  cleaned.sort((a, b) => a.start - b.start);
  return cleaned;
}

function normalizeTimeline(belts, total) {
  if (!Array.isArray(belts) || belts.length === 0) return belts;
  const maxEnd = Math.max(...belts.map((b) => b.end || 0), 0);
  const scale = maxEnd > 0 ? total / maxEnd : 1;
  const scaled = belts.map((b) => ({
    ...b,
    start: Math.max(0, Number(b.start || 0) * scale),
    end: Math.max(0, Number(b.end || 0) * scale),
  }));
  const sorted = scaled.sort((a, b) => a.start - b.start);
  let prevEnd = 0;
  for (const seg of sorted) {
    if (seg.start < prevEnd) seg.start = prevEnd;
    if (seg.end <= seg.start) seg.end = seg.start + Math.max(total * 0.002, 0.5); // ensure minimal length
    if (seg.end > total) seg.end = total;
    prevEnd = seg.end;
  }
  if (sorted.length > 0 && sorted[sorted.length - 1].end < total) {
    sorted[sorted.length - 1].end = total;
  }
  return sorted;
}

function addOrdinalPrefix(belts) {
  return belts.map((b, idx) => ({
    ...b,
    text: `${idx + 1}. ${String(b.text || "")}`.trim(),
  }));
}

function loadPromptTemplate(total, maxLabels, summary) {
  const tplPath =
    process.env.BELT_PROMPT_PATH ||
    path.join(REMOTION_ROOT, "scripts", "prompts", "belt_prompt.txt");
  let tpl = null;
  try {
    if (fs.existsSync(tplPath)) {
      tpl = fs.readFileSync(tplPath, "utf-8");
    }
  } catch {}
  if (!tpl) {
    tpl =
      `動画の総尺は約{{TOTAL_SEC}}秒です。文章量と転換点に基づき、最大{{MAX_LABELS}}個のセクションに分け、` +
      `JSON object like { "belts": [{ "start": 秒, "end": 秒, "text": "短い見出し" }] } を返してください。\n` +
      `制約: 6-12文字程度の日本語。重複なし、昇順、0<=start<end<={{TOTAL_SEC}}。等分禁止。\n` +
      `分割数はテキスト量に応じて決めてよい（上限 {{MAX_LABELS}}）。\n` +
      `禁止: 人名/キャラ名（例: ミホ, サナエ, 彼女, 彼）、抽象語のみ（導入/まとめ/説明/苦悩/物語/日常/気づき 等）。\n` +
      `トーン: 具体+感情+変化で視聴者を引きつける。ネガ一辺倒は避け、希望・決意・変化も織り交ぜる。\n` +
      `要約タイムライン:\n{{SUMMARY}}`;
  }
  return tpl
    .replace(/{{TOTAL_SEC}}/g, total.toFixed(1))
    .replace(/{{MAX_LABELS}}/g, String(maxLabels))
    .replace(/{{SUMMARY}}/g, summary);
}

async function main() {
  const opts = parseArgs();
  const srtPath = path.resolve(opts.srt);
  const outPath = path.resolve(opts.out || path.join(path.dirname(srtPath), "belt_config.generated.json"));
  const srtText = fs.readFileSync(srtPath, "utf-8");
  const cues = parseSrt(srtText);
  const total = cues.length > 0 ? Math.max(...cues.map((c) => c.end)) : 0;
  // Preserve existing episode title if present
  let existingEpisode = "";
  try {
    if (fs.existsSync(outPath)) {
      const prev = JSON.parse(fs.readFileSync(outPath, "utf-8"));
      if (prev?.episode) existingEpisode = String(prev.episode);
    }
    const sibling = path.join(path.dirname(outPath), "belt_config.json");
    if (!existingEpisode && fs.existsSync(sibling)) {
      const prev = JSON.parse(fs.readFileSync(sibling, "utf-8"));
      if (prev?.episode) existingEpisode = String(prev.episode);
    }
  } catch {}
  const episodeTitle = opts.title || existingEpisode || path.basename(srtPath, path.extname(srtPath));

  let belts = [];
  let usedLLM = false;
  try {
    const summary = cues
      .slice(0, 120)
      .map((c) => `${c.start.toFixed(1)}-${c.end.toFixed(1)} ${c.text}`)
      .join("\n")
      .slice(0, 4000);
    const prompt = loadPromptTemplate(total, opts.labels, summary);
    const raw = await callLLM(prompt, 200);
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      const dump = path.join(REMOTION_ROOT, "out", "belt_llm_raw.json");
      try {
        fs.writeFileSync(dump, raw, "utf-8");
      } catch {}
      throw new Error(`LLM JSON parse failed: ${e?.message || e}. raw saved to ${dump}`);
    }
    const beltsRaw = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.belts) ? parsed.belts : [];
    belts = clampBelts(beltsRaw, total);
    belts = normalizeTimeline(belts, total);
    belts = addOrdinalPrefix(belts);
    usedLLM = true;
  } catch (e) {
    console.error("❌ LLM生成失敗（フォールバックなし）:", e.message || e);
    process.exit(1);
  }

  const out = {
    episode: episodeTitle,
    total_duration: total,
    belts,
    source: usedLLM ? "llm" : "fallback",
    model: usedLLM ? "belt_generation" : "preset",
  };
  fs.writeFileSync(outPath, JSON.stringify(out, null, 2), "utf-8");
  console.log("✅ belt generated:", outPath);
}

main().catch((err) => {
  console.error("Failed:", err);
  process.exit(1);
});
