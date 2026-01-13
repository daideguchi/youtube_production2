import { apiUrl } from "./baseUrl";

export type GhReleasesArchiveItem = {
  archive_id: string;
  created_at: string;
  repo: string;
  release_tag: string;
  original_name?: string | null;
  original_size_bytes?: number | null;
  original_sha256?: string | null;
  tags: string[];
  note: string;
};

export type GhReleasesArchiveStatus = {
  archive_dir: string;
  manifest_path: string;
  latest_index_path: string;
  manifest_exists: boolean;
  latest_index_exists: boolean;
  manifest_entry_count: number;
  latest_index_count: number;
};

export type GhReleasesArchiveSearchResponse = {
  query: string;
  tag: string;
  offset: number;
  limit: number;
  total: number;
  items: GhReleasesArchiveItem[];
};

export type GhReleasesArchiveTagCount = { tag: string; count: number };
export type GhReleasesArchiveTagsResponse = { items: GhReleasesArchiveTagCount[] };

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return apiUrl(path);
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getGhReleasesArchiveStatus(): Promise<GhReleasesArchiveStatus> {
  return fetchJson<GhReleasesArchiveStatus>("/api/gh-releases-archive/status");
}

export async function getGhReleasesArchiveLatest(limit = 200): Promise<GhReleasesArchiveItem[]> {
  const search = new URLSearchParams();
  if (limit) search.set("limit", String(limit));
  return fetchJson<GhReleasesArchiveItem[]>(`/api/gh-releases-archive/latest?${search.toString()}`);
}

export async function searchGhReleasesArchive(params: {
  query?: string;
  tag?: string;
  offset?: number;
  limit?: number;
}): Promise<GhReleasesArchiveSearchResponse> {
  const search = new URLSearchParams();
  if (params.query) search.set("query", params.query);
  if (params.tag) search.set("tag", params.tag);
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  return fetchJson<GhReleasesArchiveSearchResponse>(`/api/gh-releases-archive/search?${search.toString()}`);
}

export async function getGhReleasesArchiveTags(): Promise<GhReleasesArchiveTagsResponse> {
  return fetchJson<GhReleasesArchiveTagsResponse>("/api/gh-releases-archive/tags");
}

