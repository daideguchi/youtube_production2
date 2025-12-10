import { useEffect, useMemo, useState } from "react";
import { Player } from "@remotion/player";
import { loadRemotionInput } from "../utils/remotionInput";
import { RemotionRoot } from "../remotion/RemotionRoot";
import type { SubtitleCue, Belt } from "../types/remotionTypes";

const DEFAULT_RUN = "192";

export function RemotionPreviewPage() {
  const [runId, setRunId] = useState(DEFAULT_RUN);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [belts, setBelts] = useState<Belt[]>([]);
  const [episode, setEpisode] = useState<string | undefined>();
  const [imageCues, setImageCues] = useState<any[]>([]);
  const [audioPath, setAudioPath] = useState<string | undefined>();
  const [subtitles, setSubtitles] = useState<SubtitleCue[] | undefined>();
  const [mp4Url, setMp4Url] = useState<string | undefined>();
  const [vttUrl, setVttUrl] = useState<string | undefined>();

  useEffect(() => {
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await loadRemotionInput(runId);
        setDuration(data.totalDuration ?? 0);
        setBelts(data.belts ?? []);
        setEpisode(data.episode);
        setImageCues(data.imageCues ?? []);
        setAudioPath(data.audioPath);
        setSubtitles(data.subtitles);
        setMp4Url(data.mp4Url);

        if (data.subtitles && data.subtitles.length > 0) {
          const vtt = subtitlesToVtt(data.subtitles);
          const blob = new Blob([vtt], { type: "text/vtt" });
          const url = URL.createObjectURL(blob);
          setVttUrl(url);
        } else {
          setVttUrl(undefined);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      } finally {
        setLoading(false);
      }
    })();
  }, [runId]);

  const inputProps = useMemo(
    () => ({
      runId,
      episode: episode ?? "",
      belts,
      imageCues,
      audioPath,
      subtitles,
    }),
    [runId, episode, belts, imageCues, audioPath, subtitles],
  );

  const canUseMp4 = Boolean(mp4Url);

  return (
    <div className="remotion-preview">
      <div className="remotion-preview__header">
        <div>
          <h1>Remotion プレビュー</h1>
          <p>CapCut とは独立。`remotion/input/&lt;id&gt;` の素材をそのまま再生して帯・字幕を確認します。</p>
        </div>
        <div className="remotion-preview__controls">
          <label>
            Run ID:
            <input
              value={runId}
              onChange={(e) => setRunId(e.target.value.trim())}
              placeholder="例: 192"
            />
          </label>
          <span className="remotion-preview__meta">
            {duration ? `総尺: ${duration.toFixed(1)}s` : "総尺: —"}
          </span>
        </div>
      </div>

      {loading && <div className="remotion-preview__status">読み込み中…</div>}
      {error && <div className="remotion-preview__error">エラー: {error}</div>}

      {!loading && !error && (
        <div className="remotion-preview__player">
          {canUseMp4 ? (
            <video
              key={mp4Url}
              src={mp4Url}
              width={960}
              height={540}
              controls
              crossOrigin="anonymous"
              style={{ background: "#000", borderRadius: 8 }}
            >
              {vttUrl && <track default kind="subtitles" src={vttUrl} srcLang="ja" label="日本語" />}
            </video>
          ) : (
            <Player
              component={RemotionRoot}
              inputProps={inputProps}
              durationInFrames={Math.max(1, Math.round((duration || 1) * 30))}
              fps={30}
              compositionWidth={960}
              compositionHeight={540}
              controls
            />
          )}
        </div>
      )}
    </div>
  );
}

function subtitlesToVtt(subs: SubtitleCue[]): string {
  const header = "WEBVTT\n\n";
  const body = subs
    .map((c, i) => {
      const start = toTimestamp(c.start);
      const end = toTimestamp(c.end);
      return `${i + 1}\n${start} --> ${end}\n${c.text}\n`;
    })
    .join("\n");
  return header + body;
}

function toTimestamp(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const ms = Math.floor((sec - Math.floor(sec)) * 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  const pad3 = (n: number) => String(n).padStart(3, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad3(ms)}`;
}
