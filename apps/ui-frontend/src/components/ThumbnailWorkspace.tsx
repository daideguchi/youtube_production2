import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import {
  assignThumbnailLibraryAsset,
  createPlanningRow,
  createThumbnailVariant,
  describeThumbnailLibraryAsset,
  fetchProgressCsv,
  fetchThumbnailImageModels,
  fetchThumbnailLibrary,
  fetchThumbnailOverview,
  fetchThumbnailTemplates,
  generateThumbnailVariants,
  importThumbnailLibraryAsset,
  resolveApiUrl,
  updatePlanning,
  updateThumbnailProject,
  updateThumbnailTemplates,
  uploadThumbnailVariantAsset,
  uploadThumbnailLibraryAssets,
} from "../api/client";
import {
  PlanningCreatePayload,
  ThumbnailChannelBlock,
  ThumbnailChannelVideo,
  ThumbnailChannelTemplates,
  ThumbnailImageModelInfo,
  ThumbnailLibraryAsset,
  ThumbnailOverview,
  ThumbnailProject,
  ThumbnailProjectStatus,
  ThumbnailVariant,
  ThumbnailVariantStatus,
} from "../api/types";

type StatusFilter = "all" | "draft" | "in_progress" | "review" | "approved" | "archived";

type ThumbnailWorkspaceTab = "projects" | "templates" | "library" | "channel";

type VariantFormState = {
  projectKey: string;
  label: string;
  status: ThumbnailVariantStatus;
  imageUrl: string;
  imagePath: string;
  notes: string;
  tags: string;
  prompt: string;
  makeSelected: boolean;
  showAdvanced: boolean;
};

type ProjectFormState = {
  projectKey: string;
  owner: string;
  summary: string;
  notes: string;
  tags: string;
  dueAt: string;
};

type PlanningDialogState = {
  projectKey: string;
  channel: string;
  projectTitle: string;
  variantLabel?: string;
  videoNumber: string;
  no: string;
  title: string;
  thumbnailUpper: string;
  thumbnailLower: string;
  thumbnailTitle: string;
  thumbnailPrompt: string;
  dallePrompt: string;
  conceptIntent: string;
  outlineNotes: string;
  primaryTag: string;
  secondaryTag: string;
  lifeScene: string;
  keyConcept: string;
  benefit: string;
  analogy: string;
  descriptionLead: string;
  descriptionTakeaways: string;
  saving: boolean;
  error?: string;
};

type GenerateDialogState = {
  projectKey: string;
  channel: string;
  video: string;
  templateId: string;
  prompt: string;
  sourceTitle: string;
  thumbnailPrompt: string;
  imageModelKey: string;
  count: number;
  label: string;
  copyUpper: string;
  copyTitle: string;
  copyLower: string;
  saveToPlanning: boolean;
  status: ThumbnailVariantStatus;
  makeSelected: boolean;
  tags: string;
  notes: string;
  saving: boolean;
  error?: string;
};

type PlanningEditableField = Exclude<
  keyof PlanningDialogState,
  "projectKey" | "channel" | "projectTitle" | "variantLabel" | "saving" | "error"
>;

type CardFeedback = {
  type: "success" | "error";
  message: ReactNode;
  timestamp: number;
};

type LibraryFormState = {
  video: string;
  pending: boolean;
  error?: string;
  success?: string;
};

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "draft", label: "ドラフト" },
  { key: "in_progress", label: "作業中" },
  { key: "review", label: "レビュー" },
  { key: "approved", label: "承認済み" },
  { key: "archived", label: "アーカイブ" },
];

const THUMBNAIL_WORKSPACE_TABS: { key: ThumbnailWorkspaceTab; label: string; description?: string }[] = [
  { key: "projects", label: "案件", description: "サムネ案の登録・生成・採用" },
  { key: "templates", label: "テンプレ", description: "チャンネルの型（AI生成用）" },
  { key: "library", label: "ライブラリ", description: "参考サムネの登録・紐付け" },
  { key: "channel", label: "チャンネル", description: "KPI / 最新動画プレビュー" },
];

const PROJECT_STATUS_OPTIONS: { value: ThumbnailProjectStatus; label: string }[] = [
  { value: "draft", label: "ドラフト" },
  { value: "in_progress", label: "作業中" },
  { value: "review", label: "レビュー中" },
  { value: "approved", label: "承認済み" },
  { value: "published", label: "公開済み" },
  { value: "archived", label: "アーカイブ" },
];

const PROJECT_STATUS_LABELS: Record<ThumbnailProjectStatus, string> = {
  draft: "ドラフト",
  in_progress: "作業中",
  review: "レビュー中",
  approved: "承認済み",
  published: "公開済み",
  archived: "アーカイブ",
};

const VARIANT_STATUS_OPTIONS: { value: ThumbnailVariantStatus; label: string }[] = [
  { value: "draft", label: "ドラフト" },
  { value: "candidate", label: "候補" },
  { value: "review", label: "レビュー中" },
  { value: "approved", label: "承認済み" },
  { value: "archived", label: "アーカイブ" },
];

const VARIANT_STATUS_LABELS: Record<ThumbnailVariantStatus, string> = VARIANT_STATUS_OPTIONS.reduce(
  (acc, option) => {
    acc[option.value] = option.label;
    return acc;
  },
  {} as Record<ThumbnailVariantStatus, string>
);

const SUPPORTED_THUMBNAIL_EXTENSIONS = /\.(png|jpe?g|webp)$/i;
const THUMBNAIL_ASSET_BASE_PATH = "thumbnails/assets";

const normalizeVideoInput = (value?: string | null): string => {
  if (!value) {
    return "";
  }
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return "";
  }
  return String(parseInt(trimmed, 10));
};

function renderPromptTemplate(template: string, context: Record<string, string>): string {
  let rendered = template ?? "";
  Object.entries(context).forEach(([key, value]) => {
    rendered = rendered.split(`{{${key}}}`).join(value ?? "");
  });
  return rendered;
}

function parsePricingNumber(value?: string | null): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatUsdAmount(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  if (value === 0) {
    return "$0";
  }
  const abs = Math.abs(value);
  if (abs < 1e-6) {
    return `$${value.toExponential(2)}`;
  }
  if (abs < 0.0001) {
    return `$${value.toFixed(8)}`;
  }
  if (abs < 0.01) {
    return `$${value.toFixed(6)}`;
  }
  if (abs < 1) {
    return `$${value.toFixed(3)}`;
  }
  return `$${value.toFixed(2)}`;
}

function formatUsdPerMillionTokens(pricePerToken: number): string {
  if (!Number.isFinite(pricePerToken)) {
    return "—";
  }
  const perMillion = pricePerToken * 1_000_000;
  const decimals = perMillion >= 10 ? 0 : perMillion >= 1 ? 2 : 3;
  return `$${perMillion.toFixed(decimals)}/1Mtok`;
}

function formatDate(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatNumber(value?: number | null): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString("ja-JP");
  }
  return "—";
}

function formatPercent(value?: number | null): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value.toFixed(2)}%`;
  }
  return "—";
}

function formatDuration(seconds?: number | null): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return "—";
  }
  const total = Math.round(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatBytes(value?: number | null): string {
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

function getProjectKey(project: ThumbnailProject): string {
  return `${project.channel}/${project.video}`;
}

function isSupportedThumbnailFile(file: File): boolean {
  if (file.type && file.type.startsWith("image/")) {
    return true;
  }
  return SUPPORTED_THUMBNAIL_EXTENSIONS.test(file.name);
}

export function ThumbnailWorkspace({ compact = false }: { compact?: boolean } = {}) {
  const [overview, setOverview] = useState<ThumbnailOverview | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ThumbnailWorkspaceTab>("projects");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [updatingProjectId, setUpdatingProjectId] = useState<string | null>(null);
  const [variantForm, setVariantForm] = useState<VariantFormState | null>(null);
  const [projectForm, setProjectForm] = useState<ProjectFormState | null>(null);
  const [planningDialog, setPlanningDialog] = useState<PlanningDialogState | null>(null);
  const [cardFeedback, setCardFeedback] = useState<Record<string, CardFeedback>>({});
  const [libraryAssets, setLibraryAssets] = useState<ThumbnailLibraryAsset[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [libraryForms, setLibraryForms] = useState<Record<string, LibraryFormState>>({});
  const libraryUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [libraryUploadStatus, setLibraryUploadStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const [libraryImportUrl, setLibraryImportUrl] = useState("");
  const [libraryImportName, setLibraryImportName] = useState("");
  const [libraryImportStatus, setLibraryImportStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const [libraryDescribeState, setLibraryDescribeState] = useState<
    Record<string, { pending: boolean; text?: string; error?: string | null }>
  >({});
  const feedbackTimers = useRef<Map<string, number>>(new Map());
  const dropzoneFileInputs = useRef(new Map<string, HTMLInputElement>());
  const [activeDropProject, setActiveDropProject] = useState<string | null>(null);
  const libraryRequestRef = useRef(0);
  const [imageModels, setImageModels] = useState<ThumbnailImageModelInfo[]>([]);
  const [imageModelsError, setImageModelsError] = useState<string | null>(null);
  const [channelTemplates, setChannelTemplates] = useState<ThumbnailChannelTemplates | null>(null);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesDirty, setTemplatesDirty] = useState(false);
  const [templatesStatus, setTemplatesStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const [generateDialog, setGenerateDialog] = useState<GenerateDialogState | null>(null);
  const [planningRowsByVideo, setPlanningRowsByVideo] = useState<Record<string, Record<string, string>>>({});
  const [planningLoading, setPlanningLoading] = useState(false);
  const [planningError, setPlanningError] = useState<string | null>(null);

  const handleCopyAssetPath = useCallback((path: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).catch(() => {
        // no-op fallback below
      });
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = path;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);
    }
  }, []);

  const activeChannel: ThumbnailChannelBlock | undefined = useMemo(() => {
    if (!overview || overview.channels.length === 0) {
      return undefined;
    }
    const firstChannel = overview.channels[0];
    if (!selectedChannel) {
      return firstChannel;
    }
    return overview.channels.find((item) => item.channel === selectedChannel) ?? firstChannel;
  }, [overview, selectedChannel]);

  const summary = activeChannel?.summary;
  const activeChannelName = activeChannel?.channel_title ?? activeChannel?.channel ?? null;
  const channelVideos = activeChannel?.videos ?? [];

  const setProjectFeedback = useCallback((projectKey: string, feedback: CardFeedback | null) => {
    setCardFeedback((current) => {
      const next = { ...current };
      if (!feedback) {
        if (feedbackTimers.current.has(projectKey)) {
          window.clearTimeout(feedbackTimers.current.get(projectKey));
          feedbackTimers.current.delete(projectKey);
        }
        delete next[projectKey];
        return next;
      }
      next[projectKey] = feedback;
      if (feedbackTimers.current.has(projectKey)) {
        window.clearTimeout(feedbackTimers.current.get(projectKey));
      }
      const timeoutId = window.setTimeout(() => {
        setCardFeedback((latest) => {
          if (!latest[projectKey]) {
            return latest;
          }
          const copy = { ...latest };
          delete copy[projectKey];
          return copy;
        });
        feedbackTimers.current.delete(projectKey);
      }, feedback.type === "success" ? 2800 : 4800);
      feedbackTimers.current.set(projectKey, timeoutId);
      return next;
    });
  }, []);

  useEffect(() => {
    const timers = feedbackTimers.current;
    return () => {
      timers.forEach((timerId) => window.clearTimeout(timerId));
      timers.clear();
    };
  }, []);

  const fetchData = useCallback(
    async (options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      if (!silent) {
        setLoading(true);
        setErrorMessage(null);
      }
      try {
        const data = await fetchThumbnailOverview();
        setOverview(data);
        setSelectedChannel((prev) => {
          if (!data.channels.length) {
            return null;
          }
          if (prev && data.channels.some((channel) => channel.channel === prev)) {
            return prev;
          }
          return data.channels[0].channel;
        });
        return data;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent) {
          setErrorMessage(message);
        }
        throw error;
      } finally {
        if (!silent) {
          setLoading(false);
        }
      }
    },
    []
  );

  const loadLibrary = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      const requestId = Date.now();
      libraryRequestRef.current = requestId;
      if (!silent) {
        setLibraryLoading(true);
        setLibraryError(null);
      }
      try {
        const assets = await fetchThumbnailLibrary(channelCode);
        if (libraryRequestRef.current !== requestId) {
          return assets;
        }
        setLibraryAssets(assets);
        setLibraryForms((current) => {
          const next: Record<string, LibraryFormState> = {};
          assets.forEach((asset) => {
            const existing = current[asset.id];
            next[asset.id] = {
              video: existing?.video ?? "",
              pending: false,
            };
          });
          return next;
        });
        setLibraryDescribeState((current) => {
          const next: Record<string, { pending: boolean; text?: string; error?: string | null }> = {};
          assets.forEach((asset) => {
            next[asset.id] = current[asset.id] ?? { pending: false };
          });
          return next;
        });
        return assets;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent && libraryRequestRef.current === requestId) {
          setLibraryError(message);
        }
        throw error;
      } finally {
        if (!silent && libraryRequestRef.current === requestId) {
          setLibraryLoading(false);
        }
      }
    },
    []
  );

  const loadTemplates = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      if (!silent) {
        setTemplatesLoading(true);
        setTemplatesStatus({ pending: false, error: null, success: null });
      }
      try {
        const templates = await fetchThumbnailTemplates(channelCode);
        setChannelTemplates(templates);
        setTemplatesDirty(false);
        return templates;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent) {
          setTemplatesStatus({ pending: false, error: message, success: null });
        }
        throw error;
      } finally {
        if (!silent) {
          setTemplatesLoading(false);
        }
      }
    },
    []
  );

  useEffect(() => {
    if (!activeChannel?.channel) {
      setLibraryAssets([]);
      setLibraryForms({});
      setLibraryError(null);
      setLibraryLoading(false);
      return;
    }
    loadLibrary(activeChannel.channel).catch(() => {
      // loadLibrary 内でエラー表示済み
    });
  }, [activeChannel?.channel, loadLibrary]);

  useEffect(() => {
    fetchData().catch(() => {
      // エラーは fetchData 内で処理済み
    });
  }, [fetchData]);

  useEffect(() => {
    fetchThumbnailImageModels()
      .then((models) => {
        setImageModels(models);
        setImageModelsError(null);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        setImageModelsError(message);
      });
  }, []);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      setChannelTemplates(null);
      setTemplatesDirty(false);
      setTemplatesLoading(false);
      setTemplatesStatus({ pending: false, error: null, success: null });
      return;
    }
    loadTemplates(channelCode).catch(() => {
      // loadTemplates 内でエラー表示済み
    });
  }, [activeChannel?.channel, loadTemplates]);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      setPlanningRowsByVideo({});
      setPlanningLoading(false);
      setPlanningError(null);
      return;
    }
    setPlanningLoading(true);
    setPlanningError(null);
    fetchProgressCsv(channelCode)
      .then((result) => {
        const map: Record<string, Record<string, string>> = {};
        (result.rows ?? []).forEach((row) => {
          const rawVideo = row["動画番号"] ?? row["VideoNumber"] ?? "";
          const normalizedVideo = normalizeVideoInput(rawVideo);
          if (!normalizedVideo) {
            return;
          }
          map[normalizedVideo] = row;
        });
        setPlanningRowsByVideo(map);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        setPlanningError(message);
        setPlanningRowsByVideo({});
      })
      .finally(() => {
        setPlanningLoading(false);
      });
  }, [activeChannel?.channel]);

  const handleLibraryVideoChange = useCallback((assetId: string, value: string) => {
    setLibraryForms((current) => {
      const existing = current[assetId] ?? { video: "", pending: false };
      return {
        ...current,
        [assetId]: {
          ...existing,
          video: value,
          error: undefined,
          success: undefined,
        },
      };
    });
  }, []);

  const handleLibraryUploadClick = useCallback(() => {
    libraryUploadInputRef.current?.click();
  }, []);

  const handleLibraryUploadChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const { files } = event.target;
      if (!files || files.length === 0) {
        return;
      }
      const fileArray = Array.from(files);
      setLibraryUploadStatus({ pending: true, error: null, success: null });
      try {
        await uploadThumbnailLibraryAssets(channelCode, fileArray);
        setLibraryUploadStatus({
          pending: false,
          error: null,
          success: `${fileArray.length} 件の画像を追加しました。`,
        });
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // handled inside loadLibrary
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryUploadStatus({ pending: false, error: message, success: null });
      } finally {
        event.target.value = "";
      }
    },
    [activeChannel, loadLibrary]
  );

  const handleLibraryImportSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const url = libraryImportUrl.trim();
      if (!url) {
        setLibraryImportStatus({ pending: false, error: "URLを入力してください。", success: null });
        return;
      }
      setLibraryImportStatus({ pending: true, error: null, success: null });
      try {
        await importThumbnailLibraryAsset(channelCode, {
          url,
          fileName: libraryImportName.trim() || undefined,
        });
        setLibraryImportStatus({ pending: false, error: null, success: "ライブラリに追加しました。" });
        setLibraryImportUrl("");
        setLibraryImportName("");
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // handled inside loadLibrary
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryImportStatus({ pending: false, error: message, success: null });
      }
    },
    [activeChannel, libraryImportName, libraryImportUrl, loadLibrary]
  );

  const handleLibraryDescribe = useCallback(
    async (asset: ThumbnailLibraryAsset) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      setLibraryDescribeState((current) => ({
        ...current,
        [asset.id]: { pending: true, text: current[asset.id]?.text },
      }));
      try {
        const description = await describeThumbnailLibraryAsset(channelCode, asset.relative_path);
        setLibraryDescribeState((current) => ({
          ...current,
          [asset.id]: { pending: false, text: description.description, error: null },
        }));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryDescribeState((current) => ({
          ...current,
          [asset.id]: { pending: false, text: current[asset.id]?.text, error: message },
        }));
      }
    },
    [activeChannel]
  );

  const handleLibraryAssignSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, asset: ThumbnailLibraryAsset) => {
      event.preventDefault();
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const formState = libraryForms[asset.id] ?? { video: "", pending: false };
      const normalizedVideo = normalizeVideoInput(formState.video);
      if (!normalizedVideo) {
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { ...formState, error: "動画番号を入力してください。", success: undefined, pending: false },
        }));
        return;
      }
      setLibraryForms((current) => ({
        ...current,
        [asset.id]: { ...formState, pending: true, error: undefined, success: undefined },
      }));
      try {
        await assignThumbnailLibraryAsset(channelCode, asset.relative_path, {
            video: normalizedVideo,
            label: asset.file_name.replace(/\.[^.]+$/, ""),
            make_selected: true,
        });
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { video: "", pending: false, error: undefined, success: `動画${normalizedVideo}へ紐付け完了` },
        }));
        await fetchData({ silent: true });
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // silent refresh
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { ...current[asset.id], pending: false, error: message, success: undefined } as LibraryFormState,
        }));
      }
    },
    [activeChannel, fetchData, libraryForms, loadLibrary]
  );

  const handleLibraryRefresh = useCallback(() => {
    if (!activeChannel?.channel) {
      return;
    }
    loadLibrary(activeChannel.channel).catch(() => {
      // loadLibrary 内でエラー表示済み
    });
  }, [activeChannel, loadLibrary]);

  const handleTemplatesRefresh = useCallback(() => {
    if (!activeChannel?.channel) {
      return;
    }
    loadTemplates(activeChannel.channel).catch(() => {
      // loadTemplates 内でエラー表示済み
    });
  }, [activeChannel, loadTemplates]);

  const handleAddTemplate = useCallback(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    const defaultModelKey = imageModels[0]?.key ?? "";
    const now = Date.now();
    const newTemplate = {
      id: `tmpl_ui_${now.toString(16)}`,
      name: "新規テンプレ",
      image_model_key: defaultModelKey,
      prompt_template:
        "YouTubeサムネ(16:9)を生成してください。テーマ: {{title}}\n"
        + "文字要素(あれば): {{thumbnail_upper}} / {{thumbnail_lower}}\n"
        + "構図: 強いコントラスト、視認性優先、人物 or シンボルを大きく。\n"
        + "出力: サムネとして使える鮮明な画像。",
      negative_prompt: "",
      notes: "",
      created_at: null,
      updated_at: null,
    };
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      return { ...base, templates: [...(base.templates ?? []), newTemplate] };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel, imageModels]);

  const handleDeleteTemplate = useCallback((templateId: string) => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      const nextTemplates = (base.templates ?? []).filter((tpl) => tpl.id !== templateId);
      const nextDefault =
        base.default_template_id && base.default_template_id === templateId ? null : base.default_template_id ?? null;
      return {
        ...base,
        templates: nextTemplates,
        default_template_id: nextDefault,
      };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel]);

  const handleTemplateFieldChange = useCallback(
    (
      templateId: string,
      field: "name" | "image_model_key" | "prompt_template" | "negative_prompt" | "notes",
      value: string
    ) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      setChannelTemplates((current) => {
        const base: ThumbnailChannelTemplates =
          current && current.channel === channelCode
            ? current
            : { channel: channelCode, default_template_id: null, templates: [] };
        const nextTemplates = (base.templates ?? []).map((tpl) => {
          if (tpl.id !== templateId) {
            return tpl;
          }
          return { ...tpl, [field]: value };
        });
        return { ...base, templates: nextTemplates };
      });
      setTemplatesDirty(true);
      setTemplatesStatus({ pending: false, error: null, success: null });
    },
    [activeChannel]
  );

  const handleTemplateDefaultChange = useCallback((templateId: string | null) => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      return { ...base, default_template_id: templateId };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel]);

  const handleSaveTemplates = useCallback(async () => {
    const channelCode = activeChannel?.channel;
    if (!channelCode || !channelTemplates || channelTemplates.channel !== channelCode) {
      return;
    }
    const templates = channelTemplates.templates ?? [];
    for (const tpl of templates) {
      if (!tpl.name?.trim()) {
        setTemplatesStatus({ pending: false, error: "テンプレ名が空です。", success: null });
        return;
      }
      if (!tpl.image_model_key?.trim()) {
        setTemplatesStatus({ pending: false, error: "画像モデルキーが未選択のテンプレがあります。", success: null });
        return;
      }
      if (!tpl.prompt_template?.trim()) {
        setTemplatesStatus({ pending: false, error: "プロンプトテンプレが空のテンプレがあります。", success: null });
        return;
      }
    }
    const defaultTemplateId = channelTemplates.default_template_id ?? null;
    if (defaultTemplateId && !templates.some((tpl) => tpl.id === defaultTemplateId)) {
      setTemplatesStatus({ pending: false, error: "デフォルトテンプレが templates に含まれていません。", success: null });
      return;
    }

    setTemplatesStatus({ pending: true, error: null, success: null });
    try {
      const updated = await updateThumbnailTemplates(channelCode, {
        default_template_id: defaultTemplateId,
        templates: templates.map((tpl) => ({
          id: tpl.id,
          name: tpl.name,
          image_model_key: tpl.image_model_key,
          prompt_template: tpl.prompt_template,
          negative_prompt: tpl.negative_prompt?.trim() ? tpl.negative_prompt : null,
          notes: tpl.notes?.trim() ? tpl.notes : null,
        })),
      });
      setChannelTemplates(updated);
      setTemplatesDirty(false);
      setTemplatesStatus({ pending: false, error: null, success: "テンプレを保存しました。" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplatesStatus({ pending: false, error: message, success: null });
    }
  }, [activeChannel, channelTemplates]);

  const handleOpenGenerateDialog = useCallback(
    (project: ThumbnailProject) => {
      const channelCode = project.channel;
      const normalizedVideo = normalizeVideoInput(project.video);
      const planningRow = normalizedVideo ? planningRowsByVideo[normalizedVideo] : undefined;
      const defaultUpper = planningRow?.["サムネタイトル上"] ?? "";
      const defaultTitle = planningRow?.["サムネタイトル"] ?? "";
      const defaultLower = planningRow?.["サムネタイトル下"] ?? "";
      const defaultSourceTitle = planningRow?.["タイトル"] ?? project.title ?? project.sheet_title ?? "";
      const defaultThumbnailPrompt =
        planningRow?.["サムネ画像プロンプト（URL・テキスト指示込み）"] ?? planningRow?.["サムネ画像プロンプト"] ?? "";

      const defaultTemplateId =
        (channelTemplates?.channel === channelCode ? channelTemplates.default_template_id : null)
          ?? (channelTemplates?.channel === channelCode ? channelTemplates.templates?.[0]?.id : null)
          ?? "";
      const selectedTemplate =
        channelTemplates?.channel === channelCode
          ? channelTemplates.templates.find((tpl) => tpl.id === defaultTemplateId)
          : undefined;
      const defaultModelKey = selectedTemplate?.image_model_key ?? imageModels[0]?.key ?? "";
      setGenerateDialog({
        projectKey: getProjectKey(project),
        channel: project.channel,
        video: project.video,
        templateId: defaultTemplateId,
        prompt: "",
        sourceTitle: defaultSourceTitle,
        thumbnailPrompt: defaultThumbnailPrompt,
        imageModelKey: defaultModelKey,
        count: 1,
        label: "",
        copyUpper: defaultUpper,
        copyTitle: defaultTitle,
        copyLower: defaultLower,
        saveToPlanning: false,
        status: "draft",
        makeSelected: project.variants.length === 0,
        tags: (project.tags ?? []).join(", "),
        notes: "",
        saving: false,
        error: undefined,
      });
    },
    [channelTemplates, imageModels, planningRowsByVideo]
  );

  const handleCloseGenerateDialog = useCallback(() => {
    setGenerateDialog(null);
  }, []);

  const handleGenerateDialogFieldChange = useCallback(
    (
      field: keyof Omit<GenerateDialogState, "projectKey" | "channel" | "video" | "saving" | "error">,
      value: string | number | boolean
    ) => {
      setGenerateDialog((current) => {
        if (!current) {
          return current;
        }
        return { ...current, [field]: value };
      });
    },
    []
  );

  const handleGenerateDialogSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (!generateDialog) {
        return;
      }
      const trimmedPrompt = generateDialog.prompt.trim();
      const templateId = generateDialog.templateId.trim();
      const modelKey = generateDialog.imageModelKey.trim();
      const selectedTemplate =
        templateId && channelTemplates?.channel === generateDialog.channel
          ? channelTemplates.templates.find((tpl) => tpl.id === templateId)
          : undefined;
      const resolvedModelKey = modelKey || selectedTemplate?.image_model_key?.trim() || "";
      if (!templateId && !trimmedPrompt) {
        setGenerateDialog((current) => (current ? { ...current, error: "テンプレまたはプロンプトを指定してください。" } : current));
        return;
      }
      if (!templateId && !resolvedModelKey) {
        setGenerateDialog((current) => (current ? { ...current, error: "テンプレなしの場合は画像モデルを選択してください。" } : current));
        return;
      }

      setGenerateDialog((current) => (current ? { ...current, saving: true, error: undefined } : current));

      const tags = generateDialog.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);

      try {
        if (generateDialog.saveToPlanning) {
          const normalizeField = (value: string): string | null => {
            const trimmed = value.trim();
            return trimmed ? trimmed : null;
          };
          await updatePlanning(generateDialog.channel, generateDialog.video, {
            fields: {
              thumbnail_upper: normalizeField(generateDialog.copyUpper),
              thumbnail_title: normalizeField(generateDialog.copyTitle),
              thumbnail_lower: normalizeField(generateDialog.copyLower),
              thumbnail_prompt: normalizeField(generateDialog.thumbnailPrompt),
            },
          });
          const normalizedVideo = normalizeVideoInput(generateDialog.video);
          if (normalizedVideo) {
            setPlanningRowsByVideo((current) => {
              const existing = current[normalizedVideo] ?? {};
              return {
                ...current,
                [normalizedVideo]: {
                  ...existing,
                  サムネタイトル上: generateDialog.copyUpper,
                  サムネタイトル: generateDialog.copyTitle,
                  サムネタイトル下: generateDialog.copyLower,
                  "サムネ画像プロンプト（URL・テキスト指示込み）": generateDialog.thumbnailPrompt,
                },
              };
            });
          }
        }

        let finalPrompt = trimmedPrompt;
        if (!finalPrompt) {
          if (!selectedTemplate) {
            throw new Error("テンプレが見つかりませんでした。テンプレを再読み込みしてください。");
          }
          const ctx: Record<string, string> = {
            channel: generateDialog.channel,
            video: normalizeVideoInput(generateDialog.video) || generateDialog.video,
            title: generateDialog.sourceTitle,
            thumbnail_upper: generateDialog.copyUpper,
            thumbnail_title: generateDialog.copyTitle,
            thumbnail_lower: generateDialog.copyLower,
            thumbnail_prompt: generateDialog.thumbnailPrompt,
          };
          finalPrompt = renderPromptTemplate(selectedTemplate.prompt_template, ctx).trim();
          const negative = selectedTemplate.negative_prompt?.trim();
          if (negative) {
            finalPrompt = `${finalPrompt}\n\n【避けるべき要素】\n${negative}`.trim();
          }
        }
        if (!finalPrompt) {
          throw new Error("プロンプトが空です。");
        }

        const payload = {
          template_id: templateId || undefined,
          image_model_key: resolvedModelKey || undefined,
          prompt: finalPrompt,
          count: generateDialog.count,
          label: generateDialog.label.trim() || undefined,
          status: generateDialog.status,
          make_selected: generateDialog.makeSelected,
          notes: generateDialog.notes.trim() || undefined,
          tags: tags.length ? tags : undefined,
        };
        await generateThumbnailVariants(generateDialog.channel, generateDialog.video, payload);
        setProjectFeedback(generateDialog.projectKey, {
          type: "success",
          message: `AI生成が完了しました（${generateDialog.count}件）。`,
          timestamp: Date.now(),
        });
        setGenerateDialog(null);
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setGenerateDialog((current) => (current ? { ...current, saving: false, error: message } : current));
      }
    },
    [channelTemplates, fetchData, generateDialog, setProjectFeedback]
  );

  const filteredProjects: ThumbnailProject[] = useMemo(() => {
    if (!activeChannel) {
      return [];
    }
        const projects = [...activeChannel.projects];
        projects.sort((a, b) => {
          const keyA = a.selected_variant_id ? 0 : 1;
          const keyB = b.selected_variant_id ? 0 : 1;
          if (keyA !== keyB) {
            return keyA - keyB;
          }
          return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
        });
        let result = projects;
    if (statusFilter !== "all") {
      result = result.filter((project) => {
        if (statusFilter === "approved") {
          return project.status === "approved" || project.status === "published";
        }
        return project.status === statusFilter;
      });
    }
    const query = searchTerm.trim().toLowerCase();
    if (!query) {
      return result;
    }
    return result.filter((project) => {
      const projectFields = [
        project.title,
        project.sheet_title,
        project.video,
        project.owner,
        ...(project.tags ?? []),
      ]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase());
      if (projectFields.some((value) => value.includes(query))) {
        return true;
      }
      return project.variants.some((variant) => {
        const label = (variant.label ?? variant.id).toLowerCase();
        if (label.includes(query)) {
          return true;
        }
        if (variant.tags && variant.tags.some((tag) => tag.toLowerCase().includes(query))) {
          return true;
        }
        return Boolean(variant.notes && variant.notes.toLowerCase().includes(query));
      });
    });
  }, [activeChannel, searchTerm, statusFilter]);

  const statusCounters = useMemo<Record<StatusFilter, number>>(() => {
    const counters: Record<StatusFilter, number> = {
      all: 0,
      draft: 0,
      in_progress: 0,
      review: 0,
      approved: 0,
      archived: 0,
    };
    if (!activeChannel) {
      return counters;
    }
    counters.all = activeChannel.projects.length;
    for (const project of activeChannel.projects) {
      switch (project.status) {
        case "draft":
        case "in_progress":
        case "review":
        case "archived":
          counters[project.status] += 1;
          break;
        case "approved":
        case "published":
          counters.approved += 1;
          break;
        default:
          break;
      }
    }
    return counters;
  }, [activeChannel]);

  const handleRefresh = useCallback(() => {
    fetchData().catch(() => {
      // fetchData 内で記録済み
    });
  }, [fetchData]);

  const handleApplyVideoThumbnail = useCallback((video: ThumbnailChannelVideo) => {
    setVariantForm((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        label: current.label || video.title,
        imageUrl: video.thumbnail_url ?? current.imageUrl,
        prompt: current.prompt || video.title,
      };
    });
  }, []);

  const handleOpenVariantForm = useCallback((project: ThumbnailProject) => {
    const projectKey = getProjectKey(project);
    const defaultPath = `assets/${project.channel}/${project.video}/`;
    setVariantForm({
      projectKey,
      label: "",
      status: "draft",
      imageUrl: "",
      imagePath: defaultPath,
      notes: "",
      tags: "",
      prompt: "",
      makeSelected: project.variants.length === 0,
      showAdvanced: false,
    });
    setProjectForm((current) => (current?.projectKey === projectKey ? current : null));
    setProjectFeedback(projectKey, null);
  }, [setProjectFeedback]);

  const handleStartNewVariant = useCallback(() => {
    if (!filteredProjects.length) {
      return;
    }
    handleOpenVariantForm(filteredProjects[0]);
  }, [filteredProjects, handleOpenVariantForm]);

  const handleCancelVariantForm = useCallback(() => {
    setVariantForm(null);
  }, []);

  const handleVariantFormFieldChange = useCallback(
    (field: keyof Omit<VariantFormState, "projectKey">, value: string | boolean) => {
      setVariantForm((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          [field]: value,
        };
      });
    },
    []
  );

  const handleVariantFormSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, project: ThumbnailProject) => {
      event.preventDefault();
      const projectKey = getProjectKey(project);
      if (!variantForm || variantForm.projectKey !== projectKey) {
        return;
      }
      const label = variantForm.label.trim();
      const imageUrl = variantForm.imageUrl.trim();
      const imagePath = variantForm.imagePath.trim();
      if (!label) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "サムネイル案の名前を入力してください。",
          timestamp: Date.now(),
        });
        return;
      }
      if (!imageUrl && !imagePath) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "画像URLまたは画像パスのいずれかを入力してください。",
          timestamp: Date.now(),
        });
        return;
      }
      if (imagePath && /\/+$/.test(imagePath)) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "画像パスにはファイル名まで指定してください。",
          timestamp: Date.now(),
        });
        return;
      }
      const tags = variantForm.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await createThumbnailVariant(project.channel, project.video, {
          label,
          image_url: imageUrl || undefined,
          image_path: imagePath || undefined,
          status: variantForm.status,
          notes: variantForm.notes.trim() || undefined,
          tags,
          prompt: variantForm.prompt.trim() || undefined,
          make_selected: variantForm.makeSelected,
        });
        setVariantForm(null);
        setProjectFeedback(projectKey, {
          type: "success",
          message: "サムネイル案を登録しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback, variantForm]
  );

  const handleOpenProjectForm = useCallback((project: ThumbnailProject) => {
    const projectKey = getProjectKey(project);
    setProjectForm({
      projectKey,
      owner: project.owner ?? "",
      summary: project.summary ?? "",
      notes: project.notes ?? "",
      tags: (project.tags ?? []).join(", "),
      dueAt: project.due_at ?? "",
    });
    setVariantForm((current) => (current?.projectKey === projectKey ? current : null));
    setProjectFeedback(projectKey, null);
  }, [setProjectFeedback]);

  const handleCancelProjectForm = useCallback(() => {
    setProjectForm(null);
  }, []);

  const handleOpenPlanningDialog = useCallback((project: ThumbnailProject, variant?: ThumbnailVariant) => {
    const projectKey = getProjectKey(project);
    const numericVideo = normalizeVideoInput(project.video);
    const variantTags = variant?.tags ?? [];
    const primaryTitle = project.title ?? project.sheet_title ?? "";
    const variantLabel = variant?.label ?? variant?.id ?? "";
    setPlanningDialog({
      projectKey,
      channel: project.channel,
      projectTitle: primaryTitle,
      variantLabel: variantLabel || undefined,
      videoNumber: numericVideo,
      no: numericVideo,
      title: variantLabel || primaryTitle || "新規企画",
      thumbnailUpper: "",
      thumbnailLower: "",
      thumbnailTitle: variantLabel || "",
      thumbnailPrompt: variant?.notes ?? project.summary ?? "",
      dallePrompt: "",
      conceptIntent: project.summary ?? "",
      outlineNotes: variant?.notes ?? project.notes ?? "",
      primaryTag: variantTags[0] ?? "",
      secondaryTag: variantTags[1] ?? "",
      lifeScene: "",
      keyConcept: "",
      benefit: "",
      analogy: "",
      descriptionLead: project.summary ?? "",
      descriptionTakeaways: "",
      saving: false,
      error: undefined,
    });
  }, []);

  const handleClosePlanningDialog = useCallback(() => {
    setPlanningDialog(null);
  }, []);

  const handlePlanningFieldChange = useCallback((field: PlanningEditableField, value: string) => {
    setPlanningDialog((current) => (current ? { ...current, [field]: value } : current));
  }, []);

  const handlePlanningSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (!planningDialog) {
        return;
      }
      const trimmedVideo = planningDialog.videoNumber.trim();
      if (!trimmedVideo) {
        setPlanningDialog((current) => (current ? { ...current, error: "動画番号を入力してください。" } : current));
        return;
      }
      setPlanningDialog((current) => (current ? { ...current, saving: true, error: undefined } : current));

      const toFieldValue = (value: string): string | null => {
        const trimmed = value.trim();
        return trimmed ? trimmed : null;
      };

      const fieldsPayload: Record<string, string | null> = {
        thumbnail_upper: toFieldValue(planningDialog.thumbnailUpper),
        thumbnail_lower: toFieldValue(planningDialog.thumbnailLower),
        thumbnail_title: toFieldValue(planningDialog.thumbnailTitle),
        thumbnail_prompt: toFieldValue(planningDialog.thumbnailPrompt),
        dalle_prompt: toFieldValue(planningDialog.dallePrompt),
        concept_intent: toFieldValue(planningDialog.conceptIntent),
        outline_notes: toFieldValue(planningDialog.outlineNotes),
        primary_pain_tag: toFieldValue(planningDialog.primaryTag),
        secondary_pain_tag: toFieldValue(planningDialog.secondaryTag),
        life_scene: toFieldValue(planningDialog.lifeScene),
        key_concept: toFieldValue(planningDialog.keyConcept),
        benefit_blurb: toFieldValue(planningDialog.benefit),
        analogy_image: toFieldValue(planningDialog.analogy),
        description_lead: toFieldValue(planningDialog.descriptionLead),
        description_takeaways: toFieldValue(planningDialog.descriptionTakeaways),
      };

      const filteredFields: Record<string, string | null> = {};
      Object.entries(fieldsPayload).forEach(([key, val]) => {
        if (val !== null) {
          filteredFields[key] = val;
        }
      });

      const payload: PlanningCreatePayload = {
        channel: planningDialog.channel,
        video_number: trimmedVideo,
        title: planningDialog.title.trim(),
        no: planningDialog.no.trim() || undefined,
        creation_flag: "3",
        progress: "topic_research: pending",
        fields: Object.keys(filteredFields).length > 0 ? filteredFields : undefined,
      };

      try {
        const result = await createPlanningRow(payload);
        const scriptFactoryUrl = `/projects?channel=${encodeURIComponent(result.channel ?? planningDialog.channel)}&video=${encodeURIComponent(result.video_number)}`;
        setProjectFeedback(planningDialog.projectKey, {
          type: "success",
          message: (
            <>
              {`${result.channel}-${result.video_number} の企画行を作成しました。`}
              <Link to={scriptFactoryUrl} className="thumbnail-card__feedback-link">
                ScriptFactoryで確認
              </Link>
            </>
          ),
          timestamp: Date.now(),
        });
        setPlanningDialog(null);
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setPlanningDialog((current) => (current ? { ...current, saving: false, error: message } : current));
      }
    },
    [fetchData, planningDialog, setProjectFeedback]
  );
  const handleProjectFormChange = useCallback(
    (field: keyof Omit<ProjectFormState, "projectKey">, value: string) => {
      setProjectForm((current) => {
        if (!current) {
          return current;
        }
        return { ...current, [field]: value };
      });
    },
    []
  );

  const handleProjectFormSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, project: ThumbnailProject) => {
      event.preventDefault();
      const projectKey = getProjectKey(project);
      if (!projectForm || projectForm.projectKey !== projectKey) {
        return;
      }
      const tags = projectForm.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);

      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, {
          owner: projectForm.owner.trim() || null,
          summary: projectForm.summary.trim() || null,
          notes: projectForm.notes.trim() || null,
          tags,
          due_at: projectForm.dueAt.trim() || null,
        });
        setProjectForm(null);
        setProjectFeedback(projectKey, {
          type: "success",
          message: "案件情報を更新しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, projectForm, setProjectFeedback]
  );

  const handleStatusChange = useCallback(
    async (project: ThumbnailProject, status: ThumbnailProjectStatus) => {
      const projectKey = getProjectKey(project);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, { status });
        setProjectFeedback(projectKey, {
          type: "success",
          message: "ステータスを更新しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleSelectVariant = useCallback(
    async (project: ThumbnailProject, variant: ThumbnailVariant) => {
      const projectKey = getProjectKey(project);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, {
          selected_variant_id: variant.id,
        });
        setProjectFeedback(projectKey, {
          type: "success",
          message: `「${variant.label ?? variant.id}」を採用中に設定しました。`,
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleDropzoneFiles = useCallback(
    async (project: ThumbnailProject, fileList: FileList | File[]) => {
      const projectKey = getProjectKey(project);
      const rawFiles = Array.isArray(fileList) ? fileList : Array.from(fileList);
      const validFiles = rawFiles.filter(isSupportedThumbnailFile);
      if (validFiles.length === 0) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "PNG / JPG / WEBP の画像をアップロードしてください。",
          timestamp: Date.now(),
        });
        return;
      }
      setProjectFeedback(projectKey, null);
      setActiveDropProject((current) => (current === projectKey ? null : current));
      setVariantForm((current) => (current?.projectKey === projectKey ? null : current));
      setUpdatingProjectId(projectKey);
      try {
        let uploaded = 0;
        for (const file of validFiles) {
          const baseName = file.name.replace(/\.[^.]+$/, "");
          const labelCandidate = baseName.replace(/[_-]+/g, " ").trim() || `案 ${uploaded + 1}`;
          await uploadThumbnailVariantAsset(project.channel, project.video, {
            file,
            label: labelCandidate.slice(0, 120),
            makeSelected: project.variants.length === 0 && uploaded === 0,
          });
          uploaded += 1;
        }
        setProjectFeedback(projectKey, {
          type: "success",
          message: `${uploaded} 件のサムネイル案を追加しました。`,
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleDropzoneInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>, project: ThumbnailProject) => {
      const { files } = event.target;
      if (!files || files.length === 0) {
        return;
      }
      const projectKey = getProjectKey(project);
      handleDropzoneFiles(project, files).finally(() => {
        const input = dropzoneFileInputs.current.get(projectKey);
        if (input) {
          input.value = "";
        }
      });
    },
    [handleDropzoneFiles]
  );

  const handleDropzoneClick = useCallback((projectKey: string, disabled: boolean) => {
    if (disabled) {
      return;
    }
    const input = dropzoneFileInputs.current.get(projectKey);
    if (input) {
      input.click();
    }
  }, []);

  const handleDropzoneDragEnter = useCallback(
    (event: DragEvent<HTMLElement>, projectKey: string, disabled: boolean) => {
      event.preventDefault();
      if (disabled) {
        event.dataTransfer.dropEffect = "none";
        return;
      }
      event.dataTransfer.dropEffect = "copy";
      setActiveDropProject(projectKey);
    },
    []
  );

  const handleDropzoneDragOver = useCallback((event: DragEvent<HTMLElement>, disabled: boolean) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = disabled ? "none" : "copy";
  }, []);

  const handleDropzoneDragLeave = useCallback(
    (event: DragEvent<HTMLElement>, projectKey: string) => {
      event.preventDefault();
      const related = event.relatedTarget as Node | null;
      if (related && event.currentTarget.contains(related)) {
        return;
      }
      setActiveDropProject((current) => (current === projectKey ? null : current));
    },
    []
  );

  const handleDropzoneDrop = useCallback(
    (event: DragEvent<HTMLElement>, project: ThumbnailProject, disabled: boolean) => {
      event.preventDefault();
      if (disabled) {
        return;
      }
      setActiveDropProject(null);
      if (event.dataTransfer?.files && event.dataTransfer.files.length > 0) {
        void handleDropzoneFiles(project, event.dataTransfer.files);
      }
    },
    [handleDropzoneFiles]
  );


  const libraryPanel = activeChannel ? (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>参考サムネ登録</h3>
          <p>
            {activeChannel.library_path
              ? `${activeChannel.library_path} 配下の PNG / JPG / WEBP が一覧に並びます。`
              : "PNG / JPG / WEBP をドラッグ & ドロップ / URL で追加できます。"}
          </p>
        </div>
        <button type="button" className="thumbnail-refresh-button" onClick={handleLibraryRefresh} disabled={libraryLoading}>
          {libraryLoading ? "読込中…" : "ライブラリ再読み込み"}
        </button>
      </div>
      <div className="thumbnail-library-panel__cards">
        <div className="thumbnail-library-panel__card">
          <h4>ローカルから追加</h4>
          <p>PNG / JPG / WEBP をまとめてドラッグ & ドロップできます。</p>
          <button
            type="button"
            className="thumbnail-upload-button"
            onClick={handleLibraryUploadClick}
            disabled={!activeChannel.channel || libraryUploadStatus.pending}
          >
            {libraryUploadStatus.pending ? "アップロード中…" : "ローカル画像を選ぶ"}
          </button>
          <input
            ref={libraryUploadInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            hidden
            onChange={handleLibraryUploadChange}
          />
        </div>
        <div className="thumbnail-library-panel__card">
          <h4>URLを取り込む</h4>
          <form className="thumbnail-library__import" onSubmit={handleLibraryImportSubmit}>
            <label>
              <span>画像URL</span>
              <input
                type="url"
                placeholder="https://example.com/thumbnail.jpg"
                value={libraryImportUrl}
                onChange={(event) => {
                  setLibraryImportUrl(event.target.value);
                  setLibraryImportStatus((current) => ({ ...current, error: null, success: null }));
                }}
                required
              />
            </label>
            <label>
              <span>保存名 (任意)</span>
              <input
                type="text"
                placeholder="my-thumbnail.png"
                value={libraryImportName}
                onChange={(event) => {
                  setLibraryImportName(event.target.value);
                  setLibraryImportStatus((current) => ({ ...current, error: null, success: null }));
                }}
              />
            </label>
            <button type="submit" disabled={libraryImportStatus.pending}>
              {libraryImportStatus.pending ? "取り込み中…" : "ライブラリへ追加"}
            </button>
          </form>
        </div>
      </div>
      {libraryError ? <p className="thumbnail-library__alert">{libraryError}</p> : null}
      {libraryUploadStatus.error ? <p className="thumbnail-library__alert">{libraryUploadStatus.error}</p> : null}
      {libraryUploadStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{libraryUploadStatus.success}</p>
      ) : null}
      {libraryImportStatus.error ? <p className="thumbnail-library__alert">{libraryImportStatus.error}</p> : null}
      {libraryImportStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{libraryImportStatus.success}</p>
      ) : null}
      {libraryAssets.length === 0 && !libraryError ? (
        <p className="thumbnail-library__placeholder">
          {libraryLoading ? "画像を読み込んでいます…" : "画像ファイルを追加するとここにサムネイルが並びます。"}
        </p>
      ) : null}
      {libraryAssets.length > 0 ? (
        <div className="thumbnail-library-grid">
          {libraryAssets.map((asset) => {
            const previewUrl = resolveApiUrl(asset.public_url);
            const formState = libraryForms[asset.id] ?? { video: "", pending: false };
            const describeState = libraryDescribeState[asset.id];
            return (
              <article key={asset.id} className="thumbnail-library-card">
                <div className="thumbnail-library-card__preview">
                  <img src={previewUrl} alt={asset.file_name} loading="lazy" />
                </div>
                <div className="thumbnail-library-card__meta">
                  <strong title={asset.file_name}>{asset.file_name}</strong>
                  <div className="thumbnail-library-card__meta-info">{asset.relative_path}</div>
                  <div className="thumbnail-library-card__meta-info">
                    {formatBytes(asset.size_bytes)}・{formatDate(asset.updated_at)}
                  </div>
                  <form
                    className="thumbnail-library-card__assign"
                    onSubmit={(event) => handleLibraryAssignSubmit(event, asset)}
                  >
                    <label>
                      <span>紐付け先の動画番号</span>
                      <input
                        type="text"
                        inputMode="numeric"
                        value={formState.video}
                        onChange={(event) => handleLibraryVideoChange(asset.id, event.target.value)}
                        placeholder="例: 191"
                        disabled={formState.pending}
                      />
                    </label>
                    <button type="submit" disabled={formState.pending || !formState.video.trim()}>
                      {formState.pending ? "紐付け中…" : "企画に紐付け"}
                    </button>
                    {formState.error ? (
                      <p className="thumbnail-library__message thumbnail-library__message--error">{formState.error}</p>
                    ) : null}
                    {formState.success ? (
                      <p className="thumbnail-library__message thumbnail-library__message--success">{formState.success}</p>
                    ) : null}
                  </form>
                  <div className="thumbnail-library-card__describe">
                    <button
                      type="button"
                      onClick={() => handleLibraryDescribe(asset)}
                      disabled={describeState?.pending}
                    >
                      {describeState?.pending ? "要約中…" : "AIで要約"}
                    </button>
                    {describeState?.text ? (
                      <p className="thumbnail-library-card__describe-text">{describeState.text}</p>
                    ) : null}
                    {describeState?.error ? (
                      <p className="thumbnail-library__message thumbnail-library__message--error">
                        {describeState.error}
                      </p>
                    ) : null}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  ) : (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>参考サムネ登録</h3>
          <p>チャンネルを選択するとライブラリが開きます。</p>
        </div>
      </div>
    </section>
  );

  const templatesPanel = activeChannel ? (
    <section className="thumbnail-library-panel thumbnail-library-panel--templates">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>サムネテンプレ（型）</h3>
          <p>
            チャンネルごとに「型」を登録して、手動でAI生成できます。置換キー:
            <code> {"{{title}} {{thumbnail_upper}} {{thumbnail_title}} {{thumbnail_lower}} {{thumbnail_prompt}}"} </code>
          </p>
        </div>
        <div className="thumbnail-library-panel__header-actions">
          <button type="button" onClick={handleTemplatesRefresh} disabled={templatesLoading || templatesStatus.pending}>
            {templatesLoading ? "読込中…" : "再読み込み"}
          </button>
          <button type="button" onClick={handleAddTemplate} disabled={templatesStatus.pending}>
            追加
          </button>
          <button type="button" onClick={handleSaveTemplates} disabled={templatesStatus.pending || !templatesDirty}>
            {templatesStatus.pending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
      {imageModels.some((model) => model.provider === "openrouter") ? (
        <details className="thumbnail-library-panel__pricing">
          <summary>料金（OpenRouter /models）</summary>
          <div className="thumbnail-library-panel__pricing-body">
            <p className="thumbnail-library__placeholder">
              OpenRouter の <code>/api/v1/models</code> から取得した単価です（USD/token・USD/request・USD/image(unit)）。この画面のAI生成は{" "}
              <strong>1枚=1 request（N枚ならN回）</strong> で送信します。概算: <code>request</code> + <code>image</code> +（入力tok×
              <code>prompt</code>）+（出力tok×<code>completion</code>）
            </p>
            <table className="thumbnail-pricing-table">
              <thead>
                <tr>
                  <th>model_key</th>
                  <th>model_name</th>
                  <th>image</th>
                  <th>request</th>
                  <th>prompt</th>
                  <th>completion</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {imageModels
                  .filter((model) => model.provider === "openrouter")
                  .map((model) => {
                    const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                    const requestUnit = parsePricingNumber(model.pricing?.request ?? null);
                    const promptUnit = parsePricingNumber(model.pricing?.prompt ?? null);
                    const completionUnit = parsePricingNumber(model.pricing?.completion ?? null);
                    return (
                      <tr key={model.key}>
                        <td>
                          <code>{model.key}</code>
                        </td>
                        <td>
                          <code>{model.model_name}</code>
                        </td>
                        <td>{imageUnit !== null ? `${formatUsdAmount(imageUnit)}/unit` : "—"}</td>
                        <td>{requestUnit !== null ? `${formatUsdAmount(requestUnit)}/req` : "—"}</td>
                        <td>{promptUnit !== null ? formatUsdPerMillionTokens(promptUnit) : "—"}</td>
                        <td>{completionUnit !== null ? formatUsdPerMillionTokens(completionUnit) : "—"}</td>
                        <td>{model.pricing_updated_at ? formatDate(model.pricing_updated_at) : "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
      {imageModelsError ? <p className="thumbnail-library__alert">{imageModelsError}</p> : null}
      {templatesStatus.error ? <p className="thumbnail-library__alert">{templatesStatus.error}</p> : null}
      {templatesStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{templatesStatus.success}</p>
      ) : null}
      {!channelTemplates || channelTemplates.templates.length === 0 ? (
        <p className="thumbnail-library__placeholder">テンプレがまだありません。「追加」→「保存」で登録します。</p>
      ) : (
        <div className="thumbnail-library-panel__cards">
          {channelTemplates.templates.map((tpl) => {
            const isDefault = channelTemplates.default_template_id === tpl.id;
            return (
              <details key={tpl.id} className="thumbnail-library-panel__card">
                <summary>
                  <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="radio"
                      name="thumbnail_default_template"
                      checked={isDefault}
                      onChange={() => handleTemplateDefaultChange(tpl.id)}
                    />
                    <strong>{tpl.name}</strong>
                  </label>
                  <span style={{ marginLeft: 8, color: "#64748b" }}>{tpl.image_model_key}</span>
                </summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  <label>
                    <span>テンプレ名</span>
                    <input
                      type="text"
                      value={tpl.name}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "name", event.target.value)}
                    />
                  </label>
                  <label>
                    <span>画像モデル</span>
                    <select
                      value={tpl.image_model_key}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "image_model_key", event.target.value)}
                    >
                      <option value="">選択してください</option>
                      {imageModels.map((model) => {
                        const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                        const costSuffix = imageUnit !== null ? ` / ${formatUsdAmount(imageUnit)}/img` : "";
                        return (
                          <option key={model.key} value={model.key}>
                            {model.key} ({model.provider}{costSuffix})
                          </option>
                        );
                      })}
                    </select>
                  </label>
                  <label>
                    <span>プロンプトテンプレ</span>
                    <textarea
                      value={tpl.prompt_template}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "prompt_template", event.target.value)}
                      rows={6}
                    />
                  </label>
                  <label>
                    <span>ネガティブ（任意）</span>
                    <textarea
                      value={tpl.negative_prompt ?? ""}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "negative_prompt", event.target.value)}
                      rows={2}
                    />
                  </label>
                  <label>
                    <span>メモ（任意）</span>
                    <textarea
                      value={tpl.notes ?? ""}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "notes", event.target.value)}
                      rows={2}
                    />
                  </label>
                  <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <button type="button" onClick={() => handleDeleteTemplate(tpl.id)}>
                      削除
                    </button>
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      )}
    </section>
  ) : (
    <section className="thumbnail-library-panel thumbnail-library-panel--templates">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>サムネテンプレ（型）</h3>
          <p>チャンネルを選択するとテンプレが表示されます。</p>
        </div>
      </div>
    </section>
  );

  const channelInfoPanel = activeChannel ? (
    <section className="channel-profile-panel">
      <div className="channel-profile-panel__header">
        <div>
          <h2>{activeChannelName ?? activeChannel.channel}</h2>
          <p className="channel-profile-panel__subtitle">チャンネルの概況</p>
        </div>
      </div>
      {summary ? (
        <div className="channel-profile-metrics">
          <div className="channel-profile-metric">
            <span>登録者</span>
            <strong>{formatNumber(summary.subscriber_count)}</strong>
          </div>
          <div className="channel-profile-metric">
            <span>総再生</span>
            <strong>{formatNumber(summary.view_count)}</strong>
          </div>
          <div className="channel-profile-metric">
            <span>案件</span>
            <strong>{summary.total.toLocaleString("ja-JP")}</strong>
          </div>
          {activeChannel.library_path ? (
            <div className="channel-profile-metric channel-profile-metric--wide">
              <span>ライブラリパス</span>
              <code>{activeChannel.library_path}</code>
            </div>
          ) : null}
        </div>
      ) : null}
      {channelVideos.length > 0 ? (
        <section className="thumbnail-channel-videos">
          <div className="thumbnail-channel-videos__header">
            <h3>最新動画プレビュー</h3>
            <span>{channelVideos.length} 件</span>
          </div>
          <p className="thumbnail-library__placeholder">
            ※案件で「案を登録」フォームを開いた状態のときに「このサムネを案に取り込む」が使えます。
          </p>
          <div className="thumbnail-channel-videos__list">
            {channelVideos.map((video) => {
              const disableApply = !variantForm;
              return (
                <div key={video.video_id} className="thumbnail-channel-video">
                  <a className="thumbnail-channel-video__thumb" href={video.url} target="_blank" rel="noreferrer">
                    {video.thumbnail_url ? (
                      <img src={video.thumbnail_url} alt={video.title} loading="lazy" />
                    ) : (
                      <span>No Image</span>
                    )}
                  </a>
                  <div className="thumbnail-channel-video__info">
                    <div className="thumbnail-channel-video__title" title={video.title}>
                      {video.title}
                    </div>
                    <div className="thumbnail-channel-video__meta">
                      <span>{formatDate(video.published_at)}</span>
                      <span>再生: {formatNumber(video.view_count)}</span>
                      <span>推定CTR: {formatPercent(video.estimated_ctr)}</span>
                      <span>長さ: {formatDuration(video.duration_seconds)}</span>
                    </div>
                    <div className="thumbnail-channel-video__actions">
                      <button type="button" onClick={() => handleApplyVideoThumbnail(video)} disabled={disableApply}>
                        このサムネを案に取り込む
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : (
        <p className="thumbnail-library__placeholder">最新の動画プレビューがまだ取得されていません。</p>
      )}
    </section>
  ) : (
    <section className="channel-profile-panel">
      <div className="channel-profile-panel__header">
        <div>
          <h2>チャンネルを選択</h2>
          <p className="channel-profile-panel__subtitle">上のタブでチャンネルを選択してください。</p>
        </div>
      </div>
    </section>
  );

  const generateDialogTemplate =
    generateDialog && channelTemplates?.channel === generateDialog.channel
      ? channelTemplates.templates.find((tpl) => tpl.id === generateDialog.templateId)
      : undefined;
  const generateDialogResolvedModelKey = generateDialog
    ? (generateDialog.imageModelKey.trim() || generateDialogTemplate?.image_model_key?.trim() || "")
    : "";
  const generateDialogResolvedModel = generateDialogResolvedModelKey
    ? imageModels.find((model) => model.key === generateDialogResolvedModelKey)
    : undefined;
  const generateDialogResolvedPricing = generateDialogResolvedModel?.pricing ?? null;
  const generateDialogResolvedPricingUpdatedAt = generateDialogResolvedModel?.pricing_updated_at ?? null;
  const generateDialogImageUnitUsd = parsePricingNumber(generateDialogResolvedPricing?.image ?? null);
  const generateDialogRequestUnitUsd = parsePricingNumber(generateDialogResolvedPricing?.request ?? null);
  const generateDialogPromptTokenUsd = parsePricingNumber(generateDialogResolvedPricing?.prompt ?? null);
  const generateDialogCompletionTokenUsd = parsePricingNumber(generateDialogResolvedPricing?.completion ?? null);
  const generateDialogImageSubtotalUsd =
    generateDialog && generateDialogImageUnitUsd !== null ? generateDialogImageUnitUsd * generateDialog.count : null;
  const generateDialogRequestSubtotalUsd =
    generateDialog && generateDialogRequestUnitUsd !== null ? generateDialogRequestUnitUsd * generateDialog.count : null;

  return (
    <>
      <section className={`thumbnail-workspace${compact ? " thumbnail-workspace--compact" : ""}`}>
        <header className="thumbnail-workspace__header">
          <div>
            <h2 className="thumbnail-workspace__title">サムネイル管理</h2>
            <p className="thumbnail-workspace__subtitle">サムネ登録→案件検討→企画反映までをこの画面で完結できます。</p>
          </div>
          <div className="thumbnail-workspace__header-actions">
            <button type="button" className="thumbnail-refresh-button" onClick={handleRefresh} disabled={loading}>
              最新の情報を再取得
            </button>
            {activeTab === "projects" ? (
              <button
                type="button"
                className="workspace-button workspace-button--primary"
                onClick={handleStartNewVariant}
                disabled={loading || filteredProjects.length === 0}
              >
                新しい案を作成
              </button>
            ) : null}
          </div>
        </header>
        <div className="thumbnail-hub">
          {overview && overview.channels.length > 1 ? (
            <nav className="thumbnail-hub__tabs thumbnail-hub__tabs--channels" aria-label="チャンネル選択">
              {overview.channels.map((channel) => {
                const isActive = channel.channel === activeChannel?.channel;
                return (
                  <button
                    key={channel.channel}
                    type="button"
                    className={`thumbnail-hub__tab ${isActive ? "thumbnail-hub__tab--active" : ""}`}
                    onClick={() => setSelectedChannel(channel.channel)}
                  >
                    {channel.channel_title ? `${channel.channel} ${channel.channel_title}` : channel.channel}
                    <span className="thumbnail-hub__tab-count">{channel.summary.total}</span>
                  </button>
                );
              })}
            </nav>
          ) : null}
          <nav className="thumbnail-hub__tabs thumbnail-hub__tabs--views" aria-label="表示切替">
            {THUMBNAIL_WORKSPACE_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`thumbnail-hub__tab ${activeTab === tab.key ? "thumbnail-hub__tab--active" : ""}`}
                onClick={() => setActiveTab(tab.key)}
                aria-pressed={activeTab === tab.key}
                title={tab.description}
              >
                {tab.label}
              </button>
            ))}
          </nav>
          <div className="thumbnail-hub__panes">
            {activeTab === "projects" ? (
              <section className="thumbnail-hub__pane thumbnail-hub__pane--projects">
            <div className="thumbnail-actions">
              <div className="thumbnail-actions__left">
                <h3 className="thumbnail-actions__title">{activeChannelName ?? "チャンネル一覧"}</h3>
              </div>
              <div className="thumbnail-actions__search">
                <input
                  type="search"
                  placeholder="企画タイトル・タグ・案名で検索"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                />
              </div>
            </div>
            <div className="thumbnail-toolbar thumbnail-toolbar--filters">
              <div className="thumbnail-toolbar__filters">
                {STATUS_FILTERS.map((filter) => (
                  <button
                    key={filter.key}
                    type="button"
                    className={`thumbnail-filter ${statusFilter === filter.key ? "is-active" : ""}`}
                    onClick={() => setStatusFilter(filter.key)}
                    aria-pressed={statusFilter === filter.key}
                  >
                    <span>{filter.label}</span>
                    <span className="thumbnail-filter__count">{statusCounters[filter.key]}</span>
                  </button>
                ))}
              </div>
            </div>
            {errorMessage ? <div className="thumbnail-alert thumbnail-alert--error">{errorMessage}</div> : null}
            {loading ? <div className="thumbnail-loading">読み込み中…</div> : null}
            {!loading && filteredProjects.length === 0 ? (
              <div className="thumbnail-empty">
                {overview && overview.channels.length > 0
                  ? "選択中の条件に該当するサムネイルがありません。"
                  : "チャンネルを登録するとサムネイル管理がここに表示されます。"}
              </div>
            ) : null}
            <div className="thumbnail-card-list">
              {filteredProjects.map((project) => {
                const projectKey = getProjectKey(project);
                const projectUpdating = updatingProjectId === projectKey;
                const isCreatingVariant = variantForm?.projectKey === projectKey;
                const currentVariantForm = isCreatingVariant ? variantForm : null;
                const disableVariantActions = projectUpdating || loading;
                const statusLabel = PROJECT_STATUS_LABELS[project.status] ?? project.status;
                const readyLabel = project.ready_for_publish ? "公開OK" : "—";
                const primaryTitle = project.title ?? project.sheet_title ?? "タイトル未設定";
                const secondaryTitle =
                  project.sheet_title && project.sheet_title !== primaryTitle ? project.sheet_title : null;
                const selectedVariant = project.variants.find((variant) => variant.is_selected);
                const feedback = cardFeedback[projectKey];
                const assetPath = `${THUMBNAIL_ASSET_BASE_PATH}/${project.channel}/${project.video}/`;
                const hasExtraInfo = Boolean(
                  secondaryTitle || project.summary || project.notes || (project.tags && project.tags.length > 0)
                );
                const selectedVariantLabel = selectedVariant ? selectedVariant.label ?? selectedVariant.id : null;
                const cardClasses = [
                  "thumbnail-card",
                  projectUpdating ? "is-updating" : "",
                  project.variants.length === 0 ? "is-empty" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <article
                    key={projectKey}
                    className={cardClasses}
                  >
                    <div className="thumbnail-card__inner">
                      <header className="thumbnail-card__header">
                        <div className="thumbnail-card__header-main">
                          <div className="thumbnail-card__identity">
                            <span className="thumbnail-card__code-main">
                              {project.script_id ?? `${project.channel}-${project.video}`}
                            </span>
                            <span className="thumbnail-card__code-sub">{project.channel}</span>
                            {project.variants.length === 0 ? (
                              <span className="thumbnail-card__badge">未登録</span>
                            ) : null}
                          </div>
                          <div className="thumbnail-card__status-group">
                            <span className={`thumbnail-card__status-badge thumbnail-card__status-badge--${project.status}`}>
                              {statusLabel}
                            </span>
                            <select
                              value={project.status}
                              onChange={(event) =>
                                handleStatusChange(project, event.target.value as ThumbnailProjectStatus)
                              }
                              disabled={disableVariantActions}
                              aria-label="ステータス変更"
                            >
                              {PROJECT_STATUS_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>
                        <div className="thumbnail-card__quick-info">
                          <div className="thumbnail-card__meta-row">
                            <span>Ready: {readyLabel}</span>
                            <span>Variants: {project.variants.length}</span>
                          </div>
                          {selectedVariantLabel ? (
                            <div className="thumbnail-card__meta-row">選択中: {selectedVariantLabel}</div>
                          ) : null}
                        </div>
                      </header>
                      <div className="thumbnail-card__actions">
                        <button
                          type="button"
                          onClick={() => handleDropzoneClick(projectKey, disableVariantActions)}
                          disabled={disableVariantActions}
                        >
                          画像を差し替える
                        </button>
                        <button type="button" onClick={() => handleOpenGenerateDialog(project)} disabled={disableVariantActions}>
                          AI生成
                        </button>
                        <button type="button" onClick={() => handleOpenVariantForm(project)} disabled={disableVariantActions}>
                          案を登録
                        </button>
                        <button
                          type="button"
                          onClick={() => handleOpenPlanningDialog(project, selectedVariant)}
                          disabled={!selectedVariant || disableVariantActions}
                        >
                          企画に書き出す
                        </button>
                        <button type="button" onClick={() => handleOpenProjectForm(project)} disabled={disableVariantActions}>
                          メモを編集
                        </button>
                      </div>
                      <div className="thumbnail-card__storage">
                        <div className="thumbnail-card__storage-label">画像パス</div>
                        <div className="thumbnail-card__storage-path">
                          <code>{assetPath}</code>
                        </div>
                        <div className="thumbnail-card__storage-actions">
                          <button
                            type="button"
                            className="thumbnail-card__storage-copy"
                            onClick={() => handleCopyAssetPath(assetPath)}
                          >
                            パスをコピー
                          </button>
                        </div>
                      </div>
                      {hasExtraInfo ? (
                        <details className="thumbnail-card__details">
                          <summary>メモ / タグ</summary>
                          <div className="thumbnail-card__details-body">
                            {secondaryTitle ? <p>サブタイトル: {secondaryTitle}</p> : null}
                            {project.summary ? <p>概要: {project.summary}</p> : null}
                            {project.notes ? <p>メモ: {project.notes}</p> : null}
                            {project.tags && project.tags.length ? (
                              <p>
                                {project.tags.map((tag) => (
                                  <span key={tag} className="thumbnail-tag">
                                    {tag}
                                  </span>
                                ))}
                              </p>
                            ) : null}
                          </div>
                        </details>
                      ) : null}
                      <div className="thumbnail-card__variants">
                        {project.variants.length === 0 ? (
                          <p className="thumbnail-library__placeholder">まだサムネイル案が登録されていません。</p>
                        ) : (
                          <div className="thumbnail-variant-grid">
                            {project.variants.map((variant) => {
                              const variantImage =
                                variant.preview_url
                                  ? resolveApiUrl(variant.preview_url)
                                  : variant.image_url
                                    ? resolveApiUrl(variant.image_url)
                                    : variant.image_path
                                      ? resolveApiUrl(`/thumbnails/assets/${variant.image_path}`)
                                      : null;
                              const variantSelected =
                                Boolean(variant.is_selected) || project.selected_variant_id === variant.id;
                              return (
                                <button
                                  type="button"
                                  key={variant.id}
                                  className={`thumbnail-variant-tile${variantSelected ? " is-selected" : ""}`}
                                  onClick={() => handleSelectVariant(project, variant)}
                                >
                                  <div className="thumbnail-variant-tile__media">
                                    {variantImage ? (
                                      <img src={variantImage} alt={variant.label ?? variant.id} loading="lazy" />
                                    ) : (
                                      <span className="thumbnail-variant-tile__placeholder">No Image</span>
                                    )}
                                  </div>
                                  <div className="thumbnail-variant-tile__content">
                                    <div className="thumbnail-variant-tile__title">{variant.label ?? variant.id}</div>
                                    <div className="thumbnail-variant-tile__badge">
                                      {VARIANT_STATUS_LABELS[variant.status]}
                                    </div>
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                      <div
                        className={`thumbnail-dropzone ${activeDropProject === projectKey ? "is-active" : ""}`}
                        onDragEnter={(event) => handleDropzoneDragEnter(event, projectKey, disableVariantActions)}
                        onDragOver={(event) => handleDropzoneDragOver(event, disableVariantActions)}
                        onDragLeave={(event) => handleDropzoneDragLeave(event, projectKey)}
                        onDrop={(event) => handleDropzoneDrop(event, project, disableVariantActions)}
                      >
                        <p className="thumbnail-dropzone__hint">ここへドラッグすると即差し替えできます。</p>
                        <input
                          ref={(element) => {
                            if (element) {
                              dropzoneFileInputs.current.set(projectKey, element);
                            } else {
                              dropzoneFileInputs.current.delete(projectKey);
                            }
                          }}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          hidden
                          onChange={(event) => handleDropzoneInputChange(event, project)}
                        />
                        <button
                          type="button"
                          className="thumbnail-card__manual-button"
                          onClick={() => handleDropzoneClick(projectKey, disableVariantActions)}
                          disabled={disableVariantActions}
                        >
                          画像を選択して追加
                        </button>
                      </div>
                      {feedback ? (
                        <div
                          className={`thumbnail-card__feedback thumbnail-card__feedback--${feedback.type}`}
                          role="status"
                        >
                          {feedback.message}
                        </div>
                      ) : null}
                      {currentVariantForm ? (
                        <form
                          className="thumbnail-variant-form"
                          onSubmit={(event) => handleVariantFormSubmit(event, project)}
                        >
                          <div className="thumbnail-variant-form__primary">
                            <label className="thumbnail-variant-form__field">
                              <span>案の名前</span>
                              <input
                                type="text"
                                value={currentVariantForm.label}
                                onChange={(event) => handleVariantFormFieldChange("label", event.target.value)}
                                placeholder="例: 参考A案"
                              />
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>状態</span>
                              <select
                                value={currentVariantForm.status}
                                onChange={(event) =>
                                  handleVariantFormFieldChange("status", event.target.value as ThumbnailVariantStatus)
                                }
                              >
                                {VARIANT_STATUS_OPTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>メモ</span>
                              <textarea
                                value={currentVariantForm.notes}
                                onChange={(event) => handleVariantFormFieldChange("notes", event.target.value)}
                                rows={3}
                                placeholder="デザイナー向けの補足や気づきなど"
                              />
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>タグ（カンマ区切り）</span>
                              <input
                                type="text"
                                value={currentVariantForm.tags}
                                onChange={(event) => handleVariantFormFieldChange("tags", event.target.value)}
                                placeholder="人物, 共感"
                              />
                            </label>
                          </div>
                          <div className="thumbnail-variant-form__actions">
                            <button
                              type="button"
                              className="thumbnail-variant-form__button thumbnail-variant-form__button--secondary"
                              onClick={handleCancelVariantForm}
                            >
                              キャンセル
                            </button>
                            <button
                              type="submit"
                              className="thumbnail-variant-form__button thumbnail-variant-form__button--primary"
                              disabled={disableVariantActions}
                            >
                              保存
                            </button>
                          </div>
                        </form>
                      ) : null}
                      {projectForm?.projectKey === projectKey ? (
                        <form
                          className="thumbnail-project-form"
                          onSubmit={(event) => handleProjectFormSubmit(event, project)}
                        >
                          <div className="thumbnail-project-form__fields">
                            <label className="thumbnail-project-form__field">
                              <span>担当</span>
                              <input
                                type="text"
                                value={projectForm.owner}
                                onChange={(event) => handleProjectFormChange("owner", event.target.value)}
                                placeholder="担当者"
                              />
                            </label>
                            <label className="thumbnail-project-form__field thumbnail-project-form__field--wide">
                              <span>サマリ</span>
                              <textarea
                                rows={2}
                                value={projectForm.summary}
                                onChange={(event) => handleProjectFormChange("summary", event.target.value)}
                                placeholder="案件の概要やターゲット"
                              />
                            </label>
                            <label className="thumbnail-project-form__field thumbnail-project-form__field--wide">
                              <span>メモ</span>
                              <textarea
                                rows={3}
                                value={projectForm.notes}
                                onChange={(event) => handleProjectFormChange("notes", event.target.value)}
                                placeholder="進行メモや懸念点"
                              />
                            </label>
                            <label className="thumbnail-project-form__field">
                              <span>タグ（カンマ区切り）</span>
                              <input
                                type="text"
                                value={projectForm.tags}
                                onChange={(event) => handleProjectFormChange("tags", event.target.value)}
                                placeholder="仏教, 人間関係"
                              />
                            </label>
                            <label className="thumbnail-project-form__field">
                              <span>期日</span>
                              <input
                                type="date"
                                value={projectForm.dueAt}
                                onChange={(event) => handleProjectFormChange("dueAt", event.target.value)}
                              />
                            </label>
                          </div>
                          <div className="thumbnail-project-form__actions">
                            <button type="button" onClick={handleCancelProjectForm}>
                              閉じる
                            </button>
                            <button type="submit" disabled={disableVariantActions}>
                              保存
                            </button>
                          </div>
                        </form>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        ) : null}
            {activeTab === "templates" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--templates">{templatesPanel}</div>
            ) : null}
            {activeTab === "library" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--library">{libraryPanel}</div>
            ) : null}
            {activeTab === "channel" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--channel">{channelInfoPanel}</div>
            ) : null}
          </div>
        </div>
      </section>
      {planningDialog ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={handleClosePlanningDialog} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                {planningDialog.channel} / {planningDialog.projectTitle || "タイトル未設定"}
              </div>
              <h2>サムネから企画行を作成</h2>
              {planningDialog.variantLabel ? (
                <p className="thumbnail-planning-dialog__meta">案: {planningDialog.variantLabel}</p>
              ) : null}
            </header>
            <form className="thumbnail-planning-form" onSubmit={(event) => handlePlanningSubmit(event)}>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>動画番号</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={planningDialog.videoNumber}
                    onChange={(event) => handlePlanningFieldChange("videoNumber", event.target.value)}
                    placeholder="例: 191"
                    required
                  />
                </label>
                <label>
                  <span>No.</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={planningDialog.no}
                    onChange={(event) => handlePlanningFieldChange("no", event.target.value)}
                    placeholder="例: 191"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>企画タイトル</span>
                  <input
                    type="text"
                    value={planningDialog.title}
                    onChange={(event) => handlePlanningFieldChange("title", event.target.value)}
                    placeholder="【○○】〜"
                    required
                  />
                </label>
              </div>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>サムネタイトル上</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailUpper}
                    onChange={(event) => handlePlanningFieldChange("thumbnailUpper", event.target.value)}
                    placeholder="呼びかけ"
                  />
                </label>
                <label>
                  <span>サムネタイトル下</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailLower}
                    onChange={(event) => handlePlanningFieldChange("thumbnailLower", event.target.value)}
                    placeholder="行動やベネフィット"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>サムネタイトル（中央）</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailTitle}
                    onChange={(event) => handlePlanningFieldChange("thumbnailTitle", event.target.value)}
                    placeholder="その人間関係、もう捨てていい。"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>サムネ生成プロンプト / 指示</span>
                <textarea
                  value={planningDialog.thumbnailPrompt}
                  onChange={(event) => handlePlanningFieldChange("thumbnailPrompt", event.target.value)}
                  rows={3}
                  placeholder="情景や掲載したいURL、文字配置の指定など"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>サムネ用 DALL·E プロンプト</span>
                <textarea
                  value={planningDialog.dallePrompt}
                  onChange={(event) => handlePlanningFieldChange("dallePrompt", event.target.value)}
                  rows={3}
                  placeholder="AI画像生成向けの詳細指示があれば記入"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>企画意図</span>
                <textarea
                  value={planningDialog.conceptIntent}
                  onChange={(event) => handlePlanningFieldChange("conceptIntent", event.target.value)}
                  rows={3}
                  placeholder="どんな悩みを持つ人に、何を提供する企画か"
                />
              </label>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>悩みタグ</span>
                  <input
                    type="text"
                    value={planningDialog.primaryTag}
                    onChange={(event) => handlePlanningFieldChange("primaryTag", event.target.value)}
                    placeholder="孤独 / 断捨離 など"
                  />
                </label>
                <label>
                  <span>サブタグ</span>
                  <input
                    type="text"
                    value={planningDialog.secondaryTag}
                    onChange={(event) => handlePlanningFieldChange("secondaryTag", event.target.value)}
                    placeholder="罪悪感 / お金 など"
                  />
                </label>
                <label>
                  <span>ライフシーン</span>
                  <input
                    type="text"
                    value={planningDialog.lifeScene}
                    onChange={(event) => handlePlanningFieldChange("lifeScene", event.target.value)}
                    placeholder="就寝前 / 朝の台所 など"
                  />
                </label>
              </div>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>キーコンセプト</span>
                  <input
                    type="text"
                    value={planningDialog.keyConcept}
                    onChange={(event) => handlePlanningFieldChange("keyConcept", event.target.value)}
                    placeholder="慈悲 / 断捨離 / 養生 など"
                  />
                </label>
                <label>
                  <span>ベネフィット一言</span>
                  <input
                    type="text"
                    value={planningDialog.benefit}
                    onChange={(event) => handlePlanningFieldChange("benefit", event.target.value)}
                    placeholder="罪悪感なく距離を取れる"
                  />
                </label>
                <label>
                  <span>たとえ話イメージ</span>
                  <input
                    type="text"
                    value={planningDialog.analogy}
                    onChange={(event) => handlePlanningFieldChange("analogy", event.target.value)}
                    placeholder="糸を静かにほどく"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>説明文（リード）</span>
                <textarea
                  value={planningDialog.descriptionLead}
                  onChange={(event) => handlePlanningFieldChange("descriptionLead", event.target.value)}
                  rows={3}
                  placeholder="視聴者への呼びかけや動画のゴールを記載"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>説明文（この動画でわかること）</span>
                <textarea
                  value={planningDialog.descriptionTakeaways}
                  onChange={(event) => handlePlanningFieldChange("descriptionTakeaways", event.target.value)}
                  rows={3}
                  placeholder="・ポイントを箇条書きで記入"
                />
              </label>
              {planningDialog.error ? (
                <div className="thumbnail-planning-form__error" role="alert">
                  {planningDialog.error}
                </div>
              ) : null}
              <div className="thumbnail-planning-form__actions">
                <button type="button" onClick={handleClosePlanningDialog} disabled={planningDialog.saving}>
                  キャンセル
                </button>
                <button type="submit" className="thumbnail-planning-form__submit" disabled={planningDialog.saving}>
                  {planningDialog.saving ? "作成中…" : "企画行を作成"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
      {generateDialog ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={handleCloseGenerateDialog} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                {generateDialog.channel} / {generateDialog.video}
              </div>
              <h2>AIでサムネを生成</h2>
            </header>
            <form className="thumbnail-planning-form" onSubmit={(event) => handleGenerateDialogSubmit(event)}>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>テンプレ</span>
                  <select
                    value={generateDialog.templateId}
                    onChange={(event) => {
                      const nextId = event.target.value;
                      setGenerateDialog((current) => {
                        if (!current) {
                          return current;
                        }
                        const selected =
                          channelTemplates?.channel === current.channel
                            ? channelTemplates.templates.find((tpl) => tpl.id === nextId)
                            : undefined;
                        return {
                          ...current,
                          templateId: nextId,
                          imageModelKey: selected?.image_model_key ?? current.imageModelKey,
                        };
                      });
                    }}
                  >
                    <option value="">（テンプレなし）</option>
                    {(channelTemplates?.channel === generateDialog.channel ? channelTemplates.templates : []).map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>枚数</span>
                  <input
                    type="number"
                    min={1}
                    max={4}
                    value={generateDialog.count}
                    onChange={(event) => handleGenerateDialogFieldChange("count", Number(event.target.value))}
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>ラベル（任意）</span>
                  <input
                    type="text"
                    value={generateDialog.label}
                    onChange={(event) => handleGenerateDialogFieldChange("label", event.target.value)}
                    placeholder="空なら自動で命名"
                  />
                </label>
              </div>
              {planningLoading ? <p className="thumbnail-library__placeholder">企画CSV読込中…</p> : null}
              {planningError ? <p className="thumbnail-library__alert">{planningError}</p> : null}
              <div className="thumbnail-planning-form__grid">
                <label className="thumbnail-planning-form__field--wide">
                  <span>上段（赤）</span>
                  <input
                    type="text"
                    value={generateDialog.copyUpper}
                    onChange={(event) => handleGenerateDialogFieldChange("copyUpper", event.target.value)}
                    placeholder="例: 知らないと危険"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>中段（黄）</span>
                  <input
                    type="text"
                    value={generateDialog.copyTitle}
                    onChange={(event) => handleGenerateDialogFieldChange("copyTitle", event.target.value)}
                    placeholder="例: 99%が誤解"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>下段（白）</span>
                  <input
                    type="text"
                    value={generateDialog.copyLower}
                    onChange={(event) => handleGenerateDialogFieldChange("copyLower", event.target.value)}
                    placeholder="例: 人間関係の本質"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>個別指示（企画CSV: サムネ画像プロンプト）</span>
                <textarea
                  value={generateDialog.thumbnailPrompt}
                  onChange={(event) => handleGenerateDialogFieldChange("thumbnailPrompt", event.target.value)}
                  rows={3}
                  placeholder="空でもOK。URLや追加の指示があれば記入。"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked" style={{ flexDirection: "row", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={generateDialog.saveToPlanning}
                  onChange={(event) => handleGenerateDialogFieldChange("saveToPlanning", event.target.checked)}
                />
                <span>この内容を企画CSVに保存してから生成する</span>
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>プロンプト（任意）</span>
                <textarea
                  value={generateDialog.prompt}
                  onChange={(event) => handleGenerateDialogFieldChange("prompt", event.target.value)}
                  rows={6}
                  placeholder="空ならテンプレ + 企画CSVの値から組み立てます（上の3段テキスト等）。"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>画像モデル（任意）</span>
                <select
                  value={generateDialog.imageModelKey}
                  onChange={(event) => handleGenerateDialogFieldChange("imageModelKey", event.target.value)}
                >
                  <option value="">（テンプレの指定を使う）</option>
                  {imageModels.map((model) => {
                    const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                    const costSuffix = imageUnit !== null ? ` / ${formatUsdAmount(imageUnit)}/img` : "";
                    return (
                      <option key={model.key} value={model.key}>
                        {model.key} ({model.provider}{costSuffix})
                      </option>
                    );
                  })}
                </select>
              </label>
              {generateDialogResolvedModelKey ? (
                generateDialogResolvedModel ? (
                  generateDialogResolvedModel.provider === "openrouter" ? (
                    generateDialogResolvedPricing ? (
                      <p className="thumbnail-library__placeholder">
                        料金(OpenRouter, USD): 画像{" "}
                        {generateDialogImageUnitUsd !== null ? `${formatUsdAmount(generateDialogImageUnitUsd)}/img` : "—"}
                        {generateDialogRequestUnitUsd !== null
                          ? `, request ${formatUsdAmount(generateDialogRequestUnitUsd)}/req`
                          : ""}
                        {generateDialogPromptTokenUsd !== null
                          ? `, 入力 ${formatUsdPerMillionTokens(generateDialogPromptTokenUsd)}`
                          : ""}
                        {generateDialogCompletionTokenUsd !== null
                          ? `, 出力 ${formatUsdPerMillionTokens(generateDialogCompletionTokenUsd)}`
                          : ""}
                        {generateDialogImageSubtotalUsd !== null
                          ? ` / 今回(${generateDialog.count}枚)の画像単価分: ${formatUsdAmount(generateDialogImageSubtotalUsd)}`
                          : ""}
                        {generateDialogRequestSubtotalUsd !== null && generateDialogRequestSubtotalUsd !== 0
                          ? ` (request合計: ${formatUsdAmount(generateDialogRequestSubtotalUsd)})`
                          : ""}
                        {generateDialogResolvedPricingUpdatedAt
                          ? ` (単価更新: ${formatDate(generateDialogResolvedPricingUpdatedAt)})`
                          : ""}
                        {" ※トークン分はプロンプト長で変動"}
                      </p>
                    ) : (
                      <p className="thumbnail-library__placeholder">
                        料金(OpenRouter): 単価情報を取得できませんでした（{generateDialogResolvedModelKey}）。
                      </p>
                    )
                  ) : (
                    <p className="thumbnail-library__placeholder">
                      料金: {generateDialogResolvedModelKey} は OpenRouter 以外のプロバイダ（{generateDialogResolvedModel.provider}）のため、単価表示対象外です。
                    </p>
                  )
                ) : (
                  <p className="thumbnail-library__placeholder">
                    料金: モデル情報が見つかりませんでした（{generateDialogResolvedModelKey}）。
                  </p>
                )
              ) : null}
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>ステータス</span>
                  <select
                    value={generateDialog.status}
                    onChange={(event) =>
                      handleGenerateDialogFieldChange("status", event.target.value as ThumbnailVariantStatus)
                    }
                  >
                    {VARIANT_STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>タグ（カンマ区切り）</span>
                  <input
                    type="text"
                    value={generateDialog.tags}
                    onChange={(event) => handleGenerateDialogFieldChange("tags", event.target.value)}
                    placeholder="例: 人物, 共感"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>メモ（任意）</span>
                <textarea
                  value={generateDialog.notes}
                  onChange={(event) => handleGenerateDialogFieldChange("notes", event.target.value)}
                  rows={2}
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked" style={{ flexDirection: "row", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={generateDialog.makeSelected}
                  onChange={(event) => handleGenerateDialogFieldChange("makeSelected", event.target.checked)}
                />
                <span>生成した1枚目を「採用中」にする</span>
              </label>
              {generateDialog.error ? (
                <div className="thumbnail-planning-form__error" role="alert">
                  {generateDialog.error}
                </div>
              ) : null}
              <div className="thumbnail-planning-form__actions">
                <button type="button" onClick={handleCloseGenerateDialog} disabled={generateDialog.saving}>
                  キャンセル
                </button>
                <button type="submit" className="thumbnail-planning-form__submit" disabled={generateDialog.saving}>
                  {generateDialog.saving ? "生成中…" : "生成"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
