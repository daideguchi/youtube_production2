import { useEffect, useMemo, useState } from "react";
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
  return channel.branding?.title ?? channel.youtube_title ?? channel.name ?? channel.code;
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

  const [channelSearch, setChannelSearch] = useState("");
  const [searchTerm, setSearchTerm] = useState("");

  const [benchmarkPreview, setBenchmarkPreview] = useState<{
    label: string;
    loading: boolean;
    content: string;
    error: string | null;
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

  const filteredChannels = useMemo(() => {
    const keyword = channelSearch.trim().toLowerCase();
    if (!keyword) {
      return sortedChannels;
    }
    return sortedChannels.filter((channel) => {
      const displayName = resolveChannelDisplayName(channel);
      const searchText = `${channel.code} ${displayName}`.toLowerCase();
      return searchText.includes(keyword);
    });
  }, [channelSearch, sortedChannels]);

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
  }, [selectedChannel]);

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
      setBenchmarkPreview({ label, loading: false, content: "", error: "path が空です。" });
      return;
    }
    setBenchmarkPreview({ label, loading: true, content: "", error: null });
    try {
      const response = await fetchResearchFile(base, path);
      setBenchmarkPreview({ label, loading: false, content: response.content, error: null });
    } catch (err) {
      setBenchmarkPreview({ label, loading: false, content: "", error: err instanceof Error ? err.message : String(err) });
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
            <input
              type="search"
              className="channel-portal-switcher-search"
              value={channelSearch}
              onChange={(event) => setChannelSearch(event.target.value)}
              placeholder="チャンネル検索（CH番号 / 名前）"
              aria-label="チャンネル検索"
            />
            <span className="muted channel-portal-switcher-count">
              {filteredChannels.length}/{sortedChannels.length}
            </span>
          </div>

          <div className="channel-portal-switcher" role="tablist" aria-label="チャンネル切り替え">
            {channelsLoading ? (
              <span className="muted">チャンネル読み込み中…</span>
            ) : channelsError ? (
              <span className="muted">チャンネル取得に失敗: {channelsError}</span>
            ) : null}
            {!channelsLoading && !channelsError && filteredChannels.length === 0 ? (
              <span className="muted">一致するチャンネルがありません。</span>
            ) : null}
            {filteredChannels.map((channel) => {
              const isActive = channel.code === selectedChannel;
              const displayName = resolveChannelDisplayName(channel);
              const avatarUrl = channel.branding?.avatar_url ?? null;
              const themeColor = channel.branding?.theme_color ?? null;
              const avatarStyle =
                avatarUrl != null
                  ? { backgroundImage: `url(${avatarUrl})` }
                  : themeColor
                    ? { background: themeColor }
                    : undefined;
              const avatarLabel = displayName.slice(0, 2);
              return (
                <button
                  key={channel.code}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`channel-portal-switcher__item${isActive ? " is-active" : ""}`}
                  onClick={() => navigate(`/channels/${encodeURIComponent(channel.code)}/portal`)}
                  title={displayName}
                >
                  <span
                    className={`channel-portal-switcher__avatar${avatarUrl ? " is-image" : ""}`}
                    style={avatarStyle}
                    aria-hidden
                  >
                    {!avatarUrl ? avatarLabel : null}
                  </span>
                  <span className="channel-portal-switcher__meta">
                    <span className="channel-portal-switcher__code">{channel.code}</span>
                    <span className="channel-portal-switcher__name">{displayName}</span>
                  </span>
                </button>
              );
            })}
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
        <div className="channel-portal-top-grid">
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
                <button
                  type="button"
                  className="channel-card__action"
                  onClick={() => navigate("/capcut-edit")}
                >
                  CapCutへ
                </button>
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

	              <details className="portal-details" style={{ marginTop: 12 }} open>
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
          </div>

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

                <details className="portal-details" open>
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
                    <td colSpan={6} className="channel-projects__empty">
                      読み込み中…
                    </td>
                  </tr>
                ) : filteredPlanningRows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="channel-projects__empty">
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
                    return (
                      <tr
                        key={row.video_number}
                        className="channel-projects__row"
                      >
                        <th scope="row">{row.video_number}</th>
                        <td>
                          <div className="channel-projects__title">{rowTitle}</div>
                          <div className="portal-chip-row">
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
                        <td>{row.progress ?? "—"}</td>
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
