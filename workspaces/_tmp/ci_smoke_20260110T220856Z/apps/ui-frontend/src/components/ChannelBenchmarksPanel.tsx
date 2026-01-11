import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchChannelProfile, fetchResearchFile, updateChannelProfile } from "../api/client";
import type {
  BenchmarkChannelSpec,
  BenchmarkScriptSampleSpec,
  ChannelBenchmarksSpec,
} from "../api/types";

type ChannelBenchmarksPanelProps = {
  channelCode: string | null;
};

type BannerState = { type: "success" | "error" | "info"; text: string };

const BENCHMARK_CHARS_PER_SECOND = 6.0;

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

function buildEmptyBenchmarks(): ChannelBenchmarksSpec {
  return {
    version: 1,
    updated_at: null,
    channels: [],
    script_samples: [],
    notes: "",
  };
}

function normalizeBenchmarks(value: ChannelBenchmarksSpec | null | undefined): ChannelBenchmarksSpec {
  if (!value) {
    return buildEmptyBenchmarks();
  }
  return {
    version: typeof value.version === "number" ? value.version : 1,
    updated_at: value.updated_at ?? null,
    channels: Array.isArray(value.channels) ? value.channels : [],
    script_samples: Array.isArray(value.script_samples) ? value.script_samples : [],
    notes: value.notes ?? "",
  };
}

function createEmptyBenchmarkChannel(): BenchmarkChannelSpec {
  return { handle: "", name: "", url: "", note: "" };
}

function createEmptyScriptSample(): BenchmarkScriptSampleSpec {
  return { base: "research", path: "", label: "", note: "" };
}

function normalizeHandle(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

export function ChannelBenchmarksPanel({ channelCode }: ChannelBenchmarksPanelProps) {
  const [benchmarks, setBenchmarks] = useState<ChannelBenchmarksSpec>(buildEmptyBenchmarks());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<BannerState | null>(null);
  const [preview, setPreview] = useState<{
    base: "research" | "scripts";
    path: string;
    label: string;
    loading: boolean;
    content: string;
    metrics: BenchmarkScriptMetrics | null;
    error: string | null;
  } | null>(null);

  const normalizedCode = channelCode?.trim().toUpperCase() ?? null;

  const isValid = useMemo(() => {
    const hasChannels = benchmarks.channels.some((item) => item.handle?.trim() || item.url?.trim());
    const hasSamples = benchmarks.script_samples.some((item) => item.path?.trim());
    return hasChannels && hasSamples;
  }, [benchmarks.channels, benchmarks.script_samples]);

  const handleReload = useCallback(async () => {
    if (!normalizedCode) {
      setBenchmarks(buildEmptyBenchmarks());
      setError(null);
      setBanner(null);
      return;
    }
    setLoading(true);
    setError(null);
    setBanner(null);
    try {
      const profile = await fetchChannelProfile(normalizedCode);
      setBenchmarks(normalizeBenchmarks(profile.benchmarks));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [normalizedCode]);

  useEffect(() => {
    void handleReload();
  }, [handleReload]);

  const handleSave = useCallback(async () => {
    if (!normalizedCode) return;
    setSaving(true);
    setError(null);
    setBanner(null);
    try {
      const payload: ChannelBenchmarksSpec = {
        ...benchmarks,
        channels: benchmarks.channels
          .map((item) => ({
            ...item,
            handle: item.handle?.trim() ? normalizeHandle(item.handle) : null,
            name: item.name?.trim() || null,
            url: item.url?.trim() || null,
            note: item.note?.trim() || null,
          }))
          .filter((item) => Boolean(item.handle || item.url)),
        script_samples: benchmarks.script_samples
          .map((item) => ({
            ...item,
            path: item.path?.trim() || "",
            label: item.label?.trim() || null,
            note: item.note?.trim() || null,
          }))
          .filter((item) => Boolean(item.path)),
        notes: benchmarks.notes?.trim() || null,
      };
      const updated = await updateChannelProfile(normalizedCode, { benchmarks: payload });
      setBenchmarks(normalizeBenchmarks(updated.benchmarks));
      setBanner({ type: "success", text: "ベンチマークを保存しました。" });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBanner({ type: "error", text: "保存に失敗しました。" });
    } finally {
      setSaving(false);
    }
  }, [benchmarks, normalizedCode]);

  const handlePreview = useCallback(async (sample: BenchmarkScriptSampleSpec) => {
    const base = sample.base;
    const path = sample.path?.trim() || "";
    const label = sample.label?.trim() || path || "プレビュー";
    if (!path) {
      setPreview({
        base,
        path: "",
        label,
        loading: false,
        content: "",
        metrics: null,
        error: "path が空です。",
      });
      return;
    }
    setPreview({
      base,
      path,
      label,
      loading: true,
      content: "",
      metrics: null,
      error: null,
    });
    try {
      const response = await fetchResearchFile(base, path);
      const content = response.content ?? "";
      const metrics = content ? analyzeBenchmarkContent(content) : null;
      setPreview((current) =>
        current
          ? {
              ...current,
              loading: false,
              content,
              metrics,
              error: null,
            }
          : null
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPreview((current) => (current ? { ...current, loading: false, error: message } : null));
    }
  }, []);

  const headerSubtitle = useMemo(() => {
    if (!normalizedCode) {
      return "チャンネルを選択するとベンチマークを表示します";
    }
    const date = benchmarks.updated_at ? `最終更新 ${benchmarks.updated_at}` : "最終更新 —";
    return `${normalizedCode} / ${date}`;
  }, [benchmarks.updated_at, normalizedCode]);

  return (
    <section className="channel-benchmarks-panel">
      <header className="channel-benchmarks-panel__header">
        <div>
          <h2>ベンチマーク</h2>
          <p className="channel-benchmarks-panel__subtitle">{headerSubtitle}</p>
        </div>
        <div className="channel-benchmarks-panel__actions">
          <button
            type="button"
            className="channel-profile-button channel-profile-button--ghost"
            onClick={() => void handleReload()}
            disabled={!normalizedCode || loading}
          >
            再読み込み
          </button>
          <button type="button" className="channel-profile-button" onClick={() => void handleSave()} disabled={!normalizedCode || saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </header>

      {banner ? <div className={`channel-profile-banner channel-profile-banner--${banner.type}`}>{banner.text}</div> : null}
      {error ? <div className="channel-profile-banner channel-profile-banner--error">{error}</div> : null}

      {!normalizedCode ? (
        <p className="channel-benchmarks-panel__placeholder">チャンネルを選択してください。</p>
      ) : loading ? (
        <p className="channel-benchmarks-panel__placeholder">読み込み中…</p>
      ) : (
        <>
          {!isValid ? (
            <div className="channel-profile-banner channel-profile-banner--info">
              最低限の要件: 競合チャンネル1件＋台本サンプル1件。未設定の項目は監査で検出されます。
            </div>
          ) : null}

          <div className="channel-benchmarks-panel__grid">
            <section className="channel-benchmarks-panel__section">
              <div className="channel-benchmarks-panel__section-header">
                <h3>競合チャンネル</h3>
                <button
                  type="button"
                  className="channel-profile-button channel-profile-button--ghost"
                  onClick={() => setBenchmarks((prev) => ({ ...prev, channels: [...prev.channels, createEmptyBenchmarkChannel()] }))}
                >
                  ＋追加
                </button>
              </div>
              {benchmarks.channels.length === 0 ? (
                <p className="channel-benchmarks-panel__placeholder">未登録です。</p>
              ) : (
                <div className="channel-benchmarks-panel__list">
                  {benchmarks.channels.map((item, index) => (
                    <div
                      key={`bench-ch-${index}`}
                      className={`channel-benchmarks-panel__row${
                        !(item.handle?.trim() || item.url?.trim()) ? " is-invalid" : ""
                      }`}
                    >
                      <div className="channel-benchmarks-panel__row-title">
                        <span className="channel-benchmarks-panel__badge">#{index + 1}</span>
                        {item.handle?.trim() ? (
                          <code>{normalizeHandle(item.handle)}</code>
                        ) : (
                          <span className="muted">handle未設定</span>
                        )}
                        {item.name?.trim() ? (
                          <span className="channel-benchmarks-panel__row-title-name">{item.name.trim()}</span>
                        ) : null}
                        {item.url?.trim() ? (
                          <a
                            className="channel-benchmarks-panel__row-link"
                            href={item.url.trim()}
                            target="_blank"
                            rel="noreferrer"
                          >
                            開く
                          </a>
                        ) : null}
                      </div>
                      <label>
                        <span>handle</span>
                        <input
                          type="text"
                          value={item.handle ?? ""}
                          placeholder="@example"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.channels];
                              next[index] = { ...next[index], handle: event.target.value };
                              return { ...prev, channels: next };
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>name</span>
                        <input
                          type="text"
                          value={item.name ?? ""}
                          placeholder="チャンネル名（任意）"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.channels];
                              next[index] = { ...next[index], name: event.target.value };
                              return { ...prev, channels: next };
                            })
                          }
                        />
                      </label>
                      <label className="wide">
                        <span>url</span>
                        <input
                          type="url"
                          value={item.url ?? ""}
                          placeholder="https://www.youtube.com/@example"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.channels];
                              next[index] = { ...next[index], url: event.target.value };
                              return { ...prev, channels: next };
                            })
                          }
                        />
                      </label>
                      <label className="wide">
                        <span>note</span>
                        <textarea
                          rows={2}
                          value={item.note ?? ""}
                          placeholder="何を学ぶか（任意。複数行OK）"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.channels];
                              next[index] = { ...next[index], note: event.target.value };
                              return { ...prev, channels: next };
                            })
                          }
                        />
                      </label>
                      <div className="channel-benchmarks-panel__row-actions">
                        <button
                          type="button"
                          className="channel-profile-button channel-profile-button--danger"
                          onClick={() =>
                            setBenchmarks((prev) => ({
                              ...prev,
                              channels: prev.channels.filter((_, idx) => idx !== index),
                            }))
                          }
                          title="削除"
                        >
                          削除
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="channel-benchmarks-panel__section">
              <div className="channel-benchmarks-panel__section-header">
                <h3>台本サンプル</h3>
                <button
                  type="button"
                  className="channel-profile-button channel-profile-button--ghost"
                  onClick={() =>
                    setBenchmarks((prev) => ({
                      ...prev,
                      script_samples: [...prev.script_samples, createEmptyScriptSample()],
                    }))
                  }
                >
                  ＋追加
                </button>
              </div>
              {benchmarks.script_samples.length === 0 ? (
                <p className="channel-benchmarks-panel__placeholder">未登録です。</p>
              ) : (
                <div className="channel-benchmarks-panel__list">
                  {benchmarks.script_samples.map((item, index) => (
                    <div
                      key={`bench-sample-${index}`}
                      className={`channel-benchmarks-panel__row${!item.path?.trim() ? " is-invalid" : ""}`}
                    >
                      <div className="channel-benchmarks-panel__row-title">
                        <span className="channel-benchmarks-panel__badge">#{index + 1}</span>
                        <code>{item.base}</code>
                        <span className="channel-benchmarks-panel__row-title-name">
                          {(item.label?.trim() || item.path?.trim() || "（未設定）").slice(0, 96)}
                        </span>
                      </div>
                      <label>
                        <span>base</span>
                        <select
                          value={item.base}
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.script_samples];
                              next[index] = { ...next[index], base: event.target.value as "research" | "scripts" };
                              return { ...prev, script_samples: next };
                            })
                          }
                        >
                          <option value="research">research</option>
                          <option value="scripts">scripts</option>
                        </select>
                      </label>
                      <label>
                        <span>label</span>
                        <input
                          type="text"
                          value={item.label ?? ""}
                          placeholder="表示名（任意）"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.script_samples];
                              next[index] = { ...next[index], label: event.target.value };
                              return { ...prev, script_samples: next };
                            })
                          }
                        />
                      </label>
                      <label className="wide">
                        <span>path</span>
                        <input
                          type="text"
                          value={item.path ?? ""}
                          placeholder="例: benchmarks_ch07_ch08.md / ブッダ系/バズった台本１"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.script_samples];
                              next[index] = { ...next[index], path: event.target.value };
                              return { ...prev, script_samples: next };
                            })
                          }
                        />
                      </label>
                      <label className="wide">
                        <span>note</span>
                        <textarea
                          rows={2}
                          value={item.note ?? ""}
                          placeholder="使いどころ（任意。複数行OK）"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.script_samples];
                              next[index] = { ...next[index], note: event.target.value };
                              return { ...prev, script_samples: next };
                            })
                          }
                        />
                      </label>
                      <div className="channel-benchmarks-panel__row-actions">
                        <button
                          type="button"
                          className="channel-profile-button channel-profile-button--ghost"
                          onClick={() => void handlePreview(item)}
                          disabled={!item.path?.trim()}
                        >
                          プレビュー
                        </button>
                        <button
                          type="button"
                          className="channel-profile-button channel-profile-button--danger"
                          onClick={() =>
                            setBenchmarks((prev) => ({
                              ...prev,
                              script_samples: prev.script_samples.filter((_, idx) => idx !== index),
                            }))
                          }
                        >
                          削除
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="channel-benchmarks-panel__notes">
            <h3>総評</h3>
            <textarea
              rows={4}
              value={benchmarks.notes ?? ""}
              placeholder="勝ちパターン / NG / 尺 / サムネ構図など"
              onChange={(event) => setBenchmarks((prev) => ({ ...prev, notes: event.target.value }))}
            />
          </section>

          {preview ? (
            <div className="modal-backdrop" onClick={() => setPreview(null)}>
              <div className="modal" onClick={(event) => event.stopPropagation()}>
                <header className="modal__header">
                  <h3>{preview.label || "プレビュー"}</h3>
                  <button type="button" className="channel-profile-button channel-profile-button--ghost" onClick={() => setPreview(null)}>
                    閉じる
                  </button>
                </header>
                <div className="modal__body" style={{ maxHeight: "70vh", overflow: "auto" }}>
                  <p className="muted" style={{ marginTop: 0 }}>
                    {preview.base} / <code>{preview.path || "—"}</code>
                  </p>
                  {preview.loading ? <p className="muted">読み込み中…</p> : null}
                  {preview.error ? <div className="main-alert main-alert--error">{preview.error}</div> : null}
                  {preview.metrics ? (
                    <dl className="portal-kv" style={{ marginTop: 0, marginBottom: 12 }}>
                      <dt>文字数</dt>
                      <dd>
                        {preview.metrics.nonWhitespaceChars.toLocaleString("ja-JP")}字（推定{" "}
                        {preview.metrics.estimatedMinutes.toFixed(1)}分）
                      </dd>

                      <dt>行</dt>
                      <dd>
                        {preview.metrics.lines.toLocaleString("ja-JP")}（非空 {preview.metrics.nonEmptyLines.toLocaleString("ja-JP")}）
                      </dd>

                      <dt>見出し / 区切り</dt>
                      <dd>
                        <span className={`mono ${preview.metrics.headings > 0 ? "is-warn" : "is-ok"}`}>
                          {preview.metrics.headings.toLocaleString("ja-JP")}
                        </span>{" "}
                        /{" "}
                        <span className={`mono ${preview.metrics.dividers > 0 ? "is-warn" : "is-ok"}`}>
                          {preview.metrics.dividers.toLocaleString("ja-JP")}
                        </span>
                      </dd>

                      <dt>先頭</dt>
                      <dd>{preview.metrics.firstNonEmptyLine ? preview.metrics.firstNonEmptyLine.slice(0, 64) : "—"}</dd>
                    </dl>
                  ) : null}
                  {preview.content ? (
                    <pre className="channel-benchmarks-panel__preview-content">{preview.content}</pre>
                  ) : !preview.loading && !preview.error ? (
                    <p className="muted">（内容が空です）</p>
                  ) : null}
                </div>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
