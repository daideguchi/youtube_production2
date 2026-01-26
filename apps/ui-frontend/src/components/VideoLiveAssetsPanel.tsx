import { useEffect, useMemo, useRef, useState } from "react";

import { resolveApiUrl } from "../api/client";
import type { VideoJobRecord } from "../api/types";

type LiveAsset = {
  path: string;
  url: string;
  kind: string;
  variant_id?: string | null;
  size_bytes?: number;
  modified_at?: string;
};

type AssetSnapshotEvent = { project_id: string; assets: LiveAsset[] };
type AssetUpsertEvent = { project_id: string; asset: LiveAsset };
type AssetRemoveEvent = { project_id: string; path: string };

type JobLogSnapshotEvent = { job_id: string; lines: string[] };
type JobLogLinesEvent = { job_id: string; lines: string[] };
type JobLogStatusEvent = { job_id: string; status: string };
type JobLogDoneEvent = { job_id: string; status: string; exit_code?: number | null; error?: string | null };

function safeJsonParse<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function assetBasename(path: string): string {
  const normalized = String(path ?? "").replace(/\\/g, "/");
  return normalized.split("/").pop() ?? normalized;
}

function parseDigits(value: string): number | null {
  const m = String(value ?? "").match(/(\d{1,6})/);
  if (!m) return null;
  const n = Number.parseInt(m[1], 10);
  return Number.isFinite(n) ? n : null;
}

function cacheBust(url: string, token?: string | null): string {
  const base = String(url ?? "");
  if (!token) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}t=${encodeURIComponent(token)}`;
}

function statusPill(status: "closed" | "connecting" | "open" | "error"): { label: string; bg: string; fg: string } {
  if (status === "open") return { label: "LIVE", bg: "#10b981", fg: "#052e2b" };
  if (status === "connecting") return { label: "接続中…", bg: "#e0f2fe", fg: "#0c4a6e" };
  if (status === "error") return { label: "エラー", bg: "#fee2e2", fg: "#7f1d1d" };
  return { label: "停止", bg: "#e2e8f0", fg: "#0f172a" };
}

export function VideoLiveAssetsPanel({
  projectId,
  jobs,
  requiredImages,
}: {
  projectId: string;
  jobs: VideoJobRecord[];
  requiredImages?: number;
}) {
  const [assetStreamEnabled, setAssetStreamEnabled] = useState(true);
  const [assetStreamStatus, setAssetStreamStatus] = useState<"closed" | "connecting" | "open" | "error">("closed");
  const [assetsByPath, setAssetsByPath] = useState<Record<string, LiveAsset>>({});
  const [assetQuery, setAssetQuery] = useState("");
  const [assetKindFilter, setAssetKindFilter] = useState<"all" | "final" | "variant">("final");
  const [maxAssets, setMaxAssets] = useState(240);

  const [logStreamEnabled, setLogStreamEnabled] = useState(true);
  const [logStreamStatus, setLogStreamStatus] = useState<"closed" | "connecting" | "open" | "error">("closed");
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const logBoxRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const activeJob = useMemo(() => {
    const isInteresting = (job: VideoJobRecord) =>
      job.action === "regenerate_images" || job.action === "generate_image_variants";
    const running = jobs.find((job) => job.status === "running" && isInteresting(job));
    if (running) return running;
    const queued = jobs.find((job) => job.status === "queued" && isInteresting(job));
    return queued ?? null;
  }, [jobs]);

  useEffect(() => {
    setAssetsByPath({});
    setLogLines([]);
    setJobStatus(null);
  }, [projectId]);

  useEffect(() => {
    if (!autoScroll) return;
    const el = logBoxRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [autoScroll, logLines]);

  useEffect(() => {
    if (!projectId || !assetStreamEnabled) {
      setAssetStreamStatus("closed");
      return;
    }
    setAssetStreamStatus("connecting");

    const url = resolveApiUrl(
      `/api/video-production/projects/${encodeURIComponent(projectId)}/assets/stream?include_existing=1`
    );
    const source = new EventSource(url);

    const handleReady = () => setAssetStreamStatus("open");
    const handleSnapshot = (event: MessageEvent) => {
      const payload = safeJsonParse<AssetSnapshotEvent>(event.data);
      if (!payload?.assets) return;
      setAssetsByPath(() => {
        const next: Record<string, LiveAsset> = {};
        payload.assets.forEach((asset) => {
          if (!asset?.path) return;
          next[asset.path] = asset;
        });
        return next;
      });
    };
    const handleUpsert = (event: MessageEvent) => {
      const payload = safeJsonParse<AssetUpsertEvent>(event.data);
      if (!payload?.asset?.path) return;
      setAssetsByPath((prev) => ({
        ...prev,
        [payload.asset.path]: payload.asset,
      }));
    };
    const handleRemove = (event: MessageEvent) => {
      const payload = safeJsonParse<AssetRemoveEvent>(event.data);
      if (!payload?.path) return;
      setAssetsByPath((prev) => {
        const next = { ...prev };
        delete next[payload.path];
        return next;
      });
    };
    const handleError = () => setAssetStreamStatus("error");

    source.addEventListener("ready", handleReady);
    source.addEventListener("snapshot", handleSnapshot);
    source.addEventListener("upsert", handleUpsert);
    source.addEventListener("remove", handleRemove);
    source.onerror = handleError;

    return () => {
      source.close();
      setAssetStreamStatus("closed");
    };
  }, [assetStreamEnabled, projectId]);

  useEffect(() => {
    setLogLines([]);
    setJobStatus(null);
  }, [activeJob?.id]);

  useEffect(() => {
    const jobId = activeJob?.id ?? "";
    if (!jobId || !logStreamEnabled) {
      setLogStreamStatus("closed");
      return;
    }
    setLogStreamStatus("connecting");

    const url = resolveApiUrl(`/api/video-production/jobs/${encodeURIComponent(jobId)}/log/stream?tail=200`);
    const source = new EventSource(url);

    const handleReady = () => setLogStreamStatus("open");
    const handleSnapshot = (event: MessageEvent) => {
      const payload = safeJsonParse<JobLogSnapshotEvent>(event.data);
      if (!payload?.lines) return;
      setLogLines(payload.lines);
    };
    const handleLines = (event: MessageEvent) => {
      const payload = safeJsonParse<JobLogLinesEvent>(event.data);
      if (!payload?.lines?.length) return;
      setLogLines((prev) => {
        const merged = [...prev, ...payload.lines];
        return merged.length > 600 ? merged.slice(-600) : merged;
      });
    };
    const handleStatus = (event: MessageEvent) => {
      const payload = safeJsonParse<JobLogStatusEvent>(event.data);
      if (!payload?.status) return;
      setJobStatus(payload.status);
    };
    const handleDone = (event: MessageEvent) => {
      const payload = safeJsonParse<JobLogDoneEvent>(event.data);
      if (!payload) return;
      setJobStatus(payload.status ?? null);
      source.close();
      setLogStreamStatus("closed");
    };
    const handleError = () => setLogStreamStatus("error");

    source.addEventListener("ready", handleReady);
    source.addEventListener("snapshot", handleSnapshot);
    source.addEventListener("lines", handleLines);
    source.addEventListener("status", handleStatus);
    source.addEventListener("done", handleDone);
    source.onerror = handleError;

    return () => {
      source.close();
      setLogStreamStatus("closed");
    };
  }, [activeJob?.id, logStreamEnabled]);

  const allAssets = useMemo(() => Object.values(assetsByPath), [assetsByPath]);
  const totals = useMemo(() => {
    const finalCount = allAssets.filter((a) => a.kind === "final").length;
    const variantCount = allAssets.filter((a) => a.kind === "variant").length;
    return { finalCount, variantCount, total: allAssets.length };
  }, [allAssets]);

  const filteredAssets = useMemo(() => {
    const q = assetQuery.trim().toLowerCase();
    const filter = assetKindFilter;
    const items = allAssets.filter((asset) => {
      if (!asset.path) return false;
      if (filter !== "all" && asset.kind !== filter) return false;
      if (!q) return true;
      return asset.path.toLowerCase().includes(q) || assetBasename(asset.path).toLowerCase().includes(q);
    });
    const sortKey = (asset: LiveAsset) => {
      const base = assetBasename(asset.path);
      const idx = parseDigits(base) ?? 0;
      if (asset.kind === "final") return idx;
      if (asset.kind === "variant") return 1_000_000_000 + idx;
      return 2_000_000_000 + idx;
    };
    items.sort((a, b) => sortKey(a) - sortKey(b));
    return items.slice(0, Number.isFinite(maxAssets) && maxAssets > 0 ? maxAssets : 240);
  }, [allAssets, assetKindFilter, assetQuery, maxAssets]);

  const progressPct = useMemo(() => {
    const denom = Number(requiredImages ?? 0);
    if (!Number.isFinite(denom) || denom <= 0) return null;
    const pct = Math.round((totals.finalCount / denom) * 100);
    return Math.max(0, Math.min(100, pct));
  }, [requiredImages, totals.finalCount]);

  const assetPill = statusPill(assetStreamStatus);
  const logPill = statusPill(logStreamStatus);

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong>Live（生成中プレビュー / 倉庫）</strong>
            <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 999, background: assetPill.bg, color: assetPill.fg, fontWeight: 800 }}>
              {assetPill.label}
            </span>
            {activeJob ? (
              <span style={{ fontSize: 12, color: "#475569" }}>
                job: <code>{activeJob.id.slice(0, 8)}</code> ({activeJob.action})
              </span>
            ) : (
              <span style={{ fontSize: 12, color: "#64748b" }}>（画像ジョブなし）</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "#475569" }}>
            final {totals.finalCount}
            {typeof requiredImages === "number" && requiredImages > 0 ? ` / ${requiredImages}` : ""} ・ variants {totals.variantCount} ・ total{" "}
            {totals.total}
          </div>
          {progressPct !== null ? (
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12, color: "#64748b" }}>
                <span>画像進捗</span>
                <span>{progressPct}%</span>
              </div>
              <div style={{ height: 8, borderRadius: 999, background: "#e2e8f0", overflow: "hidden" }}>
                <div style={{ width: `${progressPct}%`, height: "100%", background: progressPct >= 100 ? "#10b981" : "#0ea5e9" }} />
              </div>
            </div>
          ) : null}
        </div>

        <div style={{ display: "grid", gap: 8, justifyItems: "end" }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: "#475569" }}>
            <input type="checkbox" checked={assetStreamEnabled} onChange={(e) => setAssetStreamEnabled(e.target.checked)} />
            assets stream
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: "#475569" }}>
            <input type="checkbox" checked={logStreamEnabled} onChange={(e) => setLogStreamEnabled(e.target.checked)} />
            log stream
          </label>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
          filter
          <select value={assetKindFilter} onChange={(event) => setAssetKindFilter(event.target.value as typeof assetKindFilter)}>
            <option value="final">final</option>
            <option value="variant">variant</option>
            <option value="all">all</option>
          </select>
        </label>
        <label className="vp-draft-meta" style={{ display: "grid", gap: 4, minWidth: 220 }}>
          search
          <input value={assetQuery} onChange={(event) => setAssetQuery(event.target.value)} placeholder="path / filename" />
        </label>
        <label className="vp-draft-meta" style={{ display: "grid", gap: 4 }}>
          max
          <input
            type="number"
            min={40}
            max={2000}
            value={maxAssets}
            onChange={(event) => setMaxAssets(Number(event.target.value))}
            style={{ width: 110 }}
          />
        </label>
      </div>

      {filteredAssets.length === 0 ? (
        <div className="vp-empty" style={{ margin: 0 }}>
          まだ画像がありません（生成中なら少し待ってください）。
        </div>
      ) : (
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          {filteredAssets.map((asset) => {
            const href = asset.url ? resolveApiUrl(asset.url) : "";
            const src = asset.url ? resolveApiUrl(cacheBust(asset.url, asset.modified_at)) : "";
            const label = assetBasename(asset.path);
            const sub = asset.kind === "variant" && asset.variant_id ? `variant: ${asset.variant_id}` : asset.kind;
            return (
              <a
                key={asset.path}
                href={href || undefined}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "grid",
                  gap: 8,
                  border: "1px solid #e2e8f0",
                  borderRadius: 12,
                  padding: 10,
                  background: "#fff",
                  color: "inherit",
                  textDecoration: "none",
                }}
                title={asset.path}
              >
                <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: 10, overflow: "hidden", background: "#0b1220" }}>
                  {src ? (
                    <img src={src} alt={label} loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
                  ) : (
                    <div style={{ color: "#e2e8f0", fontSize: 12, padding: 10 }}>画像なし</div>
                  )}
                </div>
                <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 800, wordBreak: "break-word" }}>{label}</div>
                  <div style={{ fontSize: 11, color: "#64748b", wordBreak: "break-word" }}>{sub}</div>
                  <div style={{ fontSize: 11, color: "#64748b", wordBreak: "break-word" }}>{asset.path}</div>
                </div>
              </a>
            );
          })}
        </div>
      )}

      <details open={Boolean(activeJob)} style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 10, background: "#fff" }}>
        <summary style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <strong>ジョブログ</strong>
          <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 999, background: logPill.bg, color: logPill.fg, fontWeight: 800 }}>
            {logPill.label}
          </span>
          {jobStatus ? (
            <span style={{ fontSize: 12, color: "#475569" }}>
              status: <code>{jobStatus}</code>
            </span>
          ) : null}
          {!activeJob ? <span style={{ fontSize: 12, color: "#64748b" }}>（画像ジョブなし）</span> : null}
        </summary>

        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: "#475569" }}>
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
            auto-scroll
          </label>
          <div
            ref={logBoxRef}
            style={{
              maxHeight: 240,
              overflow: "auto",
              borderRadius: 10,
              border: "1px solid #e2e8f0",
              padding: 10,
              background: "#0b1220",
              color: "#e2e8f0",
              fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {logLines.length ? logLines.join("\n") : "（ログ待機中…）"}
          </div>
        </div>
      </details>
    </div>
  );
}
