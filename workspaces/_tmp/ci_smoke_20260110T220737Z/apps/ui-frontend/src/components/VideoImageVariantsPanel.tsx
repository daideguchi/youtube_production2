import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchVideoImageModels,
  fetchVideoImageStylePresets,
  fetchVideoImageVariants,
  resolveApiUrl,
} from "../api/client";
import type {
  VideoGenerationOptions,
  VideoImageModelInfo,
  VideoImageStylePreset,
  VideoImageVariantsResponse,
  VideoJobCreatePayload,
  VideoProductionChannelPreset,
  VideoProjectDetail,
} from "../api/types";

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

export function VideoImageVariantsPanel({
  project,
  channelPreset,
  generationOptions,
  onQuickJob,
}: {
  project: VideoProjectDetail;
  channelPreset: VideoProductionChannelPreset | null;
  generationOptions: VideoGenerationOptions;
  onQuickJob: (action: VideoJobCreatePayload["action"], options?: VideoJobCreatePayload["options"]) => Promise<void>;
}) {
  const projectId = project.summary?.id ?? "";
  const channelId = resolveChannelId(project.summary ?? undefined) ?? channelPreset?.channelId ?? "";
  const [models, setModels] = useState<VideoImageModelInfo[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [stylePresets, setStylePresets] = useState<VideoImageStylePreset[]>([]);
  const [stylesError, setStylesError] = useState<string | null>(null);
  const [variants, setVariants] = useState<VideoImageVariantsResponse | null>(null);
  const [variantsLoading, setVariantsLoading] = useState(false);
  const [variantsError, setVariantsError] = useState<string | null>(null);

  const [selectedStyleKeys, setSelectedStyleKeys] = useState<string[]>([]);
  const [customStyleText, setCustomStyleText] = useState<string>("");
  const [modelKeyOverride, setModelKeyOverride] = useState<string>("");
  const [maxCues, setMaxCues] = useState<number>(0);
  const [timeoutSec, setTimeoutSec] = useState<number>(300);
  const [maxRetries, setMaxRetries] = useState<number>(6);
  const [retryUntilSuccess, setRetryUntilSuccess] = useState<boolean>(true);

  const customStyles = useMemo(() => {
    return customStyleText
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => Boolean(line));
  }, [customStyleText]);

  const resolvedDefaultModelKey = useMemo(() => {
    const presetKey = channelPreset?.imageGeneration?.modelKey ?? null;
    if (presetKey) return presetKey;
    return "fireworks_flux_1_schnell_fp8";
  }, [channelPreset?.imageGeneration?.modelKey]);

  const effectiveRunStyle = useMemo(() => {
    const override = (generationOptions.style ?? "").trim();
    if (override) return override;
    return (channelPreset?.style ?? "").trim();
  }, [channelPreset?.style, generationOptions.style]);

  const examplePrompt = useMemo(() => {
    const cue0 = (project.cues ?? [])[0] as { prompt?: string } | undefined;
    return (cue0?.prompt ?? "").trim();
  }, [project.cues]);

  useEffect(() => {
    fetchVideoImageModels()
      .then((items) => {
        setModels(items);
        setModelsError(null);
      })
      .catch((error) => setModelsError(error instanceof Error ? error.message : String(error)));
    fetchVideoImageStylePresets()
      .then((items) => {
        setStylePresets(items);
        setStylesError(null);
      })
      .catch((error) => setStylesError(error instanceof Error ? error.message : String(error)));
  }, []);

  const reloadVariants = useCallback(async () => {
    if (!projectId) {
      setVariants(null);
      setVariantsError(null);
      setVariantsLoading(false);
      return;
    }
    setVariantsLoading(true);
    setVariantsError(null);
    try {
      const data = await fetchVideoImageVariants(projectId);
      setVariants(data);
    } catch (error) {
      setVariants(null);
      setVariantsError(error instanceof Error ? error.message : String(error));
    } finally {
      setVariantsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reloadVariants();
    setSelectedStyleKeys([]);
    setCustomStyleText("");
    setModelKeyOverride("");
  }, [projectId, reloadVariants]);

  const toggleStyleKey = useCallback((key: string) => {
    setSelectedStyleKeys((current) => {
      if (current.includes(key)) {
        return current.filter((item) => item !== key);
      }
      return [...current, key];
    });
  }, []);

  const canGenerate = selectedStyleKeys.length > 0 || customStyles.length > 0;

  const handleGenerate = async () => {
    if (!canGenerate) return;
    const options: Record<string, unknown> = {
      channel: channelId || undefined,
      style_presets: selectedStyleKeys,
      custom_styles: customStyles,
      retry_until_success: retryUntilSuccess,
      max_retries: maxRetries,
      timeout_sec: timeoutSec,
    };
    const mk = modelKeyOverride.trim() || resolvedDefaultModelKey || "";
    if (mk) {
      options.model_key = mk;
    }
    if (maxCues > 0) {
      options.max = maxCues;
    }
    await onQuickJob("generate_image_variants", options);
  };

  return (
    <div style={{ display: "grid", gap: 12, marginTop: 10 }}>
      <div className="vp-draft-meta" style={{ display: "grid", gap: 6 }}>
        <div>
          <strong>現在の設定</strong>
        </div>
        <div>
          channel: <code>{channelId || "—"}</code>
        </div>
        <div>
          model(default): <code>{resolvedDefaultModelKey ?? "(tier default)"}</code>
        </div>
        <div>
          style(default): <code>{effectiveRunStyle || "(none)"}</code>
        </div>
        {channelPreset?.promptTemplate ? (
          <div>
            prompt_template: <code>{channelPreset.promptTemplate}</code>
          </div>
        ) : null}
        {examplePrompt ? (
          <details>
            <summary>example prompt (cue#1)</summary>
            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", marginTop: 8 }}>{examplePrompt}</pre>
          </details>
        ) : null}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <strong>生成（複数画風）</strong>
          <button type="button" onClick={() => void reloadVariants()} disabled={variantsLoading}>
            {variantsLoading ? "読込中…" : "バリアント再読込"}
          </button>
        </div>

        {modelsError ? <p className="error">{modelsError}</p> : null}
        {stylesError ? <p className="error">{stylesError}</p> : null}
        {variantsError ? <p className="error">{variantsError}</p> : null}

        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
          model override（空=default）
          <select value={modelKeyOverride} onChange={(event) => setModelKeyOverride(event.target.value)}>
            <option value="">(default: {resolvedDefaultModelKey ?? "tier default"})</option>
            {models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.key} ({m.provider})
              </option>
            ))}
          </select>
        </label>

        <div style={{ display: "grid", gap: 6 }}>
          <div className="vp-draft-meta">style presets（{selectedStyleKeys.length} selected）</div>
          <div style={{ display: "grid", gap: 6, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {stylePresets.map((preset) => (
              <label
                key={preset.key}
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "flex-start",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 10,
                  background: "#fff",
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedStyleKeys.includes(preset.key)}
                  onChange={() => toggleStyleKey(preset.key)}
                />
                <span style={{ display: "grid", gap: 4, minWidth: 0 }}>
                  <span style={{ fontWeight: 700 }}>
                    {preset.label} <span style={{ color: "#64748b" }}>({preset.key})</span>
                  </span>
                  <span style={{ color: "#475569", fontSize: 12, lineHeight: 1.3, wordBreak: "break-word" }}>
                    {preset.prompt}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>

        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
          custom styles（1行=1画風）
          <textarea
            value={customStyleText}
            onChange={(event) => setCustomStyleText(event.target.value)}
            rows={4}
            placeholder="例: Stained glass art, bold leading lines..."
          />
        </label>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
            max cues（0=all）
            <input
              type="number"
              min={0}
              value={maxCues}
              onChange={(event) => setMaxCues(Number(event.target.value))}
              style={{ width: 120 }}
            />
          </label>
          <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
            timeout_sec
            <input
              type="number"
              min={10}
              value={timeoutSec}
              onChange={(event) => setTimeoutSec(Number(event.target.value))}
              style={{ width: 120 }}
            />
          </label>
          <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
            max_retries
            <input
              type="number"
              min={1}
              value={maxRetries}
              onChange={(event) => setMaxRetries(Number(event.target.value))}
              style={{ width: 120 }}
            />
          </label>
          <label className="vp-draft-meta" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={retryUntilSuccess}
              onChange={(event) => setRetryUntilSuccess(event.target.checked)}
            />
            retry_until_success（placeholder禁止）
          </label>
        </div>

        <button type="button" onClick={() => void handleGenerate()} disabled={!canGenerate}>
          この画風でバリアント生成（ジョブ）
        </button>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <strong>既存バリアント</strong>
        {!variants || variants.variants.length === 0 ? (
          <p className="muted">まだありません。上の「生成（複数画風）」から作成できます。</p>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {variants.variants.map((variant) => (
              <details
                key={variant.id}
                style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 10, background: "#fff" }}
              >
                <summary style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
                  <strong>{variant.style_key ?? "custom"}</strong>
                  <span style={{ color: "#64748b" }}>{variant.created_at ? formatJobTimestamp(variant.created_at) : ""}</span>
                  <span style={{ color: "#64748b" }}>
                    images: {variant.image_count} / model: {variant.model_key ?? "(default)"}
                  </span>
                </summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  <div className="vp-draft-meta" style={{ wordBreak: "break-word" }}>
                    <div>
                      <strong>style</strong>: {variant.style}
                    </div>
                    {variant.prompt_template ? (
                      <div>
                        <strong>template</strong>: <code>{variant.prompt_template}</code>
                      </div>
                    ) : null}
                    <div>
                      <strong>dir</strong>: <code>{variant.images_dir}</code>
                    </div>
                  </div>
                  {variant.sample_images && variant.sample_images.length ? (
                    <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
                      {variant.sample_images.map((img) => (
                        <a key={img.path} href={resolveApiUrl(img.url)} target="_blank" rel="noreferrer">
                          <img
                            src={resolveApiUrl(img.url)}
                            alt={img.path}
                            style={{ width: "100%", borderRadius: 10, display: "block" }}
                          />
                        </a>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">sample 画像なし（生成中 or 読み込み直後）</p>
                  )}
                </div>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
