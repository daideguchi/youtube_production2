// Fetch-based API client to avoid extra deps

import { apiUrl } from "./baseUrl";

export async function getLlmUsageLogs(limit: number) {
  const res = await fetch(apiUrl(`/api/llm-usage?limit=${limit}`));
  if (!res.ok) throw new Error(`Failed to fetch usage logs: ${res.status}`);
  return res.json();
}

export async function getLlmOverrides() {
  const res = await fetch(apiUrl(`/api/llm-usage/overrides`));
  if (!res.ok) throw new Error(`Failed to fetch overrides: ${res.status}`);
  return res.json();
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
  return res.json();
}

export async function getLlmModels() {
  const res = await fetch(apiUrl(`/api/llm-usage/models`));
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
  const data = await res.json();
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
  return res.json();
}
