import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchSsotCatalog } from "../api/client";
import type { SsotCatalog, SsotCatalogEntrypoint, SsotCatalogRoute } from "../api/types";
import { SsotFilePreview } from "../components/SsotFilePreview";

type TabKey = "api_routes" | "python_entrypoints" | "shell_entrypoints" | "llm_callsites";

function routeLabel(r: SsotCatalogRoute): string {
  const method = (r.method || "").toUpperCase();
  return `${method} ${r.path}`;
}

function pythonEntrypointLabel(e: SsotCatalogEntrypoint): string {
  if (e.module) return `python3 -m ${e.module}`;
  return `python3 ${e.path}`;
}

export function SsotEntrypointsPage() {
  const [catalog, setCatalog] = useState<SsotCatalog | null>(null);
  const [tab, setTab] = useState<TabKey>("api_routes");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSsotCatalog(false);
      setCatalog(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCatalog(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const apiRoutes = useMemo(() => catalog?.entrypoints?.api_routes || [], [catalog]);
  const pyEntrypoints = useMemo(() => (catalog?.entrypoints?.python || []).filter((e) => e.kind === "python"), [catalog]);
  const shEntrypoints = useMemo(() => catalog?.entrypoints?.shell || [], [catalog]);
  const llmCallsites = useMemo(() => catalog?.llm?.callsites || [], [catalog]);

  const items = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (tab === "api_routes") {
      const list = apiRoutes.map((r) => ({ id: routeLabel(r), label: routeLabel(r), meta: r.summary || "" }));
      return q ? list.filter((i) => `${i.id} ${i.meta}`.toLowerCase().includes(q)) : list;
    }
    if (tab === "python_entrypoints") {
      const list = pyEntrypoints.map((e) => ({ id: e.path, label: pythonEntrypointLabel(e), meta: e.summary || "" }));
      return q ? list.filter((i) => `${i.id} ${i.label} ${i.meta}`.toLowerCase().includes(q)) : list;
    }
    if (tab === "shell_entrypoints") {
      const list = shEntrypoints.map((e) => ({ id: e.path, label: `sh ${e.path}`, meta: e.summary || "" }));
      return q ? list.filter((i) => `${i.id} ${i.label} ${i.meta}`.toLowerCase().includes(q)) : list;
    }
    const list = llmCallsites.map((c) => ({
      id: `${c.task} @ ${c.source.path}:${c.source.line}`,
      label: c.task,
      meta: `${c.call} · ${c.source.path}:${c.source.line}`,
    }));
    return q ? list.filter((i) => `${i.id} ${i.label} ${i.meta}`.toLowerCase().includes(q)) : list;
  }, [apiRoutes, keyword, llmCallsites, pyEntrypoints, shEntrypoints, tab]);

  useEffect(() => {
    if (!selectedId && items.length > 0) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = useMemo(() => {
    if (!catalog || !selectedId) return null;
    if (tab === "api_routes") return apiRoutes.find((r) => routeLabel(r) === selectedId) || null;
    if (tab === "python_entrypoints") return pyEntrypoints.find((e) => e.path === selectedId) || null;
    if (tab === "shell_entrypoints") return shEntrypoints.find((e) => e.path === selectedId) || null;
    return llmCallsites.find((c) => `${c.task} @ ${c.source.path}:${c.source.line}` === selectedId) || null;
  }, [apiRoutes, catalog, llmCallsites, pyEntrypoints, selectedId, shEntrypoints, tab]);

  return (
    <section className="research-workspace">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot/entrypoints</p>
          <h2>Entrypoints / Routes</h2>
          <p className="research-workspace__note">CLI/API/LLM呼び出しの入口を、コードから自動収集したカタログで辿ります（read-only）。</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <Link className="research-chip" to="/ssot">
              SSOT Portal
            </Link>
            <Link className="research-chip" to="/ssot/map">
              System Map
            </Link>
            <button type="button" className={`research-chip ${tab === "api_routes" ? "is-active" : ""}`} onClick={() => setTab("api_routes")}>
              API Routes
            </button>
            <button
              type="button"
              className={`research-chip ${tab === "python_entrypoints" ? "is-active" : ""}`}
              onClick={() => setTab("python_entrypoints")}
            >
              Python CLI
            </button>
            <button type="button" className={`research-chip ${tab === "shell_entrypoints" ? "is-active" : ""}`} onClick={() => setTab("shell_entrypoints")}>
              Shell
            </button>
            <button type="button" className={`research-chip ${tab === "llm_callsites" ? "is-active" : ""}`} onClick={() => setTab("llm_callsites")}>
              LLM Callsites
            </button>
          </div>
        </div>
      </header>

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">一覧</p>
              <div className="research-breadcrumb">
                <strong>{tab}</strong>
                <span className="crumb-sep">/</span>
                <span className="muted small-text">{catalog?.generated_at ?? ""}</span>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge">{items.length} 件</span>
            </div>
          </div>
          <input className="research-workspace__search" type="search" value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="検索" />
          {loading ? <div className="main-alert">読み込み中…</div> : null}
          {error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
          <ul className="research-list__items">
            {items.map((i) => (
              <li key={i.id}>
                <button
                  className="research-entry"
                  onClick={() => setSelectedId(i.id)}
                  style={{ borderColor: selectedId === i.id ? "var(--color-primary)" : undefined }}
                >
                  <span className="badge dir">{tab === "api_routes" ? "API" : tab === "llm_callsites" ? "LLM" : "CLI"}</span>
                  <div className="research-entry__meta">
                    <span className="name">{i.label}</span>
                    <span className="meta">{i.meta || "—"}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="research-viewer">
          <div className="research-viewer__header">
            <div>
              <strong>詳細</strong>
              <p className="research-viewer__path">{selectedId ?? "—"}</p>
            </div>
            <span className="badge subtle">read-only</span>
          </div>

          {!selected ? <div className="main-alert">左から選択してください。</div> : null}

          {selected && tab === "api_routes" ? (
            <div style={{ display: "grid", gap: 14 }}>
              <section className="shell-panel shell-panel--placeholder">
                <h3 style={{ marginTop: 0 }}>Route</h3>
                <div className="mono">
                  {(selected as SsotCatalogRoute).method} {(selected as SsotCatalogRoute).path}
                </div>
                {(selected as SsotCatalogRoute).summary ? <p style={{ marginBottom: 0 }}>{(selected as SsotCatalogRoute).summary}</p> : null}
              </section>
              <SsotFilePreview repoPath={(selected as SsotCatalogRoute).source.path} highlightLine={(selected as SsotCatalogRoute).source.line} title="Implementation" />
            </div>
          ) : null}

          {selected && tab === "python_entrypoints" ? (
            <div style={{ display: "grid", gap: 14 }}>
              <section className="shell-panel shell-panel--placeholder">
                <h3 style={{ marginTop: 0 }}>Python CLI</h3>
                <div className="mono">{pythonEntrypointLabel(selected as SsotCatalogEntrypoint)}</div>
                {(selected as SsotCatalogEntrypoint).summary ? <p style={{ marginBottom: 0 }}>{(selected as SsotCatalogEntrypoint).summary}</p> : null}
              </section>
              <SsotFilePreview repoPath={(selected as SsotCatalogEntrypoint).path} title="Source" />
            </div>
          ) : null}

          {selected && tab === "shell_entrypoints" ? (
            <div style={{ display: "grid", gap: 14 }}>
              <section className="shell-panel shell-panel--placeholder">
                <h3 style={{ marginTop: 0 }}>Shell</h3>
                <div className="mono">sh {(selected as any).path}</div>
                {(selected as any).summary ? <p style={{ marginBottom: 0 }}>{(selected as any).summary}</p> : null}
              </section>
              <SsotFilePreview repoPath={(selected as any).path} title="Source" />
            </div>
          ) : null}

          {selected && tab === "llm_callsites" ? (
            <div style={{ display: "grid", gap: 14 }}>
              <section className="shell-panel shell-panel--placeholder">
                <h3 style={{ marginTop: 0 }}>LLM Callsite</h3>
                <div className="mono">
                  task={(selected as any).task} / call={(selected as any).call}
                </div>
              </section>
              <SsotFilePreview repoPath={(selected as any).source.path} highlightLine={(selected as any).source.line} title="Implementation" />
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
