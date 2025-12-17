import { useEffect, useMemo, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";

import { fetchVideoProjects } from "../api/client";
import type { VideoProjectSummary } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";
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

export function WorkflowPage() {
  const {
    channels,
    channelsLoading,
    channelsError,
    videos,
    videosLoading,
    videosError,
    selectedChannel,
    selectedVideo,
    videoDetail,
    detailLoading,
    detailError,
  } = useOutletContext<ShellOutletContext>();

  const [searchParams, setSearchParams] = useSearchParams();
  const [videoProjects, setVideoProjects] = useState<VideoProjectSummary[] | null>(null);
  const [videoProjectsLoading, setVideoProjectsLoading] = useState(false);
  const [videoProjectsError, setVideoProjectsError] = useState<string | null>(null);

  const channel = useMemo(() => normalizeChannel(selectedChannel), [selectedChannel]);
  const video = useMemo(() => normalizeVideo(selectedVideo), [selectedVideo]);
  const episodeId = channel && video ? `${channel}-${video}` : null;
  const expectedSrtRelPath = useMemo(() => {
    if (!channel || !video || !episodeId) return "";
    return `${channel}/${video}/${episodeId}.srt`;
  }, [channel, video, episodeId]);

  useEffect(() => {
    let cancelled = false;
    setVideoProjectsLoading(true);
    setVideoProjectsError(null);
    fetchVideoProjects()
      .then((items) => {
        if (cancelled) return;
        setVideoProjects(items);
      })
      .catch((error) => {
        if (cancelled) return;
        setVideoProjects(null);
        setVideoProjectsError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (cancelled) return;
        setVideoProjectsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const matchingVideoProjects = useMemo(() => {
    if (!episodeId || !videoProjects) return [];
    const needle = episodeId.toUpperCase();
    const list = videoProjects
      .filter((p) => (p.id || "").toUpperCase().startsWith(needle))
      .slice()
      .sort((a, b) => (b.last_updated || "").localeCompare(a.last_updated || "") || b.id.localeCompare(a.id));
    return list;
  }, [episodeId, videoProjects]);

  const audioState = useMemo(() => {
    if (!videoDetail) return "pending";
    return resolveAudioSubtitleState(videoDetail);
  }, [videoDetail]);

  const scriptOk = Boolean(videoDetail?.assembled_human_path || videoDetail?.assembled_path);
  const audioOk = audioState === "completed";
  const videoOk = matchingVideoProjects.length > 0;

  const episodeBaseLink =
    channel && video ? `/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}` : null;
  const scriptEditLink = episodeBaseLink ? `${episodeBaseLink}?tab=script` : "/channel-workspace";
  const audioEditLink = episodeBaseLink ? `${episodeBaseLink}?tab=audio` : "/channel-workspace";
  const audioIntegrityLink = channel && video ? `/audio-integrity?channel=${channel}&video=${video}` : "/audio-integrity";
  const planningLink =
    channel && video ? `/progress?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}` : "/progress";
  const ttsListLink = channel ? `/audio-tts-v2?channel=${encodeURIComponent(channel)}` : "/audio-tts-v2";
  const capcutDraftLink = expectedSrtRelPath
    ? `/capcut-edit/draft?srt=${encodeURIComponent(expectedSrtRelPath)}`
    : "/capcut-edit/draft";
  const videoProductionLink =
    channel && video && episodeId
      ? `/capcut-edit/production?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}&project=${encodeURIComponent(episodeId)}`
      : "/capcut-edit/production";
  const studioLink =
    channel && video
      ? `/studio?channel=${encodeURIComponent(channel)}&video=${encodeURIComponent(video)}`
      : "/studio";

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
    <div className="page workflow-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">制作フロー</p>
          <h1>企画 → 台本 → 音声 → 動画</h1>
          <p className="page-lead">1本のエピソードを迷わず前に進めるための一本道ビューです。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button" to={studioLink}>
            Episode Studio
          </Link>
          <Link className="button button--ghost" to="/dashboard">
            ← ダッシュボード
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>1) エピソードを選ぶ</h2>
          <p className="shell-panel__subtitle">チャンネルと動画番号を選ぶと、台本/音声/動画の状態がまとまって見えます。</p>
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

          {channelsError ? <div className="main-alert main-alert--error">{channelsError}</div> : null}
          {videosError ? <div className="main-alert main-alert--error">{videosError}</div> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>2) 現在地</h2>
          <p className="shell-panel__subtitle">次にやるべき作業だけを追えるように、工程を最小単位でまとめています。</p>

          <div className="main-status" style={{ marginTop: 0 }}>
            <span className="status-chip">{episodeId ? `対象: ${episodeId}` : "対象: 未選択"}</span>
            <span className={`status-chip ${scriptOk ? "" : "status-chip--warning"}`}>台本: {scriptOk ? "OK" : "未確認"}</span>
            <span className={`status-chip ${audioOk ? "" : "status-chip--warning"}`}>音声/SRT: {audioOk ? "OK" : audioState}</span>
            <span className={`status-chip ${videoOk ? "" : "status-chip--warning"}`}>動画ドラフト: {videoOk ? "OK" : "未作成"}</span>
          </div>

          {detailError ? <div className="main-alert main-alert--error">{detailError}</div> : null}
          {detailLoading ? <div className="main-alert">詳細を読み込み中です…</div> : null}
          {!episodeId ? <div className="main-alert">まずエピソードを選択してください。</div> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>3) 次のアクション</h2>
          <p className="shell-panel__subtitle">各工程のUIへ最短で飛べる導線です。</p>

          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>企画 / タイトル</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                企画行（タイトル/タグ/サムネ/プロンプト/進捗）を最初に整えると、下流の迷いが減ります。
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={planningLink}>
                  企画CSVを開く
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>台本</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                {scriptOk ? "既に台本が存在します。必要なら修正へ。" : "まだ台本が見つかりません。生成/作成へ。"}
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
                {audioOk
                  ? "音声/SRTは final に揃っています。ズレや誤読がないか確認へ。"
                  : "音声/SRTが未完了です。TTS生成や字幕調整へ。"}
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={audioEditLink}>
                  音声/字幕を開く
                </Link>
                <Link className="button button--ghost" to={audioIntegrityLink}>
                  整合性チェック
                </Link>
                <Link className="button button--ghost" to={ttsListLink}>
                  TTS生成（一覧）
                </Link>
              </div>
            </div>

            <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 14 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>動画（CapCut）</h3>
              <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
                {videoOk
                  ? `既存プロジェクト: ${matchingVideoProjects[0]?.id ?? "(unknown)"}`
                  : "まだプロジェクトがありません。final SRT からドラフトを作成へ。"}
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link className="button" to={capcutDraftLink}>
                  新規ドラフト作成
                </Link>
                <Link className="button button--ghost" to={videoProductionLink}>
                  プロジェクト管理
                </Link>
                <Link className="button button--ghost" to="/capcut-edit">
                  CapCut編集メニュー
                </Link>
              </div>
              <div style={{ marginTop: 10, fontSize: 12, color: "var(--color-text-muted)" }}>
                {videoProjectsLoading ? "video projects を読み込み中…" : null}
                {videoProjectsError ? `video projects 取得失敗: ${videoProjectsError}` : null}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>4) 参照（SoT）</h2>
          <p className="shell-panel__subtitle">どこが正本か迷ったらここだけ見ればOKです。</p>
          <ul>
            <li>企画（SoT）: workspaces/planning/channels/CHxx.csv</li>
            <li>台本（SoT）: workspaces/scripts/&lt;CH&gt;/&lt;NNN&gt;/status.json</li>
            <li>音声/SRT（SoT）: workspaces/audio/final/&lt;CH&gt;/&lt;NNN&gt;/</li>
            <li>動画run（SoT）: workspaces/video/runs/&lt;run_id&gt;/</li>
          </ul>
          {expectedSrtRelPath ? (
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--color-text-muted)" }}>
              期待するSRT相対パス: <code>{expectedSrtRelPath}</code>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
