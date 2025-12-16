import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import type { VideoDetail } from "../api/types";
import { translateStatus } from "../utils/i18n";
import { API_BASE_URL, fetchPlainTtsScript, runAudioTtsV2FromScript } from "../api/client";

type AudioWorkspaceHandlers = {
  onSaveSrt: (content: string) => Promise<unknown>;
  onVerifySrt: (tolerance?: number) => Promise<unknown>;
  onUpdateStatus: (status: string) => Promise<unknown>;
  onUpdateReady: (ready: boolean) => Promise<unknown>;
  onUpdateStages: (stages: Record<string, string>) => Promise<unknown>;
  onReplaceTts: (request: {
    original: string;
    replacement: string;
    scope: "first" | "all";
    updateAssembled: boolean;
    regenerateAudio: boolean;
  }) => Promise<unknown>;
};

interface AudioWorkspaceProps {
  detail: VideoDetail;
  handlers: AudioWorkspaceHandlers;
  refreshing: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  showSrtColumn?: boolean;
  title?: string;
  hint?: string;
}

type TimelineRow = {
  index: number;
  start: string;
  end: string;
  speaker: string | null;
  text: string;
  raw: string;
};

type TimelineEntry = TimelineRow & {
  startSeconds: number | null;
  endSeconds: number | null;
};

function parseSrt(content: string): TimelineRow[] {
  const rows: TimelineRow[] = [];
  const normalized = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const blocks = normalized
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  blocks.forEach((block, index) => {
    const lines = block.split("\n");
    if (lines.length === 0) {
      return;
    }
    let cursor = 0;
    if (/^\d+$/.test(lines[0])) {
      cursor = 1;
    }
    let start = "";
    let end = "";
    if (lines[cursor] && lines[cursor].includes("-->")) {
      const [startRaw, endRaw] = lines[cursor].split("-->").map((value) => value.trim());
      start = startRaw ?? "";
      end = endRaw ?? "";
      cursor += 1;
    }
    const text = lines.slice(cursor).join("\n");
    rows.push({
      index: index + 1,
      start,
      end,
      speaker: null,
      text,
      raw: block,
    });
  });

  return rows;
}

function parseSrtTimestamp(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const match = value.trim().match(/^(\d{2}):(\d{2}):(\d{2}),(\d{3})$/);
  if (!match) {
    return null;
  }
  const [, hh, mm, ss, ms] = match;
  const hours = Number.parseInt(hh, 10);
  const minutes = Number.parseInt(mm, 10);
  const seconds = Number.parseInt(ss, 10);
  const millis = Number.parseInt(ms, 10);
  if ([hours, minutes, seconds, millis].some((part) => Number.isNaN(part))) {
    return null;
  }
  return hours * 3600 + minutes * 60 + seconds + millis / 1000;
}

function splitScriptIntoSegments(script: string): string[] {
  return script
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((segment) => segment.trim())
    .filter((segment) => segment.length > 0);
}

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ja-JP", { hour: "2-digit", minute: "2-digit" });
}

export function AudioWorkspace({ detail, handlers, refreshing, onDirtyChange, showSrtColumn, title, hint }: AudioWorkspaceProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [audioReloadToken, setAudioReloadToken] = useState(() => Date.now());
  const versionToken = useMemo(
    () => `${detail.updated_at || detail.audio_updated_at || "na"}-${audioReloadToken}`,
    [audioReloadToken, detail.audio_updated_at, detail.updated_at]
  );

  const [audioScript, setAudioScript] = useState(detail.tts_plain_content ?? detail.tts_content ?? "");
  const [audioScriptUpdatedAt, setAudioScriptUpdatedAt] = useState<string | null>(
    detail.audio_updated_at ?? detail.updated_at ?? null
  );
  const [audioScriptLoading, setAudioScriptLoading] = useState(false);
  const [audioScriptError, setAudioScriptError] = useState<string | null>(null);
  const [srtDraft, setSrtDraft] = useState(detail.srt_content ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [srtReloading, setSrtReloading] = useState(false);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [activeTimelineIndex, setActiveTimelineIndex] = useState<number | null>(null);
  const [showSrtArea, setShowSrtArea] = useState<boolean>(showSrtColumn ?? true);
  const [latestAudioUrl, setLatestAudioUrl] = useState<string | null>(null);
  const [latestSrtUrl, setLatestSrtUrl] = useState<string | null>(null);
  const [latestLogUrl, setLatestLogUrl] = useState<string | null>(null);
  const warningMessages = useMemo(() => detail.warnings?.filter(Boolean) ?? [], [detail.warnings]);

  useEffect(() => {
    setAudioScript(detail.tts_plain_content ?? detail.tts_content ?? "");
    setSrtDraft(detail.srt_content ?? "");
    setAudioScriptUpdatedAt(detail.audio_updated_at ?? detail.updated_at ?? null);
    setMessage(null);
    setError(null);
    setActiveTimelineIndex(null);
    setShowSrtArea(showSrtColumn ?? true);
    setAudioReloadToken(Date.now()); // detailが変わったら強制的に音声URLを更新
  }, [detail, showSrtColumn]);

  useEffect(() => {
    const baseSrt = detail.srt_content ?? "";
    onDirtyChange?.(srtDraft !== baseSrt);
  }, [detail.srt_content, onDirtyChange, srtDraft]);

  const scriptSegments = useMemo(() => splitScriptIntoSegments(audioScript), [audioScript]);

  const timelineEntries = useMemo<TimelineEntry[]>(() => {
    const rows = parseSrt(srtDraft);
    if (rows.length > 0) {
      return rows.map((row) => ({
        ...row,
        startSeconds: parseSrtTimestamp(row.start),
        endSeconds: parseSrtTimestamp(row.end),
      }));
    }
    return scriptSegments.map((text, index) => ({
      index: index + 1,
      start: "",
      end: "",
      speaker: null,
      text,
      raw: text,
      startSeconds: null,
      endSeconds: null,
    }));
  }, [scriptSegments, srtDraft]);

  const timelineFromSrt = useMemo(() => timelineEntries.some((entry) => entry.start || entry.end), [timelineEntries]);

  const timelineItems = useMemo(
    () =>
      timelineEntries.map((entry) => ({
        ...entry,
        displayText: timelineFromSrt ? entry.text ?? entry.raw ?? "" : scriptSegments[entry.index - 1] ?? entry.text ?? entry.raw ?? "",
      })),
    [scriptSegments, timelineEntries, timelineFromSrt]
  );

  const audioPlaybackUrl = useMemo(() => {
    const url = latestAudioUrl ?? detail.audio_url;
    if (!url) {
      return null;
    }
    if (/^https?:/i.test(url)) {
      const separator = url.includes("?") ? "&" : "?";
      return `${url}${separator}v=${encodeURIComponent(versionToken)}`;
    }
    const normalized = url.startsWith("/") ? url : `/${url}`;
    const base = `${API_BASE_URL}${normalized}`;
    const separator = base.includes("?") ? "&" : "?";
    return `${base}${separator}v=${encodeURIComponent(versionToken)}`;
  }, [detail.audio_url, latestAudioUrl, versionToken]);

  const srtDownloadUrl = useMemo(() => {
    if (latestSrtUrl) {
      if (/^https?:/i.test(latestSrtUrl)) {
          const separator = latestSrtUrl.includes("?") ? "&" : "?";
          return `${latestSrtUrl}${separator}v=${encodeURIComponent(versionToken)}`;
      }
      const normalized = latestSrtUrl.startsWith("/") ? latestSrtUrl : `/${latestSrtUrl}`;
      const base = `${API_BASE_URL}${normalized}`;
      const separator = base.includes("?") ? "&" : "?";
      return `${base}${separator}v=${encodeURIComponent(versionToken)}`;
    }
    const base = API_BASE_URL?.replace(/\/$/, "") ?? "";
    const path = `${base}/api/channels/${encodeURIComponent(detail.channel)}/videos/${encodeURIComponent(detail.video)}/srt`;
    const separator = path.includes("?") ? "&" : "?";
    return `${path}${separator}v=${encodeURIComponent(versionToken)}`;
  }, [detail.channel, detail.video, latestSrtUrl, versionToken]);

  const displayedSrtPath = useMemo(() => {
    const fallback = `audio_tts_v2/artifacts/final/${detail.channel}/${detail.video}/${detail.channel}-${detail.video}.srt`;
    return detail.srt_path ?? fallback;
  }, [detail.channel, detail.video, detail.srt_path]);

  const handleRunTts = useCallback(async () => {
    setRunBusy(true);
    setRunMessage(null);
    setRunError(null);
    try {
      const res = await runAudioTtsV2FromScript({
        channel: detail.channel,
        video: detail.video,
      });
      // llm_meta はバックエンドの追加フィールド（型が古い場合でも拾えるようにフォールバック）
      const meta = "llm_meta" in res ? (res as any).llm_meta : null;
      const metaLabel = meta
        ? ` | LLM: ${meta.model ?? "n/a"} (${meta.provider ?? "n/a"}) req=${meta.request_id ?? "?"} latency=${meta.latency_ms ?? "?"}ms`
        : "";
      setRunMessage(`TTS実行成功: engine=${res.engine ?? "n/a"}, wav=${res.wav_path}${metaLabel}`);
      // 最新ファイルを即反映
      const absoluteWav = /^https?:/i.test(res.wav_path)
        ? res.wav_path
        : `${API_BASE_URL}${res.wav_path.startsWith("/") ? res.wav_path : `/${res.wav_path}`}`;
      setLatestAudioUrl(absoluteWav);
      if (res.srt_path) {
        const absoluteSrt = /^https?:/i.test(res.srt_path)
          ? res.srt_path
          : `${API_BASE_URL}${res.srt_path.startsWith("/") ? res.srt_path : `/${res.srt_path}`}`;
        setLatestSrtUrl(absoluteSrt);
      }
      if (res.final_wav) {
        const absoluteFinalWav = /^https?:/i.test(res.final_wav)
          ? res.final_wav
          : `${API_BASE_URL}${res.final_wav.startsWith("/") ? res.final_wav : `/${res.final_wav}`}`;
        setLatestAudioUrl(absoluteFinalWav);
      }
      if (res.final_srt) {
        const absoluteFinalSrt = /^https?:/i.test(res.final_srt)
          ? res.final_srt
          : `${API_BASE_URL}${res.final_srt.startsWith("/") ? res.final_srt : `/${res.final_srt}`}`;
        setLatestSrtUrl(absoluteFinalSrt);
      }
      if (res.log) {
        const absoluteLog = /^https?:/i.test(res.log)
          ? res.log
          : `${API_BASE_URL}${res.log.startsWith("/") ? res.log : `/${res.log}`}`;
        setLatestLogUrl(absoluteLog);
      }
    } catch (runErr) {
      const msg = runErr instanceof Error ? runErr.message : String(runErr ?? "TTS実行に失敗しました");
      setRunError(msg);
    } finally {
      setRunBusy(false);
    }
  }, [detail.channel, detail.video]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || timelineEntries.every((entry) => entry.startSeconds == null)) {
      return;
    }
    const updateActive = () => {
      const current = audio.currentTime;
      let matched: number | null = null;
      for (const entry of timelineEntries) {
        if (entry.startSeconds == null) {
          continue;
        }
        const end = entry.endSeconds ?? entry.startSeconds + 2;
        if (current >= entry.startSeconds && current < end) {
          matched = entry.index;
          break;
        }
      }
      setActiveTimelineIndex(matched);
    };
    const reset = () => setActiveTimelineIndex(null);
    audio.addEventListener("timeupdate", updateActive);
    audio.addEventListener("seeked", updateActive);
    audio.addEventListener("ended", reset);
    return () => {
      audio.removeEventListener("timeupdate", updateActive);
      audio.removeEventListener("seeked", updateActive);
      audio.removeEventListener("ended", reset);
    };
  }, [timelineEntries]);

  const handleSrtChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    setSrtDraft(event.target.value);
    setMessage(null);
    setError(null);
  }, []);

  const handleTimelineSelect = useCallback(
    (entry: TimelineEntry) => {
      const audio = audioRef.current;
      if (!audio) {
        setActiveTimelineIndex(entry.index);
        return;
      }

      const startPlayback = () => {
        if (activeTimelineIndex === entry.index && !audio.paused) {
          audio.pause();
          return;
        }
        if (entry.startSeconds != null) {
          audio.currentTime = Math.max(entry.startSeconds - 0.05, 0);
        }
        setActiveTimelineIndex(entry.index);
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.then === "function") {
          playPromise.catch(() => {
            /* ignore autoplay rejection */
          });
        }
      };

      if (audio.readyState < 1) {
        const handleReady = () => {
          audio.removeEventListener("loadedmetadata", handleReady);
          startPlayback();
        };
        audio.addEventListener("loadedmetadata", handleReady);
        audio.load();
        return;
      }

      startPlayback();
    },
    [activeTimelineIndex]
  );

  const handleSaveSrt = useCallback(async () => {
    if (saveBusy || refreshing) {
      return;
    }
    setSaveBusy(true);
    setMessage(null);
    setError(null);
    try {
      await handlers.onSaveSrt(srtDraft);
      setMessage("字幕を保存しました。必要に応じて再生成を実行してください。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaveBusy(false);
    }
  }, [handlers, refreshing, saveBusy, srtDraft]);

  const refreshAudioScript = useCallback(async () => {
    setAudioScriptLoading(true);
    setAudioScriptError(null);
    try {
      const response = await fetchPlainTtsScript(detail.channel, detail.video);
      const nextScript = response.content ?? "";
      setAudioScript(nextScript);
      setAudioScriptUpdatedAt(response.updated_at ?? detail.audio_updated_at ?? detail.updated_at ?? null);
    } catch (err) {
      setAudioScriptError(err instanceof Error ? err.message : String(err));
    } finally {
      setAudioScriptLoading(false);
    }
  }, [detail.audio_updated_at, detail.channel, detail.updated_at, detail.video]);

  const handleVerifySrt = useCallback(async () => {
    if (verifyBusy) {
      return;
    }
    setVerifyBusy(true);
    setMessage(null);
    setError(null);
    try {
      await handlers.onVerifySrt();
      setMessage("字幕のズレ検証を開始しました。ログで進行状況を確認してください。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setVerifyBusy(false);
    }
  }, [handlers, verifyBusy]);

  const handleReloadSrt = useCallback(async () => {
    if (srtReloading || !srtDownloadUrl) {
      return;
    }
    setSrtReloading(true);
    setMessage(null);
    setError(null);
    try {
      const resp = await fetch(srtDownloadUrl, { cache: "no-store" });
      if (!resp.ok) {
        throw new Error(`SRT取得に失敗しました (${resp.status})`);
      }
      const text = await resp.text();
      setSrtDraft(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSrtReloading(false);
    }
  }, [srtDownloadUrl, srtReloading]);

  const handleStatusUpdate = useCallback(
    async (status: string) => {
      if (statusUpdating) {
        return;
      }
      setStatusUpdating(true);
      setMessage(null);
      setError(null);
      try {
        await handlers.onUpdateStatus(status);
        setMessage(`ステータスを「${translateStatus(status)}」に更新しました。`);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setStatusUpdating(false);
      }
    },
    [handlers, statusUpdating]
  );

  const timelineMetaLabel = useMemo(() => {
    if (timelineEntries.length === 0) {
      return "未生成";
    }
    const hasTiming = timelineEntries.some((entry) => entry.startSeconds != null);
    return hasTiming ? `${timelineEntries.length} ブロック` : `${timelineEntries.length} ブロック（未タイムコード）`;
  }, [timelineEntries]);

  const audioScriptUpdatedLabel = useMemo(() => formatTimestamp(audioScriptUpdatedAt) || "未更新", [audioScriptUpdatedAt]);

  return (
    <div className="audio-workspace audio-workspace--compact">
      {warningMessages.length > 0 ? (
        <div className="main-alert main-alert--warning" role="alert">
          <strong>未整備:</strong> {warningMessages.join(" / ")}
        </div>
      ) : null}
      <header className="audio-workspace__header">
        <div>
          <div className="audio-workspace__breadcrumbs">
            <span className="audio-workspace__breadcrumbs-code">{detail.channel}</span>
            <span className="audio-workspace__breadcrumbs-sep">/</span>
            <span className="audio-workspace__breadcrumbs-video">{detail.video}</span>
          </div>
          {title ? <h3 className="audio-workspace__title">{title}</h3> : null}
          {hint ? <p className="audio-workspace__hint">{hint}</p> : <p className="audio-workspace__hint">生成済み音声を確認できます。</p>}
          <div className="audio-workspace__quick-actions">
            <button
              type="button"
              className="workspace-button workspace-button--primary workspace-button--compact"
              onClick={() => void handleRunTts()}
              disabled={runBusy}
            >
              {runBusy ? "音声再生成中…" : "音声を再生成 (TTS v2)"}
            </button>
            {latestLogUrl ? (
              <a className="workspace-button workspace-button--ghost workspace-button--compact" href={latestLogUrl} target="_blank" rel="noreferrer">
                最新ログ
              </a>
            ) : null}
          </div>
        </div>
        <div className="audio-workspace__header-actions">
          <span className="audio-workspace__header-note">音声テキスト更新: {audioScriptUpdatedLabel}</span>
          <button
            type="button"
            className="workspace-button workspace-button--ghost"
            onClick={() => void refreshAudioScript()}
            disabled={audioScriptLoading}
          >
            {audioScriptLoading ? "取得中…" : "音声用テキストを再取得"}
          </button>
        </div>
      </header>

      {detail.artifacts?.items?.length ? (
        <details style={{ margin: "12px 0" }}>
          <summary>Artifacts</summary>
          {detail.artifacts.project_dir ? <p className="muted small-text">dir: {detail.artifacts.project_dir}</p> : null}
          <div style={{ display: "grid", gap: 6 }}>
            {detail.artifacts.items.map((item) => (
              <div key={item.key} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <span className={`status-chip ${item.exists ? "" : "status-chip--warning"}`} style={{ minWidth: 0 }}>
                  {item.label}: {item.path}
                </span>
                <span className={`status-chip ${item.exists ? "" : "status-chip--warning"}`}>
                  {item.exists ? "OK" : "MISSING"}
                </span>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <section className="audio-workspace__editor-panel audio-workspace__editor-panel--primary" aria-label="音声用台本 (B)">
        <label className="audio-workspace__editor-field">
          <span className="audio-workspace__editor-label">音声用台本（B / 読み上げ専用）</span>
          <textarea
            className="audio-workspace__editor-textarea"
            value={audioScript}
            readOnly
            aria-readonly="true"
            placeholder="音声用テキストは assembled.md + 辞書から再生成されます。"
          />
        </label>
        <div className="audio-workspace__editor-meta">更新: {audioScriptUpdatedLabel}</div>
        <div className="audio-workspace__editor-actions">
          <button
            type="button"
            className="workspace-button workspace-button--ghost"
            onClick={() => void refreshAudioScript()}
            disabled={audioScriptLoading}
          >
            {audioScriptLoading ? "再取得中…" : "最新の音声テキストを取得"}
          </button>
          <button
            type="button"
            className="workspace-button workspace-button--ghost"
            onClick={handleVerifySrt}
            disabled={verifyBusy}
          >
            {verifyBusy ? "検証中…" : "字幕ズレを検証"}
          </button>
        </div>
        {audioScriptError ? <p className="audio-workspace__alert audio-workspace__alert--error">{audioScriptError}</p> : null}
        {message ? <p className="audio-workspace__alert audio-workspace__alert--success">{message}</p> : null}
        {error ? <p className="audio-workspace__alert audio-workspace__alert--error">{error}</p> : null}
        {runMessage ? <p className="audio-workspace__alert audio-workspace__alert--success">{runMessage}</p> : null}
        {runError ? <p className="audio-workspace__alert audio-workspace__alert--error">{runError}</p> : null}
        {latestLogUrl ? (
          <p className="audio-workspace__alert">
            <a className="link" href={latestLogUrl} target="_blank" rel="noreferrer">
              最新ログ (log.json)
            </a>
          </p>
        ) : null}
      </section>

      <div className="audio-workspace__main">
        <section className="audio-workspace__player-panel" aria-label="音声プレビュー">
        <div className="audio-workspace__player">
          {audioPlaybackUrl ? (
            <audio ref={audioRef} controls preload="metadata" src={audioPlaybackUrl} />
          ) : (
            <div className="audio-workspace__player-placeholder">音声がまだ生成されていません。</div>
          )}
        </div>
        <div className="audio-workspace__player-actions">
          <button
            type="button"
            className="workspace-button workspace-button--ghost workspace-button--compact"
            onClick={() => {
              setAudioReloadToken(Date.now());
              const audio = audioRef.current;
              if (audio) {
                audio.load();
              }
            }}
          >
            音声を再読み込み
          </button>
          {audioPlaybackUrl ? (
            <a className="workspace-button workspace-button--ghost workspace-button--compact" href={audioPlaybackUrl} target="_blank" rel="noreferrer">
              音声ダウンロード
            </a>
          ) : null}
            {srtDownloadUrl ? (
              <a className="workspace-button workspace-button--ghost workspace-button--compact" href={srtDownloadUrl} target="_blank" rel="noreferrer">
                字幕SRTを開く
              </a>
            ) : null}
            <button
              type="button"
              className="workspace-button workspace-button--primary workspace-button--compact"
              onClick={() => void handleRunTts()}
              disabled={runBusy}
            >
              {runBusy ? "音声再生成中…" : "音声を再生成 (TTS v2)"}
            </button>
          </div>
          <div className="audio-workspace__timeline" aria-label="タイムライン">
            <div className="audio-workspace__timeline-header">
              <span className="audio-workspace__timeline-title">タイムライン</span>
              <span className="audio-workspace__timeline-meta">{timelineMetaLabel}</span>
            </div>
            {timelineItems.length === 0 ? (
              <p className="audio-workspace__timeline-empty">字幕または台本を保存するとタイムラインが表示されます。</p>
            ) : (
              <ul className="audio-workspace__timeline-list">
                {timelineItems.map((entry) => (
                  <li key={`timeline-${entry.index}`}>
                    <button
                      type="button"
                      className={
                        activeTimelineIndex === entry.index
                          ? "audio-workspace__timeline-item audio-workspace__timeline-item--active"
                          : "audio-workspace__timeline-item"
                      }
                      onClick={() => handleTimelineSelect(entry)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          handleTimelineSelect(entry);
                        }
                      }}
                    >
                      <span className="audio-workspace__timeline-index">#{entry.index}</span>
                      <span className="audio-workspace__timeline-time">
                        {entry.start ? `${entry.start}${entry.end ? ` → ${entry.end}` : ""}` : "未タイムコード"}
                      </span>
                      <span className="audio-workspace__timeline-text">{entry.displayText || "テキスト未設定"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      {showSrtArea ? (
        <section className="audio-workspace__srt-panel" aria-label="字幕ソース">
          <div className="audio-workspace__srt-header">
            <h3>字幕（SRT）</h3>
            <span className="audio-workspace__srt-meta">{timelineEntries.length} ブロック</span>
            <p className="muted small-text">保存先: {displayedSrtPath}</p>
          </div>
          <textarea
            className="audio-workspace__srt-textarea"
            value={srtDraft}
            onChange={handleSrtChange}
            placeholder="SRT 形式の字幕を編集します。"
          />
          <div className="audio-workspace__editor-actions">
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => void handleReloadSrt()}
              disabled={srtReloading || refreshing}
            >
              {srtReloading ? "再取得中…" : "最新SRTを取得"}
            </button>
            <button
              type="button"
              className="workspace-button workspace-button--primary"
              onClick={handleSaveSrt}
              disabled={saveBusy || refreshing}
            >
              {saveBusy || refreshing ? "保存中…" : "字幕を保存"}
            </button>
          </div>
        </section>
      ) : null}

      <footer className="audio-workspace__footer">
        <div className="audio-workspace__status">
          <span>現在のステータス: {translateStatus(detail.status)}</span>
          <div className="audio-workspace__status-actions">
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => handleStatusUpdate("review")}
              disabled={statusUpdating}
            >
              音声レビュー待ちにする
            </button>
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => handleStatusUpdate("completed")}
              disabled={statusUpdating}
            >
              音声タスクを完了にする
            </button>
          </div>
        </div>
        <div className="audio-workspace__timestamps">
          <span>台本更新: {formatTimestamp(detail.updated_at) || "未更新"}</span>
          <span>音声更新: {formatTimestamp(detail.audio_updated_at) || "未生成"}</span>
        </div>
      </footer>
    </div>
  );
}
