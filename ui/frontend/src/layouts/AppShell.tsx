import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useSearchParams,
  matchPath,
} from "react-router-dom";
import {
  fetchChannels,
  fetchVideos,
  fetchVideoDetail,
  updateAssembled,
  updateTts,
  validateTts,
  updateSrt,
  verifySrt,
  updateStatus,
  updateReady,
  updateStages,
  fetchDashboardOverview,
  replaceTtsSegment,
} from "../api/client";
import {
  ChannelSummary,
  VideoSummary,
  VideoDetail,
  DashboardOverview,
  TtsSaveResponse,
  TtsReplaceResponse,
  TtsValidationResponse,
  SrtVerifyResponse,
} from "../api/types";
import { translateStatus, STAGE_LABELS } from "../utils/i18n";
import { pickCurrentStage, resolveStageStatus } from "../components/StageProgress";
import { ChannelListSection } from "../components/ChannelListSection";
import { resolveAudioSubtitleState } from "../utils/video";
import type { DetailTab } from "../components/VideoDetailPanel";
import { safeLocalStorage } from "../utils/safeStorage";
import "./workspace-clean.css";
import "./channel-clean.css";
import "./audio-clean.css";
import "./thumbnail-clean.css";

export type ReadyFilter = "all" | "ready" | "not_ready";

export type WorkspaceView =
  | "dashboard"
  | "channel"
  | "channelVideo"
  | "research"
  | "thumbnails"
  | "channelWorkspace"
  | "channelSettings"
  | "promptManager"
  | "scriptFactory"
  | "audioReview"
  | "capcutEdit"
  | "audioTtsV2"
  | "audioIntegrity"
  | "reports"
  | "jobs"
  | "settings";

export type ShellOutletContext = {
  view: WorkspaceView;
  channels: ChannelSummary[];
  channelsLoading: boolean;
  channelsError: string | null;
  dashboardOverview: DashboardOverview | null;
  dashboardLoading: boolean;
  dashboardError: string | null;
  selectedChannel: string | null;
  selectedChannelSummary: ChannelSummary | null;
  selectedChannelSnapshot: ChannelSnapshot | null;
  selectChannel: (code: string | null) => void;
  selectChannelFromSidebar: (code: string | null) => void;
  navigateToChannel: (code: string) => void;
  videos: VideoSummary[];
  filteredVideos: VideoSummary[];
  videosLoading: boolean;
  videosError: string | null;
  videoKeyword: string;
  setVideoKeyword: (value: string) => void;
  readyFilter: ReadyFilter;
  setReadyFilter: (value: ReadyFilter) => void;
  summaryFilter: "blocked" | "review" | "pendingAudio" | null;
  applySummaryFilter: (value: "blocked" | "review" | "pendingAudio" | null) => void;
  clearSummaryFilter: () => void;
  selectedVideo: string | null;
  selectVideo: (video: string) => void;
  openScript: (video: string) => void;
  openAudio: (video: string) => void;
  videoDetail: VideoDetail | null;
  detailLoading: boolean;
  detailError: string | null;
  detailTab: DetailTab;
  setDetailTab: (tab: DetailTab) => void;
  shouldShowDetailPanel: boolean;
  detailHandlers: DetailHandlers | null;
  hasUnsavedChanges: boolean;
  setHasUnsavedChanges: (dirty: boolean) => void;
  activityItems: ActivityItem[];
  handleFocusAudioBacklog: (code: string | null) => void;
  handleFocusNeedsAttention: (code?: string | null) => void;
  placeholderPanel: PlaceholderCopy | null;
};

export type DetailHandlers = {
  onSaveAssembled: (content: string) => Promise<unknown>;
  onSaveTts: (request: {
    plainContent?: string;
    taggedContent?: string;
    mode: "plain" | "tagged";
    regenerateAudio: boolean;
    updateAssembled: boolean;
  }) => Promise<TtsSaveResponse>;
  onValidateTts: (content: string) => Promise<TtsValidationResponse>;
  onSaveSrt: (content: string) => Promise<unknown>;
  onVerifySrt: (tolerance?: number) => Promise<SrtVerifyResponse>;
  onUpdateStatus: (status: string) => Promise<unknown>;
  onUpdateReady: (ready: boolean) => Promise<unknown>;
  onUpdateStages: (stages: Record<string, string>) => Promise<unknown>;
  onReplaceTts: (request: {
    original: string;
    replacement: string;
    scope: "first" | "all";
    updateAssembled: boolean;
    regenerateAudio: boolean;
  }) => Promise<TtsReplaceResponse>;
};

export type ChannelSnapshot = {
  total: number;
  scriptCompleted: number;
  audioSubtitleCompleted: number;
  readyForAudio: number;
  audioSubtitleBacklog: number;
};

const READY_FILTER_VALUES: ReadyFilter[] = ["all", "ready", "not_ready"];
const DETAIL_TAB_VALUES: DetailTab[] = ["overview", "script", "audio", "history"];
const COMPLETED_STATUSES = new Set(["completed", "skipped"]);
const SCRIPT_STAGE_KEYS = [
  "script_polish_ai",
  "script_validation",
  "script_review",
  "script_enhancement",
  "script_draft",
  "script_outline",
];

function sanitizeReadyFilter(value: string | null): ReadyFilter {
  if (!value) {
    return "all";
  }
  if ((READY_FILTER_VALUES as readonly string[]).includes(value)) {
    return value as ReadyFilter;
  }
  return "all";
}

function sanitizeDetailTabParam(value: string | null): DetailTab | null {
  if (!value) {
    return null;
  }
  if (DETAIL_TAB_VALUES.includes(value as DetailTab)) {
    return value as DetailTab;
  }
  return null;
}

function safeGet(key: string): string | null {
  try {
    return safeLocalStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSet(key: string, value: string): void {
  try {
    safeLocalStorage.setItem(key, value);
  } catch {
    /* no-op */
  }
}
function safeRemove(key: string): void {
  try {
    safeLocalStorage.removeItem(key);
  } catch {
    /* no-op */
  }
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

function determineView(pathname: string): WorkspaceView {
  if (matchPath("/channels/:channelCode/videos/:video", pathname)) {
    return "channelVideo";
  }
  if (matchPath("/channels/:channelCode", pathname)) {
    return "channel";
  }
  if (matchPath("/channel-workspace", pathname)) {
    return "channelWorkspace";
  }
  if (matchPath("/channel-settings", pathname)) {
    return "channelSettings";
  }
  if (matchPath("/projects", pathname)) {
    return "scriptFactory";
  }
  if (matchPath("/jobs", pathname)) {
    return "jobs";
  }
  if (matchPath("/research", pathname)) {
    return "research";
  }
  if (matchPath("/thumbnails", pathname)) {
    return "thumbnails";
  }
  if (matchPath("/prompts", pathname)) {
    return "promptManager";
  }
  if (matchPath("/settings", pathname)) {
    return "settings";
  }
  if (matchPath("/audio-review", pathname)) {
    return "audioReview";
  }
  if (matchPath("/capcut-edit/*", pathname) || matchPath("/capcut-edit", pathname)) {
    return "capcutEdit";
  }
  if (matchPath("/audio-tts-v2", pathname)) {
    return "audioTtsV2";
  }
  if (matchPath("/audio-integrity", pathname)) {
    return "audioIntegrity";
  }
  if (matchPath("/reports", pathname)) {
    return "reports";
  }
  return "dashboard";
}

const PLACEHOLDER_COPY: Record<Exclude<WorkspaceView, "dashboard" | "channel" | "channelVideo">, PlaceholderCopy> = {
  scriptFactory: {
    title: "台本作成（バッチ）",
    description: "progress/channels/CHxx.csv（planning_store）を直接参照し、作成フラグや進捗に応じて案件を量産キューへ送り込むための一覧です。",
  },
  promptManager: {
    title: "プロンプト管理",
    description: "Qwen 初期プロンプトなどのテンプレを UI から編集し、ルート prompts/ と commentary_01/prompts/ を同期させます。",
  },
  settings: {
    title: "設定",
    description: "OpenAI / OpenRouter の APIキーや既定モデルを管理し、最新のマルチモーダル構成に切り替えます。",
  },
  channelWorkspace: {
    title: "台本・音声字幕管理",
    description: "既存の台本・音声・字幕成果物を編集し、Ready 状態を調整します。",
  },
  channelSettings: {
    title: "チャンネル詳細設定",
    description: "企画テンプレやペルソナ、planning 行などチャンネル固有の SSOT 情報をまとめて確認・編集できます。",
  },
  research: {
    title: "リサーチハブ",
    description: "00_research の成果物や調査ログを参照し、重要なインサイトを確認できます。",
  },
  thumbnails: {
    title: "サムネイル管理",
    description: "サムネイル案のステータスや採用状況を整理し、ドラフトの差し替えを素早く行えます。",
  },
  jobs: {
    title: "バッチ実行",
    description: "音声やスクリプトのジョブをキューに入れて並列制御します。（将来のバッチUI用プレースホルダー）",
  },
  audioReview: {
    title: "音声レビュー",
    description: "完成済み音声を横断的にチェックし、再生成や字幕調整の必要な案件を把握できます。",
  },
  capcutEdit: {
    title: "CapCut編集",
    description: "CapCutドラフトの新規作成と、既存ドラフトの画像差し替えをまとめたビューです。",
  },
  audioTtsV2: {
    title: "Audio TTS v2",
    description: "audio_tts_v2 パイプラインを UI から実行し、WAV/SRT を生成します。",
  },
  audioIntegrity: {
    title: "音声アセット整合性",
    description: "audio_prep に必須ファイル (b_text_with_pauses.txt / WAV / SRT) が揃っているか、音声とSRTの長さが一致しているかを一覧で確認します。",
  },
  reports: {
    title: "レポート",
    description: "チャネル横断の指標や定期レポートを集計中です。暫定的にダッシュボードをご利用ください。",
  },
};

export type PlaceholderCopy = {
  title: string;
  description: string;
};

export type ActivityItem = {
  title: string;
  description?: string;
  timestamp?: string;
};

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = useMemo(() => determineView(location.pathname), [location.pathname]);

  const [channels, setChannels] = useState<ChannelSummary[]>([]);
  const [channelsLoading, setChannelsLoading] = useState(false);
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [videosLoading, setVideosLoading] = useState(false);
  const [videosError, setVideosError] = useState<string | null>(null);

  const [videoDetail, setVideoDetail] = useState<VideoDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  const [dashboardOverview, setDashboardOverview] = useState<DashboardOverview | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);

  const [selectedChannel, setSelectedChannel] = useState<string | null>(() => {
    if (typeof window === "undefined") {
      return null;
    }
    return safeGet("ui.channel.selected");
  });
  const [selectedVideo, setSelectedVideo] = useState<string | null>(null);

  const [videoKeyword, setVideoKeyword] = useState(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return safeGet("ui.video.keyword") ?? "";
  });
  const [readyFilter, setReadyFilterState] = useState<ReadyFilter>(() => {
    if (typeof window === "undefined") {
      return "all";
    }
    return sanitizeReadyFilter(safeGet("ui.video.readyFilter"));
  });
  const [summaryFilter, setSummaryFilter] = useState<"blocked" | "review" | "pendingAudio" | null>(null);
  const pendingAudioReadyFilterRef = useRef<ReadyFilter>("all");

  const [detailTab, setDetailTabState] = useState<DetailTab>(() => {
    if (typeof window === "undefined") {
      return "script";
    }
    const stored = sanitizeDetailTabParam(safeGet("ui.detail.tab"));
    return stored ?? "script";
  });
  const applyDetailTab = useCallback(
    (tab: DetailTab, options?: { syncUrl?: boolean }) => {
      setDetailTabState(tab);
      if (options?.syncUrl === false || view !== "channelVideo") {
        return;
      }
      const nextParams = new URLSearchParams(searchParams);
      const currentParam = nextParams.get("tab");
      if (tab === "script") {
        if (currentParam !== null) {
          nextParams.delete("tab");
          setSearchParams(nextParams, { replace: true });
        }
      } else if (currentParam !== tab) {
        nextParams.set("tab", tab);
        setSearchParams(nextParams, { replace: true });
      }
    },
    [view, searchParams, setSearchParams]
  );
  const previousChannelRef = useRef<string | null>(selectedChannel);

  const channelVideoMatch = matchPath("/channels/:channelCode/videos/:video", location.pathname);
  const channelMatch = matchPath("/channels/:channelCode", location.pathname);
  const routeChannelCode = channelVideoMatch?.params.channelCode ?? channelMatch?.params.channelCode ?? null;
  const routeVideoNumber = channelVideoMatch?.params.video ?? null;

  const refreshChannels = useCallback(async () => {
    setChannelsLoading(true);
    setChannelsError(null);
    try {
      const data = await fetchChannels();
      setChannels(data);
      setSelectedChannel((current) => {
        if (routeChannelCode) {
          return routeChannelCode;
        }
        if (current && data.some((item) => item.code === current)) {
          return current;
        }
        return data[0]?.code ?? null;
      });
    } catch (error) {
      setChannelsError(error instanceof Error ? error.message : String(error));
    } finally {
      setChannelsLoading(false);
    }
  }, [routeChannelCode]);

  const refreshVideos = useCallback(
    async (channel: string) => {
      setVideosLoading(true);
      setVideosError(null);
      try {
        const data = await fetchVideos(channel);
        setVideos(data);
        setSelectedVideo((current) => {
          if (routeVideoNumber) {
            return routeVideoNumber;
          }
          if (current && data.some((item) => item.video === current)) {
            return current;
          }
          return data[0]?.video ?? null;
        });
      } catch (error) {
        setVideosError(error instanceof Error ? error.message : String(error));
        setVideos([]);
        setSelectedVideo(null);
      } finally {
        setVideosLoading(false);
      }
    },
    [routeVideoNumber]
  );

  const refreshDetail = useCallback(
    async (channel: string, video: string) => {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const data = await fetchVideoDetail(channel, video);
        setVideoDetail(data);
      } catch (error) {
        setDetailError(error instanceof Error ? error.message : String(error));
        setVideoDetail(null);
      } finally {
        setDetailLoading(false);
      }
    },
    []
  );

  const refreshDashboardOverview = useCallback(async () => {
    setDashboardLoading(true);
    setDashboardError(null);
    try {
      const data = await fetchDashboardOverview();
      setDashboardOverview(data);
    } catch (error) {
      setDashboardError(error instanceof Error ? error.message : String(error));
      setDashboardOverview(null);
    } finally {
      setDashboardLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshChannels();
    refreshDashboardOverview();
  }, [refreshChannels, refreshDashboardOverview]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    safeSet("ui.video.keyword", videoKeyword);
  }, [videoKeyword]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    safeSet("ui.video.readyFilter", readyFilter);
  }, [readyFilter]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (selectedChannel) {
      safeSet("ui.channel.selected", selectedChannel);
    } else {
      safeRemove("ui.channel.selected");
    }
  }, [selectedChannel]);

  useEffect(() => {
    const previous = previousChannelRef.current;
    previousChannelRef.current = selectedChannel;
    if (!selectedChannel) {
      setVideos([]);
      setSelectedVideo(null);
      setVideoDetail(null);
      setSummaryFilter(null);
      if (previous) {
        setVideoKeyword("");
        setReadyFilterState("all");
      }
      return;
    }
    setSummaryFilter(null);
    refreshVideos(selectedChannel);
  }, [selectedChannel, refreshVideos]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) {
      setVideoDetail(null);
      return;
    }
    refreshDetail(selectedChannel, selectedVideo);
  }, [selectedChannel, selectedVideo, refreshDetail]);

  const filteredVideos = useMemo(() => {
    const keyword = videoKeyword.trim().toLowerCase();
    return videos.filter((video) => {
      const audioState = resolveAudioSubtitleState(video);
      const isReadyState = audioState !== "pending";
      const keywordMatch = keyword
        ? video.video.toLowerCase().includes(keyword) || (video.title ?? "").toLowerCase().includes(keyword)
        : true;
      const readyMatch =
        readyFilter === "all"
          ? true
          : readyFilter === "ready"
            ? isReadyState
            : !isReadyState;
      const summaryMatch =
        summaryFilter === null
          ? true
          : summaryFilter === "blocked"
            ? Object.values(video.stages ?? {}).some((status) => status === "blocked")
            : summaryFilter === "review"
              ? Object.values(video.stages ?? {}).some((status) => status === "review")
              : audioState === "pending";
      return keywordMatch && readyMatch && summaryMatch;
    });
  }, [videos, videoKeyword, readyFilter, summaryFilter]);

  const channelSummaryMap = useMemo(() => {
    const map = new Map<string, ChannelSummary>();
    channels.forEach((item) => {
      map.set(item.code, item);
    });
    return map;
  }, [channels]);

  const selectedChannelSummary = useMemo(() => {
    if (!selectedChannel) {
      return null;
    }
    return channelSummaryMap.get(selectedChannel) ?? null;
  }, [channelSummaryMap, selectedChannel]);

  const selectedChannelSnapshot: ChannelSnapshot | null = useMemo(() => {
    if (!selectedChannel) {
      return null;
    }
    const total = videos.length;
    if (total === 0) {
      return {
        total: 0,
        scriptCompleted: 0,
        audioSubtitleCompleted: 0,
        readyForAudio: 0,
        audioSubtitleBacklog: 0,
      };
    }
    let scriptCompleted = 0;
    let audioCompleted = 0;
    let readyForAudio = 0;
    videos.forEach((video) => {
      const stages = video.stages ?? {};
      if (SCRIPT_STAGE_KEYS.some((key) => COMPLETED_STATUSES.has((stages[key] ?? "").toLowerCase()))) {
        scriptCompleted += 1;
      }
      const audioState = resolveAudioSubtitleState(video);
      if (audioState === "completed") {
        audioCompleted += 1;
      } else if (audioState === "ready") {
        readyForAudio += 1;
      }
    });
    const audioSubtitleBacklog = Math.max(total - audioCompleted - readyForAudio, 0);
    return {
      total,
      scriptCompleted,
      audioSubtitleCompleted: audioCompleted,
      readyForAudio,
      audioSubtitleBacklog,
    };
  }, [selectedChannel, videos]);

  const activityItems = useMemo<ActivityItem[]>(() => {
    if (!videoDetail) {
      return [];
    }
    const items: ActivityItem[] = [];
    const currentStage = pickCurrentStage(videoDetail.stages ?? {});
    if (currentStage) {
      const status = resolveStageStatus(currentStage, videoDetail.stages ?? {});
      items.push({
        title: `現在のステージ: ${STAGE_LABELS[currentStage] ?? currentStage}`,
        description: `状態: ${translateStatus(status)}`,
      });
    }
    const detailAudioState = resolveAudioSubtitleState(videoDetail);
    const detailAudioLabel =
      detailAudioState === "completed" ? "完了" : detailAudioState === "ready" ? "準備済み" : "未準備";
    items.push({
      title: `案件ステータス: ${translateStatus(videoDetail.status)}`,
      description: `音声・字幕: ${detailAudioLabel}`,
    });
    if (videoDetail.audio_updated_at) {
      items.push({
        title: "音声ファイル更新",
        description: videoDetail.audio_duration_seconds
          ? `長さ ${videoDetail.audio_duration_seconds.toFixed(1)} 秒`
          : undefined,
        timestamp: formatDateTime(videoDetail.audio_updated_at),
      });
    }
    if (videoDetail.audio_quality_status) {
      items.push({
        title: `品質ステータス: ${videoDetail.audio_quality_status}`,
        description: videoDetail.audio_quality_summary ?? undefined,
        timestamp: formatDateTime(videoDetail.audio_updated_at),
      });
    }
    return items;
  }, [videoDetail]);

  useEffect(() => {
    if (!routeChannelCode) {
      return;
    }
    if (routeChannelCode !== selectedChannel) {
      setSelectedChannel(routeChannelCode);
    }
  }, [routeChannelCode, selectedChannel]);

  useEffect(() => {
    if (!routeVideoNumber) {
      return;
    }
    if (routeVideoNumber !== selectedVideo) {
      setSelectedVideo(routeVideoNumber);
    }
  }, [routeVideoNumber, selectedVideo]);

  useEffect(() => {
    if (view !== "audioReview" && view !== "scriptFactory" && view !== "channelSettings") {
      return;
    }
    const params = new URLSearchParams(location.search);
    const channelParam = params.get("channel");
    const videoParam = params.get("video");
    if (channelParam) {
      const normalizedChannel = channelParam.trim().toUpperCase();
      if (normalizedChannel && normalizedChannel !== selectedChannel) {
        setSelectedChannel(normalizedChannel);
      }
    }
    if (videoParam) {
      const normalizedVideo = videoParam.trim();
      if (normalizedVideo && normalizedVideo !== selectedVideo) {
        setSelectedVideo(normalizedVideo);
      }
    }
  }, [location.search, selectedChannel, selectedVideo, view]);

  useEffect(() => {
    if (view !== "channelVideo") {
      setDetailTabState((current) => (current === "script" ? current : "script"));
      return;
    }
    const tabFromUrl = sanitizeDetailTabParam(searchParams.get("tab"));
    const normalized = tabFromUrl ?? "script";
    setDetailTabState((current) => (current === normalized ? current : normalized));
  }, [view, searchParams]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      safeSet("ui.detail.tab", detailTab);
    }
  }, [detailTab]);

  const handleSelectChannel = useCallback(
    (code: string | null) => {
      setSelectedChannel(code);
      setSelectedVideo(null);
      setVideoDetail(null);
      applyDetailTab("script");
      if (!code) {
        if (view !== "scriptFactory") {
          navigate("/dashboard");
        } else if (location.pathname !== "/projects") {
          navigate("/projects");
        }
        return;
      }
      if (view === "scriptFactory") {
        if (location.pathname !== "/projects") {
          navigate("/projects");
        }
        return;
      }
      navigate(`/channels/${encodeURIComponent(code)}`);
    },
    [applyDetailTab, location.pathname, navigate, view]
  );

  const handleSidebarChannelSelect = useCallback(
    (code: string | null) => {
      if (!code) {
        if (view !== "dashboard") {
          handleSelectChannel(null);
        }
        return;
      }
      handleSelectChannel(code);
    },
    [handleSelectChannel, view]
  );

  const handleDashboardSelectChannel = useCallback(
    (code: string) => {
      navigate(`/channels/${encodeURIComponent(code)}`);
    },
    [navigate]
  );

  const handleFocusAudioBacklog = useCallback(
    (code: string | null) => {
      const params = new URLSearchParams();
      params.set("filter", "pendingAudio");
      if (code) {
        navigate(`/channels/${encodeURIComponent(code)}?${params.toString()}`);
      } else {
        navigate(`/projects?${params.toString()}`);
      }
    },
    [navigate]
  );

  const handleFocusNeedsAttention = useCallback(
    (code?: string | null) => {
      const params = new URLSearchParams();
      params.set("filter", "blocked");
      if (code) {
        navigate(`/channels/${encodeURIComponent(code)}?${params.toString()}`);
      } else {
        navigate(`/projects?${params.toString()}`);
      }
    },
    [navigate]
  );

  const handleKeywordChange = useCallback((value: string) => {
    setVideoKeyword(value);
  }, []);

  const handleReadyFilterChange = useCallback(
    (value: ReadyFilter) => {
      setReadyFilterState(value);
      if (value !== "not_ready" && summaryFilter === "pendingAudio") {
        setSummaryFilter(null);
      }
    },
    [summaryFilter]
  );

  const handleClearSummaryFilter = useCallback(() => {
    setSummaryFilter((current) => {
      if (current === "pendingAudio") {
        setReadyFilterState(pendingAudioReadyFilterRef.current);
      }
      return null;
    });
  }, []);

  const applySummaryFilter = useCallback(
    (value: "blocked" | "review" | "pendingAudio" | null) => {
      if (value === null) {
        handleClearSummaryFilter();
        return;
      }
      if (value === "pendingAudio") {
        setSummaryFilter(() => "pendingAudio");
        setReadyFilterState((current) => {
          if (current !== "not_ready") {
            pendingAudioReadyFilterRef.current = current;
            return "not_ready";
          }
          return current;
        });
      } else {
        setSummaryFilter(value);
      }
    },
    [handleClearSummaryFilter]
  );

  const buildChannelVideoUrl = useCallback(
    (videoId: string, tab?: string) => {
      const code = selectedChannel ?? routeChannelCode ?? null;
      if (!code) {
        return null;
      }
      const params = new URLSearchParams();
      if (tab) {
        params.set("tab", tab);
      }
      const query = params.toString();
      return `/channels/${encodeURIComponent(code)}/videos/${encodeURIComponent(videoId)}${query ? `?${query}` : ""}`;
    },
    [routeChannelCode, selectedChannel]
  );

  const handleSelectListVideo = useCallback(
    (video: string) => {
      setSelectedVideo(video);
      const url = buildChannelVideoUrl(video);
      if (url) {
        navigate(url);
      }
    },
    [buildChannelVideoUrl, navigate]
  );

  const handleOpenScript = useCallback(
    (video: string) => {
      setSelectedVideo(video);
      applyDetailTab("script");
      const url = buildChannelVideoUrl(video, "script");
      if (url) {
        navigate(url);
      }
    },
    [applyDetailTab, buildChannelVideoUrl, navigate]
  );

  const handleOpenAudio = useCallback(
    (video: string) => {
      setSelectedVideo(video);
      applyDetailTab("audio");
      const url = buildChannelVideoUrl(video, "audio");
      if (url) {
        navigate(url);
      }
    },
    [applyDetailTab, buildChannelVideoUrl, navigate]
  );

  const perform = useCallback(
    async (task: () => Promise<unknown>): Promise<unknown> => {
      if (!selectedChannel || !selectedVideo) {
        return;
      }
      try {
        const result = await task();
        await refreshDetail(selectedChannel, selectedVideo);
        await refreshVideos(selectedChannel);
        return result;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (message.includes("最新の情報を再取得")) {
          await refreshDetail(selectedChannel, selectedVideo);
          await refreshVideos(selectedChannel);
        }
        throw error;
      }
    },
    [refreshDetail, refreshVideos, selectedChannel, selectedVideo]
  );

  const detailHandlers = useMemo(() => {
    if (!selectedChannel || !selectedVideo) {
      return null;
    }
    const versionToken = videoDetail?.updated_at ?? null;
    return {
      onSaveAssembled: (content: string) =>
        perform(() => updateAssembled(selectedChannel, selectedVideo, content, versionToken)),
      onSaveTts: (request: {
        plainContent?: string;
        taggedContent?: string;
        mode: "plain" | "tagged";
        regenerateAudio: boolean;
        updateAssembled: boolean;
      }) =>
        perform(() =>
          updateTts(
            selectedChannel,
            selectedVideo,
            {
              plainContent: request.plainContent,
              taggedContent: request.taggedContent,
              contentMode: request.mode,
              regenerateAudio: request.regenerateAudio,
              updateAssembled: request.updateAssembled,
            },
            versionToken
          )
        ) as Promise<TtsSaveResponse>,
      onValidateTts: (content: string): Promise<TtsValidationResponse> =>
        validateTts(selectedChannel, selectedVideo, content),
      onSaveSrt: (content: string) =>
        perform(() => updateSrt(selectedChannel, selectedVideo, content, versionToken)),
      onVerifySrt: (toleranceMs?: number): Promise<SrtVerifyResponse> =>
        verifySrt(selectedChannel, selectedVideo, toleranceMs ?? 50),
      onUpdateStatus: (status: string) =>
        perform(() => updateStatus(selectedChannel, selectedVideo, status, versionToken)),
      onUpdateReady: (ready: boolean) =>
        perform(() => updateReady(selectedChannel, selectedVideo, ready, versionToken)),
      onUpdateStages: (stages: Record<string, string>) =>
        perform(() => updateStages(selectedChannel, selectedVideo, stages, versionToken)),
      onReplaceTts: (request: {
        original: string;
        replacement: string;
        scope: "first" | "all";
        updateAssembled: boolean;
        regenerateAudio: boolean;
      }) =>
        perform(() =>
          replaceTtsSegment(selectedChannel, selectedVideo, {
            ...request,
            expected_updated_at: versionToken,
          })
        ) as Promise<TtsReplaceResponse>,
    } satisfies DetailHandlers;
  }, [perform, selectedChannel, selectedVideo, videoDetail?.updated_at]);

  const placeholderPanel = useMemo(() => {
    if (view === "dashboard" || view === "channel" || view === "channelVideo") {
      return null;
    }
    return PLACEHOLDER_COPY[view as keyof typeof PLACEHOLDER_COPY] ?? null;
  }, [view]);

  const shouldShowDetailPanel = useMemo(
    () => Boolean(view === "channelVideo" && selectedChannel && selectedVideo && videoDetail),
    [view, selectedChannel, selectedVideo, videoDetail]
  );

  const contextValue: ShellOutletContext = {
    view,
    channels,
    channelsLoading,
    channelsError,
    dashboardOverview,
    dashboardLoading,
    dashboardError,
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
    selectChannel: handleSelectChannel,
    selectChannelFromSidebar: handleSidebarChannelSelect,
    navigateToChannel: handleDashboardSelectChannel,
    videos,
    filteredVideos,
    videosLoading,
    videosError,
    videoKeyword,
    setVideoKeyword: handleKeywordChange,
    readyFilter,
    setReadyFilter: handleReadyFilterChange,
    summaryFilter,
    applySummaryFilter,
    clearSummaryFilter: handleClearSummaryFilter,
    selectedVideo,
    selectVideo: handleSelectListVideo,
    openScript: handleOpenScript,
    openAudio: handleOpenAudio,
    videoDetail,
    detailLoading,
    detailError,
    detailTab,
    setDetailTab: applyDetailTab,
    shouldShowDetailPanel,
    detailHandlers,
    hasUnsavedChanges,
    setHasUnsavedChanges,
    activityItems,
    handleFocusAudioBacklog,
    handleFocusNeedsAttention,
    placeholderPanel,
  };

  // ★ここを修正: URLを /audio-integrity/{channel}/{video} にできるようにする
  // まずはサイドバーのリンク先を現在選択中のチャンネル・動画にする
  const audioIntegrityLink = useMemo(() => {
    if (selectedChannel && selectedVideo) {
      // 本来は /audio-integrity?channel=...&video=... とするか
      // ルーティング側でパラメータを受け取る形にするのがベストだが、
      // ここでは簡易的に現在の選択状態を引き継ぐクエリパラメータ付きリンクにする
      return `/audio-integrity?channel=${selectedChannel}&video=${selectedVideo}`;
    }
    return "/audio-integrity";
  }, [selectedChannel, selectedVideo]);


  const navItems = useMemo(
    () => [
      { key: "dashboard" as WorkspaceView, label: "ダッシュボード", icon: "📊", path: "/dashboard" },
      { key: "research" as WorkspaceView, label: "リサーチ", icon: "🧪", path: "/research" },
      { key: "thumbnails" as WorkspaceView, label: "サムネイル", icon: "🖼️", path: "/thumbnails" },
      { key: "promptManager" as WorkspaceView, label: "プロンプト", icon: "🗒️", path: "/prompts" },
      { key: "jobs" as WorkspaceView, label: "ジョブ管理", icon: "🛰️", path: "/jobs" },
      { key: "settings" as WorkspaceView, label: "設定", icon: "🛠️", path: "/settings" },
      { key: "channelSettings" as WorkspaceView, label: "チャンネル詳細設定", icon: "⚙️", path: "/channel-settings" },
      { key: "scriptFactory" as WorkspaceView, label: "台本作成", icon: "📝", path: "/projects" },
      { key: "channelWorkspace" as WorkspaceView, label: "台本・音声字幕管理", icon: "🎛️", path: "/channel-workspace" },
      { key: "audioReview" as WorkspaceView, label: "音声レビュー", icon: "🎧", path: "/audio-review" },
      { key: "capcutEdit" as WorkspaceView, label: "CapCut編集", icon: "🎬", path: "/capcut-edit" },
      { key: "audioTtsV2" as WorkspaceView, label: "Audio TTS v2", icon: "🔊", path: "/audio-tts-v2" },
      { key: "audioIntegrity" as WorkspaceView, label: "音声整合性", icon: "🩺", path: audioIntegrityLink }, // ★動的リンク
      { key: "reports" as WorkspaceView, label: "レポート", icon: "📈", path: "/reports" },
    ],
    [audioIntegrityLink]
  );

  const navPrimary = navItems;

  const channelStats = dashboardOverview?.channels;
  const workspaceModifiers: string[] = [];
  if (view === "thumbnails") {
    workspaceModifiers.push("workspace--thumbnail-clean");
  }
  const workspaceClass = ["workspace", ...workspaceModifiers].join(" ");

  return (
    <div className="app-shell">
      <div className={workspaceClass}>
        <aside className="shell-sidebar">
          <div className="shell-sidebar__header">
            <div className="shell-sidebar__brand">
              <span className="shell-avatar" aria-hidden>
                QC
              </span>
              <div>
                <h2 className="shell-sidebar__title">AI 制作スタジオ</h2>
                <p className="shell-sidebar__subtitle">品質管理コンソール</p>
              </div>
            </div>
          </div>

          <nav className="shell-nav" aria-label="主要メニュー">
            {navPrimary.map((item) => {
              const isChannelsPath =
                location.pathname.startsWith("/channels") || location.pathname.startsWith("/channel-workspace");
              const isChannelWorkspaceItem = item.key === "channelWorkspace";
              return (
                <NavLink
                  key={item.key}
                  to={item.path}
                  className={({ isActive }) => {
                    const active =
                      isActive ||
                      (isChannelWorkspaceItem && isChannelsPath) || 
                      (item.key === "audioIntegrity" && location.pathname === "/audio-integrity"); // パラメータ付きでもアクティブにする
                    return active ? "shell-nav__item shell-nav__item--active" : "shell-nav__item";
                  }}
                >
                  <span className="shell-nav__icon" aria-hidden>
                    {item.icon}
                  </span>
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
          </nav>


          <div className="shell-sidebar__content">
            <div className="shell-sidebar__scroll">
              <section className="shell-panel shell-panel--sidebar">
                <header className="shell-panel__header">
                  <div>
                    <h2 className="shell-panel__title">関連シート</h2>
                  </div>
                </header>
                <ul className="sidebar-link-list">
                  <li>
                    <a
                      className="sidebar-link"
                      href="https://docs.google.com/spreadsheets/d/1BABrIWO68_7GVSnBZUgi8YUt6eLwdT3KhX8t6N8qohQ/edit?gid=0"
                      target="_blank"
                      rel="noreferrer"
                    >
                      総合管理シート ↗
                    </a>
                  </li>
                  <li>
                    <a
                      className="sidebar-link"
                      href="https://docs.google.com/spreadsheets/d/1tDM0W3qmvfjMGvpo3_6savBHViJ3qTm--Q4O48I0pbY/edit?gid=0"
                      target="_blank"
                      rel="noreferrer"
                    >
                      ベンチマーク分析シート ↗
                    </a>
                  </li>
                </ul>
              </section>

              <ChannelListSection
                variant="sidebar"
                channels={channels}
                channelStats={channelStats}
                selectedChannel={selectedChannel}
                loading={channelsLoading}
                error={channelsError}
                onSelectChannel={handleSidebarChannelSelect}
              />
            </div>
          </div>

          <footer className="shell-sidebar__footer">
            <button type="button" className="shell-footer__link">
              ヘルプセンター
            </button>
            <button type="button" className="shell-footer__link">
              運用ガイド
            </button>
          </footer>
        </aside>

        <main className="workspace__main">
          <Outlet context={contextValue} />
        </main>
      </div>
    </div>
  );
}