import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Outlet,
  useLocation,
  useNavigate,
  useSearchParams,
  matchPath,
} from "react-router-dom";
import { AppSidebar, type NavSection } from "./AppSidebar";
import {
  fetchChannels,
  fetchMeta,
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
  fetchRedoSummary,
} from "../api/client";
import {
  ChannelSummary,
  VideoSummary,
  VideoDetail,
  DashboardOverview,
  MetaResponse,
  TtsSaveResponse,
  TtsReplaceResponse,
  TtsValidationResponse,
  SrtVerifyResponse,
} from "../api/types";
import { translateStatus, STAGE_LABELS } from "../utils/i18n";
import { pickCurrentStage, resolveStageStatus } from "../components/StageProgress";
import { resolveAudioSubtitleState } from "../utils/video";
import type { DetailTab } from "../components/VideoDetailPanel";
import { safeLocalStorage } from "../utils/safeStorage";
import "./workspace-clean.css";
import "./channel-clean.css";
import "./audio-clean.css";
import "./thumbnail-clean.css";
import "./remotion-clean.css";
import "./shell-layout-fixes.css";

export type ReadyFilter = "all" | "ready" | "not_ready";

export type WorkspaceView =
  | "dashboard"
  | "publishingProgress"
  | "audit"
  | "workflow"
  | "studio"
  | "channel"
  | "channelVideo"
  | "channelPortal"
  | "remotion"
  | "benchmarks"
  | "research"
  | "thumbnails"
  | "imageManagement"
  | "channelWorkspace"
  | "channelSettings"
  | "promptManager"
  | "scriptFactory"
  | "audioReview"
  | "capcutEdit"
  | "audioTts"
  | "audioIntegrity"
  | "planning"
  | "dictionary"
  | "agentBoard"
  | "agentOrg"
  | "reports"
  | "jobs"
  | "settings"
  | "modelPolicy"
  | "imageModelRouting"
  | "llmUsage";

export type ShellOutletContext = {
  view: WorkspaceView;
  channels: ChannelSummary[];
  channelsLoading: boolean;
  channelsError: string | null;
  dashboardOverview: DashboardOverview | null;
  dashboardLoading: boolean;
  dashboardError: string | null;
  redoSummary: Record<string, { redo_script: number; redo_audio: number; redo_both: number }>;
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
  refreshCurrentDetail: () => Promise<void>;
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
  publishedCount: number;
  scriptCompleted: number;
  audioSubtitleCompleted: number;
  readyForAudio: number;
  audioSubtitleBacklog: number;
};

const READY_FILTER_VALUES: ReadyFilter[] = ["all", "ready", "not_ready"];
const DETAIL_TAB_VALUES: DetailTab[] = ["overview", "note", "script", "audio", "video", "history"];
const COMPLETED_STATUSES = new Set(["completed", "skipped"]);
const SCRIPT_STAGE_KEYS = [
  "script_polish_ai",
  "script_validation",
  "script_review",
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

function normalizeChannelStorageKey(channel: string): string {
  return channel.trim().toUpperCase();
}

function videoKeywordStorageKey(channel: string): string {
  return `ui.video.keyword.${normalizeChannelStorageKey(channel)}`;
}

function readyFilterStorageKey(channel: string): string {
  return `ui.video.readyFilter.${normalizeChannelStorageKey(channel)}`;
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
  if (matchPath("/audit", pathname)) {
    return "audit";
  }
  if (matchPath("/publishing-progress", pathname)) {
    return "publishingProgress";
  }
  if (matchPath("/channels/:channelCode/videos/:video", pathname)) {
    return "channelVideo";
  }
  if (matchPath("/channels/:channelCode/portal", pathname)) {
    return "channelPortal";
  }
  if (matchPath("/channels/:channelCode", pathname)) {
    return "channel";
  }
  if (matchPath("/studio", pathname)) {
    return "studio";
  }
  if (matchPath("/workflow", pathname)) {
    return "workflow";
  }
  if (matchPath("/channel-workspace", pathname)) {
    return "channelWorkspace";
  }
  if (matchPath("/channel-settings", pathname)) {
    return "channelSettings";
  }
  if (matchPath("/benchmarks", pathname)) {
    return "benchmarks";
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
  if (matchPath("/image-management", pathname)) {
    return "imageManagement";
  }
  if (matchPath("/prompts", pathname)) {
    return "promptManager";
  }
  if (matchPath("/settings", pathname)) {
    return "settings";
  }
  if (matchPath("/model-policy", pathname)) {
    return "modelPolicy";
  }
  if (matchPath("/image-model-routing", pathname)) {
    return "imageModelRouting";
  }
  if (matchPath("/audio-review", pathname)) {
    return "audioReview";
  }
  if (matchPath("/capcut-edit/*", pathname) || matchPath("/capcut-edit", pathname)) {
    return "capcutEdit";
  }
  if (matchPath("/video-remotion", pathname)) {
    return "remotion";
  }
  if (matchPath("/audio-tts", pathname)) {
    return "audioTts";
  }
  if (matchPath("/audio-integrity/:channel/:video", pathname) || matchPath("/audio-integrity", pathname)) {
    return "audioIntegrity";
  }
  if (matchPath("/reports", pathname)) {
    return "reports";
  }
  if (matchPath("/planning", pathname)) {
    return "planning";
  }
  if (matchPath("/dictionary", pathname)) {
    return "dictionary";
  }
  if (matchPath("/agent-board", pathname)) {
    return "agentBoard";
  }
  if (matchPath("/agent-org", pathname)) {
    return "agentOrg";
  }
  if (matchPath("/llm-usage/*", pathname) || matchPath("/llm-usage", pathname)) {
    return "llmUsage";
  }
  return "dashboard";
}

const PLACEHOLDER_COPY: Record<
  Exclude<WorkspaceView, "dashboard" | "channel" | "channelVideo" | "channelPortal">,
  PlaceholderCopy
  > = {
  publishingProgress: {
    title: "投稿進捗",
    description: "Planning CSV（workspaces/planning/channels/CHxx.csv）から、投稿済み（投入済み）フラグを集計して可視化します。",
  },
  audit: {
    title: "監査（欠損チェック / Precheck）",
    description: "チャンネル監査とPrecheckをまとめて確認し、欠損や詰まりを先に潰します。",
  },
  studio: {
    title: "Episode Studio",
    description: "企画→台本→音声→動画を、エピソード単位で“次に押すべきボタン”が分かる形に統合します。",
  },
  workflow: {
    title: "制作フロー",
    description: "企画→台本→音声→動画を、1本単位で迷わず進めるための一本道ビューです。",
  },
  scriptFactory: {
    title: "台本作成（バッチ）",
    description:
      "workspaces/planning/channels/CHxx.csv（Planning SoT）を参照し、作成フラグや進捗に応じて案件を量産キューへ送り込むための一覧です。",
  },
  planning: {
    title: "企画CSVビューア",
    description:
      "workspaces/planning/channels/ 配下のSoTをUIで直接確認し、台本・音声の揺れを防ぎます。台本パスや企画意図も列で確認できます。",
  },
  dictionary: {
    title: "読み辞書 管理",
    description: "グローバル/チャンネル単位の誤読辞書を一括で追加・削除・検索します。誤読発見→即登録のための専用ハブです。",
  },
  agentOrg: {
    title: "AI Org（協調）",
    description: "複数AIエージェントの役割・稼働状態・ロック・メモを確認し、作業衝突を防ぎます。",
  },
  agentBoard: {
    title: "Shared Board",
    description: "ownership/threads/レビュー/申し送りを単一ファイル(SoT)で共有するボードです。",
  },
  promptManager: {
    title: "プロンプト管理",
    description:
      "UIから各種プロンプトを閲覧・編集します（正本: packages/**/prompts/）。ルート prompts/ はUIが参照する“公開プロンプト”の薄いハブです。",
  },
  settings: {
    title: "設定",
    description: "OpenAI / OpenRouter の APIキーや既定モデルを管理し、最新のマルチモーダル構成に切り替えます。",
  },
  modelPolicy: {
    title: "モデル方針（チャンネル別）",
    description: "画像/LLMのモデル選定方針をチャンネル単位で表に固定し、YAML書き換え運用を撲滅します。",
  },
  imageModelRouting: {
    title: "画像モデル設定",
    description: "チャンネル別に、サムネ/動画内画像の生成モデル（provider/variant）を明示的に切り替えます。",
  },
  channelWorkspace: {
    title: "台本・音声字幕管理",
    description: "既存の台本・音声・字幕成果物を編集し、Ready 状態を調整します。",
  },
  channelSettings: {
    title: "チャンネル詳細設定",
    description: "企画テンプレやペルソナ、planning 行などチャンネル固有の SSOT 情報をまとめて確認・編集できます。",
  },
  benchmarks: {
    title: "ベンチマーク",
    description: "チャンネル別の競合チャンネル情報と台本サンプル（SoT: channel_info.json）を、ベンチマークだけに絞って確認・編集できます。",
  },
  research: {
    title: "リサーチハブ",
    description: "workspaces/research の成果物や調査ログを参照し、重要なインサイトを確認できます。",
  },
  thumbnails: {
    title: "サムネイル管理",
    description: "サムネイル案のステータスや採用状況を整理し、ドラフトの差し替えを素早く行えます。",
  },
  imageManagement: {
    title: "画像管理",
    description: "run_dir 単位でモデル/画風/プロンプトを確認し、複数画風の画像バリアントを生成します。",
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
  remotion: {
    title: "Remotion編集",
    description: "Remotion で mp4 を量産し、Google Drive へ保存するためのワークスペースです。（実験/研究ライン）",
  },
  audioTts: {
    title: "Audio TTS",
    description: "audio_tts パイプラインを UI から実行し、WAV/SRT を生成します。",
  },
  audioIntegrity: {
    title: "音声アセット整合性",
    description:
      "final に必須ファイル (audio / srt / a_text.txt / log.json) が揃っているか、音声とSRTの長さが一致しているかを一覧で確認します。",
  },
  reports: {
    title: "レポート",
    description: "チャネル横断の指標や定期レポートを集計中です。暫定的にダッシュボードをご利用ください。",
  },
  llmUsage: {
    title: "LLM Usage",
    description: "LLMログとタスク別オーバーライド設定を確認・変更",
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

  const [meta, setMeta] = useState<MetaResponse | null>(null);

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
  const [redoSummary, setRedoSummary] = useState<Record<string, { redo_script: number; redo_audio: number; redo_both: number }>>({});

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
    const channel = safeGet("ui.channel.selected");
    if (!channel) {
      return "";
    }
    return safeGet(videoKeywordStorageKey(channel)) ?? "";
  });
  const [readyFilter, setReadyFilterState] = useState<ReadyFilter>(() => {
    if (typeof window === "undefined") {
      return "all";
    }
    const channel = safeGet("ui.channel.selected");
    if (!channel) {
      return "all";
    }
    return sanitizeReadyFilter(safeGet(readyFilterStorageKey(channel)));
  });
  const [summaryFilter, setSummaryFilter] = useState<"blocked" | "review" | "pendingAudio" | null>(null);
  const pendingAudioReadyFilterRef = useRef<ReadyFilter>("all");
  const videoKeywordPersistRef = useRef<{ channel: string | null; value: string }>({ channel: null, value: "" });
  const readyFilterPersistRef = useRef<{ channel: string | null; value: ReadyFilter }>({ channel: null, value: "all" });

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
  const channelPortalMatch = matchPath("/channels/:channelCode/portal", location.pathname);
  const channelMatch = matchPath("/channels/:channelCode", location.pathname);
  const routeChannelCode =
    channelVideoMatch?.params.channelCode ?? channelPortalMatch?.params.channelCode ?? channelMatch?.params.channelCode ?? null;
  const routeVideoNumber = channelVideoMatch?.params.video ?? null;

  useEffect(() => {
    let cancelled = false;
    fetchMeta()
      .then((data) => {
        if (cancelled) return;
        setMeta(data);
      })
      .catch(() => {
        if (cancelled) return;
        setMeta(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        return null;
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

  const refreshCurrentDetail = useCallback(async () => {
    if (!selectedChannel || !selectedVideo) {
      return;
    }
    await refreshDetail(selectedChannel, selectedVideo);
  }, [refreshDetail, selectedChannel, selectedVideo]);

  const refreshDashboardOverview = useCallback(async () => {
    setDashboardLoading(true);
    setDashboardError(null);
    try {
      const data = await fetchDashboardOverview();
      setDashboardOverview(data);
      // refresh redo summary (all channels)
      try {
        const rows = await fetchRedoSummary();
        const map: Record<string, { redo_script: number; redo_audio: number; redo_both: number }> = {};
        rows.forEach((r) => {
          map[r.channel] = { redo_script: r.redo_script, redo_audio: r.redo_audio, redo_both: r.redo_both };
        });
        setRedoSummary(map);
      } catch {
        /* non-blocking */
      }
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
    const previous = videoKeywordPersistRef.current;
    videoKeywordPersistRef.current = { channel: selectedChannel, value: videoKeyword };
    if (!selectedChannel) {
      return;
    }
    // Avoid writing the previous channel's keyword into the new channel bucket.
    if (previous.channel !== selectedChannel && previous.value === videoKeyword) {
      return;
    }
    safeSet(videoKeywordStorageKey(selectedChannel), videoKeyword);
  }, [selectedChannel, videoKeyword]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (!selectedChannel) {
      return;
    }
    setVideoKeyword(safeGet(videoKeywordStorageKey(selectedChannel)) ?? "");
    setReadyFilterState(sanitizeReadyFilter(safeGet(readyFilterStorageKey(selectedChannel))));
  }, [selectedChannel]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const previous = readyFilterPersistRef.current;
    readyFilterPersistRef.current = { channel: selectedChannel, value: readyFilter };
    if (!selectedChannel) {
      return;
    }
    // Avoid writing the previous channel's filter into the new channel bucket.
    if (previous.channel !== selectedChannel && previous.value === readyFilter) {
      return;
    }
    safeSet(readyFilterStorageKey(selectedChannel), readyFilter);
  }, [selectedChannel, readyFilter]);

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
        publishedCount: 0,
        scriptCompleted: 0,
        audioSubtitleCompleted: 0,
        readyForAudio: 0,
        audioSubtitleBacklog: 0,
      };
    }
    let publishedCount = 0;
    let scriptCompleted = 0;
    let audioCompleted = 0;
    let readyForAudio = 0;
    videos.forEach((video) => {
      if (Boolean(video.published_lock)) {
        publishedCount += 1;
      }
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
      publishedCount,
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
    if (
      view !== "audioReview" &&
      view !== "scriptFactory" &&
      view !== "channelSettings" &&
      view !== "workflow" &&
      view !== "studio"
    ) {
      return;
    }
    const params = new URLSearchParams(location.search);
    const channelParam = params.get("channel");
    const videoParam = params.get("video");
    // NOTE: query params are treated as an optional override.
    // When absent (e.g. opening `/workflow` from the sidebar), keep the last selection
    // to avoid forcing users to re-pick channel/video every time.
    if (channelParam) {
      const normalizedChannel = channelParam.trim().toUpperCase();
      if (normalizedChannel && normalizedChannel !== selectedChannel) {
        setSelectedChannel(normalizedChannel);
        // If the URL overrides channel without specifying video, clear the video selection
        // to avoid temporarily showing a mismatched episode while the list refreshes.
        if (!videoParam && selectedVideo !== null) {
          setSelectedVideo(null);
        }
        if (videoDetail) {
          setVideoDetail(null);
        }
      }
    }
    if (videoParam) {
      const normalizedVideo = videoParam.trim();
      if (normalizedVideo && normalizedVideo !== selectedVideo) {
        setSelectedVideo(normalizedVideo);
        if (videoDetail) {
          setVideoDetail(null);
        }
      }
    }
  }, [location.search, selectedChannel, selectedVideo, videoDetail, view]);

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
        const currentUrl = `${location.pathname}${location.search}`;
        navigate(url, { replace: currentUrl === url });
      }
    },
    [buildChannelVideoUrl, location.pathname, location.search, navigate]
  );

  const handleOpenScript = useCallback(
    (video: string) => {
      setSelectedVideo(video);
      applyDetailTab("script");
      const url = buildChannelVideoUrl(video, "script");
      if (url) {
        const currentUrl = `${location.pathname}${location.search}`;
        navigate(url, { replace: currentUrl === url });
      }
    },
    [applyDetailTab, buildChannelVideoUrl, location.pathname, location.search, navigate]
  );

  const handleOpenAudio = useCallback(
    (video: string) => {
      setSelectedVideo(video);
      applyDetailTab("audio");
      const url = buildChannelVideoUrl(video, "audio");
      if (url) {
        const currentUrl = `${location.pathname}${location.search}`;
        navigate(url, { replace: currentUrl === url });
      }
    },
    [applyDetailTab, buildChannelVideoUrl, location.pathname, location.search, navigate]
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
    if (view === "dashboard" || view === "channel" || view === "channelVideo" || view === "channelPortal") {
      return null;
    }
    return PLACEHOLDER_COPY[view as keyof typeof PLACEHOLDER_COPY] ?? null;
  }, [view]);

  const shouldShowDetailPanel = useMemo(
    () => Boolean(view === "channelVideo" && selectedChannel && selectedVideo && videoDetail),
    [view, selectedChannel, selectedVideo, videoDetail]
  );

  const contextValue = useMemo<ShellOutletContext>(
    () => ({
      view,
      channels,
      channelsLoading,
      channelsError,
      dashboardOverview,
      dashboardLoading,
      dashboardError,
      redoSummary,
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
      refreshCurrentDetail,
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
    }),
    [
      activityItems,
      applyDetailTab,
      applySummaryFilter,
      channels,
      channelsError,
      channelsLoading,
      dashboardError,
      dashboardLoading,
      dashboardOverview,
      detailHandlers,
      detailError,
      detailLoading,
      detailTab,
      filteredVideos,
      handleClearSummaryFilter,
      handleDashboardSelectChannel,
      handleFocusAudioBacklog,
      handleFocusNeedsAttention,
      handleKeywordChange,
      handleOpenAudio,
      handleOpenScript,
      handleReadyFilterChange,
      handleSelectChannel,
      handleSelectListVideo,
      handleSidebarChannelSelect,
      hasUnsavedChanges,
      placeholderPanel,
      redoSummary,
      refreshCurrentDetail,
      selectedChannel,
      selectedChannelSnapshot,
      selectedChannelSummary,
      selectedVideo,
      setHasUnsavedChanges,
      shouldShowDetailPanel,
      videoDetail,
      videoKeyword,
      videos,
      videosError,
      videosLoading,
      readyFilter,
      summaryFilter,
      view,
    ]
  );

  const audioIntegrityLink = useMemo(() => {
    if (selectedChannel && selectedVideo) {
      return `/audio-integrity/${encodeURIComponent(selectedChannel)}/${encodeURIComponent(selectedVideo)}`;
    }
    return "/audio-integrity";
  }, [selectedChannel, selectedVideo]);

  const channelPortalLink = useMemo(() => {
    const code = selectedChannel ?? routeChannelCode ?? null;
    if (code) {
      return `/channels/${encodeURIComponent(code)}/portal`;
    }
    return "/channel-settings";
  }, [routeChannelCode, selectedChannel]);

  const planningLink = useMemo(() => {
    const code = selectedChannel ?? routeChannelCode ?? safeGet("ui.channel.selected") ?? null;
    if (code) {
      return `/planning?channel=${encodeURIComponent(code)}`;
    }
    return "/planning";
  }, [routeChannelCode, selectedChannel]);

  const thumbnailsLink = useMemo(() => {
    const code = selectedChannel ?? routeChannelCode ?? safeGet("ui.channel.selected") ?? null;
    if (code) {
      return `/thumbnails?channel=${encodeURIComponent(code)}`;
    }
    return "/thumbnails";
  }, [routeChannelCode, selectedChannel]);

  const navSections = useMemo<NavSection[]>(
    () => [
      {
        title: "編集/品質",
        items: [
          { key: "dashboard", label: "ダッシュボード", icon: "📊", path: "/dashboard" },
          { key: "publishingProgress", label: "投稿進捗", icon: "📅", path: "/publishing-progress" },
          { key: "channelWorkspace", label: "台本・音声字幕管理", icon: "🎛️", path: "/channel-workspace" },
          { key: "channelPortal", label: "チャンネルポータル", icon: "🧭", path: channelPortalLink },
          { key: "audioReview", label: "音声レビュー", icon: "🎧", path: "/audio-review" },
          { key: "audioIntegrity", label: "音声整合性", icon: "🩺", path: audioIntegrityLink },
          { key: "dictionary", label: "辞書", icon: "📖", path: "/dictionary" },
        ],
      },
      {
        title: "制作フロー",
        items: [
          { key: "studio", label: "Episode Studio", icon: "🎛️", path: "/studio" },
          { key: "workflow", label: "制作フロー", icon: "🧭", path: "/workflow" },
          { key: "planning", label: "企画CSV", icon: "🗂️", path: planningLink },
          { key: "scriptFactory", label: "台本作成", icon: "📝", path: "/projects" },
          { key: "audioTts", label: "音声生成(TTS)", icon: "🔊", path: "/audio-tts" },
          { key: "capcutEdit", label: "動画(CapCut)", icon: "🎬", path: "/capcut-edit" },
          { key: "thumbnails", label: "サムネ", icon: "🖼️", path: thumbnailsLink },
          { key: "imageTimeline", label: "画像タイムライン", icon: "🕒", path: "/image-timeline" },
          { key: "imageManagement", label: "画像管理", icon: "🗃️", path: "/image-management" },
        ],
      },
      {
        title: "運用/設定",
        items: [
          { key: "ssot", label: "SSOT", icon: "📌", path: "/ssot" },
          { key: "research", label: "リサーチ", icon: "🧪", path: "/research" },
          { key: "benchmarks", label: "ベンチマーク", icon: "📚", path: "/benchmarks" },
          { key: "remotion", label: "Remotion（実験）", icon: "🎞️", path: "/video-remotion" },
          { key: "jobs", label: "ジョブ管理", icon: "🛰️", path: "/jobs" },
          { key: "agentOrg", label: "AI Org", icon: "🤖", path: "/agent-org" },
          { key: "agentBoard", label: "Shared Board", icon: "🧷", path: "/agent-board" },
          { key: "promptManager", label: "プロンプト", icon: "🗒️", path: "/prompts" },
          { key: "llmUsageDashboard", label: "LLMコスト", icon: "🧮", path: "/llm-usage/dashboard" },
          { key: "llmUsage", label: "LLMログ/Override", icon: "🧠", path: "/llm-usage" },
          { key: "modelPolicy", label: "モデル方針", icon: "📋", path: "/model-policy" },
          { key: "imageModelRouting", label: "画像モデル", icon: "🎨", path: "/image-model-routing" },
          { key: "channelSettings", label: "チャンネル設定", icon: "⚙️", path: "/channel-settings" },
          { key: "settings", label: "設定", icon: "🛠️", path: "/settings" },
          { key: "reports", label: "レポート", icon: "📈", path: "/reports" },
        ],
      },
    ],
    [audioIntegrityLink, channelPortalLink, planningLink, thumbnailsLink]
  );

  const workspaceModifiers: string[] = [];
  if (view === "thumbnails") {
    workspaceModifiers.push("workspace--thumbnail-clean");
  }
  if (view === "remotion") {
    workspaceModifiers.push("workspace--remotion-clean");
  }
  const workspaceClass = ["workspace", ...workspaceModifiers].join(" ");

  const buildLabel = useMemo(() => {
    const sha = String(meta?.git?.sha ?? "").trim();
    if (!sha) return null;
    const dirtyMark = meta?.git?.dirty ? "*" : "";
    const branch = String(meta?.git?.branch ?? "").trim();
    return branch ? `${sha}${dirtyMark} (${branch})` : `${sha}${dirtyMark}`;
  }, [meta]);

  const repoLabel = useMemo(() => {
    const root = String(meta?.repo_root ?? "").trim();
    if (!root) return null;
    const parts = root.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? null;
  }, [meta]);

  return (
    <div className="app-shell">
      <div className={workspaceClass}>
        <AppSidebar
          navSections={navSections}
          pathname={location.pathname}
          buildLabel={buildLabel}
          repoLabel={repoLabel}
        />

        <main className="workspace__main">
          <Outlet context={contextValue} />
        </main>
      </div>
    </div>
  );
}
