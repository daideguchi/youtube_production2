import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { fetchPlanningRows } from "../api/client";
import type { PlanningCsvRow } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./PublishingProgressPage.css";

type SortKey = "pending_desc" | "pending_asc" | "ratio_asc" | "ratio_desc" | "code";

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

  const [search, setSearch] = useState("");
  const [hideCompleted, setHideCompleted] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("pending_desc");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPlanningRows();
      setRows(data ?? []);
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
      });
    }
    return out;
  }, [rows, channelNameByCode]);

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
      {loading || error ? (
        <div className="main-status">
          {loading ? <span className="status-chip">企画CSV読み込み中…</span> : null}
          {error ? <span className="status-chip status-chip--danger">{error}</span> : null}
        </div>
      ) : null}

      <section className="main-content main-content--workspace">
        <div className="publishing-progress-page">
          <div className="publishing-progress-page__header">
            <div>
              <h2 className="publishing-progress-page__title">投稿進捗</h2>
              <p className="publishing-progress-page__subtitle">
                Planning CSV（workspaces/planning/channels/CHxx.csv）から、進捗=「投稿済み/公開済み」を集計します。
              </p>
            </div>
            <div className="publishing-progress-page__headerActions">
              <button type="button" className="workspace-button" onClick={load} disabled={loading}>
                更新
              </button>
            </div>
          </div>

          <div className="publishing-progress-page__summary">
            <span className="status-chip">チャンネル {summaries.length}</span>
            <span className="status-chip">総本数 {totals.totalVideos}</span>
            <span className="status-chip status-chip--warning">未投稿 {totals.pendingVideos}</span>
            <span className="status-chip">投稿済み {totals.postedVideos}</span>
            {fetchedAt ? <span className="status-chip">更新 {new Date(fetchedAt).toLocaleString()}</span> : null}
          </div>

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
                  <th>投稿率</th>
                  <th>投稿済み</th>
                  <th>未投稿</th>
                  <th>最終投稿済み</th>
                  <th>次に投稿</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr key={s.code} className={s.pending > 0 ? "publishing-progress-page__row--pending" : ""}>
                    <th>
                      <div className="publishing-progress-page__channel">
                        <div className="publishing-progress-page__channelName">{s.name}</div>
                        <div className="publishing-progress-page__channelCode">{s.code}</div>
                      </div>
                    </th>
                    <td>
                      <div className="publishing-progress-page__ratioCell">
                        <div className="publishing-progress-page__bar" aria-label={`投稿率 ${formatPercent(s.ratio)}`}>
                          <div
                            className="publishing-progress-page__barFill"
                            style={{ width: formatPercent(s.ratio) }}
                          />
                        </div>
                        <span className="publishing-progress-page__ratioText">{formatPercent(s.ratio)}</span>
                      </div>
                    </td>
                    <td>{s.posted} / {s.total}</td>
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
              ルール: 進捗に「投稿済み / 公開済み」を含む行を投稿済み扱い（YouTube投入済み=予約含む）
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
