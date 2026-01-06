import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import {
  fetchPlanningChannelCsv,
  updateVideoRedo,
  fetchRedoSummary,
  lookupThumbnails,
  markVideoPublishedLocked,
  unmarkVideoPublishedLocked,
} from "../api/client";
import { apiUrl } from "../api/baseUrl";
import type { ChannelSummary, RedoSummaryItem, ThumbnailLookupItem } from "../api/types";
import { RedoBadge } from "../components/RedoBadge";
import type { ShellOutletContext } from "../layouts/AppShell";
import { safeLocalStorage } from "../utils/safeStorage";
import "./PlanningPage.css";

type Row = Record<string, string>;

type EpisodeProgressItem = {
  video: string;
  script_status?: string | null;
  audio_ready?: boolean | null;
  video_run_id?: string | null;
  capcut_draft_status?: string | null;
  capcut_draft_run_id?: string | null;
  capcut_draft_target?: string | null;
  capcut_draft_target_exists?: boolean | null;
  issues?: string[];
};

type EpisodeProgressResponse = {
  episodes?: EpisodeProgressItem[];
};

type DialogAiAuditItem = {
  video: string;
  script_id?: string | null;
  verdict?: string | null;
  audited_at?: string | null;
  audited_by?: string | null;
  reasons?: string[];
  notes?: string | null;
  script_hash_sha1?: string | null;
  stale?: boolean | null;
};

type DialogAiAuditChannelResponse = {
  items?: DialogAiAuditItem[];
};

const CHANNEL_META: Record<string, { icon: string; color: string }> = {
  CH01: { icon: "🎯", color: "chip-cyan" },
  CH02: { icon: "📚", color: "chip-blue" },
  CH03: { icon: "💡", color: "chip-green" },
  CH04: { icon: "🧭", color: "chip-indigo" },
  CH05: { icon: "💞", color: "chip-pink" },
  CH06: { icon: "🕯️", color: "chip-purple" },
  CH07: { icon: "🌿", color: "chip-emerald" },
  CH08: { icon: "🌙", color: "chip-slate" },
  CH09: { icon: "🏛️", color: "chip-amber" },
  CH10: { icon: "🧠", color: "chip-orange" },
  CH11: { icon: "📜", color: "chip-teal" },
};

const META_COLOR_FALLBACK = [
  "chip-cyan",
  "chip-blue",
  "chip-green",
  "chip-indigo",
  "chip-pink",
  "chip-purple",
  "chip-emerald",
  "chip-slate",
  "chip-amber",
  "chip-orange",
  "chip-teal",
];

function compareChannelCode(a: string, b: string): number {
  const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
  const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
  const aNum = Number.isFinite(an);
  const bNum = Number.isFinite(bn);
  if (aNum && bNum) {
    return an - bn;
  }
  if (aNum) return -1;
  if (bNum) return 1;
  return a.localeCompare(b, "ja-JP");
}

function normalizeChannelCode(value: string | null): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  const compact = raw.toUpperCase().replace(/[\s_]/g, "");
  const match = compact.match(/CH(\d{1,3})/);
  if (match) {
    const num = match[1] ?? "";
    return `CH${num.padStart(2, "0")}`;
  }
  const digits = compact.replace(/[^0-9]/g, "");
  if (digits && digits.length <= 3) {
    return `CH${digits.padStart(2, "0")}`;
  }
  return compact;
}

const AUDIT_COLUMN = "監査(参考)";

const LONG_COLUMNS = new Set([
  "企画意図",
  "具体的な内容（話の構成案）",
  "説明文_この動画でわかること",
  "説明文_リード",
  "DALL-Eプロンプト（URL・テキスト指示込み）",
  "サムネ画像プロンプト（URL・テキスト指示込み）",
  "台本本文",
  "台本",
  "台本パス",
  "内容",
  "内容（企画要約）",
  "動画内挿絵AI向けプロンプト（10個）",
]);

const NARROW_COLUMNS = new Set([
  "動画番号",
  "動画ID",
  "進捗",
  "台本(自動)",
  "音声(自動)",
  "整合",
  AUDIT_COLUMN,
  "投稿完了",
  "動画run",
  "CapCutドラフト",
]);
const MEDIUM_COLUMNS = new Set(["タイトル", "音声生成", "音声品質", "納品"]);
const THUMB_COLUMNS = new Set(["サムネ"]);

const COMPACT_PRIORITY = [
  "動画番号",
  "動画ID",
  "タイトル",
  "サムネ",
  "進捗",
  "台本(自動)",
  "音声(自動)",
  "整合",
  AUDIT_COLUMN,
  "投稿完了",
  "動画run",
  "CapCutドラフト",
  "更新日時",
  "台本パス",
  "企画意図",
  "具体的な内容（話の構成案）",
  "ターゲット層",
  "悩みタグ_メイン",
  "悩みタグ_サブ",
  "ライフシーン",
  "キーコンセプト",
  "ベネフィット一言",
  "説明文_リード",
  "説明文_この動画でわかること",
  "サムネタイトル",
  "サムネタイトル上",
  "サムネタイトル下",
  "音声生成",
  "音声品質",
  "納品",
];

const toBool = (v: any, fallback = true) => {
  if (v === true || v === false) return v;
  if (typeof v === "string") {
    const s = v.toLowerCase();
    if (["true", "1", "yes", "y", "ok", "redo"].includes(s)) return true;
    if (["false", "0", "no", "n"].includes(s)) return false;
  }
  return fallback;
};

const normalizeVideo = (value: any): string => {
  const digits = String(value ?? "").replace(/[^0-9]/g, "");
  if (!digits) return "";
  return digits.padStart(3, "0");
};

const normalizeThumbStable = (raw: string | null | undefined): string | null => {
  const value = String(raw ?? "").trim();
  if (!value) return null;
  const base = value.split("?")[0]?.split("#")[0] ?? value;
  const name = base.split("/").filter(Boolean).slice(-1)[0] ?? base;
  if (name === "00_thumb_1.png" || name === "00_thumb_2.png") {
    return name.replace(/\.png$/i, "");
  }
  return null;
};

const pickTwoUpThumb = (items: ThumbnailLookupItem[], stable: "00_thumb_1" | "00_thumb_2"): ThumbnailLookupItem | null => {
  return (
    items.find((item) => normalizeThumbStable(item.name) === stable) ??
    items.find((item) => normalizeThumbStable(item.path) === stable) ??
    items.find((item) => normalizeThumbStable(item.url) === stable) ??
    null
  );
};

const formatDialogAuditBadge = (
  item: DialogAiAuditItem | null | undefined
): { label: string; cls: string; title: string } => {
  const verdict = String(item?.verdict || "")
    .trim()
    .toLowerCase();
  const stale = Boolean(item?.stale);

  let label = "未";
  let cls = "planning-page__align planning-page__align--unknown";
  if (item) {
    if (stale) {
      label = "要再査定";
      cls = "planning-page__align planning-page__align--warn";
    } else if (verdict === "pass") {
      label = "OK";
      cls = "planning-page__align planning-page__align--ok";
    } else if (verdict === "fail") {
      label = "NG";
      cls = "planning-page__align planning-page__align--ng";
    } else if (verdict === "grey") {
      label = "要確認";
      cls = "planning-page__align planning-page__align--warn";
    } else if (verdict) {
      label = verdict;
    }
  }

  const parts: string[] = [];
  if (!item) {
    parts.push("未監査（参考）");
  } else {
    parts.push(`verdict=${verdict || "unknown"}`);
    if (item.audited_at) parts.push(`audited_at=${item.audited_at}`);
    if (item.audited_by) parts.push(`audited_by=${item.audited_by}`);
    if (stale) parts.push("stale=true（台本が更新されている可能性）");
    const reasons = (item.reasons ?? []).map((r) => String(r || "").trim()).filter(Boolean).join(", ");
    if (reasons) parts.push(`reasons=${reasons}`);
    const notes = String(item.notes || "").trim();
    if (notes) parts.push(`notes=${notes}`);
  }
  const title = parts.join(" / ").trim() || label;
  return { label, cls, title };
};

const normalizeKeyConcept = (value: any): string => {
  const raw = String(value ?? "");
  const normalized = raw.normalize("NFKC").trim();
  return normalized.replace(/[\s\u3000・･·、,.／/\\\-‐‑‒–—―ー〜~]/g, "");
};

const extractTitleTag = (title: any): string => {
  const raw = String(title ?? "").trim();
  const match = raw.match(/^\s*【([^】]{1,40})】/);
  return (match?.[1] ?? "").trim();
};

const resolveEpisodeKey = (row: Row): { keyRaw: string; keyNorm: string } => {
  const keyConcept = String(row["キーコンセプト"] ?? "").trim();
  const keyConceptNorm = normalizeKeyConcept(keyConcept);
  if (keyConceptNorm) return { keyRaw: keyConcept, keyNorm: keyConceptNorm };

  const titleTag = extractTitleTag(row["タイトル"]);
  const titleTagNorm = normalizeKeyConcept(titleTag);
  if (titleTagNorm) return { keyRaw: titleTag, keyNorm: titleTagNorm };

  const mainTag = String(row["悩みタグ_メイン"] ?? "").trim();
  const mainTagNorm = normalizeKeyConcept(mainTag);
  if (mainTagNorm) return { keyRaw: mainTag, keyNorm: mainTagNorm };

  const subTag = String(row["悩みタグ_サブ"] ?? "").trim();
  const subTagNorm = normalizeKeyConcept(subTag);
  if (subTagNorm) return { keyRaw: subTag, keyNorm: subTagNorm };

  return { keyRaw: "", keyNorm: "" };
};

const isAdoptedEpisodeRow = (row: Row): boolean => {
  const progress = String(row["進捗"] ?? (row as any)["progress"] ?? "");
  return (
    toBool((row as any)["published_lock"], false) ||
    progress.includes("投稿済み") ||
    progress.includes("公開済み") ||
    progress.trim().toLowerCase() === "published"
  );
};

const fetchEpisodeProgress = async (channelCode: string): Promise<EpisodeProgressResponse> => {
  const ch = String(channelCode || "").trim().toUpperCase();
  if (!ch) return { episodes: [] };
  const response = await fetch(apiUrl(`/api/channels/${encodeURIComponent(ch)}/episode-progress`), {
    method: "GET",
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data?.detail) {
        message = String(data.detail);
      }
    } catch {
      // no-op
    }
    throw new Error(message);
  }
  return (await response.json()) as EpisodeProgressResponse;
};

const buildEpisodeProgressMap = (response: EpisodeProgressResponse): Record<string, EpisodeProgressItem> => {
  const map: Record<string, EpisodeProgressItem> = {};
  (response.episodes ?? []).forEach((item) => {
    const token = normalizeVideo(item.video);
    if (!token) return;
    map[token] = item;
  });
  return map;
};

const fetchDialogAuditChannel = async (channelCode: string): Promise<Record<string, DialogAiAuditItem>> => {
  const ch = String(channelCode || "").trim().toUpperCase();
  if (!ch) return {};
  const response = await fetch(apiUrl(`/api/meta/dialog_ai_audit/${encodeURIComponent(ch)}`), {
    method: "GET",
    cache: "no-store",
  });
  if (!response.ok) {
    return {};
  }
  const data = (await response.json()) as DialogAiAuditChannelResponse;
  const map: Record<string, DialogAiAuditItem> = {};
  (data.items ?? []).forEach((item) => {
    const vid = normalizeVideo(item.video);
    if (!vid) return;
    map[vid] = { ...item, video: vid };
  });
  return map;
};

const formatCapcutLabel = (statusRaw: string, isCandidate: boolean): string => {
  const status = String(statusRaw || "").trim().toLowerCase();
  const base = status === "ok" ? "OK" : status === "broken" ? "LINK切れ" : status === "missing" ? "未生成" : statusRaw || "";
  if (!base) return "";
  return isCandidate ? `${base}(候補)` : base;
};

const formatScriptLabel = (statusRaw: string): string => {
  const status = String(statusRaw || "").trim().toLowerCase();
  if (!status) return "";
  if (status === "completed" || status === "script_validated") return "完了";
  if (status === "processing") return "処理中";
  if (status === "in_progress" || status === "script_in_progress") return "作成中";
  if (status === "pending") return "待ち";
  if (status === "failed") return "失敗";
  if (status === "missing") return "未開始";
  if (status === "unknown") return "不明";
  return statusRaw;
};

const attachEpisodeProgressColumns = (rows: Row[], progressMap: Record<string, EpisodeProgressItem>): Row[] => {
  return (rows ?? []).map((row) => {
    const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
    const item = vid ? progressMap[vid] : undefined;
    const selectedRun = item?.video_run_id ? String(item.video_run_id) : "";
    const candidateRun = item?.capcut_draft_run_id ? String(item.capcut_draft_run_id) : "";
    const runLabel = selectedRun || (candidateRun ? `候補:${candidateRun}` : "");
    const isCandidate = !selectedRun && Boolean(candidateRun);
    const capcutLabel = item ? formatCapcutLabel(String(item.capcut_draft_status || ""), isCandidate) : "";
    const scriptLabel = item ? formatScriptLabel(String(item.script_status || "")) : "";
    const audioLabel = item ? (item.audio_ready ? "OK" : "未生成") : "";
    return { ...row, "台本(自動)": scriptLabel, "音声(自動)": audioLabel, 動画run: runLabel, CapCutドラフト: capcutLabel };
  });
};

export function PlanningPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { channels: availableChannels } = useOutletContext<ShellOutletContext>();
  const [searchParams] = useSearchParams();
  const channel = useMemo(() => normalizeChannelCode(searchParams.get("channel")), [searchParams]);
  const videoParam = useMemo(() => normalizeVideo(searchParams.get("video")), [searchParams]);
  const channelCodes = useMemo(() => {
    const codes = (availableChannels ?? [])
      .map((item) => item.code)
      .filter((code): code is string => typeof code === "string" && code.length > 0);
    const unique = Array.from(new Set(codes));
    unique.sort(compareChannelCode);
    return unique.length ? unique : Object.keys(CHANNEL_META).sort(compareChannelCode);
  }, [availableChannels]);
  const channelMap = useMemo(() => {
    const map: Record<string, ChannelSummary> = {};
    (availableChannels ?? []).forEach((item) => {
      map[item.code] = item;
    });
    return map;
  }, [availableChannels]);
  const applyChannel = useCallback(
    (nextRaw: string) => {
      const next = normalizeChannelCode(nextRaw);
      if (next) {
        safeLocalStorage.setItem("ui.channel.selected", next);
      } else {
        safeLocalStorage.removeItem("ui.channel.selected");
      }
      const params = new URLSearchParams(searchParams);
      if (next) {
        params.set("channel", next);
      } else {
        params.delete("channel");
      }
      params.delete("video");
      const search = params.toString();
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : "" },
        { replace: true, preventScrollReset: true }
      );
    },
    [location.pathname, navigate, searchParams]
  );
  const applyVideo = useCallback(
    (nextRaw: string | null) => {
      const next = normalizeVideo(nextRaw);
      const params = new URLSearchParams(searchParams);
      if (next) {
        params.set("video", next);
      } else {
        params.delete("video");
      }
      const search = params.toString();
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : "" },
        { replace: true, preventScrollReset: true }
      );
    },
    [location.pathname, navigate, searchParams]
  );
  const [rows, setRows] = useState<Row[]>([]);
  const [filteredRows, setFilteredRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [redoOnly, setRedoOnly] = useState(false);
  const [detailRow, setDetailRow] = useState<Row | null>(null);
  const [saving, setSaving] = useState(false);
  const [redoScriptValue, setRedoScriptValue] = useState<boolean>(true);
  const [redoAudioValue, setRedoAudioValue] = useState<boolean>(true);
  const [redoNoteValue, setRedoNoteValue] = useState<string>("");
  const [redoSummary, setRedoSummary] = useState<RedoSummaryItem | null>(null);
  const [thumbMap, setThumbMap] = useState<Record<string, ThumbnailLookupItem[]>>({});
  const [thumbPreview, setThumbPreview] = useState<string | null>(null);
  const [thumbPreviewItems, setThumbPreviewItems] = useState<ThumbnailLookupItem[] | null>(null);
  const [thumbPreviewIndex, setThumbPreviewIndex] = useState<number>(0);
  const [selectedCell, setSelectedCell] = useState<{ key: string; value: string } | null>(null);
  const [publishingKey, setPublishingKey] = useState<string | null>(null);
  const [unpublishingKey, setUnpublishingKey] = useState<string | null>(null);
  const [copiedTitleKey, setCopiedTitleKey] = useState<string | null>(null);
  const [episodeProgressMap, setEpisodeProgressMap] = useState<Record<string, EpisodeProgressItem>>({});
  const [dialogAuditMap, setDialogAuditMap] = useState<Record<string, DialogAiAuditItem>>({});
  const thumbRequestedRef = useRef<Set<string>>(new Set());
  const deepLinkAppliedRef = useRef<string | null>(null);
  const copiedTitleTimerRef = useRef<number | null>(null);
  const autoChannelAppliedRef = useRef(false);

  useEffect(() => {
    if (autoChannelAppliedRef.current) {
      return;
    }
    if (channel) {
      autoChannelAppliedRef.current = true;
      safeLocalStorage.setItem("ui.channel.selected", channel);
      return;
    }
    const stored = (safeLocalStorage.getItem("ui.channel.selected") ?? "").trim().toUpperCase();
    if (stored) {
      autoChannelAppliedRef.current = true;
      applyChannel(stored);
      return;
    }
    if ((availableChannels ?? []).length > 0 && channelCodes.length > 0) {
      autoChannelAppliedRef.current = true;
      applyChannel(channelCodes[0]);
    }
  }, [applyChannel, availableChannels, channel, channelCodes]);
  const goToVideoPage = useCallback(
    (channelCode: string, videoRaw: string) => {
      const ch = String(channelCode || "").toUpperCase();
      const token = normalizeVideo(videoRaw);
      if (!ch || !token) return;
      navigate(`/channels/${encodeURIComponent(ch)}/videos/${encodeURIComponent(token)}`);
    },
    [navigate]
  );
  const goToThumbnailsPage = useCallback(
    (channelCode: string, videoRaw: string, options?: { stable?: string | null }) => {
      const ch = normalizeChannelCode(channelCode);
      const token = normalizeVideo(videoRaw);
      if (!/^CH\\d{2,3}$/.test(ch) || !token) return;
      const stable = String(options?.stable ?? "").trim();
      const stableQuery = stable ? `&stable=${encodeURIComponent(stable)}` : "";
      navigate(`/thumbnails?channel=${encodeURIComponent(ch)}&video=${encodeURIComponent(token)}${stableQuery}`);
    },
    [navigate]
  );
  const openDetailRow = useCallback(
    (row: Row) => {
      setDetailRow(row);
      const token = normalizeVideo(row["動画番号"] || row["video"] || "");
      if (token) {
        applyVideo(token);
      }
    },
    [applyVideo]
  );
  const closeDetailRow = useCallback(() => {
    setDetailRow(null);
    applyVideo(null);
  }, [applyVideo]);

  const copyToClipboard = useCallback(async (text: string): Promise<boolean> => {
    const value = String(text ?? "");
    if (!value) return false;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch {
      // fallback below
    }
    try {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      textarea.style.top = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(textarea);
      return ok;
    } catch {
      return false;
    }
  }, []);

  const copyTitle = useCallback(
    async (key: string, title: string) => {
      const value = String(title ?? "").trim();
      if (!value) return;
      const ok = await copyToClipboard(value);
      if (!ok) {
        setError("タイトルのコピーに失敗しました");
        return;
      }
      setCopiedTitleKey(key);
      if (copiedTitleTimerRef.current) {
        window.clearTimeout(copiedTitleTimerRef.current);
      }
      copiedTitleTimerRef.current = window.setTimeout(() => {
        setCopiedTitleKey(null);
        copiedTitleTimerRef.current = null;
      }, 900);
    },
    [copyToClipboard]
  );

  const findThumbOverride = useCallback((row: Row): string | null => {
    // 明示的なサムネ列を優先
    const explicitKeys = ["thumbnail_url", "サムネURL", "サムネ画像URL", "サムネ画像"];
    for (const key of explicitKeys) {
      const v = row[key];
      if (typeof v === "string" && v.trim()) {
        const val = v.trim();
        if (/https?:\/\/.+\.(png|jpe?g|webp)$/i.test(val)) return val;
        if (/(\.png|\.jpg|\.jpeg|\.webp)$/i.test(val)) return val;
      }
    }
    // それ以外のセルからもURL/拡張子を拾う
    for (const value of Object.values(row)) {
      if (typeof value !== "string") continue;
      const v = value.trim();
      if (!v) continue;
      if (/https?:\/\/.+\.(png|jpe?g|webp)$/i.test(v)) return v;
      if (/(\.png|\.jpg|\.jpeg|\.webp)$/i.test(v)) return v;
    }
    return null;
  }, []);

  const requestThumbForRow = useCallback(
    (row: Row) => {
      const chRaw = row["チャンネル"] || row["channel"] || row["channel_code"] || row["CH"] || row["ch"] || channel;
      const vidRaw = row["動画番号"] || row["動画ID"] || row["video"] || row["video_number"] || row["vid"] || row["番号"] || "";
      const ch = normalizeChannelCode(String(chRaw));
      const vid = normalizeVideo(String(vidRaw));
      if (!ch || !vid) return;
      const key = `${ch}-${vid}`;
      if (thumbRequestedRef.current.has(key)) return;
      thumbRequestedRef.current.add(key);

      const override = findThumbOverride(row);
      if (override) {
        setThumbMap((prev) => ({
          ...prev,
          [key]: [{ path: override, url: override, name: override }],
        }));
        return;
      }

      lookupThumbnails(ch, vid, row["タイトル"] || undefined, 3)
        .then((res) => {
          setThumbMap((prev) => ({
            ...prev,
            [key]: res.items || [],
          }));
        })
        .catch(() => {
          // allow retry later on scroll/refresh
          thumbRequestedRef.current.delete(key);
        });
    },
    [channel, findThumbOverride]
  );

  const markPublished = useCallback(
    async (channelCode: string, videoRaw: string) => {
      const videoToken = normalizeVideo(videoRaw);
      if (!channelCode || !videoToken) return;
      const key = `${channelCode}-${videoToken}`;
      setPublishingKey(key);
        setError(null);
      try {
        await markVideoPublishedLocked(channelCode, videoToken, { force_complete: true });
        const res = await fetchPlanningChannelCsv(channelCode);
        let progress: Record<string, EpisodeProgressItem> = {};
        try {
          progress = buildEpisodeProgressMap(await fetchEpisodeProgress(channelCode));
        } catch {
          progress = {};
        }
        let dialogAudit: Record<string, DialogAiAuditItem> = {};
        try {
          dialogAudit = await fetchDialogAuditChannel(channelCode);
        } catch {
          dialogAudit = {};
        }
        setEpisodeProgressMap(progress);
        setDialogAuditMap(dialogAudit);
        const nextRows = attachEpisodeProgressColumns(res.rows || [], progress);
        setRows(nextRows);
        const summary = await fetchRedoSummary(channelCode);
        setRedoSummary(summary[0] ?? null);
        setDetailRow((prev) => {
          if (!prev) return prev;
          const prevCh = prev["チャンネル"] || prev["チャンネルコード"] || channelCode;
          const prevVid = normalizeVideo(prev["動画番号"] || prev["video"] || "");
          if (prevCh !== channelCode || prevVid !== videoToken) return prev;
          const updated = nextRows.find((r) => normalizeVideo(r["動画番号"] || r["video"] || "") === videoToken);
          return updated ?? prev;
        });
      } catch (e: any) {
        setError(e?.message || "投稿済みの反映に失敗しました");
      } finally {
        setPublishingKey(null);
      }
    },
    []
  );

  const unmarkPublished = useCallback(async (channelCode: string, videoRaw: string) => {
    const videoToken = normalizeVideo(videoRaw);
    if (!channelCode || !videoToken) return;
    const key = `${channelCode}-${videoToken}`;
    if (!window.confirm(`投稿済みロックを解除しますか？ (${key})`)) return;
    setUnpublishingKey(key);
    setError(null);
    try {
      await unmarkVideoPublishedLocked(channelCode, videoToken);
      const res = await fetchPlanningChannelCsv(channelCode);
      let progress: Record<string, EpisodeProgressItem> = {};
      try {
        progress = buildEpisodeProgressMap(await fetchEpisodeProgress(channelCode));
      } catch {
        progress = {};
      }
      let dialogAudit: Record<string, DialogAiAuditItem> = {};
      try {
        dialogAudit = await fetchDialogAuditChannel(channelCode);
      } catch {
        dialogAudit = {};
      }
      setEpisodeProgressMap(progress);
      setDialogAuditMap(dialogAudit);
      const nextRows = attachEpisodeProgressColumns(res.rows || [], progress);
      setRows(nextRows);
      const summary = await fetchRedoSummary(channelCode);
      setRedoSummary(summary[0] ?? null);
      setDetailRow((prev) => {
        if (!prev) return prev;
        const prevCh = prev["チャンネル"] || prev["チャンネルコード"] || channelCode;
        const prevVid = normalizeVideo(prev["動画番号"] || prev["video"] || "");
        if (prevCh !== channelCode || prevVid !== videoToken) return prev;
        const updated = nextRows.find((r) => normalizeVideo(r["動画番号"] || r["video"] || "") === videoToken);
        return updated ?? prev;
      });
    } catch (e: any) {
      setError(e?.message || "投稿済みロックの解除に失敗しました");
    } finally {
      setUnpublishingKey(null);
    }
  }, []);

  useEffect(() => {
    Object.keys(thumbMap).forEach((key) => thumbRequestedRef.current.add(key));
  }, [thumbMap]);

  useEffect(() => {
    let cancelled = false;
    if (!channel) {
      setRows([]);
      setRedoSummary(null);
      setDetailRow(null);
      setError(null);
      setLoading(false);
      setEpisodeProgressMap({});
      setDialogAuditMap({});
      return () => {
        cancelled = true;
      };
    }

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchPlanningChannelCsv(channel);
        if (cancelled) return;
        let progress: Record<string, EpisodeProgressItem> = {};
        try {
          progress = buildEpisodeProgressMap(await fetchEpisodeProgress(channel));
        } catch {
          progress = {};
        }
        let dialogAudit: Record<string, DialogAiAuditItem> = {};
        try {
          dialogAudit = await fetchDialogAuditChannel(channel);
        } catch {
          dialogAudit = {};
        }
        if (cancelled) return;
        setEpisodeProgressMap(progress);
        setDialogAuditMap(dialogAudit);
        setRows(attachEpisodeProgressColumns(res.rows || [], progress));
        const summary = await fetchRedoSummary(channel);
        if (cancelled) return;
        setRedoSummary(summary[0] ?? null);
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message || "読み込みに失敗しました");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [channel]);

  useEffect(() => {
    if (!channel || !videoParam) {
      return;
    }
    if (loading) {
      return;
    }
    const key = `${channel}-${videoParam}`;
    if (deepLinkAppliedRef.current === key) {
      return;
    }
    const match = rows.find((row) => normalizeVideo(row["動画番号"] || row["video"] || "") === videoParam);
    if (match) {
      setDetailRow(match);
      requestThumbForRow(match);
    }
    deepLinkAppliedRef.current = key;
  }, [channel, videoParam, loading, requestThumbForRow, rows]);

  useEffect(() => {
    const next = redoOnly
      ? rows.filter((row) => toBool(row["redo_script"], true) || toBool(row["redo_audio"], true))
      : rows;
    setFilteredRows(next);
    // サムネを上位40件だけ事前取得（ベストエフォート）
    next.slice(0, 40).forEach(requestThumbForRow);
  }, [rows, redoOnly, channel, requestThumbForRow]);

  const keyConceptDupes = useMemo(() => {
    const adoptedByKey: Record<string, string[]> = {};
    rows.forEach((row) => {
      if (!isAdoptedEpisodeRow(row)) return;
      const { keyNorm } = resolveEpisodeKey(row);
      if (!keyNorm) return;
      const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
      if (!vid) return;
      adoptedByKey[keyNorm] = [...(adoptedByKey[keyNorm] ?? []), vid];
    });
    Object.keys(adoptedByKey).forEach((k) => {
      adoptedByKey[k] = Array.from(new Set(adoptedByKey[k])).sort();
    });

    const dupByRow: Record<string, { keyRaw: string; conflicts: string[] }> = {};
    rows.forEach((row) => {
      if (isAdoptedEpisodeRow(row)) return;
      const { keyRaw, keyNorm } = resolveEpisodeKey(row);
      if (!keyNorm) return;
      const conflicts = adoptedByKey[keyNorm] ?? [];
      if (!conflicts.length) return;
      const ch = String(row["チャンネル"] || channel || "").trim().toUpperCase();
      const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
      if (!ch || !vid) return;
      dupByRow[`${ch}-${vid}`] = { keyRaw, conflicts };
    });

    return { adoptedByKey, dupByRow };
  }, [rows, channel]);

  const duplicateKeyConceptCount = useMemo(() => Object.keys(keyConceptDupes.dupByRow).length, [keyConceptDupes]);

  const columns = useMemo(() => {
    const first = rows[0];
    if (!first)
      return ["動画番号", "タイトル", "進捗", "台本(自動)", "音声(自動)", "整合", AUDIT_COLUMN, "投稿完了", "動画run", "CapCutドラフト", "更新日時", "台本パス"];
    const all = Object.keys(first);
    const priority = COMPACT_PRIORITY.filter((c) => all.includes(c));
    const rest = all.filter((c) => !priority.includes(c));
    const ordered = [...priority, ...rest];
    if (!ordered.includes("サムネ")) {
      const titleIndex = ordered.indexOf("タイトル");
      if (titleIndex >= 0) {
        ordered.splice(titleIndex + 1, 0, "サムネ");
      } else {
        ordered.unshift("サムネ");
      }
    }
    if (!ordered.includes("投稿完了")) {
      const progressIndex = ordered.indexOf("進捗");
      if (progressIndex >= 0) {
        ordered.splice(progressIndex + 1, 0, "投稿完了");
      } else {
        ordered.unshift("投稿完了");
      }
    }
    if (!ordered.includes(AUDIT_COLUMN)) {
      const alignIndex = ordered.indexOf("整合");
      if (alignIndex >= 0) {
        ordered.splice(alignIndex + 1, 0, AUDIT_COLUMN);
      } else {
        ordered.splice(0, 0, AUDIT_COLUMN);
      }
    }
    if (showAll) return ordered;
    // compact: keep first 16 cols (主要確認列を含む)
    return ordered.slice(0, Math.min(16, ordered.length));
  }, [rows, showAll]);

  useEffect(() => {
    if (!detailRow) return;
    setRedoScriptValue(toBool(detailRow["redo_script"], true));
    setRedoAudioValue(toBool(detailRow["redo_audio"], true));
    setRedoNoteValue(detailRow["redo_note"] || "");
  }, [detailRow]);

  return (
    <div className="planning-page">
      <div className="planning-page__controls">
        <label>
          チャンネル:
          <select value={channel} onChange={(e) => applyChannel(e.target.value)}>
            <option value="">未選択</option>
            {channelCodes.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <div className="planning-page__channel-icons">
          {channelCodes.map((c, index) => {
            const meta = CHANNEL_META[c] ?? {
              icon: "📺",
              color: META_COLOR_FALLBACK[index % META_COLOR_FALLBACK.length],
            };
            return (
            <button
              key={c}
              type="button"
              className={`planning-page__chip ${channel === c ? "is-active" : ""} ${meta.color}`}
              onClick={() => applyChannel(c)}
              title={c}
            >
              {channelMap[c]?.branding?.avatar_url ? (
                <img
                  src={channelMap[c]?.branding?.avatar_url || ""}
                  alt={c}
                  className="planning-page__chip-avatar"
                />
              ) : (
                <span className="planning-page__chip-icon" aria-hidden="true">
                  {meta.icon}
                </span>
              )}
              <span className="planning-page__chip-text">{c}</span>
            </button>
            );
          })}
        </div>
        <label className="planning-page__toggle">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          全列を表示
        </label>
        <label className="planning-page__toggle">
          <input
            type="checkbox"
            checked={redoOnly}
            onChange={(e) => setRedoOnly(e.target.checked)}
          />
          リテイクのみ
        </label>
        <button
          type="button"
          className="planning-page__refresh"
          onClick={async () => {
            if (!channel) return;
            setLoading(true);
            setError(null);
            try {
              const res = await fetchPlanningChannelCsv(channel);
              let progress: Record<string, EpisodeProgressItem> = {};
              try {
                progress = buildEpisodeProgressMap(await fetchEpisodeProgress(channel));
              } catch {
                progress = {};
              }
              let dialogAudit: Record<string, DialogAiAuditItem> = {};
              try {
                dialogAudit = await fetchDialogAuditChannel(channel);
              } catch {
                dialogAudit = {};
              }
              setEpisodeProgressMap(progress);
              setDialogAuditMap(dialogAudit);
              setRows(attachEpisodeProgressColumns(res.rows || [], progress));
              const summary = await fetchRedoSummary(channel);
              setRedoSummary(summary[0] ?? null);
            } catch (e: any) {
              setError(e?.message || "再読込に失敗しました");
            } finally {
              setLoading(false);
            }
          }}
          disabled={loading || !channel}
          title="外部で編集した企画CSVを再取得します"
        >
          企画を再読込
        </button>
        {redoSummary ? (
          <div className="planning-page__summary">
            <RedoBadge note="台本リテイク件数" label={`台本 ${redoSummary.redo_script}`} />
            <RedoBadge note="音声リテイク件数" label={`音声 ${redoSummary.redo_audio}`} />
            <RedoBadge note="両方リテイク件数" label={`両方 ${redoSummary.redo_both}`} />
          </div>
        ) : null}
        {duplicateKeyConceptCount > 0 ? (
          <div
            className="planning-page__dup-summary"
            title="採用済み回とキーコンセプトが重複している未採用行の数です"
          >
            キーコンセプト重複 {duplicateKeyConceptCount}
          </div>
        ) : null}
        {loading && <span className="planning-page__status">読み込み中...</span>}
        {error && <span className="planning-page__error">{error}</span>}
      </div>
      <div className="planning-page__table-wrapper">
        {!channel ? (
          <div className="shell-panel shell-panel--placeholder" style={{ border: "none", boxShadow: "none" }}>
            <h2>チャンネルを選択してください</h2>
            <p className="shell-panel__subtitle">上のチャンネル選択から企画CSVを読み込みます。</p>
          </div>
        ) : (
        <table className="planning-page__table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row, idx) => {
              const ch = normalizeChannelCode(String(row["チャンネル"] || channel || ""));
              const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
              const rowKey = ch && vid ? `${ch}-${vid}` : "";
              const dup = rowKey ? keyConceptDupes.dupByRow[rowKey] : null;
              return (
                <tr
                  key={idx}
                  className={`planning-page__row${dup ? " planning-page__row--dup-key-concept" : ""}`}
                  onClick={() => openDetailRow(row)}
                >
                {columns.map((col) => {
                  const isRedo = toBool(row["redo_script"], true) || toBool(row["redo_audio"], true);
                  const isLong = LONG_COLUMNS.has(col);
                  const isNarrow = NARROW_COLUMNS.has(col);
                  const isMedium = MEDIUM_COLUMNS.has(col);
                  const isThumb = THUMB_COLUMNS.has(col);
                  const isDupKeyConcept = col === "キーコンセプト" && Boolean(dup);
                  const thumbKey = rowKey;
                  const thumbs = thumbKey ? thumbMap[thumbKey] || [] : [];
                  if (isThumb && thumbKey && !thumbMap[thumbKey]) {
                    requestThumbForRow(row);
                  }
                  return (
                    <td
                      key={col}
                      className={`${isLong ? "planning-page__cell planning-page__cell--long" : "planning-page__cell"}${isNarrow ? " planning-page__cell--narrow" : ""}${
                        isMedium ? " planning-page__cell--medium" : ""
                      }${isThumb ? " planning-page__cell--thumb" : ""}${isDupKeyConcept ? " planning-page__cell--dup-key-concept" : ""} ${
                        isRedo ? "planning-page__cell--redo" : ""
                      }`}
                      title={row[col] ?? ""}
                    >
                      {col === "タイトル" && isRedo ? (
                        <span
                          className="planning-page__redo-dot"
                          title={row["redo_note"] || "リテイク対象"}
                          aria-label="リテイク対象"
                        />
                      ) : null}
                      {col === "キーコンセプト" ? (
                        (() => {
                          const value = String(row[col] ?? "");
                          if (!dup) {
                            return <span className="planning-page__cell-text">{value}</span>;
                          }
                          const conflictVideos = dup.conflicts ?? [];
                          const targetChannel = String(row["チャンネル"] || channel || "").trim().toUpperCase();
                          return (
                            <span className="planning-page__dup-cell" title="採用済み回とキーコンセプトが重複しています">
                              <span className="planning-page__badge planning-page__badge--dup">重複</span>
                              <span className="planning-page__cell-text planning-page__cell-text--flex">
                                {value || dup.keyRaw}
                              </span>
                              <span className="planning-page__dup-links">
                                {conflictVideos.slice(0, 6).map((v) => (
                                  <button
                                    key={`${targetChannel}-${v}`}
                                    type="button"
                                    className="planning-page__dup-link"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      goToVideoPage(targetChannel, v);
                                    }}
                                    title={`${targetChannel}-${v} を開く`}
                                  >
                                    {v}
                                  </button>
                                ))}
                                {conflictVideos.length > 6 ? <span className="muted">…</span> : null}
                              </span>
                            </span>
                          );
                        })()
                      ) : col === "投稿完了" ? (
                        (() => {
                          const progress = String(row["進捗"] ?? row["progress"] ?? "");
                          const locked =
                            toBool((row as any)["published_lock"], false) ||
                            progress.includes("投稿済み") ||
                            progress.includes("公開済み");
                          const ch = row["チャンネル"] || channel;
                          const vid = row["動画番号"] || row["video"] || "";
                          const token = normalizeVideo(vid);
                          const key = `${ch}-${token}`;
                          const isPublishing = publishingKey === key;
                          const isUnpublishing = unpublishingKey === key;
                          const isBusy = isPublishing || isUnpublishing;
                          return (
                            <label
                              className="planning-page__toggle"
                              onClick={(e) => e.stopPropagation()}
                              title={
                                locked
                                  ? "投稿済み（ロック中）: クリックで解除できます"
                                  : "チェックで投稿済みにする（ロック）"
                              }
                            >
                              <input
                                type="checkbox"
                                checked={locked || isBusy}
                                disabled={isBusy}
                                onChange={(e) => {
                                  e.stopPropagation();
                                  const next = e.target.checked;
                                  if (next && !locked) {
                                    markPublished(ch, vid);
                                    return;
                                  }
                                  if (!next && locked) {
                                    unmarkPublished(ch, vid);
                                  }
                                }}
                              />
                            </label>
                          );
                        })()
                      ) : col === "整合" ? (
                        (() => {
                          const value = String(row[col] ?? "");
                          const reason = String(row["整合理由"] ?? "");
                          const label = value || "未計測";
                          const cls =
                            value === "OK"
                              ? "planning-page__align planning-page__align--ok"
                              : value === "NG"
                                ? "planning-page__align planning-page__align--ng"
                                : value === "要確認"
                                  ? "planning-page__align planning-page__align--warn"
                                  : "planning-page__align planning-page__align--unknown";
                          return (
                            <span className={cls} title={reason || label}>
                              {label}
                            </span>
                          );
                        })()
                      ) : col === AUDIT_COLUMN ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? dialogAuditMap[vid] : null;
                          const badge = formatDialogAuditBadge(item);
                          return (
                            <span className={badge.cls} title={badge.title}>
                              {badge.label}
                            </span>
                          );
                        })()
                      ) : col === "動画ID" ? (
                        (() => {
                          const ch = row["チャンネル"] || channel;
                          const vid = row["動画番号"] || row["video"] || "";
                          const token = normalizeVideo(vid);
                          const hasLink = Boolean(ch && token);
                          return (
                            <span className="planning-page__video-cell">
                              <span className="planning-page__cell-text planning-page__cell-text--flex">{row[col] ?? ""}</span>
                              {hasLink ? (
                                <>
                                  <button
                                    type="button"
                                    className="planning-page__open"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      goToVideoPage(ch, vid);
                                    }}
                                    title="台本ページへ"
                                    aria-label="台本ページへ"
                                  >
                                    📝
                                  </button>
                                </>
                              ) : null}
                            </span>
                          );
                        })()
                      ) : col === "サムネ" ? (
                        thumbs.length ? (
                          (() => {
                            const thumb1 = pickTwoUpThumb(thumbs, "00_thumb_1");
                            const thumb2 = pickTwoUpThumb(thumbs, "00_thumb_2");
                            const ch =
                              row["チャンネル"] ||
                              row["channel"] ||
                              row["channel_code"] ||
                              row["CH"] ||
                              row["ch"] ||
                              channel;
                            const vid =
                              row["動画番号"] ||
                              row["動画ID"] ||
                              row["video"] ||
                              row["video_number"] ||
                              row["vid"] ||
                              row["番号"] ||
                              "";
                            const handlePreviewClick = (index: number) => {
                              setThumbPreviewItems(thumbs);
                              setThumbPreviewIndex(index);
                              setThumbPreview(thumbs[index]?.url ?? null);
                            };
                            if (thumb1 && thumb2) {
                              const extraCount = Math.max(0, thumbs.length - 2);
                              return (
                                <div className="planning-page__thumbs" title="クリックでサムネ編集（⌘/Ctrl/Alt クリックでプレビュー）">
                                  {[{ item: thumb1, stable: "00_thumb_1", label: "1" }, { item: thumb2, stable: "00_thumb_2", label: "2" }].map(
                                    ({ item, stable, label }, index) => (
                                      <button
                                        key={stable}
                                        type="button"
                                        className="planning-page__thumb planning-page__thumb--tiny"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          if (e.metaKey || e.ctrlKey || e.altKey) {
                                            handlePreviewClick(index);
                                            return;
                                          }
                                          if (ch && vid) {
                                            goToThumbnailsPage(String(ch), String(vid), { stable });
                                            return;
                                          }
                                          handlePreviewClick(index);
                                        }}
                                      >
                                        <img src={item.url} alt={`thumb:${stable}`} loading="lazy" draggable={false} />
                                        <span className="planning-page__thumb-slot">{label}</span>
                                        {index === 1 && extraCount > 0 ? (
                                          <span className="planning-page__thumb-count">+{extraCount}</span>
                                        ) : null}
                                      </button>
                                    )
                                  )}
                                </div>
                              );
                            }
                            return (
                              <button
                                type="button"
                                className="planning-page__thumb"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const thumbUrl = thumbs[0]?.url ?? "";
                                  if (e.metaKey || e.ctrlKey || e.altKey) {
                                    handlePreviewClick(0);
                                    return;
                                  }
                                  if (ch && vid) {
                                    goToThumbnailsPage(String(ch), String(vid));
                                    return;
                                  }
                                  const parsed = (() => {
                                    const match = String(thumbUrl)
                                      .replace(/^https?:\/\/[^/]+/i, "")
                                      .match(/\/thumbnails\/assets\/([^/]+)\/([^/]+)\//i);
                                    if (!match) return null;
                                    return { channel: match[1] ?? "", video: match[2] ?? "" };
                                  })();
                                  if (parsed?.channel && parsed?.video) {
                                    goToThumbnailsPage(parsed.channel, parsed.video);
                                    return;
                                  }
                                  handlePreviewClick(0);
                                }}
                                title="クリックでサムネ編集（⌘/Ctrl/Alt クリックでプレビュー）"
                              >
                                <img src={thumbs[0].url} alt="thumb" loading="lazy" draggable={false} />
                                {thumbs.length > 1 ? (
                                  <span className="planning-page__thumb-count">+{thumbs.length - 1}</span>
                                ) : null}
                              </button>
                            );
                          })()
                        ) : (
                          <span className="planning-page__cell-text muted">なし</span>
                        )
                      ) : col === "進捗" ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? episodeProgressMap[vid] : undefined;
                          const currentValue = String(row["進捗"] ?? row["progress"] ?? "");
                          const stale = Boolean(item?.issues?.includes("planning_stale_vs_status"));
                          return (
                            <span className="planning-page__progress-cell" title={currentValue || "—"}>
                              {stale ? <span className="planning-page__badge planning-page__badge--stale">古い</span> : null}
                              <span className="planning-page__cell-text planning-page__cell-text--flex">{currentValue || "—"}</span>
                            </span>
                          );
                        })()
                      ) : col === "台本(自動)" ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? episodeProgressMap[vid] : undefined;
                          const status = String(item?.script_status || "").trim().toLowerCase();
                          const fallback = String(row[col] ?? "").trim();
                          const label = item ? formatScriptLabel(status) || "—" : fallback || "—";
                          const badgeCls =
                            status === "completed" || status === "script_validated"
                              ? "planning-page__badge planning-page__badge--script-ok"
                              : status === "failed"
                                ? "planning-page__badge planning-page__badge--script-failed"
                                : status === "processing" || status === "in_progress" || status === "script_in_progress"
                                  ? "planning-page__badge planning-page__badge--script-running"
                                  : "planning-page__badge planning-page__badge--script-missing";
                          return (
                            <span className="planning-page__script-cell" title={`script_status=${status || "unknown"}`}>
                              <span className={badgeCls}>{label}</span>
                            </span>
                          );
                        })()
                      ) : col === "音声(自動)" ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? episodeProgressMap[vid] : undefined;
                          const ready = Boolean(item?.audio_ready);
                          const fallback = String(row[col] ?? "").trim();
                          const label = item ? (ready ? "OK" : "未生成") : fallback || "—";
                          const badgeCls = ready
                            ? "planning-page__badge planning-page__badge--audio-ok"
                            : "planning-page__badge planning-page__badge--audio-missing";
                          return (
                            <span className="planning-page__audio-cell" title={item ? `audio_ready=${ready ? "true" : "false"}` : label}>
                              <span className={badgeCls}>{label}</span>
                            </span>
                          );
                        })()
                      ) : col === "動画run" ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? episodeProgressMap[vid] : undefined;
                          const selected = item?.video_run_id ? String(item.video_run_id) : "";
                          const candidate = item?.capcut_draft_run_id ? String(item.capcut_draft_run_id) : "";
                          const fallback = String(row[col] ?? "").trim();
                          const label = selected || (candidate ? `候補:${candidate}` : fallback || "—");
                          const issues = item?.issues ?? [];
                          const unselected = !selected && Boolean(candidate) && issues.includes("video_run_unselected");
                          return (
                            <span className="planning-page__run-cell" title={label}>
                              {unselected ? <span className="planning-page__badge planning-page__badge--run-warn">未選択</span> : null}
                              <span className="planning-page__cell-text planning-page__cell-text--flex">{label}</span>
                            </span>
                          );
                        })()
                      ) : col === "CapCutドラフト" ? (
                        (() => {
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const item = vid ? episodeProgressMap[vid] : undefined;
                          const selected = item?.video_run_id ? String(item.video_run_id) : "";
                          const candidate = item?.capcut_draft_run_id ? String(item.capcut_draft_run_id) : "";
                          const isCandidate = !selected && Boolean(candidate);
                          const status = String(item?.capcut_draft_status || "").trim().toLowerCase();
                          const fallback = String(row[col] ?? "").trim();
                          const label = item ? formatCapcutLabel(status, isCandidate) || "—" : fallback || "—";
                          const badgeCls =
                            status === "ok"
                              ? "planning-page__badge planning-page__badge--capcut-ok"
                              : status === "broken"
                                ? "planning-page__badge planning-page__badge--capcut-broken"
                                : status === "missing"
                                  ? "planning-page__badge planning-page__badge--capcut-missing"
                                  : "planning-page__badge planning-page__badge--capcut-missing";
                          const target = item?.capcut_draft_target ? String(item.capcut_draft_target) : "";
                          const runId = selected || candidate;
                          const titleParts = [`status=${status || "unknown"}`];
                          if (runId) titleParts.push(`run=${runId}`);
                          if (target) titleParts.push(`target=${target}`);
                          return (
                            <span className="planning-page__capcut-cell" title={titleParts.join(" / ")}>
                              <span className={badgeCls}>{label}</span>
                            </span>
                          );
                        })()
                      ) : col === "タイトル" ? (
                        (() => {
                          const title = String(row["タイトル"] ?? "");
                          const ch = row["チャンネル"] || channel;
                          const vid = row["動画番号"] || row["video"] || "";
                          const token = normalizeVideo(vid);
                          const key = `${ch}-${token || String(vid)}`;
                          const copied = copiedTitleKey === key;
                          return (
                            <span className="planning-page__title-cell">
                              <span className="planning-page__cell-text planning-page__cell-text--flex">{title}</span>
                              <button
                                type="button"
                                className={`planning-page__copy ${copied ? "is-copied" : ""}`}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  copyTitle(key, title);
                                }}
                                title={copied ? "コピーしました" : "タイトルをコピー"}
                                aria-label="タイトルをコピー"
                              >
                                {copied ? "✓" : "📋"}
                              </button>
                            </span>
                          );
                        })()
                      ) : (
                        <span className="planning-page__cell-text" title={row[col] ?? ""}>
                          {row[col] ?? ""}
                          {isLong && (row[col] ?? "").length > 0 ? (
                            <button
                              type="button"
                              className="planning-page__expand"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedCell({ key: col, value: row[col] ?? "" });
                              }}
                            >
                              全文
                            </button>
                          ) : null}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
              );
            })}
          </tbody>
        </table>
        )}
      </div>

      {detailRow && (
        <div className="planning-page__overlay" onClick={closeDetailRow}>
          <div className="planning-page__detail" onClick={(e) => e.stopPropagation()}>
            <div className="planning-page__detail-header">
              <div className="planning-page__detail-title">
                {detailRow["動画ID"] || detailRow["動画番号"] || ""} {detailRow["タイトル"] || ""}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                  type="button"
                  className="planning-page__close"
                  onClick={() => {
                    const ch = detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel;
                    const vid = detailRow["動画番号"] || detailRow["video"] || "";
                    goToVideoPage(ch, vid);
                  }}
                >
                  案件ページへ
                </button>
                <button
                  type="button"
                  className="planning-page__close"
                  onClick={() => {
                    const rawCh = detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel;
                    const rawVid = detailRow["動画番号"] || detailRow["video"] || "";
                    const ch = String(rawCh || "").toUpperCase();
                    const vid = normalizeVideo(rawVid);
                    if (!ch || !vid) return;
                    navigate(`/workflow?channel=${encodeURIComponent(ch)}&video=${encodeURIComponent(vid)}`);
                  }}
                >
                  制作フロー
                </button>
                <button
                  type="button"
                  className="planning-page__close"
                  onClick={() => {
                    const rawCh = detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel;
                    const rawVid = detailRow["動画番号"] || detailRow["video"] || "";
                    const ch = String(rawCh || "").toUpperCase();
                    const vid = normalizeVideo(rawVid);
                    if (!ch || !vid) return;
                    navigate(`/studio?channel=${encodeURIComponent(ch)}&video=${encodeURIComponent(vid)}`);
                  }}
                >
                  Studio
                </button>
                <button type="button" className="planning-page__close" onClick={closeDetailRow}>
                  × 閉じる
                </button>
              </div>
            </div>
            <div className="planning-page__detail-body">
              {(() => {
                const progress = String(detailRow["進捗"] ?? "");
                const locked =
                  toBool((detailRow as any)["published_lock"], false) ||
                  progress.includes("投稿済み") ||
                  progress.includes("公開済み");
                const ch = detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel;
                const vid = detailRow["動画番号"] || detailRow["video"] || "";
                const token = normalizeVideo(vid);
                const key = `${ch}-${token}`;
                const chNorm = String(ch || "").trim().toUpperCase();
                const rowKey = chNorm && token ? `${chNorm}-${token}` : "";
                const dup = rowKey ? keyConceptDupes.dupByRow[rowKey] : null;
                const dialogAudit = token ? dialogAuditMap[token] : null;
                const dialogBadge = formatDialogAuditBadge(dialogAudit);
                const isPublishing = publishingKey === key;
                const isUnpublishing = unpublishingKey === key;
                const isBusy = isPublishing || isUnpublishing;
                return (
                  <>
                    {dup ? (
                      <div className="planning-page__detail-row">
                        <div className="planning-page__detail-key">キーコンセプト重複</div>
                        <div className="planning-page__detail-value">
                          <span className="planning-page__badge planning-page__badge--dup">重複</span>
                          <span className="planning-page__dup-links">
                            {(dup.conflicts ?? []).slice(0, 12).map((v) => (
                              <button
                                key={`${chNorm}-${v}`}
                                type="button"
                                className="planning-page__dup-link"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  goToVideoPage(chNorm, v);
                                }}
                                title={`${chNorm}-${v} を開く`}
                              >
                                {v}
                              </button>
                            ))}
                            {(dup.conflicts ?? []).length > 12 ? <span className="muted">…</span> : null}
                          </span>
                        </div>
                      </div>
                    ) : null}
                    <div className="planning-page__detail-row">
                      <div className="planning-page__detail-key">監査(参考)</div>
                      <div className="planning-page__detail-value">
                        <span className={dialogBadge.cls} title={dialogBadge.title}>
                          {dialogBadge.label}
                        </span>
                        {dialogAudit?.audited_at ? <span className="muted"> {dialogAudit.audited_at}</span> : null}
                      </div>
                    </div>
                    <div className="planning-page__detail-row">
                      <div className="planning-page__detail-key">投稿完了</div>
                      <div className="planning-page__detail-value">
                        <label
                          className="planning-page__toggle"
                          title={
                            locked
                              ? "投稿済み（ロック中）: クリックで解除できます"
                              : "チェックで投稿済みにする（ロック）（以後は原則触らない指標）"
                          }
                        >
                          <input
                            type="checkbox"
                            checked={locked || isBusy}
                            disabled={isBusy}
                            onChange={(e) => {
                              const next = e.target.checked;
                              if (next && !locked) {
                                markPublished(ch, vid);
                                return;
                              }
                              if (!next && locked) {
                                unmarkPublished(ch, vid);
                              }
                            }}
                          />
                          {locked ? "投稿済み（ロック中）" : "投稿済みにする（ロック）"}
                        </label>
                        {isBusy ? <span className="planning-page__cell-text muted">処理中...</span> : null}
                      </div>
                    </div>
                    <div className="planning-page__detail-row">
                      <div className="planning-page__detail-key">リテイク（台本）</div>
                      <div className="planning-page__detail-value">
                        <label className="planning-page__toggle">
                          <input
                            type="checkbox"
                            checked={redoScriptValue}
                            disabled={locked}
                            onChange={(e) => setRedoScriptValue(e.target.checked)}
                          />
                          再作成が必要
                        </label>
                      </div>
                    </div>
                    <div className="planning-page__detail-row">
                      <div className="planning-page__detail-key">リテイク（音声）</div>
                      <div className="planning-page__detail-value">
                        <label className="planning-page__toggle">
                          <input
                            type="checkbox"
                            checked={redoAudioValue}
                            disabled={locked}
                            onChange={(e) => setRedoAudioValue(e.target.checked)}
                          />
                          再収録が必要
                        </label>
                      </div>
                    </div>
                    <div className="planning-page__detail-row">
                      <div className="planning-page__detail-key">リテイクメモ</div>
                      <div className="planning-page__detail-value">
                        <textarea
                          className="planning-page__note"
                          value={redoNoteValue}
                          disabled={locked}
                          onChange={(e) => setRedoNoteValue(e.target.value)}
                          rows={3}
                        />
                        <div className="planning-page__note-actions">
                          <button
                            className="planning-page__save"
                            onClick={async () => {
                              if (!detailRow) return;
                              setSaving(true);
                              try {
                                await updateVideoRedo(
                                  detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel,
                                  detailRow["動画番号"] || detailRow["video"] || "",
                                  {
                                    redo_script: redoScriptValue,
                                    redo_audio: redoAudioValue,
                                    redo_note: redoNoteValue,
                                  }
                                );
                                setRows((prev) =>
                                  prev.map((r) =>
                                    normalizeVideo(r["動画番号"] || r["video"]) === normalizeVideo(detailRow["動画番号"] || detailRow["video"])
                                      ? {
                                          ...r,
                                          redo_script: redoScriptValue ? "true" : "false",
                                          redo_audio: redoAudioValue ? "true" : "false",
                                          redo_note: redoNoteValue,
                                        }
                                      : r
                                  )
                                );
                                setDetailRow((prev) =>
                                  prev
                                    ? {
                                        ...prev,
                                        redo_script: redoScriptValue ? "true" : "false",
                                        redo_audio: redoAudioValue ? "true" : "false",
                                        redo_note: redoNoteValue,
                                      }
                                    : prev
                                );
                              } finally {
                                setSaving(false);
                              }
                            }}
                            disabled={saving || locked}
                          >
                            {saving ? "保存中..." : locked ? "ロック中" : "保存"}
                          </button>
                        </div>
                      </div>
                    </div>
                  </>
                );
              })()}
              {Object.entries(detailRow).map(([k, v]) => (
                <div key={k} className="planning-page__detail-row">
                  <div className="planning-page__detail-key">{k}</div>
                  <div className="planning-page__detail-value">{v || ""}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {thumbPreview ? (
        <div className="planning-page__overlay" onClick={() => setThumbPreview(null)}>
          <div className="planning-page__preview" onClick={(e) => e.stopPropagation()}>
            <button className="planning-page__close" onClick={() => setThumbPreview(null)}>× 閉じる</button>
            <div className="planning-page__preview-body">
              <img src={thumbPreview} alt="thumbnail preview" loading="lazy" draggable={false} />
              {thumbPreviewItems && thumbPreviewItems.length > 1 ? (
                <div className="planning-page__preview-strip">
                  {thumbPreviewItems.map((item, i) => (
                    <button
                      key={`${item.path}-${i}`}
                      type="button"
                      className={`planning-page__preview-thumb ${i === thumbPreviewIndex ? "is-active" : ""}`}
                      onClick={() => {
                        setThumbPreviewIndex(i);
                        setThumbPreview(item.url);
                      }}
                    >
                      <img src={item.url} alt={`thumb ${i + 1}`} loading="lazy" />
                    </button>
                  ))}
                </div>
              ) : null}
              <a href={thumbPreview} target="_blank" rel="noreferrer" className="planning-page__preview-link">別タブで開く ↗</a>
            </div>
          </div>
        </div>
      ) : null}

      {selectedCell ? (
        <div className="planning-page__inspector">
          <div className="planning-page__inspector-header">
            <div className="planning-page__inspector-title">{selectedCell.key}</div>
            <button className="planning-page__close" onClick={() => setSelectedCell(null)}>
              × 閉じる
            </button>
          </div>
          <div className="planning-page__inspector-body">
            <pre className="planning-page__inspector-text">{selectedCell.value}</pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}
