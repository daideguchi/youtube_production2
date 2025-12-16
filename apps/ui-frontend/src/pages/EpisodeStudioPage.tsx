import { useEffect, useMemo, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";

import { createVideoProject, fetchVideoJobs, fetchVideoProjectDetail, runAudioTtsV2FromScript } from "../api/client";
import type { VideoJobRecord, VideoProjectDetail } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";
import { apiUrl } from "../utils/apiClient";
import { resolveAudioSubtitleState } from "../utils/video";

function normalizeChannel(value: string | null): string | null {
  const s = (value || "").trim().toUpperCase();
  if (!s) return null;
  return s;
}

function normalizeVideo(value: string | null): string | null {
  const s = (value || "").trim();
  if (!s) return null;
  if (/^\d+$/.test(s)) return s.padStart(3, "0");
  return s;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

function formatArtifactMeta(meta?: Record<string, unknown> | null): string | null {
  if (!meta) {
    return null;
  }
  const entries = Object.entries(meta).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return null;
  }
  const parts = entries.map(([key, value]) => {
    if (Array.isArray(value)) {
      const shown = value.slice(0, 3).map((item) => String(item));
      const suffix = value.length > shown.length ? ", …" : "";
      return `${key}=[${shown.join(", ")}${suffix}]`;
    }
    if (typeof value === "object") {
      try {
        return `${key}=${JSON.stringify(value)}`;
      } catch (error) {
        return `${key}=[object]`;
      }
    }
    return `${key}=${String(value)}`;
  });
  const joined = parts.join(", ");
  return joined.length > 180 ? `${joined.slice(0, 177)}…` : joined;
}

export function EpisodeStudioPage() {
  const {
    channels,
    channelsLoading,
    videos,
    videosLoading,
    selectedChannel,
    selectedVideo,
    videoDetail,
    detailLoading,
    detailError,
  } = useOutletContext<ShellOutletContext>();

  const [searchParams, setSearchParams] = useSearchParams();

  const channel = useMemo(() => normalizeChannel(selectedChannel), [selectedChannel]);
  const video = useMemo(() => normalizeVideo(selectedVideo), [selectedVideo]);
  const episodeId = channel && video ? `${channel}-${video}` : null;

  const scriptOk = Boolean(videoDetail?.assembled_human_path || videoDetail?.assembled_path);
  const audioState = useMemo(() => (videoDetail ? resolveAudioSubtitleState(videoDetail) : "pending"), [videoDetail]);
  const audioOk = audioState === "completed";

  const episodeBaseLink =
    channel && video ? `/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}` : null;
  const scriptEditLink = episodeBaseLink ? `${episodeBaseLink}?tab=script` : "/channel-workspace";
  const audioEditLink = episodeBaseLink ? `${episodeBaseLink}?tab=audio` : "/channel-workspace";
  const workflowLink =
    channel && video ? `/workflow?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}` : "/workflow";
  const capcutDraftLink =
    channel && video
      ? `/capcut-edit/draft?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}`
      : "/capcut-edit/draft";
  const videoProductionLink =
    channel && video && episodeId
      ? `/capcut-edit/production?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}&project=${encodeURIComponent(episodeId)}`
      : "/capcut-edit/production";

  const [refreshToken, setRefreshToken] = useState(0);
  const cacheBust = `?v=${refreshToken}`;

  const audioUrl =
    channel && video
      ? `${apiUrl(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/audio`)}${cacheBust}`
      : null;
  const srtUrl =
    channel && video
      ? `${apiUrl(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/srt`)}${cacheBust}`
      : null;
  const audioLogUrl =
    channel && video
      ? `${apiUrl(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/log`)}${cacheBust}`
      : null;

  const [videoProject, setVideoProject] = useState<VideoProjectDetail | null>(null);
  const [videoProjectLoading, setVideoProjectLoading] = useState(false);
  const [videoProjectError, setVideoProjectError] = useState<string | null>(null);

  const [videoJobs, setVideoJobs] = useState<VideoJobRecord[] | null>(null);
  const [videoJobsLoading, setVideoJobsLoading] = useState(false);
  const [videoJobsError, setVideoJobsError] = useState<string | null>(null);

  const [audioLogText, setAudioLogText] = useState<string | null>(null);
  const [audioLogLoading, setAudioLogLoading] = useState(false);
  const [audioLogError, setAudioLogError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!episodeId || !channel || !video) {
      setVideoProject(null);
      setVideoProjectError(null);
      setVideoJobs(null);
      setVideoJobsError(null);
      setAudioLogText(null);
      setAudioLogError(null);
      return () => {
        cancelled = true;
      };
    }

    setVideoProjectLoading(true);
    setVideoProjectError(null);
    fetchVideoProjectDetail(episodeId)
      .then((data) => {
        if (cancelled) return;
        setVideoProject(data);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        const lowered = msg.toLowerCase();
        if (lowered.includes("not found") || lowered.includes("404")) {
          setVideoProject(null);
          setVideoProjectError(null);
          return;
        }
        setVideoProject(null);
        setVideoProjectError(msg);
      })
      .finally(() => {
        if (cancelled) return;
        setVideoProjectLoading(false);
      });

    setVideoJobsLoading(true);
    setVideoJobsError(null);
    fetchVideoJobs(episodeId, 30)
      .then((items) => {
        if (cancelled) return;
        setVideoJobs(items);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        const lowered = msg.toLowerCase();
        if (lowered.includes("not found") || lowered.includes("404")) {
          setVideoJobs([]);
          setVideoJobsError(null);
          return;
        }
        setVideoJobs(null);
        setVideoJobsError(msg);
      })
      .finally(() => {
        if (cancelled) return;
        setVideoJobsLoading(false);
      });

    setAudioLogLoading(true);
    setAudioLogError(null);
    fetch(audioLogUrl ?? apiUrl(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/log`), {
      cache: "no-store",
    })
      .then(async (resp) => {
        if (cancelled) return;
        if (resp.status === 404) {
          setAudioLogText(null);
          return;
        }
        if (!resp.ok) {
          const text = await resp.text().catch(() => "");
          throw new Error(text || `HTTP ${resp.status} ${resp.statusText}`);
        }
        const raw = await resp.text();
        try {
          const parsed = JSON.parse(raw);
          setAudioLogText(JSON.stringify(parsed, null, 2));
        } catch {
          setAudioLogText(raw);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setAudioLogText(null);
        setAudioLogError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (cancelled) return;
        setAudioLogLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [audioLogUrl, channel, episodeId, refreshToken, video]);

  const handleRefresh = () => {
    setRefreshToken((value) => value + 1);
  };

  const [ttsRunBusy, setTtsRunBusy] = useState(false);
  const [ttsRunMessage, setTtsRunMessage] = useState<string | null>(null);
  const [ttsRunError, setTtsRunError] = useState<string | null>(null);

  const [projectCreateBusy, setProjectCreateBusy] = useState(false);
  const [projectCreateMessage, setProjectCreateMessage] = useState<string | null>(null);
  const [projectCreateError, setProjectCreateError] = useState<string | null>(null);

  const handleRunTts = async () => {
    if (!channel || !video) {
      setTtsRunError("チャンネル/動画が未選択です。");
      return;
    }
    const detail = videoDetail;
    if (!detail) {
      setTtsRunError("詳細が未取得です（少し待ってから再試行してください）。");
      return;
    }

    setTtsRunBusy(true);
    setTtsRunMessage(null);
    setTtsRunError(null);
    try {
      const res = await runAudioTtsV2FromScript({
        channel,
        video,
      });
      const finalWav = (res as any).final_wav ?? res.wav_path;
      const finalSrt = (res as any).final_srt ?? res.srt_path ?? "";
      setTtsRunMessage(`TTS実行成功: wav=${finalWav}${finalSrt ? ` srt=${finalSrt}` : ""}`);
      handleRefresh();
    } catch (err) {
      setTtsRunError(err instanceof Error ? err.message : String(err));
    } finally {
      setTtsRunBusy(false);
    }
  };

  const handleCreateVideoProject = async () => {
    if (!episodeId || !channel || !video) {
      setProjectCreateError("チャンネル/動画が未選択です。");
      return;
    }

    setProjectCreateBusy(true);
    setProjectCreateMessage(null);
    setProjectCreateError(null);
    try {
      const srtPath =
        videoDetail?.srt_path ?? `audio_tts_v2/artifacts/final/${channel}/${video}/${episodeId}.srt`;

      const res = await createVideoProject({
        projectId: episodeId,
        channelId: channel,
        existingSrtPath: srtPath,
      });
      setProjectCreateMessage(`プロジェクト作成: ${res.project_id} (${res.output_dir})`);
      handleRefresh();
    } catch (err) {
      setProjectCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setProjectCreateBusy(false);
    }
  };

  const handleChannelChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    const normalized = normalizeChannel(next);
    if (normalized) {
      params.set("channel", normalized);
    } else {
      params.delete("channel");
    }
    params.delete("video");
    setSearchParams(params, { replace: true });
  };

  const handleVideoChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    const normalized = normalizeVideo(next);
    if (normalized) {
      params.set("video", normalized);
    } else {
      params.delete("video");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="page episode-studio-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">統合スタジオ</p>
          <h1>Episode Studio</h1>
          <p className="page-lead">企画 → 台本 → 音声 → 動画（CapCut）を、1本単位で迷わず進めるための画面です。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <button className="button button--ghost" onClick={handleRefresh} disabled={!episodeId}>
            更新
          </button>
          <Link className="button button--ghost" to={workflowLink}>
            ← 制作フロー
          </Link>
          <Link className="button button--ghost" to="/dashboard">
            ダッシュボード
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>1) エピソードを選ぶ</h2>
          <p className="shell-panel__subtitle">チャンネルと動画番号を選ぶと、状態と次の導線がまとまって見えます。</p>
          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            <label>
              チャンネル
              <select
                value={channel ?? ""}
                onChange={(e) => handleChannelChange(e.target.value)}
                disabled={channelsLoading}
                style={{ width: "100%" }}
              >
                <option value="">(未選択)</option>
                {channels.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code}
                  </option>
                ))}
              </select>
            </label>
            <label>
              動画番号
              <select
                value={video ?? ""}
                onChange={(e) => handleVideoChange(e.target.value)}
                disabled={!channel || videosLoading}
                style={{ width: "100%" }}
              >
                <option value="">(未選択)</option>
                {videos.map((v) => (
                  <option key={v.video} value={v.video}>
                    {v.video} {v.title ? `- ${v.title}` : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {detailError ? <div className="main-alert main-alert--error">{detailError}</div> : null}
          {detailLoading ? <div className="main-alert">詳細を読み込み中です…</div> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>2) 現在地</h2>
          <div className="main-status" style={{ marginTop: 0 }}>
            <span className="status-chip">{episodeId ? `対象: ${episodeId}` : "対象: 未選択"}</span>
            <span className={`status-chip ${scriptOk ? "" : "status-chip--warning"}`}>
              台本: {scriptOk ? "OK" : "未確認"}
            </span>
            <span className={`status-chip ${audioOk ? "" : "status-chip--warning"}`}>
              音声/SRT: {audioOk ? "OK" : audioState}
            </span>
            <span className={`status-chip ${videoProject ? "" : "status-chip--warning"}`}>
              動画:{" "}
              {videoProjectLoading
                ? "確認中…"
                : videoProject
                  ? videoProject.guard?.status === "ok"
                    ? "OK"
                    : "要確認"
                  : "未作成"}
            </span>
          </div>

          {!episodeId ? <div className="main-alert">まずエピソードを選択してください。</div> : null}
          {videoProjectError ? <div className="main-alert main-alert--error">{videoProjectError}</div> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>3) 次の導線</h2>
          <p className="shell-panel__subtitle">各工程の画面へ最短で移動します（ログはこの下で統合表示）。</p>

          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>台本</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                {scriptOk ? "台本が存在します。必要なら修正へ。" : "台本が見つかりません。作成/生成へ。"}
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={scriptEditLink}>
                  台本を開く
                </Link>
                <Link className="button button--ghost" to="/projects">
                  台本作成（バッチ）
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>音声 / SRT</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                {audioOk ? "final に揃っています。ズレや誤読の確認へ。" : "未完了です。TTS生成や字幕調整へ。"}
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={audioEditLink}>
                  音声/字幕を開く
                </Link>
                <Link className="button button--ghost" to="/audio-tts-v2">
                  TTS生成（一覧）
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>動画（CapCut）</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                final SRT からドラフト作成（AutoDraft）またはプロジェクト管理（VideoProduction）へ。
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={capcutDraftLink}>
                  新規ドラフト作成
                </Link>
                <Link className="button button--ghost" to={videoProductionLink}>
                  プロジェクト管理
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>ログ</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                失敗時はまずログとジョブ状況を確認します。
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <a className="button" href="#episode-logs">
                  このページで見る
                </a>
                <Link className="button button--ghost" to="/jobs">
                  ジョブ管理
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>サムネ（独立）</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                サムネは別動線です（ここから独立ページへ移動します）。
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button button--ghost" to="/thumbnails">
                  サムネへ
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section" id="episode-artifacts">
        <div className="shell-panel shell-panel--placeholder">
          <h2>4) 成果物（SoT / final）</h2>
          <p className="shell-panel__subtitle">このエピソードで参照する“正本”と、ダウンロード導線です。</p>

          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0 }}>企画（CSV）</h3>
              {videoDetail?.planning ? (
                <details>
                  <summary>企画情報を開く</summary>
                  <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                    <div>
                      <span className="status-chip">creation_flag: {videoDetail.planning.creation_flag ?? "—"}</span>
                    </div>
                    {videoDetail.planning.fields?.length ? (
                      <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead>
                          <tr>
                            <th style={{ textAlign: "left" }}>Key</th>
                            <th style={{ textAlign: "left" }}>Label</th>
                            <th style={{ textAlign: "left" }}>Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {videoDetail.planning.fields.map((field) => (
                            <tr key={`${field.key}-${field.column}`}>
                              <td style={{ verticalAlign: "top", paddingRight: 10 }}>{field.key}</td>
                              <td style={{ verticalAlign: "top", paddingRight: 10 }}>{field.label}</td>
                              <td style={{ verticalAlign: "top" }}>{field.value ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <div className="main-alert">企画フィールドがありません。</div>
                    )}
                  </div>
                </details>
              ) : (
                <div className="main-alert">企画情報は未取得です（CSVが未整備の可能性）。</div>
              )}
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0 }}>台本</h3>
              <div style={{ display: "grid", gap: 8 }}>
                <div className="status-chip">
                  assembled:{" "}
                  {videoDetail?.assembled_human_path ??
                    videoDetail?.assembled_path ??
                    "(not found)"}
                </div>
                <div className="status-chip">
                  audio script:{" "}
                  {videoDetail?.script_audio_human_path ??
                    videoDetail?.script_audio_path ??
                    videoDetail?.tts_path ??
                    "(not found)"}
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <Link className="button" to={scriptEditLink}>
                    台本を開く
                  </Link>
                  <Link className="button button--ghost" to={audioEditLink}>
                    音声/字幕を開く
                  </Link>
                </div>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0 }}>音声 / SRT（final）</h3>
              <div style={{ display: "grid", gap: 10 }}>
                <div className="status-chip">
                  wav:{" "}
                  {videoDetail?.audio_path ??
                    (episodeId && channel && video
                      ? `audio_tts_v2/artifacts/final/${channel}/${video}/${episodeId}.wav`
                      : "(unknown)")}
                </div>
                <div className="status-chip">
                  srt:{" "}
                  {videoDetail?.srt_path ??
                    (episodeId && channel && video
                      ? `audio_tts_v2/artifacts/final/${channel}/${video}/${episodeId}.srt`
                      : "(unknown)")}
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <a className="button button--ghost" href={audioUrl ?? undefined} target="_blank" rel="noreferrer">
                    wav を開く
                  </a>
                  <a className="button button--ghost" href={srtUrl ?? undefined} target="_blank" rel="noreferrer">
                    srt を開く
                  </a>
                  <a className="button button--ghost" href={audioLogUrl ?? undefined} target="_blank" rel="noreferrer">
                    log.json
                  </a>
                  <button className="button" onClick={handleRunTts} disabled={!episodeId || ttsRunBusy}>
                    {ttsRunBusy ? "TTS実行中…" : "TTS実行"}
                  </button>
                </div>
                {ttsRunMessage ? <div className="main-alert">{ttsRunMessage}</div> : null}
                {ttsRunError ? <div className="main-alert main-alert--error">{ttsRunError}</div> : null}
                {audioUrl ? (
                  <audio controls src={audioUrl} style={{ width: "100%" }} />
	                ) : (
	                  <div className="main-alert">音声URLが未確定です。</div>
	                )}
	                <div className="muted">最終更新: {formatDateTime(videoDetail?.audio_updated_at ?? videoDetail?.updated_at) || "—"}</div>
	              </div>
	            </div>
	
	            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
	              <h3 style={{ marginTop: 0 }}>Artifacts（チェック）</h3>
	              {videoDetail?.artifacts?.project_dir ? (
	                <div className="muted">dir: {videoDetail.artifacts.project_dir}</div>
	              ) : null}
	              {videoDetail?.artifacts?.items?.length ? (
	                <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
	                  {videoDetail.artifacts.items.map((item) => {
	                    const metaText = formatArtifactMeta(item.meta ?? null);
	                    return (
	                      <div key={item.key} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
	                        <div style={{ minWidth: 0 }}>
	                          <div className={`status-chip ${item.exists ? "" : "status-chip--warning"}`}>
	                            {item.label}: {item.path}
	                          </div>
	                          {metaText ? <div className="muted">{metaText}</div> : null}
	                        </div>
	                        <span className={`status-chip ${item.exists ? "" : "status-chip--warning"}`}>
	                          {item.exists ? "OK" : "MISSING"}
	                        </span>
	                      </div>
	                    );
	                  })}
	                </div>
	              ) : (
	                <div className="main-alert">artifacts は未取得です（チャンネル/動画を選択してください）。</div>
	              )}
	            </div>
	          </div>
	        </div>
	      </section>

      <section className="capcut-edit-page__section" id="episode-video">
        <div className="shell-panel shell-panel--placeholder">
          <h2>5) 動画（VideoProduction）</h2>
          <p className="shell-panel__subtitle">プロジェクト状態・ガード・ジョブ・ログをここで確認できます。</p>

          {videoProjectLoading ? <div className="main-alert">プロジェクト詳細を読み込み中です…</div> : null}
          {!videoProjectLoading && !videoProject ? (
            <div className="main-alert">
              まだ VideoProduction プロジェクトが見つかりません（ID: {episodeId ?? "—"}）。まずはプロジェクト作成へ。
            </div>
          ) : null}

          {videoProject ? (
            <div style={{ display: "grid", gap: 12 }}>
              <div className="main-status" style={{ marginTop: 0 }}>
                <span className="status-chip">project: {videoProject.summary.id}</span>
                <span className="status-chip">status: {videoProject.summary.status}</span>
                <span className={`status-chip ${videoProject.guard?.status === "ok" ? "" : "status-chip--warning"}`}>
                  guard: {videoProject.guard?.status ?? "—"}
                </span>
                <span className="status-chip">images: {videoProject.summary.image_count}</span>
                <span className="status-chip">logs: {videoProject.summary.log_count}</span>
              </div>

              {videoProject.guard?.issues?.length ? (
                <details>
                  <summary>Guard issues（{videoProject.guard.issues.length}）</summary>
                  <ul>
                    {videoProject.guard.issues.map((issue, idx) => (
                      <li key={`${issue.code}-${idx}`}>
                        [{issue.code}] {issue.message}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

	              {videoProject.log_excerpt?.length ? (
	                <details>
	                  <summary>Project log excerpt</summary>
	                  <pre style={{ whiteSpace: "pre-wrap", background: "#0b1020", color: "#e6e6e6", padding: 12, borderRadius: 10, maxHeight: 360, overflow: "auto" }}>
	                    {videoProject.log_excerpt.join("\n")}
	                  </pre>
	                </details>
	              ) : null}
	
	              {videoProject.artifacts?.items?.length ? (
	                <details>
	                  <summary>Artifacts（run_dir）</summary>
	                  {videoProject.artifacts.project_dir ? (
	                    <div className="muted" style={{ marginTop: 8 }}>
	                      dir: {videoProject.artifacts.project_dir}
	                    </div>
	                  ) : null}
	                  <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
	                    {videoProject.artifacts.items.map((item) => {
	                      const metaText = formatArtifactMeta(item.meta ?? null);
	                      return (
	                        <div key={item.key} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
	                          <div style={{ minWidth: 0 }}>
	                            <div className={`status-chip ${item.exists ? "" : "status-chip--warning"}`}>
	                              {item.label}: {item.path}
	                            </div>
	                            {metaText ? <div className="muted">{metaText}</div> : null}
	                          </div>
	                          <span className={`status-chip ${item.exists ? "" : "status-chip--warning"}`}>
	                            {item.exists ? "OK" : "MISSING"}
	                          </span>
	                        </div>
	                      );
	                    })}
	                  </div>
	                </details>
	              ) : null}

	              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
	                <Link className="button" to={videoProductionLink}>
	                  プロジェクト管理へ
	                </Link>
                <Link className="button button--ghost" to={capcutDraftLink}>
                  AutoDraft へ
                </Link>
              </div>
            </div>
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button className="button" onClick={handleCreateVideoProject} disabled={!episodeId || projectCreateBusy}>
                  {projectCreateBusy ? "プロジェクト作成中…" : "プロジェクト作成（final SRT）"}
                </button>
                <Link className="button button--ghost" to={videoProductionLink}>
                  プロジェクト作成/管理へ
                </Link>
                <Link className="button button--ghost" to={capcutDraftLink}>
                  AutoDraft へ
                </Link>
              </div>
              {projectCreateMessage ? <div className="main-alert">{projectCreateMessage}</div> : null}
              {projectCreateError ? <div className="main-alert main-alert--error">{projectCreateError}</div> : null}
            </div>
          )}
        </div>
      </section>

      <section className="capcut-edit-page__section" id="episode-logs">
        <div className="shell-panel shell-panel--placeholder">
          <h2>6) ログ（エピソード単位）</h2>

          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0 }}>Audio log.json（final）</h3>
              {audioLogLoading ? <div className="main-alert">読み込み中…</div> : null}
              {audioLogError ? <div className="main-alert main-alert--error">{audioLogError}</div> : null}
              {!audioLogLoading && !audioLogError && !audioLogText ? <div className="main-alert">log が見つかりません。</div> : null}
              {audioLogText ? (
                <details>
                  <summary>log.json を表示</summary>
                  <pre style={{ whiteSpace: "pre-wrap", background: "#0b1020", color: "#e6e6e6", padding: 12, borderRadius: 10, maxHeight: 360, overflow: "auto" }}>
                    {audioLogText}
                  </pre>
                </details>
              ) : null}
              <div style={{ marginTop: 10 }}>
                <a className="button button--ghost" href={audioLogUrl ?? undefined} target="_blank" rel="noreferrer">
                  log.json を開く
                </a>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0 }}>VideoProduction jobs</h3>
              {videoJobsLoading ? <div className="main-alert">読み込み中…</div> : null}
              {videoJobsError ? <div className="main-alert main-alert--error">{videoJobsError}</div> : null}
              {!videoJobsLoading && !videoJobsError && videoJobs?.length === 0 ? <div className="main-alert">ジョブなし</div> : null}

              {videoJobs && videoJobs.length > 0 ? (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left" }}>Status</th>
                      <th style={{ textAlign: "left" }}>Action</th>
                      <th style={{ textAlign: "left" }}>Created</th>
                      <th style={{ textAlign: "left" }}>Log</th>
                    </tr>
                  </thead>
                  <tbody>
                    {videoJobs
                      .slice()
                      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
                      .map((job) => {
                        const logHref = apiUrl(`/api/video-production/jobs/${encodeURIComponent(job.id)}/log`);
                        return (
                          <tr key={job.id}>
                            <td style={{ verticalAlign: "top", paddingRight: 10 }}>{job.status}</td>
                            <td style={{ verticalAlign: "top", paddingRight: 10 }}>{job.action}</td>
                            <td style={{ verticalAlign: "top", paddingRight: 10 }}>{formatDateTime(job.created_at)}</td>
                            <td style={{ verticalAlign: "top" }}>
                              <a href={logHref} target="_blank" rel="noreferrer">
                                open
                              </a>
                              {job.log_excerpt?.length ? (
                                <details>
                                  <summary>excerpt</summary>
                                  <pre style={{ whiteSpace: "pre-wrap", background: "#0b1020", color: "#e6e6e6", padding: 10, borderRadius: 10, maxHeight: 240, overflow: "auto" }}>
                                    {job.log_excerpt.join("\n")}
                                  </pre>
                                </details>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
