import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_GENERATION_OPTIONS, createVideoJob, fetchRemotionProjects, fetchVideoJobs, fetchVideoJobLog, fetchVideoProjectDetail, resolveApiUrl, updateVideoGenerationOptions } from "../api/client";
import type { RemotionProjectSummary, RemotionAssetStatus, RemotionRenderOutput, VideoJobRecord, VideoGenerationOptions } from "../api/types";
import { loadWorkspaceSelection, saveWorkspaceSelection } from "../utils/workspaceSelection";

const CHANNEL_FILTER_ALL = "ALL";
const CHANNEL_FILTER_UNKNOWN = "__UNKNOWN__";
const STATUS_FILTER_ALL = "ALL";
const REMOTION_STATUS_OPTIONS: RemotionProjectSummary["status"][] = [
  "missing_assets",
  "assets_ready",
  "scaffolded",
  "rendered",
];

const STATUS_LABELS: Record<RemotionProjectSummary["status"], string> = {
  missing_assets: "要準備",
  assets_ready: "素材OK",
  scaffolded: "テンプレ生成済み",
  rendered: "mp4出力済み",
};

const STATUS_CLASS: Record<RemotionProjectSummary["status"], string> = {
  missing_assets: "danger",
  assets_ready: "info",
  scaffolded: "warning",
  rendered: "success",
};

type Banner = { type: "success" | "error" | "info"; message: string };

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

function formatSize(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) {
    return "—";
  }
  if (bytes === 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || Number.isNaN(seconds)) {
    return "—";
  }
  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours > 0) {
    return `${hours}時間${mins}分`;
  }
  if (minutes > 0) {
    return `${minutes}分${remain.toString().padStart(2, "0")}秒`;
  }
  return `${remain}秒`;
}

function statusBadge(status: RemotionProjectSummary["status"]) {
  return (
    <span className={`remotion-status remotion-status--${STATUS_CLASS[status] ?? "neutral"}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

async function copyToClipboard(value?: string | null): Promise<void> {
  if (!value) {
    return;
  }
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export function RemotionWorkspace() {
  const [projects, setProjects] = useState<RemotionProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectionRef = useRef(loadWorkspaceSelection());
  const [selectedId, setSelectedId] = useState<string | null>(() => selectionRef.current?.projectId ?? null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [channelFilter, setChannelFilter] = useState<string>(() => selectionRef.current?.channel ?? CHANNEL_FILTER_ALL);
  const [statusFilter, setStatusFilter] = useState<RemotionProjectSummary["status"] | typeof STATUS_FILTER_ALL>(STATUS_FILTER_ALL);
  const [keyword, setKeyword] = useState("");
  const [jobSubmitting, setJobSubmitting] = useState(false);
  const [jobRecords, setJobRecords] = useState<VideoJobRecord[]>([]);
  const [jobLoading, setJobLoading] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [jobLog, setJobLog] = useState<string>("");
  const [jobLogLoading, setJobLogLoading] = useState(false);
  const [autoRefreshJobs, setAutoRefreshJobs] = useState(false);
  const autoRefreshRef = useRef<number | null>(null);
  const retryTimeoutRef = useRef<number | null>(null);
  const [retryInfo, setRetryInfo] = useState<number | null>(null);
  const [generationOptions, setGenerationOptions] = useState<VideoGenerationOptions>({ ...DEFAULT_GENERATION_OPTIONS });
  const [generationOptionsLoading, setGenerationOptionsLoading] = useState(false);
  const lastSavedGenerationOptionsRef = useRef<string>(JSON.stringify(DEFAULT_GENERATION_OPTIONS));

  const showBanner = useCallback((next: Banner) => {
    setBanner(next);
    if (next.type !== "error") {
      window.setTimeout(() => setBanner(null), 3200);
    }
  }, []);

  const setAndTrackGenerationOptions = useCallback((options: VideoGenerationOptions) => {
    const nextOptions = { ...options };
    setGenerationOptions(nextOptions);
    lastSavedGenerationOptionsRef.current = JSON.stringify(nextOptions);
  }, []);

  const handleResetGenerationOptions = useCallback(() => {
    setAndTrackGenerationOptions(DEFAULT_GENERATION_OPTIONS);
  }, [setAndTrackGenerationOptions]);

  const loadProjects = useCallback(async (attempt = 0) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRemotionProjects();
      setProjects(data);
      setSelectedId((current) => {
        if (current && data.some((project) => project.projectId === current)) {
          return current;
        }
        return data[0]?.projectId ?? null;
      });
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current);
        retryTimeoutRef.current = null;
      }
      setRetryInfo(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      showBanner({ type: "error", message: `Remotionステータス取得失敗: ${message}` });
      if (attempt < 2) {
        const nextAttempt = attempt + 1;
        setRetryInfo(nextAttempt);
        retryTimeoutRef.current = window.setTimeout(() => {
          loadProjects(nextAttempt);
        }, 3000);
      } else {
        setRetryInfo(null);
      }
    } finally {
      setLoading(false);
    }
  }, [showBanner]);

  useEffect(() => {
    void loadProjects();
    return () => {
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current);
      }
    };
  }, [loadProjects]);

  const handleManualRetry = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
    setRetryInfo(null);
    void loadProjects();
  }, [loadProjects]);

  const loadRenderJobs = useCallback(async (projectId: string) => {
    setJobLoading(true);
    setJobError(null);
    try {
      const records = await fetchVideoJobs(projectId, 50);
      const renderJobs = records.filter((job) => job.action === "render_remotion");
      setJobRecords(renderJobs);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setJobError(message);
    } finally {
      setJobLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setJobRecords([]);
      setJobError(null);
      return;
    }
    void loadRenderJobs(selectedId);
  }, [selectedId, loadRenderJobs]);

  useEffect(() => {
    if (!selectedId) {
      setGenerationOptions({ ...DEFAULT_GENERATION_OPTIONS });
      lastSavedGenerationOptionsRef.current = JSON.stringify(DEFAULT_GENERATION_OPTIONS);
      return;
    }
    let cancelled = false;
    setGenerationOptionsLoading(true);
    void (async () => {
      try {
        const detail = await fetchVideoProjectDetail(selectedId);
        if (cancelled) {
          return;
        }
        const nextOptions = detail.generationOptions ?? DEFAULT_GENERATION_OPTIONS;
        setAndTrackGenerationOptions(nextOptions);
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          showBanner({ type: "error", message: `生成パラメータ取得失敗: ${message}` });
        }
      } finally {
        if (!cancelled) {
          setGenerationOptionsLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, setAndTrackGenerationOptions, showBanner]);

  useEffect(() => {
    if (!autoRefreshJobs || !selectedId) {
      if (autoRefreshRef.current) {
        window.clearInterval(autoRefreshRef.current);
        autoRefreshRef.current = null;
      }
      return;
    }
    autoRefreshRef.current = window.setInterval(() => {
      void loadRenderJobs(selectedId);
    }, 5000);
    return () => {
      if (autoRefreshRef.current) {
        window.clearInterval(autoRefreshRef.current);
        autoRefreshRef.current = null;
      }
    };
  }, [autoRefreshJobs, selectedId, loadRenderJobs]);

  const channelOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const project of projects) {
      const key = project.channelId ?? CHANNEL_FILTER_UNKNOWN;
      counts[key] = (counts[key] ?? 0) + 1;
    }
    const options = [
      { value: CHANNEL_FILTER_ALL, label: `全チャンネル (${projects.length})` },
    ];
    for (const [code, count] of Object.entries(counts)) {
      const label = code === CHANNEL_FILTER_UNKNOWN ? "チャンネル未設定" : code;
      options.push({ value: code, label: `${label} (${count})` });
    }
    return options;
  }, [projects]);

  const filteredProjects = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return projects.filter((project) => {
      if (channelFilter !== CHANNEL_FILTER_ALL) {
        const projectChannel = project.channelId ?? CHANNEL_FILTER_UNKNOWN;
        if (projectChannel !== channelFilter) {
          return false;
        }
      }
      if (statusFilter !== STATUS_FILTER_ALL && project.status !== statusFilter) {
        return false;
      }
      if (normalizedKeyword) {
        const haystacks = [
          project.projectId,
          project.title ?? "",
          project.channelId ?? "",
        ];
        if (!haystacks.some((value) => value.toLowerCase().includes(normalizedKeyword))) {
          return false;
        }
      }
      return true;
    });
  }, [projects, channelFilter, statusFilter, keyword]);

  useEffect(() => {
    if (channelFilter === CHANNEL_FILTER_ALL || channelFilter === CHANNEL_FILTER_UNKNOWN) {
      return;
    }
    if (!projects.length) {
      return;
    }
    if (projects.some((project) => (project.channelId ?? CHANNEL_FILTER_UNKNOWN) === channelFilter)) {
      return;
    }
    setChannelFilter(CHANNEL_FILTER_ALL);
  }, [projects, channelFilter]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedId(filteredProjects[0]?.projectId ?? null);
      return;
    }
    if (!filteredProjects.some((project) => project.projectId === selectedId)) {
      setSelectedId(filteredProjects[0]?.projectId ?? null);
    }
  }, [filteredProjects, selectedId]);

  const selected = useMemo(() => projects.find((project) => project.projectId === selectedId) ?? null, [projects, selectedId]);

  const handleChannelFilterChange = useCallback(
    (value: string) => {
      if (!value || value === CHANNEL_FILTER_ALL) {
        setChannelFilter(CHANNEL_FILTER_ALL);
        return;
      }
      if (value === CHANNEL_FILTER_UNKNOWN) {
        setChannelFilter(CHANNEL_FILTER_UNKNOWN);
        return;
      }
      setChannelFilter(value.toUpperCase());
    },
    []
  );

  useEffect(() => {
    const storedChannel =
      channelFilter === CHANNEL_FILTER_ALL || channelFilter === CHANNEL_FILTER_UNKNOWN ? null : channelFilter;
    const storedProject = selected?.projectId ?? null;
    if (!storedChannel && !storedProject) {
      saveWorkspaceSelection(null);
      return;
    }
    saveWorkspaceSelection({
      channel: storedChannel,
      projectId: storedProject,
    });
  }, [channelFilter, selected]);

  const statusCounts = useMemo(() => {
    const summary: Record<RemotionProjectSummary["status"], number> = {
      missing_assets: 0,
      assets_ready: 0,
      scaffolded: 0,
      rendered: 0,
    };
    for (const project of projects) {
      summary[project.status] = (summary[project.status] ?? 0) + 1;
    }
    return summary;
  }, [projects]);

  const renderableStatuses: RemotionProjectSummary["status"][] = ["assets_ready", "scaffolded"];
  const hasActiveRenderJob = jobRecords.some((job) => job.status === "queued" || job.status === "running");
  const canRenderRemotion = Boolean(selected && renderableStatuses.includes(selected.status));
  const remotionSelectionNotice =
    channelFilter === CHANNEL_FILTER_ALL
      ? "チャンネルを選択すると Video Production の選択内容を共有し、同じ案件で Remotion の準備状況を確認できます。"
      : !selected
        ? "左側の一覧からエピソードを選択すると素材チェックとレンダリング操作が有効になります。"
        : !canRenderRemotion
          ? "素材チェックの結果が \"要準備\" のままなのでレンダリングは待機してください。assets_ready 以上になるとボタンが有効になります。"
          : null;

  useEffect(() => {
    if (!selectedId) {
      return;
    }
    const serialized = JSON.stringify(generationOptions);
    if (serialized === lastSavedGenerationOptionsRef.current) {
      return;
    }
    const handle = window.setTimeout(() => {
      void (async () => {
        try {
          const saved = await updateVideoGenerationOptions(selectedId, generationOptions);
          setAndTrackGenerationOptions(saved);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          showBanner({ type: "error", message: `生成パラメータの保存に失敗しました: ${message}` });
        }
      })();
    }, 800);
    return () => window.clearTimeout(handle);
  }, [generationOptions, selectedId, setAndTrackGenerationOptions, showBanner]);

  const totalProjects = projects.length;
  const readyForRender = projects.filter((project) => project.status === "assets_ready" || project.status === "scaffolded").length;

  const handleRefresh = useCallback(() => {
    void loadProjects();
  }, [loadProjects]);

  const handleCopyPath = useCallback(
    async (value?: string | null) => {
      if (!value) {
        return;
      }
      await copyToClipboard(value);
      showBanner({ type: "info", message: "パスをコピーしました" });
    },
    [showBanner]
  );

  const handleSelectJob = useCallback(
    async (jobId: string) => {
      setSelectedJobId(jobId);
      setJobLogLoading(true);
      try {
        const logText = await fetchVideoJobLog(jobId);
        setJobLog(logText);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        showBanner({ type: "error", message: `ジョブログ取得失敗: ${message}` });
      } finally {
        setJobLogLoading(false);
      }
    },
    [showBanner]
  );

  const handleRenderRemotion = useCallback(async () => {
    if (!selected) {
      return;
    }
    setJobSubmitting(true);
    try {
      await createVideoJob(selected.projectId, { action: "render_remotion" });
      showBanner({ type: "success", message: `Remotionレンダリングをキューに追加しました (${selected.projectId})` });
      await loadRenderJobs(selected.projectId);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      showBanner({ type: "error", message: `Remotionジョブ投入に失敗しました: ${message}` });
    } finally {
      setJobSubmitting(false);
    }
  }, [selected, showBanner, loadRenderJobs]);

  return (
    <div className="remotion-workspace">
      <header className="remotion-workspace__header">
        <div>
          <h1>動画制作（Remotion）</h1>
          <p>CapCut とは独立した Remotion レンダリング・品質チェック用のスペースです。素材整合 → テンプレ生成 → mp4 出力までをコードで一元管理します。</p>
          <p className="remotion-workspace__note">
            仕様: <code>ssot/README.md §1.5.11</code> · データ契約: <code>docs/VIDEO_DATA_CONTRACT.md</code>
          </p>
        </div>
        <div className="remotion-workspace__actions">
          <button type="button" className="remotion-button" onClick={handleRefresh} disabled={loading}>
            {loading ? "更新中..." : "最新を取得"}
          </button>
          <a
            className="remotion-button remotion-button--secondary"
            href="/docs/VIDEO_DATA_CONTRACT.md"
            target="_blank"
            rel="noreferrer"
          >
            Data Contract ↗
          </a>
        </div>
      </header>
      {banner ? <div className={`remotion-alert remotion-alert--${banner.type}`}>{banner.message}</div> : null}
      {error ? <div className="remotion-alert remotion-alert--error">{error}</div> : null}
      <RemotionFlowGuide
        channelSelected={channelFilter !== CHANNEL_FILTER_ALL}
        projectSelected={Boolean(selected)}
        materialsReady={Boolean(selected && renderableStatuses.includes(selected.status))}
        canRender={canRenderRemotion}
      />
      <RemotionSelectionPanel
        channelFilter={channelFilter}
        onChannelChange={handleChannelFilterChange}
        channelOptions={channelOptions.map((option) => ({ value: option.value, label: option.label }))}
        statusFilter={statusFilter}
        onStatusChange={setStatusFilter}
        keyword={keyword}
        onKeywordChange={setKeyword}
        errorMessage={error}
        loading={loading}
        retryInfo={retryInfo}
        onRetry={handleManualRetry}
      />
      {remotionSelectionNotice ? (
        <div className="remotion-alert remotion-alert--info">{remotionSelectionNotice}</div>
      ) : null}
      <section className="remotion-summary">
        <div className="remotion-summary__card">
          <p>登録プロジェクト</p>
          <strong>{totalProjects}</strong>
        </div>
        <div className="remotion-summary__card">
          <p>素材準備完了</p>
          <strong>{readyForRender}</strong>
        </div>
        <div className="remotion-summary__card">
          <p>レンダリング済み</p>
          <strong>{statusCounts.rendered}</strong>
        </div>
        <div className="remotion-summary__card remotion-summary__card--grid">
          <span>要準備</span>
          <strong>{statusCounts.missing_assets}</strong>
          <span>テンプレ生成</span>
          <strong>{statusCounts.scaffolded}</strong>
        </div>
      </section>
      <div className="remotion-layout">
        <section className="remotion-panel remotion-panel--list">
          <header className="remotion-panel__header">
            <div className="remotion-panel__header-col">
              <h2>案件一覧</h2>
              <p>Remotion レンダリングの前提条件や成果物を一覧できます。</p>
            </div>
            <span className="remotion-count">表示 {filteredProjects.length} / 全 {totalProjects}</span>
          </header>
          <div className="remotion-table-wrapper">
            <table className="remotion-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>チャンネル</th>
                  <th>タイトル</th>
                  <th>尺</th>
                  <th>画像</th>
                  <th>状態</th>
                </tr>
              </thead>
              <tbody>
                {filteredProjects.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="remotion-table__empty">
                      {loading ? "読み込み中..." : "条件に一致するプロジェクトがありません"}
                    </td>
                  </tr>
                ) : null}
                {filteredProjects.map((project) => (
                  <tr
                    key={project.projectId}
                    className={project.projectId === selectedId ? "is-active" : undefined}
                    onClick={() => setSelectedId(project.projectId)}
                  >
                    <td className="mono">{project.projectId}</td>
                    <td>{project.channelId ?? "—"}</td>
                    <td>{project.title ?? "—"}</td>
                    <td>{formatDuration(project.durationSec)}</td>
                    <td>{project.metrics.imageCount}</td>
                    <td>{statusBadge(project.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="remotion-actions">
            {selected ? (
              <RemotionGenerationForm
                options={generationOptions}
                onChange={setGenerationOptions}
                disabled={generationOptionsLoading}
                onReset={handleResetGenerationOptions}
              />
            ) : null}
            <button
              type="button"
              className="remotion-button"
              onClick={handleRenderRemotion}
              disabled={!selected || !canRenderRemotion || jobSubmitting || loading || hasActiveRenderJob}
              title={!selected ? "案件を選択してください" : !canRenderRemotion ? "素材が assets_ready になるまでレンダリングできません" : undefined}
            >
              {jobSubmitting
                ? "ジョブ投入中..."
                : hasActiveRenderJob
                  ? "レンダリング進行中"
                  : canRenderRemotion
                    ? "Remotionレンダリングを実行"
                    : "素材待ち (assets_ready待機)"}
            </button>
            <p className="remotion-text-muted">
              {canRenderRemotion
                ? "実行後は下記のジョブ一覧と CapCut ジョブ管理で進捗を確認できます。"
                : "SRT ・ 画像 ・ belt など必須素材が揃うとボタンが有効化されます。"}
            </p>
          </div>
          <RemotionJobList
            jobs={jobRecords}
            loading={jobLoading}
            error={jobError}
            onReload={() => selected && loadRenderJobs(selected.projectId)}
            onSelectJob={handleSelectJob}
            selectedJobId={selectedJobId}
            autoRefresh={autoRefreshJobs}
            onToggleAutoRefresh={setAutoRefreshJobs}
          />
          {selectedJobId ? (
            <div className="remotion-log-panel">
              <header>
                <h4>ジョブログ: {selectedJobId.slice(0, 8)}</h4>
                <button type="button" className="remotion-button remotion-button--secondary" onClick={() => selectedId && handleSelectJob(selectedJobId)} disabled={jobLogLoading}>
                  {jobLogLoading ? "更新中..." : "最新を取得"}
                </button>
              </header>
              <pre className="remotion-log-output">{jobLog || (jobLogLoading ? "読み込み中..." : "ログ出力なし")}</pre>
            </div>
          ) : null}
          <RemotionEditingCard project={selected} onCopy={handleCopyPath} />
        </section>
        <section className="remotion-panel remotion-panel--detail">
          <header className="remotion-panel__header">
            <div>
              <h2>詳細</h2>
              <p>{selected ? selected.title ?? selected.projectId : "案件を選択してください。"}</p>
            </div>
          </header>
          {!selected ? (
            <p className="remotion-text-muted">左の一覧から案件を選択してください。</p>
          ) : (
            <>
              <div className="remotion-status-row">
                {statusBadge(selected.status)}
                <span>画像 {selected.metrics.imageCount} 枚 · 必須素材 {selected.metrics.assetReady}/{selected.metrics.assetTotal}</span>
              </div>
              {selected.issues.length > 0 ? (
                <div className="remotion-alert remotion-alert--warning">
                  <strong>未解決の課題</strong>
                  <ul>
                    {selected.issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <RemotionAssetsTable assets={selected.assets} onCopy={handleCopyPath} />
              <RemotionOutputs outputs={selected.outputs} onCopy={handleCopyPath} />
              <div className="remotion-kv">
                <div>
                  <dt>Remotion ディレクトリ</dt>
                  <dd>
                    {selected.remotionDir ?? "—"}
                    {selected.remotionDir ? (
                      <button type="button" className="remotion-link" onClick={() => handleCopyPath(selected.remotionDir)}>
                        コピー
                      </button>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>最終レンダリング</dt>
                  <dd>{formatDate(selected.lastRendered)}</dd>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

type AssetTableProps = {
  assets: RemotionAssetStatus[];
  onCopy: (value?: string | null) => void;
};

function RemotionAssetsTable({ assets, onCopy }: AssetTableProps) {
  if (!assets.length) {
    return null;
  }
  return (
    <div className="remotion-section">
      <h3>素材チェックリスト</h3>
      <div className="remotion-table-wrapper">
        <table className="remotion-table remotion-table--compact">
          <thead>
            <tr>
              <th>項目</th>
              <th>状態</th>
              <th>パス</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <tr key={asset.label}>
                <td>{asset.label}</td>
                <td>
                  <span className={asset.exists ? "remotion-tag remotion-tag--ok" : "remotion-tag remotion-tag--ng"}>
                    {asset.exists ? "OK" : "NG"}
                  </span>
                </td>
                <td className="mono">
                  {asset.path ?? "—"}
                  {asset.path ? (
                    <button type="button" className="remotion-link" onClick={() => onCopy(asset.path)}>
                      コピー
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type RemotionGenerationFormProps = {
  options: VideoGenerationOptions;
  onChange: (options: VideoGenerationOptions) => void;
  disabled?: boolean;
  onReset?: () => void;
};

function RemotionGenerationForm({ options, onChange, disabled, onReset }: RemotionGenerationFormProps) {
  const handleNumberChange = (key: keyof VideoGenerationOptions, value: number) => {
    if (Number.isNaN(value)) {
      return;
    }
    onChange({ ...options, [key]: value });
  };

  const handleTextChange = (key: keyof VideoGenerationOptions, value: string) => {
    onChange({ ...options, [key]: value });
  };

  return (
    <div className="remotion-generation-form">
      <div className="remotion-generation-form__row">
        <label>
          画像表示秒数 (imgdur)
          <input
            type="number"
            min={1}
            max={300}
            step={1}
            value={options.imgdur}
            onChange={(event) => handleNumberChange("imgdur", Number(event.target.value))}
            disabled={disabled}
          />
        </label>
        <label>
          クロスフェード秒 (crossfade)
          <input
            type="number"
            min={0}
            max={30}
            step={0.1}
            value={options.crossfade}
            onChange={(event) => handleNumberChange("crossfade", Number(event.target.value))}
            disabled={disabled}
          />
        </label>
      </div>
      <div className="remotion-generation-form__row">
        <label>
          FPS
          <input
            type="number"
            min={1}
            max={240}
            step={1}
            value={options.fps}
            onChange={(event) => handleNumberChange("fps", Number(event.target.value))}
            disabled={disabled}
          />
        </label>
        <label>
          スタイルタグ
          <input
            type="text"
            value={options.style}
            onChange={(event) => handleTextChange("style", event.target.value)}
            disabled={disabled}
            placeholder="例: cinematic 光"
          />
        </label>
      </div>
      <div className="remotion-generation-form__row">
        <label htmlFor="remotion-size">解像度 (WxH)</label>
        <input
          id="remotion-size"
          type="text"
          value={options.size}
          onChange={(event) => handleTextChange("size", event.target.value)}
          placeholder="1920x1080"
          disabled={disabled}
        />
        <label htmlFor="remotion-fit">Fit</label>
        <select
          id="remotion-fit"
          value={options.fit}
          onChange={(event) => handleTextChange("fit", event.target.value as VideoGenerationOptions["fit"])}
          disabled={disabled}
        >
          <option value="cover">cover</option>
          <option value="contain">contain</option>
          <option value="fill">fill</option>
        </select>
        <label htmlFor="remotion-margin">余白 (px)</label>
        <input
          id="remotion-margin"
          type="number"
          min={0}
          max={500}
          value={options.margin}
          onChange={(event) => handleNumberChange("margin", Number(event.target.value))}
          disabled={disabled}
        />
      </div>
      <p className="remotion-text-muted">
        変更は自動保存され、CapCut/Remotion 双方のジョブに適用されます。
        {options.style ? (
          <>
            {" "}
            現在のスタイル: <code>{options.style}</code>
          </>
        ) : null}
      </p>
      {onReset ? (
        <div className="remotion-generation-form__actions">
          <button type="button" className="remotion-button remotion-button--secondary" onClick={onReset} disabled={disabled}>
            既定値に戻す
          </button>
        </div>
      ) : null}
    </div>
  );
}

type RemotionFlowGuideProps = {
  channelSelected: boolean;
  projectSelected: boolean;
  materialsReady: boolean;
  canRender: boolean;
};

function RemotionFlowGuide({ channelSelected, projectSelected, materialsReady, canRender }: RemotionFlowGuideProps) {
  const steps = [
    { label: "1. チャンネル選択", done: channelSelected },
    { label: "2. 案件選択", done: projectSelected },
    { label: "3. 素材確認", done: materialsReady },
    { label: "4. パラメータ設定", done: materialsReady },
    { label: "5. レンダー", done: canRender },
  ];
  const activeIndex = steps.findIndex((step) => !step.done);
  return (
    <div className="remotion-flow-guide">
      {steps.map((step, index) => {
        const isActive = activeIndex === -1 ? index === steps.length - 1 : index === activeIndex;
        return (
          <div key={step.label} className={`remotion-flow-step ${step.done ? "is-done" : ""} ${isActive ? "is-active" : ""}`}>
            {step.label}
          </div>
        );
      })}
    </div>
  );
}

type RemotionSelectionPanelProps = {
  channelFilter: string;
  onChannelChange: (value: string) => void;
  channelOptions: { value: string; label: string }[];
  statusFilter: typeof STATUS_FILTER_ALL | RemotionProjectSummary["status"];
  onStatusChange: (value: typeof STATUS_FILTER_ALL | RemotionProjectSummary["status"]) => void;
  keyword: string;
  onKeywordChange: (value: string) => void;
  errorMessage: string | null;
  loading: boolean;
  retryInfo: number | null;
  onRetry: () => void;
};

function RemotionSelectionPanel({
  channelFilter,
  onChannelChange,
  channelOptions,
  statusFilter,
  onStatusChange,
  keyword,
  onKeywordChange,
  errorMessage,
  loading,
  retryInfo,
  onRetry,
}: RemotionSelectionPanelProps) {
  return (
    <section className="remotion-selection-panel">
      <div className="remotion-selection-panel__row">
        <label>
          チャンネル
          <select value={channelFilter} onChange={(event) => onChannelChange(event.target.value)}>
            {channelOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          ステータス
          <select
            value={statusFilter}
            onChange={(event) => onStatusChange(event.target.value as typeof STATUS_FILTER_ALL | RemotionProjectSummary["status"])}
          >
            <option value={STATUS_FILTER_ALL}>すべて</option>
            {REMOTION_STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </label>
        <label>
          キーワード
          <input
            type="text"
            value={keyword}
            placeholder="ID / タイトル / チャンネル"
            onChange={(event) => onKeywordChange(event.target.value)}
          />
        </label>
      </div>
      {errorMessage ? (
        <div className="remotion-selection-panel__error">
          <p>{errorMessage}</p>
          <div className="remotion-selection-panel__actions">
            {retryInfo ? <span>再試行中... ({retryInfo + 1}/3)</span> : null}
            <button type="button" className="remotion-button remotion-button--secondary" onClick={onRetry} disabled={loading}>
              {loading ? "再取得中..." : "再読み込み"}
            </button>
          </div>
        </div>
      ) : (
        <p className="remotion-text-muted">まずチャンネルと案件を選び、上から順に進めてください。</p>
      )}
    </section>
  );
}

type OutputsProps = {
  outputs: RemotionRenderOutput[];
  onCopy: (value?: string | null) => void;
};

function RemotionOutputs({ outputs, onCopy }: OutputsProps) {
  return (
    <div className="remotion-section">
      <h3>成果物</h3>
      {outputs.length === 0 ? (
        <p className="remotion-text-muted">まだ mp4 が生成されていません。</p>
      ) : (
        <ul className="remotion-output-list">
          {outputs.map((output) => (
            <li key={output.path}>
              <div>
                <strong>{output.fileName}</strong>
                <span className="remotion-text-muted">{formatSize(output.sizeBytes)} · {formatDate(output.modifiedTime)}</span>
              </div>
              <button type="button" className="remotion-link" onClick={() => onCopy(output.path)}>
                パスをコピー
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type RemotionJobListProps = {
  jobs: VideoJobRecord[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  onSelectJob: (jobId: string) => void;
  selectedJobId: string | null;
  autoRefresh: boolean;
  onToggleAutoRefresh: (value: boolean) => void;
};
type RemotionEditingCardProps = {
  project: RemotionProjectSummary | null;
  onCopy: (value?: string | null) => void;
};

function RemotionJobList({ jobs, loading, error, onReload, onSelectJob, selectedJobId, autoRefresh, onToggleAutoRefresh }: RemotionJobListProps) {
  return (
    <div className="remotion-section">
      <div className="remotion-panel__header">
        <div className="remotion-panel__header-col">
          <h3>レンダリングジョブ</h3>
          <p>render_remotion の履歴</p>
        </div>
        <div className="remotion-job-controls">
          <label className="remotion-job-autorefresh">
            <input type="checkbox" checked={autoRefresh} onChange={(event) => onToggleAutoRefresh(event.target.checked)} /> 自動更新
          </label>
          <button type="button" className="remotion-button remotion-button--secondary" onClick={onReload} disabled={loading}>
            {loading ? "更新中..." : "更新"}
          </button>
        </div>
      </div>
      {error ? <div className="remotion-alert remotion-alert--error">{error}</div> : null}
      <div className="remotion-table-wrapper">
        <table className="remotion-table remotion-table--compact">
          <thead>
            <tr>
              <th>ID</th>
              <th>状態</th>
              <th>開始</th>
              <th>終了</th>
              <th>ログ</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={5} className="remotion-table__empty">
                  {loading ? "ジョブを取得しています..." : "render_remotion ジョブがありません"}
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id} className={job.id === selectedJobId ? "is-active" : undefined} onClick={() => onSelectJob(job.id)}>
                  <td className="mono">{job.id.slice(0, 8)}</td>
                  <td>
                    <span className={`remotion-status remotion-status--${STATUS_CLASS_MAP[job.status] ?? "neutral"}`}>
                      {job.status}
                    </span>
                  </td>
                  <td>{formatDate(job.started_at)}</td>
                  <td>{formatDate(job.finished_at)}</td>
                  <td>
                    {job.log_path ? (
                      <a
                        className="remotion-link"
                        href={resolveApiUrl(`/api/video-production/jobs/${job.id}/log`)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        ログ表示
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const STATUS_CLASS_MAP: Record<VideoJobRecord["status"], string> = {
  queued: "info",
  running: "warning",
  succeeded: "success",
  failed: "danger",
};

function RemotionEditingCard({ project, onCopy }: RemotionEditingCardProps) {
  if (!project) {
    return null;
  }
  const latestOutput = project.outputs[0] ?? null;
  const remotionDir = project.remotionDir ?? (project.remotionDir === undefined ? null : project.remotionDir);
  const command = remotionDir
    ? `cd ${remotionDir} && npx remotion preview`
    : `cd commentary_02_srt2images_timeline/output/${project.projectId}/remotion && npx remotion preview`;

  return (
    <div className="remotion-editing-card">
      <h3>Remotion 編集</h3>
      <p className="remotion-text-muted">
        Remotion のタイムラインを開き、細かな調整や再レンダリングを行うためのエントリーポイントです。
      </p>
      <dl className="remotion-editing-grid">
        <div>
          <dt>最新レンダー</dt>
          <dd>
            {latestOutput ? (
              <>
                <strong>{latestOutput.fileName}</strong>
                <span className="remotion-text-muted"> · {formatDate(latestOutput.modifiedTime)}</span>
                <button type="button" className="remotion-link" onClick={() => onCopy(latestOutput.path)}>
                  パスをコピー
                </button>
              </>
            ) : (
              "まだ mp4 が生成されていません"
            )}
          </dd>
        </div>
        <div>
          <dt>preview コマンド</dt>
          <dd className="remotion-command">
            <code>{command}</code>
            <button type="button" className="remotion-link" onClick={() => onCopy(command)}>
              コピー
            </button>
          </dd>
        </div>
      </dl>
      <ol className="remotion-editing-steps">
        <li>上記コマンドをターミナルで実行（Remotion preview が起動）。</li>
        <li>ブラウザ上でタイムラインやコンポーネントを編集。</li>
        <li>修正後に `render_remotion` ジョブを再投入し mp4 を更新。</li>
      </ol>
    </div>
  );
}
