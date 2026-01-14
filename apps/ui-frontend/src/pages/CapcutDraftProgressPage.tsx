import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { fetchPlanningRows } from "../api/client";
import type { PlanningCsvRow } from "../api/types";
import { apiUrl } from "../api/baseUrl";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./CapcutDraftProgressPage.css";

type BadgeState = "done" | "doing" | "todo" | "danger";
type DraftStatusFilter = "all" | "unstarted" | "in_progress" | "completed" | "needs_fix";

const CHANNEL_META: Record<string, { icon: string }> = {
  CH01: { icon: "🎯" },
  CH02: { icon: "📚" },
  CH03: { icon: "💡" },
  CH04: { icon: "🧭" },
  CH05: { icon: "💞" },
  CH06: { icon: "🕯️" },
  CH07: { icon: "🌿" },
  CH08: { icon: "🌙" },
  CH09: { icon: "🏛️" },
  CH10: { icon: "🧠" },
  CH11: { icon: "📜" },
};

type CapcutDraftProgressMetrics = {
  segments?: { exists?: boolean; count?: number | null } | null;
  cues?: { exists?: boolean; count?: number | null } | null;
  prompts?: { ready?: boolean; count?: number | null } | null;
  images?: { count?: number; complete?: boolean } | null;
  belt?: { exists?: boolean } | null;
  timeline_manifest?: { exists?: boolean } | null;
  auto_run_status?: string | null;
};

type CapcutDraftProgress = {
  status?: string | null;
  stage?: string | null;
  metrics?: CapcutDraftProgressMetrics | null;
};

type EpisodeProgressItem = {
  video: string;
  published_locked?: boolean | null;
  planning_progress?: string | null;
  capcut_draft_status?: string | null;
  capcut_draft_run_id?: string | null;
  capcut_draft_progress?: CapcutDraftProgress | null;
};

type EpisodeProgressResponse = {
  channel: string;
  episodes: EpisodeProgressItem[];
};

function normalizeChannelCode(value: string | null | undefined): string {
  const raw = (value ?? "").trim().toUpperCase();
  return raw;
}

function normalizeVideo(value: string | null | undefined): string {
  const raw = (value ?? "").trim();
  const digits = raw.replace(/\D/g, "");
  if (!digits) return "";
  return String(Number.parseInt(digits, 10)).padStart(3, "0");
}

function isPostedProgress(value: string | null | undefined): boolean {
  const text = String(value ?? "").trim();
  if (!text) return false;
  if (text.includes("投稿済み") || text.includes("公開済み") || text.includes("投稿完了")) return true;
  const lower = text.toLowerCase();
  return lower === "published" || lower === "posted";
}

async function fetchEpisodeProgress(channelCode: string): Promise<EpisodeProgressResponse> {
  const ch = normalizeChannelCode(channelCode);
  if (!ch) {
    return { channel: "", episodes: [] };
  }
  const response = await fetch(apiUrl(`/api/channels/${encodeURIComponent(ch)}/episode-progress`), {
    method: "GET",
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || "episode-progress の取得に失敗しました");
  }
  return (await response.json()) as EpisodeProgressResponse;
}

function labelForDraftStatus(statusRaw: string): { label: string; badge: BadgeState } {
  const status = (statusRaw ?? "").trim().toLowerCase();
  if (status === "completed") return { label: "完了", badge: "done" };
  if (status === "in_progress") return { label: "作成中", badge: "doing" };
  if (status === "unstarted") return { label: "未着手", badge: "todo" };
  if (status === "broken") return { label: "LINK切れ", badge: "danger" };
  if (status === "failed") return { label: "失敗", badge: "danger" };
  return { label: statusRaw || "—", badge: status ? "doing" : "todo" };
}

function safeNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function badgeForStep(status: BadgeState): string {
  return `capcut-draft-progress-page__badge capcut-draft-progress-page__badge--${status}`;
}

type DraftRow = {
  video: string;
  title: string;
  progress: string;
  posted: boolean;
  runId: string;
  capcutDraftStatus: string;
  capcut: CapcutDraftProgress | null;
};

type ChannelDraftOverview = {
  channel: string;
  total: number;
  posted: number;
  backlog: number;
  unstarted: number;
  in_progress: number;
  completed: number;
  broken: number;
  failed: number;
  needs_fix: number;
  error?: string | null;
};

function normalizeDraftStatus(value: unknown): "unstarted" | "in_progress" | "completed" | "broken" | "failed" {
  const raw = String(value ?? "").trim().toLowerCase();
  if (raw === "completed") return "completed";
  if (raw === "broken") return "broken";
  if (raw === "failed") return "failed";
  if (raw === "unstarted") return "unstarted";
  if (raw === "in_progress") return "in_progress";
  if (raw === "missing") return "unstarted";
  return "in_progress";
}

function buildChannelDraftOverview(channel: string, episodes: EpisodeProgressItem[]): ChannelDraftOverview {
  const overview: ChannelDraftOverview = {
    channel,
    total: 0,
    posted: 0,
    backlog: 0,
    unstarted: 0,
    in_progress: 0,
    completed: 0,
    broken: 0,
    failed: 0,
    needs_fix: 0,
    error: null,
  };

  overview.total = episodes.length;
  episodes.forEach((ep) => {
    const posted = Boolean(ep?.published_locked);
    if (posted) {
      overview.posted += 1;
      return;
    }
    overview.backlog += 1;
    const status = normalizeDraftStatus(ep?.capcut_draft_progress?.status);
    overview[status] += 1;
  });

  overview.needs_fix = overview.broken + overview.failed;
  return overview;
}

async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  mapper: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  if (!items.length) return [];
  const concurrency = Math.max(1, Math.min(limit, items.length));
  const results: R[] = new Array(items.length);
  let cursor = 0;
  await Promise.all(
    Array.from({ length: concurrency }).map(async () => {
      while (cursor < items.length) {
        const idx = cursor;
        cursor += 1;
        results[idx] = await mapper(items[idx], idx);
      }
    })
  );
  return results;
}

export function CapcutDraftProgressPage() {
  const { channels, selectedChannel } = useOutletContext<ShellOutletContext>();

  const channelMap = useMemo(() => {
    const map: Record<string, { name?: string | null; avatar?: string | null }> = {};
    (channels ?? []).forEach((item) => {
      const code = String(item.code || "").trim().toUpperCase();
      if (!code) return;
      map[code] = {
        name: item.name ?? null,
        avatar: item.branding?.avatar_url ?? null,
      };
    });
    return map;
  }, [channels]);

  const channelCodes = useMemo(() => {
    const codes = (channels ?? [])
      .map((c) => c.code)
      .filter((c): c is string => typeof c === "string" && c.trim().length > 0)
      .map((c) => c.trim().toUpperCase());
    return Array.from(new Set(codes)).sort((a, b) => a.localeCompare(b));
  }, [channels]);

  const initialChannel = useMemo(() => {
    const stored = typeof window !== "undefined" ? window.localStorage.getItem("ui.channel.selected") : null;
    return normalizeChannelCode(selectedChannel) || normalizeChannelCode(stored) || channelCodes[0] || "";
  }, [channelCodes, selectedChannel]);

  const [channel, setChannel] = useState<string>(initialChannel);
  const [statusFilter, setStatusFilter] = useState<DraftStatusFilter>("all");
  const [unpublishedOnly, setUnpublishedOnly] = useState<boolean>(false);
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [overviewMap, setOverviewMap] = useState<Record<string, ChannelDraftOverview>>({});
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const tableWrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setChannel((current) => {
      if (initialChannel && initialChannel !== current) {
        return initialChannel;
      }
      return current;
    });
  }, [initialChannel]);

  useEffect(() => {
    if (!channelCodes.length) {
      setOverviewMap({});
      return;
    }
    let cancelled = false;
    setOverviewLoading(true);
    setOverviewError(null);
    void (async () => {
      try {
        const results = await mapWithConcurrency(channelCodes, 4, async (code) => {
          try {
            const data = await fetchEpisodeProgress(code);
            return buildChannelDraftOverview(code, data.episodes ?? []);
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err || "");
            return {
              channel: code,
              total: 0,
              posted: 0,
              backlog: 0,
              unstarted: 0,
              in_progress: 0,
              completed: 0,
              broken: 0,
              failed: 0,
              needs_fix: 0,
              error: message || "取得に失敗しました",
            } satisfies ChannelDraftOverview;
          }
        });
        if (cancelled) return;
        const map: Record<string, ChannelDraftOverview> = {};
        results.forEach((item) => {
          map[item.channel] = item;
        });
        setOverviewMap(map);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err || "");
        setOverviewError(message || "全チャンネル集計に失敗しました");
      } finally {
        if (cancelled) return;
        setOverviewLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [channelCodes]);

  useEffect(() => {
    const ch = normalizeChannelCode(channel);
    if (!ch) {
      setRows([]);
      return;
    }
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const [planning, progress] = await Promise.all([fetchPlanningRows(ch), fetchEpisodeProgress(ch)]);
        const progressMap: Record<string, EpisodeProgressItem> = {};
        (progress.episodes ?? []).forEach((ep) => {
          const vid = normalizeVideo(ep.video);
          if (!vid) return;
          progressMap[vid] = ep;
        });

        const merged: DraftRow[] = (planning ?? [])
          .map((row: PlanningCsvRow) => {
            const video = normalizeVideo(row.video_number);
            const item = video ? progressMap[video] : undefined;
            const capcut = item?.capcut_draft_progress ?? null;
            const runId = String(item?.capcut_draft_run_id || "").trim();
            const capcutDraftStatus = String(item?.capcut_draft_status || "").trim();
            const progressText = String((row.progress ?? "") || (item?.planning_progress ?? "") || "").trim();
            const posted = Boolean(item?.published_locked) || isPostedProgress(progressText);
            return {
              video,
              title: String(row.title || "").trim(),
              progress: progressText,
              posted,
              runId,
              capcutDraftStatus,
              capcut,
            };
          })
          .filter((row) => Boolean(row.video));

        merged.sort((a, b) => a.video.localeCompare(b.video));
        setRows(merged);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err || "");
        setError(message || "取得に失敗しました");
        setRows([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [channel]);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    const filter = statusFilter;
    return rows.filter((row) => {
      const capcutStatusRaw = String(row.capcut?.status || "").trim().toLowerCase();
      const effectiveStatus = capcutStatusRaw || "unstarted";
      const needsFix = effectiveStatus === "broken" || effectiveStatus === "failed" || row.capcutDraftStatus.toLowerCase() === "broken";

      if (unpublishedOnly && row.posted) return false;
      if (filter === "unstarted" && effectiveStatus !== "unstarted") return false;
      if (filter === "in_progress" && effectiveStatus !== "in_progress") return false;
      if (filter === "completed" && effectiveStatus !== "completed") return false;
      if (filter === "needs_fix" && !needsFix) return false;

      if (!needle) return true;
      return (
        row.video.toLowerCase().includes(needle) ||
        row.title.toLowerCase().includes(needle) ||
        row.progress.toLowerCase().includes(needle) ||
        row.runId.toLowerCase().includes(needle)
      );
    });
  }, [keyword, rows, statusFilter, unpublishedOnly]);

  const summary = useMemo(() => {
    const counts: Record<string, number> = {
      unstarted: 0,
      in_progress: 0,
      completed: 0,
      broken: 0,
      failed: 0,
      posted: 0,
    };
    rows.forEach((row) => {
      const status = String(row.capcut?.status || "unstarted").trim().toLowerCase();
      counts[status] = (counts[status] || 0) + 1;
      if (row.posted) counts.posted += 1;
    });
    return counts;
  }, [rows]);

  const handleChannelChange = (nextRaw: string, options?: { scrollToDetail?: boolean }) => {
    const next = normalizeChannelCode(nextRaw);
    setChannel(next);
    if (next) {
      try {
        window.localStorage.setItem("ui.channel.selected", next);
      } catch {}
    }
    if (options?.scrollToDetail) {
      requestAnimationFrame(() => {
        tableWrapperRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  };

  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">進捗管理</p>
          <h1>CapCutドラフト進捗</h1>
          <p className="page-lead">「未着手 / 作成中 / 完了」をステップ別に見える化し、チャンネル単位で迷子を減らします。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit/production">
            プロジェクト管理へ
          </Link>
          <Link className="button" to="/capcut-edit/draft">
            新規ドラフト作成へ
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="capcut-draft-progress-page">
          <div className="capcut-draft-progress-page__controls">
            <div className="capcut-draft-progress-page__channel-icons" aria-label="チャンネル切替">
              {channelCodes.map((code) => {
                const icon = CHANNEL_META[code]?.icon ?? "📺";
                const name = channelMap[code]?.name ? String(channelMap[code]?.name) : "";
                const title = name ? `${code} / ${name}` : code;
                const avatar = channelMap[code]?.avatar ? String(channelMap[code]?.avatar) : "";
                return (
                  <button
                    key={code}
                    type="button"
                    className={`capcut-draft-progress-page__chip ${channel === code ? "is-active" : ""}`}
                    onClick={() => handleChannelChange(code)}
                    title={title}
                    aria-label={title}
                    aria-pressed={channel === code}
                  >
                    {avatar ? (
                      <img src={avatar} alt={code} className="capcut-draft-progress-page__chip-avatar" />
                    ) : (
                      <span className="capcut-draft-progress-page__chip-icon" aria-hidden="true">
                        {icon}
                      </span>
                    )}
                    <span className="capcut-draft-progress-page__chip-text">{code}</span>
                  </button>
                );
              })}
            </div>

            <label>
              状態
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as DraftStatusFilter)}>
                <option value="all">すべて</option>
                <option value="unstarted">未着手</option>
                <option value="in_progress">作成中</option>
                <option value="completed">完了</option>
                <option value="needs_fix">LINK切れ/失敗</option>
              </select>
            </label>

            <label
              className="capcut-draft-progress-page__toggle"
              title="投稿済み/公開済み/投稿完了（ロック）を除外します"
            >
              <input type="checkbox" checked={unpublishedOnly} onChange={(e) => setUnpublishedOnly(e.target.checked)} />
              未投稿のみ
            </label>

            <label>
              検索
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="動画番号 / タイトル / run_id / 進捗" />
            </label>

            <span className="status-chip" title="集計は episode-progress (derived view) + planning CSV を参照">
              未着手 {summary.unstarted} / 作成中 {summary.in_progress} / 完了 {summary.completed} / LINK切れ {summary.broken} / 失敗 {summary.failed} / 投稿済み{" "}
              {summary.posted}
            </span>

            {loading ? <span className="capcut-draft-progress-page__status">読み込み中...</span> : null}
            {error ? <span className="capcut-draft-progress-page__error">{error}</span> : null}
          </div>

          <div className="capcut-draft-progress-page__overview">
            <div className="capcut-draft-progress-page__overview-head">
              <span className="status-chip">全チャンネル概要</span>
              {overviewLoading ? <span className="status-chip">集計中...</span> : null}
              {overviewError ? <span className="status-chip status-chip--danger">{overviewError}</span> : null}
            </div>
            <div className="capcut-draft-progress-page__overview-grid">
              {channelCodes.map((code) => {
                const item = overviewMap[code];
                const name = channelMap[code]?.name ? String(channelMap[code]?.name) : "";
                const avatar = channelMap[code]?.avatar ? String(channelMap[code]?.avatar) : "";
                const icon = CHANNEL_META[code]?.icon ?? "📺";
                const hasData = Boolean(item);
                const backlog = hasData ? item!.backlog : null;
                const done = hasData ? item!.completed : null;
                const inProg = hasData ? item!.in_progress : null;
                const unstarted = hasData ? item!.unstarted : null;
                const needsFix = hasData ? item!.needs_fix : null;
                const broken = hasData ? item!.broken : null;
                const failed = hasData ? item!.failed : null;
                const posted = hasData ? item!.posted : null;
                const total = hasData ? item!.total : null;
                const pct = backlog === null ? 0 : backlog > 0 ? Math.round(((done ?? 0) / backlog) * 100) : 100;
                const hasError = Boolean(item?.error);
                const danger = (needsFix ?? 0) > 0;
                const cls = `capcut-draft-progress-page__overview-card${code === channel ? " is-active" : ""}${danger ? " is-danger" : ""}`;
                const title = name ? `${code} / ${name}` : code;

                return (
                  <button
                    key={code}
                    type="button"
                    className={cls}
                    onClick={() => handleChannelChange(code, { scrollToDetail: true })}
                    title={title}
                    aria-label={title}
                  >
                    <div className="capcut-draft-progress-page__overview-top">
                      <div className="capcut-draft-progress-page__overview-left">
                        {avatar ? (
                          <img src={avatar} alt={code} className="capcut-draft-progress-page__overview-avatar" />
                        ) : (
                          <span className="capcut-draft-progress-page__overview-icon" aria-hidden="true">
                            {icon}
                          </span>
                        )}
                        <div className="capcut-draft-progress-page__overview-text">
                          <div className="capcut-draft-progress-page__overview-code">{code}</div>
                          {name ? <div className="capcut-draft-progress-page__overview-name">{name}</div> : null}
                        </div>
                      </div>

                      <div className="capcut-draft-progress-page__overview-right">
                        <div className="capcut-draft-progress-page__overview-backlog-label">未投稿</div>
                        <div className="capcut-draft-progress-page__overview-backlog-value">{backlog ?? "—"}</div>
                      </div>
                    </div>

                    {hasError ? (
                      <div className="capcut-draft-progress-page__overview-error">{item?.error}</div>
                    ) : (
                      <>
                        <div className="capcut-draft-progress-page__overview-bar" title={`完了率 ${pct}%`}>
                          <div className="capcut-draft-progress-page__overview-bar-fill" style={{ width: `${pct}%` }} />
                        </div>
                        <div className="capcut-draft-progress-page__overview-badges">
                          <span className={`${badgeForStep("done")} capcut-draft-progress-page__badge--mini`}>完了 {done ?? "—"}</span>
                          <span className={`${badgeForStep("doing")} capcut-draft-progress-page__badge--mini`}>作成中 {inProg ?? "—"}</span>
                          <span className={`${badgeForStep("todo")} capcut-draft-progress-page__badge--mini`}>未着手 {unstarted ?? "—"}</span>
                          {(broken ?? 0) > 0 ? (
                            <span
                              className={`${badgeForStep("danger")} capcut-draft-progress-page__badge--mini`}
                              title="CapCutドラフト: LINK切れ"
                            >
                              LINK切れ {broken}
                            </span>
                          ) : null}
                          {(failed ?? 0) > 0 ? (
                            <span
                              className={`${badgeForStep("danger")} capcut-draft-progress-page__badge--mini`}
                              title="CapCutドラフト: 自動生成失敗"
                            >
                              失敗 {failed}
                            </span>
                          ) : null}
                        </div>
                        <div className="capcut-draft-progress-page__overview-footer">
                          <span>投稿済 {posted ?? "—"}</span>
                          <span>総数 {total ?? "—"}</span>
                        </div>
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="capcut-draft-progress-page__table-wrapper" ref={tableWrapperRef}>
            <table className="capcut-draft-progress-page__table">
              <thead>
                <tr>
                  <th className="capcut-draft-progress-page__col-video">動画</th>
                  <th>タイトル</th>
                  <th>状態</th>
                  <th title="SRT解析 → キュー分割 → 画像プロンプト → 画像生成 → CapCutドラフト">ステップ</th>
                  <th title="ドラフト（run_id）をクリックすると、そのドラフトで使われている画像を表示します">ドラフト</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row) => {
                  const metrics = row.capcut?.metrics ?? null;
                  const segmentsExists = Boolean(metrics?.segments?.exists);
                  const cuesExists = Boolean(metrics?.cues?.exists);
                  const cueCount = safeNumber(metrics?.cues?.count);
                  const promptReady = Boolean(metrics?.prompts?.ready);
                  const promptCount = safeNumber(metrics?.prompts?.count);
                  const imagesCount = safeNumber(metrics?.images?.count) ?? 0;
                  const imagesComplete = Boolean(metrics?.images?.complete);
                  const autoRunStatus = metrics?.auto_run_status ? String(metrics.auto_run_status) : "";

                  const draftStatus = labelForDraftStatus(String(row.capcut?.status || ""));
                  const capcutStatusNorm = row.capcutDraftStatus.trim().toLowerCase();

                  const srtBadge: BadgeState = segmentsExists ? "done" : "todo";
                  const cueBadge: BadgeState = cuesExists ? "done" : segmentsExists ? "doing" : "todo";
                  const promptBadge: BadgeState =
                    cueCount && promptCount !== null && promptCount >= cueCount ? "done" : promptCount && promptCount > 0 ? "doing" : cuesExists ? "doing" : "todo";
                  const imagesBadge: BadgeState = imagesComplete ? "done" : imagesCount > 0 ? "doing" : promptReady ? "doing" : "todo";

                  const capcutStepBadge: BadgeState =
                    capcutStatusNorm === "ok"
                      ? "done"
                      : capcutStatusNorm === "broken" || autoRunStatus.toLowerCase() === "failed"
                        ? "danger"
                        : segmentsExists || cuesExists || promptCount || imagesCount > 0
                          ? "doing"
                          : "todo";
                  const capcutStepLabel =
                    capcutStatusNorm === "ok"
                      ? "CapCut 完了"
                      : capcutStatusNorm === "broken"
                        ? "CapCut LINK切れ"
                        : autoRunStatus.toLowerCase() === "failed"
                          ? "CapCut 失敗"
                          : capcutStepBadge === "doing"
                            ? "CapCut 作成中"
                            : "CapCut 未生成";

                  const imageLink = row.runId ? `/image-timeline?project=${encodeURIComponent(row.runId)}` : "";
                  const capcutLink = row.runId
                    ? `/capcut-edit/production?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(row.video)}&project=${encodeURIComponent(row.runId)}`
                    : `/capcut-edit/draft?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(row.video)}`;

                  return (
                    <tr
                      key={row.video}
                      className={`capcut-draft-progress-page__row${row.posted ? " capcut-draft-progress-page__row--posted" : ""}`}
                    >
                      <td className="capcut-draft-progress-page__video">{row.video}</td>
                      <td className="capcut-draft-progress-page__title" title={row.title || ""}>
                        {row.title || "—"}
                      </td>
                      <td>
                        <div className="capcut-draft-progress-page__status-cell" title={row.progress || ""}>
                          {row.posted ? (
                            <span className="capcut-draft-progress-page__badge capcut-draft-progress-page__badge--posted">投稿済み</span>
                          ) : null}
                          <span className={badgeForStep(draftStatus.badge)}>{draftStatus.label}</span>
                        </div>
                      </td>
                      <td>
                        <div className="capcut-draft-progress-page__steps">
                          <span className={badgeForStep(srtBadge)} title={segmentsExists ? "SRT解析/チャンク: OK" : "SRT解析/チャンク: 未着手"}>
                            SRT解析
                          </span>
                          <span
                            className={badgeForStep(cueBadge)}
                            title={
                              cuesExists
                                ? `キュー分割: cues=${cueCount ?? "?"}`
                                : segmentsExists
                                  ? "キュー分割: SRT解析済み（次にcues生成）"
                                  : "キュー分割: 未着手"
                            }
                          >
                            {cuesExists ? `キュー ${cueCount ?? "?"}` : "キュー —"}
                          </span>
                          <span
                            className={badgeForStep(promptBadge)}
                            title={
                              cuesExists
                                ? `画像プロンプト: prompts=${promptCount ?? 0}/${cueCount ?? "?"}`
                                : "画像プロンプト: 未着手"
                            }
                          >
                            {cuesExists ? `プロンプト ${promptCount ?? 0}/${cueCount ?? "?"}` : "プロンプト —"}
                          </span>
                          <span
                            className={badgeForStep(imagesBadge)}
                            title={
                              cuesExists
                                ? `画像生成: images=${imagesCount}/${cueCount ?? "?"}${imagesComplete ? " (complete)" : ""}`
                                : "画像生成: 未着手"
                            }
                          >
                            {cuesExists ? `画像 ${imagesCount}/${cueCount ?? "?"}` : "画像 —"}
                          </span>
                          <span
                            className={badgeForStep(capcutStepBadge)}
                            title={
                              capcutStatusNorm === "ok"
                                ? "CapCutドラフト: OK"
                                : capcutStatusNorm === "broken"
                                  ? "CapCutドラフト: LINK切れ"
                                  : autoRunStatus
                                    ? `CapCutドラフト: ${autoRunStatus}`
                                    : "CapCutドラフト: 未生成"
                            }
                          >
                            {capcutStepLabel}
                          </span>
                        </div>
                      </td>
                      <td>
                        {row.runId ? (
                          <div className="capcut-draft-progress-page__run-actions">
                            <Link className="capcut-draft-progress-page__link" to={imageLink} title="このドラフトで使われている画像を見る">
                              <span className="capcut-draft-progress-page__run">{row.runId}</span>
                            </Link>
                            <Link className="capcut-draft-progress-page__run-secondary" to={capcutLink} title="CapCutプロジェクト（production）へ">
                              CapCut
                            </Link>
                          </div>
                        ) : (
                          <Link className="capcut-draft-progress-page__link" to={capcutLink} title="新規ドラフト作成へ">
                            <span className="capcut-draft-progress-page__run">作成へ</span>
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {!filtered.length ? (
                  <tr>
                    <td colSpan={5} style={{ padding: 12, color: "#64748b" }}>
                      該当データがありません。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}
