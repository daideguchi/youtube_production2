#!/usr/bin/env node
/**
 * Belt generator that reads provider/model from configs/llm_registry.json + configs/llm_model_registry.yaml.
 * - Supports Azure (responses API preferred when use_responses_api=true) and OpenRouter chat.
 * - No hardcoded model names; env can override via LLM_MODEL / LLM_PROVIDER.
 * - Endpoint is normalized (strip /openai..., openai.azure.com -> cognitiveservices.azure.com).
 * - --no-fallback: fail on LLM error (no even split).
 */
import fs from "fs";
import path from "path";
import dotenv from "dotenv";

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
  const opts = { labels: 4, noFallback: false, openingOffset: 3.0 };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--srt") opts.srt = args[++i];
    else if (a === "--out") opts.out = args[++i];
    else if (a === "--labels") opts.labels = Math.max(1, Number(args[++i] || 4));
    else if (a === "--title") opts.title = args[++i];
    else if (a === "--no-fallback") opts.noFallback = true;
    else if (a === "--opening-offset") opts.openingOffset = Number(args[++i] || 3.0);
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

// Remove equal-split and preset fallback: if LLM fails and --no-fallback is set, exit with error.

function loadJsonRegistry() {
  try {
    const p = path.join(process.cwd(), "configs", "llm_registry.json");
    return JSON.parse(fs.readFileSync(p, "utf-8"));
  } catch {
    return {};
  }
}

function loadYamlRegistry() {
  try {
    const p = path.join(process.cwd(), "configs", "llm_model_registry.yaml");
    if (!fs.existsSync(p)) return {};
    const lines = fs.readFileSync(p, "utf-8").split(/\r?\n/);
    const models = {};
    let cur = null;
    let indent = null;
    for (const line of lines) {
      if (!line.trim()) continue;
      const mm = line.match(/^([ ]{2})([A-Za-z0-9._/+:-]+):\s*$/);
      if (mm && mm[2] !== "models") {
        cur = mm[2];
        indent = mm[1];
        models[cur] = {};
        continue;
      }
      if (cur && indent && line.startsWith(indent.repeat(2))) {
        const kv = line.trim().split(":");
        const key = kv.shift().trim();
        const val = kv.join(":").trim().replace(/^["']|["']$/g, "");
        models[cur][key] = val;
      }
    }
    return models;
  } catch {
    return {};
  }
}

function resolveModelConfig(modelKey = "belt_generation") {
  const json = loadJsonRegistry();
  const entryJson = json[modelKey] || json.general || {};
  const yaml = loadYamlRegistry();
  const envModel = process.env.LLM_MODEL || process.env.AZURE_OPENAI_DEPLOYMENT;
  const envProvider = process.env.LLM_PROVIDER;
  const model = envModel || entryJson.model || "gpt-5-mini";
  const yamlEntry = yaml[model] || {};
  const provider = envProvider || entryJson.provider || yamlEntry.provider || "azure";
  return { model, provider, yamlEntry };
}

function normalizeEndpoint(raw) {
  let ep = raw || "";
  if (ep.includes("/openai")) ep = ep.split("/openai")[0];
  if (ep.includes("openai.azure.com")) ep = ep.replace("openai.azure.com", "cognitiveservices.azure.com");
  return ep.replace(/\/+$/, "");
}

async function callAzureResponses(prompt, maxTokens, cfg) {
  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  const endpoint = normalizeEndpoint(process.env.AZURE_OPENAI_ENDPOINT || cfg.endpoint || "");
  const apiVersionResponses = process.env.AZURE_OPENAI_RESPONSES_API_VERSION || cfg.api_version_responses || "2025-03-01-preview";
  if (!cfg.deployment && !cfg.model) throw new Error("Azure config missing deployment/model");
  if (!apiKey || !endpoint) throw new Error("Azure config missing endpoint/apiKey");

  const url = `${endpoint}/openai/responses?api-version=${apiVersionResponses}`;
  const body = {
    model: cfg.deployment || cfg.model,
    input: [
      {
        role: "system",
        content:
          "You generate short Japanese section labels for a video timeline. Return only JSON array of objects {start,end,label}. Keep label 4-8 Japanese chars. Ensure non-overlapping, ordered, within duration.",
      },
      { role: "user", content: prompt },
    ],
    max_output_tokens: maxTokens,
    reasoning: { effort: "minimal" },
    text: { verbosity: "low" },
  };
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "api-key": apiKey },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Azure responses error ${res.status} ${await res.text()}`);
  const data = await res.json();
  const out = parseResponsesOutput(data);
  if (!out) throw new Error("Azure responses: empty content");
  return out;
}

async function callAzureChat(prompt, maxTokens, cfg) {
  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  const endpoint = normalizeEndpoint(process.env.AZURE_OPENAI_ENDPOINT || cfg.endpoint || "");
  const apiVersion = process.env.AZURE_OPENAI_API_VERSION || cfg.api_version || "2024-12-01-preview";
  const deployment = cfg.deployment || cfg.model;
  if (!deployment) throw new Error("Azure chat config missing deployment/model");
  if (!apiKey || !endpoint) throw new Error("Azure chat config missing endpoint/apiKey");
  const url = `${endpoint}/openai/deployments/${deployment}/chat/completions?api-version=${apiVersion}`;
  const body = {
    messages: [
      {
        role: "system",
        content:
          "You generate short Japanese section labels for a video timeline. Return only JSON array of objects {start,end,label}. Keep label 4-8 Japanese chars. Ensure non-overlapping, ordered, within duration.",
      },
      { role: "user", content: prompt },
    ],
    max_tokens: maxTokens,
    temperature: 0,
  };
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "api-key": apiKey,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Azure chat error ${res.status} ${await res.text()}`);
  const data = await res.json();
  const content = data?.choices?.[0]?.message?.content;
  if (!content) throw new Error("Azure chat: empty content");
  return String(content).trim();
}

async function callOpenRouter(prompt, maxTokens, cfg) {
  const apiKey = process.env.OPENROUTER_API_KEY;
  const baseUrl = process.env.OPENROUTER_BASE_URL || cfg.endpoint || "https://openrouter.ai/api/v1";
  const model = cfg.model || "meta-llama/llama-3.3-70b-instruct:free";
  if (!apiKey) throw new Error("OpenRouter config missing OPENROUTER_API_KEY");
  const url = `${baseUrl.replace(/\/+$/, "")}/chat/completions`;
  const body = {
    model,
    messages: [
      {
        role: "system",
        content:
          "You generate short Japanese section labels for a video timeline. Return only JSON array of objects {start,end,label}. Keep label 4-8 Japanese chars. Ensure non-overlapping, ordered, within duration.",
      },
      { role: "user", content: prompt },
    ],
    max_tokens: maxTokens,
    temperature: 0,
  };
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`OpenRouter error ${res.status} ${await res.text()}`);
  const data = await res.json();
  const content = data?.choices?.[0]?.message?.content;
  if (!content) throw new Error("OpenRouter: empty content");
  return String(content).trim();
}

function parseResponsesOutput(data) {
  if (data?.output_text) return String(data.output_text).trim();
  if (Array.isArray(data?.output)) {
    const texts = [];
    for (const item of data.output) {
      if (item?.type === "message") {
        for (const c of item.content || []) {
          if (c?.text) texts.push(c.text);
          if (c?.output_text) texts.push(c.output_text);
        }
      }
    }
    if (texts.length) return texts.join("\n").trim();
  }
  if (Array.isArray(data?.choices) && data.choices[0]?.message?.content) {
    return String(data.choices[0].message.content).trim();
  }
  return "";
}

async function callLLM(prompt, maxTokens = 200) {
  const { model, provider, yamlEntry } = resolveModelConfig("belt_generation");
  const cfg = { model, ...yamlEntry };
  if (provider === "openrouter") {
    return await callOpenRouter(prompt, maxTokens, cfg);
  }
  const useResponses = yamlEntry.use_responses_api !== false;
  if (provider === "azure" && useResponses) {
    return await callAzureResponses(prompt, maxTokens, cfg);
  }
  if (provider === "azure") {
    return await callAzureChat(prompt, maxTokens, cfg);
  }
  throw new Error(`Unsupported provider for belt_generation: ${provider}`);
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
      `JSON array like [{ "start": 秒, "end": 秒, "label": "短い見出し" }] を返してください。\n` +
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
    belts = clampBelts(parsed, total);
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
