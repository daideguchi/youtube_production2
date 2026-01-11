import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fetchChannels, fetchVideos } from "../api/client";
import type { ChannelSummary, VideoSummary } from "../api/types";
import { apiUrl } from "../api/baseUrl";

type RunDirItem = {
  name: string;
  path: string;
  mtime?: number;
  episode_token?: string | null;
  vrew_prompts_exists?: boolean;
  vrew_prompts_path?: string | null;
};

const COMMON_PROMPT_LIMIT = 100;
const INDIVIDUAL_PROMPT_CHUNK_LIMIT = 8000;
const COMMON_PROMPT_STORAGE_KEY = "ui.vrew.commonPrompt";

type VrewPromptsState = {
  status: "idle" | "loading" | "ready" | "error";
  runDir: string;
  promptsPath: string;
  lineCount: number;
  textLines: string;
  textKuten: string;
  error?: string;
};

const initialPromptsState: VrewPromptsState = {
  status: "idle",
  runDir: "",
  promptsPath: "",
  lineCount: 0,
  textLines: "",
  textKuten: "",
  error: undefined,
};

function safeLocalGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore
  }
}

function normalizeCommonPrompt(raw: string): string {
  return sanitizePlainText(raw);
}

function sanitizePlainText(raw: string): string {
  let text = String(raw ?? "");
  try {
    text = text.normalize("NFKC");
  } catch {
    // ignore
  }
  text = text
    .replace(/\r\n/g, "\n")
    .replace(/\bAI\b/gi, "人工知能")
    .replace(/\b2D\b/gi, "二次元")
    .replace(/\b3D\b/gi, "三次元")
    .replace(/\b1\s*:\s*1\b/g, "正方形")
    .replace(/\b1\s*x\s*1\b/gi, "正方形")
    .replace(/[．.]/g, "。")
    .replace(/[，,]/g, "、")
    .replace(/[!?！？]/g, "。")
    .replace(/・/g, "、")
    .replace(/[「」『』【】\[\]\(\){}<>]/g, " ")
    .replace(/[“”‘’"'`]/g, " ")
    .replace(/[＃#＠@％%＆&＊*＿_＋+＝=＾^〜~￥$]/g, " ")
    .replace(/[:;：；]/g, " ")
    .replace(/[|\\/]/g, " ")
    .replace(/[•·●◆◇■□▪︎]/g, " ")
    .replace(/[—–―‐‑‒]/g, " ")
    .replace(/[…]/g, " ")
    .replace(/[A-Za-z]/g, " ")
    .replace(/[^0-9０-９ぁ-ゔゞァ-ヴー々〆〤一-龥、。 \n]/g, " ")
    .replace(/\u3000/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text;
}

function normalizeIndividualPrompts(raw: string): { lines: string[]; textLines: string; textKuten: string } {
  const base = String(raw ?? "").replace(/\r\n/g, "\n").trim();
  if (!base) {
    return { lines: [], textLines: "", textKuten: "" };
  }
  const cooked = base.replace(/[．.]/g, "。").replace(/[!?！？]/g, "。");
  const parts = cooked
    .split(/[。\n]+/g)
    .map((s) => sanitizePlainText(s))
    .filter(Boolean);
  const lines = parts.map((p) => (p.endsWith("。") ? p : `${p}。`));
  return {
    lines,
    textLines: lines.join("\n"),
    textKuten: lines.join(""),
  };
}

type PromptChunk = {
  lines: string[];
  textLines: string;
  textKuten: string;
  lineCount: number;
  charCount: number;
};

function chunkPromptLines(lines: string[], maxChars: number): PromptChunk[] {
  const limit = Number(maxChars || 0);
  if (!Number.isFinite(limit) || limit <= 0) return [];

  const chunks: PromptChunk[] = [];
  let currentLines: string[] = [];
  let currentChars = 0;

  const pushChunk = () => {
    if (currentLines.length === 0) return;
    const textLines = currentLines.join("\n");
    const textKuten = currentLines.join("");
    chunks.push({
      lines: currentLines,
      textLines,
      textKuten,
      lineCount: currentLines.length,
      charCount: textKuten.length,
    });
    currentLines = [];
    currentChars = 0;
  };

  for (const rawLine of lines) {
    const line = String(rawLine || "");
    const nextLen = line.length;
    if (currentLines.length === 0) {
      currentLines = [line];
      currentChars = nextLen;
      continue;
    }
    if (currentChars + nextLen <= limit) {
      currentLines.push(line);
      currentChars += nextLen;
      continue;
    }
    pushChunk();
    currentLines = [line];
    currentChars = nextLen;
  }
  pushChunk();
  return chunks;
}

function normalizeChannelCode(raw: string): string {
  return String(raw ?? "").trim().toUpperCase();
}

function normalizeVideoNumber(raw: string): string {
  const text = String(raw ?? "").trim();
  if (!text) return "";
  if (/^\d+$/.test(text)) return text.padStart(3, "0");
  return text;
}

function buildEpisodeToken(channel: string, video: string): string {
  const ch = normalizeChannelCode(channel);
  const v = normalizeVideoNumber(video);
  if (!ch || !v) return "";
  return `${ch}-${v}`;
}

function extractEpisodeTokenFromText(raw: string): string | null {
  const text = String(raw ?? "");
  const match = text.match(/(CH\\d{2})[-_](\\d{3})/i);
  if (!match) return null;
  return `${match[1].toUpperCase()}-${match[2]}`;
}

function resolveEpisodeTokenForRunDir(item: RunDirItem): string | null {
  const direct = item.episode_token ? String(item.episode_token) : "";
  if (direct) return direct.toUpperCase();
  return (
    extractEpisodeTokenFromText(item.name)?.toUpperCase() ??
    extractEpisodeTokenFromText(item.path)?.toUpperCase() ??
    null
  );
}

function compareChannelCode(aRaw: string, bRaw: string): number {
  const a = normalizeChannelCode(aRaw);
  const b = normalizeChannelCode(bRaw);
  const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
  const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
  const aOk = Number.isFinite(an);
  const bOk = Number.isFinite(bn);
  if (aOk && bOk) return an - bn;
  if (aOk) return -1;
  if (bOk) return 1;
  return a.localeCompare(b, "ja-JP");
}

export function CapcutVrewPage() {
  const [searchParams] = useSearchParams();

  const [channels, setChannels] = useState<ChannelSummary[]>([]);
  const [channelsStatus, setChannelsStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [videosStatus, setVideosStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [videosError, setVideosError] = useState<string | null>(null);

  const [channel, setChannel] = useState(() => normalizeChannelCode(safeLocalGet("ui.channel.selected") ?? ""));
  const [video, setVideo] = useState("");
  const [videoFilter, setVideoFilter] = useState("");

  const [runDirs, setRunDirs] = useState<RunDirItem[]>([]);
  const [runDirsStatus, setRunDirsStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [runDirsError, setRunDirsError] = useState<string | null>(null);
  const [runDir, setRunDir] = useState("");
  const [manualRunDirFilter, setManualRunDirFilter] = useState("");
  const [toast, setToast] = useState("");
  const [prompts, setPrompts] = useState<VrewPromptsState>(initialPromptsState);
  const [commonPrompt, setCommonPrompt] = useState(() => safeLocalGet(COMMON_PROMPT_STORAGE_KEY) ?? "");
  const [individualPromptsRaw, setIndividualPromptsRaw] = useState("");

  const commonPromptNormalized = useMemo(() => normalizeCommonPrompt(commonPrompt), [commonPrompt]);
  const commonPromptCount = commonPromptNormalized.length;
  const commonPromptOver = commonPromptCount > COMMON_PROMPT_LIMIT;

  const individualNormalized = useMemo(() => normalizeIndividualPrompts(individualPromptsRaw), [individualPromptsRaw]);
  const individualLineCount = individualNormalized.lines.length;
  const individualPasteText = individualNormalized.textKuten;
  const individualTotalChars = individualPasteText.length;
  const individualChunks = useMemo(
    () => chunkPromptLines(individualNormalized.lines, INDIVIDUAL_PROMPT_CHUNK_LIMIT),
    [individualNormalized.lines]
  );
  const individualChunkCount = individualChunks.length;
  const individualChunkOver = individualChunks.some((c) => c.charCount > INDIVIDUAL_PROMPT_CHUNK_LIMIT);

  const episodeToken = useMemo(() => buildEpisodeToken(channel, video), [channel, video]);
  const selectedVideoSummary = useMemo(() => videos.find((v) => v.video === normalizeVideoNumber(video)) ?? null, [videos, video]);

  const runDirStats = useMemo(() => {
    const byToken = new Map<string, RunDirItem[]>();
    const bestByToken = new Map<string, RunDirItem>();
    const readyTokens = new Set<string>();
    const startedByChannel = new Map<string, Set<string>>();
    const readyByChannel = new Map<string, Set<string>>();

    for (const item of runDirs) {
      const token = resolveEpisodeTokenForRunDir(item);
      if (!token) continue;
      const list = byToken.get(token) ?? [];
      list.push(item);
      byToken.set(token, list);
    }

    byToken.forEach((list, token) => {
      const sorted = [...list].sort((a, b) => (Number(b.mtime || 0) || 0) - (Number(a.mtime || 0) || 0));
      const best = sorted.find((it) => it.vrew_prompts_exists) ?? sorted[0];
      if (best) bestByToken.set(token, best);

      const channelCode = token.split("-")[0];
      const startedSet = startedByChannel.get(channelCode) ?? new Set<string>();
      startedSet.add(token);
      startedByChannel.set(channelCode, startedSet);

      const hasPrompts = sorted.some((it) => it.vrew_prompts_exists);
      if (hasPrompts) {
        readyTokens.add(token);
        const readySet = readyByChannel.get(channelCode) ?? new Set<string>();
        readySet.add(token);
        readyByChannel.set(channelCode, readySet);
      }
    });

    return { byToken, bestByToken, readyTokens, startedByChannel, readyByChannel };
  }, [runDirs]);

  const channelProgress = useMemo(() => {
    const items = (channels || []).map((c) => {
      const code = normalizeChannelCode(c.code);
      const total = Number(c.video_count || 0) || 0;
      const started = runDirStats.startedByChannel.get(code)?.size ?? 0;
      const ready = runDirStats.readyByChannel.get(code)?.size ?? 0;
      const pct = total > 0 ? Math.min(1, Math.max(0, ready / total)) : 0;
      return { code, name: c.name ?? "", total, started, ready, pct };
    });
    items.sort((a, b) => compareChannelCode(a.code, b.code));
    return items;
  }, [channels, runDirStats.readyByChannel, runDirStats.startedByChannel]);

  const overallProgress = useMemo(() => {
    return channelProgress.reduce(
      (acc, item) => {
        acc.total += item.total;
        acc.started += item.started;
        acc.ready += item.ready;
        return acc;
      },
      { total: 0, started: 0, ready: 0 }
    );
  }, [channelProgress]);

  const applyChannelSelection = (nextChannel: string) => {
    setChannel(normalizeChannelCode(nextChannel));
    setVideo("");
    setVideoFilter("");
    setRunDir("");
    setPrompts({ ...initialPromptsState, status: "idle", runDir: "" });
    setIndividualPromptsRaw("");
  };

  const selectedChannelProgress = useMemo(() => {
    const code = normalizeChannelCode(channel);
    return channelProgress.find((it) => it.code === code) ?? null;
  }, [channel, channelProgress]);

  const applyVideoSelection = (nextVideo: string) => {
    const v = normalizeVideoNumber(nextVideo);
    setVideo(v);
    const token = buildEpisodeToken(channel, v).toUpperCase();
    const best = runDirStats.bestByToken.get(token);
    const bestPath = best?.path ?? "";
    setRunDir(bestPath);
    setPrompts({ ...initialPromptsState, status: "idle", runDir: bestPath });
    setIndividualPromptsRaw("");
  };

  const filteredVideos = useMemo(() => {
    const keyword = videoFilter.trim().toLowerCase();
    if (!keyword) return videos;
    return videos.filter((v) => {
      const title = String(v.title ?? "").toLowerCase();
      return v.video.toLowerCase().includes(keyword) || title.includes(keyword);
    });
  }, [videoFilter, videos]);

  const runDirCandidates = useMemo(() => {
    if (!episodeToken) return [];
    const token = episodeToken.toUpperCase();
    const direct = runDirStats.byToken.get(token);
    const tokenAlt = token.replace("-", "_");
    const fallback = runDirs.filter((it) => {
      const name = it.name.toUpperCase();
      const path = it.path.toUpperCase();
      return name.includes(token) || name.includes(tokenAlt) || path.includes(token) || path.includes(tokenAlt);
    });
    const candidates = direct && direct.length > 0 ? [...direct] : fallback;
    candidates.sort((a, b) => {
      const ap = a.vrew_prompts_exists ? 1 : 0;
      const bp = b.vrew_prompts_exists ? 1 : 0;
      if (ap !== bp) return bp - ap;
      return (Number(b.mtime || 0) || 0) - (Number(a.mtime || 0) || 0);
    });
    return candidates;
  }, [episodeToken, runDirs, runDirStats.byToken]);

  const selectedRunDirItem = useMemo(() => {
    if (!runDir) return null;
    return runDirs.find((it) => it.path === runDir) ?? null;
  }, [runDir, runDirs]);

  const selectedRunDirName = useMemo(() => {
    if (!runDir) return "";
    return selectedRunDirItem?.name || runDir.split(/[\\/]/).pop() || runDir;
  }, [runDir, selectedRunDirItem]);

  const selectedRunDirKnownNoPrompts = selectedRunDirItem?.vrew_prompts_exists === false;

  const filteredAllRunDirs = useMemo(() => {
    const keyword = manualRunDirFilter.trim().toLowerCase();
    if (!keyword) return runDirs;
    return runDirs.filter((it) => it.name.toLowerCase().includes(keyword) || it.path.toLowerCase().includes(keyword));
  }, [manualRunDirFilter, runDirs]);

  useEffect(() => {
    const qChannel = normalizeChannelCode(searchParams.get("channel") || "");
    const qVideo = normalizeVideoNumber(searchParams.get("video") || searchParams.get("no") || "");
    if (qChannel) setChannel(qChannel);
    if (qVideo) setVideo(qVideo);
  }, [searchParams]);

  const refreshRunDirs = async () => {
    setRunDirsStatus("loading");
    setRunDirsError(null);
    try {
      const runDirsResp = await fetch(apiUrl("/api/swap/run-dirs?limit=5000")).then((r) => r.json());
      const list: RunDirItem[] = runDirsResp?.items || [];
      list.sort((a, b) => (Number(b.mtime || 0) || 0) - (Number(a.mtime || 0) || 0));
      setRunDirs(list);
      setRunDirsStatus("ready");
      return list;
    } catch (e: any) {
      setRunDirsStatus("error");
      setRunDirsError(e?.message || "run_dir の取得に失敗しました");
      setToast(e?.message || "run_dir の取得に失敗しました");
      return [];
    }
  };

  useEffect(() => {
    setChannelsStatus("loading");
    setChannelsError(null);
    fetchChannels()
      .then((list) => {
        setChannels(list || []);
        setChannelsStatus("ready");
      })
      .catch((e: any) => {
        setChannelsStatus("error");
        setChannelsError(e?.message || "チャンネル取得に失敗しました");
      });
  }, []);

  useEffect(() => {
    refreshRunDirs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (channelsStatus !== "ready") return;
    if (channels.length === 0) return;
    const current = normalizeChannelCode(channel);
    if (current && channels.some((c) => normalizeChannelCode(c.code) === current)) {
      return;
    }
    setChannel(normalizeChannelCode(channels[0].code));
  }, [channels, channelsStatus, channel]);

  useEffect(() => {
    const ch = normalizeChannelCode(channel);
    if (!ch) {
      setVideos([]);
      setVideosStatus("idle");
      setVideosError(null);
      return;
    }
    setVideosStatus("loading");
    setVideosError(null);
    fetchVideos(ch)
      .then((list) => {
        const normalized = list || [];
        setVideos(normalized);
        setVideosStatus("ready");
        const desired = normalizeVideoNumber(video);
        if (desired && !normalized.some((v) => v.video === desired)) {
          setVideo("");
        }
      })
      .catch((e: any) => {
        setVideos([]);
        setVideosStatus("error");
        setVideosError(e?.message || "動画一覧の取得に失敗しました");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  useEffect(() => {
    safeLocalSet(COMMON_PROMPT_STORAGE_KEY, commonPrompt);
  }, [commonPrompt]);

  useEffect(() => {
    if (!episodeToken) return;
    if (runDirCandidates.length === 0) return;
    if (runDirCandidates.some((it) => it.path === runDir)) return;
    const picked = runDirCandidates[0].path;
    setRunDir(picked);
    setPrompts({ ...initialPromptsState, status: "idle", runDir: picked });
    setIndividualPromptsRaw("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episodeToken, runDirCandidates]);

  const loadVrewPrompts = async (targetRunDir: string) => {
    const resolved = String(targetRunDir || "").trim();
    if (!resolved) {
      setToast("run_dir を選択してください");
      return;
    }
    setToast("Vrewプロンプト読み込み中...");
    setRunDir(resolved);
    setPrompts({ ...initialPromptsState, status: "loading", runDir: resolved });
    try {
      const params = new URLSearchParams({ run_dir: resolved });
      const res = await fetch(apiUrl(`/api/swap/vrew-prompts?${params.toString()}`));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPrompts({
          ...initialPromptsState,
          status: "error",
          runDir: resolved,
          error: data?.detail || "Vrewプロンプトの取得に失敗しました",
        });
        setToast("");
        return;
      }
      const nextTextLines = String(data?.prompts_text || "");
      setPrompts({
        status: "ready",
        runDir: data?.run_dir || resolved,
        promptsPath: data?.prompts_path || "",
        lineCount: Number(data?.line_count || 0),
        textLines: nextTextLines,
        textKuten: String(data?.prompts_text_kuten || ""),
        error: undefined,
      });
      setIndividualPromptsRaw(nextTextLines);
      setToast("読み込み完了");
    } catch (e: any) {
      setPrompts({
        ...initialPromptsState,
        status: "error",
        runDir: resolved,
        error: e?.message || "Vrewプロンプトの取得に失敗しました",
      });
      setToast("");
    }
  };

  const handleLoadVrewPrompts = async () => {
    await loadVrewPrompts(runDir);
  };

  const handleCopy = async (text: string, label: string) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setToast(`${label} をコピーしました`);
    } catch (e: any) {
      setToast(e?.message || "コピーに失敗しました");
    }
  };

  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>Vrew用プロンプト</h1>
          <p className="page-lead">
            共通プロンプト（最大100文字）と、個別プロンプト（自動分割: 1回最大8000文字）を用意して、Vrewにコピペします（Vrewは「。」で分割）。
          </p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit">
            ← CapCut編集メニューへ戻る
          </Link>
          <Link className="button" to="/capcut-edit/draft">
            新規ドラフト作成
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section" style={{ display: "grid", gap: 14 }}>
        {toast && (
          <div style={{ padding: "10px 12px", borderRadius: 10, background: "#0f172a", color: "#fff", fontSize: 12 }}>
            {toast}
          </div>
        )}

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "grid", gap: 4 }}>
              <div style={{ fontWeight: 800 }}>0) チャンネル別 進捗（Vrew用プロンプト）</div>
              <div style={{ fontSize: 12, color: "#64748b" }}>
                完成 = vrew_import_prompts.txt あり（合計: {overallProgress.ready}/{overallProgress.total}）。
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: "#64748b" }}>
                {runDirsStatus === "loading" ? "run_dir 読み込み中..." : `run_dir: ${runDirs.length}`}
              </div>
              <button
                type="button"
                onClick={() => refreshRunDirs()}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#0f172a",
                  cursor: "pointer",
                  fontWeight: 800,
                }}
              >
                進捗更新
              </button>
            </div>
          </div>

          {runDirsStatus === "error" && <div style={{ fontSize: 12, color: "#b91c1c" }}>{runDirsError || "run_dir の取得に失敗しました"}</div>}

          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {channelProgress.map((item) => {
              const isSelected = normalizeChannelCode(channel) === item.code;
              const pctLabel = item.total > 0 ? `${Math.round(item.pct * 100)}%` : "—";
              const barColor = item.pct >= 1 ? "#16a34a" : item.pct > 0 ? "#0ea5e9" : "#cbd5e1";
              return (
                <button
                  key={item.code}
                  type="button"
                  onClick={() => applyChannelSelection(item.code)}
                  style={{
                    textAlign: "left",
                    padding: 12,
                    borderRadius: 12,
                    border: isSelected ? "2px solid #0f172a" : "1px solid #e5e7eb",
                    background: isSelected ? "#f8fafc" : "#fff",
                    cursor: "pointer",
                    display: "grid",
                    gap: 8,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                    <div style={{ fontWeight: 900, color: "#0f172a" }}>{item.code}</div>
                    <div style={{ fontSize: 12, color: "#64748b" }}>{pctLabel}</div>
                  </div>
                  {item.name && <div style={{ fontSize: 12, color: "#334155", lineHeight: 1.2 }}>{item.name}</div>}
                  <div style={{ height: 8, borderRadius: 999, background: "#e5e7eb", overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${item.pct * 100}%`, background: barColor }} />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12, color: "#64748b" }}>
                    <div>
                      完成 {item.ready}/{item.total}
                    </div>
                    <div>run {item.started}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ fontWeight: 800 }}>1) 企画を選ぶ</div>
            <div style={{ fontSize: 12, color: "#64748b" }}>チャンネル/動画番号を選ぶと、対応する run_dir を自動検出します。</div>
          </div>

          {channelsStatus === "error" && (
            <div style={{ fontSize: 12, color: "#b91c1c" }}>{channelsError || "チャンネル取得に失敗しました"}</div>
          )}
          {videosStatus === "error" && <div style={{ fontSize: 12, color: "#b91c1c" }}>{videosError || "動画一覧の取得に失敗しました"}</div>}

          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>チャンネル</div>
              <select
                value={channel}
                onChange={(e) => {
                  applyChannelSelection(e.target.value);
                }}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                <option value="" disabled>
                  {channelsStatus === "loading" ? "読み込み中..." : "選択してください"}
                </option>
                {channels.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code}
                    {c.name ? `｜${c.name}` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>動画（企画）</div>
                {selectedChannelProgress && (
                  <div style={{ fontSize: 12, color: "#64748b" }}>
                    完成 {selectedChannelProgress.ready}/{selectedChannelProgress.total}（run {selectedChannelProgress.started}）
                  </div>
                )}
              </div>
              <input
                value={videoFilter}
                onChange={(e) => setVideoFilter(e.target.value)}
                placeholder="フィルタ（例: 001 / タイトル）"
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
              />

              <div style={{ border: "1px solid #cbd5e1", borderRadius: 10, background: "#fff", overflow: "hidden" }}>
                <div style={{ maxHeight: 260, overflow: "auto" }}>
                  {filteredVideos.length === 0 && (
                    <div style={{ padding: 10, fontSize: 12, color: "#64748b" }}>該当する動画がありません</div>
                  )}
                  {filteredVideos.map((v) => {
                    const token = buildEpisodeToken(channel, v.video).toUpperCase();
                    const best = runDirStats.bestByToken.get(token);
                    const hasRun = Boolean(best);
                    const hasPrompts = runDirStats.readyTokens.has(token);
                    const isSelected = normalizeVideoNumber(video) === normalizeVideoNumber(v.video);
                    const chip = hasPrompts
                      ? { label: "✅ 完成", bg: "#dcfce7", color: "#166534" }
                      : hasRun
                        ? { label: "🕗 runあり", bg: "#ffedd5", color: "#9a3412" }
                        : { label: "⏳ 未着手", bg: "#e2e8f0", color: "#334155" };
                    return (
                      <div
                        key={v.video}
                        role="button"
                        tabIndex={0}
                        onClick={() => applyVideoSelection(v.video)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            applyVideoSelection(v.video);
                          }
                        }}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "68px 1fr auto",
                          gap: 10,
                          alignItems: "center",
                          padding: "10px 12px",
                          borderBottom: "1px solid #e5e7eb",
                          cursor: "pointer",
                          background: isSelected ? "#f8fafc" : "#fff",
                        }}
                      >
                        <div style={{ fontWeight: 900, color: "#0f172a" }}>{v.video}</div>
                        <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
                          <div
                            style={{
                              fontSize: 12,
                              fontWeight: 800,
                              color: "#0f172a",
                              lineHeight: 1.2,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {v.title || "（タイトル未設定）"}
                          </div>
                          <div style={{ fontSize: 11, color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            run_dir: {best?.name ? best.name : "—"}
                          </div>
                        </div>
                        <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end", flexWrap: "wrap" }}>
                          <div
                            style={{
                              padding: "2px 8px",
                              borderRadius: 999,
                              background: chip.bg,
                              color: chip.color,
                              fontSize: 11,
                              fontWeight: 900,
                            }}
                          >
                            {chip.label}
                          </div>
                          {hasPrompts && best?.path && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                applyVideoSelection(v.video);
                                void loadVrewPrompts(best.path);
                              }}
                              style={{
                                padding: "6px 10px",
                                borderRadius: 8,
                                border: "1px solid #cbd5e1",
                                background: "#fff",
                                color: "#0f172a",
                                cursor: "pointer",
                                fontWeight: 800,
                                fontSize: 12,
                              }}
                            >
                              読み込む
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {selectedVideoSummary?.title && <div style={{ fontSize: 11, color: "#94a3b8" }}>選択中: {selectedVideoSummary.title}</div>}

              <details style={{ paddingTop: 2 }}>
                <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12 }}>ドロップダウンで選ぶ（従来）</summary>
                <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                  <select
                    value={normalizeVideoNumber(video)}
                    onChange={(e) => applyVideoSelection(e.target.value)}
                    style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
                  >
                    <option value="">（未選択）</option>
                    {filteredVideos.map((v) => (
                      <option key={v.video} value={v.video}>
                        {v.video}
                        {v.title ? `｜${v.title}` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              </details>
            </div>

            <div style={{ display: "grid", gap: 8, paddingTop: 6, borderTop: "1px dashed #e5e7eb" }}>
              <div style={{ display: "grid", gap: 4 }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>run_dir（自動検出）</div>
                <div style={{ fontSize: 12, color: "#64748b" }}>企画ID: {episodeToken || "—"}</div>
                {episodeToken && runDirCandidates.length === 0 && (
                  <div style={{ fontSize: 12, color: "#b91c1c" }}>
                    該当run_dirが見つかりません。先に「新規ドラフト作成」で実行して run_dir を作るか、run_dir を手動で選んでください。
                  </div>
                )}
                {runDirCandidates.length > 0 && (
                  <select
                    value={runDir}
                    onChange={(e) => {
                      const value = e.target.value;
                      setRunDir(value);
                      setPrompts({ ...initialPromptsState, status: "idle", runDir: value });
                      setIndividualPromptsRaw("");
                    }}
                    style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
                  >
                    <option value="">（未選択）</option>
                    {runDirCandidates.map((r) => (
                      <option key={r.path} value={r.path}>
                        {r.vrew_prompts_exists ? "✅ " : ""}
                        {r.name}
                      </option>
                    ))}
                  </select>
                )}
                {runDir && <div style={{ fontSize: 11, color: "#94a3b8" }}>{selectedRunDirName}</div>}
              </div>

              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  onClick={() => refreshRunDirs()}
                  style={{
                    padding: "10px 14px",
                    borderRadius: 10,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    color: "#0f172a",
                    cursor: "pointer",
                    fontWeight: 800,
                  }}
                >
                  run_dir更新
                </button>
                <button
                  type="button"
                  onClick={handleLoadVrewPrompts}
                  disabled={!runDir || prompts.status === "loading" || selectedRunDirKnownNoPrompts}
                  style={{
                    padding: "10px 14px",
                    borderRadius: 10,
                    border: "none",
                    background: !runDir || prompts.status === "loading" || selectedRunDirKnownNoPrompts ? "#e5e7eb" : "#0f172a",
                    color: "#fff",
                    cursor: !runDir || prompts.status === "loading" || selectedRunDirKnownNoPrompts ? "not-allowed" : "pointer",
                    fontWeight: 800,
                  }}
                >
                  {prompts.status === "loading" ? "読み込み中..." : "個別プロンプトを読み込む"}
                </button>
                {prompts.status === "ready" && (
                  <div style={{ fontSize: 12, color: "#64748b" }}>
                    {prompts.lineCount} 件 / {prompts.promptsPath ? prompts.promptsPath : "vrew_import_prompts.txt"}
                  </div>
                )}
                {prompts.status === "error" && <div style={{ fontSize: 12, color: "#b91c1c" }}>{prompts.error}</div>}
              </div>

              {selectedRunDirKnownNoPrompts && (
                <div style={{ fontSize: 12, color: "#b91c1c" }}>
                  このrun_dirには vrew_import_prompts.txt がありません。個別プロンプト欄に手入力するか、生成してから読み込んでください。
                </div>
              )}

              <details style={{ paddingTop: 4 }}>
                <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12 }}>手動でrun_dirを選ぶ（上級者）</summary>
                <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                  <input
                    value={manualRunDirFilter}
                    onChange={(e) => setManualRunDirFilter(e.target.value)}
                    placeholder="run_dir検索（例: CH23-001）"
                    style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
                  />
                  <select
                    value={runDir}
                    onChange={(e) => {
                      const value = e.target.value;
                      setRunDir(value);
                      setPrompts({ ...initialPromptsState, status: "idle", runDir: value });
                      setIndividualPromptsRaw("");
                    }}
                    style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
                  >
                    <option value="">（未選択）</option>
                    {filteredAllRunDirs.map((r) => (
                      <option key={r.path} value={r.path}>
                        {r.vrew_prompts_exists ? "✅ " : ""}
                        {r.name}
                      </option>
                    ))}
                  </select>
                  {runDir && <div style={{ fontSize: 11, color: "#94a3b8" }}>{runDir}</div>}
                </div>
              </details>
            </div>
          </div>
        </div>

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div>
              <strong>2) 共通プロンプト（最大 {COMMON_PROMPT_LIMIT} 文字）</strong>
              <div style={{ fontSize: 12, color: "#64748b" }}>雰囲気/画風/人物の共通条件を1つにまとめます（なるべく記号なし）。</div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <div style={{ fontSize: 12, color: commonPromptOver ? "#b91c1c" : "#64748b" }}>
                {commonPromptCount}/{COMMON_PROMPT_LIMIT}
              </div>
              <button
                type="button"
                onClick={() => setCommonPrompt("")}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#0f172a",
                  cursor: "pointer",
                  fontWeight: 700,
                }}
              >
                クリア
              </button>
              <button
                type="button"
                onClick={() => handleCopy(commonPromptNormalized, "共通プロンプト")}
                disabled={!commonPromptNormalized || commonPromptOver}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: !commonPromptNormalized || commonPromptOver ? "#e5e7eb" : "#f8fafc",
                  color: "#0f172a",
                  cursor: !commonPromptNormalized || commonPromptOver ? "not-allowed" : "pointer",
                  fontWeight: 700,
                }}
              >
                コピー
              </button>
            </div>
          </div>
          {commonPromptOver && <div style={{ fontSize: 12, color: "#b91c1c" }}>共通プロンプトが長すぎます（最大 {COMMON_PROMPT_LIMIT} 文字）。</div>}
          <textarea
            value={commonPrompt}
            onChange={(e) => setCommonPrompt(e.target.value)}
            placeholder="例: 二次元の絵本風イラスト。明るい配色。人物は同じ家族。文字なし。"
            style={{
              width: "100%",
              minHeight: 100,
              borderRadius: 10,
              border: "1px solid #cbd5e1",
              padding: 10,
              fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              background: "#fff",
              whiteSpace: "pre-wrap",
            }}
          />
        </div>

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div>
              <strong>3) 個別プロンプト（自動分割: 1回最大 {INDIVIDUAL_PROMPT_CHUNK_LIMIT} 文字）</strong>
              <div style={{ fontSize: 12, color: "#64748b" }}>
                1文=1枚のつもりで末尾は「。」に揃えます。Vrewは「。」で分割。貼り付けはブロック1→2→3…の順でOKです。
              </div>
              {prompts.status === "ready" && (
                <div style={{ fontSize: 11, color: "#94a3b8" }}>
                  読み込み元: {prompts.promptsPath ? prompts.promptsPath : "vrew_import_prompts.txt"}（{prompts.lineCount} 行）
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: individualChunkOver ? "#b91c1c" : "#64748b" }}>
                総計 {individualTotalChars} 文字（{individualLineCount} 文）/ ブロック {individualChunkCount}
              </div>
              <button
                type="button"
                onClick={() => setIndividualPromptsRaw("")}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#0f172a",
                  cursor: "pointer",
                  fontWeight: 700,
                }}
              >
                クリア
              </button>
              <button
                type="button"
                onClick={() => setIndividualPromptsRaw(individualNormalized.textLines)}
                disabled={!individualPromptsRaw.trim()}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: individualPromptsRaw.trim() ? "#fff" : "#e5e7eb",
                  color: "#0f172a",
                  cursor: individualPromptsRaw.trim() ? "pointer" : "not-allowed",
                  fontWeight: 700,
                }}
              >
                整形
              </button>
            </div>
          </div>

          {individualChunkOver && (
            <div style={{ fontSize: 12, color: "#b91c1c" }}>
              一部のブロックが {INDIVIDUAL_PROMPT_CHUNK_LIMIT} 文字を超えています。文を短くしてください。
            </div>
          )}

          <textarea
            value={individualPromptsRaw}
            onChange={(e) => setIndividualPromptsRaw(e.target.value)}
            placeholder={
              "例: \n笑顔の柴犬が風船を持ってジャンプしている。\n公園のベンチで猫と子どもが絵本を読んでいる。\n雨上がりの虹を見上げて喜ぶ家族。"
            }
            style={{
              width: "100%",
              minHeight: 220,
              borderRadius: 10,
              border: "1px solid #cbd5e1",
              padding: 10,
              fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              background: "#fff",
              whiteSpace: "pre",
            }}
          />

          <div style={{ display: "grid", gap: 10 }}>
            {individualChunks.length === 0 && <div style={{ fontSize: 12, color: "#64748b" }}>ブロック表示はここに出ます（まずは入力/読み込みしてください）。</div>}
            {individualChunks.map((chunk, idx) => {
              const over = chunk.charCount > INDIVIDUAL_PROMPT_CHUNK_LIMIT;
              const label = `ブロック${idx + 1}`;
              return (
                <div
                  key={`${label}-${chunk.charCount}-${chunk.lineCount}`}
                  style={{
                    border: "1px solid #e5e7eb",
                    borderRadius: 12,
                    background: "#f8fafc",
                    padding: 12,
                    display: "grid",
                    gap: 8,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    <div style={{ fontWeight: 900, color: "#0f172a" }}>
                      {label}/{individualChunkCount}
                    </div>
                    <div style={{ fontSize: 12, color: over ? "#b91c1c" : "#64748b" }}>
                      {chunk.charCount}/{INDIVIDUAL_PROMPT_CHUNK_LIMIT}（{chunk.lineCount} 文）
                    </div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <button
                        type="button"
                        onClick={() => handleCopy(chunk.textKuten, `${label}（Vrew）`)}
                        disabled={!chunk.textKuten || over}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 8,
                          border: "1px solid #cbd5e1",
                          background: !chunk.textKuten || over ? "#e5e7eb" : "#0f172a",
                          color: "#fff",
                          cursor: !chunk.textKuten || over ? "not-allowed" : "pointer",
                          fontWeight: 900,
                          fontSize: 12,
                        }}
                      >
                        コピー（Vrew）
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCopy(chunk.textLines, `${label}（改行）`)}
                        disabled={!chunk.textKuten || over}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 8,
                          border: "1px solid #cbd5e1",
                          background: !chunk.textKuten || over ? "#e5e7eb" : "#fff",
                          color: "#0f172a",
                          cursor: !chunk.textKuten || over ? "not-allowed" : "pointer",
                          fontWeight: 800,
                          fontSize: 12,
                        }}
                      >
                        コピー（改行）
                      </button>
                    </div>
                  </div>
                  <textarea
                    readOnly
                    value={chunk.textKuten}
                    style={{
                      width: "100%",
                      minHeight: 120,
                      borderRadius: 10,
                      border: "1px solid #cbd5e1",
                      padding: 10,
                      fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                      fontSize: 12,
                      background: "#fff",
                      resize: "vertical",
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
