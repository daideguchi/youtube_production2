import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchResearchFileChunk } from "../api/client";
import type { ResearchFileResponse } from "../api/types";

type RepoBase =
  | "ssot"
  | "packages"
  | "backend"
  | "frontend"
  | "repo_scripts"
  | "prompts"
  | "configs"
  | "tests"
  | "scripts"
  | "planning"
  | "audio"
  | "video_runs"
  | "thumbnails"
  | "logs";

function resolveRepoBase(repoPath: string): { base: RepoBase; path: string } | null {
  const p = (repoPath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  if (!p) return null;
  if (p.startsWith("ssot/")) return { base: "ssot", path: p.replace(/^ssot\//, "") };
  if (p.startsWith("packages/")) return { base: "packages", path: p.replace(/^packages\//, "") };
  if (p.startsWith("apps/ui-backend/backend/")) return { base: "backend", path: p.replace(/^apps\/ui-backend\/backend\//, "") };
  if (p.startsWith("apps/ui-frontend/src/")) return { base: "frontend", path: p.replace(/^apps\/ui-frontend\/src\//, "") };
  if (p.startsWith("scripts/")) return { base: "repo_scripts", path: p.replace(/^scripts\//, "") };
  if (p.startsWith("prompts/")) return { base: "prompts", path: p.replace(/^prompts\//, "") };
  if (p.startsWith("configs/")) return { base: "configs", path: p.replace(/^configs\//, "") };
  if (p.startsWith("tests/")) return { base: "tests", path: p.replace(/^tests\//, "") };
  if (p.startsWith("workspaces/scripts/")) return { base: "scripts", path: p.replace(/^workspaces\/scripts\//, "") };
  if (p.startsWith("workspaces/planning/")) return { base: "planning", path: p.replace(/^workspaces\/planning\//, "") };
  if (p.startsWith("workspaces/audio/")) return { base: "audio", path: p.replace(/^workspaces\/audio\//, "") };
  if (p.startsWith("workspaces/video/runs/")) return { base: "video_runs", path: p.replace(/^workspaces\/video\/runs\//, "") };
  if (p.startsWith("workspaces/thumbnails/")) return { base: "thumbnails", path: p.replace(/^workspaces\/thumbnails\//, "") };
  if (p.startsWith("workspaces/logs/")) return { base: "logs", path: p.replace(/^workspaces\/logs\//, "") };
  return null;
}

function toDisplayLines(content: string, startLineNo: number, highlightLineNo?: number | null) {
  const lines = (content || "").split("\n");
  return lines.map((line, idx) => {
    const ln = startLineNo + idx;
    const highlighted = highlightLineNo ? ln === highlightLineNo : false;
    return (
      <div
        key={idx}
        style={{
          display: "grid",
          gridTemplateColumns: "72px 1fr",
          gap: 10,
          padding: "0 10px",
          background: highlighted ? "rgba(255, 200, 0, 0.15)" : "transparent",
        }}
      >
        <span className="mono muted" style={{ textAlign: "right", userSelect: "none" }}>
          {ln}
        </span>
        <span className="mono" style={{ whiteSpace: "pre-wrap" }}>
          {line || " "}
        </span>
      </div>
    );
  });
}

export function SsotFilePreview({
  repoPath,
  highlightLine,
  title,
}: {
  repoPath: string;
  highlightLine?: number | null;
  title: string;
}) {
  const [data, setData] = useState<ResearchFileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolved = useMemo(() => resolveRepoBase(repoPath), [repoPath]);

  const load = useCallback(async () => {
    if (!resolved) {
      setError("未対応のパスです（base解決不可）");
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const line = highlightLine ?? null;
      const ctx = 80;
      const start = line ? Math.max(0, line - ctx - 1) : 0;
      const length = line ? 220 : 240;
      const resp = await fetchResearchFileChunk(resolved.base, resolved.path, { offset: start, length });
      setData(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [highlightLine, resolved]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!repoPath) return null;

  return (
    <section style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, overflow: "hidden" }}>
      <header style={{ padding: 12, borderBottom: "1px solid var(--color-border-muted)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <strong>{title}</strong>
          <span className="mono muted">{repoPath}</span>
        </div>
        {resolved ? (
          <div className="muted small-text" style={{ marginTop: 6 }}>
            base={resolved.base} / path={resolved.path}
          </div>
        ) : null}
      </header>
      {loading ? <div className="main-alert">読み込み中…</div> : null}
      {error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
      {!loading && !error && data ? (
        <div style={{ maxHeight: 420, overflow: "auto", background: "var(--color-surface)" }}>
          <div style={{ padding: "10px 0" }}>{toDisplayLines(data.content, (data.offset ?? 0) + 1, highlightLine ?? undefined)}</div>
        </div>
      ) : null}
    </section>
  );
}

