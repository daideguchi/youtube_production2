import { useEffect, useMemo, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { fetchPlanningRows } from "../api/client";
import type { PlanningCsvRow } from "../api/types";
import { apiUrl } from "../api/baseUrl";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./CapcutDraftProgressPage.css";

type BadgeState = "done" | "doing" | "todo" | "danger";
type DraftStatusFilter = "all" | "unstarted" | "in_progress" | "completed" | "needs_fix";

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
  runId: string;
  capcutDraftStatus: string;
  capcut: CapcutDraftProgress | null;
};

export function CapcutDraftProgressPage() {
  const { channels, selectedChannel, selectChannel } = useOutletContext<ShellOutletContext>();

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
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<DraftRow[]>([]);

  useEffect(() => {
    setChannel((current) => {
      if (initialChannel && initialChannel !== current) {
        return initialChannel;
      }
      return current;
    });
  }, [initialChannel]);

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
            return {
              video,
              title: String(row.title || "").trim(),
              progress: String(row.progress || "").trim(),
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
  }, [keyword, rows, statusFilter]);

  const summary = useMemo(() => {
    const counts: Record<string, number> = { unstarted: 0, in_progress: 0, completed: 0, broken: 0, failed: 0 };
    rows.forEach((row) => {
      const status = String(row.capcut?.status || "unstarted").trim().toLowerCase();
      counts[status] = (counts[status] || 0) + 1;
    });
    return counts;
  }, [rows]);

  const handleChannelChange = (nextRaw: string) => {
    const next = normalizeChannelCode(nextRaw);
    setChannel(next);
    if (next) {
      try {
        window.localStorage.setItem("ui.channel.selected", next);
      } catch {}
      selectChannel(next);
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
            <label>
              チャンネル
              <select value={channel} onChange={(e) => handleChannelChange(e.target.value)}>
                {channelCodes.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>

            <label>
              状態
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as DraftStatusFilter)}>
                <option value="all">すべて</option>
                <option value="unstarted">未着手</option>
                <option value="in_progress">作成中</option>
                <option value="completed">完了</option>
                <option value="needs_fix">要修復</option>
              </select>
            </label>

            <label>
              検索
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="動画番号 / タイトル / run_id / 進捗" />
            </label>

            <span className="status-chip" title="集計は episode-progress (derived view) + planning CSV を参照">
              未着手 {summary.unstarted} / 作成中 {summary.in_progress} / 完了 {summary.completed} / LINK切れ {summary.broken} / 失敗 {summary.failed}
            </span>

            {loading ? <span className="capcut-draft-progress-page__status">読み込み中...</span> : null}
            {error ? <span className="capcut-draft-progress-page__error">{error}</span> : null}
          </div>

          <div className="capcut-draft-progress-page__table-wrapper">
            <table className="capcut-draft-progress-page__table">
              <thead>
                <tr>
                  <th>動画</th>
                  <th>タイトル</th>
                  <th>進捗</th>
                  <th>ドラフト状態</th>
                  <th>キュー分割</th>
                  <th>画像プロンプト</th>
                  <th>画像生成</th>
                  <th>CapCutドラフト</th>
                  <th>run</th>
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

                  const draftStatus = labelForDraftStatus(String(row.capcut?.status || ""));
                  const capcutStatusNorm = row.capcutDraftStatus.trim().toLowerCase();
                  const capcutBadge =
                    capcutStatusNorm === "ok"
                      ? { label: "完了", badge: "done" as const }
                      : capcutStatusNorm === "broken"
                        ? { label: "LINK切れ", badge: "danger" as const }
                        : capcutStatusNorm === "missing"
                          ? { label: segmentsExists || cuesExists || promptReady || imagesCount > 0 ? "作成中" : "未生成", badge: "doing" as const }
                          : { label: row.capcutDraftStatus || "—", badge: "todo" as const };

                  const cueBadge: BadgeState = cuesExists ? "done" : segmentsExists ? "doing" : "todo";
                  const promptBadge: BadgeState = promptReady ? "done" : cuesExists ? "doing" : "todo";
                  const imagesBadge: BadgeState = imagesComplete ? "done" : imagesCount > 0 ? "doing" : promptReady ? "doing" : "todo";

                  const runLink = row.runId
                    ? `/capcut-edit/production?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(row.video)}&project=${encodeURIComponent(row.runId)}`
                    : `/capcut-edit/draft?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(row.video)}`;

                  return (
                    <tr key={row.video}>
                      <td>{row.video}</td>
                      <td className="capcut-draft-progress-page__title" title={row.title || ""}>
                        {row.title || "—"}
                      </td>
                      <td title={row.progress || ""}>{row.progress || "—"}</td>
                      <td>
                        <span className={badgeForStep(draftStatus.badge)}>{draftStatus.label}</span>
                      </td>
                      <td>
                        <span className={badgeForStep(cueBadge)}>{cuesExists ? `${cueCount ?? "?"} cues` : "—"}</span>
                      </td>
                      <td>
                        <span className={badgeForStep(promptBadge)}>
                          {promptReady ? `${promptCount ?? "?"}/${cueCount ?? "?"}` : cuesExists ? "準備中" : "—"}
                        </span>
                      </td>
                      <td>
                        <span className={badgeForStep(imagesBadge)}>
                          {imagesCount ? `${imagesCount}/${cueCount ?? "?"}` : promptReady ? "待ち" : "—"}
                        </span>
                      </td>
                      <td>
                        <span className={badgeForStep(capcutBadge.badge)}>{capcutBadge.label}</span>
                      </td>
                      <td>
                        <Link className="capcut-draft-progress-page__link" to={runLink} title={row.runId || ""}>
                          {row.runId ? row.runId : "作成へ"}
                        </Link>
                      </td>
                    </tr>
                  );
                })}
                {!filtered.length ? (
                  <tr>
                    <td colSpan={9} style={{ padding: 12, color: "#64748b" }}>
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
