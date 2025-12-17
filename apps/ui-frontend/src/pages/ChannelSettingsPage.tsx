import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOutletContext, useNavigate, useLocation } from "react-router-dom";
import { ChannelProfilePanel } from "../components/ChannelProfilePanel";
import {
  fetchPlanningRows,
  updatePlanning,
  fetchThumbnailOverview,
  resolveApiUrl,
  fetchChannelProfile,
  refreshChannelBranding,
  assignThumbnailLibraryAsset,
  fetchThumbnailLibrary,
} from "../api/client";
import type {
  PlanningCsvRow,
  ChannelProfileResponse,
  ChannelSummary,
  ThumbnailLibraryAsset,
} from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

function formatDate(value?: string | null): string {
  if (!value) return "―";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", { hour12: false });
}

function formatCompactNumber(value?: number | null): string {
  if (typeof value !== "number") {
    return "―";
  }
  return new Intl.NumberFormat("ja-JP", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatFileSize(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const display = unitIndex === 0 ? Math.round(size).toString() : size.toFixed(1);
  return `${display} ${units[unitIndex]}`;
}

function normalizeVideoNumber(value?: string | null): string {
  if (!value) {
    return "";
  }
  const digits = value.replace(/[^0-9]/g, "");
  if (!digits) {
    return value;
  }
  return digits.padStart(3, "0");
}

function getLibraryFolderKey(asset: ThumbnailLibraryAsset): string {
  const parts = asset.relative_path.split("/").filter(Boolean);
  if (parts.length <= 2) {
    return "__root__";
  }
  const folderParts = parts.slice(1, -1);
  if (!folderParts.length) {
    return "__root__";
  }
  return folderParts.join("/");
}

function getLibraryFolderLabel(key: string): string {
  return key === "__root__" ? "チャンネル直下" : key;
}

function sanitizeTitle(title?: string | null): string {
  if (!title) return "タイトル未設定";
  const trimmed = title.trim().replace(/^#+\s*/, "");
  if (!trimmed) return "タイトル未設定";
  const limit = 40;
  return trimmed.length > limit ? `${trimmed.slice(0, limit)}…` : trimmed;
}

function pickRowTitle(row: { title?: string | null; columns?: Record<string, string | null | undefined> }): string {
  const csvTitle = row.columns?.["タイトル"];
  if (csvTitle && csvTitle.trim()) {
    return csvTitle;
  }
  return row.title ?? "";
}

function normalizeNo(value?: string | null, videoNumber?: string | null): string {
  const candidate = value?.trim();
  if (candidate && /^\d+$/.test(candidate)) {
    return candidate;
  }
  const normalized = normalizeVideoNumber(videoNumber);
  return normalized || candidate || "—";
}

function SectionCard({
  title,
  description,
  actions,
  children,
  defaultOpen = true,
  open: openOverride,
  onOpenChange,
  id,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  id?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const isControlled = openOverride !== undefined;
  const effectiveOpen = isControlled ? Boolean(openOverride) : open;
  const handleSetOpen = useCallback(
    (next: boolean) => {
      if (!isControlled) {
        setOpen(next);
      }
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange]
  );
  return (
    <section id={id} className={`channel-settings-card${effectiveOpen ? " is-open" : ""}`}>
      <header className="channel-settings-card__header">
        <div>
          <h3>{title}</h3>
          {description ? <p className="channel-settings-card__description">{description}</p> : null}
        </div>
        <div className="channel-settings-card__actions">
          {actions}
          <button
            type="button"
            className="channel-settings-card__toggle"
            onClick={() => handleSetOpen(!effectiveOpen)}
          >
            {effectiveOpen ? "最小化" : "展開"}
          </button>
        </div>
      </header>
      {effectiveOpen ? <div className="channel-settings-card__body">{children}</div> : null}
    </section>
  );
}

function convertRowsToCsv(rows: PlanningCsvRow[], targetColumns: string[]) {
  const headers = ["チャンネル", "動画番号", "動画ID", "タイトル", "進捗", "更新日時", ...targetColumns];
  const data = rows.map((row) => [
    row.channel ?? "",
    row.video_number ?? "",
    row.script_id ?? "",
    pickRowTitle(row) ?? "",
    row.progress ?? "",
    row.updated_at ?? "",
    ...targetColumns.map((column) => row.columns?.[column] ?? ""),
  ]);
  const escapeCell = (value: string) => `"${value.replace(/"/g, '""')}"`;
  const csvLines = [
    headers.map(escapeCell).join(","),
    ...data.map((line) => line.map((cell) => escapeCell(cell ?? "")).join(",")),
  ];
  return csvLines.join("\n");
}
function extractProfileSummary(profile: ChannelProfileResponse | null) {
  if (!profile) {
    return { tags: [] };
  }
  const descriptionDefaults = profile.planning_description_defaults ?? {};
  return {
    persona: profile.persona_summary ?? profile.audience_profile ?? null,
    planningPersona: profile.planning_persona ?? null,
    descriptionLead: descriptionDefaults.description_lead ?? profile.description ?? null,
    descriptionTakeaways: descriptionDefaults.description_takeaways ?? null,
    personaPath: profile.planning_persona_path ?? null,
    templatePath: profile.planning_template_path ?? null,
    tags: profile.default_tags ?? [],
  };
}

export function ChannelSettingsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    channels,
    selectedChannel,
    selectedChannelSummary,
    selectChannel,
  } = useOutletContext<ShellOutletContext>();
  const [planningRows, setPlanningRows] = useState<PlanningCsvRow[]>([]);
  const [planningLoading, setPlanningLoading] = useState(false);
  const [planningError, setPlanningError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [editor, setEditor] = useState<{
    open: boolean;
    row: PlanningCsvRow | null;
    values: Record<string, string>;
    saving: boolean;
    error: string | null;
  }>({ open: false, row: null, values: {}, saving: false, error: null });
  const [thumbnailMap, setThumbnailMap] = useState<Record<string, { url: string; label?: string | null }>>({});
  const [thumbnailLoading, setThumbnailLoading] = useState(false);
  const [thumbnailError, setThumbnailError] = useState<string | null>(null);
  const [progressFilter, setProgressFilter] = useState("all");
  const [profileSummary, setProfileSummary] = useState<{
    persona?: string | null;
    planningPersona?: string | null;
    descriptionLead?: string | null;
    descriptionTakeaways?: string | null;
    personaPath?: string | null;
    templatePath?: string | null;
    tags: string[];
  }>({ tags: [] });
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const copyNoticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [columnFilter, setColumnFilter] = useState<string | null>(null);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const sectionRef = useRef<HTMLElement | null>(null);
  const [brandingSummary, setBrandingSummary] = useState<ChannelSummary | null>(null);
  const [brandingStatus, setBrandingStatus] = useState<{ pending: boolean; success: string | null; error: string | null }>({
    pending: false,
    success: null,
    error: null,
  });
  const [registerSectionOpen, setRegisterSectionOpen] = useState(false);
  const [channelRegister, setChannelRegister] = useState<{
    channelCode: string;
    channelName: string;
    youtubeHandle: string;
    description: string;
    chapterCount: string;
    targetCharsMin: string;
    targetCharsMax: string;
  }>({
    channelCode: "",
    channelName: "",
    youtubeHandle: "",
    description: "",
    chapterCount: "",
    targetCharsMin: "",
    targetCharsMax: "",
  });
  const [channelRegisterStatus, setChannelRegisterStatus] = useState<{
    pending: boolean;
    success: string | null;
    error: string | null;
  }>({ pending: false, success: null, error: null });
  const [libraryAssets, setLibraryAssets] = useState<ThumbnailLibraryAsset[]>([]);
  const [libraryForms, setLibraryForms] = useState<
    Record<string, { video: string; pending: boolean; error?: string; success?: string }>
  >({});
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [libraryFilter, setLibraryFilter] = useState<string>("all");
  const handleCopy = useCallback((value: string, label: string) => {
    if (!value) {
      return;
    }
    navigator.clipboard
      .writeText(value)
      .then(() => {
        if (copyNoticeTimerRef.current) {
          clearTimeout(copyNoticeTimerRef.current);
        }
        setCopyNotice(`${label} をコピーしました`);
        copyNoticeTimerRef.current = setTimeout(() => {
          setCopyNotice(null);
          copyNoticeTimerRef.current = null;
        }, 3200);
      })
      .catch(() => {
        if (copyNoticeTimerRef.current) {
          clearTimeout(copyNoticeTimerRef.current);
        }
        setCopyNotice("クリップボードへのコピーに失敗しました");
        copyNoticeTimerRef.current = setTimeout(() => {
          setCopyNotice(null);
          copyNoticeTimerRef.current = null;
        }, 3200);
      });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const shouldOpen =
      location.hash === "#channel-register" || params.get("open") === "register" || params.get("add") === "1";
    if (!shouldOpen) {
      return;
    }
    setRegisterSectionOpen(true);
    window.setTimeout(() => {
      const el = document.getElementById("channel-register");
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }, [location.hash, location.search]);

  const targetColumns = useMemo(
    () => [
      "悩みタグ_メイン",
      "悩みタグ_サブ",
      "ライフシーン",
      "キーコンセプト",
      "ベネフィット一言",
      "たとえ話イメージ",
      "説明文_リード",
      "説明文_この動画でわかること",
    ],
    []
  );

  const handleSelectChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const value = event.target.value || null;
      selectChannel(value);
      if (value) {
        navigate(`/channel-settings?channel=${encodeURIComponent(value)}`, { replace: true });
      }
    },
    [navigate, selectChannel]
  );

  const loadPlanning = useCallback(
    (channelCode: string) => {
      let cancelled = false;
      setPlanningLoading(true);
      setPlanningError(null);
      fetchPlanningRows(channelCode)
        .then((rows) => {
          if (!cancelled) {
            setPlanningRows(rows);
          }
        })
        .catch((error) => {
          if (!cancelled) {
            setPlanningError(error instanceof Error ? error.message : String(error));
          }
        })
        .finally(() => {
          if (!cancelled) {
            setPlanningLoading(false);
          }
        });
      return () => {
        cancelled = true;
      };
    },
    []
  );

  useEffect(() => {
    if (!selectedChannel) {
      setPlanningRows([]);
      return;
    }
    const cleanup = loadPlanning(selectedChannel);
    return cleanup;
  }, [selectedChannel, loadPlanning]);

  useEffect(() => {
    setSearchTerm("");
    setProgressFilter("all");
    setColumnFilter(null);
    setLibraryFilter("all");
    if (copyNoticeTimerRef.current) {
      clearTimeout(copyNoticeTimerRef.current);
      copyNoticeTimerRef.current = null;
    }
    setCopyNotice(null);
  }, [selectedChannel]);

  const applyThumbnailUpdate = useCallback(
    (videoNumber: string, url: string, label?: string | null) => {
      if (!videoNumber || !url) {
        return;
      }
      const normalized = normalizeVideoNumber(videoNumber);
      if (!normalized) {
        return;
      }
      setThumbnailMap((current) => ({
        ...current,
        [normalized]: {
          url,
          label: label ?? null,
        },
      }));
    },
    []
  );

  const loadLibraryAssets = useCallback(
    async (options?: { silent?: boolean }) => {
      const channelCode = selectedChannel;
      if (!channelCode) {
        setLibraryAssets([]);
        setLibraryForms({});
        setLibraryError(null);
        setLibraryLoading(false);
        setLibraryFilter("all");
        return;
      }
      const silent = options?.silent ?? false;
      if (!silent) {
        setLibraryLoading(true);
        setLibraryError(null);
      }
      try {
        const assets = await fetchThumbnailLibrary(channelCode);
        setLibraryAssets(assets);
        setLibraryForms((current) => {
          const next: Record<string, { video: string; pending: boolean; error?: string; success?: string }> = {};
          assets.forEach((asset) => {
            const existing = current[asset.id];
            next[asset.id] = {
              video: existing?.video ?? "",
              pending: false,
            };
          });
          return next;
        });
        setLibraryFilter((current) => {
          if (current === "all") {
            return current;
          }
          const exists = assets.some((asset) => getLibraryFolderKey(asset) === current);
          return exists ? current : "all";
        });
        return assets;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryError(message);
      } finally {
        if (!silent) {
          setLibraryLoading(false);
        }
      }
    },
    [selectedChannel]
  );

  useEffect(() => {
    void loadLibraryAssets();
  }, [loadLibraryAssets]);

  const handleLibraryVideoChange = useCallback((assetId: string, value: string) => {
    setLibraryForms((current) => ({
      ...current,
      [assetId]: {
        ...(current[assetId] ?? { video: "", pending: false }),
        video: value,
      },
    }));
  }, []);

  const refreshThumbnails = useCallback(
    (options?: { silent?: boolean }) => {
      const channelCode = selectedChannel;
      if (!channelCode) {
        setThumbnailMap({});
        setThumbnailLoading(false);
        setThumbnailError(null);
        return () => {};
      }
      let cancelled = false;
      const silent = options?.silent ?? false;
      if (!silent) {
        setThumbnailLoading(true);
        setThumbnailError(null);
      }
      fetchThumbnailOverview()
        .then((overview) => {
          if (cancelled) {
            return;
          }
          const block = overview.channels.find((channel) => channel.channel === channelCode);
          if (!block) {
            setThumbnailMap({});
            return;
          }
          const next: Record<string, { url: string; label?: string | null }> = {};
          block.projects.forEach((project) => {
            const variant = project.variants.find((item) => item.is_selected) ?? project.variants[0];
            if (!variant?.image_url) {
              return;
            }
            const key = normalizeVideoNumber(project.video);
            if (!key) {
              return;
            }
            next[key] = {
              url: resolveApiUrl(variant.image_url),
              label: variant.label ?? variant.id ?? null,
            };
          });
          setThumbnailMap(next);
          setThumbnailError(null);
        })
        .catch((error) => {
          if (cancelled) {
            return;
          }
          const message = error instanceof Error ? error.message : String(error);
          setThumbnailError(message);
        })
        .finally(() => {
          if (!cancelled && !silent) {
            setThumbnailLoading(false);
          }
        });
      return () => {
        cancelled = true;
      };
    },
    [selectedChannel]
  );

  useEffect(() => {
    const cleanup = refreshThumbnails();
    return () => {
      cleanup?.();
    };
  }, [refreshThumbnails]);

  const handleLibraryAssign = useCallback(
    async (asset: ThumbnailLibraryAsset) => {
      const channelCode = selectedChannel;
      if (!channelCode) {
        return;
      }
      const state = libraryForms[asset.id] ?? { video: "", pending: false };
      const normalized = normalizeVideoNumber(state.video);
      if (!normalized || !/\d+/.test(state.video.replace(/[^0-9]/g, ""))) {
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { ...state, error: "動画番号を入力してください。", success: undefined },
        }));
        return;
      }
      setLibraryForms((current) => {
        const base = current[asset.id] ?? { video: "", pending: false };
        return {
          ...current,
          [asset.id]: { ...base, pending: true, error: undefined, success: undefined },
        };
      });
      try {
        const response = await assignThumbnailLibraryAsset(channelCode, asset.relative_path, {
          video: normalized,
          label: asset.file_name.replace(/\.[^.]+$/, ""),
          make_selected: true,
        });
        const publicUrl = resolveApiUrl(response.public_url);
        applyThumbnailUpdate(normalized, publicUrl, asset.file_name);
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { video: "", pending: false, success: `#${normalized} に登録しました`, error: undefined },
        }));
        await refreshThumbnails({ silent: true });
        await loadLibraryAssets({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryForms((current) => {
          const base = current[asset.id] ?? { video: "", pending: false };
          return {
            ...current,
            [asset.id]: { ...base, pending: false, error: message, success: undefined },
          };
        });
      }
    },
    [applyThumbnailUpdate, libraryForms, loadLibraryAssets, refreshThumbnails, selectedChannel]
  );

  useEffect(() => {
    setBrandingSummary(selectedChannelSummary);
    setBrandingStatus((current) => ({ ...current, pending: false, success: null, error: null }));
  }, [selectedChannelSummary]);

  // クイック登録は非表示のため履歴や入力リセットは行わない

  useEffect(() => {
    if (!selectedChannel) {
      setProfileSummary({ tags: [] });
      setProfileLoading(false);
      setProfileError(null);
      return;
    }
    let cancelled = false;
    setProfileLoading(true);
    setProfileError(null);
    fetchChannelProfile(selectedChannel)
      .then((profile) => {
        if (cancelled) {
          return;
        }
        setProfileSummary(extractProfileSummary(profile));
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        setProfileError(message);
      })
      .finally(() => {
        if (!cancelled) {
          setProfileLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedChannel]);

  const progressOptions = useMemo(() => {
    const counts = new Map<string, number>();
    planningRows.forEach((row) => {
      const key = row.progress?.trim() || "未設定";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    });
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([value, count]) => ({ value, count }));
  }, [planningRows]);

  const requiredFieldStats = useMemo(() => {
    return targetColumns.map((column) => {
      const missing = planningRows.filter((row) => !row.columns?.[column]?.trim()).length;
      return { column, missing };
    });
  }, [planningRows, targetColumns]);

  const filteredRows = useMemo(() => {
    const keyword = searchTerm.trim();
    return planningRows.filter((row) => {
      if (progressFilter !== "all") {
        const progressValue = row.progress?.trim() || "未設定";
        if (progressValue !== progressFilter) {
          return false;
        }
      }
      if (columnFilter) {
        const value = row.columns?.[columnFilter];
        if (value && value.trim()) {
          return false;
        }
      }
      if (!keyword) {
        return true;
      }
      const title = pickRowTitle(row) ?? "";
      const scriptId = row.script_id ?? "";
      return title.includes(keyword) || scriptId.includes(keyword);
    });
  }, [planningRows, searchTerm, progressFilter, columnFilter]);

  const sortedRows = useMemo(() => {
    const toNumber = (value?: string | null) => {
      const norm = normalizeVideoNumber(value);
      if (norm && /^\d+$/.test(norm)) return parseInt(norm, 10);
      return Number.MAX_SAFE_INTEGER;
    };
    return [...filteredRows].sort((a, b) => toNumber(a.video_number) - toNumber(b.video_number));
  }, [filteredRows]);

  const handleExportCsv = useCallback(() => {
    if (!filteredRows.length) {
      return;
    }
    const csvContent = convertRowsToCsv(filteredRows, targetColumns);
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedChannel ?? "ALL"}_planning_filtered.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }, [filteredRows, selectedChannel, targetColumns]);

  const latestUpdated = useMemo(() => {
    let latest: string | null = null;
    planningRows.forEach((row) => {
      if (!row.updated_at) {
        return;
      }
      if (!latest || row.updated_at > latest) {
        latest = row.updated_at;
      }
    });
    return latest ? formatDate(latest) : "―";
  }, [planningRows]);

  const completedCount = useMemo(
    () => planningRows.filter((row) => (row.progress ?? "").includes("completed")).length,
    [planningRows]
  );

  const missingThumbnailCount = useMemo(() => {
    return filteredRows.filter((row) => {
      const key = normalizeVideoNumber(row.video_number);
      if (!key) {
        return true;
      }
      return !thumbnailMap[key]?.url;
    }).length;
  }, [filteredRows, thumbnailMap]);

  const totalRows = planningRows.length;
  const filteredCount = sortedRows.length;
  const thumbnailsTotal = Object.keys(thumbnailMap).length;
  const libraryFolderOptions = useMemo(() => {
    const counts = new Map<string, number>();
    libraryAssets.forEach((asset) => {
      const key = getLibraryFolderKey(asset);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    });
    return Array.from(counts.entries())
      .map(([key, count]) => ({ key, label: getLibraryFolderLabel(key), count }))
      .sort((a, b) => {
        if (a.key === "__root__") return -1;
        if (b.key === "__root__") return 1;
        return a.label.localeCompare(b.label, "ja");
      });
  }, [libraryAssets]);
  const filteredLibraryAssets = useMemo(() => {
    if (libraryFilter === "all") {
      return libraryAssets;
    }
    return libraryAssets.filter((asset) => getLibraryFolderKey(asset) === libraryFilter);
  }, [libraryAssets, libraryFilter]);
  const personaFields = useMemo(() => {
    return [
      { key: "persona", label: "固定ペルソナ", value: profileSummary.persona },
      { key: "planningPersona", label: "企画シート用ペルソナ", value: profileSummary.planningPersona },
      { key: "descriptionLead", label: "説明文リード", value: profileSummary.descriptionLead },
      { key: "descriptionTakeaways", label: "説明文_この動画でわかること", value: profileSummary.descriptionTakeaways },
    ];
  }, [profileSummary]);
  const summaryDisplayName = useMemo(() => {
    if (!selectedChannel) {
      return "";
    }
    return (
      selectedChannelSummary?.branding?.title ??
      selectedChannelSummary?.youtube_title ??
      selectedChannelSummary?.name ??
      selectedChannel
    );
  }, [selectedChannel, selectedChannelSummary]);

  const youtubeMetrics = useMemo(() => {
    if (!brandingSummary?.branding) {
      return null;
    }
    const branding = brandingSummary.branding;
    return {
      avatarUrl: branding.avatar_url ?? null,
      handle: branding.handle ?? brandingSummary.youtube_handle ?? null,
      url: branding.url ?? null,
      updatedAt: branding.updated_at ?? null,
      subscriberCount: branding.subscriber_count ?? null,
      viewCount: branding.view_count ?? null,
      videoCount: branding.video_count ?? null,
    };
  }, [brandingSummary]);

  useEffect(() => {
    const handleScroll = () => {
      setShowScrollTop(window.scrollY > 320);
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", handleScroll);
    };
  }, []);

  const handleScrollTop = useCallback(() => {
    if (sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, []);

  const handleRefreshBranding = useCallback(async () => {
    if (!selectedChannel) {
      return;
    }
    setBrandingStatus({ pending: true, success: null, error: null });
    try {
      const refreshed = await refreshChannelBranding(selectedChannel);
      setBrandingSummary(refreshed);
      setBrandingStatus({
        pending: false,
        success: `${selectedChannel} の YouTube 情報を更新しました。`,
        error: null,
      });
    } catch (error) {
      setBrandingStatus({
        pending: false,
        success: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [selectedChannel]);

  const handleRegisterChannel = useCallback(async () => {
    const trimmedCode = channelRegister.channelCode.trim();
    const trimmedName = channelRegister.channelName.trim();
    const trimmedHandle = channelRegister.youtubeHandle.trim();
    if (!trimmedCode || !trimmedName || !trimmedHandle) {
      setChannelRegisterStatus({
        pending: false,
        success: null,
        error: "CHコード / 表示名 / YouTubeハンドルは必須です。",
      });
      return;
    }
    const numericOrNull = (value: string): number | null => {
      const raw = value.trim();
      if (!raw) return null;
      const parsed = Number(raw);
      return Number.isFinite(parsed) ? parsed : null;
    };

    setChannelRegisterStatus({ pending: true, success: null, error: null });
    try {
      const response = await fetch(resolveApiUrl("/api/channels/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_code: trimmedCode,
          channel_name: trimmedName,
          youtube_handle: trimmedHandle,
          description: channelRegister.description.trim() || null,
          chapter_count: numericOrNull(channelRegister.chapterCount),
          target_chars_min: numericOrNull(channelRegister.targetCharsMin),
          target_chars_max: numericOrNull(channelRegister.targetCharsMax),
        }),
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const data = await response.json();
          detail = (data?.detail as string) ?? detail;
        } catch {
          try {
            detail = (await response.text()) || detail;
          } catch {
            /* no-op */
          }
        }
        throw new Error(detail);
      }
      const profile = (await response.json()) as ChannelProfileResponse;
      setChannelRegisterStatus({
        pending: false,
        success: `${profile.channel_code} を登録しました。ページを再読み込みします…`,
        error: null,
      });
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      setChannelRegisterStatus({
        pending: false,
        success: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [channelRegister]);

  // クイック登録は非表示のため処理を無効化

  return (
    <section className="channel-settings-page workspace--channel-clean" ref={sectionRef}>
      <header className="channel-settings-page__header">
        <div>
          <h1>チャンネル詳細設定</h1>
        </div>
        <div className="channel-settings-page__controls">
          <label className="channel-settings-page__select-label">
            <span>チャンネル</span>
            <select value={selectedChannel ?? ""} onChange={handleSelectChange}>
              <option value="">未選択</option>
              {channels.map((channel) => (
                <option key={channel.code} value={channel.code}>
                  {channel.code}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="channel-settings-page__button channel-settings-page__button--primary"
            onClick={() => {
              setRegisterSectionOpen(true);
              window.setTimeout(() => {
                const el = document.getElementById("channel-register");
                el?.scrollIntoView({ behavior: "smooth", block: "start" });
              }, 0);
            }}
          >
            ＋ 新規チャンネル追加
          </button>
        </div>
      </header>

      <div className="channel-settings-page__quick-select" role="tablist" aria-label="チャンネル切り替え">
        {channels.map((channel) => (
          <button
            key={channel.code}
            type="button"
            className={
              channel.code === selectedChannel
                ? "channel-settings-page__pill channel-settings-page__pill--active"
                : "channel-settings-page__pill"
            }
            onClick={() => {
              selectChannel(channel.code);
              navigate(`/channel-settings?channel=${encodeURIComponent(channel.code)}`, { replace: true });
            }}
          >
            {channel.code}
          </button>
        ))}
      </div>

      <SectionCard
        title="新規チャンネル登録（ハンドル必須）"
        description="YouTubeハンドル(@name)だけで一意特定し、channels/channel_info + planning/persona + sources.yaml を自動生成します。"
        id="channel-register"
        open={registerSectionOpen}
        onOpenChange={setRegisterSectionOpen}
        defaultOpen={false}
      >
        <p className="channel-settings-page__quick-add-description">
          入力は「CHコード / 表示名 / YouTubeハンドル」の3つが必須です。登録後にページを再読み込みします。
        </p>
        <div className="channel-settings-page__quick-add-form">
          <label>
            <span>CHコード（必須）</span>
            <input
              type="text"
              value={channelRegister.channelCode}
              onChange={(event) =>
                setChannelRegister((current) => ({ ...current, channelCode: event.target.value.toUpperCase() }))
              }
              placeholder="例: CH17"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>表示名（必須）</span>
            <input
              type="text"
              value={channelRegister.channelName}
              onChange={(event) => setChannelRegister((current) => ({ ...current, channelName: event.target.value }))}
              placeholder="例: ブッダの◯◯"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>YouTubeハンドル（必須）</span>
            <input
              type="text"
              value={channelRegister.youtubeHandle}
              onChange={(event) => setChannelRegister((current) => ({ ...current, youtubeHandle: event.target.value }))}
              placeholder="例: @buddha-a001"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>説明（任意）</span>
            <input
              type="text"
              value={channelRegister.description}
              onChange={(event) => setChannelRegister((current) => ({ ...current, description: event.target.value }))}
              placeholder="チャンネルの一言説明"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>chapter_count（任意）</span>
            <input
              type="text"
              inputMode="numeric"
              value={channelRegister.chapterCount}
              onChange={(event) => setChannelRegister((current) => ({ ...current, chapterCount: event.target.value }))}
              placeholder="例: 8"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>target_chars_min（任意）</span>
            <input
              type="text"
              inputMode="numeric"
              value={channelRegister.targetCharsMin}
              onChange={(event) => setChannelRegister((current) => ({ ...current, targetCharsMin: event.target.value }))}
              placeholder="例: 6500"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <label>
            <span>target_chars_max（任意）</span>
            <input
              type="text"
              inputMode="numeric"
              value={channelRegister.targetCharsMax}
              onChange={(event) => setChannelRegister((current) => ({ ...current, targetCharsMax: event.target.value }))}
              placeholder="例: 8500"
              disabled={channelRegisterStatus.pending}
            />
          </label>
          <div>
            <button
              type="button"
              className="channel-settings-page__button channel-settings-page__button--primary"
              onClick={handleRegisterChannel}
              disabled={channelRegisterStatus.pending}
            >
              {channelRegisterStatus.pending ? "登録中…" : "登録する"}
            </button>
          </div>
        </div>
        {channelRegisterStatus.error ? <p className="channel-settings-page__warning">{channelRegisterStatus.error}</p> : null}
        {channelRegisterStatus.success ? (
          <p className="channel-settings-page__success">{channelRegisterStatus.success}</p>
        ) : null}
      </SectionCard>

      {!selectedChannel ? (
        <p className="channel-settings-page__placeholder">左上のプルダウンからチャンネルを選択してください。</p>
      ) : (
        <>
          {copyNotice ? (
            <div className="channel-settings-page__toast">{copyNotice}</div>
          ) : null}
          <section className="channel-settings-page__summary">
            <div className="channel-settings-page__summary-header">
              <div>
                <h2>{summaryDisplayName}</h2>
                <p className="channel-settings-page__summary-subtitle">{selectedChannel}</p>
              </div>
              <div className="channel-settings-page__summary-actions">
                <span>最終更新 {latestUpdated || "—"}</span>
                <span>サムネイル {thumbnailsTotal} 件</span>
                <button type="button" onClick={() => refreshThumbnails()}>
                  🔄 サムネイル再取得
                </button>
              </div>
            </div>
            <div className="channel-settings-page__kpis">
              <div className="channel-settings-page__kpi">
                <span>全企画</span>
                <strong>{totalRows}</strong>
              </div>
              <div className="channel-settings-page__kpi">
                <span>表示中</span>
                <strong>{filteredCount}</strong>
              </div>
              <div className="channel-settings-page__kpi">
                <span>completed 含む</span>
                <strong>{completedCount}</strong>
              </div>
              <div className="channel-settings-page__kpi">
                <span>サムネ未取得</span>
                <strong>{missingThumbnailCount}</strong>
              </div>
            </div>
          </section>
          <section className="channel-settings-page__youtube">
            <div className="channel-settings-page__youtube-header">
              <div className="channel-settings-page__youtube-identity">
                {youtubeMetrics?.avatarUrl ? (
                  <img src={youtubeMetrics.avatarUrl} alt="" className="channel-settings-page__youtube-avatar" />
                ) : (
                  <div className="channel-settings-page__youtube-avatar channel-settings-page__youtube-avatar--fallback">
                    {summaryDisplayName.slice(0, 2) || selectedChannel.slice(0, 2)}
                  </div>
                )}
                <div>
                  <p className="channel-settings-page__youtube-handle">
                    {youtubeMetrics?.handle ? `@${youtubeMetrics.handle}` : "YouTube 情報"}
                  </p>
                  {youtubeMetrics?.updatedAt ? (
                    <small>最終同期 {formatDate(youtubeMetrics.updatedAt)}</small>
                  ) : null}
                </div>
              </div>
              <div className="channel-settings-page__youtube-actions">
                {youtubeMetrics?.url ? (
                  <a
                    className="channel-settings-page__button channel-settings-page__button--ghost"
                    href={youtubeMetrics.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    YouTubeを開く ↗
                  </a>
                ) : null}
                <button
                  type="button"
                  className="channel-settings-page__button"
                  onClick={handleRefreshBranding}
                  disabled={brandingStatus.pending}
                >
                  {brandingStatus.pending ? "同期中…" : "YouTube情報を再取得"}
                </button>
              </div>
            </div>
            {brandingStatus.error ? (
              <p className="channel-settings-page__warning">{brandingStatus.error}</p>
            ) : null}
            {brandingStatus.success ? (
              <p className="channel-settings-page__success">{brandingStatus.success}</p>
            ) : null}
            {youtubeMetrics ? (
              <div className="channel-settings-page__youtube-metrics">
                <div>
                  <span>登録者</span>
                  <strong>{formatCompactNumber(youtubeMetrics.subscriberCount)}</strong>
                </div>
                <div>
                  <span>総再生回数</span>
                  <strong>{formatCompactNumber(youtubeMetrics.viewCount)}</strong>
                </div>
                <div>
                  <span>公開本数</span>
                  <strong>{formatCompactNumber(youtubeMetrics.videoCount)}</strong>
                </div>
              </div>
            ) : (
              <p className="channel-settings-page__placeholder">YouTube 情報を同期するとメトリクスが表示されます。</p>
            )}
          </section>
          {/* クイック登録は未使用のため非表示 */}
        <SectionCard
          title="ライブラリから登録"
          description="最近取り込んだサムネをそのまま企画に紐付けできます。"
          defaultOpen={false}
          actions={
            <div className="channel-settings-page__library-actions">
              <button
                type="button"
                className="channel-settings-page__button channel-settings-page__button--ghost"
                onClick={() => loadLibraryAssets()}
                disabled={libraryLoading}
              >
                {libraryLoading ? "更新中…" : "最新の状態に更新"}
              </button>
              <button
                type="button"
                className="channel-settings-page__button channel-settings-page__button--ghost"
                onClick={() => navigate(`/thumbnails?channel=${encodeURIComponent(selectedChannel ?? "")}`)}
              >
                サムネイルページを開く
              </button>
            </div>
          }
        >
          <div className="channel-settings-page__library">
            <div className="channel-settings-page__library-filters">
              <label>
                <span>フォルダ</span>
                <select value={libraryFilter} onChange={(event) => setLibraryFilter(event.target.value)}>
                  <option value="all">すべて（{libraryAssets.length}件）</option>
                  {libraryFolderOptions.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label}（{option.count}）
                    </option>
                  ))}
                </select>
              </label>
              <span className="channel-settings-page__library-count">
                表示 {filteredLibraryAssets.length} / {libraryAssets.length} 件
              </span>
            </div>
            {libraryError ? <p className="channel-settings-page__warning">{libraryError}</p> : null}
            {libraryLoading && libraryAssets.length === 0 ? (
              <p className="channel-settings-page__placeholder">ライブラリを読み込み中です…</p>
            ) : filteredLibraryAssets.length ? (
              <div className="channel-settings-page__library-grid">
                {filteredLibraryAssets.map((asset) => {
                  const formState = libraryForms[asset.id] ?? { video: "", pending: false };
                  const previewUrl = resolveApiUrl(asset.public_url);
                  const folderLabel = getLibraryFolderLabel(getLibraryFolderKey(asset));
                  return (
                    <article key={asset.id} className="channel-settings-page__library-card">
                      <div className="channel-settings-page__library-preview">
                        <img src={previewUrl} alt={asset.file_name} loading="lazy" />
                      </div>
                      <div className="channel-settings-page__library-meta">
                        <strong>{asset.file_name}</strong>
                        <span>{folderLabel}</span>
                        <span>{asset.relative_path}</span>
                        <span>
                          {formatFileSize(asset.size_bytes)}・{formatDate(asset.updated_at)}
                        </span>
                      </div>
                      <div className="channel-settings-page__library-form">
                        <label>
                          <span>動画番号</span>
                          <input
                            type="text"
                            inputMode="numeric"
                            value={formState.video}
                            onChange={(event) => handleLibraryVideoChange(asset.id, event.target.value)}
                            placeholder="例: 191"
                            disabled={formState.pending}
                          />
                        </label>
                        <button
                          type="button"
                          className="channel-settings-page__button channel-settings-page__button--primary"
                          onClick={() => handleLibraryAssign(asset)}
                          disabled={formState.pending || !formState.video.trim()}
                        >
                          {formState.pending ? "紐付け中…" : "この企画に使う"}
                        </button>
                      </div>
                      {formState.error ? (
                        <p className="channel-settings-page__warning">{formState.error}</p>
                      ) : null}
                      {formState.success ? (
                        <p className="channel-settings-page__success">{formState.success}</p>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            ) : (
              <p className="channel-settings-page__placeholder">
                ライブラリに表示できる画像がありません。サムネイルページから登録してください。
              </p>
            )}
          </div>
        </SectionCard>
          <div className="channel-settings-page__filters">
            {progressFilter !== "all" ? (
              <button
                type="button"
                className="channel-settings-page__filter-badge"
                onClick={() => setProgressFilter("all")}
              >
                進捗: {progressFilter} ×
              </button>
            ) : null}
            {columnFilter ? (
              <button
                type="button"
                className="channel-settings-page__filter-badge"
                onClick={() => setColumnFilter(null)}
              >
                欠損列: {columnFilter} ×
              </button>
            ) : null}
            <button
              type="button"
              className="channel-settings-page__button channel-settings-page__button--ghost"
              onClick={handleExportCsv}
              disabled={!filteredRows.length}
            >
              CSV書き出し
            </button>
            <button
              type="button"
              className="channel-settings-page__button channel-settings-page__button--ghost"
              onClick={() => handleCopy(filteredRows.map((row) => row.script_id ?? "").join(", "), "動画ID一覧")}
              disabled={!filteredRows.length}
            >
              動画IDコピー
            </button>
          </div>
            {requiredFieldStats.some((item) => item.missing > 0) ? (
              <SectionCard
                title="必須列の欠損状況"
                description="クリックすると欠損行のみ表示します"
                defaultOpen={false}
              >
                <div className="channel-settings-page__requirements">
                  <div className="channel-settings-page__requirements-list">
                    {requiredFieldStats.map((item) => (
                      <button
                        key={item.column}
                        type="button"
                        className={
                          columnFilter === item.column
                            ? "channel-settings-page__requirements-chip is-active"
                            : "channel-settings-page__requirements-chip"
                        }
                        disabled={item.missing === 0}
                        onClick={() => setColumnFilter(item.column)}
                      >
                        <span>{item.column}</span>
                        <strong>{item.missing}</strong>
                      </button>
                    ))}
                  </div>
                </div>
              </SectionCard>
            ) : null}
          <SectionCard title="ペルソナ / 説明文デフォルト" defaultOpen={false}>
            <div className="channel-settings-page__persona-card">
              <div className="channel-settings-page__persona-content">
                <div>
                  <h3>固定ペルソナ / 説明文デフォルト</h3>
                </div>
                {profileLoading ? (
                  <p className="channel-settings-page__placeholder">読み込み中…</p>
                ) : profileError ? (
                  <p className="channel-settings-page__error">ペルソナ取得に失敗: {profileError}</p>
                ) : (
                  <dl>
                    {personaFields.map((field) => (
                      <div key={field.key} className="channel-settings-page__persona-field">
                        <dt>{field.label}</dt>
                        <dd>{field.value ?? "—"}</dd>
                        {field.value ? (
                          <button
                            type="button"
                            className="channel-settings-page__persona-copy"
                            onClick={() => handleCopy(field.value!, field.label)}
                          >
                            コピー
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </dl>
                )}
              </div>
              {profileSummary.tags.length ? (
                <div className="channel-settings-page__persona-tags">
                  {profileSummary.tags.slice(0, 6).map((tag) => (
                    <span key={tag}>{tag}</span>
                  ))}
                  {profileSummary.tags.length > 6 ? (
                    <span className="channel-settings-page__persona-tags-more">
                      +{profileSummary.tags.length - 6}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="channel-settings-page__persona-copy channel-settings-page__persona-copy--tag"
                    onClick={() => handleCopy(profileSummary.tags.join(", "), "デフォルトタグ")}
                  >
                    タグをコピー
                  </button>
                </div>
              ) : null}
              {(profileSummary.personaPath || profileSummary.templatePath) ? (
                <div className="channel-settings-page__persona-files">
                  {profileSummary.personaPath ? (
                    <button
                      type="button"
                      className="channel-settings-page__persona-file"
                      onClick={() => handleCopy(profileSummary.personaPath!, "ペルソナファイルパス")}
                    >
                      ペルソナDoc: <code>{profileSummary.personaPath}</code>
                    </button>
                  ) : null}
                  {profileSummary.templatePath ? (
                    <button
                      type="button"
                      className="channel-settings-page__persona-file"
                      onClick={() => handleCopy(profileSummary.templatePath!, "テンプレCSVパス")}
                    >
                      テンプレCSV: <code>{profileSummary.templatePath}</code>
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </SectionCard>
          <div className="channel-settings-page__grid">
            <div className="channel-settings-page__planning">
              <div className="channel-settings-page__planning-header">
                <div>
                  <h2>企画一覧</h2>
                </div>
                <div className="channel-settings-page__planning-actions">
                  <label className="channel-settings-page__field">
                    <span>動画番号 / タイトル</span>
                    <input
                      id="channel-settings-search"
                      type="text"
                      value={searchTerm}
                      placeholder="例: 015 / 孤独"
                      onChange={(event) => setSearchTerm(event.target.value)}
                    />
                  </label>
                  <label className="channel-settings-page__field">
                    <span>進捗フィルター</span>
                    <select value={progressFilter} onChange={(event) => setProgressFilter(event.target.value)}>
                      <option value="all">すべて</option>
                      {progressOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.value}（{option.count}）
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            {planningError ? (
              <div className="channel-settings-page__error">{planningError}</div>
            ) : null}
            {thumbnailError ? (
              <div className="channel-settings-page__warning">サムネイル取得に失敗: {thumbnailError}</div>
            ) : null}
            <div className="channel-settings-page__table-wrapper">
              {planningLoading ? (
                <p className="channel-settings-page__placeholder">読み込み中…</p>
              ) : filteredRows.length === 0 ? (
                <p className="channel-settings-page__placeholder">該当する企画がありません。</p>
              ) : (
                <table className="channel-settings-page__table">
                  <thead>
                    <tr>
                      <th>No.</th>
                      <th>動画ID</th>
                      <th>タイトル</th>
                      <th>サムネイル</th>
                      <th>進捗</th>
                      <th>更新日時</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map((row) => {
                      const displayNo = normalizeNo(row.columns?.["No."], row.video_number);
                      const displayTitle = sanitizeTitle(pickRowTitle(row));
                      return (
                        <tr key={`${row.channel}-${row.video_number}`}>
                          <td className="channel-settings-page__col-no">{displayNo}</td>
                          <td className="channel-settings-page__col-id">{row.script_id ?? `${row.channel}-${row.video_number}`}</td>
                          <td className="channel-settings-page__table-title">{displayTitle}</td>
                          <td className="channel-settings-page__col-thumb">
                            {(() => {
                              const thumbKey = normalizeVideoNumber(row.video_number);
                              const thumbnail = thumbKey ? thumbnailMap[thumbKey] : undefined;
                              if (thumbnail?.url) {
                                return (
                                  <img
                                    src={thumbnail.url}
                                    alt={thumbnail.label ?? "サムネイル"}
                                    className="channel-settings-page__thumb"
                                    loading="lazy"
                                  />
                                );
                              }
                              return (
                                <span className="channel-settings-page__thumb-placeholder">
                                  {thumbnailLoading ? "読込中…" : "—"}
                                </span>
                              );
                            })()}
                          </td>
                          <td className="channel-settings-page__col-progress">{row.progress ?? "未設定"}</td>
                          <td className="channel-settings-page__col-updated">{formatDate(row.updated_at)}</td>
                          <td>
                            <button
                              type="button"
                              className="channel-settings-page__link"
                              onClick={() => navigate(`/channels/${row.channel}/videos/${row.video_number}`)}
                            >
                              詳細へ
                            </button>
                            <button
                              type="button"
                              className="channel-settings-page__link"
                              onClick={() => {
                                setEditor({
                                  open: true,
                                  row,
                                  values: targetColumns.reduce((acc, column) => {
                                    acc[column] = row.columns?.[column] ?? "";
                                    return acc;
                                  }, {} as Record<string, string>),
                                  saving: false,
                                  error: null,
                                });
                              }}
                            >
                              編集
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
            <div className="channel-settings-page__profile">
              <ChannelProfilePanel channelCode={selectedChannel} channelName={selectedChannelSummary?.name ?? null} />
            </div>
          </div>
        </>
      )}

      {editor.open && editor.row ? (
        <div className="channel-settings-editor" role="dialog" aria-modal="true">
          <div className="channel-settings-editor__backdrop" onClick={() => setEditor({ open: false, row: null, values: {}, saving: false, error: null })} />
          <div className="channel-settings-editor__panel">
            <header>
              <h3>
                {editor.row.channel}-{editor.row.video_number} ({editor.row.title})
              </h3>
              <p>悩みタグや説明文など企画の主要フィールドを編集します。</p>
            </header>
            <div className="channel-settings-editor__grid">
              {targetColumns.map((column) => {
                const isLong =
                  column.includes("説明文") ||
                  column.includes("ベネフィット") ||
                  column.includes("たとえ話") ||
                  column.includes("悩みタグ") ||
                  column.includes("ライフシーン") ||
                  column.includes("キーコンセプト");
                return (
                  <label key={column} className={isLong ? "channel-settings-editor__field channel-settings-editor__field--wide" : "channel-settings-editor__field"}>
                    <span>{column}</span>
                    <textarea
                      rows={isLong ? 5 : 3}
                      value={editor.values[column] ?? ""}
                      onChange={(event) =>
                        setEditor((prev) => ({
                          ...prev,
                          values: { ...prev.values, [column]: event.target.value },
                        }))
                      }
                    />
                  </label>
                );
              })}
            </div>
            {editor.error ? <p className="channel-settings-editor__error">{editor.error}</p> : null}
            <div className="channel-settings-editor__actions">
              <button
                type="button"
                className="channel-profile-button channel-profile-button--secondary"
                onClick={() =>
                  setEditor({ open: false, row: null, values: {}, saving: false, error: null })
                }
                disabled={editor.saving}
              >
                キャンセル
              </button>
              <button
                type="button"
                className="channel-profile-button channel-profile-button--primary"
                onClick={async () => {
                  if (!editor.row) {
                    return;
                  }
                  setEditor((prev) => ({ ...prev, saving: true, error: null }));
                  try {
                    await updatePlanning(editor.row.channel, editor.row.video_number, {
                      fields: {
                        primary_pain_tag: editor.values["悩みタグ_メイン"] ?? null,
                        secondary_pain_tag: editor.values["悩みタグ_サブ"] ?? null,
                        life_scene: editor.values["ライフシーン"] ?? null,
                        key_concept: editor.values["キーコンセプト"] ?? null,
                        benefit_blurb: editor.values["ベネフィット一言"] ?? null,
                        analogy_image: editor.values["たとえ話イメージ"] ?? null,
                        description_lead: editor.values["説明文_リード"] ?? null,
                        description_takeaways: editor.values["説明文_この動画でわかること"] ?? null,
                      },
                    });
                    setEditor({ open: false, row: null, values: {}, saving: false, error: null });
                    if (selectedChannel) {
                      loadPlanning(selectedChannel);
                    }
                  } catch (error) {
                    setEditor((prev) => ({
                      ...prev,
                      saving: false,
                      error: error instanceof Error ? error.message : String(error),
                    }));
                  }
                }}
                disabled={editor.saving}
              >
                {editor.saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {showScrollTop ? (
        <button
          type="button"
          className="channel-settings-page__scroll-top"
          onClick={handleScrollTop}
        >
          ↑
          <span>TOP</span>
        </button>
      ) : null}
    </section>
  );
}
