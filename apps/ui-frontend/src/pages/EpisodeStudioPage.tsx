import { useMemo } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";

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
          </div>

          {!episodeId ? <div className="main-alert">まずエピソードを選択してください。</div> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>3) 次の導線</h2>
          <p className="shell-panel__subtitle">各工程の画面へ最短で移動します（実行系/ログ統合は次のフェーズで拡張）。</p>

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
    </div>
  );
}

