import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  DEFAULT_GENERATION_OPTIONS,
  createVideoJob,
  fetchVideoProductionChannels,
  fetchVideoProjectDetail,
  fetchVideoProjects,
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

function resolveEffectiveStyle(
  generationOptions: VideoGenerationOptions,
  channelPreset: VideoProductionChannelPreset | null
): string {
  const fromOptions = (generationOptions.style ?? "").trim();
  if (fromOptions) return fromOptions;
  return (channelPreset?.style ?? "").trim();
}

type BannerState = { kind: "info" | "error" | "success"; message: string } | null;

export function ImageManagementPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  const [channels, setChannels] = useState<VideoProductionChannelPreset[]>([]);
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [projectDetail, setProjectDetail] = useState<VideoProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [banner, setBanner] = useState<BannerState>(null);
  const [promptFilter, setPromptFilter] = useState("");
  const [promptLimit, setPromptLimit] = useState<number>(50);

  const selectedProjectId = (searchParams.get("project") ?? "").trim();

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

  const handleSelectProject = (projectId: string) => {
    const params = new URLSearchParams(searchParams);
    if (projectId) {
      params.set("project", projectId);
    } else {
      params.delete("project");
    }
    setSearchParams(params, { replace: true });
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

  const bannerClass =
    banner?.kind === "error" ? "main-alert main-alert--error" : banner?.kind === "success" ? "main-alert main-alert--success" : "main-alert";

  return (
    <div className="page image-management-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">画像管理</p>
          <h1>モデル / 画風 / プロンプト</h1>
          <p className="page-lead">run_dir（Video Project）単位で、設定とプロンプトを確認しながら複数画風のバリアントを生成します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <button type="button" className="button button--ghost" onClick={() => void refreshIndex()} disabled={projectsLoading}>
            {projectsLoading ? "読込中…" : "一覧更新"}
          </button>
          <button type="button" className="button" onClick={() => void refreshDetail()} disabled={!selectedProjectId || detailLoading}>
            {detailLoading ? "更新中…" : "詳細更新"}
          </button>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>1) 対象プロジェクトを選ぶ</h2>
          <p className="shell-panel__subtitle">プロジェクト（run_dir）を選ぶと、モデル/画風/プロンプトと既存バリアントが表示されます。</p>
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
                    {p.title ? ` - ${p.title}` : ""}
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

          {projectsError ? <div className="main-alert main-alert--error">{projectsError}</div> : null}
          {channelsError ? <div className="main-alert main-alert--error">{channelsError}</div> : null}
          {detailError ? <div className="main-alert main-alert--error">{detailError}</div> : null}
          {banner ? <div className={bannerClass}>{banner.message}</div> : null}
          {!projectsLoading && !selectedProjectId ? <div className="main-alert">まず project を選択してください。</div> : null}

          {projectDetail ? (
            <div className="main-status" style={{ marginTop: 10 }}>
              <span className="status-chip">
                project: <code>{projectDetail.summary.id}</code>
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
            </div>
          ) : null}
        </div>
      </section>

      {projectDetail ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
              <h2>2) プロンプト（cues）</h2>
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

      {projectDetail ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <h2>3) 画風バリアント生成</h2>
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

