import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { fetchResearchFile } from "../api/client";
import type { BenchmarkChannelSpec, BenchmarkScriptSampleSpec, ChannelSummary } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./BenchmarksPage.css";

const CHARS_PER_SECOND = 6.0;

function compareChannelCode(a: string, b: string): number {
  const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
  const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
  const aNum = Number.isFinite(an);
  const bNum = Number.isFinite(bn);
  if (aNum && bNum) {
    return an - bn;
  }
  if (aNum) return -1;
  if (bNum) return 1;
  return a.localeCompare(b, "ja-JP");
}

function resolveChannelDisplayName(channel: ChannelSummary): string {
  return channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
}

function normalizeHandle(value?: string | null): string | null {
  const trimmed = (value ?? "").trim();
  if (!trimmed) return null;
  return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

function resolveCompetitorUrl(item: BenchmarkChannelSpec): string | null {
  const url = (item.url ?? "").trim();
  if (url) return url;
  const handle = normalizeHandle(item.handle);
  if (!handle) return null;
  return `https://www.youtube.com/${handle}`;
}

function formatBytes(value?: number): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 1024) return `${value.toLocaleString("ja-JP")} B`;
  const kb = value / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(1)} MB`;
}

function formatCompactNumber(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ja-JP", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatShortDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("ja-JP");
}

function formatDurationSeconds(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const total = Math.max(0, Math.round(value));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

function extractYouTubeHandleFromUrl(url: string): string | null {
  const raw = (url ?? "").trim();
  if (!raw) return null;
  const match = raw.match(/\/@([^/?#]+)/);
  if (!match) return null;
  const value = decodeURIComponent(match[1] ?? "").trim();
  if (!value) return null;
  return value.startsWith("@") ? value : `@${value}`;
}

function extractYouTubeChannelIdFromUrl(url: string): string | null {
  const raw = (url ?? "").trim();
  if (!raw) return null;
  const match = raw.match(/\/channel\/(UC[\\w-]+)/);
  if (!match) return null;
  return (match[1] ?? "").trim() || null;
}

function parseJsonObject<T>(raw: string): T {
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON root is not an object");
  }
  return parsed as T;
}

type GenreIndexEntry = {
  name: string;
  indexPath: string;
  referencedChannels: string[];
  referencedCount: number | null;
};

type GenreCompetitorEntry = {
  spec: BenchmarkChannelSpec;
  referencedChannels: string[];
  raw: string;
};

type GenreScriptSampleEntry = {
  sample: BenchmarkScriptSampleSpec;
  referencedChannels: string[];
  status: string | null;
  note: string | null;
};

type YtDlpReportsIndexEntry = {
  playlist_channel_id: string;
  playlist_uploader_id?: string | null;
  playlist_channel?: string | null;
  channel_avatar_url?: string | null;
  source_url?: string | null;
  fetched_at?: string | null;
  playlist_end?: number | null;
  video_count?: number | null;
  report_md_path?: string | null;
  report_json_path?: string | null;
  top_video?: YtDlpVideoEntry | null;
  stats?: {
    view_count_median?: number | null;
    view_count_p75?: number | null;
    duration_median_sec?: number | null;
    title_starts_with_bracket_ratio?: number | null;
    top_bracket_prefix?: string | null;
  } | null;
};

type YtDlpReportsIndex = {
  version?: number;
  generated_at?: string;
  entries?: YtDlpReportsIndexEntry[];
};

type YtDlpVideoEntry = {
  id: string;
  title?: string | null;
  url?: string | null;
  duration_sec?: number | null;
  view_count?: number | null;
  thumbnail_url?: string | null;
  playlist_index?: number | null;
};

type YtDlpThumbnailInsight = {
  schema?: string;
  generated_at?: string;
  source?: string | null;
  model?: string | null;
  analysis?: {
    caption_ja?: string | null;
    thumbnail_text?: string | null;
    hook_type?: string | null;
    promise?: string | null;
    target?: string | null;
    emotion?: string | null;
    composition?: string | null;
    colors?: string | null;
    design_elements?: string[] | null;
    tags?: string[] | null;
  };
};

type YtDlpThumbnailSummaryEntry = {
  value: string;
  count: number;
};

type YtDlpThumbnailSummary = {
  schema?: string;
  generated_at?: string;
  insight_count?: number;
  top_tags?: YtDlpThumbnailSummaryEntry[];
  hook_types?: YtDlpThumbnailSummaryEntry[];
};

type YtDlpChannelReport = {
  version?: number;
  fetched_at?: string;
  playlist_end?: number;
  channel?: {
    playlist_channel_id?: string | null;
    playlist_uploader_id?: string | null;
    playlist_channel?: string | null;
    source_url?: string | null;
    avatar_url?: string | null;
  };
  top_by_views?: YtDlpVideoEntry[];
  recent?: YtDlpVideoEntry[];
  videos?: YtDlpVideoEntry[];
  thumbnail_insights?: Record<string, YtDlpThumbnailInsight>;
  thumbnail_summary?: YtDlpThumbnailSummary;
};

function extractMarkdownSectionLines(markdown: string, headingPrefix: string): string[] {
  const lines = (markdown ?? "").split(/\r?\n/);
  const startIndex = lines.findIndex((line) => line.trim().startsWith(`## ${headingPrefix}`));
  if (startIndex < 0) return [];
  const out: string[] = [];
  for (let idx = startIndex + 1; idx < lines.length; idx += 1) {
    const line = lines[idx];
    if (line.trim().startsWith("## ")) break;
    out.push(line);
  }
  return out;
}

function parseChannelCodes(text: string): string[] {
  const matches = text.match(/CH\d+/gi) ?? [];
  const unique = Array.from(new Set(matches.map((m) => m.toUpperCase())));
  unique.sort(compareChannelCode);
  return unique;
}

function formatChannelCodesPreview(codes: string[], limit = 8): string {
  const cleaned = (codes ?? []).map((c) => c.trim()).filter(Boolean);
  if (!cleaned.length) return "";
  if (cleaned.length <= limit) return cleaned.join(", ");
  return `${cleaned.slice(0, limit).join(", ")} …+${cleaned.length - limit}`;
}

function parseResearchRootGenreIndex(markdown: string): GenreIndexEntry[] {
  const lines = extractMarkdownSectionLines(markdown, "ジャンル一覧");
  const entries: GenreIndexEntry[] = [];
  let current: GenreIndexEntry | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const match = line.match(/^-\s+\*\*(.+?)\*\*（参照CH:\s*(\d+)\s*）\s*→\s*`([^`]+)`/);
    if (match) {
      current = {
        name: match[1].trim(),
        indexPath: match[3].trim(),
        referencedChannels: [],
        referencedCount: Number(match[2]),
      };
      entries.push(current);
      continue;
    }
    if (!current) continue;
    const channelMatch = line.match(/^\s*-\s*参照CH:\s*(.+)$/);
    if (channelMatch) {
      current.referencedChannels = parseChannelCodes(channelMatch[1]);
    }
  }

  return entries;
}

function extractManualBlock(markdown: string): string | null {
  const startToken = "<!-- MANUAL START -->";
  const endToken = "<!-- MANUAL END -->";
  const start = markdown.indexOf(startToken);
  const end = markdown.indexOf(endToken);
  if (start === -1 || end === -1 || end <= start) return null;
  const body = markdown.slice(start + startToken.length, end).trim();
  return body || null;
}

function parseCompetitorRaw(raw: string): { handle: string | null; name: string | null; url: string | null } {
  const parts = raw
    .split(" / ")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);

  let handle: string | null = null;
  let name: string | null = null;
  let url: string | null = null;

  if (parts.length >= 3) {
    handle = parts[0] || null;
    name = parts[1] || null;
    url = parts.slice(2).join(" / ") || null;
  } else if (parts.length === 2) {
    if (parts[0].startsWith("@")) {
      handle = parts[0] || null;
      url = parts[1] || null;
    } else {
      name = parts[0] || null;
      url = parts[1] || null;
    }
  } else if (parts.length === 1) {
    const value = parts[0];
    if (value.startsWith("http://") || value.startsWith("https://")) {
      url = value;
    } else if (value.startsWith("@")) {
      handle = value;
    } else {
      name = value;
    }
  }

  return { handle, name, url };
}

function parseGenreCompetitors(markdown: string): GenreCompetitorEntry[] {
  const lines = extractMarkdownSectionLines(markdown, "競合チャンネル");
  const entries: GenreCompetitorEntry[] = [];

  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = lines[idx].trim();
    const match = line.match(/^-\s+`([^`]+)`（参照CH:\s*([^)]*)）/);
    if (!match) continue;

    const raw = match[1].trim();
    const referencedChannels = parseChannelCodes(match[2]);

    const notes: string[] = [];
    while (idx + 1 < lines.length) {
      const next = lines[idx + 1];
      const noteMatch = next.match(/^\s{2,}-\s+(.*)$/);
      if (!noteMatch) break;
      notes.push(noteMatch[1].trim());
      idx += 1;
    }

    const { handle, name, url } = parseCompetitorRaw(raw);
    const note = notes.length ? notes.join("\n") : null;

    entries.push({
      raw,
      referencedChannels,
      spec: { handle, name, url, note },
    });
  }

  return entries;
}

function parseGenreSharedSamples(markdown: string): GenreScriptSampleEntry[] {
  const lines = extractMarkdownSectionLines(markdown, "共有サンプル");
  const entries: GenreScriptSampleEntry[] = [];

  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = lines[idx].trim();
    const match = line.match(/^-\s+`([^`]+)`（([^)]*)）/);
    if (!match) continue;

    const path = match[1].trim();
    const meta = match[2].trim();
    const referencedChannels = parseChannelCodes(meta);

    const status = meta.split("/")[0]?.trim() || null;

    const notes: string[] = [];
    while (idx + 1 < lines.length) {
      const next = lines[idx + 1];
      const noteMatch = next.match(/^\s{2,}-\s+(.*)$/);
      if (!noteMatch) break;
      notes.push(noteMatch[1].trim());
      idx += 1;
    }

    const note = notes.length ? notes.join("\n") : null;
    const label = path.split("/").pop() ?? path;

    entries.push({
      sample: { base: "research", path, label, note },
      referencedChannels,
      status,
      note,
    });
  }

  return entries;
}

function parseGenreUnreferencedFiles(markdown: string): string[] {
  const lines = extractMarkdownSectionLines(markdown, "未参照ファイル");
  const paths: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trim();
    const match = line.match(/^-\s+`([^`]+)`/);
    if (match) {
      paths.push(match[1].trim());
    }
  }
  return paths;
}

type ScriptMetrics = {
  nonWhitespaceChars: number;
  rawChars: number;
  lines: number;
  nonEmptyLines: number;
  headings: number;
  dividers: number;
  estimatedMinutes: number;
  firstNonEmptyLine: string;
  topKanjiPhrases: Array<{ phrase: string; count: number }>;
};

function analyzeScriptContent(content: string): ScriptMetrics {
  const raw = content ?? "";
  const lines = raw.split(/\r?\n/);
  const nonEmptyLines = lines.filter((line) => line.trim().length > 0);
  const headings = lines.filter((line) => /^#{1,6}\s+/.test(line.trim())).length;
  const dividers = lines.filter((line) => /^(-{3,}|={3,}|_{3,})\s*$/.test(line.trim())).length;
  const nonWhitespaceChars = raw.replace(/\s/g, "").length;
  const estimatedMinutes = nonWhitespaceChars / CHARS_PER_SECOND / 60;
  const firstNonEmptyLine = (nonEmptyLines[0] ?? "").trim();

  const phrases = raw.match(/[一-龯]{2,}/g) ?? [];
  const freq = new Map<string, number>();
  for (const phrase of phrases) {
    freq.set(phrase, (freq.get(phrase) ?? 0) + 1);
  }
  const topKanjiPhrases = Array.from(freq.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([phrase, count]) => ({ phrase, count }));

  return {
    nonWhitespaceChars,
    rawChars: raw.length,
    lines: lines.length,
    nonEmptyLines: nonEmptyLines.length,
    headings,
    dividers,
    estimatedMinutes,
    firstNonEmptyLine,
    topKanjiPhrases,
  };
}

type SampleCacheEntry = {
  base: "research" | "scripts";
  path: string;
  label: string;
  note: string | null;
  loading: boolean;
  error: string | null;
  content: string | null;
  size?: number;
  modified?: string;
  metrics?: ScriptMetrics;
};

type YtDlpReportCacheEntry = {
  path: string;
  loading: boolean;
  error: string | null;
  report: YtDlpChannelReport | null;
  size?: number;
  modified?: string;
};

function sampleKey(sample: BenchmarkScriptSampleSpec): string {
  return `${sample.base}:${sample.path}`;
}

function buildSampleLabel(sample: BenchmarkScriptSampleSpec): string {
  const label = (sample.label ?? "").trim();
  return label || sample.path;
}

export function BenchmarksPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { channels } = useOutletContext<ShellOutletContext>();

  const queryParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const queryTab = (queryParams.get("tab") ?? "").trim();
  const queryGenre = (queryParams.get("genre") ?? "").trim();
  const queryYtId = (queryParams.get("yt") ?? "").trim();
  const queryYtKeyword = (queryParams.get("q") ?? "").trim();

  const activeTab = useMemo(() => {
    if (queryTab === "yt") return "yt";
    if (queryTab === "genre") return "genre";
    return queryGenre ? "genre" : "yt";
  }, [queryGenre, queryTab]);

  const [genreIndex, setGenreIndex] = useState<GenreIndexEntry[]>([]);
  const [genreIndexLoading, setGenreIndexLoading] = useState(false);
  const [genreIndexError, setGenreIndexError] = useState<string | null>(null);
  const [genreKeyword, setGenreKeyword] = useState("");
  const [channelFilter, setChannelFilter] = useState<string | null>(null);

  const [genreManual, setGenreManual] = useState<string | null>(null);
  const [genreCompetitors, setGenreCompetitors] = useState<GenreCompetitorEntry[]>([]);
  const [genreSamples, setGenreSamples] = useState<GenreScriptSampleEntry[]>([]);
  const [genreUnreferenced, setGenreUnreferenced] = useState<string[]>([]);
  const [genreDetailsLoading, setGenreDetailsLoading] = useState(false);
  const [genreDetailsError, setGenreDetailsError] = useState<string | null>(null);
  const genreDetailsLoadSeq = useRef(0);

  const [activeSampleKeyState, setActiveSampleKeyState] = useState<string | null>(null);
  const [sampleCache, setSampleCache] = useState<Record<string, SampleCacheEntry>>({});
  const [bulkLoading, setBulkLoading] = useState(false);
  const [copyBanner, setCopyBanner] = useState<string | null>(null);

  const [ytDlpIndex, setYtDlpIndex] = useState<YtDlpReportsIndex | null>(null);
  const [ytDlpIndexLoading, setYtDlpIndexLoading] = useState(false);
  const [ytDlpIndexError, setYtDlpIndexError] = useState<string | null>(null);
  const [activeYtDlpEntry, setActiveYtDlpEntry] = useState<YtDlpReportsIndexEntry | null>(null);
  const [activeYtDlpTab, setActiveYtDlpTab] = useState<"top" | "recent">("top");
  const [activeYtDlpTagFilter, setActiveYtDlpTagFilter] = useState<string | null>(null);
  const [activeYtDlpHookFilter, setActiveYtDlpHookFilter] = useState<string | null>(null);
  const [ytDlpShowAnalyzedOnly, setYtDlpShowAnalyzedOnly] = useState<boolean>(false);
  const [ytDlpExpandedCards, setYtDlpExpandedCards] = useState<Record<string, boolean>>({});
  const [ytDlpReportCache, setYtDlpReportCache] = useState<Record<string, YtDlpReportCacheEntry>>({});
  const [ytDlpPickerKeyword, setYtDlpPickerKeyword] = useState("");
  const [ytSortKey, setYtSortKey] = useState<"views_median" | "views_p75" | "fetched_at" | "name">("views_median");

  useEffect(() => {
    setYtDlpExpandedCards({});
  }, [activeYtDlpEntry?.playlist_channel_id, activeYtDlpTab]);

  const channelLabelByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const channel of channels ?? []) {
      map.set(channel.code.toUpperCase(), resolveChannelDisplayName(channel));
    }
    return map;
  }, [channels]);

  const ytDlpEntries = useMemo(() => {
    const entries = ytDlpIndex?.entries;
    if (!Array.isArray(entries)) return [];
    return entries.filter(
      (entry): entry is YtDlpReportsIndexEntry =>
        Boolean(entry && typeof entry === "object" && (entry as YtDlpReportsIndexEntry).playlist_channel_id)
    );
  }, [ytDlpIndex?.entries]);

  const ytDlpEntryByHandle = useMemo(() => {
    const map = new Map<string, YtDlpReportsIndexEntry>();
    for (const entry of ytDlpEntries) {
      const handle = normalizeHandle(entry.playlist_uploader_id);
      if (!handle) continue;
      map.set(handle.toLowerCase(), entry);
    }
    return map;
  }, [ytDlpEntries]);

  const ytDlpEntryByChannelId = useMemo(() => {
    const map = new Map<string, YtDlpReportsIndexEntry>();
    for (const entry of ytDlpEntries) {
      const cid = (entry.playlist_channel_id ?? "").trim();
      if (!cid) continue;
      map.set(cid, entry);
    }
    return map;
  }, [ytDlpEntries]);

  const ytDlpPickerEntries = useMemo(() => {
    const keyword = ytDlpPickerKeyword.trim().toLowerCase();
    const entries = [...ytDlpEntries].sort((a, b) => {
      const aName = (a.playlist_channel ?? "").trim();
      const bName = (b.playlist_channel ?? "").trim();
      if (aName && bName) return aName.localeCompare(bName, "ja-JP");
      if (aName) return -1;
      if (bName) return 1;
      const aHandle = (a.playlist_uploader_id ?? "").trim();
      const bHandle = (b.playlist_uploader_id ?? "").trim();
      return aHandle.localeCompare(bHandle, "ja-JP");
    });
    if (!keyword) return entries;
    return entries.filter((entry) => {
      const haystack = [
        entry.playlist_channel_id,
        entry.playlist_channel ?? "",
        entry.playlist_uploader_id ?? "",
        entry.source_url ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [ytDlpEntries, ytDlpPickerKeyword]);

  const ytDlpSidebarEntries = useMemo(() => {
    const keyword = ytDlpPickerKeyword.trim().toLowerCase();
    const filtered = keyword
      ? ytDlpEntries.filter((entry) => {
          const haystack = [
            entry.playlist_channel_id,
            entry.playlist_channel ?? "",
            entry.playlist_uploader_id ?? "",
            entry.source_url ?? "",
            entry.top_video?.title ?? "",
          ]
            .join(" ")
            .toLowerCase();
          return haystack.includes(keyword);
        })
      : ytDlpEntries;

    const sorted = [...filtered];
    sorted.sort((a, b) => {
      if (ytSortKey === "fetched_at") {
        const at = a.fetched_at ?? "";
        const bt = b.fetched_at ?? "";
        return bt.localeCompare(at);
      }
      if (ytSortKey === "name") {
        const an = (a.playlist_channel ?? a.playlist_uploader_id ?? a.playlist_channel_id).trim();
        const bn = (b.playlist_channel ?? b.playlist_uploader_id ?? b.playlist_channel_id).trim();
        return an.localeCompare(bn, "ja-JP");
      }
      if (ytSortKey === "views_p75") {
        const av = typeof a.stats?.view_count_p75 === "number" ? a.stats.view_count_p75 : -1;
        const bv = typeof b.stats?.view_count_p75 === "number" ? b.stats.view_count_p75 : -1;
        return bv - av;
      }
      const av = typeof a.stats?.view_count_median === "number" ? a.stats.view_count_median : -1;
      const bv = typeof b.stats?.view_count_median === "number" ? b.stats.view_count_median : -1;
      return bv - av;
    });

    return sorted;
  }, [ytDlpEntries, ytDlpPickerKeyword, ytSortKey]);

  const resolveYtDlpEntry = useCallback(
    (spec: BenchmarkChannelSpec): YtDlpReportsIndexEntry | null => {
      const url = (spec.url ?? "").trim();
      const handle = normalizeHandle(spec.handle) ?? (url ? extractYouTubeHandleFromUrl(url) : null);
      if (handle) {
        const found = ytDlpEntryByHandle.get(handle.toLowerCase());
        if (found) return found;
      }
      const channelId = url ? extractYouTubeChannelIdFromUrl(url) : null;
      if (channelId) {
        const found = ytDlpEntryByChannelId.get(channelId);
        if (found) return found;
      }
      return null;
    },
    [ytDlpEntryByChannelId, ytDlpEntryByHandle]
  );

  const loadGenreRoot = useCallback(async () => {
    setGenreIndexLoading(true);
    setGenreIndexError(null);
    try {
      const response = await fetchResearchFile("research", "INDEX.md");
      setGenreIndex(parseResearchRootGenreIndex(response.content));
    } catch (err) {
      setGenreIndexError(err instanceof Error ? err.message : String(err));
      setGenreIndex([]);
    } finally {
      setGenreIndexLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== "genre") return;
    void loadGenreRoot();
  }, [activeTab, loadGenreRoot]);

  const loadYtDlpIndex = useCallback(async () => {
    setYtDlpIndexLoading(true);
    setYtDlpIndexError(null);
    try {
      const response = await fetchResearchFile("research", "YouTubeベンチマーク（yt-dlp）/REPORTS.json");
      setYtDlpIndex(parseJsonObject<YtDlpReportsIndex>(response.content));
    } catch (err) {
      setYtDlpIndexError(err instanceof Error ? err.message : String(err));
      setYtDlpIndex(null);
    } finally {
      setYtDlpIndexLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadYtDlpIndex();
  }, [loadYtDlpIndex]);

  useEffect(() => {
    if (activeTab !== "yt") return;
    if (!queryYtKeyword) return;
    setYtDlpPickerKeyword((current) => (current ? current : queryYtKeyword));
  }, [activeTab, queryYtKeyword]);

  useEffect(() => {
    if (activeTab !== "genre") return;
    if (queryGenre) return;
    if (!genreIndex.length) return;
    const params = new URLSearchParams(location.search);
    params.set("tab", "genre");
    params.set("genre", genreIndex[0].name);
    params.delete("view");
    params.delete("channel");
    const search = params.toString();
    navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
  }, [activeTab, genreIndex, location.search, navigate, queryGenre]);

  const handleSelectTab = useCallback(
    (nextTab: "yt" | "genre") => {
      const params = new URLSearchParams(location.search);
      params.set("tab", nextTab);
      const search = params.toString();
      navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
    },
    [location.search, navigate]
  );

  const handleSelectGenre = useCallback(
    (genreName: string) => {
      const value = genreName.trim();
      if (!value) return;
      const params = new URLSearchParams(location.search);
      params.set("tab", "genre");
      params.set("genre", value);
      params.delete("view");
      params.delete("channel");
      const search = params.toString();
      navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
    },
    [location.search, navigate]
  );

  const toggleChannelFilter = useCallback((channelCode: string) => {
    const normalized = channelCode.trim().toUpperCase();
    if (!normalized) return;
    setChannelFilter((current) => (current?.toUpperCase() === normalized ? null : normalized));
  }, []);

  const normalizedChannelFilter = useMemo(() => {
    const normalized = (channelFilter ?? "").trim().toUpperCase();
    return normalized || null;
  }, [channelFilter]);

  const handleOpenChannelSettings = useCallback(() => {
    if (!normalizedChannelFilter) return;
    navigate(`/channel-settings?channel=${encodeURIComponent(normalizedChannelFilter)}`);
  }, [navigate, normalizedChannelFilter]);

  const filteredGenreIndex = useMemo(() => {
    const keyword = genreKeyword.trim().toLowerCase();
    if (!keyword) return genreIndex;
    return genreIndex.filter((entry) => {
      const haystack = [entry.name, entry.indexPath, entry.referencedChannels.join(" ")].join(" ").toLowerCase();
      return haystack.includes(keyword);
    });
  }, [genreIndex, genreKeyword]);

  const effectiveGenre = useMemo(() => queryGenre || genreIndex[0]?.name || null, [genreIndex, queryGenre]);

  useEffect(() => {
    if (activeTab !== "genre") return;
    setChannelFilter(null);
  }, [activeTab, effectiveGenre]);

  useEffect(() => {
    if (activeTab !== "genre") return;
    setActiveYtDlpEntry(null);
    setActiveYtDlpTab("top");
    setActiveYtDlpTagFilter(null);
    setActiveYtDlpHookFilter(null);
    setYtDlpShowAnalyzedOnly(false);
    setYtDlpPickerKeyword("");
  }, [activeTab, effectiveGenre]);

  const selectedGenreEntry = useMemo(() => {
    if (!effectiveGenre) return null;
    return genreIndex.find((entry) => entry.name === effectiveGenre) ?? null;
  }, [effectiveGenre, genreIndex]);

  const selectedGenreIndexPath =
    selectedGenreEntry?.indexPath ?? (effectiveGenre ? `${effectiveGenre}/INDEX.md` : null);

  const loadGenreDetails = useCallback(async () => {
    if (!selectedGenreIndexPath) {
      setGenreManual(null);
      setGenreCompetitors([]);
      setGenreSamples([]);
      setGenreUnreferenced([]);
      setGenreDetailsError(null);
      setGenreDetailsLoading(false);
      return;
    }

    const seq = ++genreDetailsLoadSeq.current;
    setGenreDetailsLoading(true);
    setGenreDetailsError(null);
    try {
      const response = await fetchResearchFile("research", selectedGenreIndexPath);
      if (genreDetailsLoadSeq.current !== seq) return;
      setGenreManual(extractManualBlock(response.content));
      setGenreCompetitors(parseGenreCompetitors(response.content));
      setGenreSamples(parseGenreSharedSamples(response.content));
      setGenreUnreferenced(parseGenreUnreferencedFiles(response.content));
    } catch (err) {
      if (genreDetailsLoadSeq.current !== seq) return;
      setGenreDetailsError(err instanceof Error ? err.message : String(err));
      setGenreManual(null);
      setGenreCompetitors([]);
      setGenreSamples([]);
      setGenreUnreferenced([]);
    } finally {
      if (genreDetailsLoadSeq.current !== seq) return;
      setGenreDetailsLoading(false);
    }
  }, [selectedGenreIndexPath]);

  useEffect(() => {
    if (activeTab !== "genre") return;
    void loadGenreDetails();
  }, [activeTab, loadGenreDetails]);

  const visibleCompetitors = useMemo(() => {
    if (!normalizedChannelFilter) return genreCompetitors;
    return genreCompetitors.filter((entry) => entry.referencedChannels.includes(normalizedChannelFilter));
  }, [genreCompetitors, normalizedChannelFilter]);

  const visibleSamples = useMemo(() => {
    if (!normalizedChannelFilter) return genreSamples;
    return genreSamples.filter((entry) => entry.referencedChannels.includes(normalizedChannelFilter));
  }, [genreSamples, normalizedChannelFilter]);

  useEffect(() => {
    if (!visibleSamples.length) {
      setActiveSampleKeyState(null);
      return;
    }
    setActiveSampleKeyState((current) => {
      const first = sampleKey(visibleSamples[0].sample);
      if (!current) return first;
      const stillExists = visibleSamples.some((entry) => sampleKey(entry.sample) === current);
      return stillExists ? current : first;
    });
  }, [visibleSamples]);

  const activeSampleEntry = useMemo(() => {
    if (!activeSampleKeyState) return null;
    return visibleSamples.find((entry) => sampleKey(entry.sample) === activeSampleKeyState) ?? null;
  }, [activeSampleKeyState, visibleSamples]);

  const activeSample = useMemo(() => activeSampleEntry?.sample ?? null, [activeSampleEntry]);

  const activeSampleState = useMemo(() => {
    if (!activeSample) return null;
    return sampleCache[sampleKey(activeSample)] ?? null;
  }, [activeSample, sampleCache]);

  const activeYtDlpReportPath = useMemo(() => {
    const path = (activeYtDlpEntry?.report_json_path ?? "").trim();
    return path || null;
  }, [activeYtDlpEntry?.report_json_path]);

  const activeYtDlpReportState = useMemo(() => {
    if (!activeYtDlpReportPath) return null;
    return ytDlpReportCache[activeYtDlpReportPath] ?? null;
  }, [activeYtDlpReportPath, ytDlpReportCache]);

  const activeYtDlpReport = useMemo(() => activeYtDlpReportState?.report ?? null, [activeYtDlpReportState?.report]);
  const activeYtDlpAvatarUrl = useMemo(() => {
    const fromIndex = (activeYtDlpEntry?.channel_avatar_url ?? "").trim();
    if (fromIndex) return fromIndex;
    const fromReport = (activeYtDlpReport?.channel?.avatar_url ?? "").trim();
    return fromReport || null;
  }, [activeYtDlpEntry?.channel_avatar_url, activeYtDlpReport?.channel?.avatar_url]);

  const loadSample = useCallback(async (sample: BenchmarkScriptSampleSpec) => {
    const key = sampleKey(sample);
    const base = sample.base;
    const path = sample.path.trim();
    if (!path) return;

    setSampleCache((prev) => {
      const current = prev[key];
      if (current?.loading) return prev;
      return {
        ...prev,
        [key]: {
          base,
          path,
          label: buildSampleLabel(sample),
          note: sample.note ?? null,
          loading: true,
          error: null,
          content: current?.content ?? null,
          size: current?.size,
          modified: current?.modified,
          metrics: current?.metrics,
        },
      };
    });

    try {
      const response = await fetchResearchFile(base, path);
      const metrics = analyzeScriptContent(response.content);
      setSampleCache((prev) => ({
        ...prev,
        [key]: {
          base,
          path,
          label: buildSampleLabel(sample),
          note: sample.note ?? null,
          loading: false,
          error: null,
          content: response.content,
          size: response.size,
          modified: response.modified,
          metrics,
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSampleCache((prev) => ({
        ...prev,
        [key]: {
          base,
          path,
          label: buildSampleLabel(sample),
          note: sample.note ?? null,
          loading: false,
          error: message,
          content: prev[key]?.content ?? null,
          size: prev[key]?.size,
          modified: prev[key]?.modified,
          metrics: prev[key]?.metrics,
        },
      }));
    }
  }, []);

  const loadYtDlpReport = useCallback(async (path: string) => {
    const normalized = path.trim();
    if (!normalized) return;

    setYtDlpReportCache((prev) => {
      const current = prev[normalized];
      if (current?.loading) return prev;
      return {
        ...prev,
        [normalized]: {
          path: normalized,
          loading: true,
          error: null,
          report: current?.report ?? null,
          size: current?.size,
          modified: current?.modified,
        },
      };
    });

    try {
      const response = await fetchResearchFile("research", normalized);
      const report = parseJsonObject<YtDlpChannelReport>(response.content);
      setYtDlpReportCache((prev) => ({
        ...prev,
        [normalized]: {
          path: normalized,
          loading: false,
          error: null,
          report,
          size: response.size,
          modified: response.modified,
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setYtDlpReportCache((prev) => ({
        ...prev,
        [normalized]: {
          path: normalized,
          loading: false,
          error: message,
          report: prev[normalized]?.report ?? null,
          size: prev[normalized]?.size,
          modified: prev[normalized]?.modified,
        },
      }));
    }
  }, []);

  const handleOpenYtDlpAnalysis = useCallback(
    (entry: YtDlpReportsIndexEntry) => {
      setActiveYtDlpEntry(entry);
      setActiveYtDlpTab("top");
      setActiveYtDlpTagFilter(null);
      setActiveYtDlpHookFilter(null);
      setYtDlpShowAnalyzedOnly(false);
      const reportPath = (entry.report_json_path ?? "").trim();
      if (!reportPath) return;
      const cached = ytDlpReportCache[reportPath];
      if (!cached?.report && !cached?.loading) {
        void loadYtDlpReport(reportPath);
      }
    },
    [loadYtDlpReport, ytDlpReportCache]
  );

  const handleCloseYtDlpAnalysis = useCallback(() => {
    setActiveYtDlpEntry(null);
    setActiveYtDlpTab("top");
    setActiveYtDlpTagFilter(null);
    setActiveYtDlpHookFilter(null);
    setYtDlpShowAnalyzedOnly(false);
  }, []);

  const handleSelectYtDlpEntry = useCallback(
    (entry: YtDlpReportsIndexEntry) => {
      const id = (entry.playlist_channel_id ?? "").trim();
      if (!id) return;
      handleOpenYtDlpAnalysis(entry);
      const params = new URLSearchParams(location.search);
      params.set("tab", "yt");
      params.set("yt", id);
      const keyword = ytDlpPickerKeyword.trim();
      if (keyword) {
        params.set("q", keyword);
      } else {
        params.delete("q");
      }
      const search = params.toString();
      navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
    },
    [handleOpenYtDlpAnalysis, location.search, navigate, ytDlpPickerKeyword]
  );

  const handleCloseYtDlpAnalysisWithUrl = useCallback(() => {
    handleCloseYtDlpAnalysis();
    const params = new URLSearchParams(location.search);
    params.set("tab", "yt");
    params.delete("yt");
    const search = params.toString();
    navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
  }, [handleCloseYtDlpAnalysis, location.search, navigate]);

  useEffect(() => {
    if (activeTab !== "yt") return;
    if (!queryYtId) return;
    if (activeYtDlpEntry?.playlist_channel_id === queryYtId) return;
    const entry = ytDlpEntries.find((it) => it.playlist_channel_id === queryYtId) ?? null;
    if (entry) {
      handleOpenYtDlpAnalysis(entry);
    }
  }, [activeTab, activeYtDlpEntry?.playlist_channel_id, handleOpenYtDlpAnalysis, queryYtId, ytDlpEntries]);

  useEffect(() => {
    if (activeTab !== "yt") return;
    if (queryYtId) return;
    if (activeYtDlpEntry) return;
    if (!ytDlpEntries.length) return;
    handleSelectYtDlpEntry(ytDlpEntries[0]);
  }, [activeTab, activeYtDlpEntry, handleSelectYtDlpEntry, queryYtId, ytDlpEntries]);

  useEffect(() => {
    if (!activeSample) return;
    if (activeSampleState?.content || activeSampleState?.loading) return;
    void loadSample(activeSample);
  }, [activeSample, activeSampleState?.content, activeSampleState?.loading, loadSample]);

  const handleSelectSample = useCallback(
    (sample: BenchmarkScriptSampleSpec) => {
      const key = sampleKey(sample);
      setActiveSampleKeyState(key);
      const cached = sampleCache[key];
      if (!cached?.content && !cached?.loading) {
        void loadSample(sample);
      }
    },
    [loadSample, sampleCache]
  );

  const handleAnalyzeAllSamples = useCallback(
    async (samples: BenchmarkScriptSampleSpec[]) => {
      if (!samples.length) return;
      setBulkLoading(true);
      try {
        await Promise.allSettled(samples.map((sample) => loadSample(sample)));
      } finally {
        setBulkLoading(false);
      }
    },
    [loadSample]
  );

  const clearSampleCache = useCallback(() => {
    setSampleCache({});
    setCopyBanner(null);
  }, []);

  const handleCopy = useCallback(async (value: string, label: string) => {
    setCopyBanner(null);
    try {
      await navigator.clipboard.writeText(value);
      setCopyBanner(`${label} をコピーしました。`);
      window.setTimeout(() => setCopyBanner(null), 1800);
    } catch (err) {
      try {
        const textarea = document.createElement("textarea");
        textarea.value = value;
        textarea.setAttribute("readonly", "true");
        textarea.style.position = "fixed";
        textarea.style.top = "0";
        textarea.style.left = "0";
        textarea.style.width = "1px";
        textarea.style.height = "1px";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (ok) {
          setCopyBanner(`${label} をコピーしました。`);
          window.setTimeout(() => setCopyBanner(null), 1800);
          return;
        }
      } catch (_fallbackErr) {
        // ignore
      }
      setCopyBanner(err instanceof Error ? err.message : String(err));
      window.setTimeout(() => setCopyBanner(null), 2500);
    }
  }, []);

  const activeYtDlpVideos = useMemo(() => {
    if (!activeYtDlpReport) return [];
    const list = activeYtDlpTab === "recent" ? activeYtDlpReport.recent : activeYtDlpReport.top_by_views;
    if (!Array.isArray(list)) return [];
    return list.filter((item): item is YtDlpVideoEntry => Boolean(item && typeof item === "object" && (item as YtDlpVideoEntry).id));
  }, [activeYtDlpReport, activeYtDlpTab]);

  const activeYtDlpInsights = useMemo(() => {
    const map = activeYtDlpReport?.thumbnail_insights;
    return map && typeof map === "object" ? map : null;
  }, [activeYtDlpReport?.thumbnail_insights]);

  const activeYtDlpTagOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const video of activeYtDlpVideos) {
      const insight = activeYtDlpInsights?.[video.id];
      const tags = insight?.analysis?.tags;
      if (!Array.isArray(tags)) continue;
      for (const raw of tags) {
        const tag = (raw ?? "").trim();
        if (!tag) continue;
        counts.set(tag, (counts.get(tag) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 24)
      .map(([tag, count]) => ({ tag, count }));
  }, [activeYtDlpInsights, activeYtDlpVideos]);

  const activeYtDlpHookOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const video of activeYtDlpVideos) {
      const insight = activeYtDlpInsights?.[video.id];
      const hook = (insight?.analysis?.hook_type ?? "").trim();
      if (!hook) continue;
      counts.set(hook, (counts.get(hook) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 16)
      .map(([hook, count]) => ({ hook, count }));
  }, [activeYtDlpInsights, activeYtDlpVideos]);

  const activeYtDlpDesignOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const video of activeYtDlpVideos) {
      const insight = activeYtDlpInsights?.[video.id];
      const elements = insight?.analysis?.design_elements;
      if (!Array.isArray(elements)) continue;
      for (const raw of elements) {
        const value = (raw ?? "").trim();
        if (!value) continue;
        counts.set(value, (counts.get(value) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 18)
      .map(([value, count]) => ({ value, count }));
  }, [activeYtDlpInsights, activeYtDlpVideos]);

  const ytDlpThumbnailSummary = useMemo(() => {
    const summary = activeYtDlpReport?.thumbnail_summary;
    const normalize = (items: unknown, fallback: Array<{ value: string; count: number }>) => {
      if (!Array.isArray(items)) return fallback;
      const out: Array<{ value: string; count: number }> = [];
      for (const raw of items) {
        if (!raw || typeof raw !== "object") continue;
        const obj = raw as Partial<YtDlpThumbnailSummaryEntry>;
        const value = typeof obj.value === "string" ? obj.value.trim() : "";
        const count = typeof obj.count === "number" && Number.isFinite(obj.count) ? obj.count : 0;
        if (!value || count <= 0) continue;
        out.push({ value, count });
      }
      return out.length ? out : fallback;
    };

    const fallbackHooks = activeYtDlpHookOptions.map((it) => ({ value: it.hook, count: it.count }));
    const fallbackTags = activeYtDlpTagOptions.map((it) => ({ value: it.tag, count: it.count }));

    const hookTypes = normalize(summary?.hook_types, fallbackHooks).slice(0, 10);
    const topTags = normalize(summary?.top_tags, fallbackTags).slice(0, 14);
    const insightCount =
      typeof summary?.insight_count === "number" && Number.isFinite(summary.insight_count)
        ? summary.insight_count
        : activeYtDlpVideos.length;
    const generatedAt = typeof summary?.generated_at === "string" ? summary.generated_at : null;
    const hasAny = hookTypes.length > 0 || topTags.length > 0 || activeYtDlpDesignOptions.length > 0;
    if (!hasAny) return null;
    return { hookTypes, topTags, generatedAt, insightCount };
  }, [
    activeYtDlpDesignOptions.length,
    activeYtDlpHookOptions,
    activeYtDlpReport?.thumbnail_summary,
    activeYtDlpTagOptions,
    activeYtDlpVideos.length,
  ]);

  const ytDlpThumbStats = useMemo(() => {
    const total = activeYtDlpVideos.length;
    if (!total) {
      return { total: 0, analyzed: 0, withCaption: 0, withText: 0 };
    }
    let analyzed = 0;
    let withCaption = 0;
    let withText = 0;
    for (const video of activeYtDlpVideos) {
      const analysis = activeYtDlpInsights?.[video.id]?.analysis;
      if (!analysis) continue;
      analyzed += 1;
      if (analysis.caption_ja?.trim()) withCaption += 1;
      if (analysis.thumbnail_text?.trim()) withText += 1;
    }
    return { total, analyzed, withCaption, withText };
  }, [activeYtDlpInsights, activeYtDlpVideos]);

  const filteredYtDlpVideos = useMemo(() => {
    const tag = (activeYtDlpTagFilter ?? "").trim();
    return activeYtDlpVideos.filter((video) => {
      const insight = activeYtDlpInsights?.[video.id];
      const analysis = insight?.analysis;
      if (ytDlpShowAnalyzedOnly && !analysis) return false;
      const hook = (activeYtDlpHookFilter ?? "").trim();
      if (hook && (analysis?.hook_type ?? "").trim() !== hook) return false;
      if (!tag) return true;
      const tags = insight?.analysis?.tags;
      if (!Array.isArray(tags)) return false;
      return tags.some((t) => (t ?? "").trim() === tag);
    });
  }, [activeYtDlpHookFilter, activeYtDlpInsights, ytDlpShowAnalyzedOnly, activeYtDlpTagFilter, activeYtDlpVideos]);

  const buildYtDlpThumbExportTsv = useCallback(
    (videos: YtDlpVideoEntry[]) => {
      const norm = (value: unknown) =>
        String(value ?? "")
          .replace(/\t/g, " ")
          .replace(/\r?\n/g, " ")
          .trim();

      const header = [
        "video_id",
        "views",
        "duration_sec",
        "duration_mmss",
        "title",
        "hook",
        "promise",
        "target",
        "emotion",
        "composition",
        "colors",
        "caption_ja",
        "thumbnail_text",
        "design_elements",
        "tags",
        "thumbnail_url",
        "video_url",
      ].join("\t");

      const lines = videos.map((video) => {
        const insight = activeYtDlpInsights?.[video.id];
        const analysis = insight?.analysis;
        const designElements = Array.isArray(analysis?.design_elements) ? (analysis?.design_elements ?? []) : [];
        const tagsList = Array.isArray(analysis?.tags) ? (analysis?.tags ?? []) : [];
        const design = designElements.filter(Boolean).join(", ");
        const tags = tagsList.filter(Boolean).join(", ");
        const durationSec = typeof video.duration_sec === "number" ? video.duration_sec : null;
        return [
          norm(video.id),
          typeof video.view_count === "number" ? String(video.view_count) : "",
          durationSec == null ? "" : String(durationSec),
          durationSec == null ? "" : formatDurationSeconds(durationSec),
          norm(video.title ?? ""),
          norm(analysis?.hook_type ?? ""),
          norm(analysis?.promise ?? ""),
          norm(analysis?.target ?? ""),
          norm(analysis?.emotion ?? ""),
          norm(analysis?.composition ?? ""),
          norm(analysis?.colors ?? ""),
          norm(analysis?.caption_ja ?? ""),
          norm(analysis?.thumbnail_text ?? ""),
          norm(design),
          norm(tags),
          norm(video.thumbnail_url ?? ""),
          norm(video.url ?? ""),
        ].join("\t");
      });

      return [header, ...lines].join("\n");
    },
    [activeYtDlpInsights]
  );

  const handleCopyYtDlpThumbTsv = useCallback(async () => {
    if (!activeYtDlpEntry) return;
    if (!filteredYtDlpVideos.length) return;
    await handleCopy(buildYtDlpThumbExportTsv(filteredYtDlpVideos), "サムネ分析TSV");
  }, [activeYtDlpEntry, buildYtDlpThumbExportTsv, filteredYtDlpVideos, handleCopy]);

  const ytDlpAnalyzeCommand = useMemo(() => {
    const channelId = (activeYtDlpEntry?.playlist_channel_id ?? "").trim();
    if (!channelId) return "";
    return [
      "python3 scripts/ops/yt_dlp_thumbnail_analyze.py",
      `--channel-id ${channelId}`,
      "--target both",
      "--limit 20",
      "--continue-on-failover",
      "--apply",
    ].join(" ");
  }, [activeYtDlpEntry?.playlist_channel_id]);

  return (
    <section className="benchmarks-page workspace--channel-clean">
      <header className="benchmarks-header channel-card">
        <div className="benchmarks-header__title">
          <p className="eyebrow">/benchmarks</p>
          <h1>ベンチマーク</h1>
          <div className="benchmarks-tabs" role="tablist" aria-label="ベンチマーク表示切替">
            <button
              type="button"
              className={activeTab === "yt" ? "benchmarks-tab is-active" : "benchmarks-tab"}
              onClick={() => handleSelectTab("yt")}
              role="tab"
              aria-selected={activeTab === "yt"}
            >
              YouTube（yt-dlp）
            </button>
            <button
              type="button"
              className={activeTab === "genre" ? "benchmarks-tab is-active" : "benchmarks-tab"}
              onClick={() => handleSelectTab("genre")}
              role="tab"
              aria-selected={activeTab === "genre"}
            >
              ジャンル別（台本）
            </button>
          </div>
          <p className="benchmarks-header__subtitle">
            {activeTab === "yt"
              ? "バズっている競合チャンネルの公開メタ（再生数/尺/サムネ/タイトル型）をまとめて確認します。"
              : "ジャンル → 競合 → 台本サンプル → 分析 を1ページで確認します。"}
          </p>
        </div>
        <div className="benchmarks-header__controls">
          {activeTab === "yt" ? (
            <button
              type="button"
              className="channel-profile-button channel-profile-button--ghost"
              onClick={() => void loadYtDlpIndex()}
              disabled={ytDlpIndexLoading}
            >
              {ytDlpIndexLoading ? "yt-dlp読込中…" : "yt-dlp再読み込み"}
            </button>
          ) : (
            <button
              type="button"
              className="channel-profile-button channel-profile-button--ghost"
              onClick={() => void loadGenreRoot()}
              disabled={genreIndexLoading}
            >
              {genreIndexLoading ? "ジャンル更新中…" : "ジャンル再読み込み"}
            </button>
          )}
        </div>
      </header>

      {activeTab === "yt" ? (
        <div className="benchmarks-layout">
          <aside className="benchmarks-sidebar channel-card">
            <div className="benchmarks-sidebar__header">
              <h4>YouTube競合（yt-dlp）</h4>
              <span className="benchmarks-sidebar__hint">選択すると右側に表示</span>
            </div>

            <input
              className="benchmarks-sidebar__search"
              type="search"
              value={ytDlpPickerKeyword}
              onChange={(event) => setYtDlpPickerKeyword(event.target.value)}
              placeholder="チャンネル/タイトル/ID で検索"
            />

            <div className="benchmarks-sidebar__filters">
              <span className="badge">{ytDlpSidebarEntries.length} 件</span>
              <select
                className="benchmarks-sidebar__select"
                value={ytSortKey}
                onChange={(event) => setYtSortKey(event.target.value as typeof ytSortKey)}
                title="並び替え"
              >
                <option value="views_median">再生数（中央値）</option>
                <option value="views_p75">再生数（p75）</option>
                <option value="fetched_at">取得日</option>
                <option value="name">チャンネル名</option>
              </select>
            </div>

            {ytDlpIndexError ? (
              <div className="channel-profile-banner channel-profile-banner--error">
                <div>{ytDlpIndexError}</div>
                <div className="muted" style={{ marginTop: 6 }}>
                  生成コマンド: <code>python3 scripts/ops/yt_dlp_benchmark_analyze.py --all --apply</code>
                </div>
              </div>
            ) : null}

            <div className="benchmarks-sidebar__list" role="list">
              {ytDlpSidebarEntries.length === 0 ? (
                <div className="benchmarks-sidebar__empty">{ytDlpIndexLoading ? "読み込み中…" : "該当データがありません。"}</div>
              ) : (
                ytDlpSidebarEntries.map((entry) => {
                  const active = activeYtDlpEntry?.playlist_channel_id === entry.playlist_channel_id;
                  const label = (entry.playlist_channel ?? entry.playlist_uploader_id ?? entry.playlist_channel_id).trim();
                  const subLabel = entry.playlist_uploader_id ? entry.playlist_uploader_id : entry.playlist_channel_id;
                  const avatarUrl = (entry.channel_avatar_url ?? "").trim() || null;
                  const thumbUrl = avatarUrl ?? entry.top_video?.thumbnail_url ?? null;
                  const medianViews =
                    typeof entry.stats?.view_count_median === "number" ? `${formatCompactNumber(entry.stats.view_count_median)}回` : "—";
                  const fetchedLabel = entry.fetched_at ? `取得 ${formatShortDate(entry.fetched_at)}` : "";
                  return (
                    <button
                      key={entry.playlist_channel_id}
                      type="button"
                      className={active ? "benchmarks-yt-item is-active" : "benchmarks-yt-item"}
                      onClick={() => handleSelectYtDlpEntry(entry)}
                      title={label}
                    >
                      <div className="benchmarks-yt-item__row">
                        <div
                          className={
                            avatarUrl ? "benchmarks-yt-item__thumb benchmarks-yt-item__thumb--avatar" : "benchmarks-yt-item__thumb"
                          }
                          aria-hidden="true"
                        >
                          {thumbUrl ? (
                            <img src={thumbUrl} alt="" loading="lazy" />
                          ) : (
                            <div className="benchmarks-yt-item__thumb-placeholder">—</div>
                          )}
                        </div>
                        <div className="benchmarks-yt-item__body">
                          <div className="benchmarks-yt-item__name">{label}</div>
                          <div className="benchmarks-yt-item__meta mono">
                            {subLabel}
                            {"  "}·{"  "}再生数中央値 {medianViews}
                            {fetchedLabel ? `  ·  ${fetchedLabel}` : ""}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </aside>

          <main className="benchmarks-main">
            {!activeYtDlpEntry ? (
              <div className="channel-profile-banner channel-profile-banner--info">左の一覧からチャンネルを選択してください。</div>
            ) : (
              <>
                <section className="channel-card">
                  <div className="channel-card__header">
                    <div className="channel-card__heading">
                      <div className="benchmarks-channel-heading">
                        {activeYtDlpAvatarUrl ? (
                          <img
                            src={activeYtDlpAvatarUrl}
                            alt=""
                            className="benchmarks-channel-avatar"
                            loading="lazy"
                          />
                        ) : null}
                        <div className="benchmarks-channel-heading__text">
                          <h4>{(activeYtDlpEntry.playlist_channel ?? activeYtDlpEntry.playlist_uploader_id ?? activeYtDlpEntry.playlist_channel_id).trim()}</h4>
                          <span className="channel-card__total mono">{activeYtDlpEntry.playlist_channel_id}</span>
                        </div>
                      </div>
                    </div>
                    <div className="benchmarks-summary-actions">
                      {activeYtDlpEntry.source_url ? (
                        <a
                          className="channel-card__action"
                          href={activeYtDlpEntry.source_url}
                          target="_blank"
                          rel="noreferrer"
                          title="YouTubeを開く"
                        >
                          YouTube
                        </a>
                      ) : null}
                      {activeYtDlpReportPath ? (
                        <button
                          type="button"
                          className="channel-card__action"
                          onClick={() => void loadYtDlpReport(activeYtDlpReportPath)}
                          disabled={activeYtDlpReportState?.loading}
                          title={activeYtDlpReportPath}
                        >
                          {activeYtDlpReportState?.loading ? "更新中…" : "レポート再読込"}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="channel-card__action"
                        onClick={handleCloseYtDlpAnalysisWithUrl}
                        title="選択解除"
                      >
                        閉じる
                      </button>
                    </div>
                  </div>

                  <div className="channel-profile-banner channel-profile-banner--info" style={{ marginBottom: 12 }}>
                    CTR（クリック率）は競合チャンネルの公開データから取得できません（YouTube Analytics所有者指標）。代わりに公開メタ（再生数/尺/タイトル/サムネ）を整理しています。
                  </div>

                  <dl className="benchmarks-kv" style={{ marginTop: 0 }}>
                    <dt>取得日</dt>
                    <dd>{formatShortDate(activeYtDlpEntry.fetched_at ?? null)}</dd>

                    <dt>動画数</dt>
                    <dd>{typeof activeYtDlpEntry.video_count === "number" ? `${activeYtDlpEntry.video_count.toLocaleString("ja-JP")}本` : "—"}</dd>

                    <dt>再生数（中央値）</dt>
                    <dd>
                      {typeof activeYtDlpEntry.stats?.view_count_median === "number"
                        ? `${formatCompactNumber(activeYtDlpEntry.stats.view_count_median)}回`
                        : "—"}
                    </dd>

                    <dt title="再生数の75パーセンタイル（上位25%ライン）">再生数（p75）</dt>
                    <dd>
                      {typeof activeYtDlpEntry.stats?.view_count_p75 === "number"
                        ? `${formatCompactNumber(activeYtDlpEntry.stats.view_count_p75)}回`
                        : "—"}
                    </dd>

                    <dt>尺（中央値）</dt>
                    <dd>{formatDurationSeconds(activeYtDlpEntry.stats?.duration_median_sec ?? null)}</dd>

                    <dt title="タイトルが「【...】」で始まる割合">【】始まり率</dt>
                    <dd>
                      {typeof activeYtDlpEntry.stats?.title_starts_with_bracket_ratio === "number"
                        ? `${Math.round(activeYtDlpEntry.stats.title_starts_with_bracket_ratio * 100)}%`
                        : "—"}
                    </dd>

                    <dt title="「【...】」で始まる場合の最多プレフィックス">最多【】</dt>
                    <dd>{activeYtDlpEntry.stats?.top_bracket_prefix ?? "—"}</dd>
                  </dl>
                </section>

                <section className="channel-card">
                  <div className="benchmarks-card-header">
                    <h4>サムネ分析（yt-dlp）</h4>
                    <div className="benchmarks-card-actions">
                      {activeYtDlpEntry ? (
                        <button
                          type="button"
                          className="channel-profile-button channel-profile-button--ghost"
                          onClick={() => void handleCopyYtDlpThumbTsv()}
                          disabled={!filteredYtDlpVideos.length}
                          title="表示中のサムネ分析をTSVでコピー"
                        >
                          TSVコピー
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="channel-profile-button channel-profile-button--ghost"
                        onClick={() => void loadYtDlpIndex()}
                        disabled={ytDlpIndexLoading}
                        title="workspaces/research/YouTubeベンチマーク（yt-dlp）/REPORTS.json を再読込"
                      >
                        {ytDlpIndexLoading ? "更新中…" : "インデックス再読込"}
                      </button>
                      {activeYtDlpEntry ? (
                        <button
                          type="button"
                          className="channel-profile-button channel-profile-button--ghost"
                          onClick={handleCloseYtDlpAnalysisWithUrl}
                        >
                          閉じる
                        </button>
                      ) : null}
                    </div>
                  </div>

                  {ytDlpIndexError ? <div className="channel-profile-banner channel-profile-banner--error">{ytDlpIndexError}</div> : null}

                  {!activeYtDlpEntry ? (
                    <>
                      <p className="muted">左の一覧から選択すると、言語化データを確認できます。</p>
                      {ytDlpEntries.length ? (
                        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                          <input
                            className="benchmarks-sidebar__search"
                            type="search"
                            value={ytDlpPickerKeyword}
                            onChange={(event) => setYtDlpPickerKeyword(event.target.value)}
                            placeholder="yt-dlpレポート検索（チャンネル名 / @handle）"
                            style={{ flex: "1 1 260px", minWidth: 220, maxWidth: 520 }}
                          />
                          <select
                            defaultValue=""
                            onChange={(event) => {
                              const selected = event.target.value;
                              if (!selected) return;
                              const entry = ytDlpEntries.find((it) => it.playlist_channel_id === selected);
                              if (entry) {
                                handleSelectYtDlpEntry(entry);
                              }
                            }}
                            style={{ flex: "0 0 auto", minWidth: 260 }}
                          >
                            <option value="">yt-dlpレポートを選択…</option>
                            {ytDlpPickerEntries.map((entry) => {
                              const name =
                                (entry.playlist_channel ?? "").trim() ||
                                (entry.playlist_uploader_id ?? "").trim() ||
                                entry.playlist_channel_id;
                              const handle = normalizeHandle(entry.playlist_uploader_id);
                              return (
                                <option key={entry.playlist_channel_id} value={entry.playlist_channel_id}>
                                  {handle ? `${name} (${handle})` : name}
                                </option>
                              );
                            })}
                          </select>
                        </div>
                      ) : (
                        <p className="muted mono">{ytDlpIndexLoading ? "yt-dlpインデックス読み込み中…" : "yt-dlpインデックスが空です。"}</p>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="benchmarks-thumb-meta">
                        <div className="benchmarks-thumb-meta__title">
                          <strong>
                            {activeYtDlpEntry.playlist_channel ??
                              activeYtDlpEntry.playlist_uploader_id ??
                              activeYtDlpEntry.playlist_channel_id}
                          </strong>
                          <span className="mono">{activeYtDlpEntry.playlist_uploader_id ? ` ${activeYtDlpEntry.playlist_uploader_id}` : ""}</span>
                        </div>
                        <div className="benchmarks-thumb-meta__sub mono">
                          {activeYtDlpEntry.fetched_at ? `取得: ${formatShortDate(activeYtDlpEntry.fetched_at)}` : ""}
                          {activeYtDlpEntry.video_count ? ` ・ 動画数: ${activeYtDlpEntry.video_count}` : ""}
                          {ytDlpThumbStats.total ? ` ・ 分析: ${ytDlpThumbStats.analyzed}/${ytDlpThumbStats.total}` : ""}
                          {activeYtDlpEntry.report_json_path ? ` ・ ${activeYtDlpEntry.report_json_path}` : ""}
                        </div>
                      </div>

                      <div className="benchmarks-thumb-controls">
                        <div className="benchmarks-thumb-tabs">
                          <button
                            type="button"
                            className={activeYtDlpTab === "top" ? "benchmarks-chip is-active" : "benchmarks-chip"}
                            onClick={() => setActiveYtDlpTab("top")}
                          >
                            再生数上位
                          </button>
                          <button
                            type="button"
                            className={activeYtDlpTab === "recent" ? "benchmarks-chip is-active" : "benchmarks-chip"}
                            onClick={() => setActiveYtDlpTab("recent")}
                          >
                            直近
                          </button>
                          <button
                            type="button"
                            className={ytDlpShowAnalyzedOnly ? "benchmarks-chip is-active" : "benchmarks-chip"}
                            onClick={() => setYtDlpShowAnalyzedOnly((prev) => !prev)}
                            title="分析済み（thumbnail_insightsあり）の動画だけ表示"
                          >
                            分析済みのみ
                          </button>
                        </div>

                        {activeYtDlpHookOptions.length ? (
                          <div className="benchmarks-thumb-tags">
                            <span className="benchmarks-thumb-filter-label">フック:</span>
                            <button
                              type="button"
                              className={!activeYtDlpHookFilter ? "benchmarks-chip is-active" : "benchmarks-chip"}
                              onClick={() => setActiveYtDlpHookFilter(null)}
                              title="フック絞り込み解除"
                            >
                              全フック
                            </button>
                            {activeYtDlpHookOptions.map((item) => (
                              <button
                                key={item.hook}
                                type="button"
                                className={activeYtDlpHookFilter === item.hook ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                onClick={() => setActiveYtDlpHookFilter((prev) => (prev === item.hook ? null : item.hook))}
                                title={`${item.count} 件`}
                              >
                                {item.hook}
                              </button>
                            ))}
                          </div>
                        ) : null}

                        {activeYtDlpTagOptions.length ? (
                          <div className="benchmarks-thumb-tags">
                            <span className="benchmarks-thumb-filter-label">タグ:</span>
                            <button
                              type="button"
                              className={!activeYtDlpTagFilter ? "benchmarks-chip is-active" : "benchmarks-chip"}
                              onClick={() => setActiveYtDlpTagFilter(null)}
                              title="タグ絞り込み解除"
                            >
                              全タグ
                            </button>
                            {activeYtDlpTagOptions.map((item) => (
                              <button
                                key={item.tag}
                                type="button"
                                className={activeYtDlpTagFilter === item.tag ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                onClick={() => setActiveYtDlpTagFilter(item.tag)}
                                title={`${item.count} 件`}
                              >
                                {item.tag}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>

                      {ytDlpThumbnailSummary ? (
                        <section className="benchmarks-thumb-style" aria-label="サムネスタイル集計">
                          <div className="benchmarks-card-header benchmarks-thumb-style__header">
                            <div className="benchmarks-thumb-style__header-left">
                              <h4>サムネスタイル（言語化 / 集計）</h4>
                              {ytDlpThumbnailSummary.generatedAt ? (
                                <div className="benchmarks-thumb-style__meta muted small-text mono" title={ytDlpThumbnailSummary.generatedAt}>
                                  生成: {formatShortDate(ytDlpThumbnailSummary.generatedAt)}
                                </div>
                              ) : null}
                            </div>
                            <span className="badge">{ytDlpThumbnailSummary.insightCount || ytDlpThumbStats.total} 枚</span>
                          </div>
                          <div className="benchmarks-thumb-style__grid">
                            {ytDlpThumbnailSummary.hookTypes.length ? (
                              <div className="benchmarks-thumb-style__section benchmarks-thumb-style__section--hooks">
                                <div className="benchmarks-thumb-style__label">頻出フック</div>
                                <div className="benchmarks-thumb-style__items">
                                  {ytDlpThumbnailSummary.hookTypes.map((item) => (
                                    <span key={`hook-${item.value}`} className="benchmarks-badge">
                                      {item.value}
                                      <span className="mono">{item.count}</span>
                                    </span>
                                  ))}
                                </div>
                              </div>
                            ) : null}

                            {activeYtDlpDesignOptions.length ? (
                              <div className="benchmarks-thumb-style__section benchmarks-thumb-style__section--design">
                                <div className="benchmarks-thumb-style__label">頻出デザイン要素</div>
                                <div className="benchmarks-thumb-style__items">
                                  {activeYtDlpDesignOptions.slice(0, 14).map((item) => (
                                    <span key={`design-${item.value}`} className="benchmarks-badge">
                                      {item.value}
                                      <span className="mono">{item.count}</span>
                                    </span>
                                  ))}
                                </div>
                              </div>
                            ) : null}

                            {ytDlpThumbnailSummary.topTags.length ? (
                              <div className="benchmarks-thumb-style__section benchmarks-thumb-style__section--tags">
                                <div className="benchmarks-thumb-style__label">頻出タグ</div>
                                <div className="benchmarks-thumb-style__items">
                                  {ytDlpThumbnailSummary.topTags.slice(0, 16).map((item) => (
                                    <span key={`tag-${item.value}`} className="benchmarks-badge">
                                      {item.value}
                                      <span className="mono">{item.count}</span>
                                    </span>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        </section>
                      ) : null}

                      {!activeYtDlpReportPath ? (
                        <p className="muted">report.json の参照パスがありません。</p>
                      ) : activeYtDlpReportState?.loading ? (
                        <p className="muted">report.json 読み込み中…</p>
                      ) : activeYtDlpReportState?.error ? (
                        <div className="channel-profile-banner channel-profile-banner--error">{activeYtDlpReportState.error}</div>
                      ) : !activeYtDlpReport ? (
                        <p className="muted">report.json が未読み込みです。</p>
                      ) : (
                        <>
                          {ytDlpThumbStats.total > 0 && ytDlpThumbStats.analyzed < ytDlpThumbStats.total ? (
                            <div className="benchmarks-thumb-hint-row">
                              <div className="benchmarks-thumb-hint mono">
                                生成コマンド: {ytDlpAnalyzeCommand || "—"}
                                {ytDlpThumbStats.total
                                  ? `\n(analyzed: ${ytDlpThumbStats.analyzed}/${ytDlpThumbStats.total} ・ caption: ${ytDlpThumbStats.withCaption} ・ text: ${ytDlpThumbStats.withText})`
                                  : ""}
                              </div>
                              {ytDlpAnalyzeCommand ? (
                                <div className="benchmarks-thumb-hint-copy">
                                  <button
                                    type="button"
                                    className="channel-profile-button channel-profile-button--ghost"
                                    onClick={() => void handleCopy(ytDlpAnalyzeCommand, "サムネ分析生成コマンド")}
                                    title="生成コマンドをコピー"
                                  >
                                    コピー
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          {filteredYtDlpVideos.length === 0 ? (
                            <p className="muted">該当する動画がありません。</p>
                          ) : (
                            <div className="benchmarks-thumb-grid">
                              {filteredYtDlpVideos.map((video) => {
                                const id = video.id;
                                const insight = activeYtDlpInsights?.[id] ?? null;
                                const analysis = insight?.analysis ?? null;
                                const thumbUrl = (video.thumbnail_url ?? "").trim() || null;
                                const videoUrl = (video.url ?? "").trim() || null;
                                const title = (video.title ?? "").trim() || id;
                                const viewCount = typeof video.view_count === "number" ? video.view_count : null;
                                const durationSec = typeof video.duration_sec === "number" ? video.duration_sec : null;
                                const tags = analysis?.tags ?? null;
                                const design = analysis?.design_elements ?? null;
                                const isExpanded = Boolean(ytDlpExpandedCards[id]);

                                const copyValueParts: string[] = [];
                                if (analysis?.caption_ja) copyValueParts.push(`caption: ${analysis.caption_ja}`);
                                if (analysis?.thumbnail_text) copyValueParts.push(`thumb_text: ${analysis.thumbnail_text}`);
                                if (analysis?.hook_type) copyValueParts.push(`hook_type: ${analysis.hook_type}`);
                                if (analysis?.promise) copyValueParts.push(`promise: ${analysis.promise}`);
                                if (analysis?.target) copyValueParts.push(`target: ${analysis.target}`);
                                if (analysis?.emotion) copyValueParts.push(`emotion: ${analysis.emotion}`);
                                if (analysis?.composition) copyValueParts.push(`composition: ${analysis.composition}`);
                                if (analysis?.colors) copyValueParts.push(`colors: ${analysis.colors}`);
                                if (Array.isArray(design) && design.length) copyValueParts.push(`design: ${design.join(", ")}`);
                                if (Array.isArray(tags) && tags.length) copyValueParts.push(`tags: ${tags.join(", ")}`);
                                const copyValue = copyValueParts.join("\n").trim();

                                return (
                                  <div key={id} className="benchmarks-thumb-card">
                                    <div className="benchmarks-thumb-card__top">
                                      {thumbUrl ? (
                                        <a href={thumbUrl} target="_blank" rel="noreferrer" className="benchmarks-thumb-card__image-link">
                                          <img src={thumbUrl} alt={title} className="benchmarks-thumb-card__image" loading="lazy" />
                                        </a>
                                      ) : (
                                        <div className="benchmarks-thumb-card__image-placeholder mono">no thumbnail</div>
                                      )}

                                      <div className="benchmarks-thumb-card__body">
                                        <div className="benchmarks-thumb-card__header">
                                          <div className="benchmarks-thumb-card__title">{title}</div>
                                          <div className="benchmarks-thumb-card__actions">
                                            {videoUrl ? (
                                              <a className="benchmarks-competitor__link" href={videoUrl} target="_blank" rel="noreferrer">
                                                YouTube
                                              </a>
                                            ) : null}
                                            {copyValue ? (
                                              <button
                                                type="button"
                                                className="benchmarks-competitor__action"
                                                onClick={() => void handleCopy(copyValue, "サムネ分析")}
                                              >
                                                コピー
                                              </button>
                                            ) : null}
                                            {analysis ? (
                                              <button
                                                type="button"
                                                className="benchmarks-competitor__action"
                                                onClick={() =>
                                                  setYtDlpExpandedCards((prev) => ({
                                                    ...prev,
                                                    [id]: !prev[id],
                                                  }))
                                                }
                                                aria-expanded={isExpanded}
                                                aria-controls={`benchmarks-thumb-details-${id}`}
                                              >
                                                {isExpanded ? "閉じる" : "詳細"}
                                              </button>
                                            ) : null}
                                          </div>
                                        </div>

                                      <div className="benchmarks-thumb-card__meta mono">
                                        {typeof viewCount === "number" ? `${viewCount.toLocaleString("ja-JP")}回` : "—"} ・{" "}
                                        {formatDurationSeconds(typeof durationSec === "number" ? durationSec : undefined)}
                                        {insight?.model ? ` ・ ${insight.model}` : ""}
                                      </div>

                                        {analysis?.caption_ja ? (
                                          <div className="benchmarks-thumb-card__caption">{analysis.caption_ja}</div>
                                        ) : (
                                          <div className="muted small-text">言語化なし（未生成）</div>
                                        )}
                                      </div>
                                    </div>

                                    {analysis && isExpanded ? (
                                      <div className="benchmarks-thumb-card__details" id={`benchmarks-thumb-details-${id}`}>
                                        {analysis.thumbnail_text ? (
                                          <pre className="benchmarks-thumb-card__text mono">{analysis.thumbnail_text}</pre>
                                        ) : null}

                                          <dl className="benchmarks-thumb-card__kv">
                                            <dt>フック</dt>
                                            <dd>{analysis?.hook_type?.trim() ? analysis.hook_type : "—"}</dd>
                                            <dt>約束</dt>
                                            <dd>{analysis?.promise?.trim() ? analysis.promise : "—"}</dd>
                                            <dt>ターゲット</dt>
                                            <dd>{analysis?.target?.trim() ? analysis.target : "—"}</dd>
                                            <dt>感情</dt>
                                            <dd>{analysis?.emotion?.trim() ? analysis.emotion : "—"}</dd>
                                            <dt>構図</dt>
                                            <dd>{analysis?.composition?.trim() ? analysis.composition : "—"}</dd>
                                            <dt>色</dt>
                                            <dd>{analysis?.colors?.trim() ? analysis.colors : "—"}</dd>
                                          </dl>

                                        {Array.isArray(design) && design.length ? (
                                          <div className="benchmarks-thumb-card__chips">
                                            {design.slice(0, 12).map((item) => (
                                              <span key={item} className="benchmarks-badge">
                                                {item}
                                              </span>
                                            ))}
                                          </div>
                                        ) : null}

                                        {Array.isArray(tags) && tags.length ? (
                                          <div className="benchmarks-thumb-card__chips">
                                            {tags.slice(0, 16).map((tag) => (
                                              <button
                                                key={tag}
                                                type="button"
                                                className={activeYtDlpTagFilter === tag ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                                onClick={() => setActiveYtDlpTagFilter((prev) => (prev === tag ? null : tag))}
                                              >
                                                {tag}
                                              </button>
                                            ))}
                                          </div>
                                        ) : null}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </>
                      )}
                    </>
                  )}
                </section>
              </>
            )}
          </main>
        </div>
      ) : (
        <div className="benchmarks-layout">
          <aside className="benchmarks-sidebar channel-card">
          <div className="benchmarks-sidebar__header">
            <h4>ジャンル一覧</h4>
            <span className="benchmarks-sidebar__hint">選択すると右側に表示</span>
          </div>

          <input
            className="benchmarks-sidebar__search"
            type="search"
            value={genreKeyword}
            onChange={(event) => setGenreKeyword(event.target.value)}
            placeholder="ジャンル / CH で検索"
          />

          <div className="benchmarks-sidebar__filters">
            <span className="badge">{filteredGenreIndex.length} 件</span>
            <span className="benchmarks-sidebar__hint">{genreIndexLoading ? "読み込み中…" : ""}</span>
          </div>

          {genreIndexError ? <div className="channel-profile-banner channel-profile-banner--error">{genreIndexError}</div> : null}

          <div className="benchmarks-sidebar__list" role="list">
            {filteredGenreIndex.length === 0 ? (
              <div className="benchmarks-sidebar__empty">{genreIndexLoading ? "読み込み中…" : "該当するジャンルがありません。"}</div>
            ) : (
              filteredGenreIndex.map((entry) => {
                const active = effectiveGenre === entry.name;
                const channelsText = entry.referencedChannels.join(", ");
                const channelsPreview = formatChannelCodesPreview(entry.referencedChannels, 8);
                return (
                  <button
                    key={entry.name}
                    type="button"
                    className={active ? "benchmarks-genre-item is-active" : "benchmarks-genre-item"}
                    onClick={() => handleSelectGenre(entry.name)}
                    title={channelsText ? `${entry.name} — ${channelsText}` : entry.name}
                  >
                    <div className="benchmarks-genre-item__top">
                      <span className="benchmarks-genre-item__name">{entry.name}</span>
                      <span className="benchmarks-genre-item__count mono">{entry.referencedChannels.length}ch</span>
                    </div>
                    {channelsPreview ? <div className="benchmarks-genre-item__meta mono">参照: {channelsPreview}</div> : null}
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <main className="benchmarks-main">
          {!effectiveGenre ? (
            <div className="channel-profile-banner channel-profile-banner--info">左の一覧からジャンルを選択してください。</div>
          ) : (
            <>
              <section className="channel-card">
                <div className="channel-card__header">
                  <div className="channel-card__heading">
                    <h4>{effectiveGenre} / ジャンル概要</h4>
                    <span className="channel-card__total mono">{selectedGenreIndexPath ?? "—"}</span>
                  </div>
                  <div className="benchmarks-summary-actions">
                    {normalizedChannelFilter ? (
                      <button
                        type="button"
                        className="channel-card__action"
                        onClick={handleOpenChannelSettings}
                        title={`${normalizedChannelFilter} のチャンネル設定を開く`}
                      >
                        CH設定
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="channel-card__action"
                      onClick={() => void loadGenreDetails()}
                      disabled={genreDetailsLoading}
                    >
                      {genreDetailsLoading ? "更新中…" : "再読み込み"}
                    </button>
                  </div>
                </div>

                {genreDetailsError ? <div className="channel-profile-banner channel-profile-banner--error">{genreDetailsError}</div> : null}

                <dl className="benchmarks-kv">
                  <dt>参照CH（絞り込み）</dt>
                  <dd>
                    {selectedGenreEntry?.referencedChannels?.length ? (
                      <div className="benchmarks-chips">
                        <button
                          type="button"
                          className={!normalizedChannelFilter ? "benchmarks-chip is-active" : "benchmarks-chip"}
                          onClick={() => setChannelFilter(null)}
                          title="絞り込み解除"
                        >
                          全CH
                        </button>
                        {selectedGenreEntry.referencedChannels.map((code) => {
                          const normalized = code.toUpperCase();
                          const name = channelLabelByCode.get(normalized);
                          const title = name ? `${normalized} / ${name}` : normalized;
                          const active = normalizedChannelFilter === normalized;
                          return (
                            <button
                              key={normalized}
                              type="button"
                              className={active ? "benchmarks-chip is-active" : "benchmarks-chip"}
                              onClick={() => toggleChannelFilter(normalized)}
                              title={title}
                            >
                              {normalized}
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <span className="mono">—</span>
                    )}
                  </dd>

                  <dt>競合チャンネル</dt>
                  <dd className="mono">
                    {normalizedChannelFilter ? `${visibleCompetitors.length}/${genreCompetitors.length}` : genreCompetitors.length} 件
                  </dd>

                  <dt>共有サンプル</dt>
                  <dd className="mono">
                    {normalizedChannelFilter ? `${visibleSamples.length}/${genreSamples.length}` : genreSamples.length} 件
                  </dd>
                </dl>
              </section>

              <section className="benchmarks-detail-grid">
                <div className="channel-card">
                  <div className="benchmarks-card-header">
                    <h4>競合チャンネル（ジャンル）</h4>
                    <span className="badge">
                      {normalizedChannelFilter ? `${visibleCompetitors.length}/${genreCompetitors.length}` : genreCompetitors.length} 件
                    </span>
                  </div>

                  {visibleCompetitors.length === 0 ? (
                    <p className="muted">
                      {genreDetailsLoading
                        ? "読み込み中…"
                        : normalizedChannelFilter
                          ? "このCHに該当する競合チャンネルがありません。"
                          : "未登録です。"}
                    </p>
                  ) : (
                    <div className="benchmarks-competitors">
                      {visibleCompetitors.map((entry, idx) => {
                        const handle = normalizeHandle(entry.spec.handle);
                        const url = resolveCompetitorUrl(entry.spec);
                        const ytDlpEntry = resolveYtDlpEntry(entry.spec);
                        const title = (entry.spec.name ?? "").trim() || handle || url || entry.raw || "—";
                        return (
                          <div key={`${entry.raw}-${idx}`} className="benchmarks-competitor">
                            <div className="benchmarks-competitor__top">
                              <div className="benchmarks-competitor__title">{title}</div>
                              <div className="benchmarks-competitor__actions">
                                {url ? (
                                  <a className="benchmarks-competitor__link" href={url} target="_blank" rel="noreferrer">
                                    開く
                                  </a>
                                ) : null}
                                {ytDlpEntry?.report_json_path?.trim() ? (
                                  <button
                                    type="button"
                                    className="benchmarks-competitor__action"
                                    onClick={() => handleSelectYtDlpEntry(ytDlpEntry)}
                                    title="yt-dlpレポートからサムネ分析（言語化）を確認"
                                  >
                                    サムネ分析
                                  </button>
                                ) : null}
                              </div>
                            </div>
                            <div className="benchmarks-competitor__meta">
                              <span className="mono">{handle ?? "—"}</span>
                              {entry.spec.url?.trim() ? <span className="mono">{entry.spec.url}</span> : null}
                            </div>
                            {entry.spec.note?.trim() ? <div className="benchmarks-competitor__note">{entry.spec.note}</div> : null}
                            {entry.referencedChannels.length ? (
                              <div className="benchmarks-chips">
                                {entry.referencedChannels.map((code) => {
                                  const normalized = code.toUpperCase();
                                  const name = channelLabelByCode.get(normalized);
                                  const title = name ? `${normalized} / ${name}` : normalized;
                                  const active = normalizedChannelFilter === normalized;
                                  return (
                                    <button
                                      key={`${entry.raw}-${normalized}`}
                                      type="button"
                                      className={active ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                      onClick={() => toggleChannelFilter(normalized)}
                                      title={title}
                                    >
                                      {normalized}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="channel-card">
                  <div className="benchmarks-card-header">
                    <h4>ジャンルメモ（手動）</h4>
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={() => void handleCopy(genreManual ?? "", "ジャンルメモ")}
                      disabled={!genreManual?.trim()}
                    >
                      コピー
                    </button>
                  </div>
                  <pre className="benchmarks-notes">{genreManual?.trim() ? genreManual : "—"}</pre>
                </div>
              </section>

              {/*
              <section className="channel-card">
                <div className="benchmarks-card-header">
                  <h4>サムネ分析（yt-dlp）</h4>
                  <div className="benchmarks-card-actions">
                    {activeYtDlpEntry ? (
                      <button
                        type="button"
                        className="channel-profile-button channel-profile-button--ghost"
                        onClick={() => void handleCopyYtDlpThumbTsv()}
                        disabled={!filteredYtDlpVideos.length}
                        title="表示中のサムネ分析をTSVでコピー"
                      >
                        TSVコピー
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={() => void loadYtDlpIndex()}
                      disabled={ytDlpIndexLoading}
                      title="workspaces/research/YouTubeベンチマーク（yt-dlp）/REPORTS.json を再読込"
                    >
                      {ytDlpIndexLoading ? "更新中…" : "インデックス再読込"}
                    </button>
                    {activeYtDlpEntry ? (
                      <button
                        type="button"
                        className="channel-profile-button channel-profile-button--ghost"
                        onClick={handleCloseYtDlpAnalysis}
                      >
                        閉じる
                      </button>
                    ) : null}
                  </div>
                </div>

                {ytDlpIndexError ? <div className="channel-profile-banner channel-profile-banner--error">{ytDlpIndexError}</div> : null}

                {!activeYtDlpEntry ? (
                  <>
                    <p className="muted">競合チャンネルの「サムネ分析」から選択すると、言語化データを確認できます。</p>
                    {ytDlpEntries.length ? (
                      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                        <input
                          className="benchmarks-sidebar__search"
                          type="search"
                          value={ytDlpPickerKeyword}
                          onChange={(event) => setYtDlpPickerKeyword(event.target.value)}
                          placeholder="yt-dlpレポート検索（チャンネル名 / @handle）"
                          style={{ flex: "1 1 260px", minWidth: 220, maxWidth: 520 }}
                        />
                        <select
                          defaultValue=""
                          onChange={(event) => {
                            const selected = event.target.value;
                            if (!selected) return;
                            const entry = ytDlpEntries.find((it) => it.playlist_channel_id === selected);
                            if (entry) {
                              handleOpenYtDlpAnalysis(entry);
                            }
                          }}
                          style={{ flex: "0 0 auto", minWidth: 260 }}
                        >
                          <option value="">yt-dlpレポートを選択…</option>
                          {ytDlpPickerEntries.map((entry) => {
                            const name =
                              (entry.playlist_channel ?? "").trim() ||
                              (entry.playlist_uploader_id ?? "").trim() ||
                              entry.playlist_channel_id;
                            const handle = normalizeHandle(entry.playlist_uploader_id);
                            return (
                              <option key={entry.playlist_channel_id} value={entry.playlist_channel_id}>
                                {handle ? `${name} (${handle})` : name}
                              </option>
                            );
                          })}
                        </select>
                      </div>
                    ) : (
                      <p className="muted mono">{ytDlpIndexLoading ? "yt-dlpインデックス読み込み中…" : "yt-dlpインデックスが空です。"}</p>
                    )}
                  </>
                ) : (
                  <>
		                    <div className="benchmarks-thumb-meta">
		                      <div className="benchmarks-thumb-meta__title">
		                        <strong>{activeYtDlpEntry.playlist_channel ?? activeYtDlpEntry.playlist_uploader_id ?? activeYtDlpEntry.playlist_channel_id}</strong>
	                        <span className="mono">
	                          {activeYtDlpEntry.playlist_uploader_id ? ` ${activeYtDlpEntry.playlist_uploader_id}` : ""}
	                        </span>
	                      </div>
	                      <div className="benchmarks-thumb-meta__sub mono">
	                        {activeYtDlpEntry.fetched_at ? `fetched_at: ${activeYtDlpEntry.fetched_at}` : ""}
	                        {activeYtDlpEntry.video_count ? ` ・ videos: ${activeYtDlpEntry.video_count}` : ""}
	                        {ytDlpThumbStats.total ? ` ・ analyzed: ${ytDlpThumbStats.analyzed}/${ytDlpThumbStats.total}` : ""}
	                        {activeYtDlpEntry.report_json_path ? ` ・ ${activeYtDlpEntry.report_json_path}` : ""}
	                      </div>
	                    </div>

	                    <div className="benchmarks-thumb-controls">
	                      <div className="benchmarks-thumb-tabs">
	                        <button
                          type="button"
                          className={activeYtDlpTab === "top" ? "benchmarks-chip is-active" : "benchmarks-chip"}
                          onClick={() => setActiveYtDlpTab("top")}
                        >
                          再生数上位
                        </button>
                        <button
                          type="button"
                          className={activeYtDlpTab === "recent" ? "benchmarks-chip is-active" : "benchmarks-chip"}
                          onClick={() => setActiveYtDlpTab("recent")}
	                        >
	                          直近
	                        </button>
	                        <button
	                          type="button"
	                          className={ytDlpShowAnalyzedOnly ? "benchmarks-chip is-active" : "benchmarks-chip"}
	                          onClick={() => setYtDlpShowAnalyzedOnly((prev) => !prev)}
	                          title="分析済み（thumbnail_insightsあり）の動画だけ表示"
	                        >
	                          分析済みのみ
	                        </button>
	                      </div>

	                      {activeYtDlpHookOptions.length ? (
	                        <div className="benchmarks-thumb-tags">
	                          <span className="benchmarks-thumb-filter-label">hook:</span>
	                          <button
	                            type="button"
	                            className={!activeYtDlpHookFilter ? "benchmarks-chip is-active" : "benchmarks-chip"}
	                            onClick={() => setActiveYtDlpHookFilter(null)}
	                            title="hook絞り込み解除"
	                          >
	                            全hook
	                          </button>
	                          {activeYtDlpHookOptions.map((item) => (
	                            <button
	                              key={item.hook}
	                              type="button"
	                              className={activeYtDlpHookFilter === item.hook ? "benchmarks-chip is-active" : "benchmarks-chip"}
	                              onClick={() => setActiveYtDlpHookFilter((prev) => (prev === item.hook ? null : item.hook))}
	                              title={`${item.count} 件`}
	                            >
	                              {item.hook}
	                            </button>
	                          ))}
	                        </div>
	                      ) : null}

	                      {activeYtDlpTagOptions.length ? (
	                        <div className="benchmarks-thumb-tags">
	                          <span className="benchmarks-thumb-filter-label">tag:</span>
	                          <button
	                            type="button"
	                            className={!activeYtDlpTagFilter ? "benchmarks-chip is-active" : "benchmarks-chip"}
	                            onClick={() => setActiveYtDlpTagFilter(null)}
                            title="タグ絞り込み解除"
                          >
                            全タグ
                          </button>
                          {activeYtDlpTagOptions.map((item) => (
                            <button
                              key={item.tag}
                              type="button"
                              className={activeYtDlpTagFilter === item.tag ? "benchmarks-chip is-active" : "benchmarks-chip"}
                              onClick={() => setActiveYtDlpTagFilter(item.tag)}
                              title={`${item.count} 件`}
                            >
                              {item.tag}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>

                    {!activeYtDlpReportPath ? (
                      <p className="muted">report.json の参照パスがありません。</p>
                    ) : activeYtDlpReportState?.loading ? (
                      <p className="muted mono">読み込み中… ({activeYtDlpReportPath})</p>
                    ) : activeYtDlpReportState?.error ? (
                      <div className="channel-profile-banner channel-profile-banner--error">{activeYtDlpReportState.error}</div>
                    ) : !activeYtDlpReport ? (
                      <p className="muted">レポートが未読み込みです。</p>
                    ) : (
                      <>
                        {!activeYtDlpInsights || Object.keys(activeYtDlpInsights).length === 0 ? (
                          <div className="channel-profile-banner channel-profile-banner--warn">
                            サムネの言語化データが未生成です。
                            {ytDlpAnalyzeCommand ? (
                              <>
                                <div className="benchmarks-thumb-hint-row">
                                  <div className="mono benchmarks-thumb-hint">{ytDlpAnalyzeCommand}</div>
                                  <button
                                    type="button"
                                    className="channel-profile-button channel-profile-button--ghost benchmarks-thumb-hint-copy"
                                    onClick={() => void handleCopy(ytDlpAnalyzeCommand, "サムネ分析生成コマンド")}
                                    title="生成コマンドをコピー"
                                  >
                                    コピー
                                  </button>
                                </div>
                                <div className="muted small-text">
                                  APIが失敗した場合はTHINK MODEにpendingが作成されます（agent_runnerで完了→同コマンド再実行）。
                                </div>
                              </>
                            ) : null}
                          </div>
                        ) : null}

                        {filteredYtDlpVideos.length === 0 ? (
                          <p className="muted">該当する動画がありません。</p>
                        ) : (
                          <div className="benchmarks-thumb-grid">
                            {filteredYtDlpVideos.map((video) => {
                              const id = video.id;
                              const insight = activeYtDlpInsights?.[id] ?? null;
                              const analysis = insight?.analysis ?? null;
                              const thumbUrl = (video.thumbnail_url ?? "").trim() || null;
                              const videoUrl = (video.url ?? "").trim() || null;
                              const title = (video.title ?? "").trim() || id;
                              const viewCount = typeof video.view_count === "number" ? video.view_count : null;
                              const durationSec = typeof video.duration_sec === "number" ? video.duration_sec : null;
                              const tags = analysis?.tags ?? null;
                              const design = analysis?.design_elements ?? null;

                              const copyValueParts: string[] = [];
                              if (analysis?.caption_ja) copyValueParts.push(`caption: ${analysis.caption_ja}`);
                              if (analysis?.thumbnail_text) copyValueParts.push(`thumb_text: ${analysis.thumbnail_text}`);
                              if (analysis?.hook_type) copyValueParts.push(`hook_type: ${analysis.hook_type}`);
                              if (analysis?.promise) copyValueParts.push(`promise: ${analysis.promise}`);
                              if (analysis?.target) copyValueParts.push(`target: ${analysis.target}`);
                              if (analysis?.emotion) copyValueParts.push(`emotion: ${analysis.emotion}`);
                              if (analysis?.composition) copyValueParts.push(`composition: ${analysis.composition}`);
                              if (analysis?.colors) copyValueParts.push(`colors: ${analysis.colors}`);
                              if (Array.isArray(design) && design.length) copyValueParts.push(`design: ${design.join(", ")}`);
                              if (Array.isArray(tags) && tags.length) copyValueParts.push(`tags: ${tags.join(", ")}`);
                              const copyValue = copyValueParts.join("\n").trim();

                              return (
                                <div key={id} className="benchmarks-thumb-card">
                                  {thumbUrl ? (
                                    <a href={thumbUrl} target="_blank" rel="noreferrer" className="benchmarks-thumb-card__image-link">
                                      <img src={thumbUrl} alt={title} className="benchmarks-thumb-card__image" loading="lazy" />
                                    </a>
                                  ) : (
                                    <div className="benchmarks-thumb-card__image-placeholder mono">no thumbnail</div>
                                  )}

                                  <div className="benchmarks-thumb-card__body">
                                    <div className="benchmarks-thumb-card__header">
                                      <div className="benchmarks-thumb-card__title">{title}</div>
                                      <div className="benchmarks-thumb-card__actions">
                                        {videoUrl ? (
                                          <a className="benchmarks-competitor__link" href={videoUrl} target="_blank" rel="noreferrer">
                                            YouTube
                                          </a>
                                        ) : null}
                                        {copyValue ? (
                                          <button
                                            type="button"
                                            className="benchmarks-competitor__action"
                                            onClick={() => void handleCopy(copyValue, "サムネ分析")}
                                          >
                                            コピー
                                          </button>
                                        ) : null}
                                      </div>
                                    </div>

                                    <div className="benchmarks-thumb-card__meta mono">
                                      {typeof viewCount === "number" ? `${viewCount.toLocaleString("ja-JP")} views` : "—"} ・{" "}
                                      {formatDurationSeconds(durationSec)}
                                    </div>

                                    {analysis?.caption_ja ? (
                                      <div className="benchmarks-thumb-card__caption">{analysis.caption_ja}</div>
                                    ) : (
                                      <div className="muted small-text">言語化なし（未生成）</div>
                                    )}

                                    {analysis?.thumbnail_text ? (
                                      <pre className="benchmarks-thumb-card__text mono">{analysis.thumbnail_text}</pre>
                                    ) : null}

                                    <dl className="benchmarks-thumb-card__kv">
                                      <dt>hook</dt>
                                      <dd>{analysis?.hook_type?.trim() ? analysis.hook_type : "—"}</dd>
                                      <dt>promise</dt>
                                      <dd>{analysis?.promise?.trim() ? analysis.promise : "—"}</dd>
                                      <dt>target</dt>
                                      <dd>{analysis?.target?.trim() ? analysis.target : "—"}</dd>
                                      <dt>emotion</dt>
                                      <dd>{analysis?.emotion?.trim() ? analysis.emotion : "—"}</dd>
                                      <dt>composition</dt>
                                      <dd>{analysis?.composition?.trim() ? analysis.composition : "—"}</dd>
                                      <dt>colors</dt>
                                      <dd>{analysis?.colors?.trim() ? analysis.colors : "—"}</dd>
                                    </dl>

                                    {Array.isArray(design) && design.length ? (
                                      <div className="benchmarks-thumb-card__chips">
                                        {design.slice(0, 12).map((item) => (
                                          <span key={item} className="benchmarks-badge">
                                            {item}
                                          </span>
                                        ))}
                                      </div>
                                    ) : null}

                                    {Array.isArray(tags) && tags.length ? (
                                      <div className="benchmarks-thumb-card__chips">
                                        {tags.slice(0, 16).map((tag) => (
                                          <button
                                            key={tag}
                                            type="button"
                                            className={activeYtDlpTagFilter === tag ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                            onClick={() => setActiveYtDlpTagFilter((prev) => (prev === tag ? null : tag))}
                                          >
                                            {tag}
                                          </button>
                                        ))}
                                      </div>
                                    ) : null}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
              </section>
              */}

              <section className="channel-card">
                <div className="benchmarks-card-header">
                  <h4>共有サンプル（プレビュー＋分析）</h4>
                  <div className="benchmarks-card-actions">
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={() => void handleAnalyzeAllSamples(visibleSamples.map((entry) => entry.sample))}
                      disabled={bulkLoading || !visibleSamples.length}
                    >
                      {bulkLoading ? "分析中…" : normalizedChannelFilter ? "表示中を読み込み" : "全件読み込み"}
                    </button>
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={clearSampleCache}
                      disabled={!Object.keys(sampleCache).length}
                    >
                      キャッシュ削除
                    </button>
                  </div>
                </div>

                {copyBanner ? <div className="channel-profile-banner channel-profile-banner--info">{copyBanner}</div> : null}

                {visibleSamples.length === 0 ? (
                  <p className="muted">
                    {genreDetailsLoading
                      ? "読み込み中…"
                      : normalizedChannelFilter
                        ? "このCHに該当するサンプルがありません。"
                        : "未登録です。"}
                  </p>
                ) : (
                  <div className="benchmarks-scripts-grid">
                    <div className="benchmarks-sample-list" role="list">
                      {visibleSamples.map((entry, idx) => {
                        const sample = entry.sample;
                        const key = sampleKey(sample);
                        const cached = sampleCache[key];
                        const active = key === activeSampleKeyState;
                        const label = buildSampleLabel(sample);
                        const metrics = cached?.metrics;
                        const hint = `${sample.base} / ${sample.path}`;
                        const noteLine = (entry.note ?? "").split("\n")[0]?.trim();
                        const noteHasMore = Boolean((entry.note ?? "").trim().includes("\n"));
                        return (
                          <div
                            key={`${key}-${idx}`}
                            className={active ? "benchmarks-sample-item is-active" : "benchmarks-sample-item"}
                            role="button"
                            tabIndex={0}
                            onClick={() => handleSelectSample(sample)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                handleSelectSample(sample);
                              }
                            }}
                            title={hint}
                          >
                            <div className="benchmarks-sample-item__title">{label}</div>
                            <div className="benchmarks-sample-item__meta mono">
                              {sample.path}
                              {entry.status ? ` ・ ${entry.status}` : ""}
                            </div>
                            {noteLine ? (
                              <div className="benchmarks-sample-item__note">
                                {noteLine}
                                {noteHasMore ? "…" : ""}
                              </div>
                            ) : null}
                            {entry.referencedChannels.length ? (
                              <div className="benchmarks-sample-item__chips">
                                {entry.referencedChannels.map((code) => {
                                  const normalized = code.toUpperCase();
                                  const name = channelLabelByCode.get(normalized);
                                  const title = name ? `${normalized} / ${name}` : normalized;
                                  const chipActive = normalizedChannelFilter === normalized;
                                  return (
                                    <button
                                      key={`${key}-${normalized}`}
                                      type="button"
                                      className={chipActive ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        toggleChannelFilter(normalized);
                                      }}
                                      title={title}
                                    >
                                      {normalized}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}
                            {cached?.loading ? (
                              <div className="benchmarks-sample-item__status mono">読み込み中…</div>
                            ) : cached?.error ? (
                              <div className="benchmarks-sample-item__status mono is-warn">取得失敗</div>
                            ) : metrics ? (
                              <div className="benchmarks-sample-item__status mono">
                                {metrics.nonWhitespaceChars.toLocaleString("ja-JP")}字 / {metrics.estimatedMinutes.toFixed(1)}分
                              </div>
                            ) : (
                              <div className="benchmarks-sample-item__status mono">未読み込み</div>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    <div className="benchmarks-preview">
                      {!activeSample ? (
                        <div className="muted">サンプルを選択してください。</div>
                      ) : (
                        <>
                          <div className="benchmarks-preview__header">
                            <div className="benchmarks-preview__title">
                              <strong>{buildSampleLabel(activeSample)}</strong>
                              <div className="benchmarks-preview__path mono">
                                {activeSample.path}
                                {activeSampleEntry?.status ? ` ・ ${activeSampleEntry.status}` : ""}
                                {activeSampleState?.modified ? ` ・ 更新: ${formatShortDate(activeSampleState.modified)}` : ""}
                                {activeSampleState?.size ? ` ・ ${formatBytes(activeSampleState.size) ?? ""}` : ""}
                              </div>
                            </div>
                            <div className="benchmarks-preview__actions">
                              <button
                                type="button"
                                className="channel-profile-button channel-profile-button--ghost"
                                onClick={() => void handleCopy(`${activeSample.base} / ${activeSample.path}`, "パス")}
                              >
                                パス
                              </button>
                              <button
                                type="button"
                                className="channel-profile-button channel-profile-button--ghost"
                                onClick={() => void handleCopy(activeSampleState?.content ?? "", "本文")}
                                disabled={!activeSampleState?.content}
                              >
                                本文
                              </button>
                              <button
                                type="button"
                                className="channel-profile-button channel-profile-button--ghost"
                                onClick={() => void loadSample(activeSample)}
                              >
                                更新
                              </button>
                            </div>
                          </div>

                          {activeSampleEntry?.referencedChannels?.length ? (
                            <div className="benchmarks-chips">
                              {activeSampleEntry.referencedChannels.map((code) => {
                                const normalized = code.toUpperCase();
                                const name = channelLabelByCode.get(normalized);
                                const title = name ? `${normalized} / ${name}` : normalized;
                                const active = normalizedChannelFilter === normalized;
                                return (
                                  <button
                                    key={`${activeSample.base}:${activeSample.path}:${normalized}`}
                                    type="button"
                                    className={active ? "benchmarks-chip is-active" : "benchmarks-chip"}
                                    onClick={() => toggleChannelFilter(normalized)}
                                    title={title}
                                  >
                                    {normalized}
                                  </button>
                                );
                              })}
                            </div>
                          ) : null}

                          {activeSampleState?.loading ? (
                            <div className="muted">読み込み中…</div>
                          ) : activeSampleState?.error ? (
                            <div className="channel-profile-banner channel-profile-banner--error">{activeSampleState.error}</div>
                          ) : activeSampleState?.content ? (
                            <>
                              {activeSampleEntry?.note?.trim() ? (
                                <div className="benchmarks-preview__note">
                                  <div className="benchmarks-preview__note-label">メモ</div>
                                  <pre className="benchmarks-preview__note-body">{activeSampleEntry.note}</pre>
                                </div>
                              ) : null}
                              {activeSampleState.metrics ? (
                                <div className="benchmarks-metrics">
                                  {(() => {
                                    const metrics = activeSampleState.metrics!;
                                    return (
                                      <>
                                        <span className="benchmarks-badge">{metrics.nonWhitespaceChars.toLocaleString("ja-JP")}字</span>
                                        <span className="benchmarks-badge">推定 {metrics.estimatedMinutes.toFixed(1)} 分</span>
                                        <span className="benchmarks-badge">
                                          行 {metrics.lines.toLocaleString("ja-JP")}（非空 {metrics.nonEmptyLines.toLocaleString("ja-JP")}）
                                        </span>
                                        <span className="benchmarks-badge">
                                          見出し {metrics.headings.toLocaleString("ja-JP")} / 区切り {metrics.dividers.toLocaleString("ja-JP")}
                                        </span>
                                        {metrics.firstNonEmptyLine ? (
                                          <span className="benchmarks-badge">
                                            先頭: {metrics.firstNonEmptyLine.slice(0, 36)}
                                            {metrics.firstNonEmptyLine.length > 36 ? "…" : ""}
                                          </span>
                                        ) : null}
                                      </>
                                    );
                                  })()}
                                </div>
                              ) : null}

                              {activeSampleState.metrics?.topKanjiPhrases?.length ? (
                                <div className="benchmarks-tokens">
                                  {activeSampleState.metrics.topKanjiPhrases.map((entry) => (
                                    <button
                                      key={entry.phrase}
                                      type="button"
                                      className="benchmarks-token"
                                      onClick={() => void handleCopy(entry.phrase, "キーワード")}
                                      title="クリックでコピー"
                                    >
                                      {entry.phrase}
                                      <span className="benchmarks-token__count">{entry.count}</span>
                                    </button>
                                  ))}
                                </div>
                              ) : null}

                              <pre className="benchmarks-preview__content">{activeSampleState.content}</pre>
                            </>
                          ) : (
                            <div className="muted">未読み込みです。左のサンプルを選択してください。</div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                )}
              </section>

              {genreUnreferenced.length ? (
                <section className="channel-card">
                  <details className="benchmarks-details">
                    <summary className="benchmarks-details__summary">
                      未参照ファイル（refs=0） <span className="badge">{genreUnreferenced.length} 件</span>
                    </summary>
                    <div className="benchmarks-details__body">
                      <ul className="benchmarks-unref-list">
                        {genreUnreferenced.map((path) => (
                          <li key={path}>
                            <button
                              type="button"
                              className="benchmarks-unref-item mono"
                              onClick={() => void handleCopy(path, "パス")}
                              title="クリックでコピー"
                            >
                              {path}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </details>
                </section>
              ) : null}
            </>
          )}
        </main>
      </div>
      )}
    </section>
  );
}
