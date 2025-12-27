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
  const queryGenre = (queryParams.get("genre") ?? "").trim();

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

  const channelLabelByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const channel of channels ?? []) {
      map.set(channel.code.toUpperCase(), resolveChannelDisplayName(channel));
    }
    return map;
  }, [channels]);

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
    void loadGenreRoot();
  }, [loadGenreRoot]);

  useEffect(() => {
    if (queryGenre) return;
    if (!genreIndex.length) return;
    const params = new URLSearchParams(location.search);
    params.set("genre", genreIndex[0].name);
    params.delete("view");
    params.delete("channel");
    const search = params.toString();
    navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
  }, [genreIndex, location.search, navigate, queryGenre]);

  const handleSelectGenre = useCallback(
    (genreName: string) => {
      const value = genreName.trim();
      if (!value) return;
      const params = new URLSearchParams(location.search);
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
    setChannelFilter(null);
  }, [effectiveGenre]);

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
    void loadGenreDetails();
  }, [loadGenreDetails]);

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
      setCopyBanner(err instanceof Error ? err.message : String(err));
      window.setTimeout(() => setCopyBanner(null), 2500);
    }
  }, []);

  return (
    <section className="benchmarks-page workspace--channel-clean">
      <header className="benchmarks-header channel-card">
        <div className="benchmarks-header__title">
          <p className="eyebrow">/benchmarks</p>
          <h1>ベンチマーク（ジャンル別）</h1>
          <p className="benchmarks-header__subtitle">ジャンル → 競合 → 台本サンプル → 分析 を1ページで確認します。</p>
        </div>
        <div className="benchmarks-header__controls">
          <button
            type="button"
            className="channel-profile-button channel-profile-button--ghost"
            onClick={() => void loadGenreRoot()}
            disabled={genreIndexLoading}
          >
            {genreIndexLoading ? "ジャンル更新中…" : "ジャンル再読み込み"}
          </button>
        </div>
      </header>

      <div className="benchmarks-layout">
        <aside className="benchmarks-sidebar channel-card">
          <div className="benchmarks-sidebar__header">
            <h4>ジャンル一覧</h4>
            <span className="benchmarks-sidebar__hint">クリックで切替</span>
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
                    {channelsText ? <div className="benchmarks-genre-item__meta mono">{channelsText}</div> : null}
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
                        const title = (entry.spec.name ?? "").trim() || handle || url || entry.raw || "—";
                        return (
                          <div key={`${entry.raw}-${idx}`} className="benchmarks-competitor">
                            <div className="benchmarks-competitor__top">
                              <div className="benchmarks-competitor__title">{title}</div>
                              {url ? (
                                <a className="benchmarks-competitor__link" href={url} target="_blank" rel="noreferrer">
                                  開く
                                </a>
                              ) : null}
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
                              {sample.base} / {sample.path}
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
                                {activeSample.base} / {activeSample.path}
                                {activeSampleEntry?.status ? ` ・ ${activeSampleEntry.status}` : ""}
                                {activeSampleState?.modified ? ` ・ 更新: ${activeSampleState.modified}` : ""}
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
    </section>
  );
}
