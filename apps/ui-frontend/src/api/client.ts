import {
  ApiErrorShape,
  AudioReviewItem,
  BatchQueueEntry,
  BatchWorkflowLogResponse,
  BatchWorkflowRequestPayload,
  BatchWorkflowTask,
  CapcutInstallResult,
  ChannelProfileResponse,
  ChannelProfileUpdatePayload,
  ChannelSummary,
  DashboardOverview,
  LockMetrics,
  NaturalCommandResponse,
  PersonaDocumentResponse,
  PersonaDocumentUpdatePayload,
  PlanningCreatePayload,
  PlanningCsvRow,
  PlanningTemplateResponse,
  PlanningTemplateUpdatePayload,
  PlanningUpdatePayload,
  PlanningUpdateResponse,
  PlanningSpreadsheetResponse,
  PromptDocumentDetail,
  PromptDocumentSummary,
  PromptUpdatePayload,
  ThumbnailLibraryAsset,
  ThumbnailLibraryAssignResponse,
  ThumbnailDescriptionResponse,
  ThumbnailImageModelInfo,
  ThumbnailChannelTemplates,
  ThumbnailChannelTemplatesUpdate,
  ThumbnailQuickHistoryEntry,
  ThumbnailOverview,
  ThumbnailProjectStatus,
  ThumbnailVariant,
  ThumbnailVariantGeneratePayload,
  ThumbnailVariantComposePayload,
  ThumbnailVariantStatus,
  TtsReplaceRequestPayload,
  TtsReplaceResponse,
  TtsSaveResponse,
  TtsValidationResponse,
  ScriptTextResponse,
  SrtVerifyResponse,
  VideoDetail,
  VideoJobCreatePayload,
  VideoJobRecord,
  VideoProjectCreatePayload,
  VideoProjectCreateResponse,
  VideoProjectDetail,
  VideoProjectImageAsset,
  VideoProjectBeltEntry,
  SourceStatus,
  VideoProjectGuard,
  VideoProjectSummary,
  VideoSummary,
  WorkflowPrecheckResponse,
  VideoProductionChannelPreset,
  ChannelPresetUpdatePayload,
  CapcutDraftSummary,
  CapcutDraftDetail,
  RemotionProjectSummary,
  VideoGenerationOptions,
  VideoProjectCapcutSettings,
  LlmSettings,
  LlmSettingsUpdate,
  LlmModelInfo,
  HumanScriptResponse,
  HumanScriptUpdatePayload,
  RunTtsV2Response,
  AutoDraftListResponse,
  AutoDraftCreateResponse,
  AutoDraftCreatePayload,
  AutoDraftSrtItem,
  AutoDraftSrtContent,
  ProjectSrtContent,
  PromptTemplateContentResponse,
  ResearchFileEntry,
  ResearchListResponse,
  ResearchFileResponse,
  UiParams,
  UiParamsResponse,
  AudioIntegrityItem,
  AudioAnalysis,
  PublishLockPayload,
  PublishLockResponse,
  PublishUnlockResponse,
  RedoUpdatePayload,
  RedoUpdateResponse,
  RedoSummaryItem,
  ThumbnailLookupResponse,
  ScriptManifest,
  LlmArtifactListItem,
  LlmTextArtifact,
  LlmTextArtifactUpdatePayload,
  SrtSegmentsArtifact,
  VisualCuesPlanArtifact,
  VisualCuesPlanUpdatePayload,
} from "./types";

import { apiUrl } from "../utils/apiClient";

export type { VideoJobCreatePayload, VideoProjectCreatePayload } from "./types";

const DEFAULT_API_BASE_URL = ""; // use relative path by default
export const API_BASE_URL = process.env.REACT_APP_API_BASE_URL ?? DEFAULT_API_BASE_URL;

type GitHubRepoInfo = {
  owner: string;
  repo: string;
  branch: string;
};

type GitHubContent = {
  name: string;
  path: string;
  type: "file" | "dir" | string;
  size: number;
  download_url?: string | null;
};

const GITHUB_BASE_DIRS: Record<string, string> = {
  research: "workspaces/research",
  scripts: "workspaces/scripts",
};

type JsonMap = Record<string, unknown>;

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  // GitHub Pages対応：apiUrlヘルパーを使用
  return apiUrl(path);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    // 明示的にキャッシュを無効化し、最新の設定を取得する
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data: ApiErrorShape = await response.json();
      if (data.detail) {
        message = data.detail;
      }
    } catch (error) {
      // no-op: fall back to default message.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function requestForm<T>(path: string, formData: FormData, method: "POST" | "PUT" | "PATCH" = "POST"): Promise<T> {
  const response = await fetch(buildUrl(path), {
    method,
    body: formData,
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data: ApiErrorShape = await response.json();
      if (data.detail) {
        message = data.detail;
      }
    } catch (error) {
      // ignore parse failure
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const response = await fetch(buildUrl(path), init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data: ApiErrorShape = await response.json();
      if (data.detail) {
        message = data.detail;
      }
    } catch (error) {
      // ignore parse failure
    }
    throw new Error(message);
  }
  return response.status === 204 ? "" : (await response.text());
}

function isGitHubPagesHost(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.hostname.endsWith("github.io");
}

function resolveGitHubRepoInfo(): GitHubRepoInfo {
  if (typeof window === "undefined") {
    return {
      owner: process.env.REACT_APP_GITHUB_OWNER ?? "daideguchi",
      repo: process.env.REACT_APP_GITHUB_REPO ?? "youtube_production2",
      branch: process.env.REACT_APP_GITHUB_BRANCH ?? "main",
    };
  }

  const owner = process.env.REACT_APP_GITHUB_OWNER ?? window.location.hostname.split(".")[0];
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const repoFromPath = pathParts.length > 0 ? pathParts[0] : undefined;

  return {
    owner,
    repo: process.env.REACT_APP_GITHUB_REPO ?? repoFromPath ?? "youtube_production2",
    branch: process.env.REACT_APP_GITHUB_BRANCH ?? "main",
  };
}

function cleanPath(path: string): string {
  return path.replace(/^\/+|\/+$/g, "");
}

function joinRepoPath(...parts: string[]): string {
  return parts
    .map(cleanPath)
    .filter(Boolean)
    .join("/");
}

function encodeRepoPath(path: string): string {
  return path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

async function fetchGitHubContents(base: string, relPath: string): Promise<GitHubContent | GitHubContent[]> {
  const repoInfo = resolveGitHubRepoInfo();
  const baseDir = GITHUB_BASE_DIRS[base];
  if (!baseDir) {
    throw new Error("unsupported base for GitHub fetch");
  }

  const repoPath = joinRepoPath(baseDir, relPath);
  const encodedPath = encodeRepoPath(repoPath || baseDir);
  const url = `https://api.github.com/repos/${repoInfo.owner}/${repoInfo.repo}/contents/${encodedPath}?ref=${encodeURIComponent(
    repoInfo.branch
  )}`;

  const response = await fetch(url, {
    headers: {
      Accept: "application/vnd.github+json",
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`GitHub contents error ${response.status}: ${message || response.statusText}`);
  }

  return (await response.json()) as GitHubContent | GitHubContent[];
}

async function fetchGitHubResearchList(base: string, path = ""): Promise<ResearchListResponse> {
  const repoData = await fetchGitHubContents(base, path);
  const currentPath = cleanPath(path);

  const items = Array.isArray(repoData)
    ? repoData
    : repoData.type === "file"
      ? [repoData]
      : [];

  const entries = items
    .map<ResearchFileEntry>((item) => ({
      name: item.name,
      path: cleanPath(joinRepoPath(currentPath, item.name)),
      is_dir: item.type === "dir",
      size: item.size,
    }))
    .sort((a, b) => {
      if (a.is_dir && !b.is_dir) return -1;
      if (!a.is_dir && b.is_dir) return 1;
      return a.name.localeCompare(b.name);
    });

  return {
    base,
    path: currentPath,
    entries,
  };
}

async function fetchGitHubResearchFile(base: string, path: string): Promise<ResearchFileResponse> {
  const targetPath = cleanPath(path);
  const repoData = await fetchGitHubContents(base, targetPath);
  const item = Array.isArray(repoData) ? null : repoData;

  if (!item || item.type !== "file" || !item.download_url) {
    throw new Error("GitHub file not found or unsupported type");
  }

  const response = await fetch(item.download_url);
  if (!response.ok) {
    throw new Error(`Failed to load file from GitHub: ${response.statusText}`);
  }

  const content = await response.text();

  return {
    base,
    path: targetPath,
    size: item.size,
    modified: undefined,
    content,
  };
}

export function fetchChannels(): Promise<ChannelSummary[]> {
  return request<ChannelSummary[]>("/api/channels");
}

export function fetchVideos(channel: string): Promise<VideoSummary[]> {
  return request<VideoSummary[]>(`/api/channels/${encodeURIComponent(channel)}/videos`);
}

export function fetchChannelProfile(channel: string): Promise<ChannelProfileResponse> {
  return request<ChannelProfileResponse>(`/api/channels/${encodeURIComponent(channel)}/profile`);
}

export function fetchPlainTtsScript(channel: string, video: string): Promise<ScriptTextResponse> {
  return request<ScriptTextResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/tts/plain`
  );
}

export function fetchHumanScripts(channel: string, video: string): Promise<HumanScriptResponse> {
  return request<HumanScriptResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/scripts/human`
  );
}

export function updateHumanScripts(
  channel: string,
  video: string,
  payload: HumanScriptUpdatePayload
): Promise<{ status: string; updated_at?: string; audio_reviewed?: boolean }> {
  const body: JsonMap = {};
  if (payload.assembled_human !== undefined) {
    body.assembled_human = payload.assembled_human;
  }
  if (payload.script_audio_human !== undefined) {
    body.script_audio_human = payload.script_audio_human;
  }
  if (payload.audio_reviewed !== undefined) {
    body.audio_reviewed = payload.audio_reviewed;
  }
  if (payload.expectedUpdatedAt !== undefined) {
    body.expected_updated_at = payload.expectedUpdatedAt ?? null;
  }
  return request(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/scripts/human`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function fetchScriptManifest(channel: string, video: string): Promise<ScriptManifest> {
  return request<ScriptManifest>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/script-manifest`
  );
}

export function refreshScriptManifest(channel: string, video: string): Promise<ScriptManifest> {
  return request<ScriptManifest>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/script-manifest/refresh`,
    { method: "POST" }
  );
}

export function reconcileScriptPipeline(channel: string, video: string): Promise<JsonMap> {
  return request<JsonMap>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/script-pipeline/reconcile`,
    { method: "POST" }
  );
}

export function runScriptPipelineStage(channel: string, video: string, stage: string): Promise<JsonMap> {
  return request<JsonMap>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/script-pipeline/run/${encodeURIComponent(
      stage
    )}`,
    { method: "POST" }
  );
}

export function listLlmArtifacts(channel: string, video: string): Promise<LlmArtifactListItem[]> {
  return request<LlmArtifactListItem[]>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/llm-artifacts`
  );
}

export function fetchLlmArtifact(channel: string, video: string, artifactName: string): Promise<LlmTextArtifact> {
  return request<LlmTextArtifact>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/llm-artifacts/${encodeURIComponent(
      artifactName
    )}`
  );
}

export function updateLlmArtifact(
  channel: string,
  video: string,
  artifactName: string,
  payload: LlmTextArtifactUpdatePayload
): Promise<LlmTextArtifact> {
  return request<LlmTextArtifact>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/llm-artifacts/${encodeURIComponent(
      artifactName
    )}`,
    {
      method: "PUT",
      body: JSON.stringify({
        status: payload.status,
        content: payload.content,
        notes: payload.notes ?? undefined,
        apply_output: payload.applyOutput ?? false,
      }),
    }
  );
}

export function fetchWorkflowPrecheck(
  channel?: string,
  limit?: number
): Promise<WorkflowPrecheckResponse> {
  const params = new URLSearchParams();
  if (channel) {
    params.set("channel", channel);
  }
  if (limit) {
    params.set("limit", String(limit));
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return request<WorkflowPrecheckResponse>(`/api/guards/workflow-precheck${suffix}`);
}

export function updateChannelProfile(
  channel: string,
  payload: ChannelProfileUpdatePayload
): Promise<ChannelProfileResponse> {
  return request<ChannelProfileResponse>(`/api/channels/${encodeURIComponent(channel)}/profile`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function fetchPlanningRows(channel?: string): Promise<PlanningCsvRow[]> {
  const search = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  return request<PlanningCsvRow[]>(`/api/planning${search}`);
}

export function refreshPlanningStore(channel?: string): Promise<{ ok: boolean }> {
  const search = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  return request<{ ok: boolean }>(`/api/planning/refresh${search}`, {
    method: "POST",
  });
}

export function fetchPlanningSpreadsheet(channel: string): Promise<PlanningSpreadsheetResponse> {
  return request<PlanningSpreadsheetResponse>(`/api/planning/spreadsheet?channel=${encodeURIComponent(channel)}`);
}

export function fetchPromptDocuments(): Promise<PromptDocumentSummary[]> {
  return request<PromptDocumentSummary[]>("/api/prompts");
}

export function fetchPromptDocument(promptId: string): Promise<PromptDocumentDetail> {
  return request<PromptDocumentDetail>(`/api/prompts/${encodeURIComponent(promptId)}`);
}

export function updatePromptDocument(promptId: string, payload: PromptUpdatePayload): Promise<PromptDocumentDetail> {
  const body: Record<string, unknown> = {
    content: payload.content,
  };
  if (payload.expectedChecksum) {
    body.expected_checksum = payload.expectedChecksum;
  }
  return request<PromptDocumentDetail>(`/api/prompts/${encodeURIComponent(promptId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function refreshChannelBranding(channel: string, ignoreBackoff = false): Promise<ChannelSummary> {
  const search = ignoreBackoff ? "?ignore_backoff=true" : "";
  return request<ChannelSummary>(
    `/api/channels/${encodeURIComponent(channel)}/branding/refresh${search}`,
    { method: "POST" }
  );
}

export function fetchVideoDetail(channel: string, video: string): Promise<VideoDetail> {
  return request<VideoDetail>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}`);
}

export function updateAssembled(
  channel: string,
  video: string,
  content: string,
  expectedUpdatedAt?: string | null
): Promise<JsonMap> {
  return request<JsonMap>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/assembled`, {
    method: "PUT",
    body: JSON.stringify({
      content,
      expected_updated_at: expectedUpdatedAt ?? null,
    }),
  });
}

export function updateTts(
  channel: string,
  video: string,
  payload: {
    plainContent?: string;
    taggedContent?: string;
    contentMode?: "plain" | "tagged";
    regenerateAudio?: boolean;
    updateAssembled?: boolean;
  },
  expectedUpdatedAt?: string | null
): Promise<TtsSaveResponse> {
  const body: Record<string, unknown> = {
    expected_updated_at: expectedUpdatedAt ?? null,
    regenerate_audio: payload.regenerateAudio ?? false,
    update_assembled: payload.updateAssembled ?? false,
  };
  if (typeof payload.plainContent === "string") {
    body.content = payload.plainContent;
  }
  if (typeof payload.taggedContent === "string") {
    body.tagged_content = payload.taggedContent;
  }
  if (payload.contentMode) {
    body.content_mode = payload.contentMode;
  }
  return request<TtsSaveResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/tts`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    }
  );
}

export function validateTts(
  channel: string,
  video: string,
  content: string
): Promise<TtsValidationResponse> {
  return request<TtsValidationResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/tts/validate`,
    {
      method: "POST",
      body: JSON.stringify({ content }),
    }
  );
}

export function updateSrt(
  channel: string,
  video: string,
  content: string,
  expectedUpdatedAt?: string | null
): Promise<JsonMap> {
  return request<JsonMap>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/srt`, {
    method: "PUT",
    body: JSON.stringify({
      content,
      expected_updated_at: expectedUpdatedAt ?? null,
    }),
  });
}

export function verifySrt(
  channel: string,
  video: string,
  toleranceMs = 50
): Promise<SrtVerifyResponse> {
  const search = new URLSearchParams({ tolerance_ms: String(toleranceMs) }).toString();
  const path = `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/srt/verify${
    search ? `?${search}` : ""
  }`;
  return request<SrtVerifyResponse>(path, {
    method: "POST",
  });
}

export function updateStatus(
  channel: string,
  video: string,
  status: string,
  expectedUpdatedAt?: string | null
): Promise<JsonMap> {
  return request<JsonMap>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/status`, {
    method: "PUT",
    body: JSON.stringify({
      status,
      expected_updated_at: expectedUpdatedAt ?? null,
    }),
  });
}

export function updateStages(
  channel: string,
  video: string,
  stages: Record<string, string>,
  expectedUpdatedAt?: string | null
): Promise<JsonMap> {
  const payload: JsonMap = {
    stages: Object.fromEntries(
      Object.entries(stages).map(([key, value]) => [key, { status: value }])
    ),
    expected_updated_at: expectedUpdatedAt ?? null,
  };
  return request<JsonMap>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/stages`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function updateReady(
  channel: string,
  video: string,
  ready: boolean,
  expectedUpdatedAt?: string | null
): Promise<JsonMap> {
  return request<JsonMap>(`/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/ready`, {
    method: "PUT",
    body: JSON.stringify({
      ready,
      expected_updated_at: expectedUpdatedAt ?? null,
    }),
  });
}

export function updateVideoRedo(
  channel: string,
  video: string,
  payload: RedoUpdatePayload
): Promise<RedoUpdateResponse> {
  return request<RedoUpdateResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/redo`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}

export function markVideoPublishedLocked(
  channel: string,
  video: string,
  payload: PublishLockPayload = {}
): Promise<PublishLockResponse> {
  const body: Record<string, unknown> = {};
  if (payload.force_complete !== undefined) {
    body.force_complete = payload.force_complete;
  }
  if (payload.published_at !== undefined) {
    body.published_at = payload.published_at;
  }
  return request<PublishLockResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/published`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
}

export function unmarkVideoPublishedLocked(channel: string, video: string): Promise<PublishUnlockResponse> {
  return request<PublishUnlockResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/published`,
    {
      method: "DELETE",
    }
  );
}

export function updatePlanning(
  channel: string,
  video: string,
  payload: PlanningUpdatePayload = {}
): Promise<PlanningUpdateResponse> {
  const body: Record<string, unknown> = {};
  if (payload.creationFlag !== undefined) {
    body.creation_flag = payload.creationFlag;
  }
  if (payload.fields) {
    body.fields = payload.fields;
  }
  if (payload.expectedUpdatedAt !== undefined) {
    body.expected_updated_at = payload.expectedUpdatedAt ?? null;
  }
  return request<PlanningUpdateResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/planning`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    }
  );
}

export function createPlanningRow(payload: PlanningCreatePayload): Promise<PlanningCsvRow> {
  const body: Record<string, unknown> = {
    channel: payload.channel,
    video_number: payload.video_number,
    title: payload.title,
  };
  if (payload.no !== undefined) {
    body.no = payload.no;
  }
  if (payload.creation_flag !== undefined) {
    body.creation_flag = payload.creation_flag;
  }
  if (payload.progress !== undefined) {
    body.progress = payload.progress;
  }
  if (payload.fields) {
    body.fields = payload.fields;
  }
  return request<PlanningCsvRow>("/api/planning", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchRedoSummary(channel?: string): Promise<RedoSummaryItem[]> {
  const search = new URLSearchParams();
  if (channel) {
    search.set("channel", channel);
  }
  const path = `/api/redo/summary${search.toString() ? `?${search.toString()}` : ""}`;
  return request<RedoSummaryItem[]>(path);
}

export type KnowledgeBaseResponse = {
  version: number;
  words: Record<string, string>;
};

export function fetchKnowledgeBase(): Promise<KnowledgeBaseResponse> {
  return request<KnowledgeBaseResponse>("/api/kb");
}

export function upsertKnowledgeBaseEntry(word: string, reading: string): Promise<KnowledgeBaseResponse> {
  return request<KnowledgeBaseResponse>("/api/kb", {
    method: "POST",
    body: JSON.stringify({ word, reading }),
  });
}

export function deleteKnowledgeBaseEntry(word: string): Promise<void> {
  return request<void>(`/api/kb/${encodeURIComponent(word)}`, { method: "DELETE" });
}

export function fetchChannelReadingDict(channel: string): Promise<Record<string, any>> {
  return request<Record<string, any>>(`/api/reading-dict/${encodeURIComponent(channel)}`);
}

export function upsertChannelReadingEntry(
  channel: string,
  payload: { surface: string; reading_kana: string; reading_hira?: string; voicevox_kana?: string }
): Promise<Record<string, any>> {
  return request<Record<string, any>>(`/api/reading-dict/${encodeURIComponent(channel)}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteChannelReadingEntry(channel: string, surface: string): Promise<void> {
  return request<void>(
    `/api/reading-dict/${encodeURIComponent(channel)}/${encodeURIComponent(surface)}`,
    { method: "DELETE" }
  );
}

export function lookupThumbnails(channel: string, video?: string, title?: string, limit = 3): Promise<ThumbnailLookupResponse> {
  const search = new URLSearchParams({ channel });
  if (video) search.set("video", video);
  if (title) search.set("title", title);
  if (limit) search.set("limit", String(limit));
  const path = `/api/thumbnails/lookup?${search.toString()}`;
  return request<ThumbnailLookupResponse>(path);
}

export function fetchPersonaDocument(channel: string): Promise<PersonaDocumentResponse> {
  return request<PersonaDocumentResponse>(`/api/ssot/persona/${encodeURIComponent(channel)}`);
}

export function updatePersonaDocument(channel: string, payload: PersonaDocumentUpdatePayload): Promise<PersonaDocumentResponse> {
  return request<PersonaDocumentResponse>(`/api/ssot/persona/${encodeURIComponent(channel)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function fetchPlanningTemplate(channel: string): Promise<PlanningTemplateResponse> {
  return request<PlanningTemplateResponse>(`/api/ssot/templates/${encodeURIComponent(channel)}`);
}

export function updatePlanningTemplate(
  channel: string,
  payload: PlanningTemplateUpdatePayload
): Promise<PlanningTemplateResponse> {
  return request<PlanningTemplateResponse>(`/api/ssot/templates/${encodeURIComponent(channel)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function fetchLockMetrics(): Promise<LockMetrics> {
  return request<LockMetrics>("/api/admin/lock-metrics");
}

export function fetchDashboardOverview(): Promise<DashboardOverview> {
  return request<DashboardOverview>("/api/dashboard/overview");
}

export function replaceTtsSegment(
  channel: string,
  video: string,
  payload: TtsReplaceRequestPayload
): Promise<TtsReplaceResponse> {
  return request<TtsReplaceResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/tts/replace`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export function enhanceTts(
  channel: string,
  video: string,
  payload: { text: string; instruction?: string }
): Promise<{ suggestion?: string }> {
  return request<{ suggestion?: string }>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/tts/enhance`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export function submitNaturalCommand(
  channel: string,
  video: string,
  command: string,
  expectedUpdatedAt?: string | null
): Promise<NaturalCommandResponse> {
  return request<NaturalCommandResponse>(
    `/api/channels/${encodeURIComponent(channel)}/videos/${encodeURIComponent(video)}/command`,
    {
      method: "POST",
      body: JSON.stringify({
        command,
        expected_updated_at: expectedUpdatedAt ?? null,
      }),
    }
  );
}

export function startBatchWorkflow(payload: BatchWorkflowRequestPayload): Promise<BatchWorkflowTask> {
  return request<BatchWorkflowTask>("/api/batch-workflow/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function enqueueBatchWorkflow(payload: BatchWorkflowRequestPayload): Promise<BatchQueueEntry> {
  return request<BatchQueueEntry>("/api/batch-workflow/queue", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchBatchQueue(channel?: string): Promise<BatchQueueEntry[]> {
  const search = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  return request<BatchQueueEntry[]>(`/api/batch-workflow/queue${search}`);
}

export function cancelBatchQueueEntry(entryId: number): Promise<BatchQueueEntry> {
  return request<BatchQueueEntry>(`/api/batch-workflow/queue/${entryId}/cancel`, {
    method: "POST",
  });
}

export function fetchBatchWorkflowTask(taskId: string): Promise<BatchWorkflowTask> {
  return request<BatchWorkflowTask>(`/api/batch-workflow/${encodeURIComponent(taskId)}`);
}

export function fetchBatchWorkflowLog(taskId: string, tail = 200): Promise<BatchWorkflowLogResponse> {
  const search = new URLSearchParams({ tail: String(tail) }).toString();
  const path = `/api/batch-workflow/${encodeURIComponent(taskId)}/log${search ? `?${search}` : ""}`;
  return request<BatchWorkflowLogResponse>(path);
}

export function fetchAudioReviewItems(params?: { channel?: string; status?: string; video?: string }): Promise<AudioReviewItem[]> {
  const search = new URLSearchParams();
  if (params?.channel) {
    search.set("channel", params.channel);
  }
  if (params?.status) {
    search.set("status", params.status);
  }
  if (params?.video) {
    search.set("video", params.video);
  }
  const path = `/api/workspaces/audio-review${search.toString() ? `?${search.toString()}` : ""}`;
  return request<AudioReviewItem[]>(path);
}

export function fetchAText(channel: string, video: string): Promise<string> {
  const path = `/api/channels/${channel}/videos/${video}/a-text`;
  return request<string>(path);
}

export function runAudioTtsV2(payload: {
  channel: string;
  video: string;
  input_path: string;
  engine_override?: string;
  reading_source?: string;
  voicepeak_narrator?: string;
  voicepeak_speed?: number;
  voicepeak_pitch?: number;
  voicepeak_emotion?: string;
}): Promise<RunTtsV2Response> {
  return request<RunTtsV2Response>("/api/audio-tts-v2/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchProgressCsv(channel: string) {
  return request<{ channel: string; rows: Record<string, string>[] }>(`/api/progress/channels/${encodeURIComponent(channel)}`);
}

export function runAudioTtsV2FromScript(payload: {
  channel: string;
  video: string;
  engine_override?: string;
  reading_source?: string;
}): Promise<RunTtsV2Response> {
  return request<RunTtsV2Response>("/api/audio-tts-v2/run-from-script", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchThumbnailOverview(): Promise<ThumbnailOverview> {
  return request<ThumbnailOverview>("/api/workspaces/thumbnails");
}

export function fetchThumbnailImageModels(): Promise<ThumbnailImageModelInfo[]> {
  return request<ThumbnailImageModelInfo[]>("/api/workspaces/thumbnails/image-models");
}

export function fetchThumbnailTemplates(channel: string): Promise<ThumbnailChannelTemplates> {
  return request<ThumbnailChannelTemplates>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/templates`
  );
}

export function updateThumbnailTemplates(
  channel: string,
  payload: ThumbnailChannelTemplatesUpdate
): Promise<ThumbnailChannelTemplates> {
  return request<ThumbnailChannelTemplates>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/templates`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}

export function updateThumbnailProject(
  channel: string,
  video: string,
  payload: {
    owner?: string | null;
    summary?: string | null;
    notes?: string | null;
    tags?: string[];
    due_at?: string | null;
    status?: ThumbnailProjectStatus;
    selected_variant_id?: string | null;
  }
): Promise<JsonMap> {
  const body: JsonMap = {};
  if (payload.owner !== undefined) {
    body.owner = payload.owner;
  }
  if (payload.summary !== undefined) {
    body.summary = payload.summary;
  }
  if (payload.notes !== undefined) {
    body.notes = payload.notes;
  }
  if (payload.tags !== undefined) {
    body.tags = payload.tags;
  }
  if (payload.due_at !== undefined) {
    body.due_at = payload.due_at;
  }
  if (payload.status !== undefined) {
    body.status = payload.status;
  }
  if (payload.selected_variant_id !== undefined) {
    body.selected_variant_id = payload.selected_variant_id;
  }
  return request<JsonMap>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/${encodeURIComponent(video)}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    }
  );
}

export function generateThumbnailVariants(
  channel: string,
  video: string,
  payload: ThumbnailVariantGeneratePayload
): Promise<ThumbnailVariant[]> {
  return request<ThumbnailVariant[]>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/${encodeURIComponent(video)}/variants/generate`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export function composeThumbnailVariant(
  channel: string,
  video: string,
  payload: ThumbnailVariantComposePayload
): Promise<ThumbnailVariant> {
  return request<ThumbnailVariant>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/${encodeURIComponent(video)}/variants/compose`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export function createThumbnailVariant(
  channel: string,
  video: string,
  payload: {
    label: string;
    status: ThumbnailVariantStatus;
    image_url?: string;
    image_path?: string;
    notes?: string;
    tags?: string[];
    prompt?: string;
    make_selected?: boolean;
  }
): Promise<ThumbnailVariant> {
  const body: JsonMap = {
    label: payload.label,
    status: payload.status,
  };
  if (payload.image_url) {
    body.image_url = payload.image_url;
  }
  if (payload.image_path) {
    body.image_path = payload.image_path;
  }
  if (payload.notes) {
    body.notes = payload.notes;
  }
  if (payload.tags) {
    body.tags = payload.tags;
  }
  if (payload.prompt) {
    body.prompt = payload.prompt;
  }
  if (payload.make_selected !== undefined) {
    body.make_selected = payload.make_selected;
  }
  return request<ThumbnailVariant>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/${encodeURIComponent(video)}/variants`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
}

export function uploadThumbnailVariantAsset(
  channel: string,
  video: string,
  payload: {
    file: File;
    label?: string;
    makeSelected?: boolean;
    status?: ThumbnailVariantStatus;
    tags?: string[];
    notes?: string;
  }
): Promise<ThumbnailVariant> {
  const form = new FormData();
  form.append("file", payload.file);
  if (payload.label) {
    form.append("label", payload.label);
  }
  if (payload.makeSelected !== undefined) {
    form.append("make_selected", payload.makeSelected ? "true" : "false");
  }
  if (payload.status) {
    form.append("status", payload.status);
  }
  if (payload.tags && payload.tags.length > 0) {
    form.append("tags", JSON.stringify(payload.tags));
  }
  if (payload.notes) {
    form.append("notes", payload.notes);
  }
  return requestForm<ThumbnailVariant>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/${encodeURIComponent(video)}/variants/upload`,
    form
  );
}

export function fetchThumbnailLibrary(channel: string): Promise<ThumbnailLibraryAsset[]> {
  return request<ThumbnailLibraryAsset[]>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library`
  );
}

export function uploadThumbnailLibraryAssets(
  channel: string,
  files: File[]
): Promise<ThumbnailLibraryAsset[]> {
  if (!files.length) {
    return Promise.reject(new Error("アップロードする画像を選択してください。"));
  }
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return requestForm<ThumbnailLibraryAsset[]>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/upload`,
    form
  );
}

export function importThumbnailLibraryAsset(
  channel: string,
  params: { url: string; fileName?: string }
): Promise<ThumbnailLibraryAsset> {
  const body: JsonMap = {
    url: params.url,
  };
  if (params.fileName) {
    body.file_name = params.fileName;
  }
  return request<ThumbnailLibraryAsset>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/import`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
}

export function describeThumbnailLibraryAsset(
  channel: string,
  assetName: string
): Promise<ThumbnailDescriptionResponse> {
  return request<ThumbnailDescriptionResponse>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/${encodeURIComponent(assetName)}/describe`
  );
}

export function fetchThumbnailHistory(channel?: string, limit = 5): Promise<ThumbnailQuickHistoryEntry[]> {
  const params = new URLSearchParams();
  if (channel) {
    params.set("channel", channel);
  }
  if (limit) {
    params.set("limit", String(limit));
  }
  const query = params.toString();
  return request<ThumbnailQuickHistoryEntry[]>(
    `/api/workspaces/thumbnails/history${query ? `?${query}` : ""}`
  );
}

export function renameThumbnailLibraryAsset(
  channel: string,
  assetName: string,
  newName: string
): Promise<ThumbnailLibraryAsset> {
  return request<ThumbnailLibraryAsset>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/${encodeURIComponent(assetName)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ new_name: newName }),
    }
  );
}

export function deleteThumbnailLibraryAsset(channel: string, assetName: string): Promise<void> {
  return request<void>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/${encodeURIComponent(assetName)}`,
    {
      method: "DELETE",
    }
  );
}

export function assignThumbnailLibraryAsset(
  channel: string,
  assetName: string,
  payload: { video: string; label?: string; make_selected?: boolean }
): Promise<ThumbnailLibraryAssignResponse> {
  return request<ThumbnailLibraryAssignResponse>(
    `/api/workspaces/thumbnails/${encodeURIComponent(channel)}/library/${encodeURIComponent(assetName)}/assign`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export async function fetchResearchList(base: string, path = ""): Promise<ResearchListResponse> {
  const search = new URLSearchParams();
  if (base) search.set("base", base);
  if (path) search.set("path", path);
  const query = search.toString();

  try {
    return await request<ResearchListResponse>(`/api/research/list${query ? `?${query}` : ""}`);
  } catch (error) {
    if (isGitHubPagesHost()) {
      return fetchGitHubResearchList(base, path);
    }
    throw error;
  }
}

export async function fetchResearchFile(base: string, path: string): Promise<ResearchFileResponse> {
  const search = new URLSearchParams({ base, path });
  try {
    return await request<ResearchFileResponse>(`/api/research/file?${search.toString()}`);
  } catch (error) {
    if (isGitHubPagesHost()) {
      return fetchGitHubResearchFile(base, path);
    }
    throw error;
  }
}

export function fetchUiParams(): Promise<UiParamsResponse> {
  return request<UiParamsResponse>("/api/params");
}

export function updateUiParams(body: Partial<UiParams>): Promise<UiParamsResponse> {
  return request<UiParamsResponse>("/api/params", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchAudioIntegrity(): Promise<AudioIntegrityItem[]> {
  return request<AudioIntegrityItem[]>("/api/audio/integrity");
}

export function fetchAudioAnalysis(channel: string, video: string): Promise<AudioAnalysis> {
  return request<AudioAnalysis>(`/api/audio/analysis/${encodeURIComponent(channel)}/${encodeURIComponent(video)}`);
}

type SourceStatusResponse = {
  channel?: string | null;
  video_number?: string | null;
  srt_ready?: boolean;
  audio_ready?: boolean;
  srt_path?: string | null;
  audio_path?: string | null;
};

type VideoProjectSummaryResponse = {
  id: string;
  title?: string | null;
  status: string;
  next_action?: string | null;
  template_used?: string | null;
  image_count: number;
  log_count: number;
  created_at?: string | null;
  last_updated?: string | null;
  srt_file?: string | null;
  draft_path?: string | null;
  channel_id?: string | null;
  channelId?: string | null;
  source_status?: SourceStatusResponse | null;
};
type VideoProjectGuardResponse = {
  status: "ok" | "fail";
  cue_count?: number | null;
  image_count?: number | null;
  min_image_bytes?: number | null;
  persona_required?: boolean;
  missing_profiles?: number[];
  tiny_images?: string[];
  recommended_commands?: string[];
  issues?: Array<{ code: string; message: string; details?: Record<string, unknown> }>;
  project_dir?: string | null;
  image_dir?: string | null;
};

type VideoGenerationOptionsResponse = {
  imgdur?: number | null;
  crossfade?: number | null;
  fps?: number | null;
  style?: string | null;
  size?: string | null;
  fit?: string | null;
  margin?: number | null;
};

type VideoProjectCapcutSettingsResponse = {
  channel_id?: string | null;
  channelId?: string | null;
  template_used?: string | null;
  templateUsed?: string | null;
  draft_name?: string | null;
  draftName?: string | null;
  draft_path?: string | null;
  draftPath?: string | null;
  transform?: {
    tx?: number | null;
    ty?: number | null;
    scale?: number | null;
  };
  crossfade_sec?: number | null;
  fade_duration_sec?: number | null;
  opening_offset?: number | null;
};

type VideoProjectDetailResponse = Omit<
  VideoProjectDetail,
  "summary" | "guard" | "sourceStatus" | "generationOptions" | "capcut"
> & {
  summary: VideoProjectSummaryResponse;
  guard?: VideoProjectGuardResponse | null;
  source_status?: SourceStatusResponse | null;
  generation_options?: VideoGenerationOptionsResponse | null;
  capcut?: VideoProjectCapcutSettingsResponse | null;
};
type VideoProductionChannelPresetResponse = VideoProductionChannelPreset & {
  channel_id: string;
  prompt_template?: string | null;
  capcut_template?: string | null;
  persona_required?: boolean;
  image_min_bytes?: number | null;
  srt_files?: Array<{
    channel_id: string;
    name: string;
    relative_path: string;
    size?: number;
    modified_time_iso?: string;
  }>;
};

export const DEFAULT_GENERATION_OPTIONS: VideoGenerationOptions = {
  imgdur: 20,
  crossfade: 0.5,
  fps: 30,
  style: "",
  size: "1920x1080",
  fit: "cover",
  margin: 0,
};

function normalizeProjectSummary(raw: VideoProjectSummaryResponse): VideoProjectSummary {
  return {
    id: raw.id,
    title: raw.title,
    status: raw.status,
    next_action: raw.next_action,
    template_used: raw.template_used,
    image_count: raw.image_count,
    log_count: raw.log_count,
    created_at: raw.created_at,
    last_updated: raw.last_updated,
    srt_file: raw.srt_file,
    draft_path: raw.draft_path,
    channel_id: raw.channel_id ?? raw.channelId ?? null,
    channelId: raw.channelId ?? raw.channel_id ?? null,
    sourceStatus: normalizeSourceStatus(raw.source_status),
  };
}

function normalizeSourceStatus(raw?: SourceStatusResponse | null): SourceStatus | null {
  if (!raw) {
    return null;
  }
  return {
    channel: raw.channel ?? null,
    videoNumber: raw.video_number ?? null,
    srtReady: raw.srt_ready ?? false,
    audioReady: raw.audio_ready ?? false,
    srtPath: raw.srt_path ?? null,
    audioPath: raw.audio_path ?? null,
  };
}

function normalizeGuard(raw?: VideoProjectGuardResponse | null): VideoProjectGuard | null {
  if (!raw) {
    return null;
  }
  return {
    status: raw.status,
    cueCount: raw.cue_count ?? null,
    imageCount: raw.image_count ?? null,
    minImageBytes: raw.min_image_bytes ?? null,
    personaRequired: Boolean(raw.persona_required),
    missingProfiles: raw.missing_profiles ?? [],
    tinyImages: raw.tiny_images ?? [],
    recommendedCommands: raw.recommended_commands ?? [],
    issues: raw.issues ?? [],
    projectDir: raw.project_dir ?? null,
    imageDir: raw.image_dir ?? null,
  };
}

function normalizeGenerationOptions(raw?: VideoGenerationOptionsResponse | null): VideoGenerationOptions {
  return {
    imgdur: typeof raw?.imgdur === "number" ? raw.imgdur : DEFAULT_GENERATION_OPTIONS.imgdur,
    crossfade: typeof raw?.crossfade === "number" ? raw.crossfade : DEFAULT_GENERATION_OPTIONS.crossfade,
    fps: typeof raw?.fps === "number" ? raw.fps : DEFAULT_GENERATION_OPTIONS.fps,
    style: typeof raw?.style === "string" ? raw.style : DEFAULT_GENERATION_OPTIONS.style,
    size: typeof raw?.size === "string" ? raw.size : DEFAULT_GENERATION_OPTIONS.size,
    fit: (raw?.fit as VideoGenerationOptions["fit"]) || DEFAULT_GENERATION_OPTIONS.fit,
    margin: typeof raw?.margin === "number" ? raw.margin : DEFAULT_GENERATION_OPTIONS.margin,
  };
}

function normalizeCapcutSettings(raw?: VideoProjectCapcutSettingsResponse | null): VideoProjectCapcutSettings | null {
  if (!raw) {
    return null;
  }
  const transform = raw.transform ?? {};
  return {
    channelId: raw.channel_id ?? raw.channelId ?? null,
    templateUsed: raw.template_used ?? raw.templateUsed ?? null,
    draftName: raw.draft_name ?? raw.draftName ?? null,
    draftPath: raw.draft_path ?? raw.draftPath ?? null,
    transform: {
      tx: typeof transform.tx === "number" ? transform.tx : 0,
      ty: typeof transform.ty === "number" ? transform.ty : 0,
      scale: typeof transform.scale === "number" ? transform.scale : 1,
    },
    crossfadeSec: typeof raw.crossfade_sec === "number" ? raw.crossfade_sec : DEFAULT_GENERATION_OPTIONS.crossfade,
    fadeDurationSec:
      typeof raw.fade_duration_sec === "number"
        ? raw.fade_duration_sec
        : typeof raw.crossfade_sec === "number"
          ? raw.crossfade_sec
          : DEFAULT_GENERATION_OPTIONS.crossfade,
    openingOffset: typeof raw.opening_offset === "number" ? raw.opening_offset : 0,
  };
}

function normalizeChannelPreset(raw: VideoProductionChannelPresetResponse): VideoProductionChannelPreset {
  const { srt_files, position, belt, ...rest } = raw;
  return {
    ...rest,
    channelId: raw.channel_id,
    promptTemplate: raw.promptTemplate ?? raw.prompt_template ?? null,
    capcutTemplate: raw.capcutTemplate ?? raw.capcut_template ?? null,
    personaRequired: raw.personaRequired ?? raw.persona_required ?? undefined,
    imageMinBytes: raw.imageMinBytes ?? raw.image_min_bytes ?? undefined,
    position: position ?? undefined,
    belt: belt ?? undefined,
    beltLabels: (raw as any).belt_labels ?? (raw as any).beltLabels ?? null,
    srtFiles:
      srt_files?.map((item) => ({
        channelId: item.channel_id,
        name: item.name,
        relativePath: item.relative_path,
        size: item.size,
        modifiedTimeIso: item.modified_time_iso,
      })) ?? undefined,
  };
}

type CapcutDraftSummaryResponse = {
  name: string;
  path: string;
  title?: string;
  duration?: number;
  image_count?: number;
  imageCount?: number;
  modified_time?: number;
  modifiedTime?: number;
  modified_time_iso?: string;
  modifiedTimeIso?: string;
  channel_id?: string | null;
  channelId?: string | null;
  channel_name?: string | null;
  channelName?: string | null;
  video_number?: string | null;
  videoNumber?: string | null;
  project_id?: string | null;
  projectId?: string | null;
  project_exists?: boolean;
  projectExists?: boolean;
  project_hint?: string | null;
  projectHint?: string | null;
};

function normalizeCapcutDraftSummary(raw: CapcutDraftSummaryResponse): CapcutDraftSummary {
  const channelId = raw.channel_id ?? raw.channelId ?? null;
  return {
    name: raw.name,
    path: raw.path,
    title: raw.title ?? raw.name,
    duration: raw.duration ?? 0,
    imageCount: raw.image_count ?? raw.imageCount ?? 0,
    modifiedTime: raw.modified_time ?? raw.modifiedTime ?? 0,
    modifiedTimeIso: raw.modified_time_iso ?? raw.modifiedTimeIso,
    channelId: channelId ? channelId.toUpperCase() : null,
    channelName: raw.channel_name ?? raw.channelName ?? null,
    videoNumber: raw.video_number ?? raw.videoNumber ?? null,
    projectId: raw.project_id ?? raw.projectId ?? null,
    projectExists: raw.project_exists ?? raw.projectExists ?? undefined,
    projectHint: raw.project_hint ?? raw.projectHint ?? null,
  };
}

type RemotionProjectSummaryResponse = {
  project_id: string;
  channel_id?: string | null;
  title?: string | null;
  duration_sec?: number | null;
  status: string;
  issues?: string[];
  metrics?: {
    image_count?: number;
    asset_ready?: number;
    asset_total?: number;
  };
  assets?: Array<{
    label: string;
    path?: string | null;
    exists?: boolean;
    type?: string;
    size_bytes?: number | null;
    modified_time?: string | null;
  }>;
  outputs?: Array<{
    path: string;
    url?: string | null;
    file_name?: string;
    size_bytes?: number | null;
    modified_time?: string | null;
  }>;
  remotion_dir?: string | null;
  timeline_path?: string | null;
  last_rendered?: string | null;
  drive_upload?: {
    uploaded_at?: string | null;
    destination?: {
      folder_path?: string | null;
    } | null;
    drive?: {
      id?: string | null;
      name?: string | null;
      webViewLink?: string | null;
    } | null;
  } | null;
};

function normalizeRemotionProject(raw: RemotionProjectSummaryResponse): RemotionProjectSummary {
  const drive = raw.drive_upload?.drive ?? null;
  const destination = raw.drive_upload?.destination ?? null;
  const driveUpload = raw.drive_upload
    ? {
        uploadedAt: raw.drive_upload.uploaded_at ?? null,
        fileId: drive?.id ?? null,
        fileName: drive?.name ?? null,
        webViewLink: drive?.webViewLink ?? null,
        folderPath: destination?.folder_path ?? null,
      }
    : null;
  return {
    projectId: raw.project_id,
    channelId: raw.channel_id ?? null,
    title: raw.title ?? null,
    durationSec: raw.duration_sec ?? null,
    status: (raw.status as RemotionProjectSummary["status"]) ?? "missing_assets",
    issues: raw.issues ?? [],
    metrics: {
      imageCount: raw.metrics?.image_count ?? 0,
      assetReady: raw.metrics?.asset_ready ?? 0,
      assetTotal: raw.metrics?.asset_total ?? 0,
    },
    assets:
      raw.assets?.map((asset) => ({
        label: asset.label,
        path: asset.path ?? null,
        exists: Boolean(asset.exists),
        type: asset.type === "directory" ? "directory" : "file",
        sizeBytes: asset.size_bytes ?? null,
        modifiedTime: asset.modified_time ?? null,
      })) ?? [],
    outputs:
      raw.outputs?.map((entry) => ({
        path: entry.path,
        url: entry.url ?? null,
        fileName: entry.file_name ?? (entry.path ? entry.path.split(/[/\\]/).pop() ?? entry.path : entry.path),
        sizeBytes: entry.size_bytes ?? null,
        modifiedTime: entry.modified_time ?? null,
      })) ?? [],
    remotionDir: raw.remotion_dir ?? null,
    timelinePath: raw.timeline_path ?? null,
    lastRendered: raw.last_rendered ?? null,
    driveUpload,
  };
}

type AutoDraftListResponseRaw = {
  items?: AutoDraftSrtItem[];
  input_root?: string;
  inputRoot?: string;
};

type AutoDraftCreateResponseRaw = {
  ok: boolean;
  stdout?: string;
  stderr?: string;
  run_name?: string;
  runName?: string;
  title?: string;
  channel?: string;
  run_dir?: string;
  runDir?: string;
};

type AutoDraftSrtContentRaw = {
  name: string;
  path: string;
  content: string;
  size_bytes?: number;
  modified_time?: number;
  ok?: boolean;
};

type ProjectSrtContentRaw = AutoDraftSrtContentRaw;

type PromptTemplateListResponseRaw = {
  items?: { name: string; path: string }[];
  template_root?: string;
  templateRoot?: string;
};

type PromptTemplateContentResponseRaw = {
  name: string;
  path: string;
  content: string;
  template_root?: string;
  templateRoot?: string;
};

export async function fetchAutoDraftSrts(): Promise<AutoDraftListResponse> {
  const data = await request<AutoDraftListResponseRaw>("/api/auto-draft/srts");
  return {
    items: (data.items ?? []).map((item) => ({
      name: item.name,
      path: item.path,
    })),
    inputRoot: data.input_root ?? data.inputRoot ?? "",
  };
}

export function createAutoDraft(payload: AutoDraftCreatePayload): Promise<AutoDraftCreateResponse> {
  const body: Record<string, unknown> = {
    srt_path: payload.srtPath,
  };
  if (payload.channel) {
    body.channel = payload.channel;
  }
  if (payload.runName) {
    body.run_name = payload.runName;
  }
  if (payload.title) {
    body.title = payload.title;
  }
  if (payload.labels) {
    body.labels = payload.labels;
  }
  if (payload.template) {
    body.template = payload.template;
  }
  if (payload.promptTemplate) {
    body.prompt_template = payload.promptTemplate;
  }
  if (payload.beltMode) {
    body.belt_mode = payload.beltMode;
  }
  if (payload.chaptersJson) {
    body.chapters_json = payload.chaptersJson;
  }
  if (payload.episodeInfoJson) {
    body.episode_info_json = payload.episodeInfoJson;
  }
  if (payload.imgDuration) {
    body.imgdur = payload.imgDuration;
  }

  return request<AutoDraftCreateResponseRaw>("/api/auto-draft/create", {
    method: "POST",
    body: JSON.stringify(body),
  }).then((data) => ({
    ok: Boolean(data.ok),
    stdout: data.stdout ?? "",
    stderr: data.stderr ?? "",
    runName: data.run_name ?? data.runName ?? "",
    title: data.title ?? "",
    channel: data.channel ?? "",
    runDir: data.run_dir ?? data.runDir ?? "",
  }));
}

export async function fetchAutoDraftSrtContent(path: string): Promise<AutoDraftSrtContent> {
  const params = new URLSearchParams({ path });
  const data = await request<AutoDraftSrtContentRaw>(`/api/auto-draft/srt?${params.toString()}`);
  return {
    name: data.name,
    path: data.path,
    content: data.content,
    sizeBytes: data.size_bytes ?? null,
    modifiedTime: data.modified_time ?? null,
  };
}

export async function updateAutoDraftSrtContent(path: string, content: string): Promise<AutoDraftSrtContent> {
  const data = await request<AutoDraftSrtContentRaw>("/api/auto-draft/srt", {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });
  return {
    name: data.name,
    path: data.path,
    content,
    sizeBytes: data.size_bytes ?? null,
    modifiedTime: data.modified_time ?? null,
  };
}

export async function fetchProjectSrtContent(projectId: string): Promise<ProjectSrtContent> {
  const data = await request<ProjectSrtContentRaw>(`/api/video-production/projects/${encodeURIComponent(projectId)}/srt`);
  return {
    name: data.name,
    path: data.path,
    content: data.content,
    sizeBytes: data.size_bytes ?? null,
    modifiedTime: data.modified_time ?? null,
  };
}

export async function updateProjectSrtContent(projectId: string, content: string): Promise<ProjectSrtContent> {
  const data = await request<ProjectSrtContentRaw>(`/api/video-production/projects/${encodeURIComponent(projectId)}/srt`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
  return {
    name: data.name,
    path: data.path,
    content,
    sizeBytes: data.size_bytes ?? null,
    modifiedTime: data.modified_time ?? null,
  };
}

export async function fetchAutoDraftPromptTemplates(): Promise<{ items: { name: string; path: string }[]; templateRoot: string }> {
  const data = await request<PromptTemplateListResponseRaw>("/api/auto-draft/prompt-templates");
  return {
    items: data.items ?? [],
    templateRoot: data.template_root ?? data.templateRoot ?? "",
  };
}

export async function fetchAutoDraftPromptTemplateContent(path: string): Promise<PromptTemplateContentResponse> {
  const params = new URLSearchParams({ path });
  const data = await request<PromptTemplateContentResponseRaw>(`/api/auto-draft/prompt-template?${params.toString()}`);
  return {
    name: data.name,
    path: data.path,
    content: data.content,
    templateRoot: data.template_root ?? data.templateRoot ?? "",
  };
}

export function resolveApiUrl(path: string): string {
  return buildUrl(path);
}

export function fetchLlmSettings(): Promise<LlmSettings> {
  return request<LlmSettings>("/api/settings/llm");
}

export function updateLlmSettings(payload: LlmSettingsUpdate): Promise<LlmSettings> {
  return request<LlmSettings>("/api/settings/llm", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function fetchLlmModelScores(): Promise<LlmModelInfo[]> {
  return request<LlmModelInfo[]>("/api/llm/models");
}

export async function fetchVideoProductionChannels(includeSrts = false): Promise<VideoProductionChannelPreset[]> {
  const params = new URLSearchParams();
  if (includeSrts) {
    params.set("include_srts", "true");
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  const data = await request<VideoProductionChannelPresetResponse[]>(`/api/video-production/channels${suffix}`);
  return data.map(normalizeChannelPreset);
}

export function fetchChannelPreset(channelId: string): Promise<VideoProductionChannelPreset> {
  return request<VideoProductionChannelPresetResponse>(
    `/api/video-production/channel-presets/${encodeURIComponent(channelId)}`
  ).then(normalizeChannelPreset);
}

export function updateChannelPreset(
  channelId: string,
  payload: ChannelPresetUpdatePayload
): Promise<VideoProductionChannelPreset> {
  return request<VideoProductionChannelPresetResponse>(
    `/api/video-production/channel-presets/${encodeURIComponent(channelId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        name: payload.name,
        prompt_template: payload.promptTemplate,
        style: payload.style,
        capcut_template: payload.capcutTemplate,
        persona_required: payload.personaRequired,
        image_min_bytes: payload.imageMinBytes,
        position: payload.position ?? undefined,
        belt: payload.belt ?? undefined,
        notes: payload.notes,
        status: payload.status,
      }),
    }
  ).then(normalizeChannelPreset);
}

export async function fetchVideoProjects(): Promise<VideoProjectSummary[]> {
  const data = await request<VideoProjectSummaryResponse[]>("/api/video-production/projects");
  return data.map(normalizeProjectSummary);
}

export async function fetchVideoProjectDetail(projectId: string): Promise<VideoProjectDetail> {
  const detail = await request<VideoProjectDetailResponse>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}`
  );
  const { generation_options, source_status, summary, guard, capcut, ...rest } = detail;
  return {
    ...rest,
    summary: normalizeProjectSummary(summary),
    guard: normalizeGuard(guard),
    sourceStatus: normalizeSourceStatus(source_status),
    generationOptions: normalizeGenerationOptions(generation_options),
    capcut: normalizeCapcutSettings(capcut),
  };
}

export function fetchProjectSrtSegments(projectId: string): Promise<SrtSegmentsArtifact> {
  return request<SrtSegmentsArtifact>(`/api/video-production/projects/${encodeURIComponent(projectId)}/srt-segments`);
}

export function fetchProjectVisualCuesPlan(projectId: string): Promise<VisualCuesPlanArtifact> {
  return request<VisualCuesPlanArtifact>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/visual-cues-plan`
  );
}

export function updateProjectVisualCuesPlan(
  projectId: string,
  payload: VisualCuesPlanUpdatePayload
): Promise<VisualCuesPlanArtifact> {
  return request<VisualCuesPlanArtifact>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/visual-cues-plan`,
    {
      method: "PUT",
      body: JSON.stringify({
        status: payload.status,
        sections: payload.sections,
        style_hint: payload.styleHint ?? undefined,
      }),
    }
  );
}

export function updateVideoGenerationOptions(
  projectId: string,
  options: VideoGenerationOptions
): Promise<VideoGenerationOptions> {
  return request<VideoGenerationOptions>(`/api/video-production/projects/${encodeURIComponent(projectId)}/generation-options`, {
    method: "PUT",
    body: JSON.stringify(options),
  });
}

export function replaceProjectImage(projectId: string, imagePath: string, file: File): Promise<VideoProjectImageAsset> {
  const formData = new FormData();
  formData.append("image_path", imagePath);
  formData.append("file", file);
  return requestForm<VideoProjectImageAsset>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/images/replace`,
    formData
  );
}

export function regenerateProjectImage(
  projectId: string,
  imageIndex: number,
  payload: { prompt?: string | null; promptSuffix?: string | null }
): Promise<VideoProjectImageAsset> {
  return request<VideoProjectImageAsset>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/images/${imageIndex}/regenerate`,
    {
      method: "POST",
      body: JSON.stringify({
        prompt: payload.prompt ?? null,
        prompt_suffix: payload.promptSuffix ?? null,
      }),
    }
  );
}

export interface BeltPatchEntryPayload {
  index: number;
  text?: string;
  start?: number;
  end?: number;
}

export function fetchProjectBelt(projectId: string): Promise<{ belts: VideoProjectBeltEntry[] }> {
  return request<{ belts: VideoProjectBeltEntry[] }>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/belt`
  );
}

export function updateProjectBelt(
  projectId: string,
  entries: BeltPatchEntryPayload[]
): Promise<{ belts: VideoProjectBeltEntry[] }> {
  return request<{ belts: VideoProjectBeltEntry[] }>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/belt`,
    {
      method: "PATCH",
      body: JSON.stringify({ entries }),
    }
  );
}

export interface CapcutSettingsPatchPayload {
  tx?: number;
  ty?: number;
  scale?: number;
  crossfadeSec?: number;
  fadeDurationSec?: number;
  openingOffset?: number;
}

export function updateProjectCapcutSettings(
  projectId: string,
  payload: CapcutSettingsPatchPayload
): Promise<VideoProjectCapcutSettings | null> {
  return request<VideoProjectCapcutSettingsResponse | null>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/capcut-settings`,
    {
      method: "PATCH",
      body: JSON.stringify({
        tx: payload.tx,
        ty: payload.ty,
        scale: payload.scale,
        crossfade_sec: payload.crossfadeSec,
        fade_duration_sec: payload.fadeDurationSec,
        opening_offset: payload.openingOffset,
      }),
    }
  ).then((data) => normalizeCapcutSettings(data));
}

export function createVideoProject(payload: VideoProjectCreatePayload): Promise<VideoProjectCreateResponse> {
  const form = new FormData();
  form.append("project_id", payload.projectId);
  if (payload.channelId) {
    form.append("channel_id", payload.channelId);
  }
  if (payload.targetSections !== undefined) {
    form.append("target_sections", String(payload.targetSections));
  }
  if (payload.existingSrtPath) {
    form.append("existing_srt_path", payload.existingSrtPath);
  } else if (payload.srtFile) {
    form.append("srt_file", payload.srtFile);
  }
  return requestForm<VideoProjectCreateResponse>("/api/video-production/projects", form);
}

export function createVideoJob(projectId: string, payload: VideoJobCreatePayload): Promise<VideoJobRecord> {
  return request<VideoJobRecord>(`/api/video-production/projects/${encodeURIComponent(projectId)}/jobs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchVideoJobs(projectId: string, limit = 50): Promise<VideoJobRecord[]> {
  const params = new URLSearchParams();
  params.set("project_id", projectId);
  if (limit) {
    params.set("limit", String(limit));
  }
  return request<VideoJobRecord[]>(`/api/video-production/jobs?${params.toString()}`);
}

export function fetchVideoJobLog(jobId: string): Promise<string> {
  return requestText(`/api/video-production/jobs/${encodeURIComponent(jobId)}/log`);
}

export function installCapcutDraft(
  projectId: string,
  payload: { overwrite: boolean }
): Promise<CapcutInstallResult> {
  return request<CapcutInstallResult>(
    `/api/video-production/projects/${encodeURIComponent(projectId)}/capcut/install`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export async function fetchCapcutDrafts(): Promise<CapcutDraftSummary[]> {
  const drafts = await request<CapcutDraftSummaryResponse[]>("/api/video-production/drafts");
  return drafts.map(normalizeCapcutDraftSummary);
}

export function fetchCapcutDraftDetail(draftName: string): Promise<CapcutDraftDetail> {
  return request<CapcutDraftDetail>(`/api/video-production/drafts/${encodeURIComponent(draftName)}`);
}

export async function fetchRemotionProjects(): Promise<RemotionProjectSummary[]> {
  const data = await request<RemotionProjectSummaryResponse[]>("/api/video-production/remotion/projects");
  return data.map(normalizeRemotionProject);
}
