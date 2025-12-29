import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";
import { fetchVideos } from "../api/client";
import type { VideoSummary } from "../api/types";

type ChannelLoadResult = {
  channel: string;
  videos: VideoSummary[];
  error: string | null;
};

const COMPLETED_STATUSES = new Set(["completed", "skipped"]);
const DONE_STAGE_STATUSES = new Set(["completed", "skipped", "done"]);
const SCRIPT_STAGE_KEYS = [
  "script_polish_ai",
  "script_validation",
  "script_review",
  "script_enhancement",
  "script_draft",
  "script_outline",
];

function normalizeChannel(value: string | null): string | null {
  const trimmed = (value ?? "").trim().toUpperCase();
  if (!trimmed || trimmed === "ALL") return null;
  return trimmed;
}

function toBoolParam(value: string | null): boolean {
  return value === "1" || value === "true";
}

function stageStatus(video: VideoSummary, key: string): string {
  const status = video.stages?.[key];
  return typeof status === "string" && status.trim() ? status : "pending";
}

function isDone(status: string | null | undefined): boolean {
  if (!status) return false;
  return DONE_STAGE_STATUSES.has(status);
}

async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  mapper: (item: T) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let nextIndex = 0;
  const workerCount = Math.max(1, Math.min(limit, items.length));
  const workers = Array.from({ length: workerCount }, async () => {
    while (true) {
      const idx = nextIndex;
      nextIndex += 1;
      if (idx >= items.length) return;
      results[idx] = await mapper(items[idx]);
    }
  });
  await Promise.all(workers);
  return results;
}

export function ReportsPage() {
  const { channels, channelsLoading, channelsError, selectedChannel } = useOutletContext<ShellOutletContext>();
  const [searchParams, setSearchParams] = useSearchParams();
  const channelParam = normalizeChannel(searchParams.get("channel")) ?? selectedChannel ?? null;
  const keyword = (searchParams.get("q") ?? "").trim();
  const hideCompleted = toBoolParam(searchParams.get("hide_completed"));

  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<ChannelLoadResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  const applyParams = useCallback(
    (next: { channel?: string | null; q?: string; hide_completed?: boolean }) => {
      const params = new URLSearchParams(searchParams);
      const normalizedChannel = normalizeChannel(next.channel ?? channelParam);
      const nextKeyword = (next.q ?? keyword).trim();
      const nextHideCompleted = next.hide_completed ?? hideCompleted;

      if (normalizedChannel) {
        params.set("channel", normalizedChannel);
      } else {
        params.delete("channel");
      }

      if (nextKeyword) {
        params.set("q", nextKeyword);
      } else {
        params.delete("q");
      }

      if (nextHideCompleted) {
        params.set("hide_completed", "1");
      } else {
        params.delete("hide_completed");
      }

      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams, channelParam, keyword, hideCompleted]
  );

  const load = useCallback(async () => {
    if (channelsLoading) return;
    if (channelsError) {
      setError(channelsError);
      return;
    }
    if (!channels.length) {
      setError("チャンネル一覧が空です。");
      return;
    }

    const targetChannels = channelParam ? [channelParam] : channels.map((c) => c.code);
    setLoading(true);
    setError(null);
    setResults([]);
    try {
      const loaded = await mapWithConcurrency(targetChannels, 4, async (channel) => {
        try {
          const videos = await fetchVideos(channel);
          return { channel, videos, error: null } satisfies ChannelLoadResult;
        } catch (err: any) {
          return { channel, videos: [], error: err?.message || "failed to load" } satisfies ChannelLoadResult;
        }
      });
      setResults(loaded);
    } catch (err: any) {
      setError(err?.message || "failed to load");
    } finally {
      setLoading(false);
    }
  }, [channels, channelsLoading, channelsError, channelParam]);

  useEffect(() => {
    if (channelsLoading) return;
    void load();
  }, [channelsLoading, channelParam, reloadKey, load]);

  const channelNameMap = useMemo(() => {
    const map = new Map<string, string>();
    channels.forEach((channel) => {
      const name = channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
      map.set(channel.code, name);
    });
    return map;
  }, [channels]);

  const allVideos = useMemo(() => {
    const out: Array<VideoSummary & { channel: string; channel_title: string }> = [];
    results.forEach((res) => {
      const title = channelNameMap.get(res.channel) ?? res.channel;
      res.videos.forEach((video) => out.push({ ...video, channel: res.channel, channel_title: title }));
    });
    return out;
  }, [results, channelNameMap]);

  const filteredVideos = useMemo(() => {
    const q = keyword.toLowerCase();
    return allVideos.filter((video) => {
      if (hideCompleted && COMPLETED_STATUSES.has(video.status)) return false;
      if (!q) return true;
      const hay = `${video.channel} ${video.video} ${video.title ?? ""} ${video.script_id ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [allVideos, keyword, hideCompleted]);

  const summary = useMemo(() => {
    let total = 0;
    let completed = 0;
    let scriptDone = 0;
    let audioDone = 0;
    let srtDone = 0;
    let readyForAudio = 0;
    filteredVideos.forEach((video) => {
      total += 1;
      if (COMPLETED_STATUSES.has(video.status)) completed += 1;
      const scriptOk = SCRIPT_STAGE_KEYS.some((key) => isDone(stageStatus(video, key)));
      if (scriptOk) scriptDone += 1;
      if (isDone(stageStatus(video, "audio_synthesis"))) audioDone += 1;
      if (isDone(stageStatus(video, "srt_generation"))) srtDone += 1;
      if (video.ready_for_audio) readyForAudio += 1;
    });
    return { total, completed, scriptDone, audioDone, srtDone, readyForAudio };
  }, [filteredVideos]);

  return (
    <div className="page reports-page" style={{ padding: 16, display: "grid", gap: 12 }}>
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">Reports</p>
          <h1>進捗一覧（全チャンネル）</h1>
          <p className="page-lead">チャンネル横断で、案件（動画）ごとの進捗をざっくり確認します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/dashboard">
            ← ダッシュボード
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>フィルタ</h2>
          <p className="shell-panel__subtitle">ロードは自動で走ります。重い場合は対象チャンネルを絞ってください。</p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span>CH</span>
              <select
                value={channelParam ?? ""}
                onChange={(event) => applyParams({ channel: event.target.value || null })}
                disabled={channelsLoading}
              >
                <option value="">(ALL)</option>
                {channels.map((channel) => (
                  <option key={channel.code} value={channel.code}>
                    {channel.code}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span>検索</span>
              <input
                type="text"
                value={keyword}
                onChange={(event) => applyParams({ q: event.target.value })}
                placeholder="video / title / script_id"
                style={{ width: 260 }}
              />
            </label>

            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={hideCompleted}
                onChange={(event) => applyParams({ hide_completed: event.target.checked })}
              />
              <span>完了を隠す</span>
            </label>

            <button type="button" className="button button--ghost" onClick={() => setReloadKey((v) => v + 1)}>
              再読み込み
            </button>
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        {channelsLoading ? <p className="muted">チャンネルを読み込み中…</p> : null}
        {error ? <div className="main-alert main-alert--error">{error}</div> : null}
        {!channelsLoading && !error ? (
          <div className="shell-panel shell-panel--placeholder">
            <h2>サマリ</h2>
            <dl className="portal-kv" style={{ marginTop: 8 }}>
              <dt>合計</dt>
              <dd>{summary.total.toLocaleString("ja-JP")}</dd>
              <dt>完了</dt>
              <dd>{summary.completed.toLocaleString("ja-JP")}</dd>
              <dt>台本（推定）</dt>
              <dd>{summary.scriptDone.toLocaleString("ja-JP")}</dd>
              <dt>音声</dt>
              <dd>{summary.audioDone.toLocaleString("ja-JP")}</dd>
              <dt>SRT</dt>
              <dd>{summary.srtDone.toLocaleString("ja-JP")}</dd>
              <dt>音声待ち</dt>
              <dd>{summary.readyForAudio.toLocaleString("ja-JP")}</dd>
            </dl>
            {loading ? <p className="muted" style={{ marginTop: 8 }}>読み込み中…</p> : null}
            {results.some((r) => r.error) ? (
              <details className="portal-details" style={{ marginTop: 12 }}>
                <summary>取得失敗（チャンネル別）</summary>
                <ul>
                  {results
                    .filter((r) => r.error)
                    .map((r) => (
                      <li key={r.channel}>
                        {r.channel}: {r.error}
                      </li>
                    ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>案件一覧</h2>
          {filteredVideos.length ? (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left" }}>CH</th>
                    <th style={{ textAlign: "left" }}>Video</th>
                    <th style={{ textAlign: "left" }}>Title</th>
                    <th style={{ textAlign: "left" }}>Status</th>
                    <th style={{ textAlign: "left" }}>Ready</th>
                    <th style={{ textAlign: "left" }}>Script</th>
                    <th style={{ textAlign: "left" }}>Audio</th>
                    <th style={{ textAlign: "left" }}>SRT</th>
                    <th style={{ textAlign: "left" }}>Chars</th>
                    <th style={{ textAlign: "left" }}>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredVideos.map((video) => {
                    const scriptOk = SCRIPT_STAGE_KEYS.some((key) => isDone(stageStatus(video, key)));
                    const audio = stageStatus(video, "audio_synthesis");
                    const srt = stageStatus(video, "srt_generation");
                    const status = video.status;
                    const portalLink = `/channels/${encodeURIComponent(video.channel)}/portal`;
                    const detailLink = `/channels/${encodeURIComponent(video.channel)}/videos/${encodeURIComponent(video.video)}`;
                    return (
                      <tr key={`${video.channel}-${video.video}`}>
                        <td>
                          <Link to={portalLink}>{video.channel}</Link>
                        </td>
                        <td>
                          <Link to={detailLink}>{video.video}</Link>
                        </td>
                        <td style={{ maxWidth: 540 }}>
                          <div style={{ fontWeight: 600 }}>{video.title ?? "—"}</div>
                          <div className="muted" style={{ fontSize: 12 }}>
                            {video.channel_title}
                          </div>
                        </td>
                        <td>{status}</td>
                        <td>{video.ready_for_audio ? "ready" : "—"}</td>
                        <td>{scriptOk ? "ok" : "—"}</td>
                        <td>{audio}</td>
                        <td>{srt}</td>
                        <td>{video.character_count ? video.character_count.toLocaleString("ja-JP") : "—"}</td>
                        <td>{video.updated_at ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">対象なし</p>
          )}
        </div>
      </section>
    </div>
  );
}
