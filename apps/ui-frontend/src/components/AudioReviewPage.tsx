import { useCallback, useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  API_BASE_URL,
  fetchAudioReviewItems,
  fetchChannels,
  runAudioTtsV2FromScript,
  fetchAText,
  updateVideoRedo,
} from "../api/client";
import type { AudioReviewItem, ChannelSummary } from "../api/types";
import { translateStatus } from "../utils/i18n";
import { BatchTtsProgressPanel } from "./BatchTtsProgressPanel";
import { RedoBadge } from "./RedoBadge";

function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(seconds)) {
    return "-";
  }
  const rounded = Math.max(seconds, 0);
  const minutes = Math.floor(rounded / 60);
  const remain = Math.round((rounded % 60) * 10) / 10;
  if (minutes === 0) {
    return `${remain.toFixed(remain % 1 === 0 ? 0 : 1)} 秒`;
  }
  return `${minutes}分 ${remain.toFixed(remain % 1 === 0 ? 0 : 1)}秒`;
}

const toBool = (v: any, fallback = true) => {
  if (v === true || v === false) return v;
  if (typeof v === "string") {
    const s = v.toLowerCase();
    if (["true", "1", "yes", "y", "ok", "redo"].includes(s)) return true;
    if (["false", "0", "no", "n"].includes(s)) return false;
  }
  return fallback;
};

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "更新なし";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const AUDIO_STAGE_LABEL: Record<string, string> = {
  completed: "音声完了",
  in_progress: "音声生成中",
  review: "音声レビュー待ち",
  blocked: "要対応",
  pending: "未着手",
  unknown: "不明",
};

type SortKey = "recent" | "duration_desc" | "duration_asc";

const SUBTITLE_STAGE_LABEL: Record<string, string> = {
  completed: "字幕完了",
  in_progress: "字幕生成中",
  review: "字幕レビュー待ち",
  blocked: "字幕要対応",
  pending: "字幕未着手",
  unknown: "字幕不明",
};

function normalizeWorkspacePath(rawPath: string | null | undefined): string {
  if (!rawPath) {
    return "/projects";
  }
  const base = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
  try {
    const url = new URL(rawPath, base);
    url.searchParams.set("tab", "audio");
    return `${url.pathname}${url.search}`;
  } catch {
    const [pathPart, queryPart] = rawPath.split("?");
    const params = new URLSearchParams(queryPart ?? "");
    params.set("tab", "audio");
    const pathname = pathPart.startsWith("/") ? pathPart : `/${pathPart}`;
    const search = params.toString();
    return `${pathname}${search ? `?${search}` : ""}`;
  }
}

function resolveAudioSrc(path?: string | null): string | null {
  if (!path) {
    return null;
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${normalized}`;
}

export function AudioReviewPage() {
  const navigate = useNavigate();
  const [channels, setChannels] = useState<ChannelSummary[]>([]);
  const [channelLoading, setChannelLoading] = useState(false);
  const [channelError, setChannelError] = useState<string | null>(null);

  const [items, setItems] = useState<AudioReviewItem[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [logModalOpen, setLogModalOpen] = useState(false);
  const [logModalTitle, setLogModalTitle] = useState<string>("");
  const [logModalContent, setLogModalContent] = useState<string>("");
  const [logModalError, setLogModalError] = useState<string | null>(null);
  const [logModalLoading, setLogModalLoading] = useState(false);
  const [aTextModalOpen, setATextModalOpen] = useState(false);
  const [aTextModalTitle, setATextModalTitle] = useState<string>("");
  const [aTextModalContent, setATextModalContent] = useState<string>("");
  const [aTextModalError, setATextModalError] = useState<string | null>(null);
  const [aTextModalLoading, setATextModalLoading] = useState(false);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const openLogModal = useCallback(async (url: string, title: string) => {
    setLogModalOpen(true);
    setLogModalTitle(title);
    setLogModalContent("");
    setLogModalError(null);
    setLogModalLoading(true);
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      const text = await resp.text();
      setLogModalContent(text);
    } catch (err) {
      setLogModalError(err instanceof Error ? err.message : String(err));
    } finally {
      setLogModalLoading(false);
    }
  }, []);
  const openATextModal = useCallback(
    async (channel: string, video: string) => {
      setATextModalOpen(true);
      setATextModalTitle(`${channel}-${video} Aテキスト`);
      setATextModalContent("");
      setATextModalError(null);
      setATextModalLoading(true);
      try {
        const text = await fetchAText(channel, video);
        setATextModalContent(text);
      } catch (err) {
        setATextModalError(err instanceof Error ? err.message : String(err));
      } finally {
        setATextModalLoading(false);
      }
    },
    []
  );
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runBusyKey, setRunBusyKey] = useState<string | null>(null);

  const [channelFilter, setChannelFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("recent");
  const [showManualOnly, setShowManualOnly] = useState(false);
  const [showQualityAttentionOnly, setShowQualityAttentionOnly] = useState(false);
  const [showSubtitlePendingOnly, setShowSubtitlePendingOnly] = useState(false);
  const [showRedoOnly, setShowRedoOnly] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [autoplayNext, setAutoplayNext] = useState(true);
  const [currentItemKey, setCurrentItemKey] = useState<string | null>(null);
  const audioRefs = useRef<Map<string, HTMLAudioElement>>(new Map());
  const stopCardPropagation = useCallback((event: SyntheticEvent) => {
    event.stopPropagation();
  }, []);

  useEffect(() => {
    setChannelLoading(true);
    setChannelError(null);
    fetchChannels()
      .then((data) => {
        setChannels(data);
        if (!channelFilter && data.length > 0) {
          setChannelFilter("all");
        }
      })
      .catch((error: unknown) => {
        setChannelError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        setChannelLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshItems = useCallback(
    (params?: { channel?: string; status?: string; video?: string }) => {
      setItemsLoading(true);
      setItemsError(null);
      fetchAudioReviewItems(params)
        .then((data) => setItems(data))
        .catch((error: unknown) => {
          setItemsError(error instanceof Error ? error.message : String(error));
        })
        .finally(() => {
          setItemsLoading(false);
        });
    },
    []
  );

  useEffect(() => {
    const selectedChannelParam = channelFilter && channelFilter !== "all" ? channelFilter : undefined;
    const selectedStatusParam = statusFilter && statusFilter !== "all" ? statusFilter : undefined;
    refreshItems({ channel: selectedChannelParam, status: selectedStatusParam });
  }, [channelFilter, statusFilter, refreshItems]);

  const normalizedKeyword = searchKeyword.trim().toLowerCase();
  const processedItems = useMemo(() => {
    let data = items;
    if (normalizedKeyword) {
      data = data.filter((item) => {
        const haystacks = [item.video, item.channel, item.title ?? "", item.status, item.audio_quality_status ?? ""];
        return haystacks.some((value) => value.toLowerCase().includes(normalizedKeyword));
      });
    }
    if (showManualOnly) {
      data = data.filter((item) => (item.manual_pause_count ?? 0) > 0);
    }
    if (showQualityAttentionOnly) {
      data = data.filter((item) => item.audio_quality_status && !/ok|良好|完了|問題なし/i.test(item.audio_quality_status));
    }
    if (showSubtitlePendingOnly) {
      data = data.filter((item) => item.subtitle_stage !== "completed");
    }
    if (showRedoOnly) {
      const toBool = (v: any, fallback = true) => {
        if (v === true || v === false) return v;
        if (typeof v === "string") {
          const s = v.toLowerCase();
          if (["true", "1", "yes", "y", "ok", "redo"].includes(s)) return true;
          if (["false", "0", "no", "n"].includes(s)) return false;
        }
        return fallback;
      };
      data = data.filter((item) => {
        const redoAudio = (item as any)["redo_audio"];
        const redoScript = (item as any)["redo_script"];
        return toBool(redoAudio, true) || toBool(redoScript, true);
      });
    }
    const sorted = [...data];
    sorted.sort((a, b) => {
      if (sortKey === "recent") {
        const aTime = a.audio_updated_at ? new Date(a.audio_updated_at).getTime() : 0;
        const bTime = b.audio_updated_at ? new Date(b.audio_updated_at).getTime() : 0;
        return bTime - aTime;
      }
      const aDuration = a.audio_duration_seconds ?? 0;
      const bDuration = b.audio_duration_seconds ?? 0;
      if (sortKey === "duration_desc") {
        return bDuration - aDuration;
      }
      return aDuration - bDuration;
    });
    return sorted;
  }, [items, normalizedKeyword, showManualOnly, showQualityAttentionOnly, showSubtitlePendingOnly, showRedoOnly, sortKey]);

  const processedKeys = useMemo(() => processedItems.map((item) => `${item.channel}-${item.video}`), [processedItems]);

  const channelMetaMap = useMemo(() => {
    const map = new Map<string, ChannelSummary>();
    channels.forEach((channel) => {
      map.set(channel.code, channel);
    });
    return map;
  }, [channels]);

  const reviewStats = useMemo(() => {
    const total = processedItems.length;
    if (total === 0) {
      return [];
    }
    const completed = processedItems.filter((item) => item.audio_stage === "completed").length;
    const needsAttention = processedItems.filter((item) => {
      if (!item.audio_quality_status) {
        return false;
      }
      return !/ok|良好|完了|問題なし/i.test(item.audio_quality_status);
    }).length;
    const manualPauses = processedItems.filter((item) => (item.manual_pause_count ?? 0) > 0).length;
    const subtitlePending = processedItems.filter((item) => item.subtitle_stage !== "completed").length;
    return [
      {
        label: "音声完了",
        value: completed,
        description: `${Math.round((completed / total) * 100)}% 完了`,
        onClick: () => setStatusFilter((current) => (current === "completed" ? "all" : "completed")),
        active: statusFilter === "completed",
      },
      {
        label: "品質確認要",
        value: needsAttention,
        description: "品質ステータスに注意",
        onClick: () => setShowQualityAttentionOnly((current) => !current),
        active: showQualityAttentionOnly,
      },
      {
        label: "手動ポーズあり",
        value: manualPauses,
        description: "manualタグのある案件",
        onClick: () => setShowManualOnly((current) => !current),
        active: showManualOnly,
      },
      {
        label: "字幕未完",
        value: subtitlePending,
        description: "字幕仕上げ待ち",
        onClick: () => setShowSubtitlePendingOnly((current) => !current),
        active: showSubtitlePendingOnly,
      },
    ];
  }, [processedItems, showManualOnly, showQualityAttentionOnly, showSubtitlePendingOnly, statusFilter]);

  useEffect(() => {
    const keys = new Set(processedKeys);
    for (const key of Array.from(audioRefs.current.keys())) {
      if (!keys.has(key)) {
        audioRefs.current.delete(key);
      }
    }
    if (currentItemKey && !keys.has(currentItemKey)) {
      setCurrentItemKey(null);
    }
  }, [processedKeys, currentItemKey]);

  const playItem = useCallback(
    (key: string | null, resetTime = false) => {
      if (!key) {
        return;
      }
      const target = audioRefs.current.get(key);
      if (!target) {
        return;
      }
      if (resetTime) {
        target.currentTime = 0;
      }
      target.play().catch(() => {
        target.focus();
      });
      setCurrentItemKey(key);
    },
    []
  );

  const playAdjacent = useCallback(
    (direction: -1 | 1) => {
      if (!processedKeys.length) {
        return;
      }
      const current = currentItemKey ? processedKeys.indexOf(currentItemKey) : -1;
      const nextIndex = current < 0 ? (direction === 1 ? 0 : processedKeys.length - 1) : current + direction;
      if (nextIndex < 0 || nextIndex >= processedKeys.length) {
        return;
      }
      playItem(processedKeys[nextIndex], true);
    },
    [currentItemKey, playItem, processedKeys]
  );

  const currentIndices = useMemo(() => {
    if (!currentItemKey) {
      return { index: -1, canPrev: false, canNext: processedKeys.length > 0 };
    }
    const index = processedKeys.indexOf(currentItemKey);
    return {
      index,
      canPrev: index > 0,
      canNext: index >= 0 && index < processedKeys.length - 1,
    };
  }, [currentItemKey, processedKeys]);

  const handleAutoplay = useCallback(
    (key: string) => {
      if (!autoplayNext) {
        return;
      }
      const currentIndex = processedKeys.indexOf(key);
      if (currentIndex === -1) {
        return;
      }
      const nextKey = processedKeys[currentIndex + 1];
      if (nextKey) {
        playItem(nextKey, true);
      }
    },
    [autoplayNext, playItem, processedKeys]
  );

  const activeChannelName = useMemo(() => {
    if (!channelFilter || channelFilter === "all") {
      return "すべてのチャンネル";
    }
    const target = channels.find((channel) => channel.code === channelFilter);
    return target?.name || target?.branding?.title || channelFilter;
  }, [channelFilter, channels]);

  return (
    <div className="audio-review">
      <header className="audio-review__header">
        <div>
          <h1>音声レビュー</h1>
          <p className="audio-review__subtitle">
            完成済み音声を横断的にチェックし、気になる案件を音声タブですぐ開けます。
          </p>
        </div>
        <div className="audio-review__summary">
          <span className="audio-review__summary-badge">{processedItems.length} 件</span>
          <span className="audio-review__summary-text">{activeChannelName}</span>
        </div>
      </header>

      <BatchTtsProgressPanel />

      <section className="audio-review__notice message message--info">
        <p className="muted">
          基本フロー: 案件詳細の「音声タブ」で再生成・最終WAV/SRT/ログを確認。ここでは横断チェックと個別再生成のみ行います。
        </p>
      </section>

      {runMessage ? <div className="message message--success">{runMessage}</div> : null}
      {runError ? <div className="message message--danger">{runError}</div> : null}
      {toast ? <div className="message message--info">{toast}</div> : null}
      {logModalOpen ? (
        <div className="modal-backdrop" onClick={() => setLogModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal__header">
              <h3>{logModalTitle || "ログ"}</h3>
              <button
                className="workspace-button workspace-button--ghost"
                style={{ background: "#0f172a", color: "#fff", borderColor: "#0f172a" }}
                onClick={() => setLogModalOpen(false)}
              >
                閉じる
              </button>
            </header>
            <div className="modal__body" style={{ maxHeight: "60vh", overflow: "auto" }}>
              {logModalLoading ? <p>読み込み中…</p> : null}
              {logModalError ? <p className="error">{logModalError}</p> : null}
              {logModalContent ? <pre className="code-block">{logModalContent}</pre> : null}
            </div>
          </div>
        </div>
      ) : null}
      {aTextModalOpen ? (
        <div className="modal-backdrop" onClick={() => setATextModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal__header">
              <h3>{aTextModalTitle || "Aテキスト"}</h3>
              <button
                className="workspace-button workspace-button--ghost"
                style={{ background: "#0f172a", color: "#fff", borderColor: "#0f172a" }}
                onClick={() => setATextModalOpen(false)}
              >
                閉じる
              </button>
            </header>
            <div className="modal__body" style={{ maxHeight: "60vh", overflow: "auto" }}>
              {aTextModalLoading ? <p>読み込み中…</p> : null}
              {aTextModalError ? <p className="error">{aTextModalError}</p> : null}
              {aTextModalContent ? <pre className="code-block" style={{ whiteSpace: "pre-wrap" }}>{aTextModalContent}</pre> : null}
            </div>
          </div>
        </div>
      ) : null}

      <section className="audio-review__toolbar" aria-label="音声フィルタ">
        <label className="audio-review__field">
          <span>チャンネル</span>
          <select
            value={channelFilter}
            onChange={(event) => setChannelFilter(event.target.value)}
            disabled={channelLoading}
          >
            <option value="all">すべて</option>
            {channels.map((channel) => (
              <option key={channel.code} value={channel.code}>
                {channel.code} / {channel.name ?? channel.branding?.title ?? "名称未設定"}
              </option>
            ))}
          </select>
        </label>

        <label className="audio-review__field">
          <span>案件ステータス</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">すべて</option>
            <option value="completed">完了</option>
            <option value="in_progress">進行中</option>
            <option value="review">レビュー待ち</option>
            <option value="blocked">要対応</option>
            <option value="pending">未着手</option>
          </select>
        </label>

        <label className="audio-review__field">
          <span>並び替え</span>
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
            <option value="recent">最新更新順</option>
            <option value="duration_desc">長さ（長い順）</option>
            <option value="duration_asc">長さ（短い順）</option>
          </select>
        </label>

        <label className="audio-review__field audio-review__field--grow">
          <span>キーワード</span>
          <input
            type="search"
            placeholder="企画番号・タイトル・品質など"
            value={searchKeyword}
            onChange={(event) => setSearchKeyword(event.target.value)}
          />
        </label>

        <label className="audio-review__field audio-review__field--checkbox">
          <input
            type="checkbox"
            checked={showManualOnly}
            onChange={(event) => setShowManualOnly(event.target.checked)}
          />
          <span>手動ポーズありのみ</span>
        </label>

        <label className="audio-review__field audio-review__field--checkbox">
          <input
            type="checkbox"
            checked={showQualityAttentionOnly}
            onChange={(event) => setShowQualityAttentionOnly(event.target.checked)}
          />
          <span>品質確認要のみ</span>
        </label>

        <label className="audio-review__field audio-review__field--checkbox">
          <input
            type="checkbox"
            checked={showSubtitlePendingOnly}
            onChange={(event) => setShowSubtitlePendingOnly(event.target.checked)}
          />
            <span>字幕未完のみ</span>
          </label>

          <label className="audio-review__field audio-review__field--checkbox">
            <input
              type="checkbox"
              checked={showRedoOnly}
              onChange={(event) => setShowRedoOnly(event.target.checked)}
            />
            <span>リテイクのみ</span>
          </label>

        {itemsLoading ? <span className="status-chip">読み込み中…</span> : null}
        {itemsError ? <span className="status-chip status-chip--danger">{itemsError}</span> : null}
      </section>

      {reviewStats.length > 0 ? (
        <section className="audio-review__stats" aria-label="音声レビューサマリ">
          {reviewStats.map((stat) => (
            <button
              key={stat.label}
              type="button"
              className={`audio-review__stat-card${stat.active ? " audio-review__stat-card--active" : ""}`}
              onClick={stat.onClick}
            >
              <span className="audio-review__stat-value">{stat.value}</span>
              <span className="audio-review__stat-label">{stat.label}</span>
              <span className="audio-review__stat-meta">{stat.description}</span>
            </button>
          ))}
        </section>
      ) : null}

      {processedItems.length > 0 ? (
        <section className="audio-review__playback" aria-label="再生コントロール">
          <div className="audio-review__playback-left">
            <label className="audio-review__playback-option">
              <input
                type="checkbox"
                checked={autoplayNext}
                onChange={(event) => setAutoplayNext(event.target.checked)}
              />
              <span>連続再生</span>
            </label>
            {currentIndices.index >= 0 ? (
              <span className="audio-review__playback-progress">
                {currentIndices.index + 1} / {processedItems.length}
              </span>
            ) : null}
          </div>
          <div className="audio-review__playback-buttons">
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => playAdjacent(-1)}
              disabled={!currentIndices.canPrev}
            >
              前へ
            </button>
            <button
              type="button"
              className="workspace-button workspace-button--ghost"
              onClick={() => playAdjacent(1)}
              disabled={!currentIndices.canNext}
            >
              次へ
            </button>
          </div>
        </section>
      ) : null}

      <section className="audio-review__list" aria-label="完成済み音声一覧">
        {processedItems.map((item, positionIndex) => {
          const key = `${item.channel}-${item.video}`;
          const audioSrc = resolveAudioSrc(item.audio_url);
          const srtSrc = resolveAudioSrc(item.srt_url ?? undefined);
          const logSrc = resolveAudioSrc(item.audio_log_url ?? undefined);
          const finalWav = resolveAudioSrc(`/api/channels/${item.channel}/videos/${item.video}/audio`);
          const finalSrt = resolveAudioSrc(`/api/channels/${item.channel}/videos/${item.video}/srt`);
          const redoAudio = toBool((item as any)["redo_audio"], true);
          const redoScript = toBool((item as any)["redo_script"], true);
          const redoNote = (item as any)["redo_note"] || "";
          const metaLines: string[] = [];
          if (item.audio_engine) metaLines.push(`engine: ${item.audio_engine}`);
          if (item.audio_duration_seconds != null) metaLines.push(formatDuration(item.audio_duration_seconds));
          if (item.audio_quality_status) metaLines.push(item.audio_quality_status);
          const logSummary = item.audio_log_summary;
          if (!audioSrc) {
            audioRefs.current.delete(key);
          }
          const audioStageLabel = AUDIO_STAGE_LABEL[item.audio_stage] ?? `音声: ${item.audio_stage}`;
          const subtitleStageLabel = SUBTITLE_STAGE_LABEL[item.subtitle_stage] ?? `字幕: ${item.subtitle_stage}`;
          const qualityVariant: "success" | "warning" | undefined = item.audio_quality_status
            ? /ok|良好|完了|問題なし/i.test(item.audio_quality_status)
              ? "success"
              : "warning"
            : undefined;
          const isActive = currentItemKey === key;
          const isRunning = runBusyKey === key;
          const qualityBadgeClass = `audio-card__badge audio-card__badge--quality${qualityVariant ? ` audio-card__badge--quality-${qualityVariant}` : ""
            }`;
          const channelMeta = channelMetaMap.get(item.channel);
          const avatarUrl = channelMeta?.branding?.avatar_url ?? null;
          const avatarLabelSource =
            channelMeta?.branding?.title ?? channelMeta?.youtube_title ?? channelMeta?.name ?? channelMeta?.code ?? item.channel;
          const avatarInitial = avatarLabelSource?.trim().charAt(0).toUpperCase() ?? "?";

          const avatarAlt = channelMeta?.branding?.title ?? channelMeta?.name ?? item.channel;

          const handleNavigateToDetail = () => {
            const targetPath = normalizeWorkspacePath(item.workspace_path);
            navigate(targetPath);
          };

          return (
            <article
              key={key}
              className={`audio-card${isActive ? " audio-card--active" : ""}`}
              data-redo={redoAudio || redoScript ? "1" : "0"}
              style={redoAudio || redoScript ? { borderColor: "#f97316", boxShadow: "0 0 0 2px rgba(249,115,22,0.15)" } : undefined}
              role="button"
              tabIndex={0}
              onClick={handleNavigateToDetail}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  handleNavigateToDetail();
                }
              }}
            >
              <div className="audio-card__marker">
                <span className="audio-card__index">#{positionIndex + 1}</span>
                <span className="audio-card__avatar" title={avatarAlt}>
                  {avatarUrl ? <img src={avatarUrl} alt={`${avatarAlt}のアイコン`} /> : avatarInitial}
                </span>
              </div>
              <header className="audio-card__header">
               <div className="audio-card__identity">
                 <span className="audio-card__chip audio-card__chip--code">{item.channel}-{item.video}</span>
                {item.channel_title ? (
                  <span className="audio-card__chip audio-card__chip--channel">{item.channel_title}</span>
                ) : null}
                {redoAudio || redoScript ? (
                  <RedoBadge
                    label={`リテイク${redoScript ? " 台本" : ""}${redoScript && redoAudio ? " /" : ""}${redoAudio ? " 音声" : ""}`}
                    note={redoNote || "リテイク対象"}
                  />
                ) : null}
                </div>
                <div className="audio-card__status-group">
                  <span className="audio-card__badge">{audioStageLabel}</span>
                  <span className="audio-card__badge">{subtitleStageLabel}</span>
                  <span className="audio-card__badge">案件: {translateStatus(item.status)}</span>
                  {isRunning ? <span className="audio-card__badge audio-card__badge--info">再生成中…</span> : null}
                </div>
              </header>
              <div
                className="audio-card__title"
                title={
                  redoAudio || redoScript
                    ? (redoNote || item.title || "タイトル未設定")
                    : (item.title || "タイトル未設定")
                }
              >
                {item.title || "タイトル未設定"}
              </div>
              <div className="audio-card__stats">
                <span className={qualityBadgeClass}>{item.audio_quality_status ?? "品質未評価"}</span>
                {metaLines.map((line, idx) => (
                  <span key={`meta-${key}-${idx}`} className="audio-card__chip">
                    {line}
                  </span>
                ))}
                {redoAudio || redoScript ? <RedoBadge note={redoNote || "リテイク対象"} /> : null}
                {logSummary ? (
                  <span className="audio-card__chip">
                    log: {logSummary.engine ?? "-"} / {logSummary.duration_sec ? `${logSummary.duration_sec.toFixed(1)}s` : "?"}
                    {logSummary.chunk_count != null ? ` / chunks ${logSummary.chunk_count}` : ""}
                  </span>
                ) : null}
                <span>{formatDateTime(item.audio_updated_at)}</span>
              </div>
              <div className="audio-card__player">
                {isRunning ? (
                  <div
                    style={{
                      position: "absolute",
                      top: 8,
                      right: 8,
                      background: "rgba(0,0,0,0.6)",
                      color: "#fff",
                      padding: "4px 8px",
                      borderRadius: 4,
                      fontSize: 12,
                      zIndex: 2,
                    }}
                  >
                    再生成中…
                  </div>
                ) : null}
                {audioSrc ? (
                  <audio
                    className="audio-card__audio"
                    controls
                    preload="metadata"
                    src={audioSrc}
                    aria-label={`${item.channel}-${item.video} の音声を再生`}
                    ref={(element) => {
                      if (element) {
                        audioRefs.current.set(key, element);
                      } else {
                        audioRefs.current.delete(key);
                      }
                    }}
                    onPlay={() => setCurrentItemKey(key)}
                    onEnded={() => handleAutoplay(key)}
                    onClick={stopCardPropagation}
                    onPointerDown={stopCardPropagation}
                    onPointerUp={stopCardPropagation}
                    onDoubleClick={stopCardPropagation}
                  />
                ) : (
                  <div className="audio-card__unavailable">音声ファイルが見つかりません</div>
                )}
                <div className="audio-card__actions">
                  {srtSrc ? (
                    <a
                      className="workspace-button workspace-button--ghost workspace-button--compact"
                      href={srtSrc}
                      target="_blank"
                      rel="noreferrer"
                      onClick={stopCardPropagation}
                    >
                      字幕SRT
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className="workspace-button workspace-button--primary workspace-button--compact"
                    disabled={runBusyKey === key}
                    onClick={async (event) => {
                      event.stopPropagation();
                      setRunBusyKey(key);
                      setRunMessage(null);
                      setRunError(null);
                      setToast(`再生成中: ${item.channel}-${item.video}`);
                      try {
                        const res = await runAudioTtsV2FromScript({
                          channel: item.channel,
                          video: item.video,
                        });
                        const logInfo = res.log ? ` / log: ${res.log}` : "";
                        const finalInfo = res.final_wav ? ` / final_wav: ${res.final_wav}` : "";
                        setRunMessage(`再生成完了: ${item.channel}-${item.video} (${res.engine ?? "engine?"})${logInfo}${finalInfo}`);
                        setToast(null);
                        // 成功時にリテイク（音声）を自動解除
                        try {
                          await updateVideoRedo(item.channel, item.video, { redo_audio: false });
                        } catch {
                          /* best effort */
                        }
                      } catch (runErr) {
                        const msg = runErr instanceof Error ? runErr.message : String(runErr ?? "再生成に失敗しました");
                        setRunError(`${item.channel}-${item.video}: ${msg}`);
                        setToast(null);
                      } finally {
                        setRunBusyKey(null);
                        // 対象案件のみ再取得して最新音声/SRTを反映
                        refreshItems({
                          channel: channelFilter && channelFilter !== "all" ? channelFilter : undefined,
                          status: statusFilter && statusFilter !== "all" ? statusFilter : undefined,
                          video: item.video,
                        });
                      }
                    }}
                  >
                    {runBusyKey === key ? "再生成中…" : "再生成 (TTS v2)"}
                  </button>
                  <button
                    type="button"
                    className="workspace-button workspace-button--ghost workspace-button--compact"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleNavigateToDetail();
                    }}
                  >
                    音声詳細設定
                  </button>
                  {finalWav ? (
                    <a
                      className="workspace-button workspace-button--ghost workspace-button--compact"
                      href={finalWav}
                      target="_blank"
                      rel="noreferrer"
                      onClick={stopCardPropagation}
                    >
                      最終WAV
                    </a>
                  ) : null}
                  {finalSrt ? (
                    <a
                      className="workspace-button workspace-button--ghost workspace-button--compact"
                      href={finalSrt}
                      target="_blank"
                      rel="noreferrer"
                      onClick={stopCardPropagation}
                    >
                      最終SRT
                    </a>
                  ) : null}
                  {logSrc ? (
                    <a
                      className="workspace-button workspace-button--ghost workspace-button--compact"
                      href={logSrc}
                      target="_blank"
                      rel="noreferrer"
                      onClick={stopCardPropagation}
                    >
                      ログ
                    </a>
                  ) : null}
                  {logSrc ? (
                    <button
                      type="button"
                      className="workspace-button workspace-button--ghost workspace-button--compact"
                      onClick={(event) => {
                        event.stopPropagation();
                        void openLogModal(logSrc, `${item.channel}-${item.video} log.json`);
                      }}
                    >
                      ログ全文
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="workspace-button workspace-button--ghost workspace-button--compact"
                    onClick={(event) => {
                      event.stopPropagation();
                      void openATextModal(item.channel, item.video);
                    }}
                  >
                    Aテキストを見る
                  </button>
                </div>
                <div className="audio-card__artifacts">
                  <div className="audio-card__meta-line">
                    <span className="audio-card__meta-label">更新:</span>
                    <span className="audio-card__meta-value">{formatDateTime(item.audio_updated_at)}</span>
                    <span className="audio-card__meta-sep">/</span>
                    <span className="audio-card__meta-label">長さ:</span>
                    <span className="audio-card__meta-value">{formatDuration(item.audio_duration_seconds)}</span>
                  </div>
                  <div className="audio-card__meta-line">
                    <span className="audio-card__meta-label">成果物:</span>
                    <span className="audio-card__meta-value">
                      {finalWav ? (
                        <a className="link" href={finalWav} target="_blank" rel="noreferrer" onClick={stopCardPropagation}>
                          WAV
                        </a>
                      ) : (
                        "なし"
                      )}
                      {finalSrt ? (
                        <>
                          {" / "}
                          <a className="link" href={finalSrt} target="_blank" rel="noreferrer" onClick={stopCardPropagation}>
                            SRT
                          </a>
                        </>
                      ) : null}
                      {logSrc ? (
                        <>
                          {" / "}
                          <a className="link" href={logSrc} target="_blank" rel="noreferrer" onClick={stopCardPropagation}>
                            LOG
                          </a>
                        </>
                      ) : null}
                    </span>
                  </div>
                </div>
              </div>
            </article>
          );
        })}

        {!itemsLoading && processedItems.length === 0 ? (
          <div className="audio-review__empty">該当する音声は見つかりませんでした。</div>
        ) : null}
      </section>

      {channelError ? <p className="audio-review__error">チャンネルの取得に失敗しました: {channelError}</p> : null}
    </div>
  );
}
