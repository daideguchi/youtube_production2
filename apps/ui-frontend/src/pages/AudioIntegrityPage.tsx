import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  fetchAudioCheckLog,
  fetchAudioCheckRecent,
  fetchAudioIntegrity,
  fetchKnowledgeBase,
  type KnowledgeBaseResponse,
} from "../api/client";
import type { AudioCheckLog, AudioCheckRecentItem, AudioIntegrityItem } from "../api/types";

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
}

function formatSeconds(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}s`;
}

function verdictLabel(verdict: string): string {
  if (verdict === "match_kanji") return "一致 (漢字)";
  if (verdict === "llm_match_kanji") return "LLM承認 (漢字)";
  if (verdict === "llm_patched_action_a") return "修正 (一部カナ)";
  if (verdict === "kb_applied") return "辞書適用";
  if (verdict.includes("fallback")) return "全カナ (Fallback)";
  return verdict;
}

function verdictStyle(verdict: string): { color: string; borderColor: string; fontWeight?: "bold" } {
  if (verdict.includes("match")) return { color: "green", borderColor: "green" };
  if (verdict.includes("patched") || verdict.includes("fix")) return { color: "orange", borderColor: "orange", fontWeight: "bold" };
  if (verdict.includes("fallback")) return { color: "red", borderColor: "red" };
  if (verdict.includes("kb")) return { color: "blue", borderColor: "blue" };
  return { color: "gray", borderColor: "gray" };
}

type RouteParams = { channel?: string; video?: string };

export function AudioIntegrityPage() {
  const navigate = useNavigate();
  const params = useParams<RouteParams>();
  const [searchParams] = useSearchParams();

  const routeChannel = (params.channel ?? "").trim();
  const routeVideo = (params.video ?? "").trim();
  const queryChannel = (searchParams.get("channel") ?? "").trim();
  const queryVideo = (searchParams.get("video") ?? "").trim();

  const shouldRedirectToCanonical = !routeChannel && !routeVideo && Boolean(queryChannel && queryVideo);
  const channelId = (routeChannel || queryChannel || "").trim() || null;
  const videoId = (routeVideo || queryVideo || "").trim() || null;

  const [integrityItems, setIntegrityItems] = useState<AudioIntegrityItem[]>([]);
  const [integrityLoading, setIntegrityLoading] = useState(true);
  const [integrityError, setIntegrityError] = useState<string | null>(null);

  const [recentLogs, setRecentLogs] = useState<AudioCheckRecentItem[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState<string | null>(null);

  const [log, setLog] = useState<AudioCheckLog | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  const [kb, setKb] = useState<KnowledgeBaseResponse | null>(null);
  const [kbError, setKbError] = useState<string | null>(null);

  useEffect(() => {
    if (!shouldRedirectToCanonical) return;
    void navigate(`/audio-integrity/${encodeURIComponent(queryChannel)}/${encodeURIComponent(queryVideo)}`, { replace: true });
  }, [navigate, queryChannel, queryVideo, shouldRedirectToCanonical]);

  const refreshIntegrity = useCallback(async () => {
    setIntegrityLoading(true);
    setIntegrityError(null);
    try {
      setIntegrityItems(await fetchAudioIntegrity());
    } catch (error) {
      setIntegrityError(error instanceof Error ? error.message : String(error));
    } finally {
      setIntegrityLoading(false);
    }
  }, []);

  const refreshKnowledgeBase = useCallback(async () => {
    setKbError(null);
    try {
      setKb(await fetchKnowledgeBase());
    } catch (error) {
      setKbError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  const refreshRecentLogs = useCallback(async () => {
    setRecentLoading(true);
    setRecentError(null);
    try {
      setRecentLogs(await fetchAudioCheckRecent(30));
    } catch (error) {
      setRecentError(error instanceof Error ? error.message : String(error));
    } finally {
      setRecentLoading(false);
    }
  }, []);

  const refreshLog = useCallback(async () => {
    if (!channelId || !videoId || shouldRedirectToCanonical) {
      setLog(null);
      setLogError(null);
      setLogLoading(false);
      return;
    }
    setLogLoading(true);
    setLogError(null);
    try {
      setLog(await fetchAudioCheckLog(channelId, videoId));
    } catch (error) {
      setLog(null);
      setLogError(error instanceof Error ? error.message : String(error));
    } finally {
      setLogLoading(false);
    }
  }, [channelId, shouldRedirectToCanonical, videoId]);

  useEffect(() => {
    void refreshIntegrity();
    void refreshKnowledgeBase();
  }, [refreshIntegrity, refreshKnowledgeBase]);

  useEffect(() => {
    if (channelId && videoId) {
      void refreshLog();
      return;
    }
    void refreshRecentLogs();
  }, [channelId, refreshLog, refreshRecentLogs, videoId]);

  const selectedIntegrity = useMemo(() => {
    if (!channelId || !videoId) return null;
    return integrityItems.find((item) => item.channel === channelId && item.video === videoId) ?? null;
  }, [channelId, integrityItems, videoId]);

  const kbCount = kb?.words ? Object.keys(kb.words).length : 0;
  const headerTitle = channelId && videoId ? `音声整合性: ${channelId}-${videoId}` : "音声整合性";

  return (
    <div className="page audio-integrity-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">Audio</p>
          <h1>{headerTitle}</h1>
          <p className="page-lead">
            final の必須ファイル（Audio/SRT/TTS入力）と、音声チェックログ（log.json）を確認します。
          </p>
        </div>
        <div className="capcut-edit-page__actions">
          <button type="button" className="button button--ghost" onClick={() => void refreshIntegrity()} disabled={integrityLoading}>
            {integrityLoading ? "更新中…" : "一覧更新"}
          </button>
          <button type="button" className="button button--ghost" onClick={() => void refreshKnowledgeBase()}>
            辞書更新
          </button>
          {channelId && videoId ? (
            <button type="button" className="button" onClick={() => void refreshLog()} disabled={logLoading}>
              {logLoading ? "読込中…" : "ログ更新"}
            </button>
          ) : (
            <button type="button" className="button" onClick={() => void refreshRecentLogs()} disabled={recentLoading}>
              {recentLoading ? "読込中…" : "recent更新"}
            </button>
          )}
        </div>
      </header>

      {integrityError ? <div className="main-alert main-alert--error">{integrityError}</div> : null}
      {kbError ? <div className="main-alert main-alert--error">{kbError}</div> : null}
      {recentError ? <div className="main-alert main-alert--error">{recentError}</div> : null}
      {logError ? <div className="main-alert main-alert--error">{logError}</div> : null}

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
            <h2>1) final 整合（一覧）</h2>
            <div className="main-status">
              <span className="status-chip">items: {integrityItems.length}</span>
              <span className="status-chip">
                KB: {kbCount} words（<Link to="/dictionary">辞書</Link>）
              </span>
            </div>
          </div>

          {integrityLoading ? (
            <div className="main-alert">読込中…</div>
          ) : integrityItems.length === 0 ? (
            <div className="main-alert">データがありません。</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
                <thead>
                  <tr style={{ textAlign: "left" }}>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>Episode</th>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>Missing</th>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>Audio</th>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>SRT</th>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>Δ</th>
                    <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>Links</th>
                  </tr>
                </thead>
                <tbody>
                  {integrityItems.map((item) => {
                    const hasIssues = item.missing.length > 0 || (item.duration_diff ?? 0) > 0.2;
                    return (
                      <tr
                        key={`${item.channel}-${item.video}`}
                        style={{ borderBottom: "1px solid #eee", background: hasIssues ? "#fff7ed" : "#fff" }}
                      >
                        <td style={{ padding: "8px" }}>
                          <Link to={`/audio-integrity/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.video)}`}>
                            <strong>
                              {item.channel}-{item.video}
                            </strong>
                          </Link>
                        </td>
                        <td style={{ padding: "8px" }}>
                          {item.missing.length ? (
                            <span style={{ color: "#b45309" }}>{item.missing.join(", ")}</span>
                          ) : (
                            <span style={{ color: "#16a34a" }}>OK</span>
                          )}
                        </td>
                        <td style={{ padding: "8px" }}>{formatSeconds(item.audio_duration)}</td>
                        <td style={{ padding: "8px" }}>{formatSeconds(item.srt_duration)}</td>
                        <td style={{ padding: "8px" }}>{formatSeconds(item.duration_diff)}</td>
                        <td style={{ padding: "8px" }}>
                          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                            {item.audio_path ? (
                              <a href={item.audio_path} className="muted" target="_blank" rel="noreferrer">
                                audio
                              </a>
                            ) : null}
                            {item.srt_path ? (
                              <a href={item.srt_path} className="muted" target="_blank" rel="noreferrer">
                                srt
                              </a>
                            ) : null}
                            {item.b_text_path ? (
                              <a href={item.b_text_path} className="muted" target="_blank" rel="noreferrer">
                                tts_input
                              </a>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {channelId && videoId ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
              <h2>2) 音声チェックログ（log.json）</h2>
              <Link to="/audio-integrity" className="button button--ghost">
                一覧へ戻る
              </Link>
            </div>

            {selectedIntegrity ? (
              <div className="main-status" style={{ marginTop: 10 }}>
                <span className="status-chip">
                  missing: {selectedIntegrity.missing.length ? selectedIntegrity.missing.join(", ") : "OK"}
                </span>
                <span className="status-chip">
                  audio: {formatSeconds(selectedIntegrity.audio_duration)} / srt: {formatSeconds(selectedIntegrity.srt_duration)} / Δ:{" "}
                  {formatSeconds(selectedIntegrity.duration_diff)}
                </span>
              </div>
            ) : null}

            {logLoading ? <div className="main-alert">読込中…</div> : null}
            {!logLoading && log === null ? (
              <div className="main-alert">ログが見つかりません（Strict pipeline未実行の可能性）。</div>
            ) : null}

            {log ? (
              <div style={{ display: "grid", gap: 10 }}>
                <div className="main-status">
                  <span className="status-chip">engine: {log.engine ?? "—"}</span>
                  <span className="status-chip">
                    generated: {log.timestamp ? formatDateTime(new Date(log.timestamp * 1000).toISOString()) : "—"}
                  </span>
                  <span className="status-chip">segments: {log.segments?.length ?? 0}</span>
                </div>

                <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
                    <thead style={{ position: "sticky", top: 0, backgroundColor: "#f9f9f9", zIndex: 1 }}>
                      <tr style={{ textAlign: "left" }}>
                        <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40px" }}>#</th>
                        <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40%" }}>
                          テキスト (漢字) / MeCab
                        </th>
                        <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40%" }}>
                          最終読み / Voicevox
                        </th>
                        <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>判定</th>
                        <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>ポーズ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(log.segments || []).map((seg, idx) => {
                        const modified = seg.text !== seg.reading;
                        const style = verdictStyle(seg.verdict);
                        return (
                          <tr
                            key={idx}
                            style={{ backgroundColor: seg.heading ? "#eef6fc" : "white", borderBottom: "1px solid #eee" }}
                          >
                            <td style={{ padding: "8px" }}>{idx + 1}</td>
                            <td style={{ padding: "8px" }}>
                              <div style={{ fontWeight: seg.heading ? "bold" : "normal" }}>{seg.text}</div>
                              <div style={{ fontSize: "0.75rem", color: "#888", marginTop: "2px" }}>MeCab: {seg.mecab}</div>
                            </td>
                            <td style={{ padding: "8px" }}>
                              <div
                                style={{
                                  fontWeight: modified ? "bold" : "normal",
                                  color: modified ? "#d32f2f" : "inherit",
                                }}
                              >
                                {seg.reading}
                              </div>
                              <div style={{ fontSize: "0.75rem", color: "#666", marginTop: "2px" }}>Orig: {seg.voicevox}</div>
                            </td>
                            <td style={{ padding: "8px" }}>
                              <span
                                style={{
                                  border: `1px solid ${style.borderColor}`,
                                  color: style.color,
                                  padding: "2px 6px",
                                  borderRadius: "12px",
                                  fontSize: "0.75rem",
                                  whiteSpace: "nowrap",
                                  fontWeight: style.fontWeight ?? "normal",
                                }}
                              >
                                {verdictLabel(seg.verdict)}
                              </span>
                            </td>
                            <td style={{ padding: "8px" }}>
                              {seg.pre && seg.pre > 0 ? (
                                <span style={{ display: "block", fontSize: "0.75rem", color: "#666" }}>Pre {seg.pre}s</span>
                              ) : null}
                              {seg.post && seg.post > 0 ? (
                                <span style={{ display: "block", fontSize: "0.75rem", color: "#666" }}>Post {seg.post}s</span>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
        </section>
      ) : (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <h2>2) recent（log.json）</h2>
            <p className="shell-panel__subtitle">直近で生成された音声チェックログ（workspaces/audio/final/**/log.json）</p>
            {recentLoading ? (
              <div className="main-alert">読込中…</div>
            ) : recentLogs.length === 0 ? (
              <div className="main-alert">生成済みログが見つかりません。</div>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
                {recentLogs.map((item) => (
                  <li key={`${item.channel}-${item.video}`}>
                    <Link
                      to={`/audio-integrity/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.video)}`}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 12,
                        padding: "10px 12px",
                        borderRadius: 10,
                        border: "1px solid #e2e8f0",
                        background: "#fff",
                        textDecoration: "none",
                        color: "inherit",
                      }}
                    >
                      <span>
                        <strong>
                          {item.channel}-{item.video}
                        </strong>
                      </span>
                      <span className="muted">{formatDateTime(item.updated_at ?? null)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
