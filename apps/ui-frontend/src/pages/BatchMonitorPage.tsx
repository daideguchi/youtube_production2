import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiUrl } from "../api/baseUrl";
import { safeLocalStorage } from "../utils/safeStorage";

type BatchDescriptor = {
  id: string;
  pid_path: string;
  log_path?: string | null;
  mtime?: number | null;
};

type BatchMonitorBatchesResponse = {
  now?: string;
  repo_root?: string;
  batches?: BatchDescriptor[];
};

type BatchProgress = {
  total?: number;
  done?: number;
  pending?: number;
  current_episode?: string;
};

type CurrentRunStatus = {
  run_dir?: string;
  exists?: boolean;
  stage?: string;
  cues_total?: number | null;
  images_count?: number | null;
  capcut_done?: boolean;
  capcut_draft?: string;
  srt2images_log_tail?: string[];
  srt2images_log_mtime?: number | null;
};

type BatchMonitorStatusResponse = {
  now?: string;
  batch?: {
    id?: string;
    pid?: number;
    running?: boolean;
    elapsed_sec?: number | null;
    pid_path?: string;
    log_path?: string | null;
    log_mtime?: number | null;
    stalled?: boolean;
  };
  current?: {
    kind?: string | null;
    run_id?: string | null;
    run?: CurrentRunStatus | null;
  };
  channels?: string[];
  progress?: Record<string, BatchProgress>;
  log_tail?: string[];
  batches?: BatchDescriptor[];
  error?: string;
};

function fmtSec(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return "—";
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = Math.floor(n % 60);
  return `${h}h${String(m).padStart(2, "0")}m${String(s).padStart(2, "0")}s`;
}

function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ja-JP");
}

function pct(done: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}

function badge(text: string, tone: "ok" | "warn" | "bad" | "muted") {
  const colors: Record<string, { bg: string; fg: string; border: string }> = {
    ok: { bg: "#e8fff0", fg: "#0a7a2a", border: "#b6f3c9" },
    warn: { bg: "#fff8e6", fg: "#8a5a00", border: "#ffe1a6" },
    bad: { bg: "#fff0f0", fg: "#b11212", border: "#ffd0d0" },
    muted: { bg: "#f3f4f6", fg: "#374151", border: "#e5e7eb" },
  };
  const c = colors[tone];
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        background: c.bg,
        color: c.fg,
        border: `1px solid ${c.border}`,
        fontWeight: 600,
      }}
    >
      {text}
    </span>
  );
}

export function BatchMonitorPage() {
  const [batches, setBatches] = useState<BatchDescriptor[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<string>(() => safeLocalStorage.getItem("ui.batchMonitor.batchId") || "");
  const [intervalSec, setIntervalSec] = useState<number>(() => {
    const raw = safeLocalStorage.getItem("ui.batchMonitor.intervalSec");
    const n = raw ? Number(raw) : 5;
    return Number.isFinite(n) ? Math.max(0, Math.min(60, n)) : 5;
  });
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<BatchMonitorStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const timerRef = useRef<number | null>(null);

  const fetchBatches = useCallback(async () => {
    const res = await fetch(apiUrl("/api/ops/batch-monitor/batches"), { method: "GET", cache: "no-store" });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || "batches の取得に失敗しました");
    }
    const data = (await res.json()) as BatchMonitorBatchesResponse;
    const list = Array.isArray(data.batches) ? data.batches : [];
    setBatches(list);
    if (!selectedBatch && list.length) {
      setSelectedBatch(list[0].id);
    }
  }, [selectedBatch]);

  const fetchStatus = useCallback(
    async (batchId?: string) => {
      const id = (batchId ?? selectedBatch ?? "").trim();
      const qs = id ? `?batch_id=${encodeURIComponent(id)}` : "";
      const res = await fetch(apiUrl(`/api/ops/batch-monitor/status${qs}`), { method: "GET", cache: "no-store" });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || "status の取得に失敗しました");
      }
      const data = (await res.json()) as BatchMonitorStatusResponse;
      setStatus(data);
    },
    [selectedBatch]
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await fetchBatches();
      await fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchBatches, fetchStatus]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    safeLocalStorage.setItem("ui.batchMonitor.intervalSec", String(intervalSec));
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (intervalSec > 0) {
      timerRef.current = window.setInterval(() => {
        void fetchStatus();
      }, intervalSec * 1000);
    }
    return () => {
      if (timerRef.current) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [intervalSec, fetchStatus]);

  useEffect(() => {
    safeLocalStorage.setItem("ui.batchMonitor.batchId", selectedBatch);
    void fetchStatus(selectedBatch);
  }, [selectedBatch, fetchStatus]);

  const batchState = status?.batch;
  const current = status?.current;
  const currentRun = current?.run;
  const progress = status?.progress;
  const sortedChannels = useMemo(() => {
    const keys = Object.keys(progress ?? {});
    return keys.sort((a, b) => a.localeCompare(b));
  }, [progress]);

  const currentStageLabel = useMemo(() => {
    if (!currentRun?.exists) return "—";
    const stage = String(currentRun.stage || "").trim() || "—";
    if (stage === "generating_images") return "画像生成中";
    if (stage === "capcut_inserting") return "CapCut挿入中";
    if (stage === "done") return "完了";
    if (stage === "initializing") return "初期化中";
    return stage;
  }, [currentRun?.exists, currentRun?.stage]);

  return (
    <div className="page" style={{ padding: 16, display: "grid", gap: 12 }}>
      <h1>バッチ監視</h1>

      {error ? (
        <div className="error" style={{ color: "red" }}>
          {error}
        </div>
      ) : null}

      <div className="card" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: "#666", fontSize: 12 }}>Batch</span>
          <select
            value={selectedBatch}
            onChange={(e) => setSelectedBatch(e.target.value)}
            style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #ccc" }}
          >
            {batches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.id}
              </option>
            ))}
          </select>
        </div>

        <button onClick={refresh} disabled={loading} style={{ padding: "6px 10px" }}>
          更新
        </button>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: "#666", fontSize: 12 }}>自動更新</span>
          <select
            value={intervalSec}
            onChange={(e) => setIntervalSec(Number(e.target.value))}
            style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #ccc" }}
          >
            <option value={2}>2s</option>
            <option value={5}>5s</option>
            <option value={10}>10s</option>
            <option value={30}>30s</option>
            <option value={0}>OFF</option>
          </select>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ color: "#666", fontSize: 12 }}>now</span>
          <span className="mono" style={{ fontSize: 12 }}>
            {status?.now || "—"}
          </span>
        </div>
      </div>

      <div className="card" style={{ display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {batchState?.running ? badge("RUNNING", "ok") : badge("STOPPED", "bad")}
          {batchState?.stalled ? badge("STALED(>90s)", "warn") : null}
          <span style={{ color: "#666", fontSize: 12 }}>pid</span>
          <span className="mono" style={{ fontSize: 12 }}>
            {batchState?.pid ?? "—"}
          </span>
          <span style={{ color: "#666", fontSize: 12 }}>elapsed</span>
          <span className="mono" style={{ fontSize: 12 }}>
            {fmtSec(batchState?.elapsed_sec ?? null)}
          </span>
          <span style={{ color: "#666", fontSize: 12 }}>log_mtime</span>
          <span className="mono" style={{ fontSize: 12 }}>
            {fmtDateTime(batchState?.log_mtime ?? null)}
          </span>
        </div>

        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ color: "#666", fontSize: 12 }}>log_path</div>
          <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
            {batchState?.log_path || "—"}
          </div>
        </div>
      </div>

      <div className="card" style={{ display: "grid", gap: 12 }}>
        <h3 style={{ margin: 0 }}>進捗</h3>
        {sortedChannels.length ? (
          sortedChannels.map((ch) => {
            const p = progress?.[ch] ?? {};
            const total = Number(p.total || 0);
            const done = Number(p.done || 0);
            const percent = pct(done, total);
            return (
              <div key={ch} style={{ display: "grid", gap: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <div>
                    <span style={{ fontWeight: 700 }}>{ch}</span>{" "}
                    <span style={{ color: "#666", fontSize: 12 }}>
                      {done}/{total}
                    </span>
                    {p.current_episode ? (
                      <span style={{ color: "#666", fontSize: 12 }}> · current {p.current_episode}</span>
                    ) : null}
                  </div>
                  <div className="mono" style={{ fontSize: 12 }}>
                    {percent}%
                  </div>
                </div>
                <div style={{ height: 10, background: "#eee", borderRadius: 999, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${percent}%`, background: "#2563eb" }} />
                </div>
              </div>
            );
          })
        ) : (
          <div style={{ color: "#666" }}>進捗情報がありません（まだRUNが記録されていない可能性）。</div>
        )}
      </div>

      <div className="row" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <div className="card" style={{ display: "grid", gap: 8 }}>
          <h3 style={{ margin: 0 }}>現在RUN</h3>
          <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
            {current?.run_id ? `${current.kind || ""} ${current.run_id}` : "—"}
          </div>
          <div style={{ display: "grid", gap: 4 }}>
            <div style={{ color: "#666", fontSize: 12 }}>stage</div>
            <div style={{ fontWeight: 700 }}>{currentStageLabel}</div>
          </div>
          {currentRun?.exists ? (
            <div style={{ display: "grid", gap: 4 }}>
              <div style={{ color: "#666", fontSize: 12 }}>cues / images</div>
              <div className="mono" style={{ fontSize: 12 }}>
                {(currentRun.images_count ?? 0).toString()}/{(currentRun.cues_total ?? "—").toString()}
              </div>
              <div style={{ color: "#666", fontSize: 12 }}>capcut</div>
              <div>{currentRun.capcut_done ? badge("DONE", "ok") : badge("NOT YET", "muted")}</div>
              {currentRun.capcut_draft ? (
                <>
                  <div style={{ color: "#666", fontSize: 12 }}>capcut_draft</div>
                  <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
                    {currentRun.capcut_draft}
                  </div>
                </>
              ) : null}
            </div>
          ) : (
            <div style={{ color: "#666" }}>run_dir がまだ生成されていない可能性があります。</div>
          )}
        </div>

        <div className="card" style={{ display: "grid", gap: 8 }}>
          <h3 style={{ margin: 0 }}>現在RUNログ（srt2images.log tail）</h3>
          <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
            {(currentRun?.srt2images_log_tail || []).slice(-40).join("\n") || "—"}
          </div>
        </div>

        <div className="card" style={{ display: "grid", gap: 8 }}>
          <h3 style={{ margin: 0 }}>バッチログ tail</h3>
          <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
            {(status?.log_tail || []).slice(-80).join("\n") || "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
