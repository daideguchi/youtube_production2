import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import {
  fetchAutoDraftPromptTemplateContent,
  fetchChannelPreset,
  fetchChannelProfile,
  fetchLlmSettings,
  fetchPersonaDocument,
  fetchPlanningRows,
  fetchPlanningTemplate,
  fetchResearchFile,
  markVideoPublishedLocked,
  unmarkVideoPublishedLocked,
  updatePlanningChannelProgress,
} from "../api/client";
import type {
  BenchmarkScriptSampleSpec,
  ChannelProfileResponse,
  ChannelSummary,
  LlmSettings,
  PlanningCsvRow,
  PlanningTemplateResponse,
  PersonaDocumentResponse,
  PromptTemplateContentResponse,
  VideoProductionChannelPreset,
  VideoSummary,
} from "../api/types";
import { ChannelOverviewPanel } from "../components/ChannelOverviewPanel";
import { pickCurrentStage, resolveStageStatus } from "../components/StageProgress";
import type { ShellOutletContext } from "../layouts/AppShell";
import { translateStage, translateStatus } from "../utils/i18n";
import { safeLocalStorage } from "../utils/safeStorage";
import { resolveAudioSubtitleState } from "../utils/video";
import "./ChannelPortalPage.css";

function formatDate(value?: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

function pickRowTitle(row: PlanningCsvRow): string {
  const csvTitle = row.columns?.["タイトル"];
  if (csvTitle && csvTitle.trim()) {
    return csvTitle;
  }
  return row.title ?? "";
}

function normalizePreviewText(value?: string | null, limit = 420): string {
  if (!value) {
    return "";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  if (trimmed.length <= limit) {
    return trimmed;
  }
  return `${trimmed.slice(0, limit)}…`;
}

const BENCHMARK_CHARS_PER_SECOND = 6.0;

type PortalTopTab = "channel" | "production" | "benchmarks";

const PORTAL_TOP_TABS: { key: PortalTopTab; label: string }[] = [
  { key: "channel", label: "チャンネル" },
  { key: "production", label: "制作設定" },
  { key: "benchmarks", label: "ベンチマーク" },
];

type BenchmarkScriptMetrics = {
  nonWhitespaceChars: number;
  rawChars: number;
  lines: number;
  nonEmptyLines: number;
  headings: number;
  dividers: number;
  estimatedMinutes: number;
  firstNonEmptyLine: string;
};

function analyzeBenchmarkContent(content: string): BenchmarkScriptMetrics {
  const raw = content ?? "";
  const lines = raw.split(/\r?\n/);
  const nonEmptyLines = lines.filter((line) => line.trim().length > 0);
  const headings = lines.filter((line) => /^#{1,6}\s+/.test(line.trim())).length;
  const dividers = lines.filter((line) => /^(-{3,}|={3,}|_{3,})\s*$/.test(line.trim())).length;
  const nonWhitespaceChars = raw.replace(/\s/g, "").length;
  const estimatedMinutes = nonWhitespaceChars / BENCHMARK_CHARS_PER_SECOND / 60;
  const firstNonEmptyLine = (nonEmptyLines[0] ?? "").trim();

  return {
    nonWhitespaceChars,
    rawChars: raw.length,
    lines: lines.length,
    nonEmptyLines: nonEmptyLines.length,
    headings,
    dividers,
    estimatedMinutes,
    firstNonEmptyLine,
  };
}

function normalizeHandle(value?: string | null): string {
  if (!value) {
    return "—";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "—";
  }
  return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

function pickFirstNonEmpty(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    if (!value) continue;
    const trimmed = value.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return null;
}

function trimOrNull(value?: string | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function pickPlanningValue(row: PlanningCsvRow | null, key: string): string | null {
  if (!row?.columns) {
    return null;
  }
  const value = row.columns[key];
  if (!value) {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function buildPlanningSearchText(row: PlanningCsvRow): string {
  const parts = [
    row.video_number,
    row.script_id ?? "",
    row.progress ?? "",
    pickRowTitle(row),
    row.columns?.["悩みタグ_メイン"] ?? "",
    row.columns?.["悩みタグ_サブ"] ?? "",
    row.columns?.["ライフシーン"] ?? "",
    row.columns?.["キーコンセプト"] ?? "",
  ];
  return parts.join(" ").toLowerCase();
}

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

function resolveChannelDisplayName(channel: ChannelSummary): string {
  return channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
}

function copyToClipboard(text: string): Promise<boolean> {
  const value = String(text ?? "");
  if (!value) {
    return Promise.resolve(false);
  }
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard
      .writeText(value)
      .then(() => true)
      .catch(() => false);
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return Promise.resolve(Boolean(ok));
  } catch {
    return Promise.resolve(false);
  }
}

export function ChannelPortalPage() {
  const navigate = useNavigate();
  const {
    channels,
    channelsLoading,
    channelsError,
    selectedChannel,
    selectedChannelSummary,
    selectedChannelSnapshot,
    videos,
    openScript,
    openAudio,
    navigateToChannel,
  } = useOutletContext<ShellOutletContext>();

  const [profile, setProfile] = useState<ChannelProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [planningRows, setPlanningRows] = useState<PlanningCsvRow[]>([]);
  const [planningLoading, setPlanningLoading] = useState(false);
  const [planningError, setPlanningError] = useState<string | null>(null);

  const [llmSettings, setLlmSettings] = useState<LlmSettings | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);

  const [channelPreset, setChannelPreset] = useState<VideoProductionChannelPreset | null>(null);
  const [presetLoading, setPresetLoading] = useState(false);
  const [presetError, setPresetError] = useState<string | null>(null);

  const [personaDoc, setPersonaDoc] = useState<PersonaDocumentResponse | null>(null);
  const [personaLoading, setPersonaLoading] = useState(false);
  const [personaError, setPersonaError] = useState<string | null>(null);

  const [planningTemplate, setPlanningTemplate] = useState<PlanningTemplateResponse | null>(null);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);

  const [promptTemplateContent, setPromptTemplateContent] = useState<PromptTemplateContentResponse | null>(null);
  const [promptTemplateLoading, setPromptTemplateLoading] = useState(false);
  const [promptTemplateError, setPromptTemplateError] = useState<string | null>(null);

  const [searchTerm, setSearchTerm] = useState("");
  const [progressDraft, setProgressDraft] = useState<Record<string, string>>({});
  const [progressSaving, setProgressSaving] = useState<Record<string, boolean>>({});
  const [progressError, setProgressError] = useState<Record<string, string>>({});
  const [publishingKey, setPublishingKey] = useState<string | null>(null);
  const [unpublishingKey, setUnpublishingKey] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<Record<string, string>>({});
  const [copiedVideo, setCopiedVideo] = useState<string | null>(null);
  const copiedTimerRef = useRef<number | null>(null);
  const [channelAvatarErrors, setChannelAvatarErrors] = useState<Record<string, boolean>>({});

  const [portalTopTab, setPortalTopTab] = useState<PortalTopTab>(() => {
    const raw = (safeLocalStorage.getItem("ui.portal.topTab") ?? "").trim();
    if (raw === "channel" || raw === "production" || raw === "benchmarks") {
      return raw as PortalTopTab;
    }
    return "channel";
  });

  useEffect(() => {
    safeLocalStorage.setItem("ui.portal.topTab", portalTopTab);
  }, [portalTopTab]);

  const [benchmarkPreview, setBenchmarkPreview] = useState<{
    label: string;
    loading: boolean;
    content: string;
    error: string | null;
    metrics: BenchmarkScriptMetrics | null;
  } | null>(null);

  const videosByNumber = useMemo(() => {
    const map = new Map<string, VideoSummary>();
    videos.forEach((video) => {
      map.set(video.video, video);
    });
    return map;
  }, [videos]);

  const sortedChannels = useMemo(() => {
    const list = [...(channels ?? [])];
    list.sort((left, right) => compareChannelCode(left.code, right.code));
    return list;
  }, [channels]);

  useEffect(() => {
    let cancelled = false;
    setLlmLoading(true);
    setLlmError(null);
    fetchLlmSettings()
      .then((settings) => {
        if (cancelled) return;
        setLlmSettings(settings);
      })
      .catch((error) => {
        if (cancelled) return;
        setLlmSettings(null);
        setLlmError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (cancelled) return;
        setLlmLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedChannel) {
      setPersonaDoc(null);
      setPersonaLoading(false);
      setPersonaError(null);
      setPlanningTemplate(null);
      setTemplateLoading(false);
      setTemplateError(null);
      return;
    }

    let cancelled = false;
    setPersonaLoading(true);
    setPersonaError(null);
    setTemplateLoading(true);
    setTemplateError(null);

    (async () => {
      const [personaResult, templateResult] = await Promise.allSettled([
        fetchPersonaDocument(selectedChannel),
        fetchPlanningTemplate(selectedChannel),
      ]);

      if (cancelled) return;

      if (personaResult.status === "fulfilled") {
        setPersonaDoc(personaResult.value);
      } else {
        setPersonaDoc(null);
        setPersonaError(personaResult.reason instanceof Error ? personaResult.reason.message : String(personaResult.reason));
      }
      setPersonaLoading(false);

      if (templateResult.status === "fulfilled") {
        setPlanningTemplate(templateResult.value);
      } else {
        setPlanningTemplate(null);
        setTemplateError(templateResult.reason instanceof Error ? templateResult.reason.message : String(templateResult.reason));
      }
      setTemplateLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedChannel]);

  useEffect(() => {
    const rawTemplateValue = channelPreset?.promptTemplate?.trim() ?? "";
    const looksLikePath =
      rawTemplateValue.length > 0 &&
      !rawTemplateValue.includes("\n") &&
      (rawTemplateValue.includes("/") || rawTemplateValue.endsWith(".txt"));

    if (!rawTemplateValue || !looksLikePath) {
      setPromptTemplateContent(null);
      setPromptTemplateLoading(false);
      setPromptTemplateError(null);
      return;
    }
    let cancelled = false;
    setPromptTemplateLoading(true);
    setPromptTemplateError(null);
    fetchAutoDraftPromptTemplateContent(rawTemplateValue)
      .then((data) => {
        if (cancelled) return;
        setPromptTemplateContent(data);
      })
      .catch((error) => {
        if (cancelled) return;
        setPromptTemplateContent(null);
        setPromptTemplateError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (cancelled) return;
        setPromptTemplateLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [channelPreset?.promptTemplate]);

  useEffect(() => {
    if (!selectedChannel) {
      setChannelPreset(null);
      setPresetLoading(false);
      setPresetError(null);
      return;
    }
    // CapCut presets are only required for capcut workflow channels.
    // vrew/remotion workflows don't have entries in video_production presets and would 404.
    const workflowKey = selectedChannelSummary?.video_workflow?.key ?? null;
    const shouldLoadPreset = workflowKey === "capcut";

    if (!workflowKey || !shouldLoadPreset) {
      setChannelPreset(null);
      setPresetLoading(false);
      setPresetError(null);
      return;
    }
    let cancelled = false;
    setPresetLoading(true);
    setPresetError(null);
    fetchChannelPreset(selectedChannel)
      .then((preset) => {
        if (cancelled) return;
        setChannelPreset(preset);
      })
      .catch((error) => {
        if (cancelled) return;
        setChannelPreset(null);
        setPresetError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (cancelled) return;
        setPresetLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedChannel, selectedChannelSummary?.video_workflow?.key]);

  useEffect(() => {
    if (!selectedChannel) {
      setProfile(null);
      setPlanningRows([]);
      setProfileLoading(false);
      setPlanningLoading(false);
      setProfileError(null);
      setPlanningError(null);
      return;
    }

    let cancelled = false;
    setProfileLoading(true);
    setPlanningLoading(true);
    setProfileError(null);
    setPlanningError(null);

    (async () => {
      const [profileResult, planningResult] = await Promise.allSettled([
        fetchChannelProfile(selectedChannel),
        fetchPlanningRows(selectedChannel),
      ]);

      if (cancelled) {
        return;
      }

      if (profileResult.status === "fulfilled") {
        setProfile(profileResult.value);
      } else {
        setProfile(null);
        setProfileError(profileResult.reason instanceof Error ? profileResult.reason.message : String(profileResult.reason));
      }
      setProfileLoading(false);

      if (planningResult.status === "fulfilled") {
        setPlanningRows(planningResult.value);
      } else {
        setPlanningRows([]);
        setPlanningError(planningResult.reason instanceof Error ? planningResult.reason.message : String(planningResult.reason));
      }
      setPlanningLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedChannel]);

  useEffect(() => {
    return () => {
      if (copiedTimerRef.current) {
        window.clearTimeout(copiedTimerRef.current);
        copiedTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    setBenchmarkPreview(null);
  }, [selectedChannel]);

  const sortedPlanningRows = useMemo(() => {
    const list = [...planningRows];
    list.sort((left, right) => {
      const ln = Number.parseInt(left.video_number, 10);
      const rn = Number.parseInt(right.video_number, 10);
      const lNum = Number.isFinite(ln);
      const rNum = Number.isFinite(rn);
      if (lNum && rNum) {
        return ln - rn;
      }
      if (lNum) return -1;
      if (rNum) return 1;
      return left.video_number.localeCompare(right.video_number, "ja-JP");
    });
    return list;
  }, [planningRows]);

  const filteredPlanningRows = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) {
      return sortedPlanningRows;
    }
    return sortedPlanningRows.filter((row) => buildPlanningSearchText(row).includes(keyword));
  }, [sortedPlanningRows, searchTerm]);

  const applyProgressDraft = useCallback((video: string, value: string) => {
    setProgressDraft((current) => ({ ...current, [video]: value }));
    setProgressError((current) => {
      if (!current[video]) {
        return current;
      }
      const next = { ...current };
      delete next[video];
      return next;
    });
  }, []);

  const saveProgress = useCallback(
    async (row: PlanningCsvRow) => {
      if (!selectedChannel) {
        return;
      }
      const video = row.video_number;
      const draft = progressDraft[video];
      const nextProgress = (draft ?? row.progress ?? "").trim();
      if (!nextProgress) {
        setProgressError((current) => ({ ...current, [video]: "進捗が空です。" }));
        return;
      }
      const currentProgress = (row.progress ?? "").trim();
      if (nextProgress === currentProgress) {
        setProgressDraft((current) => {
          if (current[video] === undefined) return current;
          const next = { ...current };
          delete next[video];
          return next;
        });
        return;
      }
      setProgressSaving((current) => ({ ...current, [video]: true }));
      setProgressError((current) => {
        const next = { ...current };
        delete next[video];
        return next;
      });
      try {
        const updated = await updatePlanningChannelProgress(selectedChannel, video, {
          progress: nextProgress,
          expectedUpdatedAt: row.updated_at ?? null,
        });
        setPlanningRows((current) => current.map((item) => (item.video_number === video ? updated : item)));
        setProgressDraft((current) => {
          const next = { ...current };
          delete next[video];
          return next;
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setProgressError((current) => ({ ...current, [video]: message }));
      } finally {
        setProgressSaving((current) => ({ ...current, [video]: false }));
      }
    },
    [progressDraft, selectedChannel]
  );

  const markPublished = useCallback(
    async (videoRaw: string) => {
      if (!selectedChannel) {
        return;
      }
      const videoToken = String(videoRaw ?? "").trim();
      if (!videoToken) {
        return;
      }
      const key = `${selectedChannel}-${videoToken}`;
      setPublishingKey(key);
      setPublishError((current) => {
        if (!current[videoToken]) return current;
        const next = { ...current };
        delete next[videoToken];
        return next;
      });
      try {
        await markVideoPublishedLocked(selectedChannel, videoToken, { force_complete: true });
        const nextRows = await fetchPlanningRows(selectedChannel);
        setPlanningRows(nextRows);
        setProgressDraft((current) => {
          if (current[videoToken] === undefined) return current;
          const next = { ...current };
          delete next[videoToken];
          return next;
        });
        setProgressError((current) => {
          if (!current[videoToken]) return current;
          const next = { ...current };
          delete next[videoToken];
          return next;
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setPublishError((current) => ({ ...current, [videoToken]: message }));
      } finally {
        setPublishingKey(null);
      }
    },
    [selectedChannel]
  );

  const unmarkPublished = useCallback(
    async (videoRaw: string) => {
      if (!selectedChannel) {
        return;
      }
      const videoToken = String(videoRaw ?? "").trim();
      if (!videoToken) {
        return;
      }
      const key = `${selectedChannel}-${videoToken}`;
      if (!window.confirm(`投稿済みロックを解除しますか？ (${key})`)) {
        return;
      }
      setUnpublishingKey(key);
      setPublishError((current) => {
        if (!current[videoToken]) return current;
        const next = { ...current };
        delete next[videoToken];
        return next;
      });
      try {
        await unmarkVideoPublishedLocked(selectedChannel, videoToken);
        const nextRows = await fetchPlanningRows(selectedChannel);
        setPlanningRows(nextRows);
        setProgressDraft((current) => {
          if (current[videoToken] === undefined) return current;
          const next = { ...current };
          delete next[videoToken];
          return next;
        });
        setProgressError((current) => {
          if (!current[videoToken]) return current;
          const next = { ...current };
          delete next[videoToken];
          return next;
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setPublishError((current) => ({ ...current, [videoToken]: message }));
      } finally {
        setUnpublishingKey(null);
      }
    },
    [selectedChannel]
  );

  const handleCopyTitle = useCallback(async (row: PlanningCsvRow) => {
    const title = pickRowTitle(row);
    const ok = await copyToClipboard(title);
    if (!ok) {
      return;
    }
    setCopiedVideo(row.video_number);
    if (copiedTimerRef.current) {
      window.clearTimeout(copiedTimerRef.current);
    }
    copiedTimerRef.current = window.setTimeout(() => {
      setCopiedVideo(null);
      copiedTimerRef.current = null;
    }, 1200);
  }, []);

  if (!selectedChannel || !selectedChannelSummary || !selectedChannelSnapshot) {
    return (
      <section className="main-content main-content--channel">
        <div className="shell-panel shell-panel--placeholder">
          <h2>チャンネルを選択してください</h2>
          <p className="shell-panel__subtitle">サイドバーからチャンネルを選ぶとポータルが表示されます。</p>
          <button
            type="button"
            className="workspace-button workspace-button--ghost"
            onClick={() => navigate("/channel-settings")}
          >
            チャンネル設定を開く
          </button>
        </div>
      </section>
    );
  }

  const planningTotal = planningRows.length;
  const planningFiltered = filteredPlanningRows.length;
  const profileHandle = normalizeHandle(profile?.youtube_handle ?? selectedChannelSummary.youtube_handle ?? null);
  const profileTitle = pickFirstNonEmpty(profile?.youtube_title ?? null, selectedChannelSummary.youtube_title ?? null, selectedChannelSummary.name ?? null);
  const profileTags = (profile?.default_tags ?? []).filter(Boolean);
  const workflowSpec = profile?.video_workflow ?? selectedChannelSummary.video_workflow ?? null;
  const workflowLabel = workflowSpec ? `${workflowSpec.label}（${workflowSpec.id}）` : "—";
  const workflowDescription = trimOrNull(workflowSpec?.description ?? null);
  const isCapCutWorkflow = workflowSpec?.key === "capcut";

  const audioTemplateVoiceLine =
    (profile?.youtube_description ?? "")
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.startsWith("【音声】")) ?? null;
  const audioTemplateVoice = audioTemplateVoiceLine ? audioTemplateVoiceLine.replace(/^【音声】\s*/, "").trim() : null;

  const benchmarkChannels = (profile?.benchmarks?.channels ?? []).filter(
    (item) => Boolean(trimOrNull(item.handle) || trimOrNull(item.url))
  );
  const benchmarkSamples = (profile?.benchmarks?.script_samples ?? []).filter((item) => Boolean(trimOrNull(item.path)));
  const benchmarkNotes = trimOrNull(profile?.benchmarks?.notes ?? null);
  const benchmarkUpdatedAt = trimOrNull(profile?.benchmarks?.updated_at ?? null);
  const benchmarksChannelsCount = benchmarkChannels.length;
  const benchmarksScriptsCount = benchmarkSamples.length;
  const hasBenchmarkMinimum = benchmarksChannelsCount >= 1 && benchmarksScriptsCount >= 1;

	  const handleBenchmarkPreview = async (sample: BenchmarkScriptSampleSpec) => {
	    const base = sample.base;
	    const path = (sample.path ?? "").trim();
	    const label = sample.label?.trim() || path || "プレビュー";
	    if (!path) {
	      setBenchmarkPreview({ label, loading: false, content: "", error: "path が空です。", metrics: null });
	      return;
	    }
	    setBenchmarkPreview({ label, loading: true, content: "", error: null, metrics: null });
	    try {
	      const response = await fetchResearchFile(base, path);
	      const content = response.content ?? "";
	      const metrics = content ? analyzeBenchmarkContent(content) : null;
	      setBenchmarkPreview({ label, loading: false, content, error: null, metrics });
	    } catch (err) {
	      setBenchmarkPreview({
	        label,
	        loading: false,
	        content: "",
	        error: err instanceof Error ? err.message : String(err),
	        metrics: null,
	      });
	    }
	  };
  const scriptRewritePhase = llmSettings?.llm?.phase_models?.script_rewrite ?? null;
  const scriptRewriteDisplay = scriptRewritePhase?.model ? `${scriptRewritePhase.provider}:${scriptRewritePhase.model}` : "—";
  const captionProvider = llmSettings?.llm?.caption_provider ?? null;
  const captionModel =
    captionProvider === "openai"
      ? llmSettings?.llm?.openai_caption_model ?? null
      : captionProvider === "openrouter"
        ? llmSettings?.llm?.openrouter_caption_model ?? null
        : null;
  const customPath = selectedChannelSummary.branding?.custom_url?.replace(/^\//, "") ?? null;
  const youtubeUrl = selectedChannelSummary.branding?.url ?? (customPath ? `https://www.youtube.com/${customPath}` : null);
  const spreadsheetUrl = selectedChannelSummary.spreadsheet_id
    ? `https://docs.google.com/spreadsheets/d/${selectedChannelSummary.spreadsheet_id}`
    : null;

  const personaText = pickFirstNonEmpty(personaDoc?.content ?? null, profile?.persona_summary ?? null, profile?.audience_profile ?? null);
  const planningTemplateText = planningTemplate?.content ?? null;
  const rawPromptTemplateValue = channelPreset?.promptTemplate?.trim() ?? "";
  const promptTemplateIsPath =
    rawPromptTemplateValue.length > 0 &&
    !rawPromptTemplateValue.includes("\n") &&
    (rawPromptTemplateValue.includes("/") || rawPromptTemplateValue.endsWith(".txt"));
  const promptTemplateText = promptTemplateContent?.content ?? null;
  const resolvedPromptTemplateText = promptTemplateText ?? (!promptTemplateIsPath && rawPromptTemplateValue ? rawPromptTemplateValue : null);

  return (
    <section className="channel-portal-page workspace--channel-clean">
      <div className="channel-portal-switcher-bar">
        <section className="main-content main-content--workspace channel-portal-switcher-card">
          <div className="channel-portal-switcher-card__header">
            <div className="channel-portal-switcher-card__title">
              <h1>チャンネルポータル</h1>
              <p className="muted">設定 / 企画 / 動画の主要情報を、1画面で確認します。</p>
            </div>
            <div className="channel-portal-switcher-card__actions">
              <button
                type="button"
                className="workspace-button workspace-button--ghost workspace-button--compact"
                onClick={() => navigate(`/channels/${encodeURIComponent(selectedChannel)}`)}
              >
                案件一覧
              </button>
              <button
                type="button"
                className="workspace-button workspace-button--ghost workspace-button--compact"
                onClick={() => navigate(`/channel-settings?channel=${encodeURIComponent(selectedChannel)}`)}
              >
                チャンネル設定
              </button>
              <button
                type="button"
                className="workspace-button workspace-button--ghost workspace-button--compact"
                onClick={() => navigate(`/planning?channel=${encodeURIComponent(selectedChannel)}`)}
              >
                企画CSV
              </button>
              {youtubeUrl ? (
                <a
                  className="workspace-button workspace-button--ghost workspace-button--compact"
                  href={youtubeUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  YouTube ↗
                </a>
              ) : null}
              {spreadsheetUrl ? (
                <a
                  className="workspace-button workspace-button--ghost workspace-button--compact"
                  href={spreadsheetUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  管理シート ↗
                </a>
              ) : null}
            </div>
          </div>

          <div className="channel-portal-switcher-tools">
            <div className="channel-portal-switcher-label">
              <div className="channel-portal-switcher-row">
                <span>チャンネル切替:</span>
                <div className="channel-portal-channel-pills" role="list" aria-label="チャンネル選択">
                  {sortedChannels.map((channel) => {
                    const isActive = channel.code === selectedChannel;
                    const displayName = resolveChannelDisplayName(channel);
                    const avatarUrl = (channel.branding?.avatar_url ?? "").trim() || null;
                    const avatarEnabled = Boolean(avatarUrl && !channelAvatarErrors[channel.code]);
                    const iconLabel = channel.code.replace(/^CH/i, "") || channel.code;
                    return (
                      <button
                        key={channel.code}
                        type="button"
                        className={`channel-portal-channel-pill${isActive ? " channel-portal-channel-pill--active" : ""}`}
                        aria-pressed={isActive}
                        disabled={channelsLoading || Boolean(channelsError)}
                        title={`${channel.code} ${displayName}`}
                        onClick={() => navigate(`/channels/${encodeURIComponent(channel.code)}/portal`)}
                      >
                        <span className="channel-portal-channel-pill__icon" aria-hidden="true">
                          {avatarEnabled ? (
                            <img
                              className="channel-portal-channel-pill__avatar"
                              src={avatarUrl ?? undefined}
                              alt=""
                              loading="lazy"
                              onError={() =>
                                setChannelAvatarErrors((current) => ({ ...current, [channel.code]: true }))
                              }
                            />
                          ) : (
                            <span className="channel-portal-channel-pill__icon-fallback">{iconLabel}</span>
                          )}
                        </span>
                        <span className="channel-portal-channel-pill__code">{channel.code}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <details className="channel-portal-switcher-details">
                <summary>プルダウンで選択</summary>
                <select
                  className="channel-portal-switcher-select"
                  value={selectedChannel ?? ""}
                  onChange={(event) => {
                    const next = event.target.value;
                    if (!next) {
                      return;
                    }
                    navigate(`/channels/${encodeURIComponent(next)}/portal`);
                  }}
                  disabled={channelsLoading || Boolean(channelsError)}
                >
                  <option value="" disabled>
                    {channelsLoading ? "読み込み中…" : channelsError ? "取得失敗" : "選択してください"}
                  </option>
                  {sortedChannels.map((channel) => {
                    const displayName = resolveChannelDisplayName(channel);
                    return (
                      <option key={channel.code} value={channel.code}>
                        {channel.code} {displayName}
                      </option>
                    );
                  })}
                </select>
              </details>
            </div>
            <span className="muted channel-portal-switcher-count">
              {sortedChannels.length}
            </span>
          </div>
        </section>
      </div>

      <section className="main-content main-content--channel">
        <ChannelOverviewPanel
          channel={selectedChannelSummary}
          snapshot={selectedChannelSnapshot}
          onBackToDashboard={() => navigateToChannel(selectedChannel)}
          backLabel="⬅ 案件一覧へ"
        />
      </section>

      <section className="main-content main-content--workspace channel-portal-content">
        <div className="channel-portal-top">
          <nav className="portal-tabs" aria-label="ポータルセクション">
            {PORTAL_TOP_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`portal-tab${portalTopTab === tab.key ? " portal-tab--active" : ""}`}
                onClick={() => setPortalTopTab(tab.key)}
                aria-pressed={portalTopTab === tab.key}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {portalTopTab === "channel" ? (
            <div className="channel-card">
            <div className="channel-card__header">
              <div className="channel-card__heading">
                <h4>チャンネル情報</h4>
                <span className="channel-card__total">YouTube/タグ/プロンプト/Persona</span>
              </div>
              <button
                type="button"
                className="channel-card__action"
                onClick={() => navigate(`/channel-settings?channel=${encodeURIComponent(selectedChannel)}`)}
              >
                設定を編集
              </button>
            </div>

            {profileLoading ? <p className="muted">読み込み中…</p> : null}
            {profileError ? <div className="main-alert main-alert--error">{profileError}</div> : null}

            {!profileLoading && !profileError ? (
              <div className="channel-portal-card-body">
                <dl className="portal-kv">
                  <dt>YouTubeタイトル</dt>
                  <dd>{profileTitle ?? "—"}</dd>

                  <dt>ハンドル</dt>
                  <dd>{profileHandle}</dd>

	                  <dt>既定タグ</dt>
	                  <dd>{profileTags.length ? profileTags.join(" / ") : "—"}</dd>

	                  <dt>制作型</dt>
	                  <dd>{workflowLabel}</dd>

		                  <dt>字数レンジ</dt>
		                  <dd>
		                    {profile?.default_min_characters ?? "—"}〜{profile?.default_max_characters ?? "—"}
		                  </dd>

		                  <dt>章数</dt>
		                  <dd>{profile?.chapter_count ?? "—"}</dd>

		                  <dt>音声</dt>
		                  <dd>{audioTemplateVoice ?? profile?.audio_default_voice_key ?? "—"}</dd>

                  <dt>Persona</dt>
                  <dd>{personaText ? normalizePreviewText(personaText, 140) : "—"}</dd>

                  <dt>企画テンプレ</dt>
                  <dd>{planningTemplateText ? normalizePreviewText(planningTemplateText, 140) : "—"}</dd>

	                  <dt>ベンチマーク</dt>
	                  <dd>
	                    channels: {benchmarksChannelsCount} / scripts: {benchmarksScriptsCount}
	                  </dd>
	                </dl>

	                {workflowDescription ? (
	                  <div className="channel-profile-banner channel-profile-banner--info">{workflowDescription}</div>
	                ) : null}

	                <details className="portal-details">
	                  <summary>ペルソナ（SSOT本文）</summary>
	                  {personaLoading ? <p className="muted">読み込み中…</p> : null}
	                  {personaError ? <p className="muted">取得失敗: {personaError}</p> : null}
	                  <pre>{personaText ?? "—"}</pre>
	                </details>

	                <details className="portal-details">
	                  <summary>チャンネル説明文</summary>
	                  <pre>{profile?.description?.trim() ? profile.description : "—"}</pre>
	                </details>

	                <details className="portal-details">
	                  <summary>動画説明（固定テンプレ）</summary>
	                  <pre>{profile?.youtube_description?.trim() ? profile.youtube_description : "—"}</pre>
	                </details>

                <details className="portal-details">
                  <summary>企画テンプレ（SSOT）</summary>
                  {templateLoading ? <p className="muted">読み込み中…</p> : null}
                  {templateError ? <p className="muted">取得失敗: {templateError}</p> : null}
                  <pre>{planningTemplateText ?? "—"}</pre>
                </details>

                <details className="portal-details">
                  <summary>台本作成プロンプト</summary>
                  <pre>{profile?.script_prompt?.trim() ? profile.script_prompt : "—"}</pre>
                </details>
              </div>
            ) : null}
            </div>
          ) : null}

          {portalTopTab === "production" ? (
            <div className="channel-card">
            <div className="channel-card__header">
              <div className="channel-card__heading">
                <h4>既定モデル / CapCut設定</h4>
                <span className="channel-card__total">量産モデル・テンプレ・ベルト・配置</span>
              </div>
              <div className="channel-portal-preview__header-actions">
                <button
                  type="button"
                  className="channel-card__action"
                  onClick={() => navigate("/settings")}
                >
                  LLM設定
                </button>
                {isCapCutWorkflow ? (
                  <button
                    type="button"
                    className="channel-card__action"
                    onClick={() => navigate("/capcut-edit")}
                  >
                    CapCutへ
                  </button>
                ) : null}
              </div>
            </div>

            {llmLoading ? <p className="muted">LLM設定 読み込み中…</p> : null}
            {llmError ? <div className="main-alert main-alert--error">{llmError}</div> : null}

            <dl className="portal-kv">
              <dt>台本量産（script_rewrite）</dt>
              <dd>{scriptRewriteDisplay}</dd>

              <dt>Caption</dt>
              <dd>{captionProvider ? `${captionProvider}:${captionModel ?? "—"}` : "—"}</dd>
            </dl>

            {isCapCutWorkflow ? (
              <>
                {presetLoading ? <p className="muted" style={{ marginTop: 12 }}>CapCut設定 読み込み中…</p> : null}
                {presetError ? <div className="main-alert main-alert--error">{presetError}</div> : null}

                {!presetLoading && !presetError ? (
                  <dl className="portal-kv" style={{ marginTop: 12 }}>
                    <dt>Preset</dt>
                    <dd>{channelPreset?.name ?? "—"}</dd>

                    <dt>CapCutテンプレ</dt>
                    <dd>{channelPreset?.capcutTemplate ?? "—"}</dd>

                    <dt>スタイル</dt>
                    <dd>{channelPreset?.style ?? "—"}</dd>

                    <dt>画像プロンプト</dt>
                    <dd>{resolvedPromptTemplateText ? normalizePreviewText(resolvedPromptTemplateText, 260) : "—"}</dd>

                    <dt>ベルト</dt>
                    <dd>
                      {channelPreset?.belt?.enabled
                        ? `enabled (opening_offset=${channelPreset.belt.opening_offset ?? 0})`
                        : "disabled"}
                    </dd>

                    <dt>位置</dt>
                    <dd>
                      {channelPreset?.position
                        ? `tx=${channelPreset.position.tx ?? 0}, ty=${channelPreset.position.ty ?? 0}, scale=${channelPreset.position.scale ?? 1}`
                        : "—"}
                    </dd>
                  </dl>
                ) : null}

                <details className="portal-details" style={{ marginTop: 12 }}>
                  <summary>画像プロンプト（全文）</summary>
                  {promptTemplateLoading ? <p className="muted">読み込み中…</p> : null}
                  {promptTemplateError ? <p className="muted">取得失敗: {promptTemplateError}</p> : null}
                  <pre>
                    {resolvedPromptTemplateText
                      ? resolvedPromptTemplateText
                      : rawPromptTemplateValue
                        ? "（テンプレ本文を取得できませんでした。AutoDraft のテンプレ設定を確認してください。）"
                        : "—"}
                  </pre>
                </details>

                {channelPreset?.notes ? (
                  <details className="portal-details" style={{ marginTop: 12 }}>
                    <summary>メモ</summary>
                    <pre>{channelPreset.notes}</pre>
                  </details>
                ) : null}
              </>
            ) : (
              <p className="muted" style={{ marginTop: 12 }}>
                このチャンネルは {workflowLabel} のため、CapCut設定は未使用です。
              </p>
            )}
            </div>
          ) : null}

          {portalTopTab === "benchmarks" ? (
            <div className="channel-card">
            <div className="channel-card__header">
	              <div className="channel-card__heading">
	                <h4>ベンチマーク</h4>
	                <span className="channel-card__total">
	                  channels: {benchmarksChannelsCount} / scripts: {benchmarksScriptsCount}
	                  {benchmarkUpdatedAt ? ` / updated: ${benchmarkUpdatedAt}` : ""}
	                </span>
	              </div>
              <button
                type="button"
                className="channel-card__action"
                onClick={() => navigate(`/benchmarks?channel=${encodeURIComponent(selectedChannel)}`)}
              >
                編集
              </button>
            </div>

            {profileLoading ? <p className="muted">読み込み中…</p> : null}
            {profileError ? <div className="main-alert main-alert--error">{profileError}</div> : null}

            {!profileLoading && !profileError ? (
              <div className="channel-portal-card-body">
                {!hasBenchmarkMinimum ? (
                  <div className="channel-profile-banner channel-profile-banner--info">
                    最低ライン: 競合チャンネル1件＋台本サンプル1件（不足しています）
                  </div>
                ) : null}

                <details className="portal-details">
                  <summary>競合チャンネル（{benchmarksChannelsCount}）</summary>
                  {benchmarkChannels.length ? (
                    <ul className="portal-list">
                      {benchmarkChannels.map((item, index) => (
                        <li key={`bench-ch-${index}`} className="portal-list__item">
                          <div className="portal-list__row">
                            <div className="portal-list__title">{item.name?.trim() || item.handle?.trim() || item.url?.trim() || "—"}</div>
                            {item.url?.trim() ? (
                              <a className="link-button" href={item.url} target="_blank" rel="noreferrer">
                                YouTube ↗
                              </a>
                            ) : null}
                          </div>
                          {item.note?.trim() ? <div className="portal-list__note">{item.note}</div> : null}
                          <div className="portal-list__meta">
                            {[item.handle?.trim(), item.url?.trim()].filter(Boolean).join(" / ") || "—"}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">未登録です。</p>
                  )}
                </details>

                <details className="portal-details">
                  <summary>台本サンプル（{benchmarksScriptsCount}）</summary>
                  {benchmarkSamples.length ? (
                    <ul className="portal-list">
                      {benchmarkSamples.map((item, index) => (
                        <li key={`bench-sample-${index}`} className="portal-list__item">
                          <div className="portal-list__row">
                            <div className="portal-list__title">{item.label?.trim() || item.path}</div>
                            <button type="button" className="link-button" onClick={() => void handleBenchmarkPreview(item)}>
                              プレビュー
                            </button>
                          </div>
                          {item.note?.trim() ? <div className="portal-list__note">{item.note}</div> : null}
                          <div className="portal-list__meta">
                            {item.base}:{item.path}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">未登録です。</p>
                  )}
                </details>

                {benchmarkNotes ? (
                  <details className="portal-details">
                    <summary>メモ</summary>
                    <pre>{benchmarkNotes}</pre>
                  </details>
                ) : null}
              </div>
            ) : null}
            </div>
          ) : null}
        </div>

        <section className="channel-projects channel-portal-planning">
          <header className="channel-projects__header">
            <div>
              <h2>企画一覧</h2>
              <p className="muted">
                {planningLoading
                  ? "読み込み中…"
                  : planningFiltered === planningTotal
                    ? `全 ${planningTotal} 件`
                    : `${planningTotal} 件中 ${planningFiltered} 件を表示`}
              </p>
            </div>
            <div className="channel-projects__actions">
              <input
                type="search"
                className="channel-projects__search"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="番号・タイトル・進捗・タグで検索"
                disabled={planningLoading}
              />
            </div>
          </header>

          {planningError ? <div className="main-alert main-alert--error">{planningError}</div> : null}

          <div className="channel-projects__table-wrapper">
            <table className="channel-projects__table">
              <thead>
                <tr>
                  <th scope="col">番号</th>
                  <th scope="col">タイトル</th>
                  <th scope="col">進捗</th>
                  <th scope="col">投稿</th>
                  <th scope="col">音声</th>
                  <th scope="col">更新</th>
                  <th scope="col" className="channel-projects__actions-column">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {planningLoading ? (
                  <tr>
                    <td colSpan={7} className="channel-projects__empty">
                      読み込み中…
                    </td>
                  </tr>
                ) : filteredPlanningRows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="channel-projects__empty">
                      条件に一致する企画はありません。
                    </td>
                  </tr>
                ) : (
                  filteredPlanningRows.map((row) => {
                    const rowTitle = pickRowTitle(row) || "タイトル未設定";
                    const summary = videosByNumber.get(row.video_number) ?? null;
                    const stageKey = summary ? pickCurrentStage(summary.stages ?? {}) : null;
                    const stageStatus = summary && stageKey ? resolveStageStatus(stageKey, summary.stages ?? {}) : null;
                    const audioState = summary ? resolveAudioSubtitleState(summary) : null;
                    const audioLabel =
                      audioState === "completed"
                        ? "完了"
                        : audioState === "ready"
                          ? "準備済"
                          : audioState
                            ? "未準備"
                            : "—";
                    const metaParts = [
                      row.script_id ?? "",
                      pickPlanningValue(row, "悩みタグ_メイン") ?? "",
                      pickPlanningValue(row, "悩みタグ_サブ") ?? "",
                      pickPlanningValue(row, "ライフシーン") ?? "",
                    ].filter((item) => item && item.trim().length > 0);
                    const progressValue = progressDraft[row.video_number] ?? row.progress ?? "";
                    const progressChanged = progressValue.trim() !== (row.progress ?? "").trim();
                    const progressBusy = Boolean(progressSaving[row.video_number]);
                    const progressErr = progressError[row.video_number] ?? null;
                    const publishedLocked =
                      String(row.progress ?? "").includes("投稿済み") || String(row.progress ?? "").includes("公開済み");
                    const publishKey = `${selectedChannel}-${row.video_number}`;
                    const isPublishing = publishingKey === publishKey;
                    const isUnpublishing = unpublishingKey === publishKey;
                    const publishBusy = isPublishing || isUnpublishing;
                    const publishErr = publishError[row.video_number] ?? null;
                    return (
                      <tr
                        key={row.video_number}
                        className="channel-projects__row"
                        tabIndex={0}
                        onClick={(event) => {
                          const target = event.target as HTMLElement | null;
                          if (target?.closest("button, a, input, select, textarea, label")) {
                            return;
                          }
                          openScript(row.video_number);
                        }}
                        onKeyDown={(event) => {
                          if (event.target !== event.currentTarget) {
                            return;
                          }
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            openScript(row.video_number);
                          }
                        }}
                      >
                        <th scope="row">{row.video_number}</th>
                        <td>
                          <div className="channel-projects__title">{rowTitle}</div>
                          <div className="portal-chip-row">
                            <button
                              type="button"
                              className="link-button"
                              onClick={(event) => {
                                event.preventDefault();
                                handleCopyTitle(row);
                              }}
                            >
                              {copiedVideo === row.video_number ? "コピー済み" : "タイトルをコピー"}
                            </button>
                            {summary?.status ? (
                              <span className={`status-badge status-badge--${summary.status ?? "pending"}`}>
                                {translateStatus(summary.status)}
                              </span>
                            ) : null}
                            {stageKey ? (
                              <span className={`status-badge status-badge--${stageStatus ?? "pending"}`}>
                                {translateStage(stageKey)}
                              </span>
                            ) : summary ? (
                              <span className="status-badge status-badge--completed">全工程完了</span>
                            ) : null}
                          </div>
                          <div className="channel-projects__meta">{metaParts.length ? metaParts.join(" / ") : "—"}</div>
                        </td>
                        <td>
                          <div className="channel-projects__progress">
                            <input
                              type="text"
                              className="channel-projects__progress-input"
                              value={progressValue}
                              onChange={(event) => applyProgressDraft(row.video_number, event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault();
                                  saveProgress(row);
                                }
                                if (event.key === "Escape") {
                                  event.preventDefault();
                                  setProgressDraft((current) => {
                                    const next = { ...current };
                                    delete next[row.video_number];
                                    return next;
                                  });
                                  setProgressError((current) => {
                                    const next = { ...current };
                                    delete next[row.video_number];
                                    return next;
                                  });
                                }
                              }}
                              disabled={planningLoading || progressBusy}
                              aria-label={`${row.video_number} 進捗`}
                            />
                            <button
                              type="button"
                              className="link-button"
                              onClick={(event) => {
                                event.preventDefault();
                                saveProgress(row);
                              }}
                              disabled={planningLoading || progressBusy || !progressChanged}
                              title={progressChanged ? "進捗を保存" : "変更なし"}
                            >
                              {progressBusy ? "保存中…" : "保存"}
                            </button>
                          </div>
                          {progressErr ? <div className="channel-projects__progress-error">{progressErr}</div> : null}
                        </td>
                        <td>
                          <label
                            className="portal-publish-toggle"
                            title={
                              publishedLocked
                                ? "投稿済み（ロック中）: クリックで解除できます"
                                : "チェックで投稿済みにする（ロック）"
                            }
                          >
                            <input
                              type="checkbox"
                              checked={publishedLocked || publishBusy}
                              disabled={publishBusy}
                              onChange={(event) => {
                                const next = event.target.checked;
                                if (next && !publishedLocked) {
                                  void markPublished(row.video_number);
                                  return;
                                }
                                if (!next && publishedLocked) {
                                  void unmarkPublished(row.video_number);
                                }
                              }}
                            />
                          </label>
                          {publishErr ? <div className="portal-publish-error">{publishErr}</div> : null}
                        </td>
                        <td>{audioLabel}</td>
                        <td>{formatDate(row.updated_at)}</td>
                        <td className="channel-projects__actions-cell">
                          <button
                            type="button"
                            className="link-button"
                            onClick={(event) => {
                              event.preventDefault();
                              openScript(row.video_number);
                            }}
                          >
                            台本
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={(event) => {
                              event.preventDefault();
                              openAudio(row.video_number);
                            }}
                          >
                            音声・字幕
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
	        </section>
	      </section>

	      {benchmarkPreview ? (
	        <div className="modal-backdrop" onClick={() => setBenchmarkPreview(null)}>
	          <div className="modal" onClick={(event) => event.stopPropagation()}>
	            <header className="modal__header">
	              <h3>{benchmarkPreview.label || "ベンチマーク"}</h3>
	              <button
	                type="button"
	                className="workspace-button workspace-button--ghost"
	                style={{ background: "#0f172a", color: "#fff", borderColor: "#0f172a" }}
	                onClick={() => setBenchmarkPreview(null)}
	              >
	                閉じる
	              </button>
	            </header>
		            <div className="modal__body" style={{ maxHeight: "70vh", overflow: "auto" }}>
		              {benchmarkPreview.loading ? <p className="muted">読み込み中…</p> : null}
		              {benchmarkPreview.error ? <div className="main-alert main-alert--error">{benchmarkPreview.error}</div> : null}
		              {benchmarkPreview.metrics ? (
		                <dl className="portal-kv" style={{ marginTop: 0, marginBottom: 12 }}>
		                  <dt>文字数</dt>
		                  <dd>
		                    {benchmarkPreview.metrics.nonWhitespaceChars.toLocaleString("ja-JP")}字（推定{" "}
		                    {benchmarkPreview.metrics.estimatedMinutes.toFixed(1)}分）
		                  </dd>

		                  <dt>行</dt>
		                  <dd>
		                    {benchmarkPreview.metrics.lines.toLocaleString("ja-JP")}（非空{" "}
		                    {benchmarkPreview.metrics.nonEmptyLines.toLocaleString("ja-JP")}）
		                  </dd>

		                  <dt>見出し / 区切り</dt>
		                  <dd>
		                    {benchmarkPreview.metrics.headings.toLocaleString("ja-JP")} /{" "}
		                    {benchmarkPreview.metrics.dividers.toLocaleString("ja-JP")}
		                  </dd>
		                </dl>
		              ) : null}
		              {benchmarkPreview.content ? (
		                <pre style={{ whiteSpace: "pre-wrap" }}>{benchmarkPreview.content}</pre>
		              ) : !benchmarkPreview.loading && !benchmarkPreview.error ? (
		                <p className="muted">（内容が空です）</p>
		              ) : null}
		            </div>
	          </div>
	        </div>
	      ) : null}
	    </section>
	  );
}
