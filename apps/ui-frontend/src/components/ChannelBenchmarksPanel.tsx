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
    if (!path) {
      setPreview({
        base,
        path: "",
        label: sample.label?.trim() || path || "プレビュー",
        loading: false,
        content: "",
        error: "path が空です。",
      });
      return;
    }
    setPreview({
      base,
      path,
      label: sample.label?.trim() || path,
      loading: true,
      content: "",
      error: null,
    });
    try {
      const response = await fetchResearchFile(base, path);
      setPreview((current) =>
        current
          ? {
              ...current,
              loading: false,
              content: response.content,
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
                    <div key={`bench-ch-${index}`} className="channel-benchmarks-panel__row">
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
                        <input
                          type="text"
                          value={item.note ?? ""}
                          placeholder="何を学ぶか（任意）"
                          onChange={(event) =>
                            setBenchmarks((prev) => {
                              const next = [...prev.channels];
                              next[index] = { ...next[index], note: event.target.value };
                              return { ...prev, channels: next };
                            })
                          }
                        />
                      </label>
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
                    <div key={`bench-sample-${index}`} className="channel-benchmarks-panel__row">
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
                        <span>note</span>
                        <input
                          type="text"
                          value={item.note ?? ""}
                          placeholder="使いどころ（任意）"
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
            <section className="channel-benchmarks-panel__preview">
              <div className="channel-benchmarks-panel__preview-header">
                <div>
                  <strong>プレビュー</strong>
                  <div className="channel-benchmarks-panel__preview-path">
                    {preview.base} / <code>{preview.path || "—"}</code>
                  </div>
                </div>
                <button type="button" className="channel-profile-button channel-profile-button--ghost" onClick={() => setPreview(null)}>
                  閉じる
                </button>
              </div>
              {preview.loading ? (
                <p className="channel-benchmarks-panel__placeholder">読み込み中…</p>
              ) : preview.error ? (
                <p className="channel-benchmarks-panel__error">{preview.error}</p>
              ) : (
                <textarea readOnly rows={10} value={preview.content} />
              )}
            </section>
          ) : null}
        </>
      )}
    </section>
  );
}
