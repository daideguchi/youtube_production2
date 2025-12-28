import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import {
  fetchPlanningChannelCsv,
  updateVideoRedo,
  fetchRedoSummary,
  lookupThumbnails,
  markVideoPublishedLocked,
  unmarkVideoPublishedLocked,
  updatePlanningChannelProgress,
} from "../api/client";
import type { ChannelSummary, RedoSummaryItem, ThumbnailLookupItem } from "../api/types";
import { RedoBadge } from "../components/RedoBadge";
import type { ShellOutletContext } from "../layouts/AppShell";
import { safeLocalStorage } from "../utils/safeStorage";
import "./PlanningPage.css";

type Row = Record<string, string>;

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
  return (value ?? "").trim().toUpperCase();
}

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

const NARROW_COLUMNS = new Set(["動画番号", "動画ID", "進捗", "整合", "投稿完了"]);
const MEDIUM_COLUMNS = new Set(["タイトル", "音声生成", "音声品質", "納品"]);
const THUMB_COLUMNS = new Set(["サムネ"]);

const COMPACT_PRIORITY = [
  "動画番号",
  "動画ID",
  "タイトル",
  "サムネ",
  "進捗",
  "整合",
  "投稿完了",
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
  const [progressEditing, setProgressEditing] = useState<{ key: string; value: string } | null>(null);
  const [progressSaving, setProgressSaving] = useState<Record<string, boolean>>({});
  const [progressErrors, setProgressErrors] = useState<Record<string, string>>({});
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

  const beginProgressEdit = useCallback(
    (row: Row) => {
      const ch = String(row["チャンネル"] || channel || "").trim().toUpperCase();
      const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
      if (!ch || !vid) {
        return;
      }
      const key = `${ch}-${vid}`;
      const current = String(row["進捗"] ?? row["progress"] ?? "").trim();
      setProgressEditing({ key, value: current });
      setProgressErrors((currentErrors) => {
        if (!currentErrors[key]) return currentErrors;
        const next = { ...currentErrors };
        delete next[key];
        return next;
      });
    },
    [channel]
  );

  const cancelProgressEdit = useCallback(() => {
    setProgressEditing(null);
  }, []);

  const saveProgressEdit = useCallback(
    async (row: Row) => {
      const ch = String(row["チャンネル"] || channel || "").trim().toUpperCase();
      const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
      if (!ch || !vid) {
        return;
      }
      const key = `${ch}-${vid}`;
      const nextProgressRaw =
        progressEditing?.key === key
          ? progressEditing.value
          : String(row["進捗"] ?? row["progress"] ?? "");
      const nextProgress = nextProgressRaw.trim();
      if (!nextProgress) {
        setProgressErrors((current) => ({ ...current, [key]: "進捗が空です。" }));
        return;
      }
      const currentProgress = String(row["進捗"] ?? row["progress"] ?? "").trim();
      if (nextProgress === currentProgress) {
        setProgressEditing(null);
        return;
      }

      setProgressSaving((current) => ({ ...current, [key]: true }));
      setProgressErrors((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      try {
        const expectedUpdatedAt = String(row["更新日時"] ?? row["updated_at"] ?? "").trim() || null;
        const updated = await updatePlanningChannelProgress(ch, vid, {
          progress: nextProgress,
          expectedUpdatedAt,
        });
        setRows((currentRows) =>
          currentRows.map((candidate) => {
            const candCh = String(candidate["チャンネル"] || ch || "").trim().toUpperCase();
            const candVid = normalizeVideo(candidate["動画番号"] || candidate["video"] || "");
            if (candCh === ch && candVid === vid) {
              return {
                ...candidate,
                進捗: updated.progress ?? nextProgress,
                更新日時: updated.updated_at ?? candidate["更新日時"] ?? "",
              };
            }
            return candidate;
          })
        );
        setProgressEditing(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setProgressErrors((current) => ({ ...current, [key]: message }));
      } finally {
        setProgressSaving((current) => ({ ...current, [key]: false }));
      }
    },
    [channel, progressEditing]
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
      const ch = row["チャンネル"] || channel;
      const vid = row["動画番号"] || row["video"] || "";
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

      lookupThumbnails(ch, vid, row["タイトル"] || undefined, 1)
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
        const nextRows = res.rows || [];
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
      const nextRows = res.rows || [];
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
        setRows(res.rows || []);
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

  const columns = useMemo(() => {
    const first = rows[0];
    if (!first) return ["動画番号", "タイトル", "進捗", "投稿完了", "更新日時", "台本パス"];
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
              setRows(res.rows || []);
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
	            {filteredRows.map((row, idx) => (
	              <tr
	                key={idx}
	                className="planning-page__row"
	                onClick={() => openDetailRow(row)}
	              >
                {columns.map((col) => {
                  const isRedo = toBool(row["redo_script"], true) || toBool(row["redo_audio"], true);
                  const isLong = LONG_COLUMNS.has(col);
                  const isNarrow = NARROW_COLUMNS.has(col);
                  const isMedium = MEDIUM_COLUMNS.has(col);
                  const isThumb = THUMB_COLUMNS.has(col);
                  const thumbKey = `${row["チャンネル"] || channel}-${row["動画番号"] || row["video"] || ""}`;
                  const thumbs = thumbMap[thumbKey] || [];
                  if (isThumb && !thumbMap[thumbKey]) {
                    requestThumbForRow(row);
                  }
                  return (
                    <td
                      key={col}
                      className={`${isLong ? "planning-page__cell planning-page__cell--long" : "planning-page__cell"}${isNarrow ? " planning-page__cell--narrow" : ""}${
                        isMedium ? " planning-page__cell--medium" : ""
                      }${isThumb ? " planning-page__cell--thumb" : ""} ${isRedo ? "planning-page__cell--redo" : ""}`}
                      title={row[col] ?? ""}
                    >
                      {col === "タイトル" && isRedo ? (
                        <span
                          className="planning-page__redo-dot"
                          title={row["redo_note"] || "リテイク対象"}
                          aria-label="リテイク対象"
                        />
                      ) : null}
                      {col === "投稿完了" ? (
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
                              ) : null}
                            </span>
                          );
                        })()
                      ) : col === "サムネ" ? (
                        thumbs.length ? (
                          <button
                            type="button"
                            className="planning-page__thumb"
                            onClick={(e) => {
                              e.stopPropagation();
                              setThumbPreviewItems(thumbs);
                              setThumbPreviewIndex(0);
                              setThumbPreview(thumbs[0].url);
                            }}
                            title="サムネをプレビュー"
                          >
                            <img src={thumbs[0].url} alt="thumb" loading="lazy" />
                            {thumbs.length > 1 ? (
                              <span className="planning-page__thumb-count">+{thumbs.length - 1}</span>
                            ) : null}
                          </button>
                        ) : (
                          <span className="planning-page__cell-text muted">なし</span>
                        )
                      ) : col === "進捗" ? (
                        (() => {
                          const ch = String(row["チャンネル"] || channel || "").trim().toUpperCase();
                          const vid = normalizeVideo(row["動画番号"] || row["video"] || "");
                          const rowKey = ch && vid ? `${ch}-${vid}` : "";
                          const isEditing = Boolean(rowKey && progressEditing?.key === rowKey);
                          const busy = Boolean(rowKey && progressSaving[rowKey]);
                          const errorMessage = rowKey ? progressErrors[rowKey] : null;
                          const currentValue = String(row["進捗"] ?? row["progress"] ?? "");
                          const draftValue = isEditing ? progressEditing?.value ?? "" : currentValue;
                          const changed = draftValue.trim() !== currentValue.trim();
                          return (
                            <div className="planning-page__progress">
                              {isEditing ? (
                                <>
                                  <input
                                    type="text"
                                    value={draftValue}
                                    onClick={(e) => e.stopPropagation()}
                                    onChange={(e) => {
                                      e.stopPropagation();
                                      setProgressEditing((current) =>
                                        current && current.key === rowKey ? { ...current, value: e.target.value } : current
                                      );
                                    }}
                                    onKeyDown={(e) => {
                                      e.stopPropagation();
                                      if (e.key === "Enter") {
                                        e.preventDefault();
                                        saveProgressEdit(row);
                                      }
                                      if (e.key === "Escape") {
                                        e.preventDefault();
                                        cancelProgressEdit();
                                      }
                                    }}
                                    disabled={busy}
                                    className="planning-page__progress-input"
                                    aria-label={`${rowKey} 進捗`}
                                  />
                                  <button
                                    type="button"
                                    className="planning-page__progress-save"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      saveProgressEdit(row);
                                    }}
                                    disabled={busy || !changed || !draftValue.trim()}
                                    title={changed ? "進捗を保存" : "変更なし"}
                                  >
                                    {busy ? "保存中…" : "保存"}
                                  </button>
                                  <button
                                    type="button"
                                    className="planning-page__progress-cancel"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      cancelProgressEdit();
                                    }}
                                    disabled={busy}
                                  >
                                    取消
                                  </button>
                                </>
                              ) : (
                                <>
                                  <span className="planning-page__cell-text planning-page__cell-text--flex">
                                    {currentValue || "—"}
                                  </span>
                                  <button
                                    type="button"
                                    className="planning-page__progress-edit"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      beginProgressEdit(row);
                                    }}
                                    disabled={!rowKey || loading}
                                    title="進捗を編集"
                                  >
                                    ✏️
                                  </button>
                                </>
                              )}
                              {errorMessage ? <span className="planning-page__progress-error">{errorMessage}</span> : null}
                            </div>
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
            ))}
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
                const isPublishing = publishingKey === key;
                const isUnpublishing = unpublishingKey === key;
                const isBusy = isPublishing || isUnpublishing;
                return (
                  <>
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
              <img src={thumbPreview} alt="thumbnail preview" loading="lazy" />
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
