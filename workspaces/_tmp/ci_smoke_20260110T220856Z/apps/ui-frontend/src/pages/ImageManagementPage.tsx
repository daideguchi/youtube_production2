import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import "./ImageTimelinePage.css";

import {
  DEFAULT_GENERATION_OPTIONS,
  createVideoJob,
  fetchVideoProductionChannels,
  fetchVideoProjectDetail,
  fetchVideoProjects,
  resolveApiUrl,
  uploadProjectImage,
} from "../api/client";
import type {
  VideoGenerationOptions,
  VideoJobCreatePayload,
  VideoProductionChannelPreset,
  VideoProjectDetail,
  VideoProjectSummary,
} from "../api/types";
import { VideoImageVariantsPanel } from "../components/VideoImageVariantsPanel";

function resolveChannelId(summary?: VideoProjectDetail["summary"]): string | null {
  if (!summary) return null;
  return (
    (summary as { channelId?: string }).channelId ??
    (summary as { channel_id?: string }).channel_id ??
    (summary.id?.includes("-") ? summary.id.split("-", 1)[0] : null)
  );
}

function resolveProjectChannelCode(project: VideoProjectSummary): string {
  const direct = (
    (project.channelId ?? (project as { channel_id?: string | null }).channel_id ?? project.channel_id ?? "").trim()
  ).toUpperCase();
  if (direct) return direct;
  const planning = (project.planning?.channel ?? "").trim().toUpperCase();
  if (planning) return planning;
  const match = project.id.match(/[A-Za-z]{2}\d{2}/);
  if (match) return match[0].toUpperCase();
  if (project.id.includes("-")) {
    const head = project.id.split("-", 1)[0]?.trim();
    if (head) return head.toUpperCase();
  }
  return "UNKNOWN";
}

function resolveEffectiveStyle(
  generationOptions: VideoGenerationOptions,
  channelPreset: VideoProductionChannelPreset | null
): string {
  const fromOptions = (generationOptions.style ?? "").trim();
  if (fromOptions) return fromOptions;
  return (channelPreset?.style ?? "").trim();
}

type BannerState = { kind: "info" | "error" | "success"; message: string } | null;

function formatTime(seconds: number): string {
  const total = Number.isFinite(seconds) ? seconds : 0;
  const minutes = Math.floor(total / 60);
  const sec = Math.floor(total % 60).toString().padStart(2, "0");
  return `${minutes}:${sec}`;
}

function normalizeAssetPath(path: string): string {
  return String(path ?? "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

function encodeAssetPathForUrl(path: string): string {
  const normalized = normalizeAssetPath(path);
  if (!normalized) return "";
  return normalized
    .split("/")
    .filter((seg) => seg.length > 0)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
}

function assetBasename(path: string): string {
  const normalized = normalizeAssetPath(path);
  return normalized.split("/").pop() ?? normalized;
}

function buildImageAssetUrl(asset: { path: string; modified_at?: string }, fallbackUrl?: string): string {
  const encodedPath = encodeAssetPathForUrl(asset.path);
  const base = fallbackUrl ? fallbackUrl : `/api/video-production/assets/${encodedPath}`;
  const suffix = asset.modified_at ? `?t=${encodeURIComponent(asset.modified_at)}` : "";
  return resolveApiUrl(`${base}${suffix}`);
}

function TimelineImg({ src, alt, fit }: { src: string; alt: string; fit: "cover" | "contain" }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#64748b",
          fontSize: 12,
          padding: 12,
          textAlign: "center",
        }}
      >
        画像を表示できません
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ width: "100%", height: "100%", objectFit: fit }}
    />
  );
}

const IMAGE_TIMELINE_LAST_PROJECT_ID_KEY = "imageTimeline:lastProjectId";

export function ImageManagementPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const timelineOnly = location.pathname === "/image-timeline";
  const selectedProjectId = (searchParams.get("project") ?? "").trim();

  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  const [channels, setChannels] = useState<VideoProductionChannelPreset[]>([]);
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [projectDetail, setProjectDetail] = useState<VideoProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [banner, setBanner] = useState<BannerState>(null);
  const [projectSearch, setProjectSearch] = useState("");
  const [promptFilter, setPromptFilter] = useState("");
  const [promptLimit, setPromptLimit] = useState<number>(50);
  const [timelineFilter, setTimelineFilter] = useState<"all" | "needs" | "missing" | "placeholder" | "ready">("needs");
  const [timelineViewMode, setTimelineViewMode] = useState<"review" | "grid" | "cards">(() => (timelineOnly ? "review" : "grid"));
  const [activeCueIndex, setActiveCueIndex] = useState<number | null>(null);
  const [previewCueIndex, setPreviewCueIndex] = useState<number | null>(null);
  const [timelineStage, setTimelineStage] = useState<"pick" | "work">(() => {
    if (!timelineOnly) return "work";
    return selectedProjectId ? "work" : "pick";
  });

  const handleSelectProject = useCallback(
    (projectId: string, opts?: { replace?: boolean }) => {
      const params = new URLSearchParams(searchParams);
      if (projectId) {
        params.set("project", projectId);
        if (timelineOnly) {
          try {
            localStorage.setItem(IMAGE_TIMELINE_LAST_PROJECT_ID_KEY, projectId);
          } catch {
            // ignore
          }
        }
      } else {
        params.delete("project");
      }
      setSearchParams(params, { replace: opts?.replace ?? !timelineOnly });
    },
    [searchParams, setSearchParams, timelineOnly]
  );

  const refreshIndex = useCallback(async () => {
    setProjectsLoading(true);
    setProjectsError(null);
    setChannelsError(null);
    try {
      const [projectsData, channelsData] = await Promise.all([
        fetchVideoProjects(),
        fetchVideoProductionChannels(false),
      ]);
      setProjects(projectsData);
      setChannels(channelsData);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setProjectsError(msg);
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const refreshDetail = useCallback(async () => {
    if (!selectedProjectId) {
      setProjectDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    try {
      const detail = await fetchVideoProjectDetail(selectedProjectId);
      setProjectDetail(detail);
    } catch (error) {
      setProjectDetail(null);
      setDetailError(error instanceof Error ? error.message : String(error));
    } finally {
      setDetailLoading(false);
    }
  }, [selectedProjectId]);

  useEffect(() => {
    void refreshIndex();
  }, [refreshIndex]);

  useEffect(() => {
    void refreshDetail();
    setBanner(null);
    setPromptFilter("");
  }, [refreshDetail]);

  const channelId = useMemo(() => resolveChannelId(projectDetail?.summary ?? undefined) ?? null, [projectDetail?.summary]);

  const channelPreset = useMemo(() => {
    if (!channelId) return null;
    return channels.find((c) => c.channelId === channelId) ?? null;
  }, [channels, channelId]);

  const generationOptions: VideoGenerationOptions = useMemo(() => {
    return projectDetail?.generationOptions ?? DEFAULT_GENERATION_OPTIONS;
  }, [projectDetail?.generationOptions]);

  const effectiveStyle = useMemo(() => resolveEffectiveStyle(generationOptions, channelPreset), [generationOptions, channelPreset]);

  const projectPickerItems = useMemo(() => {
    const needle = projectSearch.trim().toLowerCase();
    const items = projects
      .map((p) => {
        const progress = p.imageProgress;
        const required = progress?.requiredTotal ?? 0;
        const ready = progress?.generatedReady ?? 0;
        const placeholders = progress?.placeholders ?? 0;
        const needsTotal = required > 0 ? Math.max(0, required - ready) : Math.max(0, progress?.missing ?? 0);
        const missingFiles = Math.max(0, needsTotal - placeholders);
        const title = (p.planning?.title ?? p.title ?? p.id ?? "").trim() || p.id;
        const updatedMs = Date.parse(p.last_updated ?? p.created_at ?? "") || 0;
        return { project: p, title, required, ready, placeholders, missing: missingFiles, needs: needsTotal, updatedMs };
      })
      .filter((item) => {
        if (!needle) return true;
        const hay = `${item.project.id} ${item.title}`.toLowerCase();
        return hay.includes(needle);
      })
      .sort((a, b) => b.needs - a.needs || b.updatedMs - a.updatedMs || a.project.id.localeCompare(b.project.id));
    return items;
  }, [projectSearch, projects]);

  const timelineAggregates = useMemo(() => {
    const channelNameById = new Map<string, string>();
    for (const c of channels) {
      channelNameById.set(String(c.channelId ?? "").trim().toUpperCase(), String(c.name ?? "").trim());
    }

    let totalRequired = 0;
    let totalReady = 0;
    let totalPlaceholders = 0;
    let totalMissing = 0;
    let totalNeeds = 0;

    const byChannel = new Map<
      string,
      {
        channelId: string;
        channelName: string;
        projects: number;
        total: number;
        ready: number;
        placeholders: number;
        missing: number;
        needs: number;
      }
    >();

    for (const p of projects) {
      const progress = p.imageProgress;
      const requiredRaw = progress?.requiredTotal ?? 0;
      const ready = progress?.generatedReady ?? 0;
      const placeholders = progress?.placeholders ?? 0;
      const needsTotal = requiredRaw > 0 ? Math.max(0, requiredRaw - ready) : Math.max(0, progress?.missing ?? 0);
      const missingFiles = Math.max(0, needsTotal - placeholders);
      const total = Math.max(0, requiredRaw > 0 ? requiredRaw : ready + needsTotal);

      totalRequired += total;
      totalReady += ready;
      totalPlaceholders += placeholders;
      totalMissing += missingFiles;
      totalNeeds += needsTotal;

      const channelId = resolveProjectChannelCode(p);
      const channelName = channelNameById.get(channelId) ?? "";
      const existing = byChannel.get(channelId);
      if (existing) {
        existing.projects += 1;
        existing.total += total;
        existing.ready += ready;
        existing.placeholders += placeholders;
        existing.missing += missingFiles;
        existing.needs += needsTotal;
      } else {
        byChannel.set(channelId, {
          channelId,
          channelName,
          projects: 1,
          total,
          ready,
          placeholders,
          missing: missingFiles,
          needs: needsTotal,
        });
      }
    }

    const channelsSorted = Array.from(byChannel.values())
      .filter((row) => row.total > 0 || row.needs > 0 || row.ready > 0)
      .sort((a, b) => b.needs - a.needs || b.total - a.total || a.channelId.localeCompare(b.channelId));
    return {
      totals: {
        projects: projects.length,
        total: totalRequired,
        ready: totalReady,
        placeholders: totalPlaceholders,
        missing: totalMissing,
        needs: totalNeeds,
      },
      channels: channelsSorted,
    };
  }, [channels, projects]);

  const selectedProjectPicker = useMemo(() => {
    if (!selectedProjectId) return null;
    const project = projects.find((p) => p.id === selectedProjectId) ?? null;
    if (!project) return null;
    const progress = project.imageProgress;
    const required = progress?.requiredTotal ?? 0;
    const ready = progress?.generatedReady ?? 0;
    const placeholders = progress?.placeholders ?? 0;
    const needsTotal = required > 0 ? Math.max(0, required - ready) : Math.max(0, progress?.missing ?? 0);
    const missingFiles = Math.max(0, needsTotal - placeholders);
    const title = (project.planning?.title ?? project.title ?? project.id ?? "").trim() || project.id;
    const updated = (project.last_updated ?? project.created_at ?? "").slice(0, 19);
    const okPct = required > 0 ? Math.max(0, Math.min(100, Math.round((ready / required) * 100))) : 0;
    return { project, title, required, ready, placeholders, missing: missingFiles, needs: needsTotal, updated, okPct };
  }, [projects, selectedProjectId]);

  const scrollToTop = useCallback(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const openProjectPicker = useCallback(() => {
    setTimelineStage("pick");
    scrollToTop();
  }, [scrollToTop]);

  const enterTimelineWork = useCallback(
    (projectId: string) => {
      handleSelectProject(projectId);
      setTimelineStage("work");
      scrollToTop();
    },
    [handleSelectProject, scrollToTop]
  );

  useEffect(() => {
    if (!timelineOnly) return;
    if (!selectedProjectId) {
      setTimelineStage("pick");
    }
  }, [selectedProjectId, timelineOnly]);

  const cuePrompts = useMemo(() => {
    const cues = projectDetail?.cues ?? [];
    const needle = promptFilter.trim().toLowerCase();
    const rows = cues
      .map((cue, idx) => ({
        index: idx + 1,
        startSec: cue.start_sec,
        endSec: cue.end_sec,
        prompt: (cue.prompt ?? "").trim(),
      }))
      .filter((row) => Boolean(row.prompt));
    if (!needle) return rows;
    return rows.filter((row) => row.prompt.toLowerCase().includes(needle));
  }, [projectDetail?.cues, promptFilter]);

  const cueTimeline = useMemo(() => {
    if (!projectDetail) return [];
    const minBytes = channelPreset?.imageMinBytes ?? null;
    const images = projectDetail.images ?? [];
    const imagesByIndex = new Map<number, (typeof images)[number]>();
    for (const asset of images) {
      const name = assetBasename(asset.path);
      const match = name.match(/^0*(\d+)\./);
      if (!match) continue;
      const parsed = Number.parseInt(match[1], 10);
      if (Number.isFinite(parsed) && parsed > 0) {
        imagesByIndex.set(parsed, asset);
      }
    }
    return (projectDetail.cues ?? []).map((cue) => {
      const idx = cue.index;
      const asset =
        (Number.isFinite(idx) ? imagesByIndex.get(idx) : undefined) ??
        (idx > 0 && idx <= images.length ? images[idx - 1] : undefined);
      const isMissing = !asset;
      const isPlaceholder =
        !isMissing &&
        typeof minBytes === "number" &&
        minBytes > 0 &&
        typeof asset.size_bytes === "number" &&
        asset.size_bytes < minBytes;
      const status = isMissing ? "missing" : isPlaceholder ? "placeholder" : "ready";
      return {
        cue,
        asset: asset ?? null,
        status,
      };
    });
  }, [projectDetail, channelPreset?.imageMinBytes]);

  const timelineStats = useMemo(() => {
    let ok = 0;
    let placeholder = 0;
    let missing = 0;
    for (const row of cueTimeline) {
      if (row.status === "ready") ok += 1;
      else if (row.status === "placeholder") placeholder += 1;
      else missing += 1;
    }
    return {
      total: cueTimeline.length,
      ok,
      placeholder,
      missing,
      needs: placeholder + missing,
    };
  }, [cueTimeline]);

  const timelineOkPct = useMemo(() => {
    if (timelineStats.total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((timelineStats.ok / timelineStats.total) * 100)));
  }, [timelineStats.ok, timelineStats.total]);

  const timelineStatsDenom = useMemo(() => Math.max(1, timelineStats.total), [timelineStats.total]);

  const cueTimelineView = useMemo(() => {
    if (timelineFilter === "all") return cueTimeline;
    if (timelineFilter === "needs") return cueTimeline.filter((row) => row.status !== "ready");
    return cueTimeline.filter((row) => row.status === timelineFilter);
  }, [cueTimeline, timelineFilter]);

  const cueCount = cueTimeline.length;

  useEffect(() => {
    if (timelineViewMode !== "review") {
      return;
    }
    if (!projectDetail) {
      setActiveCueIndex(null);
      return;
    }
    if (cueTimelineView.length === 0) {
      setActiveCueIndex(null);
      return;
    }
    const current =
      typeof activeCueIndex === "number" ? cueTimelineView.find((row) => row.cue.index === activeCueIndex) : null;
    if (current) {
      return;
    }
    setActiveCueIndex(cueTimelineView[0].cue.index);
  }, [activeCueIndex, cueTimelineView, projectDetail, timelineViewMode]);

  useEffect(() => {
    if (timelineViewMode !== "review") return;
    if (typeof activeCueIndex !== "number") return;
    const el = document.getElementById(`cue-list-${activeCueIndex}`);
    if (!el) return;
    el.scrollIntoView({ block: "nearest" });
  }, [activeCueIndex, timelineViewMode]);

  const activeCueViewIndex = useMemo(() => {
    if (timelineViewMode !== "review") return -1;
    if (typeof activeCueIndex !== "number") return -1;
    return cueTimelineView.findIndex((row) => row.cue.index === activeCueIndex);
  }, [activeCueIndex, cueTimelineView, timelineViewMode]);

  const activeCueRow = useMemo(() => {
    if (timelineViewMode !== "review") return null;
    if (activeCueViewIndex < 0) return null;
    return cueTimelineView[activeCueViewIndex] ?? null;
  }, [activeCueViewIndex, cueTimelineView, timelineViewMode]);

  const previewRow = useMemo(() => {
    if (previewCueIndex === null) return null;
    return cueTimeline.find((row) => row.cue.index === previewCueIndex) ?? null;
  }, [cueTimeline, previewCueIndex]);

  const closePreview = () => setPreviewCueIndex(null);
  const openPreview = (index: number) => setPreviewCueIndex(index);
  const gotoPreviewPrev = () =>
    setPreviewCueIndex((current) => (typeof current === "number" && current > 1 ? current - 1 : current));
  const gotoPreviewNext = () =>
    setPreviewCueIndex((current) =>
      typeof current === "number" && cueCount > 0 && current < cueCount ? current + 1 : current
    );

  const gotoActivePrev = () => {
    if (timelineViewMode !== "review") return;
    if (activeCueViewIndex <= 0) return;
    const prev = cueTimelineView[activeCueViewIndex - 1];
    if (prev) setActiveCueIndex(prev.cue.index);
  };

  const gotoActiveNext = () => {
    if (timelineViewMode !== "review") return;
    if (activeCueViewIndex < 0) return;
    const next = cueTimelineView[activeCueViewIndex + 1];
    if (next) setActiveCueIndex(next.cue.index);
  };

  useEffect(() => {
    if (previewCueIndex === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreviewCueIndex(null);
        return;
      }
      if (event.key === "ArrowLeft") {
        setPreviewCueIndex((current) => (typeof current === "number" && current > 1 ? current - 1 : current));
        return;
      }
      if (event.key === "ArrowRight") {
        setPreviewCueIndex((current) =>
          typeof current === "number" && cueCount > 0 && current < cueCount ? current + 1 : current
        );
        return;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [previewCueIndex, cueCount]);

  useEffect(() => {
    if (!timelineOnly) return;
    if (timelineStage !== "pick") return;
    setPreviewCueIndex(null);
  }, [timelineOnly, timelineStage]);

  const totalDurationSec = useMemo(() => {
    if (!projectDetail?.cues?.length) return 0;
    const last = projectDetail.cues[projectDetail.cues.length - 1];
    const end = Number.isFinite(last?.end_sec) ? Number(last.end_sec) : 0;
    return Math.max(0, end);
  }, [projectDetail?.cues]);

  const scrollToCue = (index: number) => {
    if (timelineViewMode === "review") {
      setActiveCueIndex(index);
      return;
    }
    const el = document.getElementById(`cue-${index}`);
    if (!el) {
      openPreview(index);
      return;
    }
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handleCopyCuePrompt = async (index: number, prompt: string) => {
    const text = (prompt ?? "").trim();
    if (!text) {
      setBanner({ kind: "error", message: `#${index}: prompt が空です。` });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setBanner({ kind: "success", message: `#${index}: prompt をコピーしました。` });
    } catch {
      setBanner({ kind: "error", message: "コピーに失敗しました（ブラウザ権限の可能性）。" });
    }
  };

  const handleUploadCueImage = async (index: number, file: File) => {
    if (!selectedProjectId) {
      setBanner({ kind: "error", message: "project が未選択です。" });
      return;
    }
    setBanner({ kind: "info", message: `#${index}: 画像をアップロードしています…` });
    try {
      await uploadProjectImage(selectedProjectId, index, file);
      setBanner({ kind: "success", message: `#${index}: 画像を差し替えました。` });
      await refreshDetail();
    } catch (error) {
      setBanner({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  };

  const jumpToFirstNeedsWork = () => {
    const target = cueTimeline.find((row) => row.status !== "ready");
    if (!target) {
      setBanner({ kind: "success", message: "要対応の cue はありません（全部OK）。" });
      return;
    }
    if (timelineViewMode === "review") {
      setActiveCueIndex(target.cue.index);
      return;
    }
    scrollToCue(target.cue.index);
  };

  const handleQuickJob = useCallback(
    async (action: VideoJobCreatePayload["action"], options?: VideoJobCreatePayload["options"]) => {
      if (!selectedProjectId) {
        setBanner({ kind: "error", message: "project が未選択です。" });
        return;
      }
      setBanner({ kind: "info", message: "ジョブを作成しています…" });
      try {
        const job = await createVideoJob(selectedProjectId, { action, options: options ?? undefined });
        setBanner({ kind: "success", message: `job queued: ${job.id} (${job.action})` });
      } catch (error) {
        setBanner({ kind: "error", message: error instanceof Error ? error.message : String(error) });
      }
    },
    [selectedProjectId]
  );

  const handleCopyAllPrompts = async () => {
    if (!projectDetail) return;
    const lines = (projectDetail.cues ?? [])
      .map((cue, idx) => {
        const prompt = (cue.prompt ?? "").trim();
        if (!prompt) return null;
        return `#${idx + 1}\n${prompt}`;
      })
      .filter((line): line is string => Boolean(line));
    const text = lines.join("\n\n---\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setBanner({ kind: "success", message: "プロンプトをクリップボードにコピーしました。" });
    } catch {
      setBanner({ kind: "error", message: "コピーに失敗しました（ブラウザ権限の可能性）。" });
    }
  };

  useEffect(() => {
    if (!banner) return;
    if (banner.kind !== "success") return;
    const timeout = window.setTimeout(() => setBanner(null), 2500);
    return () => window.clearTimeout(timeout);
  }, [banner]);

  const bannerClass =
    banner?.kind === "error"
      ? "main-alert main-alert--error"
      : banner?.kind === "success"
        ? "main-alert main-alert--success"
        : banner?.kind === "info"
          ? "main-alert main-alert--info"
          : "main-alert";

  const showProjectPicker = !timelineOnly || timelineStage === "pick";
  const showTimelineWork = !timelineOnly || timelineStage === "work";

  return (
    <div className={`page image-management-page${timelineOnly ? " image-timeline-page" : ""}`}>
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">{timelineOnly ? "画像タイムライン" : "画像管理"}</p>
          <h1>
            {timelineOnly
              ? timelineStage === "pick"
                ? "プロジェクトを選択"
                : "画像差し替え（手動生成→アップロード）"
              : "モデル / 画風 / プロンプト"}
          </h1>
          <p className="page-lead">
            {timelineOnly
              ? timelineStage === "pick"
                ? "まず企画（run_dir）を選びます。選択後は「要対応（未作成/仮画像）」を順番に処理します。"
                : "要対応（未作成/仮画像）を選ぶ → prompt をコピー → 画像をアップロードして差し替え。"
              : "run_dir（Video Project）単位で、設定とプロンプトを確認しながら複数画風のバリアントを生成します。"}
          </p>
        </div>
        <div className="capcut-edit-page__actions">
          {timelineOnly ? (
            <>
              {timelineStage === "work" ? (
                <button type="button" className="workspace-button workspace-button--ghost" onClick={openProjectPicker}>
                  ← プロジェクト一覧
                </button>
              ) : null}
              <button type="button" className="workspace-button workspace-button--ghost" onClick={() => void refreshIndex()}>
                一覧更新
              </button>
              {timelineStage === "work" ? (
                <button type="button" className="workspace-button workspace-button--primary" onClick={() => void refreshDetail()} disabled={!selectedProjectId || detailLoading}>
                  {detailLoading ? "更新中…" : "同期"}
                </button>
              ) : null}
            </>
          ) : (
            <>
              <button type="button" className="button button--ghost" onClick={() => void refreshIndex()} disabled={projectsLoading}>
                {projectsLoading ? "読込中…" : "一覧更新"}
              </button>
              <button type="button" className="button" onClick={() => void refreshDetail()} disabled={!selectedProjectId || detailLoading}>
                {detailLoading ? "更新中…" : "詳細更新"}
              </button>
            </>
          )}
        </div>
      </header>

      {showProjectPicker ? (
        <section className="capcut-edit-page__section" id={timelineOnly ? "image-timeline-section-projects" : undefined}>
          <div className="shell-panel shell-panel--placeholder">
            <h2>{timelineOnly ? "プロジェクト一覧" : "1) 対象プロジェクトを選ぶ"}</h2>
            <p className="shell-panel__subtitle">
              {timelineOnly
                ? "クリックで開く（残り=未作成+仮画像 が多い順）。"
                : "プロジェクト（run_dir）を選ぶと、モデル/画風/プロンプトと既存バリアントが表示されます。"}
            </p>

            {timelineOnly ? (
              <div className="timeline-pick" aria-busy={projectsLoading}>
                <div className="timeline-summary" aria-label="全体集計">
                  <div className="timeline-summary__head">
                    <div>
                      <div className="timeline-summary__title">全体集計</div>
                      <div className="timeline-summary__subtitle">残り（未作成 + 仮画像）と OK 数</div>
                    </div>
                    <div className="timeline-summary__metrics">
                      <div className="timeline-metric timeline-metric--danger">
                        <div className="timeline-metric__label">残り</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.needs.toLocaleString()}</div>
                      </div>
                      <div className="timeline-metric timeline-metric--danger">
                        <div className="timeline-metric__label">未作成</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.missing.toLocaleString()}</div>
                      </div>
                      <div className="timeline-metric timeline-metric--warning">
                        <div className="timeline-metric__label">仮画像</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.placeholders.toLocaleString()}</div>
                      </div>
                      <div className="timeline-metric timeline-metric--ok">
                        <div className="timeline-metric__label">OK</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.ready.toLocaleString()}</div>
                      </div>
                      <div className="timeline-metric">
                        <div className="timeline-metric__label">合計</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.total.toLocaleString()}</div>
                      </div>
                      <div className="timeline-metric">
                        <div className="timeline-metric__label">projects</div>
                        <div className="timeline-metric__value">{timelineAggregates.totals.projects.toLocaleString()}</div>
                      </div>
                    </div>
                  </div>

                  <div className="timeline-summary__channels" aria-label="チャンネル別集計">
                    {timelineAggregates.channels.map((row) => {
                      const denom = row.total > 0 ? row.total : 1;
                      const label = row.channelName ? `${row.channelId}（${row.channelName}）` : row.channelId;
                      return (
                        <button
                          key={row.channelId}
                          type="button"
                          className="timeline-channel-row"
                          title="クリックでこのチャンネルに絞り込み"
                          onClick={() => setProjectSearch(row.channelId)}
                        >
                          <div className="timeline-channel-row__main">
                            <div className="timeline-channel-row__title">
                              <span className="timeline-channel-row__id">{row.channelId}</span>
                              {row.channelName ? <span className="timeline-channel-row__name">{row.channelName}</span> : null}
                            </div>
                            <div className="timeline-channel-row__meta">
                              {label} ・ 残り <strong>{row.needs.toLocaleString()}</strong>（未 {row.missing.toLocaleString()} / 仮{" "}
                              {row.placeholders.toLocaleString()}） ・ OK {row.ready.toLocaleString()}/{row.total.toLocaleString()} ・
                              projects {row.projects.toLocaleString()}
                            </div>
                            {row.total > 0 ? (
                              <div className="timeline-channel-row__bar" aria-label="画像進捗">
                                <div className="timeline-channel-row__bar-ok" style={{ width: `${(row.ready / denom) * 100}%` }} />
                                <div
                                  className="timeline-channel-row__bar-placeholder"
                                  style={{ width: `${(row.placeholders / denom) * 100}%` }}
                                />
                                <div className="timeline-channel-row__bar-missing" style={{ width: `${(row.missing / denom) * 100}%` }} />
                              </div>
                            ) : null}
                          </div>
                          <div className="timeline-channel-row__cta">絞り込み →</div>
                        </button>
                      );
                    })}
                    {timelineAggregates.channels.length === 0 ? (
                      <div className="main-alert" style={{ marginTop: 0 }}>
                        集計できるデータがありません。
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="timeline-pick__toolbar">
                  <label className="timeline-pick__search">
                    <span className="timeline-pick__label">検索（id / title）</span>
                    <input
                      className="timeline-pick__input"
                      value={projectSearch}
                      onChange={(e) => setProjectSearch(e.target.value)}
                      placeholder="例: CH22 / 004 / タイトルの一部"
                    />
                  </label>
                  <div className="timeline-pick__meta">
                    <span className="status-chip">{projectsLoading ? "読込中…" : `${projectPickerItems.length}件`}</span>
                    {projectSearch.trim() ? (
                      <button type="button" className="workspace-button workspace-button--ghost" onClick={() => setProjectSearch("")}>
                        クリア
                      </button>
                    ) : null}
                    {selectedProjectPicker ? (
                      <button
                        type="button"
                        className="workspace-button workspace-button--primary"
                        onClick={() => {
                          setTimelineStage("work");
                          scrollToTop();
                        }}
                      >
                        選択中を開く
                      </button>
                    ) : null}
                  </div>
                </div>

                <div className="timeline-pick__list">
                  {projectPickerItems.map((item) => {
                    const p = item.project;
                    const isSelected = p.id === selectedProjectId;
                    const required = item.required;
                    const total = required > 0 ? required : Math.max(0, item.ready + item.placeholders + item.missing);
                    const denom = total > 0 ? total : 1;
                    const needsTone = item.needs <= 0 ? "ok" : item.missing > 0 ? "danger" : "warning";
                    const updatedLabel = (p.last_updated ?? p.created_at ?? "").slice(0, 19);
                    return (
                      <button
                        key={p.id}
                        type="button"
                        className={`timeline-project-row timeline-project-row--${needsTone}${isSelected ? " is-selected" : ""}`}
                        aria-pressed={isSelected}
                        onClick={() => enterTimelineWork(p.id)}
                      >
                        <div className="timeline-project-row__needs">
                          <div className="timeline-project-row__needs-num">{item.needs}</div>
                          <div className="timeline-project-row__needs-label">残り</div>
                        </div>
                        <div className="timeline-project-row__main">
                          <div className="timeline-project-row__title">{item.title}</div>
                          <div className="timeline-project-row__id">
                            <code>{p.id}</code>
                          </div>
                          <div className="timeline-project-row__meta">
                            OK {item.ready}/{item.required || "—"} ・ 未 {item.missing} ・ 仮 {item.placeholders}
                            {p.imageProgress?.mode === "none" ? " ・ 自動生成停止" : ""}
                            {updatedLabel ? ` ・ updated ${updatedLabel}` : ""}
                          </div>
                          {total > 0 ? (
                            <div className="timeline-project-row__bar" aria-label="画像進捗">
                              <div className="timeline-project-row__bar-ok" style={{ width: `${(item.ready / denom) * 100}%` }} />
                              <div
                                className="timeline-project-row__bar-placeholder"
                                style={{ width: `${(item.placeholders / denom) * 100}%` }}
                              />
                              <div className="timeline-project-row__bar-missing" style={{ width: `${(item.missing / denom) * 100}%` }} />
                            </div>
                          ) : null}
                        </div>
                        <div className="timeline-project-row__cta">開く →</div>
                      </button>
                    );
                  })}
                  {!projectsLoading && projectPickerItems.length === 0 ? (
                    <div className="main-alert" style={{ marginTop: 0 }}>
                      条件に一致するプロジェクトがありません。
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
                <label>
                  project
                  <select
                    value={selectedProjectId}
                    onChange={(e) => handleSelectProject(e.target.value)}
                    disabled={projectsLoading}
                    style={{ width: "100%" }}
                  >
                    <option value="">(未選択)</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.id}
                        {p.imageProgress && p.imageProgress.requiredTotal > 0
                          ? ` (img ${p.imageProgress.generatedReady}/${p.imageProgress.requiredTotal})`
                          : ""}
                        {p.planning?.title ? ` - ${p.planning.title}` : p.title ? ` - ${p.title}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  prompt contains（filter）
                  <input
                    value={promptFilter}
                    onChange={(e) => setPromptFilter(e.target.value)}
                    placeholder="例: living room / stained glass"
                    style={{ width: "100%" }}
                    disabled={!projectDetail}
                  />
                </label>
                <label>
                  prompt rows（max）
                  <input
                    type="number"
                    min={1}
                    value={Number.isFinite(promptLimit) && promptLimit > 0 ? promptLimit : 50}
                    onChange={(e) => setPromptLimit(Number(e.target.value))}
                    style={{ width: "100%" }}
                    disabled={!projectDetail}
                  />
                </label>
              </div>
            )}

            {projectsError ? <div className="main-alert main-alert--error">{projectsError}</div> : null}
            {channelsError ? <div className="main-alert main-alert--error">{channelsError}</div> : null}
            {detailError ? <div className="main-alert main-alert--error">{detailError}</div> : null}
            {banner ? <div className={bannerClass}>{banner.message}</div> : null}
            {!timelineOnly && !projectsLoading && !selectedProjectId ? <div className="main-alert">まず project を選択してください。</div> : null}
            {timelineOnly && projectsLoading ? <div className="main-alert">プロジェクトを読み込み中…</div> : null}
            {timelineOnly && !projectsLoading && projects.length === 0 ? (
              <div className="main-alert main-alert--warning">プロジェクトがありません。</div>
            ) : null}

            {projectDetail && !timelineOnly ? (
              <>
                <div className="main-status" style={{ marginTop: 10 }}>
                  <span className="status-chip">
                    project: <code>{projectDetail.summary.id}</code>
                  </span>
                  <span className="status-chip">
                    企画: <code>{projectDetail.summary.planning?.title ?? "—"}</code>
                  </span>
                  <span className="status-chip">
                    channel: <code>{channelId ?? "—"}</code>
                  </span>
                  <span className="status-chip">
                    model: <code>{channelPreset?.imageGeneration?.modelKey ?? "(unset)"}</code>
                  </span>
                  <span className="status-chip">
                    style: <code>{effectiveStyle || "(none)"}</code>
                  </span>
                  <span className="status-chip">
                    prompt_template: <code>{channelPreset?.promptTemplate ?? "(unset)"}</code>
                  </span>
                  <span className="status-chip">cues: {projectDetail.cues.length}</span>
                  <span
                    className={`status-chip${
                      (projectDetail.summary.imageProgress?.missing ?? 0) > 0 ? " status-chip--warning" : ""
                    }`}
                  >
                    images: {projectDetail.summary.imageProgress?.generatedReady ?? 0}/
                    {projectDetail.summary.imageProgress?.requiredTotal ?? projectDetail.cues.length}（残り{" "}
                    {projectDetail.summary.imageProgress?.missing ??
                      Math.max(
                        0,
                        (projectDetail.summary.imageProgress?.requiredTotal ?? projectDetail.cues.length) -
                          (projectDetail.summary.imageProgress?.generatedReady ?? 0)
                      )}
                    ）
                    {(projectDetail.summary.imageProgress?.placeholders ?? 0) > 0
                      ? ` / placeholder ${projectDetail.summary.imageProgress?.placeholders}`
                      : ""}
                    {projectDetail.summary.imageProgress?.mode === "none" ? " / 生成停止中" : ""}
                  </span>
                </div>
                {(() => {
                  const progress = projectDetail.summary.imageProgress;
                  const required = progress?.requiredTotal ?? projectDetail.cues.length;
                  const generated = progress?.generatedReady ?? 0;
                  const pct = required > 0 ? Math.max(0, Math.min(100, Math.round((generated / required) * 100))) : 0;
                  if (required <= 0) return null;
                  return (
                    <div
                      style={{
                        marginTop: 10,
                        background: "#fff",
                        border: "1px solid #e2e8f0",
                        borderRadius: 12,
                        padding: "10px 12px",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                        <strong>画像進捗</strong>
                        <span style={{ fontSize: 12, color: "#64748b" }}>{pct}%</span>
                      </div>
                      <div
                        style={{
                          marginTop: 8,
                          height: 8,
                          borderRadius: 999,
                          background: "#e2e8f0",
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            width: `${pct}%`,
                            height: "100%",
                            background: pct >= 100 ? "#10b981" : "#0ea5e9",
                          }}
                        />
                      </div>
                    </div>
                  );
                })()}
              </>
            ) : null}
          </div>
        </section>
      ) : null}

      {(timelineOnly ? showTimelineWork : Boolean(projectDetail)) ? (
        <section className="capcut-edit-page__section" id={timelineOnly ? "image-timeline-section-timeline" : undefined}>
          <div className="shell-panel shell-panel--placeholder">
            {!projectDetail ? (
              <div className={`main-alert${detailError ? " main-alert--error" : ""}`}>
                {detailLoading ? "プロジェクト詳細を読み込み中…" : detailError ? detailError : "プロジェクト詳細がありません。"}
              </div>
            ) : (
              <>
                {!timelineOnly ? (
                  <>
                    <h2>2) タイムライン（cues × images）</h2>
                    <p className="shell-panel__subtitle">
                      未作成/仮画像/OK を時系列で把握し、prompt をコピーして手動生成→アップロード差し替えできます。
                    </p>
                  </>
                ) : null}
                <div className={timelineOnly ? "image-timeline" : undefined}>
                  {timelineOnly ? (
                    <div className="timeline-work-header">
                      <div className="timeline-work-header__top">
                        <div>
                          <div className="timeline-work-header__title">
                            {projectDetail.summary.planning?.title ? projectDetail.summary.planning.title : projectDetail.summary.id}
                          </div>
                          <div className="timeline-work-header__subtitle">
                            <code>{projectDetail.summary.id}</code> ・ 残り <strong>{timelineStats.needs}</strong> / {timelineStats.total}
                            （未 {timelineStats.missing} / 仮 {timelineStats.placeholder} / OK {timelineStats.ok}）
                          </div>
                        </div>
                        <div className="timeline-work-header__actions">
                          <button
                            type="button"
                            className="workspace-button workspace-button--ghost"
                            onClick={openProjectPicker}
                            title="プロジェクト一覧へ戻る"
                          >
                            ← 一覧
                          </button>
                          <button
                            type="button"
                            className="workspace-button workspace-button--primary"
                            onClick={() => {
                              setTimelineFilter("needs");
                              jumpToFirstNeedsWork();
                            }}
                          >
                            次の要対応へ
                          </button>
                        </div>
                      </div>

                      {projectDetail.summary.imageProgress?.mode === "none" ? (
                        <div className="timeline-work-header__note">自動生成は停止中です（手動差し替えで進めてください）。</div>
                      ) : null}

                      {banner ? (
                        <div className={`${bannerClass} timeline-work-header__banner`}>
                          <span>{banner.message}</span>
                          <button
                            type="button"
                            className="timeline-work-header__banner-close"
                            onClick={() => setBanner(null)}
                            aria-label="閉じる"
                          >
                            ×
                          </button>
                        </div>
                      ) : null}

                      <div
                        className="timeline-work-header__bar"
                        aria-label="画像進捗"
                        title={`OK ${timelineStats.ok}/${timelineStats.total} (${timelineOkPct}%)`}
                      >
                        <div
                          className="timeline-work-header__bar-seg timeline-work-header__bar-seg--ok"
                          style={{ width: `${(timelineStats.ok / timelineStatsDenom) * 100}%` }}
                          title={`OK ${timelineStats.ok}`}
                        />
                        <div
                          className="timeline-work-header__bar-seg timeline-work-header__bar-seg--placeholder"
                          style={{ width: `${(timelineStats.placeholder / timelineStatsDenom) * 100}%` }}
                          title={`仮画像 ${timelineStats.placeholder}`}
                        />
                        <div
                          className="timeline-work-header__bar-seg timeline-work-header__bar-seg--missing"
                          style={{ width: `${(timelineStats.missing / timelineStatsDenom) * 100}%` }}
                          title={`未作成 ${timelineStats.missing}`}
                        />
                      </div>

                      {totalDurationSec > 0 ? (
                        <div className="timeline-scrub" aria-label="映像タイムライン">
                          <div className="timeline-scrub__bar">
                            {cueTimeline.map(({ cue, status }) => {
                              const start = Number.isFinite(cue.start_sec) ? cue.start_sec : 0;
                              const end = Number.isFinite(cue.end_sec) ? cue.end_sec : start;
                              const leftPct = Math.max(0, Math.min(100, (start / totalDurationSec) * 100));
                              const widthPct = Math.max(0.5, Math.min(100, ((end - start) / totalDurationSec) * 100));
                              const matchesFilter =
                                timelineFilter === "all"
                                  ? true
                                  : timelineFilter === "needs"
                                    ? status !== "ready"
                                    : status === timelineFilter;
                              const isDim = timelineFilter !== "all" && !matchesFilter;
                              return (
                                <button
                                  key={cue.index}
                                  type="button"
                                  className={`timeline-scrub__seg timeline-scrub__seg--${status}${
                                    cue.index === activeCueIndex ? " is-active" : ""
                                  }${isDim ? " is-dim" : ""}`}
                                  onClick={() => scrollToCue(cue.index)}
                                  title={`#${cue.index} ${formatTime(start)}-${formatTime(end)}（${
                                    status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成"
                                  }）`}
                                  aria-label={`Cue ${cue.index} ${formatTime(start)}-${formatTime(end)}`}
                                  style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                                />
                              );
                            })}
                          </div>
                          <div className="timeline-scrub__meta">
                            <span>0:00</span>
                            <span className="timeline-scrub__hint">クリックで該当cueへ</span>
                            <span className="timeline-scrub__end">{formatTime(totalDurationSec)}</span>
                          </div>
                        </div>
                      ) : null}

                      <div className="timeline-filter" role="tablist" aria-label="フィルタ">
                        {([
                          { key: "needs", label: "要対応", count: timelineStats.needs },
                          { key: "missing", label: "未作成", count: timelineStats.missing },
                          { key: "placeholder", label: "仮画像", count: timelineStats.placeholder },
                          { key: "ready", label: "OK", count: timelineStats.ok },
                          { key: "all", label: "全部", count: timelineStats.total },
                        ] as const).map((item) => (
                          <button
                            key={item.key}
                            type="button"
                            role="tab"
                            aria-selected={timelineFilter === item.key}
                            className={`timeline-filter__button${timelineFilter === item.key ? " is-active" : ""}`}
                            onClick={() => setTimelineFilter(item.key)}
                          >
                            <span className="timeline-filter__label">{item.label}</span>
                            <span className="timeline-filter__count">{item.count}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : null}

            {!timelineOnly ? (
              <div
                style={{
                  marginTop: 10,
                  border: "1px solid #e2e8f0",
                  borderRadius: 14,
                  background: "#f8fafc",
                  padding: 12,
                  display: "grid",
                  gap: 10,
                  position: "sticky",
                  top: 10,
                  zIndex: 20,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
                  <strong>見方（誰でもここだけ見ればOK）</strong>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button type="button" className="button button--ghost" onClick={() => jumpToFirstNeedsWork()}>
                      要対応へジャンプ
                    </button>
                    {projectDetail.summary.imageProgress?.mode === "none" ? (
                      <span className="status-chip status-chip--warning">生成停止中（手動差し替え運用）</span>
                    ) : null}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <span
                    className="status-chip"
                    style={{ borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" }}
                  >
                    OK: {timelineStats.ok}
                  </span>
                  <span className="status-chip status-chip--warning">仮画像: {timelineStats.placeholder}</span>
                  <span className="status-chip status-chip--danger">未作成: {timelineStats.missing}</span>
                  <span className="status-chip">合計: {timelineStats.total}</span>
                  <span className="status-chip status-chip--warning">要対応: {timelineStats.needs}</span>
                  <span style={{ fontSize: 12, color: "#64748b" }}>
                    仮画像判定: size{" "}
                    {typeof channelPreset?.imageMinBytes === "number" ? `< ${channelPreset.imageMinBytes} bytes` : "(minBytes unset)"}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <span style={{ fontSize: 12, color: "#64748b" }}>フィルタ:</span>
                    {([
                      { key: "needs", label: `要対応 (${timelineStats.needs})` },
                      { key: "missing", label: `未作成 (${timelineStats.missing})` },
                      { key: "placeholder", label: `仮画像 (${timelineStats.placeholder})` },
                      { key: "ready", label: `OK (${timelineStats.ok})` },
                      { key: "all", label: "すべて" },
                    ] as const).map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className="status-chip"
                        aria-pressed={timelineFilter === item.key}
                        onClick={() => setTimelineFilter(item.key)}
                        style={{
                          cursor: "pointer",
                          opacity: timelineFilter === item.key ? 1 : 0.6,
                          borderColor: timelineFilter === item.key ? "#0ea5e9" : undefined,
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <span style={{ fontSize: 12, color: "#64748b" }}>表示:</span>
                    {([
                      { key: "grid", label: "サムネ" },
                      { key: "cards", label: "詳細一覧" },
                    ] as const).map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className="status-chip"
                        aria-pressed={timelineViewMode === item.key}
                        onClick={() => setTimelineViewMode(item.key)}
                        style={{
                          cursor: "pointer",
                          opacity: timelineViewMode === item.key ? 1 : 0.6,
                          borderColor: timelineViewMode === item.key ? "#0ea5e9" : undefined,
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                    <span style={{ fontSize: 12, color: "#64748b" }}>（サムネをクリックで拡大）</span>
                  </div>
                  <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.5 }}>
                    手順: ① <strong>仮画像/未作成</strong> の行で <strong>prompt をコピー</strong> → ② 画像を手動生成 → ③{" "}
                    <strong>アップロードして差し替え</strong>
                  </div>
                </div>
              </div>
            ) : null}

            {!timelineOnly && totalDurationSec > 0 ? (
              <div style={{ marginTop: 12 }}>
                <div
                  style={{
                    position: "relative",
                    height: 18,
                    borderRadius: 999,
                    background: "#e2e8f0",
                    overflow: "hidden",
                    border: "1px solid #e2e8f0",
                  }}
                >
                  {cueTimeline.map(({ cue, status }) => {
                    const start = Number.isFinite(cue.start_sec) ? cue.start_sec : 0;
                    const end = Number.isFinite(cue.end_sec) ? cue.end_sec : start;
                    const leftPct = Math.max(0, Math.min(100, (start / totalDurationSec) * 100));
                    const widthPct = Math.max(0.5, Math.min(100, ((end - start) / totalDurationSec) * 100));
                    const color = status === "ready" ? "#10b981" : status === "placeholder" ? "#f59e0b" : "#ef4444";
                    return (
                      <button
                        key={cue.index}
                        type="button"
                        onClick={() => scrollToCue(cue.index)}
                        title={`#${cue.index} ${formatTime(start)}-${formatTime(end)} (${status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成"})`}
                        style={{
                          position: "absolute",
                          left: `${leftPct}%`,
                          width: `${widthPct}%`,
                          height: "100%",
                          background: color,
                          border: "none",
                          padding: 0,
                          cursor: "pointer",
                        }}
                      />
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8, fontSize: 12, color: "#64748b" }}>
                  <span>0:00</span>
                  <span style={{ color: "#475569" }}>（クリックで該当cueへジャンプ）</span>
                  <span style={{ marginLeft: "auto" }}>{formatTime(totalDurationSec)}</span>
                </div>
              </div>
            ) : null}

            {timelineViewMode === "review" ? (
              <div className="image-timeline__workspace" style={{ marginTop: 12 }}>
                <div className="image-timeline__list">
                  <div className="image-timeline__list-header">
                    <strong>
                      {timelineFilter === "needs"
                        ? "要対応リスト（未作成 + 仮画像）"
                        : timelineFilter === "missing"
                          ? "未作成リスト"
                          : timelineFilter === "placeholder"
                            ? "仮画像リスト"
                            : timelineFilter === "ready"
                              ? "OKリスト"
                              : "全cue"}
                    </strong>
                    <span className="image-timeline__small">{cueTimelineView.length} 件（クリックで選択）</span>
                  </div>
                  <div className="image-timeline__list-items">
                    {cueTimelineView.map(({ cue, asset, status }) => {
                      const isActive = cue.index === activeCueIndex;
                      const chipClassBase =
                        status === "ready"
                          ? "status-chip"
                          : status === "placeholder"
                            ? "status-chip status-chip--warning"
                            : "status-chip status-chip--danger";
                      const chipStyle =
                        status === "ready"
                          ? { borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" }
                          : undefined;
                      const chipLabel = status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成";
                      const assetUrl = asset ? buildImageAssetUrl({ path: asset.path, modified_at: asset.modified_at }) : null;
                      const promptHeadline = (cue.prompt ?? "").trim().split("\n", 1)[0].slice(0, 80);
                      return (
                        <button
                          key={cue.index}
                          id={`cue-list-${cue.index}`}
                          type="button"
                          onClick={() => setActiveCueIndex(cue.index)}
                          aria-pressed={isActive}
                          className={`image-timeline__item image-timeline__item--${status}`}
                        >
                          <div className="image-timeline__thumb">
                            {assetUrl ? (
                              <TimelineImg src={assetUrl} alt={asset?.path ?? `cue-${cue.index}`} fit="cover" />
                            ) : (
                              "未作成"
                            )}
                          </div>
                          <div style={{ display: "grid", gap: 4 }}>
                            <div className="image-timeline__item-title-row">
                              <strong>#{cue.index}</strong>
                              <span className={chipClassBase} style={chipStyle}>
                                {chipLabel}
                              </span>
                            </div>
                            <div className="image-timeline__item-meta">
                              {formatTime(cue.start_sec)}–{formatTime(cue.end_sec)}
                            </div>
                            {promptHeadline ? (
                              <div className="image-timeline__item-prompt">
                                <span style={{ color: "#475569", fontWeight: 700 }}>prompt:</span> {promptHeadline}
                              </div>
                            ) : (
                              <div className="image-timeline__item-prompt" style={{ color: "#64748b" }}>
                                prompt: （空）
                              </div>
                            )}
                          </div>
                        </button>
                      );
                    })}
                    {cueTimelineView.length === 0 ? (
                      <div className="main-alert" style={{ marginTop: 0 }}>
                        フィルタ条件に一致する cue がありません。
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="image-timeline__detail">
                  {activeCueRow ? (() => {
                    const { cue, asset, status } = activeCueRow;
                    const chipClassBase =
                      status === "ready"
                        ? "status-chip"
                        : status === "placeholder"
                          ? "status-chip status-chip--warning"
                          : "status-chip status-chip--danger";
                    const chipStyle =
                      status === "ready"
                        ? { borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" }
                        : undefined;
                    const chipLabel = status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成";
                    const borderColor = status === "ready" ? "#10b981" : status === "placeholder" ? "#f59e0b" : "#ef4444";
                    const promptText = (cue.prompt ?? "").trim();
                    const assetUrl = asset ? buildImageAssetUrl({ path: asset.path, modified_at: asset.modified_at }) : null;
                    const uploadButtonClass = status === "ready" ? "button button--ghost" : "button";
                    const viewCount = cueTimelineView.length;
                    const canPrev = activeCueViewIndex > 0;
                    const canNext = activeCueViewIndex >= 0 && activeCueViewIndex < viewCount - 1;
                    const activePosition = activeCueViewIndex >= 0 ? activeCueViewIndex + 1 : 0;
                    const promptHeadline = promptText.split("\n", 1)[0].slice(0, 160);
                    return (
                      <>
                        <div className="image-timeline__detail-header">
                          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                            <strong style={{ fontSize: 16 }}>Cue #{cue.index}</strong>
                            <span className={chipClassBase} style={chipStyle}>
                              {chipLabel}
                            </span>
                            <span className="image-timeline__small">
                              {formatTime(cue.start_sec)}–{formatTime(cue.end_sec)}（{Math.max(0, cue.end_sec - cue.start_sec).toFixed(1)}s）
                            </span>
                            <span className="image-timeline__small">
                              {activePosition}/{viewCount}
                            </span>
                          </div>
                          <div className="image-timeline__detail-actions">
                            <button type="button" className="button button--ghost" onClick={() => gotoActivePrev()} disabled={!canPrev}>
                              ← 前へ
                            </button>
                            <button type="button" className="button button--ghost" onClick={() => gotoActiveNext()} disabled={!canNext}>
                              次へ →
                            </button>
                            <button type="button" className="button button--ghost" onClick={() => openPreview(cue.index)}>
                              拡大
                            </button>
                            {assetUrl ? (
                              <a className="button button--ghost" href={assetUrl} target="_blank" rel="noreferrer">
                                新しいタブで開く
                              </a>
                            ) : null}
                          </div>
                        </div>

                        <div className="image-timeline__preview" style={{ borderColor }}>
                          <button
                            type="button"
                            onClick={() => openPreview(cue.index)}
                            title="クリックで拡大表示"
                            style={{
                              width: "100%",
                              border: "none",
                              background: "transparent",
                              padding: 0,
                              cursor: "zoom-in",
                            }}
                          >
                            <div className="image-timeline__preview-inner">
                              {assetUrl ? (
                                <TimelineImg src={assetUrl} alt={asset?.path ?? `cue-${cue.index}`} fit="contain" />
                              ) : (
                                "画像がありません（未作成）"
                              )}
                            </div>
                          </button>
                        </div>

                        <div
                          className={`image-timeline__callout image-timeline__callout--${status}`}
                          style={{ borderColor }}
                        >
                          <strong>次にやること:</strong>{" "}
                          {status === "ready"
                            ? "この cue は OK です（必要なら手動差し替えも可能）"
                            : status === "placeholder"
                              ? "仮画像なので、prompt を使って作り直してアップロードしてください"
                              : "未作成なので、prompt を使って画像を作ってアップロードしてください"}
                        </div>

                        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                          <button type="button" className="button" onClick={() => void handleCopyCuePrompt(cue.index, promptText)} disabled={!promptText}>
                            ① prompt をコピー
                          </button>
                          <label
                            className={uploadButtonClass}
                            style={{ position: "relative", overflow: "hidden", display: "inline-flex", justifyContent: "center" }}
                          >
                            ② 画像アップロード（差し替え）
                            <input
                              type="file"
                              accept="image/png,image/jpeg"
                              style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) {
                                  void handleUploadCueImage(cue.index, file);
                                  event.target.value = "";
                                }
                              }}
                            />
                          </label>
                          <span style={{ fontSize: 12, color: "#64748b" }}>アップロード後、一覧が自動更新されます</span>
                        </div>

                        <div style={{ display: "grid", gap: 6 }}>
                          <div style={{ fontSize: 12, color: "#475569" }}>
                            {asset ? (
                              <>
                                file: <code>{assetBasename(asset.path)}</code> / {asset.size_bytes ?? "?"} bytes
                              </>
                            ) : (
                              "file: —"
                            )}
                          </div>
                          {promptHeadline ? (
                            <div style={{ fontSize: 12, color: "#475569" }}>
                              <strong>prompt:</strong> {promptHeadline}
                            </div>
                          ) : null}
                        </div>

                        <details open={false}>
                          <summary style={{ cursor: "pointer" }}>prompt / context（詳細）</summary>
                          <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                            {cue.summary ? (
                              <div style={{ fontSize: 13, color: "#0f172a" }}>
                                <strong>summary:</strong> {cue.summary}
                              </div>
                            ) : null}
                            {cue.text ? (
                              <div style={{ fontSize: 12, color: "#475569", whiteSpace: "pre-wrap" }}>{cue.text}</div>
                            ) : null}
                            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontSize: 12 }}>
                              {promptText || "(prompt empty)"}
                            </pre>
                          </div>
                        </details>
                      </>
                    );
                  })() : (
                    <div className="main-alert">左のリストから cue を選択してください。</div>
                  )}
                </div>
              </div>
            ) : timelineViewMode === "grid" ? (
              <div
                style={{
                  display: "grid",
                  gap: 10,
                  marginTop: 12,
                  gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                }}
              >
                {cueTimelineView.map(({ cue, asset, status }) => {
                  const chipClassBase =
                    status === "ready"
                      ? "status-chip"
                      : status === "placeholder"
                        ? "status-chip status-chip--warning"
                        : "status-chip status-chip--danger";
                  const chipStyle =
                    status === "ready"
                      ? { borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" }
                      : undefined;
                  const chipLabel = status === "ready" ? "OK" : status === "placeholder" ? "仮" : "未";
                  const borderLeftColor =
                    status === "ready" ? "#10b981" : status === "placeholder" ? "#f59e0b" : "#ef4444";
                  const assetUrl = asset ? buildImageAssetUrl({ path: asset.path, modified_at: asset.modified_at }) : null;
                  return (
                    <button
                      key={cue.index}
                      type="button"
                      onClick={() => openPreview(cue.index)}
                      title={`#${cue.index} ${formatTime(cue.start_sec)}-${formatTime(cue.end_sec)}（クリックで拡大）`}
                      style={{
                        textAlign: "left",
                        border: "1px solid #e2e8f0",
                        borderLeft: `6px solid ${borderLeftColor}`,
                        borderRadius: 14,
                        background: "#fff",
                        padding: 10,
                        cursor: "pointer",
                        display: "grid",
                        gap: 8,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                        <strong>#{cue.index}</strong>
                        <span className={chipClassBase} style={chipStyle}>
                          {chipLabel}
                        </span>
                      </div>
                      <div
                        style={{
                          width: "100%",
                          aspectRatio: "16/9",
                          borderRadius: 10,
                          overflow: "hidden",
                          border: "1px solid #e2e8f0",
                          background: "#f8fafc",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "#64748b",
                          fontSize: 12,
                        }}
                      >
                        {assetUrl ? (
                          <TimelineImg src={assetUrl} alt={asset?.path ?? `cue-${cue.index}`} fit="cover" />
                        ) : (
                          "画像なし"
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: "#64748b" }}>
                        {formatTime(cue.start_sec)}–{formatTime(cue.end_sec)}
                      </div>
                    </button>
                  );
                })}
                {cueTimelineView.length === 0 ? (
                  <div className="main-alert" style={{ marginTop: 0 }}>
                    フィルタ条件に一致する cue がありません。
                  </div>
                ) : null}
              </div>
            ) : (
              <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
                {cueTimelineView.map(({ cue, asset, status }) => {
                  const chipClassBase =
                    status === "ready"
                      ? "status-chip"
                      : status === "placeholder"
                        ? "status-chip status-chip--warning"
                        : "status-chip status-chip--danger";
                  const chipStyle =
                    status === "ready"
                      ? { borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" }
                      : undefined;
                  const chipLabel = status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成";
                  const borderLeftColor =
                    status === "ready" ? "#10b981" : status === "placeholder" ? "#f59e0b" : "#ef4444";
                  const promptHeadline = (cue.prompt ?? "").trim().split("\n", 1)[0].slice(0, 120);
                  const uploadButtonClass = status === "ready" ? "button button--ghost" : "button";
                  const statusHelp =
                    status === "ready"
                      ? "この画像はOK（差し替え不要）"
                      : status === "placeholder"
                        ? "これは仮画像（要差し替え）"
                        : "画像が未作成（要作成）";
                  const statusHelpColor =
                    status === "ready" ? "#047857" : status === "placeholder" ? "#b45309" : "#b91c1c";
                  const assetUrl = asset ? buildImageAssetUrl({ path: asset.path, modified_at: asset.modified_at }) : null;
                  return (
                    <div
                      key={cue.index}
                      id={`cue-${cue.index}`}
                      style={{
                        border: "1px solid #e2e8f0",
                        borderLeft: `6px solid ${borderLeftColor}`,
                        borderRadius: 14,
                        background: "#fff",
                        padding: 12,
                        display: "grid",
                        gridTemplateColumns: "minmax(220px, 280px) 1fr",
                        gap: 12,
                      }}
                    >
                      <div style={{ display: "grid", gap: 8 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                          <strong>#{cue.index}</strong>
                          <span className={chipClassBase} style={chipStyle}>
                            {chipLabel}
                          </span>
                        </div>
                        <div style={{ fontSize: 12, color: "#64748b" }}>
                          {formatTime(cue.start_sec)} – {formatTime(cue.end_sec)}（{Math.max(0, cue.end_sec - cue.start_sec).toFixed(1)}s）
                        </div>
                        <div style={{ fontSize: 12, color: statusHelpColor, fontWeight: 700 }}>{statusHelp}</div>
                        <button
                          type="button"
                          onClick={() => openPreview(cue.index)}
                          title="クリックで拡大表示"
                          style={{ border: "none", background: "transparent", padding: 0, cursor: "zoom-in" }}
                        >
                          <div
                            style={{
                              width: "100%",
                              aspectRatio: "16/9",
                              borderRadius: 10,
                              overflow: "hidden",
                              border: "1px solid #e2e8f0",
                              background: "#f8fafc",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              color: "#64748b",
                              fontSize: 12,
                            }}
                          >
                            {assetUrl ? (
                              <TimelineImg src={assetUrl} alt={asset?.path ?? `cue-${cue.index}`} fit="cover" />
                            ) : (
                              "画像なし（クリックで詳細）"
                            )}
                          </div>
                        </button>
                      </div>
                      <div style={{ display: "grid", gap: 10 }}>
                        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
                          <div style={{ fontSize: 12, color: "#64748b" }}>
                            {asset ? (
                              <>
                                file: <code>{assetBasename(asset.path)}</code> / {asset.size_bytes ?? "?"} bytes
                              </>
                            ) : (
                              "file: —"
                            )}
                          </div>
                          <button
                            type="button"
                            className="button"
                            onClick={() => void handleCopyCuePrompt(cue.index, cue.prompt ?? "")}
                            style={{ marginLeft: "auto" }}
                          >
                            ① prompt をコピー
                          </button>
                        </div>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                          <label
                            className={uploadButtonClass}
                            style={{ position: "relative", overflow: "hidden", display: "inline-flex", justifyContent: "center" }}
                          >
                            ② 画像アップロード（差し替え）
                            <input
                              type="file"
                              accept="image/png,image/jpeg"
                              style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) {
                                  void handleUploadCueImage(cue.index, file);
                                  event.target.value = "";
                                }
                              }}
                            />
                          </label>
                          <span style={{ fontSize: 12, color: "#64748b" }}>← ここで差し替え完了（すぐ一覧に反映）</span>
                        </div>
                        {promptHeadline ? (
                          <div style={{ fontSize: 12, color: "#475569" }}>
                            <strong>prompt:</strong> {promptHeadline}
                          </div>
                        ) : null}
                        <details>
                          <summary style={{ cursor: "pointer" }}>prompt / context（詳細）</summary>
                          <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                            {cue.summary ? (
                              <div style={{ fontSize: 13, color: "#0f172a" }}>
                                <strong>summary:</strong> {cue.summary}
                              </div>
                            ) : null}
                            {cue.text ? (
                              <div style={{ fontSize: 12, color: "#475569", whiteSpace: "pre-wrap" }}>{cue.text}</div>
                            ) : null}
                            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontSize: 12 }}>
                              {(cue.prompt ?? "").trim() || "(prompt empty)"}
                            </pre>
                          </div>
                        </details>
                      </div>
                    </div>
                  );
                })}
                {cueTimelineView.length === 0 ? (
                  <div className="main-alert" style={{ marginTop: 0 }}>
                    フィルタ条件に一致する cue がありません。
                  </div>
                ) : null}
              </div>
            )}

            </div>

            {previewRow ? (() => {
              const { cue, asset, status } = previewRow;
              const chipLabel = status === "ready" ? "OK" : status === "placeholder" ? "仮画像" : "未作成";
              const chipClass =
                status === "ready"
                  ? "status-chip"
                  : status === "placeholder"
                    ? "status-chip status-chip--warning"
                    : "status-chip status-chip--danger";
              const chipStyle =
                status === "ready" ? { borderColor: "#d1fae5", background: "#d1fae5", color: "#047857" } : undefined;
              const borderColor = status === "ready" ? "#10b981" : status === "placeholder" ? "#f59e0b" : "#ef4444";
              const assetUrl = asset ? buildImageAssetUrl({ path: asset.path, modified_at: asset.modified_at }) : null;
              const promptText = (cue.prompt ?? "").trim();
              return (
                <div
                  role="dialog"
                  aria-modal="true"
                  onClick={() => closePreview()}
                  style={{
                    position: "fixed",
                    inset: 0,
                    background: "rgba(15, 23, 42, 0.75)",
                    zIndex: 1000,
                    padding: 18,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <div
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      width: "min(1100px, 96vw)",
                      maxHeight: "92vh",
                      overflow: "auto",
                      background: "#fff",
                      borderRadius: 16,
                      border: "1px solid #e2e8f0",
                      boxShadow: "0 18px 60px rgba(0,0,0,0.35)",
                    }}
                  >
                    <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <strong style={{ fontSize: 16 }}>Cue #{cue.index}</strong>
                      <span className={chipClass} style={chipStyle}>
                        {chipLabel}
                      </span>
                      <span style={{ fontSize: 12, color: "#64748b" }}>
                        {formatTime(cue.start_sec)}–{formatTime(cue.end_sec)}（{Math.max(0, cue.end_sec - cue.start_sec).toFixed(1)}s）
                      </span>
                      <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button type="button" className="button button--ghost" onClick={() => gotoPreviewPrev()} disabled={cue.index <= 1}>
                          ← 前へ
                        </button>
                        <button type="button" className="button button--ghost" onClick={() => gotoPreviewNext()} disabled={cueCount > 0 ? cue.index >= cueCount : true}>
                          次へ →
                        </button>
                        {assetUrl ? (
                          <a className="button button--ghost" href={assetUrl} target="_blank" rel="noreferrer">
                            新しいタブで開く
                          </a>
                        ) : null}
                        <button type="button" className="button" onClick={() => closePreview()}>
                          閉じる（Esc）
                        </button>
                      </div>
                    </div>
                    <div style={{ padding: 14, display: "grid", gap: 12 }}>
                      <div
                        style={{
                          border: `1px solid ${borderColor}`,
                          borderRadius: 14,
                          overflow: "hidden",
                          background: "#0b1220",
                        }}
                      >
                        <div style={{ width: "100%", aspectRatio: "16/9", background: "#0b1220", display: "flex", alignItems: "center", justifyContent: "center" }}>
                          {assetUrl ? (
                            <TimelineImg src={assetUrl} alt={asset?.path ?? `cue-${cue.index}`} fit="contain" />
                          ) : (
                            <div style={{ color: "#e2e8f0", fontSize: 14, padding: 12, textAlign: "center" }}>
                              画像がありません（未作成）
                            </div>
                          )}
                        </div>
                      </div>

                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                        <button
                          type="button"
                          className="button button--ghost"
                          onClick={() => void handleCopyCuePrompt(cue.index, promptText)}
                          disabled={!promptText}
                        >
                          ① prompt をコピー
                        </button>
                        <label className="button" style={{ position: "relative", overflow: "hidden" }}>
                          ② 画像アップロード（差し替え）
                          <input
                            type="file"
                            accept="image/png,image/jpeg"
                            style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              if (file) {
                                void handleUploadCueImage(cue.index, file);
                                event.target.value = "";
                              }
                            }}
                          />
                        </label>
                        <span style={{ fontSize: 12, color: "#64748b" }}>← ここで差し替え完了（すぐ一覧に反映）</span>
                      </div>

                      <details>
                        <summary style={{ cursor: "pointer" }}>prompt / context（詳細）</summary>
                        <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                          {cue.summary ? (
                            <div style={{ fontSize: 13, color: "#0f172a" }}>
                              <strong>summary:</strong> {cue.summary}
                            </div>
                          ) : null}
                          {cue.text ? (
                            <div style={{ fontSize: 12, color: "#475569", whiteSpace: "pre-wrap" }}>{cue.text}</div>
                          ) : null}
                          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontSize: 12 }}>
                            {promptText || "(prompt empty)"}
                          </pre>
                        </div>
                      </details>
                    </div>
                  </div>
                </div>
              );
            })() : null}
              </>
            )}
          </div>
        </section>
      ) : null}

      {projectDetail && !timelineOnly ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
              <h2>3) プロンプト（cues）</h2>
              <button type="button" className="button button--ghost" onClick={() => void handleCopyAllPrompts()}>
                全プロンプトをコピー
              </button>
            </div>
            <p className="shell-panel__subtitle">画像生成に使われる prompt をキュー単位で確認できます（filter で絞り込み可）。</p>

            {cuePrompts.length === 0 ? (
              <div className="main-alert">該当する prompt がありません。</div>
            ) : (
              <div style={{ display: "grid", gap: 8 }}>
                {cuePrompts.slice(0, Number.isFinite(promptLimit) && promptLimit > 0 ? promptLimit : 50).map((row) => (
                  <details key={row.index} style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 10, background: "#fff" }}>
                    <summary style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
                      <strong>#{row.index}</strong>
                      <span style={{ color: "#64748b" }}>
                        {row.startSec.toFixed(2)}s – {row.endSec.toFixed(2)}s
                      </span>
                      <span style={{ color: "#64748b" }}>{row.prompt.split("\n", 1)[0].slice(0, 80)}</span>
                    </summary>
                    <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>{row.prompt}</pre>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="button button--ghost"
                          onClick={() => void navigator.clipboard.writeText(row.prompt)}
                        >
                          copy
                        </button>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {projectDetail && !timelineOnly ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <h2>4) 画風バリアント生成</h2>
            <p className="shell-panel__subtitle">「この画風とこの画風で動画用画像を作る」を、ジョブとしてまとめて実行します。</p>
            <VideoImageVariantsPanel
              project={projectDetail}
              channelPreset={channelPreset}
              generationOptions={generationOptions}
              onQuickJob={handleQuickJob}
            />
          </div>
        </section>
      ) : null}
    </div>
  );
}
