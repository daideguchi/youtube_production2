import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { fetchPlanningRows } from "../api/client";
import type { PlanningCsvRow } from "../api/types";
import { apiUrl } from "../api/baseUrl";
import type { ShellOutletContext } from "../layouts/AppShell";
import { safeLocalStorage } from "../utils/safeStorage";
import "./PublishingProgressPage.css";

type SortKey =
  | "runway_asc"
  | "runway_desc"
  | "pending_desc"
  | "pending_asc"
  | "ratio_asc"
  | "ratio_desc"
  | "code";

type ManualScheduleUpcomingItem = {
  scheduled_publish_at: string; // YYYY-MM-DD
  title: string | null;
};

type ManualScheduleChannelSummary = {
  channel: string;
  last_published_date: string | null; // YYYY-MM-DD
  last_scheduled_date: string | null; // YYYY-MM-DD
  schedule_runway_days: number | null;
  upcoming_count: number;
  upcoming: ManualScheduleUpcomingItem[];
};

type ManualScheduleSnapshot = {
  parsed_at: string;
  channels: ManualScheduleChannelSummary[];
  warnings: string[];
};

type ChannelPublishSummary = {
  code: string;
  name: string;
  total: number;
  posted: number;
  pending: number;
  ratio: number; // 0..1
  lastPostedVideo: string | null;
  nextPendingVideo: string | null;
  latestUpdatedAt: string | null;
  schedule: ManualScheduleChannelSummary | null;
};

type ApiErrorShape = { detail?: string };

type YouTubePublishingChannel = {
  channel: string;
  youtube_channel_id?: string | null;
  youtube_handle?: string | null;
  latest_published_at?: string | null;
  latest_published_date_jst?: string | null;
  latest_title?: string | null;
  latest_video_id?: string | null;
  latest_url?: string | null;
};

type YouTubePublishingResponse = {
  status: string;
  generated_at: string;
  fetched_at?: string | null;
  channels: YouTubePublishingChannel[];
  warnings?: string[];
};

function normalizeChannelCode(value: string): string {
  return (value ?? "").trim().toUpperCase();
}

function normalizeVideoToken(value: string | null | undefined): string | null {
  const raw = (value ?? "").trim();
  if (!raw) return null;
  const digits = raw.replace(/\D/g, "");
  if (!digits) return null;
  const n = Number.parseInt(digits, 10);
  if (!Number.isFinite(n)) return null;
  return String(n).padStart(3, "0");
}

function isPostedProgress(value: string | null | undefined): boolean {
  const text = (value ?? "").trim();
  if (!text) return false;
  if (text.includes("投稿済み") || text.includes("公開済み")) return true;
  const lower = text.toLowerCase();
  return lower === "published" || lower === "posted";
}

function formatPercent(ratio: number): string {
  const pct = Math.round(Math.max(0, Math.min(1, ratio)) * 100);
  return `${pct}%`;
}

function safeParseUpdatedAt(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return null;
  return ms;
}

const MANUAL_SCHEDULE_STORAGE_KEY = "publishingProgress.manualScheduleSnapshot.v1";

function isoDateFromParts(year: number, month: number, day: number): string {
  const yyyy = String(year).padStart(4, "0");
  const mm = String(month).padStart(2, "0");
  const dd = String(day).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function isoDateLocal(date: Date): string {
  return isoDateFromParts(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

function dateFromIsoDateLocal(isoDate: string): Date | null {
  const m = isoDate.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  return new Date(year, month - 1, day, 0, 0, 0, 0);
}

function diffDaysIsoDateLocal(fromIsoDate: string, toIsoDate: string): number | null {
  const from = dateFromIsoDateLocal(fromIsoDate);
  const to = dateFromIsoDateLocal(toIsoDate);
  if (!from || !to) return null;
  const msPerDay = 24 * 60 * 60 * 1000;
  return Math.round((to.getTime() - from.getTime()) / msPerDay);
}

function parseStudioDateToIso(value: string): string | null {
  const raw = (value ?? "").trim();
  if (!raw) return null;
  const m = raw.match(/^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\b.*)?$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  return isoDateFromParts(year, month, day);
}

function normalizeManualChannelMarker(line: string): string | null {
  const m = (line ?? "").trim().match(/^ch\s*(\d{1,2})$/i);
  if (!m) return null;
  const token = String(Number(m[1])).padStart(2, "0");
  return `CH${token}`;
}

function maxIsoDate(dates: string[]): string | null {
  if (!dates.length) return null;
  const sorted = dates.slice().sort();
  return sorted[sorted.length - 1] ?? null;
}

function parseManualScheduleSnapshot(text: string): ManualScheduleSnapshot {
  const normalized = (text ?? "").replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const warnings: string[] = [];
  const perChannel: Record<
    string,
    {
      published: string[];
      scheduled: Array<{ date: string; title: string | null }>;
    }
  > = {};

  let currentChannel: string | null = null;
  let lastTitle: string | null = null;

  for (let i = 0; i < lines.length; i += 1) {
    const line = (lines[i] ?? "").trim();
    if (!line) continue;

    const ch = normalizeManualChannelMarker(line);
    if (ch) {
      currentChannel = ch;
      lastTitle = null;
      perChannel[currentChannel] ??= { published: [], scheduled: [] };
      continue;
    }

    if (!currentChannel) continue;

    const thumbMatch = line.match(/^動画のサムネイル:\s*(.+)$/);
    if (thumbMatch) {
      lastTitle = (thumbMatch[1] ?? "").trim() || null;
      continue;
    }

    if (line === "公開" || line === "公開予約") {
      let dateIso: string | null = null;
      for (let j = 1; j <= 6 && i + j < lines.length; j += 1) {
        const candidate = (lines[i + j] ?? "").trim();
        const parsed = parseStudioDateToIso(candidate);
        if (parsed) {
          dateIso = parsed;
          break;
        }
      }

      if (!dateIso) {
        warnings.push(`日付が見つかりません: ${currentChannel} (${line}) @line ${i + 1}`);
        continue;
      }

      perChannel[currentChannel] ??= { published: [], scheduled: [] };
      if (line === "公開") {
        perChannel[currentChannel].published.push(dateIso);
      } else {
        perChannel[currentChannel].scheduled.push({ date: dateIso, title: lastTitle });
      }
    }
  }

  const todayIso = isoDateLocal(new Date());
  const channels: ManualScheduleChannelSummary[] = Object.entries(perChannel).map(([channel, data]) => {
    const lastPublished = maxIsoDate(data.published);
    const scheduledDates = data.scheduled.map((s) => s.date);
    const lastScheduled = maxIsoDate(scheduledDates);
    const runwayDaysRaw = lastScheduled ? diffDaysIsoDateLocal(todayIso, lastScheduled) : null;
    const runwayDays = runwayDaysRaw === null ? null : Math.max(0, runwayDaysRaw);

    const upcoming = data.scheduled
      .filter((s) => s.date >= todayIso)
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((s) => ({
        scheduled_publish_at: s.date,
        title: s.title,
      }));

    return {
      channel,
      last_published_date: lastPublished,
      last_scheduled_date: lastScheduled,
      schedule_runway_days: runwayDays,
      upcoming_count: upcoming.length,
      upcoming,
    };
  });

  channels.sort((a, b) => a.channel.localeCompare(b.channel));
  return {
    parsed_at: new Date().toISOString(),
    channels,
    warnings,
  };
}

function formatIsoDate(value: string | null | undefined): string {
  const raw = (value ?? "").trim();
  if (!raw) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) return raw;
  return new Date(ms).toLocaleDateString();
}

async function fetchYouTubePublishing(options?: { refresh?: boolean; limit?: number }): Promise<YouTubePublishingResponse> {
  const params = new URLSearchParams();
  if (options?.refresh) params.set("refresh", "true");
  if (typeof options?.limit === "number") params.set("limit", String(options.limit));
  const query = params.toString();
  const suffix = query ? `?${query}` : "";

  const response = await fetch(apiUrl(`/api/meta/youtube/publishing${suffix}`), {
    method: "GET",
    cache: "no-store",
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data: ApiErrorShape = await response.json();
      if (data.detail) message = data.detail;
    } catch (error) {
      // no-op
    }
    throw new Error(message);
  }

  return response.json() as Promise<YouTubePublishingResponse>;
}

export function PublishingProgressPage() {
  const navigate = useNavigate();
  const { channels } = useOutletContext<ShellOutletContext>();

  const channelNameByCode = useMemo(() => {
    const map: Record<string, string> = {};
    for (const ch of channels) {
      const code = normalizeChannelCode(ch.code);
      if (!code) continue;
      map[code] = (ch.name ?? "").trim() || code;
    }
    return map;
  }, [channels]);

  const [rows, setRows] = useState<PlanningCsvRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [scheduleSnapshot, setScheduleSnapshot] = useState<ManualScheduleSnapshot | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState("");
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [youtubePublishing, setYoutubePublishing] = useState<YouTubePublishingResponse | null>(null);
  const [youtubeError, setYoutubeError] = useState<string | null>(null);
  const [forceYoutubeRefresh, setForceYoutubeRefresh] = useState(false);

  const [search, setSearch] = useState("");
  const [hideCompleted, setHideCompleted] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("runway_asc");

  useEffect(() => {
    try {
      const saved = safeLocalStorage.getItem(MANUAL_SCHEDULE_STORAGE_KEY);
      if (!saved) return;
      const parsed = JSON.parse(saved) as ManualScheduleSnapshot;
      if (!parsed || !Array.isArray(parsed.channels)) return;
      setScheduleSnapshot(parsed);
    } catch (err) {
      // no-op: ignore invalid storage.
    }
  }, []);

  const load = useCallback(async (options?: { youtubeRefresh?: boolean }) => {
    setLoading(true);
    setError(null);
    setYoutubeError(null);
    try {
      const [planningResult, youtubeResult] = await Promise.allSettled([
        fetchPlanningRows(),
        fetchYouTubePublishing({ refresh: Boolean(options?.youtubeRefresh), limit: 1 }),
      ]);

      if (planningResult.status === "fulfilled") {
        setRows(planningResult.value ?? []);
      } else {
        setError(planningResult.reason instanceof Error ? planningResult.reason.message : String(planningResult.reason));
      }

      if (youtubeResult.status === "fulfilled") {
        setYoutubePublishing(youtubeResult.value ?? null);
      } else {
        setYoutubePublishing(null);
        setYoutubeError(youtubeResult.reason instanceof Error ? youtubeResult.reason.message : String(youtubeResult.reason));
      }

      setFetchedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const scheduleByChannel = useMemo(() => {
    const map: Record<string, ManualScheduleChannelSummary> = {};
    for (const s of scheduleSnapshot?.channels ?? []) {
      const code = normalizeChannelCode(s.channel);
      if (!code) continue;
      map[code] = s;
    }
    return map;
  }, [scheduleSnapshot]);

  const applyScheduleDraft = useCallback(() => {
    const text = scheduleDraft.trim();
    if (!text) {
      setScheduleSnapshot(null);
      setScheduleError(null);
      safeLocalStorage.removeItem(MANUAL_SCHEDULE_STORAGE_KEY);
      return;
    }
    try {
      const snapshot = parseManualScheduleSnapshot(text);
      setScheduleSnapshot(snapshot);
      setScheduleError(null);
      safeLocalStorage.setItem(MANUAL_SCHEDULE_STORAGE_KEY, JSON.stringify(snapshot));
    } catch (err) {
      setScheduleError(err instanceof Error ? err.message : String(err));
    }
  }, [scheduleDraft]);

  const clearSchedule = useCallback(() => {
    setScheduleDraft("");
    setScheduleSnapshot(null);
    setScheduleError(null);
    safeLocalStorage.removeItem(MANUAL_SCHEDULE_STORAGE_KEY);
  }, []);

  const onScheduleFileChange = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      setScheduleDraft(text);
      setScheduleError(null);
    } catch (err) {
      setScheduleError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const summaries = useMemo<ChannelPublishSummary[]>(() => {
    const byChannel: Record<string, PlanningCsvRow[]> = {};
    for (const row of rows) {
      const code = normalizeChannelCode(row.channel);
      if (!code) continue;
      if (!byChannel[code]) byChannel[code] = [];
      byChannel[code]?.push(row);
    }

    const codes: string[] = [];
    const seen: Record<string, true> = {};
    for (const code of Object.keys(channelNameByCode)) {
      if (seen[code]) continue;
      seen[code] = true;
      codes.push(code);
    }
    for (const code of Object.keys(byChannel)) {
      if (seen[code]) continue;
      seen[code] = true;
      codes.push(code);
    }
    for (const code of Object.keys(scheduleByChannel)) {
      if (seen[code]) continue;
      seen[code] = true;
      codes.push(code);
    }

    const out: ChannelPublishSummary[] = [];
    for (const code of codes) {
      const list = byChannel[code] ?? [];

      let posted = 0;
      let latestUpdatedMs = -1;
      let latestUpdatedAt: string | null = null;

      let lastPostedNum = -1;
      let lastPostedToken: string | null = null;

      let nextPendingNum = Number.POSITIVE_INFINITY;
      let nextPendingToken: string | null = null;

      for (const row of list) {
        const progress = (row.progress ?? row.columns?.["進捗"] ?? "").trim();
        const isPosted = isPostedProgress(progress);
        if (isPosted) posted += 1;

        const token = normalizeVideoToken(row.video_number);
        if (token) {
          const num = Number.parseInt(token, 10);
          if (Number.isFinite(num)) {
            if (isPosted && num > lastPostedNum) {
              lastPostedNum = num;
              lastPostedToken = token;
            }
            if (!isPosted && num < nextPendingNum) {
              nextPendingNum = num;
              nextPendingToken = token;
            }
          }
        }

        const updatedMs = safeParseUpdatedAt(row.updated_at);
        if (updatedMs !== null && updatedMs > latestUpdatedMs) {
          latestUpdatedMs = updatedMs;
          latestUpdatedAt = row.updated_at ?? null;
        }
      }

      const total = list.length;
      const pending = Math.max(0, total - posted);
      const ratio = total > 0 ? posted / total : 0;

      out.push({
        code,
        name: channelNameByCode[code] || code,
        total,
        posted,
        pending,
        ratio,
        lastPostedVideo: lastPostedToken,
        nextPendingVideo: nextPendingToken,
        latestUpdatedAt,
        schedule: scheduleByChannel[code] ?? null,
      });
    }
    return out;
  }, [rows, channelNameByCode, scheduleByChannel]);

  const totals = useMemo(() => {
    let totalVideos = 0;
    let postedVideos = 0;
    for (const s of summaries) {
      totalVideos += s.total;
      postedVideos += s.posted;
    }
    const pendingVideos = Math.max(0, totalVideos - postedVideos);
    return { totalVideos, postedVideos, pendingVideos };
  }, [summaries]);

  const scheduleTotals = useMemo(() => {
    if (!scheduleSnapshot) return null;
    let upcomingTotal = 0;
    let channelsWithUpcoming = 0;
    let maxScheduledDate: string | null = null;
    for (const ch of scheduleSnapshot.channels ?? []) {
      const upcomingCount = ch.upcoming_count ?? 0;
      upcomingTotal += upcomingCount;
      if (upcomingCount > 0) channelsWithUpcoming += 1;
      const lastScheduled = (ch.last_scheduled_date ?? "").trim();
      if (lastScheduled) {
        if (!maxScheduledDate || lastScheduled > maxScheduledDate) {
          maxScheduledDate = lastScheduled;
        }
      }
    }
    return { upcomingTotal, channelsWithUpcoming, maxScheduledDate };
  }, [scheduleSnapshot]);

  const youtubeByChannel = useMemo(() => {
    const map: Record<string, YouTubePublishingChannel> = {};
    for (const item of youtubePublishing?.channels ?? []) {
      const code = normalizeChannelCode(item.channel);
      if (!code) continue;
      map[code] = item;
    }
    return map;
  }, [youtubePublishing]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = summaries.filter((s) => {
      if (hideCompleted && s.pending === 0) return false;
      if (!q) return true;
      if (s.code.toLowerCase().includes(q)) return true;
      return s.name.toLowerCase().includes(q);
    });

    const sorted = filtered.slice();
    sorted.sort((a, b) => {
      if (sortKey === "runway_asc") {
        const aHas = Boolean(a.schedule?.last_scheduled_date);
        const bHas = Boolean(b.schedule?.last_scheduled_date);
        const aVal = aHas ? (a.schedule?.schedule_runway_days ?? 0) : Number.POSITIVE_INFINITY;
        const bVal = bHas ? (b.schedule?.schedule_runway_days ?? 0) : Number.POSITIVE_INFINITY;
        return aVal - bVal || a.code.localeCompare(b.code);
      }
      if (sortKey === "runway_desc") {
        const aHas = Boolean(a.schedule?.last_scheduled_date);
        const bHas = Boolean(b.schedule?.last_scheduled_date);
        const aVal = aHas ? (a.schedule?.schedule_runway_days ?? 0) : -1;
        const bVal = bHas ? (b.schedule?.schedule_runway_days ?? 0) : -1;
        return bVal - aVal || a.code.localeCompare(b.code);
      }
      if (sortKey === "code") return a.code.localeCompare(b.code);
      if (sortKey === "pending_asc") return a.pending - b.pending || a.code.localeCompare(b.code);
      if (sortKey === "pending_desc") return b.pending - a.pending || a.code.localeCompare(b.code);
      if (sortKey === "ratio_asc") return a.ratio - b.ratio || a.code.localeCompare(b.code);
      if (sortKey === "ratio_desc") return b.ratio - a.ratio || a.code.localeCompare(b.code);
      return 0;
    });
    return sorted;
  }, [summaries, search, hideCompleted, sortKey]);

  return (
    <>
      {loading || error || scheduleError || youtubeError ? (
        <div className="main-status">
          {loading ? <span className="status-chip">企画CSV読み込み中…</span> : null}
          {error ? <span className="status-chip status-chip--danger">{error}</span> : null}
          {scheduleError ? <span className="status-chip status-chip--danger">{scheduleError}</span> : null}
          {youtubeError ? <span className="status-chip status-chip--danger">{youtubeError}</span> : null}
        </div>
      ) : null}

      <section className="main-content main-content--workspace">
        <div className="publishing-progress-page">
          <div className="publishing-progress-page__header">
            <div>
              <h2 className="publishing-progress-page__title">投稿進捗</h2>
              <p className="publishing-progress-page__subtitle">
                Planning CSV（workspaces/planning/channels/CHxx.csv）: 進捗=「投稿済み/公開済み」を集計。公開は YouTube Data API（公開済みのみ）で補助、公開予約は YouTube Studio のコピペ取込みで併記（わかる範囲で）。
              </p>
            </div>
            <div className="publishing-progress-page__headerActions">
              <label className="publishing-progress-page__toggle">
                <input
                  type="checkbox"
                  checked={forceYoutubeRefresh}
                  onChange={(e) => setForceYoutubeRefresh(e.target.checked)}
                />
                <span>YouTube強制更新</span>
              </label>
              <button
                type="button"
                className="workspace-button"
                onClick={() => void load({ youtubeRefresh: forceYoutubeRefresh })}
                disabled={loading}
              >
                更新
              </button>
            </div>
          </div>

          <div className="publishing-progress-page__summary">
            <span className="status-chip">チャンネル {summaries.length}</span>
            <span className="status-chip">総本数 {totals.totalVideos}</span>
            <span className="status-chip status-chip--warning">未投稿 {totals.pendingVideos}</span>
            <span className="status-chip">投稿済み {totals.postedVideos}</span>
            {scheduleTotals ? (
              <>
                <span className="status-chip">予約合計 {scheduleTotals.upcomingTotal}</span>
                <span className="status-chip">予約あり {scheduleTotals.channelsWithUpcoming}</span>
                <span className="status-chip">予約最終 {scheduleTotals.maxScheduledDate ?? "—"}</span>
              </>
            ) : null}
            {scheduleSnapshot?.parsed_at ? (
              <span className="status-chip">取込み {new Date(scheduleSnapshot.parsed_at).toLocaleString()}</span>
            ) : null}
            {youtubePublishing?.fetched_at ? (
              <span className="status-chip">YouTube更新 {new Date(youtubePublishing.fetched_at).toLocaleString()}</span>
            ) : null}
            {fetchedAt ? <span className="status-chip">更新 {new Date(fetchedAt).toLocaleString()}</span> : null}
          </div>

          <details className="publishing-progress-page__import">
            <summary>公開/公開予約 取込み（手動）</summary>
            <p className="publishing-progress-page__importHelp">
              YouTube Studio の「コンテンツ」一覧をコピーして貼り付け →「取り込む」。チャンネル見出しは <code>ch04</code> のような行が必要です。
            </p>
            <textarea
              className="publishing-progress-page__importTextarea"
              placeholder="例: memo7 の内容を貼り付け"
              value={scheduleDraft}
              onChange={(e) => setScheduleDraft(e.target.value)}
            />
            <div className="publishing-progress-page__importActions">
              <button type="button" className="workspace-button" onClick={applyScheduleDraft}>
                取り込む
              </button>
              <button type="button" className="workspace-button workspace-button--secondary" onClick={clearSchedule}>
                クリア
              </button>
              <label className="publishing-progress-page__fileButton workspace-button workspace-button--secondary">
                ファイル読み込み
                <input type="file" accept="text/*" onChange={onScheduleFileChange} />
              </label>
              {scheduleSnapshot ? (
                <span className="status-chip">取込み済み {scheduleSnapshot.channels.length}ch</span>
              ) : (
                <span className="status-chip">未取込み</span>
              )}
            </div>
          </details>

          <div className="publishing-progress-page__controls">
            <input
              className="publishing-progress-page__search"
              type="search"
              placeholder="チャンネル検索（CH06 / チャンネル名）"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />

            <label className="publishing-progress-page__toggle">
              <input
                type="checkbox"
                checked={hideCompleted}
                onChange={(e) => setHideCompleted(e.target.checked)}
              />
              <span>完了（未投稿=0）を隠す</span>
            </label>

            <label className="publishing-progress-page__selectLabel">
              <span>並び替え</span>
              <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
                <option value="runway_asc">予約最終 近い順</option>
                <option value="runway_desc">予約最終 遠い順</option>
                <option value="pending_desc">未投稿 多い順</option>
                <option value="pending_asc">未投稿 少ない順</option>
                <option value="ratio_asc">投稿率 低い順</option>
                <option value="ratio_desc">投稿率 高い順</option>
                <option value="code">CH順</option>
              </select>
            </label>
          </div>

          <div className="publishing-progress-page__table channel-progress-table">
            <table>
              <thead>
                <tr>
                  <th>Channel</th>
                  <th>投稿（公開/予約）</th>
                  <th>投稿率</th>
                  <th>未投稿</th>
                  <th>最終投稿済み</th>
                  <th>次に投稿</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr
                    key={s.code}
                    className={[
                      s.pending > 0 ? "publishing-progress-page__row--pending" : "",
                      s.schedule?.last_scheduled_date && (s.schedule?.schedule_runway_days ?? 0) <= 2
                        ? "publishing-progress-page__row--runwayCritical"
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <th>
                      <div className="publishing-progress-page__channel">
                        <div className="publishing-progress-page__channelName">{s.name}</div>
                        <div className="publishing-progress-page__channelCode">{s.code}</div>
                      </div>
                    </th>
                    <td>
                      <div className="publishing-progress-page__publishCell">
                        <div className="publishing-progress-page__publishLine">
                          <span className="publishing-progress-page__publishLabel">公開</span>
                          <span className="publishing-progress-page__publishValue">
                            {formatIsoDate(
                              youtubeByChannel[s.code]?.latest_published_date_jst ?? s.schedule?.last_published_date ?? null
                            )}
                          </span>
                        </div>
                        <div className="publishing-progress-page__publishLine">
                          <span className="publishing-progress-page__publishLabel">予約</span>
                          <span className="publishing-progress-page__publishValue">
                            {formatIsoDate(s.schedule?.last_scheduled_date ?? null)}
                          </span>
                          {s.schedule?.last_scheduled_date ? (
                            <span className="publishing-progress-page__publishMeta">
                              （{Math.max(0, s.schedule?.schedule_runway_days ?? 0)}日 / {s.schedule?.upcoming_count ?? 0}本）
                            </span>
                          ) : null}
                        </div>
                        {s.schedule?.upcoming?.length ? (
                          <details className="publishing-progress-page__upcomingDetails">
                            <summary>今後 {s.schedule.upcoming_count ?? s.schedule.upcoming.length} 本</summary>
                            <ul className="publishing-progress-page__upcomingList">
                              {s.schedule.upcoming.map((item, idx) => (
                                <li key={`${s.code}-${item.scheduled_publish_at}-${idx}`}>
                                  <span className="publishing-progress-page__upcomingAt">
                                    {formatIsoDate(item.scheduled_publish_at)}
                                  </span>
                                  <span className="publishing-progress-page__upcomingTitle">{item.title ?? "—"}</span>
                                </li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <div className="publishing-progress-page__ratioCell">
                        <div className="publishing-progress-page__bar" aria-label={`投稿率 ${formatPercent(s.ratio)}`}>
                          <div className="publishing-progress-page__barFill" style={{ width: formatPercent(s.ratio) }} />
                        </div>
                        <span className="publishing-progress-page__ratioText">{formatPercent(s.ratio)}</span>
                        <span className="publishing-progress-page__ratioSub">{s.posted} / {s.total}</span>
                      </div>
                    </td>
                    <td>
                      {s.pending > 0 ? (
                        <span className="status-badge status-badge--review">{s.pending}</span>
                      ) : (
                        <span className="status-badge status-badge--completed">完了</span>
                      )}
                    </td>
                    <td>{s.lastPostedVideo ?? "—"}</td>
                    <td>{s.nextPendingVideo ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="channel-progress-table__link"
                        onClick={() => navigate(`/planning?channel=${encodeURIComponent(s.code)}`)}
                        title="企画CSVを開く"
                      >
                        企画CSV
                      </button>
                    </td>
                  </tr>
                ))}
                {visible.length === 0 ? (
                  <tr>
                    <td colSpan={7}>
                      <span className="status-chip">該当するチャンネルがありません</span>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div className="publishing-progress-page__note">
            <span className="status-chip">
              Planning: 進捗に「投稿済み / 公開済み」を含む行を投稿済み扱い
            </span>
            <span className="status-chip">
              公開: YouTube Data API（公開済み動画のみ）で最新公開日を補助（API無効時は「—」）
            </span>
            <span className="status-chip">
              公開予約: YouTube Studio のコピペ取込みから集計（未取込みは「—」）
            </span>
            {scheduleSnapshot?.warnings?.length ? (
              <details className="publishing-progress-page__warnings">
                <summary>取込み警告 {scheduleSnapshot.warnings.length}</summary>
                <ul>
                  {scheduleSnapshot.warnings.slice(0, 50).map((w, idx) => (
                    <li key={`${idx}-${w}`}>{w}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {youtubePublishing?.warnings?.length ? (
              <details className="publishing-progress-page__warnings">
                <summary>YouTube警告 {youtubePublishing.warnings.length}</summary>
                <ul>
                  {youtubePublishing.warnings.slice(0, 50).map((w, idx) => (
                    <li key={`${idx}-${w}`}>{w}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        </div>
      </section>
    </>
  );
}
