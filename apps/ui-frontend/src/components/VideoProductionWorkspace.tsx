import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ChangeEvent } from "react";
import { useSearchParams } from "react-router-dom";

import {
  createVideoProject,
  createVideoJob,
  fetchCapcutDraftDetail,
  fetchCapcutDrafts,
  installCapcutDraft,
  fetchVideoJobs,
  fetchVideoProductionChannels,
  fetchVideoProjectDetail,
  fetchVideoProjects,
  fetchProjectSrtSegments,
  fetchProjectVisualCuesPlan,
  fetchProjectSrtContent,
  updateProjectVisualCuesPlan,
  updateProjectSrtContent,
  replaceProjectImage,
  regenerateProjectImage,
  resolveApiUrl,
  DEFAULT_GENERATION_OPTIONS,
  updateChannelPreset,
  updateProjectBelt,
  updateProjectCapcutSettings,
  updateVideoGenerationOptions,
} from "../api/client";
import type {
  CapcutDraftDetail,
  CapcutDraftSummary,
  VideoProjectCreatePayload,
  VideoJobCreatePayload,
  VideoJobRecord,
  VideoProductionChannelPreset,
  VideoProjectDetail,
  VideoProjectSummary,
  VideoProjectImageAsset,
  VideoProjectBeltEntry,
  SourceStatus,
  VideoGenerationOptions,
  ChannelPresetUpdatePayload,
  SrtSegmentsArtifact,
  VisualCuesPlanArtifact,
  VisualCuesPlanSection,
} from "../api/types";
import { INTEGRITY_LABEL, getIntegrityStatusLabel } from "../copy/videoProduction";
import { loadWorkspaceSelection, saveWorkspaceSelection } from "../utils/workspaceSelection";
import { VideoImageVariantsPanel } from "./VideoImageVariantsPanel";

type StepState = { id: string; label: string; state: "done" | "active" | "todo" | "danger" };
type PipelineStepPlan = {
  action: VideoJobCreatePayload["action"];
  label: string;
  options?: Record<string, unknown>;
};
type PipelinePlan = {
  steps: PipelineStepPlan[];
  reason?: string;
};

type CapcutLayoutDraft = {
  tx: number;
  ty: number;
  scale: number;
  crossfadeSec: number;
  fadeDurationSec: number;
  openingOffset: number;
};

type ChannelPresetDraft = {
  capcutTemplate: string;
  promptTemplate: string;
  style: string;
  tx: number;
  ty: number;
  scale: number;
  beltOpening: number;
  beltRequiresConfig: boolean;
  beltEnabled: boolean;
  notes: string;
};

type ProjectCreateDraft = {
  channelId: string;
  video: string;
  projectId: string;
  srtRelativePath: string;
  targetSections: string;
};

const STEP_SEQUENCE: Array<{ id: string; label: string }> = [
  { id: "materials", label: "素材" },
  { id: "chunk", label: "チャンク" },
  { id: "visual", label: "画像/帯" },
  { id: "guard", label: INTEGRITY_LABEL },
  { id: "capcut", label: "CapCut" },
];

const JOB_LABELS: Record<string, string> = {
  analyze_srt: "SRT解析",
  regenerate_images: "画像生成",
  generate_image_variants: "画像バリアント",
  generate_belt: "帯生成",
  validate_capcut: INTEGRITY_LABEL,
  build_capcut_draft: "CapCutドラフト",
  render_remotion: "Remotion生成",
};

const JOB_STATUS_LABELS: Record<VideoJobRecord["status"], string> = {
  queued: "待機中",
  running: "実行中",
  succeeded: "成功",
  failed: "失敗",
};

const DEFAULT_CHANNEL_FILTER = "";

function normalizeVideoToken(value: string): string {
  const raw = (value ?? "").trim();
  if (!raw) return "";
  if (/^\d+$/.test(raw)) return raw.padStart(3, "0");
  return raw;
}

function normalizeChannelToken(value: string): string {
  return (value ?? "").trim().toUpperCase();
}

export function VideoProductionWorkspace() {
  const [searchParams] = useSearchParams();
  const [channelPresets, setChannelPresets] = useState<VideoProductionChannelPreset[]>([]);
  const [channelFilter, setChannelFilter] = useState<string>(DEFAULT_CHANNEL_FILTER);
  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [projectDetail, setProjectDetail] = useState<VideoProjectDetail | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [srtFilter, setSrtFilter] = useState("");
  const [srtLineNumbers, setSrtLineNumbers] = useState(false);
  const [srtFull, setSrtFull] = useState(false);
  const [srtEdit, setSrtEdit] = useState("");
  const [savingSrt, setSavingSrt] = useState(false);
  const renderSrtLine = useCallback((line: string, idx: number) => {
    const arrow = " --> ";
    const tsMatch = line.match(
      /^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})(.*)$/i
    );
    if (tsMatch) {
      return (
        <span>
          <span style={{ color: "#0f172a", fontWeight: 700 }}>{tsMatch[1]}</span>
          <span style={{ color: "#94a3b8" }}>{arrow}</span>
          <span style={{ color: "#0f172a", fontWeight: 700 }}>{tsMatch[2]}</span>
          <span style={{ color: "#475569" }}>{tsMatch[3]}</span>
        </span>
      );
    }
    if (/^\d+$/.test(line.trim())) {
      return <span style={{ color: "#475569" }}>{line}</span>;
    }
    return <span style={{ color: "#0f172a" }}>{line || "\u00A0"}</span>;
  }, []);
  const [capcutDrafts, setCapcutDrafts] = useState<CapcutDraftSummary[]>([]);
  const [draftSearch, setDraftSearch] = useState("");
  const [draftSort, setDraftSort] = useState<"recent" | "oldest">("recent");
  const [draftLinkFilter, setDraftLinkFilter] = useState<"all" | "linked" | "unlinked">("all");
  const [selectedDraft, setSelectedDraft] = useState<CapcutDraftSummary | null>(null);
  const [draftDetail, setDraftDetail] = useState<CapcutDraftDetail | null>(null);
  const [activeImageIndex, setActiveImageIndex] = useState<number | null>(null);
  const [fullPipelineRunning, setFullPipelineRunning] = useState(false);
  const [generationDraft, setGenerationDraft] = useState<VideoGenerationOptions>(DEFAULT_GENERATION_OPTIONS);
  const [generationSaving, setGenerationSaving] = useState(false);
  const [capcutSettingsDraft, setCapcutSettingsDraft] = useState<CapcutLayoutDraft | null>(null);
  const [capcutSettingsSaving, setCapcutSettingsSaving] = useState(false);
  const [jobRecords, setJobRecords] = useState<VideoJobRecord[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [presetDraft, setPresetDraft] = useState<ChannelPresetDraft | null>(null);
  const [presetSaving, setPresetSaving] = useState(false);
  const [installingDraft, setInstallingDraft] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [projectCreateDraft, setProjectCreateDraft] = useState<ProjectCreateDraft>({
    channelId: "",
    video: "",
    projectId: "",
    srtRelativePath: "",
    targetSections: "",
  });
  const [creatingProject, setCreatingProject] = useState(false);
  const selectionRef = useMemo(() => loadWorkspaceSelection(), []);
  const pipelinePlan = useMemo(
    () => resolvePipelinePlan(projectDetail),
    [projectDetail]
  );
  const appliedQueryRef = useRef(false);
  const priorDerivedProjectIdRef = useRef<string>("");
  const priorSuggestedSrtRef = useRef<string>("");

  const queryChannel = normalizeChannelToken(searchParams.get("channel") || "");
  const queryVideo = normalizeVideoToken(searchParams.get("video") || "");
  const queryProject = (searchParams.get("project") || "").trim();

  useEffect(() => {
    void (async () => {
      try {
        const presets = await fetchVideoProductionChannels(true);
        setChannelPresets(presets);
        setChannelFilter((current) => normalizeChannelToken(queryChannel) || current || presets[0]?.channelId || DEFAULT_CHANNEL_FILTER);
      } catch (error) {
        console.error(error);
      }
    })();
  }, [queryChannel]);

  useEffect(() => {
    void (async () => {
      try {
        const items = await fetchCapcutDrafts();
        setCapcutDrafts(items);
      } catch (error) {
        console.error(error);
      }
    })();
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const data = await fetchVideoProjects();
        setProjects(data);
        if (queryProject && data.some((item) => item.id === queryProject)) {
          setSelectedProjectId(queryProject);
          return;
        }
        if (selectionRef?.projectId && data.some((item) => item.id === selectionRef.projectId)) {
          setSelectedProjectId(selectionRef.projectId);
          return;
        }
        setSelectedProjectId(data[0]?.id ?? null);
      } catch (error) {
        console.error(error);
      }
    })();
  }, [queryProject, selectionRef]);

  useEffect(() => {
    saveWorkspaceSelection({
      channel: channelFilter ? channelFilter : null,
      projectId: selectedProjectId ?? null,
    });
  }, [channelFilter, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setProjectDetail(null);
      setSrtEdit("");
      return;
    }
    setProjectLoading(true);
    fetchVideoProjectDetail(selectedProjectId)
      .then(async (detail) => {
        setProjectDetail(detail);
        try {
          const srt = await fetchProjectSrtContent(selectedProjectId);
          setSrtEdit(srt.content);
        } catch {
          setSrtEdit("");
        }
      })
      .catch((error) => {
        console.error(error);
        setProjectDetail(null);
        setSrtEdit("");
      })
      .finally(() => setProjectLoading(false));
  }, [selectedProjectId]);

  useEffect(() => {
    setGenerationDraft(projectDetail?.generationOptions ?? DEFAULT_GENERATION_OPTIONS);
  }, [projectDetail?.generationOptions]);

  useEffect(() => {
    if (!projectDetail?.capcut) {
      setCapcutSettingsDraft(null);
      return;
    }
    setCapcutSettingsDraft({
      tx: projectDetail.capcut.transform?.tx ?? 0,
      ty: projectDetail.capcut.transform?.ty ?? 0,
      scale: projectDetail.capcut.transform?.scale ?? 1,
      crossfadeSec: projectDetail.capcut.crossfadeSec ?? DEFAULT_GENERATION_OPTIONS.crossfade,
      fadeDurationSec: projectDetail.capcut.fadeDurationSec ?? projectDetail.capcut.crossfadeSec ?? DEFAULT_GENERATION_OPTIONS.crossfade,
      openingOffset: projectDetail.capcut.openingOffset ?? 0,
    });
  }, [projectDetail?.capcut]);

  const readyProjects = useMemo(() => {
    return projects.filter((project) => {
      const status = project.sourceStatus ?? (project as { source_status?: VideoProjectSummary["sourceStatus"] }).source_status;
      return Boolean(status?.srtReady && status?.audioReady);
    });
  }, [projects]);

  const filteredDrafts = useMemo(() => {
    const keyword = draftSearch.trim().toLowerCase();
    const list = capcutDrafts.filter((draft) => {
      if (channelFilter && draft.channelId?.toUpperCase() !== channelFilter.toUpperCase()) {
        return false;
      }
      if (draftLinkFilter === "linked" && !draft.projectId) {
        return false;
      }
      if (draftLinkFilter === "unlinked" && draft.projectId) {
        return false;
      }
      if (!keyword) {
        return true;
      }
      return (
        draft.name.toLowerCase().includes(keyword) ||
        (draft.projectId ?? "").toLowerCase().includes(keyword) ||
        (draft.title ?? "").toLowerCase().includes(keyword)
      );
    });
    const sorted = [...list].sort((a, b) => {
      const timeA = a.modifiedTime ?? 0;
      const timeB = b.modifiedTime ?? 0;
      if (draftSort === "recent") {
        return timeB - timeA;
      }
      return timeA - timeB;
    });
    return sorted.slice(0, 60);
  }, [capcutDrafts, channelFilter, draftLinkFilter, draftSearch, draftSort]);

  const selectedChannelPreset = useMemo(() => {
    if (!channelFilter) {
      return null;
    }
    return channelPresets.find((preset) => preset.channelId === channelFilter) ?? null;
  }, [channelFilter, channelPresets]);

  const projectChannelPreset = useMemo(() => {
    const channelId = resolveChannelId(projectDetail?.summary ?? undefined);
    if (channelId) {
      return channelPresets.find((preset) => preset.channelId === channelId) ?? null;
    }
    return selectedChannelPreset;
  }, [channelPresets, projectDetail?.summary, selectedChannelPreset]);

  const createChannelPreset = useMemo(() => {
    const channelId = normalizeChannelToken(projectCreateDraft.channelId);
    if (!channelId) return null;
    return channelPresets.find((preset) => preset.channelId === channelId) ?? null;
  }, [channelPresets, projectCreateDraft.channelId]);

  const createSrtOptions = useMemo(() => createChannelPreset?.srtFiles ?? [], [createChannelPreset]);

  const derivedProjectId = useMemo(() => {
    const channelId = normalizeChannelToken(projectCreateDraft.channelId);
    const video = normalizeVideoToken(projectCreateDraft.video);
    if (!channelId || !video) return "";
    return `${channelId}-${video}`;
  }, [projectCreateDraft.channelId, projectCreateDraft.video]);

  const queryProjectMissing = useMemo(() => {
    if (!queryProject) return false;
    return !projects.some((project) => project.id === queryProject);
  }, [projects, queryProject]);

  const applyProjectCreatePatch = useCallback((patch: Partial<ProjectCreateDraft>) => {
    setProjectCreateDraft((prev) => {
      const prevChannel = normalizeChannelToken(prev.channelId);
      const prevVideo = normalizeVideoToken(prev.video);
      const prevDerived = prevChannel && prevVideo ? `${prevChannel}-${prevVideo}` : "";

      const next: ProjectCreateDraft = {
        ...prev,
        ...patch,
      };
      next.channelId = normalizeChannelToken(next.channelId);
      next.video = normalizeVideoToken(next.video);

      const nextDerived = next.channelId && next.video ? `${next.channelId}-${next.video}` : "";
      const projectIdPatched = typeof patch.projectId === "string";
      const projectIdWasAuto = !prev.projectId || prev.projectId === prevDerived;
      if (!projectIdPatched && projectIdWasAuto && nextDerived) {
        next.projectId = nextDerived;
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (appliedQueryRef.current) return;
    if (!channelPresets.length && !queryChannel && !queryVideo && !queryProject) return;

    appliedQueryRef.current = true;
    const fallbackChannel = normalizeChannelToken(queryChannel || channelFilter || channelPresets[0]?.channelId || "");
    const fallbackVideo = normalizeVideoToken(queryVideo || "");
    const fallbackProjectId = queryProject || (fallbackChannel && fallbackVideo ? `${fallbackChannel}-${fallbackVideo}` : "");
    applyProjectCreatePatch({
      channelId: fallbackChannel,
      video: fallbackVideo,
      projectId: fallbackProjectId,
    });
  }, [applyProjectCreatePatch, channelFilter, channelPresets, queryChannel, queryProject, queryVideo]);

  useEffect(() => {
    const priorDerived = priorDerivedProjectIdRef.current;
    priorDerivedProjectIdRef.current = derivedProjectId;
    if (!derivedProjectId) return;
    setProjectCreateDraft((prev) => {
      const currentId = (prev.projectId ?? "").trim();
      if (!currentId || currentId === priorDerived) {
        return { ...prev, projectId: derivedProjectId };
      }
      return prev;
    });
  }, [derivedProjectId]);

  useEffect(() => {
    if (!createSrtOptions.length) return;
    const channelId = normalizeChannelToken(projectCreateDraft.channelId);
    const video = normalizeVideoToken(projectCreateDraft.video);
    const expectedSuffix = channelId && video ? `/${channelId}/${video}/${channelId}-${video}.srt` : "";
    const expectedNeedle = expectedSuffix.toLowerCase();
    const match =
      expectedNeedle
        ? createSrtOptions.find((item) => item.relativePath.replace(/\\\\/g, "/").toLowerCase().endsWith(expectedNeedle))
        : createSrtOptions[0];
    if (!match) return;
    const suggested = match.relativePath;

    setProjectCreateDraft((prev) => {
      const previous = prev.srtRelativePath || "";
      const priorSuggested = priorSuggestedSrtRef.current;
      priorSuggestedSrtRef.current = suggested;
      if (!previous || previous === priorSuggested) {
        return { ...prev, srtRelativePath: suggested };
      }
      return prev;
    });
  }, [createSrtOptions, projectCreateDraft.channelId, projectCreateDraft.video]);

  const handleCreateProject = useCallback(async () => {
    const channelId = normalizeChannelToken(projectCreateDraft.channelId);
    const video = normalizeVideoToken(projectCreateDraft.video);
    const projectId = (projectCreateDraft.projectId || (channelId && video ? `${channelId}-${video}` : "")).trim();
    const srtRelativePath = (projectCreateDraft.srtRelativePath || "").trim();
    const targetSectionsRaw = (projectCreateDraft.targetSections || "").trim();
    let targetSections: number | undefined = undefined;

    if (!projectId) {
      setBanner("project_id を入力してください。");
      return;
    }
    if (!srtRelativePath) {
      setBanner("SRT を選択してください。（SoT: workspaces/audio/final）");
      return;
    }
    if (targetSectionsRaw) {
      const parsed = Number(targetSectionsRaw);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setBanner("target_sections は 1 以上の数値で指定してください。");
        return;
      }
      targetSections = parsed;
    }

    setCreatingProject(true);
    try {
      const payload: VideoProjectCreatePayload = {
        projectId,
        channelId: channelId || undefined,
        targetSections,
        existingSrtPath: srtRelativePath,
      };
      await createVideoProject(payload);
      const nextProjects = await fetchVideoProjects();
      setProjects(nextProjects);
      setSelectedProjectId(projectId);
      setBanner(`プロジェクトを作成しました: ${projectId}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBanner(`プロジェクト作成に失敗しました: ${message}`);
    } finally {
      setCreatingProject(false);
    }
  }, [projectCreateDraft]);

  const refreshJobs = useCallback(async () => {
    if (!selectedProjectId) {
      setJobRecords([]);
      setJobsLoading(false);
      return;
    }
    setJobsLoading(true);
    try {
      const records = await fetchVideoJobs(selectedProjectId, 20);
      setJobRecords(records);
    } catch (error) {
      console.error("failed to load jobs", error);
    } finally {
      setJobsLoading(false);
    }
  }, [selectedProjectId]);

  const handleDraftSelect = useCallback(
    async (draft: CapcutDraftSummary) => {
      setSelectedDraft(draft);
      if (draft.projectId) {
        setSelectedProjectId(draft.projectId);
      }
      try {
        const detail = await fetchCapcutDraftDetail(draft.name);
        setDraftDetail(detail);
      } catch (error) {
        console.error(error);
        setDraftDetail(null);
      }
    },
    []
  );

  const handleQuickJob = useCallback(
    async (action: VideoJobCreatePayload["action"], options: VideoJobCreatePayload["options"] = {}) => {
      if (!selectedProjectId) {
        setBanner("プロジェクトを選択してください。");
        return;
      }
      try {
        await createVideoJob(selectedProjectId, { action, options });
        setBanner("ジョブを追加しました。");
        void refreshJobs();
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setBanner(`ジョブ失敗: ${message}`);
      }
    },
    [refreshJobs, selectedProjectId]
  );

  const handleImageReplace = useCallback(
    async (assetPath: string, file: File) => {
      if (!selectedProjectId) {
        setBanner("プロジェクトを選択してください。");
        return;
      }
      try {
        await replaceProjectImage(selectedProjectId, assetPath, file);
        const fresh = await fetchVideoProjectDetail(selectedProjectId);
        setProjectDetail(fresh);
        setBanner("画像を更新しました。");
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setBanner(`画像更新に失敗しました: ${message}`);
      }
    },
    [selectedProjectId]
  );

  const handleImageRegenerate = useCallback(
    async (imageIndex: number, promptOverride: string | null) => {
      if (!selectedProjectId) {
        setBanner("プロジェクトを選択してください。");
        return;
      }
      try {
        await regenerateProjectImage(selectedProjectId, imageIndex, {
          prompt: promptOverride ?? undefined,
        });
        const refreshed = await fetchVideoProjectDetail(selectedProjectId);
        setProjectDetail(refreshed);
        await createVideoJob(selectedProjectId, {
          action: "validate_capcut",
          options: { use_existing_draft: true },
        });
        await createVideoJob(selectedProjectId, {
          action: "build_capcut_draft",
        });
        setBanner("画像を再生成し、整合チェックと CapCut 更新ジョブを順に追加しました。");
        void refreshJobs();
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setBanner(`画像再生成に失敗しました: ${message}`);
        throw error;
      }
    },
    [refreshJobs, selectedProjectId]
  );

  const handleBeltSave = useCallback(
    async (beltIndex: number, updates: Partial<VideoProjectBeltEntry>) => {
      if (!selectedProjectId) {
        setBanner("プロジェクトを選択してください。");
        return;
      }
      try {
        await updateProjectBelt(selectedProjectId, [
          {
            index: beltIndex,
            text: updates.text,
            start: updates.start,
            end: updates.end,
          },
        ]);
        const refreshed = await fetchVideoProjectDetail(selectedProjectId);
        setProjectDetail(refreshed);
        await createVideoJob(selectedProjectId, {
          action: "validate_capcut",
          options: { use_existing_draft: true },
        });
        await createVideoJob(selectedProjectId, {
          action: "build_capcut_draft",
        });
        setBanner("帯テキストを保存し、整合チェックと CapCut 更新ジョブを追加しました。");
        void refreshJobs();
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setBanner(`帯テキストの保存に失敗しました: ${message}`);
        throw error;
      }
    },
    [refreshJobs, selectedProjectId]
  );

  const handleFullPipeline = useCallback(async () => {
    if (!selectedProjectId) {
      setBanner("プロジェクトを選択してください。");
      return;
    }
    if (!pipelinePlan.steps.length) {
      setBanner(pipelinePlan.reason ?? "一括実行できる工程がありません。");
      return;
    }
    setFullPipelineRunning(true);
    try {
      for (const step of pipelinePlan.steps) {
        await createVideoJob(selectedProjectId, {
          action: step.action,
          options: step.options ?? {},
        });
      }
      setBanner("SRT→CapCut の一括ジョブを順に追加しました。");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBanner(`一括実行に失敗しました: ${message}`);
    } finally {
      setFullPipelineRunning(false);
    }
    void refreshJobs();
  }, [pipelinePlan, refreshJobs, selectedProjectId]);

  const handleGenerationChange = useCallback(
    (key: keyof VideoGenerationOptions, value: number | string) => {
      setGenerationDraft((prev) => ({
        ...prev,
        [key]: typeof prev[key] === "number" ? Number(value) : value,
      }));
    },
    []
  );

  const handleGenerationSave = useCallback(async () => {
    if (!selectedProjectId) {
      setBanner("プロジェクトを選択してください。");
      return;
    }
    setGenerationSaving(true);
    try {
      await updateVideoGenerationOptions(selectedProjectId, generationDraft);
      const refreshed = await fetchVideoProjectDetail(selectedProjectId);
      setProjectDetail(refreshed);
      setBanner("生成パラメータを保存しました。次回の画像生成に反映されます。");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBanner(`生成パラメータの保存に失敗しました: ${message}`);
    } finally {
      setGenerationSaving(false);
    }
  }, [generationDraft, selectedProjectId]);

  const handleCapcutSettingsChange = useCallback((key: keyof CapcutLayoutDraft, value: number) => {
    setCapcutSettingsDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }, []);

  const handleCapcutSettingsSave = useCallback(async () => {
    if (!selectedProjectId || !capcutSettingsDraft) {
      setBanner("プロジェクトを選択してください。");
      return;
    }
    setCapcutSettingsSaving(true);
    try {
      await updateProjectCapcutSettings(selectedProjectId, {
        tx: capcutSettingsDraft.tx,
        ty: capcutSettingsDraft.ty,
        scale: capcutSettingsDraft.scale,
        crossfadeSec: capcutSettingsDraft.crossfadeSec,
        fadeDurationSec: capcutSettingsDraft.fadeDurationSec,
        openingOffset: capcutSettingsDraft.openingOffset,
      });
      const refreshed = await fetchVideoProjectDetail(selectedProjectId);
      setProjectDetail(refreshed);
      await createVideoJob(selectedProjectId, {
        action: "validate_capcut",
        options: { use_existing_draft: true },
      });
      await createVideoJob(selectedProjectId, {
        action: "build_capcut_draft",
      });
      setBanner("レイアウト/フェードを保存し、整合チェックと CapCut 更新ジョブを追加しました。");
      void refreshJobs();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBanner(`レイアウト設定の保存に失敗しました: ${message}`);
      throw error;
    } finally {
      setCapcutSettingsSaving(false);
    }
  }, [capcutSettingsDraft, refreshJobs, selectedProjectId]);

  const handlePresetFieldChange = useCallback(
    (key: keyof ChannelPresetDraft, value: string | number | boolean) => {
      setPresetDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    },
    []
  );

  const handlePresetSave = useCallback(async () => {
    if (!selectedChannelPreset || !presetDraft) {
      setBanner("チャンネルを選択してください。");
      return;
    }
    setPresetSaving(true);
    try {
      const payload: ChannelPresetUpdatePayload = {
        capcutTemplate: presetDraft.capcutTemplate || null,
        promptTemplate: presetDraft.promptTemplate || null,
        style: presetDraft.style || null,
        position: {
          tx: presetDraft.tx,
          ty: presetDraft.ty,
          scale: presetDraft.scale,
        },
        belt: {
          enabled: presetDraft.beltEnabled,
          opening_offset: presetDraft.beltOpening,
          requires_config: presetDraft.beltRequiresConfig,
        },
        notes: presetDraft.notes || null,
      };
      const updated = await updateChannelPreset(selectedChannelPreset.channelId, payload);
      setChannelPresets((prev) =>
        prev.map((preset) => (preset.channelId === updated.channelId ? updated : preset))
      );
      setBanner("チャンネルプリセットを更新しました。");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBanner(`チャンネルプリセットの更新に失敗しました: ${message}`);
    } finally {
      setPresetSaving(false);
    }
  }, [presetDraft, selectedChannelPreset, setChannelPresets]);

  const handleInstallDraft = useCallback(
    async (draft: CapcutDraftSummary) => {
      if (!draft.projectId) {
        setBanner("このドラフトは SoT と紐付いていません。");
        return;
      }
      setInstallingDraft(draft.name);
      try {
        await installCapcutDraft(draft.projectId, { overwrite: true });
        setBanner(`CapCut へコピーしました (${draft.projectId})`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setBanner(`CapCut コピーに失敗しました: ${message}`);
      } finally {
        setInstallingDraft(null);
      }
    },
    [setBanner]
  );

  useEffect(() => {
    if (!selectedChannelPreset) {
      setPresetDraft(null);
      return;
    }
    setPresetDraft({
      capcutTemplate: selectedChannelPreset.capcutTemplate ?? "",
      promptTemplate: selectedChannelPreset.promptTemplate ?? "",
      style: selectedChannelPreset.style ?? "",
      tx: selectedChannelPreset.position?.tx ?? 0,
      ty: selectedChannelPreset.position?.ty ?? 0,
      scale: selectedChannelPreset.position?.scale ?? 1,
      beltOpening: selectedChannelPreset.belt?.opening_offset ?? 0,
      beltRequiresConfig: Boolean(selectedChannelPreset.belt?.requires_config),
      beltEnabled: selectedChannelPreset.belt?.enabled ?? true,
      notes: selectedChannelPreset.notes ?? "",
    });
  }, [selectedChannelPreset]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshJobs();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [refreshJobs, selectedProjectId]);

  const projectSteps = useMemo(() => buildStepState(projectDetail), [projectDetail]);
  const srtDisplay = useMemo(() => {
    const lines = projectDetail?.srt_preview ?? [];
    const keyword = srtFilter.trim().toLowerCase();
    const filtered = keyword ? lines.filter((line) => line.toLowerCase().includes(keyword)) : lines;
    if (!filtered.length) {
      return keyword ? "フィルタに一致する行がありません" : "SRTの冒頭をここに表示します。ソースが無い場合はSRTを紐付けてください。";
    }
    const limited = srtFull ? filtered : filtered.slice(0, 200);
    const body = limited.map((line, idx) => (srtLineNumbers ? `${idx + 1}: ${line}` : line)).join("\n");
    const omitted = !srtFull && filtered.length > limited.length ? `\n… (${filtered.length - limited.length} 行省略)` : "";
    return body + omitted;
  }, [projectDetail?.srt_preview, srtFilter, srtFull, srtLineNumbers]);

  return (
    <div className="vp-shell">
      <div className="vp-panel">
        <div className="vp-panel__header">
          <div>
            <h2 style={{ margin: 0 }}>新規プロジェクト作成</h2>
            <p className="video-production-text-muted" style={{ margin: "6px 0 0" }}>
              SRT（SoT: workspaces/audio/final）から SoT プロジェクトを作成します。
            </p>
          </div>
          <div className="vp-panel__actions">
            <button type="button" onClick={handleCreateProject} disabled={creatingProject}>
              {creatingProject ? "作成中…" : "プロジェクト作成"}
            </button>
          </div>
        </div>
        {queryProjectMissing ? (
          <p className="video-production-alert video-production-alert--warning">
            指定されたプロジェクトが見つかりません: <strong>{queryProject}</strong>（必要ならここから作成してください）
          </p>
        ) : null}
        <div className="vp-options-grid">
          <label>
            <span>チャンネル</span>
            <select
              value={projectCreateDraft.channelId}
              onChange={(event) =>
                applyProjectCreatePatch({ channelId: event.target.value, srtRelativePath: "" })
              }
            >
              <option value="">未選択</option>
              {channelPresets.map((preset) => (
                <option key={preset.channelId} value={preset.channelId}>
                  {preset.name ?? preset.channelId}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>動画番号</span>
            <input
              type="text"
              value={projectCreateDraft.video}
              placeholder="例: 022"
              onChange={(event) => applyProjectCreatePatch({ video: event.target.value })}
            />
          </label>
          <label>
            <span>project_id</span>
            <input
              type="text"
              value={projectCreateDraft.projectId}
              placeholder={derivedProjectId ? `例: ${derivedProjectId}` : "例: CH01-022"}
              onChange={(event) => applyProjectCreatePatch({ projectId: event.target.value })}
            />
          </label>
          <label>
            <span>SRT（relative_path）</span>
            <select
              value={projectCreateDraft.srtRelativePath}
              onChange={(event) => applyProjectCreatePatch({ srtRelativePath: event.target.value })}
              disabled={!createSrtOptions.length}
            >
              <option value="">{createSrtOptions.length ? "選択してください" : "SRTが見つかりません"}</option>
              {createSrtOptions.map((srt) => (
                <option key={srt.relativePath} value={srt.relativePath}>
                  {srt.name} ({srt.relativePath})
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>target_sections（任意）</span>
            <input
              type="number"
              min="1"
              step="1"
              value={projectCreateDraft.targetSections}
              placeholder="例: 12"
              onChange={(event) => applyProjectCreatePatch({ targetSections: event.target.value })}
            />
          </label>
        </div>
      </div>
      <div className="vp-shell__grid">
        <CapcutDraftBoard
          channelFilter={channelFilter}
          onChannelChange={setChannelFilter}
          channelOptions={channelPresets}
          readyProjects={readyProjects}
          selectedProjectId={selectedProjectId}
          onProjectChange={setSelectedProjectId}
        drafts={filteredDrafts}
          selectedDraft={selectedDraft}
          draftDetail={draftDetail}
          search={draftSearch}
          onSearchChange={setDraftSearch}
          sortMode={draftSort}
          onSortModeChange={setDraftSort}
          linkFilter={draftLinkFilter}
          onLinkFilterChange={setDraftLinkFilter}
          installingDraft={installingDraft}
          onInstallDraft={handleInstallDraft}
          onSelectDraft={handleDraftSelect}
        />
        <div className="vp-shell__main">
          <HeroHeader
            project={projectDetail}
            steps={projectSteps}
            loading={projectLoading}
            banner={banner}
            onRunFullPipeline={handleFullPipeline}
            fullPipelineRunning={fullPipelineRunning}
            pipelinePlan={pipelinePlan}
          />
          {projectDetail ? (
            <div className="vp-section-block" style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 14, background: "#fff" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div>
                  <strong>SRTプレビュー / 編集</strong>
                  <div style={{ fontSize: 12, color: "#64748b" }}>{projectDetail.summary?.srt_file || "未設定"}</div>
                </div>
                <span style={{ fontSize: 12, color: "#475569" }}>
                  {(projectDetail.srt_preview?.length ?? 0) > 0 ? `${projectDetail.srt_preview.length}行表示` : "プレビューなし"}
                </span>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8, marginBottom: 8 }}>
                <input
                  type="text"
                  placeholder="キーワードで絞り込み"
                  value={srtFilter}
                  onChange={(event) => setSrtFilter(event.target.value)}
                  style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1", minWidth: 220 }}
                />
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#334155" }}>
                  <input
                    type="checkbox"
                    checked={srtLineNumbers}
                    onChange={(event) => setSrtLineNumbers(event.target.checked)}
                  />
                  行番号を表示
                </label>
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#334155" }}>
                  <input
                    type="checkbox"
                    checked={srtFull}
                    onChange={(event) => setSrtFull(event.target.checked)}
                  />
                  全文表示（デフォルト200行）
                </label>
                <button
                  type="button"
                  onClick={() => {
                    if (!projectDetail?.srt_preview?.length) return;
                    const text = projectDetail.srt_preview.join("\n");
                    void navigator.clipboard.writeText(text);
                  }}
                  disabled={!projectDetail?.srt_preview?.length}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid #cbd5e1",
                    background: projectDetail?.srt_preview?.length ? "#f8fafc" : "#e5e7eb",
                    color: "#0f172a",
                    cursor: projectDetail?.srt_preview?.length ? "pointer" : "not-allowed",
                  }}
                >
                  コピー
                </button>
              </div>
              <div
                style={{
                  border: "1px solid #e5e7eb",
                  borderRadius: 10,
                  background: "#f8fafc",
                  padding: 10,
                  minHeight: 140,
                  maxHeight: 260,
                  overflow: "auto",
                  fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                }}
              >
                {projectDetail.srt_preview && projectDetail.srt_preview.length ? (
                  srtDisplay.split("\n").length === 1 && srtDisplay.startsWith("フィルタに一致する行がありません") ? (
                    srtDisplay
                  ) : (
                    srtDisplay
                      .split("\n")
                      .map((line, idx) => (
                        <div key={`${line}-${idx}`} style={{ lineHeight: 1.4 }}>
                          {renderSrtLine(line, idx)}
                        </div>
                      ))
                  )
                ) : (
                  srtDisplay
                )}
              </div>
              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                <div style={{ fontWeight: 700, fontSize: 12, color: "#334155" }}>SRT編集（保存すると上書き）</div>
                <textarea
                  value={srtEdit}
                  onChange={(event) => setSrtEdit(event.target.value)}
                  style={{
                    width: "100%",
                    minHeight: 200,
                    borderRadius: 10,
                    border: "1px solid #cbd5e1",
                    padding: 10,
                    fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                    fontSize: 12,
                    background: "#fff",
                  }}
                />
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    onClick={async () => {
                      if (!selectedProjectId) return;
                      setSavingSrt(true);
                      try {
                        await updateProjectSrtContent(selectedProjectId, srtEdit);
                      } catch (error) {
                        console.error(error);
                      } finally {
                        setSavingSrt(false);
                      }
                    }}
                    disabled={savingSrt}
                    style={{
                      padding: "10px 14px",
                      borderRadius: 10,
                      border: "none",
                      background: savingSrt ? "#e5e7eb" : "#0f172a",
                      color: "#fff",
                      cursor: savingSrt ? "not-allowed" : "pointer",
                      fontWeight: 700,
                    }}
                  >
                    {savingSrt ? "保存中..." : "この内容で保存"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (!projectDetail?.srt_preview) return;
                      setSrtEdit(projectDetail.srt_preview.join("\n"));
                    }}
                    disabled={!projectDetail?.srt_preview?.length}
                    style={{
                      padding: "10px 14px",
                      borderRadius: 10,
                      border: "1px solid #cbd5e1",
                      background: "#f8fafc",
                      color: "#0f172a",
                      cursor: projectDetail?.srt_preview?.length ? "pointer" : "not-allowed",
                      fontWeight: 600,
                    }}
                  >
                    プレビュー内容でリセット
                  </button>
                </div>
              </div>
            </div>
          ) : null}
          <DraftWorkspace
            project={projectDetail}
            selectedDraft={selectedDraft}
            draftDetail={draftDetail}
            loading={projectLoading}
            onQuickJob={handleQuickJob}
            onReplace={handleImageReplace}
            onSelectImage={setActiveImageIndex}
          />
          {projectDetail ? (
            <details className="vp-section-block">
              <summary>画像バリアント</summary>
              <VideoImageVariantsPanel
                project={projectDetail}
                channelPreset={projectChannelPreset}
                generationOptions={generationDraft}
                onQuickJob={handleQuickJob}
              />
            </details>
          ) : null}
          {projectDetail ? (
            <details className="vp-section-block" open>
              <summary>帯 / レイアウト</summary>
              <ProjectBeltEditor
                belts={projectDetail.belt ?? []}
                loading={projectLoading}
                onSave={handleBeltSave}
              />
              <CapcutLayoutEditor
                value={capcutSettingsDraft}
                loading={projectLoading}
                saving={capcutSettingsSaving}
                template={
                  projectDetail.capcut?.templateUsed ??
                  projectDetail.summary?.template_used ??
                  undefined
                }
                onChange={handleCapcutSettingsChange}
                onSave={handleCapcutSettingsSave}
              />
            </details>
          ) : null}
          <details className="vp-section-block">
            <summary>設定</summary>
            {projectDetail ? (
              <ProjectGenerationOptions
                value={generationDraft}
                onChange={handleGenerationChange}
                onSave={handleGenerationSave}
                saving={generationSaving}
              />
            ) : null}
            {selectedChannelPreset ? (
              <ChannelPresetEditorPanel
                preset={selectedChannelPreset}
                draft={presetDraft}
                onChange={handlePresetFieldChange}
                onSave={handlePresetSave}
                saving={presetSaving}
              />
            ) : null}
          </details>
          <details className="vp-section-block">
            <summary>ジョブ履歴</summary>
            <JobTimeline
              jobs={jobRecords}
              loading={jobsLoading}
              onRefresh={refreshJobs}
              onRetry={(action, options) => void handleQuickJob(action, options)}
            />
          </details>
        </div>
      </div>

      <ImageDetailDrawer
        detail={projectDetail}
        draftDetail={draftDetail}
        index={activeImageIndex}
        onClose={() => setActiveImageIndex(null)}
        onReplace={handleImageReplace}
        onRegenerate={handleImageRegenerate}
      />
    </div>
  );
}

function HeroHeader({
  project,
  steps,
  loading,
  banner,
  onRunFullPipeline,
  fullPipelineRunning,
  pipelinePlan,
}: {
  project: VideoProjectDetail | null;
  steps: StepState[];
  loading: boolean;
  banner: string | null;
  onRunFullPipeline: () => Promise<void>;
  fullPipelineRunning: boolean;
  pipelinePlan: PipelinePlan;
}) {
  const summary = project?.summary ?? null;
  const channelId = resolveChannelId(summary ?? undefined);
  const heroSubtitle = summary
    ? `チャンネル ${channelId ?? "—"} / ${summary.status ?? "進行中"}`
    : "左のサイドバーからエピソードを選択してください。";
  const heroTitle = summary?.title ?? summary?.id ?? "エピソード未選択";
  const disabled = fullPipelineRunning || loading || !pipelinePlan.steps.length;
  const hint = !pipelinePlan.steps.length ? pipelinePlan.reason : null;
  return (
    <header className="vp-hero">
      <div className="vp-hero__row">
        <div className="vp-hero__title">
          <h1>動画制作ワークスペース</h1>
          <p>{heroSubtitle}</p>
          <span className="vp-hero__project">{heroTitle}</span>
        </div>
        {banner ? <span className="vp-hero__badge">{banner}</span> : null}
      </div>
      <div className="vp-hero__steps">
        {steps.map((step, index) => (
          <span key={step.id} className={`vp-hero__step is-${step.state}`}>
            <span className="vp-hero__step-index">{index + 1}</span>
            <span className="vp-hero__step-label">{step.label}</span>
          </span>
        ))}
        {loading ? <span className="vp-hero__status">更新中…</span> : null}
      </div>
      <div className="vp-hero__cta">
        <button type="button" onClick={() => onRunFullPipeline()} disabled={disabled}>
          {fullPipelineRunning ? "一括実行中…" : "SRT→CapCut を一括実行"}
        </button>
        {hint ? <span className="vp-hero__cta-hint">{hint}</span> : null}
      </div>
    </header>
  );
}

function ActionButton({
  label,
  description,
  disabled,
  disabledReason,
  onClick,
}: {
  label: string;
  description: string;
  disabled: boolean;
  disabledReason?: string;
  onClick: () => void;
}) {
  return (
    <div className={`vp-action${disabled ? " is-disabled" : ""}`} title={disabledReason ?? undefined}>
      <div>
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
      <button type="button" disabled={disabled} onClick={onClick}>
        {label}
      </button>
    </div>
  );
}

function DraftWorkspace({
  project,
  selectedDraft,
  draftDetail,
  loading,
  onQuickJob,
  onReplace,
  onSelectImage,
}: {
  project: VideoProjectDetail | null;
  selectedDraft: CapcutDraftSummary | null;
  draftDetail: CapcutDraftDetail | null;
  loading: boolean;
  onQuickJob: (action: VideoJobCreatePayload["action"], options?: VideoJobCreatePayload["options"]) => Promise<void>;
  onReplace: (assetPath: string, file: File) => Promise<void>;
  onSelectImage: (index: number | null) => void;
}) {
  const projectId = project?.summary?.id ?? null;
  const [visualPlan, setVisualPlan] = useState<VisualCuesPlanArtifact | null>(null);
  const [visualPlanDraft, setVisualPlanDraft] = useState<VisualCuesPlanSection[]>([]);
  const [visualPlanStyleHint, setVisualPlanStyleHint] = useState("");
  const [visualPlanLoading, setVisualPlanLoading] = useState(false);
  const [visualPlanSaving, setVisualPlanSaving] = useState(false);
  const [visualPlanError, setVisualPlanError] = useState<string | null>(null);
  const [srtSegments, setSrtSegments] = useState<SrtSegmentsArtifact | null>(null);
  const [srtSegmentsLoading, setSrtSegmentsLoading] = useState(false);
  const [srtSegmentsError, setSrtSegmentsError] = useState<string | null>(null);

  const visualPlanDirty = useMemo(() => {
    const baseSections = visualPlan?.sections ?? [];
    const baseStyle = visualPlan?.style_hint ?? "";
    return JSON.stringify(baseSections) !== JSON.stringify(visualPlanDraft) || baseStyle !== visualPlanStyleHint;
  }, [visualPlan, visualPlanDraft, visualPlanStyleHint]);

  const reloadVisualPlan = useCallback(async () => {
    if (!projectId) {
      setVisualPlan(null);
      setVisualPlanDraft([]);
      setVisualPlanStyleHint("");
      return;
    }
    setVisualPlanLoading(true);
    setVisualPlanError(null);
    try {
      const plan = await fetchProjectVisualCuesPlan(projectId);
      setVisualPlan(plan);
      setVisualPlanDraft(plan.sections ?? []);
      setVisualPlanStyleHint(plan.style_hint ?? "");
    } catch (error) {
      setVisualPlan(null);
      setVisualPlanDraft([]);
      setVisualPlanStyleHint("");
      setVisualPlanError(error instanceof Error ? error.message : String(error));
    } finally {
      setVisualPlanLoading(false);
    }
  }, [projectId]);

  const reloadSrtSegments = useCallback(async () => {
    if (!projectId) {
      setSrtSegments(null);
      return;
    }
    setSrtSegmentsLoading(true);
    setSrtSegmentsError(null);
    try {
      const data = await fetchProjectSrtSegments(projectId);
      setSrtSegments(data);
    } catch (error) {
      setSrtSegments(null);
      setSrtSegmentsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSrtSegmentsLoading(false);
    }
  }, [projectId]);

  const saveVisualPlan = useCallback(
    async (status: "pending" | "ready") => {
      if (!projectId) return;
      setVisualPlanSaving(true);
      setVisualPlanError(null);
      try {
        const updated = await updateProjectVisualCuesPlan(projectId, {
          status,
          sections: visualPlanDraft,
          styleHint: visualPlanStyleHint || null,
        });
        setVisualPlan(updated);
        setVisualPlanDraft(updated.sections ?? []);
        setVisualPlanStyleHint(updated.style_hint ?? "");
      } catch (error) {
        setVisualPlanError(error instanceof Error ? error.message : String(error));
      } finally {
        setVisualPlanSaving(false);
      }
    },
    [projectId, visualPlanDraft, visualPlanStyleHint]
  );

  useEffect(() => {
    void reloadVisualPlan();
    void reloadSrtSegments();
  }, [reloadSrtSegments, reloadVisualPlan]);

  if (loading && !project && !selectedDraft) {
    return (
      <section className="vp-workspace">
        <p className="vp-empty">エピソード情報を読み込んでいます…</p>
      </section>
    );
  }
  const summary = project?.summary ?? null;
  const status = project?.sourceStatus ?? (project as { source_status?: SourceStatus })?.source_status;
  const guard = project?.guard;
  const guardStatus = guard?.status ?? (guard?.issues?.length ? "danger" : "unknown");
  const guardLabel = getIntegrityStatusLabel(guardStatus);
  const guardIssues = guard?.issues ?? [];
  const channelId = resolveChannelId(summary ?? undefined) ?? selectedDraft?.channelId ?? undefined;
  const storyboardDone = summary?.status !== "pending" && Boolean(summary);
  const imagesDone = summary?.status === "images_ready" || summary?.status === "draft_ready";
  const analyzeDisabledReason = !summary
    ? "プロジェクト情報を読み込み中"
    : summary.status !== "pending"
      ? "SRT解析は完了済み"
      : null;
  const generateDisabledReason = storyboardDone ? null : "先に SRT解析 を完了してください";
  const guardDisabledReason = imagesDone ? null : "画像が未生成です";
  const capcutDisabledReason = guardStatus === "ok" ? null : "整合チェックを通過させてください";
  const assets =
    (project?.images && project.images.length ? project.images : project?.image_samples) ?? [];
  const runJob = (action: VideoJobCreatePayload["action"], extra: VideoJobCreatePayload["options"] = {}) =>
    onQuickJob(action, {
      channel: channelId ?? undefined,
      ...extra,
    });

  const visualPlanSegmentCount = visualPlan?.segment_count ?? srtSegments?.segments?.length ?? null;

  const updatePlanSection = (index: number, patch: Partial<VisualCuesPlanSection>) => {
    setVisualPlanDraft((current) =>
      current.map((section, idx) => (idx === index ? { ...section, ...patch } : section))
    );
  };

  const removePlanSection = (index: number) => {
    setVisualPlanDraft((current) => current.filter((_, idx) => idx !== index));
  };

  const addPlanSection = () => {
    setVisualPlanDraft((current) => {
      const last = current[current.length - 1];
      const start = Math.max(1, Number(last?.end_segment ?? 0) + 1);
      const clampedStart =
        typeof visualPlanSegmentCount === "number" ? Math.min(start, visualPlanSegmentCount) : start;
      const next: VisualCuesPlanSection = {
        start_segment: clampedStart,
        end_segment: clampedStart,
        summary: "",
        visual_focus: "",
        emotional_tone: "",
        persona_needed: false,
        role_tag: "",
        section_type: "",
      };
      return [...current, next];
    });
  };

  return (
    <section className="vp-workspace">
      <div className="vp-workspace__summary">
        {project ? (
          <div className="vp-summary-grid">
            <div>
              <span>エピソード</span>
              <strong>{summary?.title ?? summary?.id ?? "—"}</strong>
            </div>
            <div>
              <span>チャンネル</span>
              <strong>{channelId ?? "—"}</strong>
            </div>
            <div>
              <span>SRT</span>
              <strong>{status?.srtReady ? "READY" : "未"}</strong>
            </div>
            <div>
              <span>音声</span>
              <strong>{status?.audioReady ? "READY" : "未"}</strong>
            </div>
            <div>
              <span>CapCutテンプレ</span>
              <strong>{project.capcut?.templateUsed ?? "—"}</strong>
            </div>
            <div>
              <span>Transform</span>
              <strong>
                x:{project.capcut?.transform?.tx ?? 0} / y:{project.capcut?.transform?.ty ?? 0} / scale:
                {project.capcut?.transform?.scale ?? 1}
              </strong>
            </div>
          </div>
        ) : selectedDraft ? (
          <div className="vp-summary-grid">
            <div>
              <span>ドラフト</span>
              <strong>{selectedDraft.title ?? selectedDraft.name}</strong>
            </div>
            <div>
              <span>チャンネル</span>
              <strong>{selectedDraft.channelName ?? selectedDraft.channelId ?? "—"}</strong>
            </div>
            <div>
              <span>プロジェクトID</span>
              <strong>{selectedDraft.projectId ?? selectedDraft.projectHint ?? "未紐付"}</strong>
            </div>
            <div>
              <span>画像枚数</span>
              <strong>{selectedDraft.imageCount}</strong>
            </div>
          </div>
        ) : null}
        <span className={`vp-guard vp-guard--${guardStatus}`}>{guardLabel}</span>
      </div>
      <div className="vp-workspace__actions">
        <ActionButton
          label="SRT解析"
          description="LLM でチャンク/プロンプトを生成"
          disabled={Boolean(analyzeDisabledReason)}
          disabledReason={analyzeDisabledReason ?? undefined}
          onClick={() => runJob("analyze_srt")}
        />
        <ActionButton
          label="画像/帯"
          description="画像生成 + 帯再作成"
          disabled={Boolean(generateDisabledReason)}
          disabledReason={generateDisabledReason ?? undefined}
          onClick={() =>
            runJob("regenerate_images", {
              imgdur: DEFAULT_GENERATION_OPTIONS.imgdur,
              crossfade: DEFAULT_GENERATION_OPTIONS.crossfade,
              fps: DEFAULT_GENERATION_OPTIONS.fps,
              style: DEFAULT_GENERATION_OPTIONS.style,
            })
          }
        />
        <ActionButton
          label={INTEGRITY_LABEL}
          description="ファイル整合チェック"
          disabled={Boolean(guardDisabledReason)}
          disabledReason={guardDisabledReason ?? undefined}
          onClick={() => runJob("validate_capcut", { use_existing_draft: true })}
        />
        <ActionButton
          label="CapCutドラフト"
          description="テンプレへ配置"
          disabled={Boolean(capcutDisabledReason)}
          disabledReason={capcutDisabledReason ?? undefined}
          onClick={() => runJob("build_capcut_draft")}
        />
      </div>
      {project?.artifacts?.items?.length ? (
        <div className="vp-draft-segments" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
            <strong>Artifacts</strong>
            {project.artifacts.project_dir ? (
              <span className="vp-draft-meta" style={{ textAlign: "right" }}>
                {project.artifacts.project_dir}
              </span>
            ) : null}
          </div>
          {project.artifacts.items.map((item) => {
            const metaText = formatArtifactMeta(item.meta);
            const statusClass = item.exists ? "is-linked" : "is-warning";
            const statusLabel = item.exists ? "OK" : "MISSING";
            return (
              <div key={item.key} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <strong style={{ fontSize: 13 }}>{item.label}</strong>{" "}
                  <span className="vp-draft-meta" style={{ display: "inline" }}>
                    {item.path}
                  </span>
                  {metaText ? <span className="vp-draft-meta">{metaText}</span> : null}
                </div>
                <span className={`vp-draft-status ${statusClass}`}>{statusLabel}</span>
              </div>
            );
          })}
        </div>
      ) : null}
      {project ? (
        <details className="vp-draft-segments" style={{ marginTop: 12 }}>
          <summary>
            Visual cues plan（箱）
            {visualPlan ? ` / ${visualPlan.status}` : ""}
            {visualPlanDirty ? " / 未保存" : ""}
          </summary>
          <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
            <p className="vp-draft-meta">
              セクション分割（start/end）と要約・視覚指示をここで確定すると、後続ジョブが機械的に進められます（等間隔分割ではなく文脈ベースで調整してください）。
            </p>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
              <div className="vp-draft-meta" style={{ minWidth: 0 }}>
                {visualPlan ? (
                  <>
                    segments: {visualPlan.segment_count} / base: {visualPlan.base_seconds.toFixed(2)}s{" "}
                    <span style={{ wordBreak: "break-all" }}>
                      / srt: <code>{visualPlan.source_srt?.path}</code>
                    </span>
                  </>
                ) : (
                  <span>visual_cues_plan.json が未生成（または取得できません）</span>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" onClick={() => void reloadVisualPlan()} disabled={visualPlanLoading || visualPlanSaving}>
                  再読込
                </button>
                <button
                  type="button"
                  onClick={() => void saveVisualPlan("pending")}
                  disabled={!visualPlan || visualPlanLoading || visualPlanSaving || !visualPlanDirty}
                >
                  下書き保存（pending）
                </button>
                <button
                  type="button"
                  onClick={() => void saveVisualPlan("ready")}
                  disabled={!visualPlan || visualPlanLoading || visualPlanSaving}
                >
                  確定保存（ready）
                </button>
              </div>
            </div>
            {visualPlanLoading ? <p className="vp-draft-meta">読み込み中…</p> : null}
            {visualPlanError ? <p className="error">{visualPlanError}</p> : null}
            {!visualPlan && !visualPlanLoading ? (
              <p className="muted">
                先に cues plan 経路（THINK/AGENT or <code>SRT2IMAGES_CUES_PLAN_MODE=plan</code>）で{" "}
                <code>visual_cues_plan.json</code> を作ってから、ここで埋めてください。
              </p>
            ) : null}
            {visualPlan ? (
              <>
                <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
                  style_hint
                  <input
                    value={visualPlanStyleHint}
                    onChange={(event) => setVisualPlanStyleHint(event.target.value)}
                    disabled={visualPlanSaving || visualPlanLoading}
                    style={{
                      width: "100%",
                      borderRadius: 10,
                      border: "1px solid #cbd5e1",
                      padding: "8px 10px",
                      background: "#fff",
                    }}
                    placeholder="（任意）このrunの画作りのヒント"
                  />
                </label>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
                  <strong>Sections</strong>
                  <button type="button" onClick={() => addPlanSection()} disabled={visualPlanSaving || visualPlanLoading}>
                    + 追加
                  </button>
                </div>
                {visualPlanDraft.length === 0 ? (
                  <p className="muted">sections が空です。まずセクションを追加してください。</p>
                ) : null}
                <div style={{ display: "grid", gap: 10 }}>
                  {visualPlanDraft.map((section, index) => (
                    <div
                      key={`visual-plan-${index}`}
                      style={{
                        border: "1px solid #cbd5e1",
                        borderRadius: 12,
                        padding: 12,
                        background: "#fff",
                      }}
                    >
                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                        <label className="vp-draft-meta">
                          start
                          <input
                            type="number"
                            min={1}
                            value={section.start_segment}
                            onChange={(event) => {
                              const nextStart = Math.max(1, Number.parseInt(event.target.value || "1", 10));
                              const end = Math.max(nextStart, section.end_segment);
                              updatePlanSection(index, { start_segment: nextStart, end_segment: end });
                            }}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{ width: 88, marginLeft: 6 }}
                          />
                        </label>
                        <label className="vp-draft-meta">
                          end
                          <input
                            type="number"
                            min={1}
                            value={section.end_segment}
                            onChange={(event) => {
                              const nextEnd = Math.max(1, Number.parseInt(event.target.value || "1", 10));
                              const start = Math.min(section.start_segment, nextEnd);
                              updatePlanSection(index, { start_segment: start, end_segment: nextEnd });
                            }}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{ width: 88, marginLeft: 6 }}
                          />
                        </label>
                        <label className="vp-draft-meta">
                          <input
                            type="checkbox"
                            checked={section.persona_needed}
                            onChange={(event) => updatePlanSection(index, { persona_needed: event.target.checked })}
                            disabled={visualPlanSaving || visualPlanLoading}
                          />{" "}
                          persona_needed
                        </label>
                        <label className="vp-draft-meta">
                          role_tag
                          <input
                            value={section.role_tag}
                            onChange={(event) => updatePlanSection(index, { role_tag: event.target.value })}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{ width: 160, marginLeft: 6 }}
                          />
                        </label>
                        <label className="vp-draft-meta">
                          section_type
                          <input
                            value={section.section_type}
                            onChange={(event) => updatePlanSection(index, { section_type: event.target.value })}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{ width: 160, marginLeft: 6 }}
                          />
                        </label>
                        <button
                          type="button"
                          onClick={() => removePlanSection(index)}
                          disabled={visualPlanSaving || visualPlanLoading}
                        >
                          削除
                        </button>
                      </div>
                      <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
                          summary
                          <textarea
                            value={section.summary}
                            onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                              updatePlanSection(index, { summary: event.target.value })
                            }
                            disabled={visualPlanSaving || visualPlanLoading}
                            rows={2}
                            style={{
                              width: "100%",
                              borderRadius: 10,
                              border: "1px solid #cbd5e1",
                              padding: "8px 10px",
                            }}
                          />
                        </label>
                        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
                          visual_focus
                          <input
                            value={section.visual_focus}
                            onChange={(event) => updatePlanSection(index, { visual_focus: event.target.value })}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{
                              width: "100%",
                              borderRadius: 10,
                              border: "1px solid #cbd5e1",
                              padding: "8px 10px",
                            }}
                          />
                        </label>
                        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
                          emotional_tone
                          <input
                            value={section.emotional_tone}
                            onChange={(event) => updatePlanSection(index, { emotional_tone: event.target.value })}
                            disabled={visualPlanSaving || visualPlanLoading}
                            style={{
                              width: "100%",
                              borderRadius: 10,
                              border: "1px solid #cbd5e1",
                              padding: "8px 10px",
                            }}
                          />
                        </label>
                      </div>
                    </div>
                  ))}
                </div>

                <details style={{ marginTop: 10 }}>
                  <summary>
                    SRT segments（参考）{srtSegments?.segments?.length ? `: ${srtSegments.segments.length}` : ""}
                  </summary>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                    <button type="button" onClick={() => void reloadSrtSegments()} disabled={srtSegmentsLoading}>
                      segments を再読込
                    </button>
                  </div>
                  {srtSegmentsLoading ? <p className="vp-draft-meta">読み込み中…</p> : null}
                  {srtSegmentsError ? <p className="error">{srtSegmentsError}</p> : null}
                  {srtSegments?.segments?.length ? (
                    <div
                      style={{
                        maxHeight: 240,
                        overflow: "auto",
                        borderRadius: 12,
                        border: "1px solid #cbd5e1",
                        padding: 12,
                        background: "#fff",
                        fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                        fontSize: 12,
                        whiteSpace: "pre-wrap",
                        lineHeight: 1.35,
                      }}
                    >
                      {srtSegments.segments.map((seg) => (
                        <div key={seg.index}>
                          <span style={{ color: "#64748b" }}>
                            #{seg.index} {formatTime(seg.start_sec)}-{formatTime(seg.end_sec)}
                          </span>{" "}
                          <span>{seg.text}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </details>
              </>
            ) : null}
          </div>
        </details>
      ) : null}
      {guardIssues.length ? (
        <ul className="vp-guard-issues">
          {guardIssues.slice(0, 4).map((issue) => (
            <li key={issue.code}>{issue.message}</li>
          ))}
        </ul>
      ) : null}
      <div className="vp-gallery">
        {!project ? (
          <p className="vp-empty">
            {selectedDraft
              ? selectedDraft.projectId
                ? `ドラフト ${selectedDraft.projectId} の成果フォルダを取得できませんでした。SoT を確認してください。`
                : "このドラフトに紐づくプロジェクトがありません。"
              : "ドラフトを選択してください。"}
          </p>
        ) : assets.length === 0 ? (
          <p className="vp-empty">画像がまだ生成されていません。画像/帯ジョブを実行してください。</p>
        ) : (
          assets.map((asset, index) => (
            <figure key={asset.path} className="vp-image-card" onClick={() => onSelectImage(index)}>
              <img
                src={buildAssetUrl(
                  asset.path,
                  "modified_at" in asset ? (asset as VideoProjectImageAsset).modified_at : undefined
                )}
                alt={asset.path}
              />
              <figcaption>
                <span>{asset.path.split("/").pop()}</span>
                <label
                  className="vp-image-upload"
                  onClick={(event) => event.stopPropagation()}
                >
                  差し替え
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) {
                        void onReplace(asset.path, file);
                        event.target.value = "";
                      }
                    }}
                  />
                </label>
              </figcaption>
            </figure>
          ))
        )}
      </div>
      {draftDetail?.segments?.length ? (
        <div className="vp-draft-segments">
          {draftDetail.segments.slice(0, 6).map((segment) => (
            <div key={segment.materialId}>
              <span>{segment.filename}</span>
              <span>
                {formatTime(segment.startSec)} - {formatTime(segment.endSec)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function CapcutDraftBoard({
  channelFilter,
  onChannelChange,
  channelOptions,
  readyProjects,
  selectedProjectId,
  onProjectChange,
  drafts,
  selectedDraft,
  draftDetail,
  search,
  onSearchChange,
  sortMode,
  onSortModeChange,
  linkFilter,
  onLinkFilterChange,
  installingDraft,
  onInstallDraft,
  onSelectDraft,
}: {
  channelFilter: string;
  onChannelChange: (value: string) => void;
  channelOptions: VideoProductionChannelPreset[];
  readyProjects: VideoProjectSummary[];
  selectedProjectId: string | null;
  onProjectChange: (value: string | null) => void;
  drafts: CapcutDraftSummary[];
  selectedDraft: CapcutDraftSummary | null;
  draftDetail: CapcutDraftDetail | null;
  search: string;
  onSearchChange: (value: string) => void;
  sortMode: "recent" | "oldest";
  onSortModeChange: (value: "recent" | "oldest") => void;
  linkFilter: "all" | "linked" | "unlinked";
  onLinkFilterChange: (value: "all" | "linked" | "unlinked") => void;
  installingDraft: string | null;
  onInstallDraft: (draft: CapcutDraftSummary) => void;
  onSelectDraft: (draft: CapcutDraftSummary) => void;
}) {
  return (
    <aside className="vp-board">
      <div className="vp-board__filters">
        <label>
          <span>チャンネル</span>
          <select value={channelFilter} onChange={(event) => onChannelChange(event.target.value)}>
            <option value="">全て</option>
            {channelOptions.map((preset) => (
              <option key={preset.channelId} value={preset.channelId}>
                {preset.name ?? preset.channelId}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>READYエピソード</span>
          <select value={selectedProjectId ?? ""} onChange={(event) => onProjectChange(event.target.value || null)}>
            <option value="">未選択</option>
            {readyProjects.map((project) => (
              <option key={project.id} value={project.id}>
                {((project as { summary?: { title?: string } }).summary?.title ?? project.title ?? project.id)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>検索</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="ドラフト名/ID"
          />
        </label>
        <label>
          <span>並び順</span>
          <select value={sortMode} onChange={(event) => onSortModeChange(event.target.value as "recent" | "oldest")}
          >
            <option value="recent">更新が新しい順</option>
            <option value="oldest">古い順</option>
          </select>
        </label>
        <div className="vp-board__chips">
          {[
            { key: "all", label: "すべて" },
            { key: "linked", label: "SoTあり" },
            { key: "unlinked", label: "未紐付" },
          ].map((chip) => (
            <button
              key={chip.key}
              type="button"
              className={chip.key === linkFilter ? "is-active" : ""}
              onClick={() => onLinkFilterChange(chip.key as typeof linkFilter)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>
      <div className="vp-draft-list">
        {drafts.map((draft) => {
          const active = selectedDraft?.name === draft.name;
          const linked = Boolean(draft.projectId);
          return (
            <button
              key={draft.name}
              type="button"
              className={`vp-draft-row${active ? " is-active" : ""}`}
              onClick={() => onSelectDraft(draft)}
            >
              <div>
                <strong>{draft.title ?? draft.name}</strong>
                <span className="vp-draft-meta">{draft.modifiedTimeIso ?? "—"}</span>
              </div>
              <span className={`vp-draft-status ${linked ? "is-linked" : "is-warning"}`}>
                {linked ? "SoT" : "未紐付"}
              </span>
            </button>
          );
        })}
      </div>
      {selectedDraft ? (
        <details className="vp-board__detail" open>
          <summary>選択中のドラフト</summary>
          <div className="vp-board__detail-body">
            <p>チャンネル: {selectedDraft.channelName ?? selectedDraft.channelId ?? "—"}</p>
            <p>
              プロジェクト: {selectedDraft.projectId ?? "未紐付"}
              {selectedDraft.projectId ? null : selectedDraft.projectHint ? ` (${selectedDraft.projectHint})` : null}
            </p>
            <p>画像枚数: {selectedDraft.imageCount}</p>
            {!selectedDraft.projectId ? (
              <p className="video-production-alert video-production-alert--warning">
                CapCut ドラフトと SoT が紐付いていません。
              </p>
            ) : null}
            <div className="vp-board__detail-actions">
              <button
                type="button"
                disabled={!selectedDraft.projectId || installingDraft === selectedDraft.name}
                onClick={() => onInstallDraft(selectedDraft)}
              >
                {installingDraft === selectedDraft.name ? "コピー中…" : "CapCutへコピー"}
              </button>
            </div>
            {draftDetail?.segments?.length ? (
              <div className="vp-draft-segments">
                {draftDetail.segments.slice(0, 4).map((segment) => (
                  <div key={segment.materialId}>
                    <span>{segment.filename}</span>
                    <span>
                      {formatTime(segment.startSec)} - {formatTime(segment.endSec)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="vp-empty">セグメント情報はありません。</p>
            )}
          </div>
        </details>
      ) : (
        <p className="vp-empty">ドラフトを選択してください。</p>
      )}
    </aside>
  );
}

function ProjectGenerationOptions({
  value,
  onChange,
  onSave,
  saving,
}: {
  value: VideoGenerationOptions;
  onChange: (key: keyof VideoGenerationOptions, value: number | string) => void;
  onSave: () => Promise<void>;
  saving: boolean;
}) {
  return (
    <div className="vp-panel">
      <div className="vp-panel__header">
        <div>
          <h2>生成パラメータ</h2>
          <p className="video-production-text-muted">画像生成やタイミングに影響する値を調整します。</p>
        </div>
      </div>
      <div className="vp-options-grid">
        <label>
          <span>表示秒数 (imgdur)</span>
          <input
            type="number"
            min="1"
            step="1"
            value={value.imgdur}
            onChange={(event) => onChange("imgdur", Number(event.target.value))}
          />
        </label>
        <label>
          <span>クロスフェード (秒)</span>
          <input
            type="number"
            min="0"
            step="0.1"
            value={value.crossfade}
            onChange={(event) => onChange("crossfade", Number(event.target.value))}
          />
        </label>
        <label>
          <span>FPS</span>
          <input
            type="number"
            min="1"
            step="1"
            value={value.fps}
            onChange={(event) => onChange("fps", Number(event.target.value))}
          />
        </label>
        <label>
          <span>スタイル</span>
          <input
            type="text"
            value={value.style}
            onChange={(event) => onChange("style", event.target.value)}
          />
        </label>
      </div>
      <div className="vp-options-actions">
        <button type="button" onClick={() => void onSave()} disabled={saving}>
          {saving ? "保存中…" : "生成パラメータを保存"}
        </button>
      </div>
    </div>
  );
}

type BeltEditorEntry = {
  text: string;
  start: number;
  end: number;
};

function ProjectBeltEditor({
  belts,
  loading,
  onSave,
}: {
  belts: VideoProjectBeltEntry[];
  loading: boolean;
  onSave: (index: number, entry: Partial<VideoProjectBeltEntry>) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<BeltEditorEntry[]>(() =>
    belts.map((belt) => ({ text: belt.text ?? "", start: belt.start ?? 0, end: belt.end ?? 0 }))
  );
  const [savingIndex, setSavingIndex] = useState<number | null>(null);
  const [errorIndex, setErrorIndex] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(belts.map((belt) => ({ text: belt.text ?? "", start: belt.start ?? 0, end: belt.end ?? 0 })));
  }, [belts]);

  if (loading || !belts.length) {
    return null;
  }

  const handleSave = async (index: number) => {
    setSavingIndex(index);
    setErrorIndex(null);
    try {
      const payload = drafts[index];
      await onSave(index, payload);
    } catch (error) {
      setErrorIndex(
        error instanceof Error ? error.message : "帯テキストの保存に失敗しました。画面を再読み込みして再試行してください。"
      );
    } finally {
      setSavingIndex(null);
    }
  };

  return (
    <div className="vp-panel">
      <div className="vp-panel__header">
        <div>
          <h2>帯テキスト</h2>
          <p className="video-production-text-muted">チャンクタイトルやサマリを編集します。</p>
        </div>
      </div>
      <div className="vp-belt-grid">
        {drafts.map((draft, index) => (
          <div key={`belt-${index}`} className="vp-belt-card">
            <header>
              <strong>#{index + 1}</strong>
              <span>
                {belts[index]?.start?.toFixed?.(1) ?? "0"}s - {belts[index]?.end?.toFixed?.(1) ?? "0"}s
              </span>
            </header>
            <label>
              <span>テキスト</span>
              <textarea
                value={draft.text}
                onChange={(event) =>
                  setDrafts((prev) => {
                    const next = [...prev];
                    next[index] = { ...next[index], text: event.target.value };
                    return next;
                  })
                }
                rows={3}
              />
            </label>
            <div className="vp-belt-times">
              <label>
                <span>開始 (秒)</span>
                <input
                  type="number"
                  step="0.1"
                  value={draft.start}
                  onChange={(event) =>
                    setDrafts((prev) => {
                      const next = [...prev];
                      next[index] = { ...next[index], start: Number(event.target.value) };
                      return next;
                    })
                  }
                />
              </label>
              <label>
                <span>終了 (秒)</span>
                <input
                  type="number"
                  step="0.1"
                  value={draft.end}
                  onChange={(event) =>
                    setDrafts((prev) => {
                      const next = [...prev];
                      next[index] = { ...next[index], end: Number(event.target.value) };
                      return next;
                    })
                  }
                />
              </label>
            </div>
            <div className="vp-belt-actions">
              <button type="button" onClick={() => void handleSave(index)} disabled={savingIndex === index}>
                {savingIndex === index ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        ))}
      </div>
      {errorIndex ? <p className="video-production-alert video-production-alert--error">{errorIndex}</p> : null}
    </div>
  );
}

function CapcutLayoutEditor({
  value,
  loading,
  saving,
  template,
  onChange,
  onSave,
}: {
  value: CapcutLayoutDraft | null;
  loading: boolean;
  saving: boolean;
  template?: string | null;
  onChange: (key: keyof CapcutLayoutDraft, value: number) => void;
  onSave: () => Promise<void> | void;
}) {
  if (loading || !value) {
    return null;
  }
  const handleChange = (key: keyof CapcutLayoutDraft) => (event: ChangeEvent<HTMLInputElement>) => {
    onChange(key, Number(event.target.value));
  };
  return (
    <div className="vp-panel">
      <div className="vp-panel__header">
        <div>
          <h2>レイアウト / フェード</h2>
          <p className="video-production-text-muted">CapCut ドラフトのトランスフォームやフェードを微調整します。</p>
        </div>
        {template ? <span className="video-production-text-muted">テンプレ: {template}</span> : null}
      </div>
      <div className="vp-options-grid">
        <label>
          <span>Transform X</span>
          <input type="number" step="0.01" value={value.tx} onChange={handleChange("tx")} />
        </label>
        <label>
          <span>Transform Y</span>
          <input type="number" step="0.01" value={value.ty} onChange={handleChange("ty")} />
        </label>
        <label>
          <span>Scale</span>
          <input type="number" step="0.01" min="0.1" value={value.scale} onChange={handleChange("scale")} />
        </label>
        <label>
          <span>クロスフェード (秒)</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={value.crossfadeSec}
            onChange={handleChange("crossfadeSec")}
          />
        </label>
        <label>
          <span>フェード (秒)</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={value.fadeDurationSec}
            onChange={handleChange("fadeDurationSec")}
          />
        </label>
        <label>
          <span>冒頭オフセット (秒)</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={value.openingOffset}
            onChange={handleChange("openingOffset")}
          />
        </label>
      </div>
      <div className="vp-options-actions">
        <button type="button" onClick={() => void onSave()} disabled={saving}>
          {saving ? "保存中…" : "レイアウトを保存"}
        </button>
      </div>
    </div>
  );
}

function JobTimeline({
  jobs,
  loading,
  onRefresh,
  onRetry,
}: {
  jobs: VideoJobRecord[];
  loading: boolean;
  onRefresh: () => void;
  onRetry: (action: VideoJobCreatePayload["action"], options?: VideoJobCreatePayload["options"]) => void;
}) {
  return (
    <div className="vp-panel">
      <div className="vp-panel__header">
        <div>
          <h2>ジョブ進行状況</h2>
          <p className="video-production-text-muted">直近の CLI 実行ログ</p>
        </div>
        <button type="button" className="vp-button vp-button--ghost" onClick={() => onRefresh()} disabled={loading}>
          {loading ? "更新中…" : "最新表示"}
        </button>
      </div>
      {jobs.length === 0 ? (
        <p className="vp-empty">ジョブ履歴がありません。</p>
      ) : (
        <ul className="vp-job-list">
          {jobs.map((job) => (
            <li key={job.id} className={`vp-job is-${job.status}`}>
              <header>
                <strong>{JOB_LABELS[job.action] ?? job.summary ?? job.action}</strong>
                <span className={`vp-job__status is-${job.status}`}>{JOB_STATUS_LABELS[job.status] ?? job.status}</span>
              </header>
              <div className="vp-job__meta">
                <span>開始: {formatJobTimestamp(job.created_at)}</span>
                <span>
                  終了: {job.finished_at ? formatJobTimestamp(job.finished_at) : job.status === "running" ? "実行中" : "—"}
                </span>
                <span>所要: {formatJobDuration(job)}</span>
              </div>
              {job.log_excerpt?.length ? (
                <p className="vp-job__log">{job.log_excerpt[job.log_excerpt.length - 1]}</p>
              ) : null}
              {job.error ? <p className="vp-job__error">{job.error}</p> : null}
              {job.status === "failed" ? (
                <div className="vp-job__actions">
                  <button
                    type="button"
                    onClick={() =>
                      onRetry(job.action, (job.options ?? undefined) as VideoJobCreatePayload["options"])
                    }
                  >
                    再実行
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChannelPresetEditorPanel({
  preset,
  draft,
  onChange,
  onSave,
  saving,
}: {
  preset: VideoProductionChannelPreset | null;
  draft: ChannelPresetDraft | null;
  onChange: (key: keyof ChannelPresetDraft, value: string | number | boolean) => void;
  onSave: () => Promise<void> | void;
  saving: boolean;
}) {
  if (!preset || !draft) {
    return null;
  }
  return (
    <div className="vp-panel">
      <div className="vp-panel__header">
        <div>
          <h2>チャンネルプリセット</h2>
          <p className="video-production-text-muted">テンプレ名や transform を編集できます。</p>
        </div>
        <span className="video-production-text-muted">{preset.channelId}</span>
      </div>
      <div className="vp-preset-grid">
        <label>
          <span>CapCut テンプレ</span>
          <input
            type="text"
            value={draft.capcutTemplate}
            onChange={(event) => onChange("capcutTemplate", event.target.value)}
          />
        </label>
        <label>
          <span>プロンプトテンプレ</span>
          <input
            type="text"
            value={draft.promptTemplate}
            onChange={(event) => onChange("promptTemplate", event.target.value)}
          />
        </label>
        <label>
          <span>スタイル</span>
          <input type="text" value={draft.style} onChange={(event) => onChange("style", event.target.value)} />
        </label>
        <label>
          <span>Transform X</span>
          <input
            type="number"
            step="0.01"
            value={draft.tx}
            onChange={(event) => onChange("tx", Number(event.target.value))}
          />
        </label>
        <label>
          <span>Transform Y</span>
          <input
            type="number"
            step="0.01"
            value={draft.ty}
            onChange={(event) => onChange("ty", Number(event.target.value))}
          />
        </label>
        <label>
          <span>Scale</span>
          <input
            type="number"
            step="0.01"
            value={draft.scale}
            onChange={(event) => onChange("scale", Number(event.target.value))}
          />
        </label>
        <label>
          <span>帯オフセット (秒)</span>
          <input
            type="number"
            step="0.1"
            value={draft.beltOpening}
            onChange={(event) => onChange("beltOpening", Number(event.target.value))}
          />
        </label>
        <label className="vp-preset-checkbox">
          <input
            type="checkbox"
            checked={draft.beltRequiresConfig}
            onChange={(event) => onChange("beltRequiresConfig", event.target.checked)}
          />
          <span>帯 config 必須</span>
        </label>
        <label className="vp-preset-checkbox">
          <input
            type="checkbox"
            checked={draft.beltEnabled}
            onChange={(event) => onChange("beltEnabled", event.target.checked)}
          />
          <span>帯を有効にする</span>
        </label>
      </div>
      <label>
        <span>メモ</span>
        <textarea
          rows={2}
          value={draft.notes}
          onChange={(event) => onChange("notes", event.target.value)}
        />
      </label>
      <div className="vp-options-actions">
        <button type="button" onClick={() => void onSave()} disabled={saving}>
          {saving ? "保存中…" : "プリセットを保存"}
        </button>
      </div>
    </div>
  );
}

function ImageDetailDrawer({
  detail,
  draftDetail,
  index,
  onClose,
  onReplace,
  onRegenerate,
}: {
  detail: VideoProjectDetail | null;
  draftDetail: CapcutDraftDetail | null;
  index: number | null;
  onClose: () => void;
  onReplace: (assetPath: string, file: File) => Promise<void>;
  onRegenerate: (imageIndex: number, promptOverride: string | null) => Promise<void>;
}) {
  const [promptValue, setPromptValue] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);

  useEffect(() => {
    if (index === null || !detail) {
      setPromptValue("");
      setRegenerateError(null);
      return;
    }
    if (!detail.cues || index < 0 || index >= detail.cues.length) {
      setPromptValue("");
      setRegenerateError(null);
      return;
    }
    const cue = detail.cues[index];
    setPromptValue(cue?.prompt ?? "");
    setRegenerateError(null);
  }, [detail, index]);

  if (index === null || !detail) {
    return null;
  }
  const assets = detail.images && detail.images.length ? detail.images : detail.image_samples;
  if (!assets || index < 0 || index >= assets.length) {
    return null;
  }
  const asset = assets[index];
  const cue = detail.cues[index] ?? null;
  const draftImage = draftDetail?.segments?.[index];
  const downloadUrl = buildAssetUrl(
    asset.path,
    "modified_at" in asset ? (asset as VideoProjectImageAsset).modified_at : undefined
  );
  const roleAssetUrl =
    cue?.role_asset?.path && cue.role_asset.path.startsWith("http")
      ? cue.role_asset.path
      : cue?.role_asset?.path
        ? resolveApiUrl(`/api/video-production/assets/${cue.role_asset.path}`)
        : null;

  const handleRegenerate = async () => {
    setRegenerating(true);
    setRegenerateError(null);
    try {
      await onRegenerate(index, promptValue);
    } catch (error) {
      setRegenerateError(error instanceof Error ? error.message : String(error));
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <div className="vp-image-drawer">
      <div className="vp-image-drawer__content">
        <header>
          <h3>画像 #{index + 1}</h3>
          <button type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="vp-image-drawer__body">
          <div className="vp-image-drawer__preview">
            <img src={downloadUrl} alt={asset.path} />
          </div>
          <div className="vp-image-drawer__meta">
            <dl>
              <dt>ファイル名</dt>
              <dd>{asset.path.split("/").pop()}</dd>
              <dt>開始〜終了</dt>
              <dd>
                {draftImage
                  ? `${formatTime(draftImage.startSec)}〜${formatTime(draftImage.endSec)}`
                  : cue
                    ? `${formatTime(cue.start_sec)}〜${formatTime(cue.end_sec)}`
                    : "—"}
              </dd>
            </dl>
            {cue ? (
              <div className="vp-image-drawer__cue">
                <h4>LLMコンテキスト</h4>
                {cue.summary ? <p className="vp-image-drawer__cue-summary">{cue.summary}</p> : null}
                {cue.text ? <p className="vp-image-drawer__cue-text">{cue.text}</p> : null}
                {cue.visual_focus ? (
                  <p className="vp-image-drawer__cue-text">フォーカス: {cue.visual_focus}</p>
                ) : null}
                {cue.role_tag ? <p className="vp-image-drawer__cue-text">ロール: {cue.role_tag}</p> : null}
                {cue.role_asset ? (
                  <p className="vp-image-drawer__cue-text">
                    素材:{" "}
                    {roleAssetUrl ? (
                      <a href={roleAssetUrl} target="_blank" rel="noreferrer">
                        {cue.role_asset.path}
                      </a>
                    ) : cue.role_asset.path ? (
                      cue.role_asset.path
                    ) : (
                      "指定あり"
                    )}
                    {cue.role_asset.note ? ` (${cue.role_asset.note})` : ""}
                  </p>
                ) : null}
              </div>
            ) : null}
            <div className="vp-image-drawer__actions">
              <a className="vp-button" href={downloadUrl} download>
                ダウンロード
              </a>
              <label className="vp-button vp-button--ghost">
                差し替え
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      void onReplace(asset.path, file);
                      event.target.value = "";
                    }
                  }}
                />
              </label>
            </div>
            <div className="vp-image-drawer__prompt">
              <label>
                <span>生成プロンプト</span>
                <textarea
                  value={promptValue}
                  onChange={(event) => setPromptValue(event.target.value)}
                  rows={8}
                />
              </label>
              <div className="vp-image-drawer__regen">
                {regenerateError ? <span className="vp-image-drawer__regen-error">{regenerateError}</span> : null}
                <button type="button" onClick={handleRegenerate} disabled={regenerating}>
                  {regenerating ? "再生成中…" : "このプロンプトで再生成"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function buildStepState(detail: VideoProjectDetail | null): StepState[] {
  if (!detail?.summary) {
    return STEP_SEQUENCE.map((step, index) => ({
      ...step,
      state: (index === 0 ? "active" : "todo") as StepState["state"],
    }));
  }
  const summary = detail.summary;
  const status = detail.sourceStatus ?? (detail as { source_status?: VideoProjectDetail["sourceStatus"] }).source_status;
  const sourceReady = Boolean(status?.srtReady && status?.audioReady);
  const guardStatus = detail.guard?.status ?? (detail.guard?.issues?.length ? "danger" : "unknown");
  return STEP_SEQUENCE.map((step, index) => {
    let state: "done" | "active" | "todo" | "danger" = "todo";
    switch (step.id) {
      case "materials":
        state = sourceReady ? "done" : "active";
        break;
      case "chunk":
        state = summary.status !== "pending" ? "done" : sourceReady ? "active" : "todo";
        break;
      case "visual":
        state = summary.status === "images_ready" || summary.status === "draft_ready" ? "done" : "active";
        break;
      case "guard":
        state = guardStatus === "danger" ? "danger" : guardStatus === "ok" ? "done" : "active";
        break;
      case "capcut":
        state = summary.status === "draft_ready" ? "done" : "todo";
        break;
      default:
        state = "todo";
    }
    return { ...step, state };
  });
}

function formatTime(value: number) {
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatArtifactMeta(meta?: Record<string, unknown> | null): string | null {
  if (!meta) {
    return null;
  }
  const entries = Object.entries(meta).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return null;
  }
  const parts = entries.map(([key, value]) => {
    if (Array.isArray(value)) {
      const shown = value.slice(0, 3).map((item) => String(item));
      const suffix = value.length > shown.length ? ", …" : "";
      return `${key}=[${shown.join(", ")}${suffix}]`;
    }
    if (typeof value === "object") {
      try {
        return `${key}=${JSON.stringify(value)}`;
      } catch (error) {
        return `${key}=[object]`;
      }
    }
    return `${key}=${String(value)}`;
  });
  const joined = parts.join(", ");
  return joined.length > 140 ? `${joined.slice(0, 137)}…` : joined;
}

function formatJobTimestamp(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const month = (date.getMonth() + 1).toString().padStart(2, "0");
  const day = date.getDate().toString().padStart(2, "0");
  const hours = date.getHours().toString().padStart(2, "0");
  const minutes = date.getMinutes().toString().padStart(2, "0");
  const seconds = date.getSeconds().toString().padStart(2, "0");
  return `${month}/${day} ${hours}:${minutes}:${seconds}`;
}

function formatJobDuration(job: VideoJobRecord) {
  const start = job.started_at ?? job.created_at;
  if (!start) {
    return "—";
  }
  const startTime = new Date(start).getTime();
  if (Number.isNaN(startTime)) {
    return "—";
  }
  if (job.status === "queued" && !job.started_at) {
    return "未開始";
  }
  const endTime = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
  if (Number.isNaN(endTime)) {
    return "—";
  }
  const diffSeconds = Math.max(0, Math.round((endTime - startTime) / 1000));
  if (diffSeconds < 60) {
    return `${diffSeconds}s`;
  }
  const minutes = Math.floor(diffSeconds / 60);
  const seconds = (diffSeconds % 60).toString().padStart(2, "0");
  return `${minutes}m${seconds}s`;
}

function buildAssetUrl(path: string, modifiedAt?: string | null): string {
  const base = `/api/video-production/assets/${path}`;
  const suffix = modifiedAt ? `?t=${encodeURIComponent(modifiedAt)}` : "";
  return resolveApiUrl(`${base}${suffix}`);
}

function resolveChannelId(summary?: VideoProjectDetail["summary"]) {
  if (!summary) {
    return null;
  }
  return (
    (summary as { channelId?: string }).channelId ??
    (summary as { channel_id?: string }).channel_id ??
    (summary.id?.includes("-") ? summary.id.split("-", 1)[0] : null)
  );
}

function resolvePipelinePlan(detail: VideoProjectDetail | null): PipelinePlan {
  if (!detail?.summary) {
    return { steps: [], reason: "プロジェクト情報が読み込まれるまでお待ちください。" };
  }
  const steps: PipelineStepPlan[] = [];
  const status = detail.summary.status;
  if (status === "pending") {
    steps.push({ action: "analyze_srt", label: "SRT解析" });
  }
  if (status === "pending" || status === "storyboard_ready") {
    const options = detail.generationOptions ?? DEFAULT_GENERATION_OPTIONS;
    steps.push({
      action: "regenerate_images",
      label: "画像生成",
      options: {
        imgdur: options.imgdur,
        crossfade: options.crossfade,
        fps: options.fps,
        style: options.style,
      },
    });
  }
  steps.push({ action: "generate_belt", label: "帯生成" });
  if (detail.guard?.status !== "ok") {
    steps.push({
      action: "validate_capcut",
      label: INTEGRITY_LABEL,
      options: { use_existing_draft: true },
    });
  }
  steps.push({ action: "build_capcut_draft", label: "CapCutドラフト" });
  return { steps };
}
