// Fetch-based API client to avoid extra deps

import { apiUrl } from "./baseUrl";

export async function getLlmUsageLogs(limit: number) {
  const res = await fetch(apiUrl(`/llm-usage?limit=${limit}`));
  if (!res.ok) throw new Error(`Failed to fetch usage logs: ${res.status}`);
  return res.json();
}

export async function getLlmOverrides() {
  const res = await fetch(apiUrl(`/llm-usage/overrides`));
  if (!res.ok) throw new Error(`Failed to fetch overrides: ${res.status}`);
  return res.json();
}

export async function saveLlmOverrides(body: any) {
  const res = await fetch(apiUrl(`/llm-usage/overrides`), {
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
  const res = await fetch(apiUrl(`/llm-usage/models`));
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
  const data = await res.json();
  return data.models || [];
}
