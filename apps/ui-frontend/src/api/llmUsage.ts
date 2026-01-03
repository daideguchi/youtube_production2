// Fetch-based API client to avoid extra deps

import { apiUrl } from "./baseUrl";

async function readJsonOrThrow(res: Response, context: string) {
  const text = await res.text().catch(() => "");
  try {
    return JSON.parse(text);
  } catch {
    const head = (text || "").slice(0, 200);
    throw new Error(`${context}: Invalid JSON response. head=${JSON.stringify(head)}`);
  }
}

export async function getLlmUsageLogs(limit: number) {
  const res = await fetch(apiUrl(`/api/llm-usage?limit=${limit}`));
  if (!res.ok) throw new Error(`Failed to fetch usage logs: ${res.status}`);
  return readJsonOrThrow(res, `Failed to parse usage logs (${res.status})`);
}

export async function getLlmOverrides() {
  const res = await fetch(apiUrl(`/api/llm-usage/overrides`));
  if (!res.ok) throw new Error(`Failed to fetch overrides: ${res.status}`);
  return readJsonOrThrow(res, `Failed to parse overrides (${res.status})`);
}

export async function saveLlmOverrides(body: any) {
  const res = await fetch(apiUrl(`/api/llm-usage/overrides`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to save overrides: ${res.status} ${text}`);
  }
  return readJsonOrThrow(res, `Failed to parse save overrides response (${res.status})`);
}

export async function getLlmModels() {
  const res = await fetch(apiUrl(`/api/llm-usage/models`));
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
  const data = await readJsonOrThrow(res, `Failed to parse models (${res.status})`);
  return data.models || [];
}

export async function getLlmUsageSummary(params: { range?: string; topN?: number; provider?: string }) {
  const range = params.range ?? "today_jst";
  const topN = params.topN ?? 12;
  const provider = params.provider ?? "";
  const qs = new URLSearchParams();
  qs.set("range", range);
  qs.set("top_n", String(topN));
  if (provider) {
    qs.set("provider", provider);
  }
  const res = await fetch(apiUrl(`/api/llm-usage/summary?${qs.toString()}`));
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Failed to fetch usage summary: ${res.status} ${text}`);
  }
  return readJsonOrThrow(res, `Failed to parse usage summary (${res.status})`);
}

export async function getFireworksKeyStatus(params?: { pools?: string }) {
  const pools = params?.pools ?? "script,image";
  const qs = new URLSearchParams();
  qs.set("pools", pools);
  const res = await fetch(apiUrl(`/api/llm-usage/fireworks/status?${qs.toString()}`));
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Failed to fetch Fireworks key status: ${res.status} ${text}`);
  }
  return readJsonOrThrow(res, `Failed to parse Fireworks key status (${res.status})`);
}

export async function probeFireworksKeys(params: { pool: "script" | "image" | "all"; limit?: number }) {
  const qs = new URLSearchParams();
  qs.set("pool", params.pool);
  if (params.limit != null) {
    qs.set("limit", String(params.limit));
  }
  const res = await fetch(apiUrl(`/api/llm-usage/fireworks/probe?${qs.toString()}`), { method: "POST" });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Failed to probe Fireworks keys: ${res.status} ${text}`);
  }
  return readJsonOrThrow(res, `Failed to parse Fireworks probe result (${res.status})`);
}

export async function getScriptRoutes(params: { channels: string; maxVideos?: number }) {
  const qs = new URLSearchParams();
  qs.set("channels", params.channels);
  if (params.maxVideos != null) {
    qs.set("max_videos", String(params.maxVideos));
  }
  const res = await fetch(apiUrl(`/api/llm-usage/script-routes?${qs.toString()}`));
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Failed to fetch script routes: ${res.status} ${text}`);
  }
  return readJsonOrThrow(res, `Failed to parse script routes (${res.status})`);
}
