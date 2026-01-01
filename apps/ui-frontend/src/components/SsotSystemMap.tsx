import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchSsotCatalog } from "../api/client";
import type { SsotCatalog, SsotCatalogFlowStep } from "../api/types";
import { SsotFilePreview } from "./SsotFilePreview";
import { SsotFlowGraph } from "./SsotFlowGraph";

type FlowKey =
  | "mainline"
  | "planning"
  | "script_pipeline"
  | "audio_tts"
  | "video_auto_capcut_run"
  | "thumbnails"
  | "publish";

function nodeTitle(step: SsotCatalogFlowStep): string {
  const order = step.order ? String(step.order).padStart(2, "0") : "";
  const prefix = step.phase && order ? `${step.phase}-${order}` : step.phase || "";
  const label = step.node_id || "";
  const name = step.name || "";
  const base = label && name && !label.endsWith(name) ? `${label} · ${name}` : label || name || "unknown";
  if (prefix) return `${prefix} ${base}`;
  return base;
}

export function SsotSystemMap() {
  const [catalog, setCatalog] = useState<SsotCatalog | null>(null);
  const [flow, setFlow] = useState<FlowKey>("mainline");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<"horizontal" | "vertical">("horizontal");
  const [graphScale, setGraphScale] = useState(1);
  const [graphSize, setGraphSize] = useState<{ width: number; height: number }>({ width: 640, height: 240 });
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const graphViewportRef = useRef<HTMLDivElement | null>(null);

  const loadCatalog = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSsotCatalog(refresh);
      setCatalog(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCatalog(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCatalog(false);
  }, [loadCatalog]);

  const nodes: SsotCatalogFlowStep[] = useMemo(() => {
    if (!catalog) return [];
    if (flow === "mainline") return catalog.mainline.nodes || [];
    if (flow === "planning") return catalog.flows.planning?.steps || [];
    if (flow === "script_pipeline") return catalog.flows.script_pipeline?.steps || [];
    if (flow === "audio_tts") return catalog.flows.audio_tts?.steps || [];
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run?.steps || [];
    if (flow === "thumbnails") return catalog.flows.thumbnails?.steps || [];
    if (flow === "publish") return catalog.flows.publish?.steps || [];
    return [];
  }, [catalog, flow]);

  const edges = useMemo(() => {
    if (!catalog) return [];
    if (flow === "mainline") return catalog.mainline.edges || [];
    if (flow === "planning") return catalog.flows.planning?.edges || [];
    if (flow === "script_pipeline") return catalog.flows.script_pipeline?.edges || [];
    if (flow === "audio_tts") return catalog.flows.audio_tts?.edges || [];
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run?.edges || [];
    if (flow === "thumbnails") return catalog.flows.thumbnails?.edges || [];
    if (flow === "publish") return catalog.flows.publish?.edges || [];
    return [];
  }, [catalog, flow]);

  useEffect(() => {
    setOrientation(flow === "mainline" ? "horizontal" : "vertical");
  }, [flow]);

  const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));
  const zoomIn = () => setGraphScale((s) => clamp(Number((s + 0.1).toFixed(2)), 0.3, 2.5));
  const zoomOut = () => setGraphScale((s) => clamp(Number((s - 0.1).toFixed(2)), 0.3, 2.5));
  const zoomReset = () => setGraphScale(1);
  const zoomFit = () => {
    const el = graphViewportRef.current;
    if (!el) return;
    const w = el.clientWidth - 40;
    const h = el.clientHeight - 40;
    if (w <= 0 || h <= 0) return;
    const scale = Math.min(w / graphSize.width, h / graphSize.height);
    setGraphScale(clamp(Number(scale.toFixed(2)), 0.3, 2.5));
  };

  const handleGraphSize = useCallback((size: { width: number; height: number }) => {
    if (!size.width || !size.height) return;
    setGraphSize((prev) => {
      if (prev.width === size.width && prev.height === size.height) return prev;
      return size;
    });
  }, []);

  const filteredNodes = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return nodes;
    return nodes.filter((n) => `${n.node_id} ${n.name} ${n.description || ""}`.toLowerCase().includes(q));
  }, [keyword, nodes]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    return nodes.find((n) => n.node_id === selectedNodeId) ?? null;
  }, [nodes, selectedNodeId]);

  useEffect(() => {
    if (!selectedNodeId && nodes.length > 0) {
      setSelectedNodeId(nodes[0].node_id);
    }
  }, [nodes, selectedNodeId]);

  const selectedRunnerRef = useMemo(() => {
    if (!selectedNode) return null;
    const impl = selectedNode.impl as any;
    const runner = impl?.runner;
    const auto = impl?.auto_capcut_run;
    if (runner?.path) {
      return { path: String(runner.path), line: runner.dispatch_line ? Number(runner.dispatch_line) : null };
    }
    if (auto?.path) {
      return { path: String(auto.path), line: auto.line ? Number(auto.line) : null };
    }
    return null;
  }, [selectedNode]);

  const selectedImplRefs = useMemo(() => {
    if (!selectedNode) return [];
    const refs = (selectedNode as any).impl_refs;
    if (Array.isArray(refs) && refs.length > 0) {
      return refs
        .map((r) => ({
          path: r?.path ? String(r.path) : "",
          line: r?.line ? Number(r.line) : null,
          symbol: r?.symbol ? String(r.symbol) : null,
        }))
        .filter((r) => Boolean(r.path));
    }
    return selectedRunnerRef ? [{ path: selectedRunnerRef.path, line: selectedRunnerRef.line, symbol: null }] : [];
  }, [selectedNode, selectedRunnerRef]);

  const selectedTemplatePath = useMemo(() => {
    if (!selectedNode) return null;
    const tpl = selectedNode.template as any;
    const p = tpl?.path ? String(tpl.path) : "";
    return p || null;
  }, [selectedNode]);

  const stageLlmTask = useMemo(() => {
    if (!selectedNode) return null;
    const llm = selectedNode.llm as any;
    return llm?.task ? String(llm.task) : null;
  }, [selectedNode]);

  const missingTasks = useMemo(() => catalog?.llm?.missing_task_defs || [], [catalog]);

  return (
    <section className="research-workspace">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot/map</p>
          <h2>System Map（全処理の可視化）</h2>
          <p className="research-workspace__note">
            SSOTと実装の“ズレ”をなくすために、コードから自動生成したカタログを閲覧します（read-only）。
          </p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <button type="button" className={`research-chip ${flow === "mainline" ? "active" : ""}`} onClick={() => setFlow("mainline")}>
              Mainline
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "planning" ? "active" : ""}`}
              onClick={() => setFlow("planning")}
              disabled={!catalog?.flows?.planning}
            >
              Planning
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "script_pipeline" ? "active" : ""}`}
              onClick={() => setFlow("script_pipeline")}
              disabled={!catalog?.flows?.script_pipeline}
            >
              Script Pipeline
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "audio_tts" ? "active" : ""}`}
              onClick={() => setFlow("audio_tts")}
              disabled={!catalog?.flows?.audio_tts}
            >
              Audio/TTS
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "video_auto_capcut_run" ? "active" : ""}`}
              onClick={() => setFlow("video_auto_capcut_run")}
              disabled={!catalog?.flows?.video_auto_capcut_run}
            >
              Video auto_capcut_run
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "thumbnails" ? "active" : ""}`}
              onClick={() => setFlow("thumbnails")}
              disabled={!catalog?.flows?.thumbnails}
            >
              Thumbnails
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "publish" ? "active" : ""}`}
              onClick={() => setFlow("publish")}
              disabled={!catalog?.flows?.publish}
            >
              Publish
            </button>
            <button type="button" className="research-chip" onClick={() => void loadCatalog(true)} disabled={loading}>
              {loading ? "更新中…" : "再生成"}
            </button>
          </div>
        </div>
      </header>

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">カタログ</p>
              <div className="research-breadcrumb">
                <strong>{catalog?.schema ?? "—"}</strong>
                <span className="crumb-sep">/</span>
                <span className="muted small-text">{catalog?.generated_at ?? ""}</span>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge">{filteredNodes.length} 件</span>
            </div>
          </div>
          <input
            className="research-workspace__search"
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="検索（node_id / 名前 / 説明）"
          />
          {error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
          {missingTasks.length > 0 ? (
            <div className="main-alert main-alert--warning">
              LLMタスク定義が見つからないものがあります: <span className="mono">{missingTasks.join(", ")}</span>
            </div>
          ) : null}
          <ul className="research-list__items">
            {filteredNodes.map((n) => (
              <li key={n.node_id}>
                <button
                  className="research-entry"
                  onClick={() => setSelectedNodeId(n.node_id)}
                  style={{ borderColor: selectedNodeId === n.node_id ? "var(--color-primary)" : undefined }}
                >
                  <span className="badge dir">{n.phase}</span>
                  <div className="research-entry__meta">
                    <span className="name">{nodeTitle(n)}</span>
                    <span className="meta">{n.description ? n.description : "—"}</span>
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
              <p className="research-viewer__path">{selectedNode ? nodeTitle(selectedNode) : "—"}</p>
            </div>
            {selectedNode ? <span className="badge subtle">read-only</span> : null}
          </div>

          <div style={{ display: "grid", gap: 14 }}>
            <section className="shell-panel shell-panel--placeholder">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <h3 style={{ marginTop: 0 }}>Flow Graph</h3>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className={`research-chip ${orientation === "horizontal" ? "active" : ""}`}
                    onClick={() => setOrientation("horizontal")}
                  >
                    横
                  </button>
                  <button
                    type="button"
                    className={`research-chip ${orientation === "vertical" ? "active" : ""}`}
                    onClick={() => setOrientation("vertical")}
                  >
                    縦
                  </button>
                  <button type="button" className="research-chip" onClick={zoomOut}>
                    −
                  </button>
                  <button type="button" className="research-chip" onClick={zoomReset}>
                    {graphScale.toFixed(2)}x
                  </button>
                  <button type="button" className="research-chip" onClick={zoomIn}>
                    +
                  </button>
                  <button type="button" className="research-chip" onClick={zoomFit}>
                    Fit
                  </button>
                </div>
              </div>
              <div
                ref={graphViewportRef}
                style={{
                  marginTop: 10,
                  border: "1px solid var(--color-border-muted)",
                  borderRadius: 14,
                  background: "var(--color-surface-subtle)",
                  overflow: "auto",
                  maxHeight: "55vh",
                }}
              >
                <div
                  style={{
                    position: "relative",
                    width: graphSize.width * graphScale,
                    height: graphSize.height * graphScale,
                    minWidth: 640,
                    minHeight: 240,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      transform: `scale(${graphScale})`,
                      transformOrigin: "top left",
                    }}
                  >
                    <SsotFlowGraph
                      steps={nodes}
                      edges={edges}
                      selectedNodeId={selectedNodeId}
                      onSelect={(id) => setSelectedNodeId(id)}
                      orientation={orientation}
                      highlightedNodeIds={keyword.trim() ? filteredNodes.map((n) => n.node_id) : []}
                      onSize={handleGraphSize}
                    />
                  </div>
                </div>
              </div>
              <div className="muted small-text" style={{ marginTop: 8 }}>
                ノードをクリックすると詳細へジャンプします（黄色=検索一致、緑=下流、紫=上流）。
              </div>
            </section>

            {!selectedNode ? <div className="main-alert">Flow Graph のノードをクリックするか、左から選択してください。</div> : null}

            {selectedNode ? (
              <>
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>概要</h3>
                  <div className="mono muted">node_id: {selectedNode.node_id}</div>
                  {selectedNode.description ? <p style={{ marginBottom: 0 }}>{selectedNode.description}</p> : null}
                </section>

              {selectedNode.outputs ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>Outputs（宣言）</h3>
                  <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(selectedNode.outputs, null, 2)}
                  </pre>
                </section>
              ) : null}

              {(selectedNode as any).sot ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>SoT（正本）</h3>
                  <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                    {JSON.stringify((selectedNode as any).sot, null, 2)}
                  </pre>
                </section>
              ) : null}

              {selectedNode.llm ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>LLM</h3>
                  {stageLlmTask ? (
                    <p style={{ marginTop: 0 }}>
                      task: <span className="mono">{stageLlmTask}</span>
                    </p>
                  ) : null}
                  <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(selectedNode.llm, null, 2)}
                  </pre>
                </section>
              ) : null}

              {selectedTemplatePath ? <SsotFilePreview repoPath={selectedTemplatePath} title="Prompt Template" /> : null}
              {selectedImplRefs.map((ref, idx) => (
                <SsotFilePreview
                  key={`${ref.path}:${ref.line ?? 0}:${idx}`}
                  repoPath={ref.path}
                  highlightLine={ref.line}
                  title={ref.symbol ? `Implementation (${ref.symbol})` : "Implementation (source)"}
                />
              ))}

                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>Catalog Summary</h3>
                  <div style={{ display: "grid", gap: 6 }}>
                    <div>
                      API routes: <span className="mono">{catalog?.entrypoints?.api_routes?.length ?? 0}</span>
                    </div>
                    <div>
                      CLI entrypoints (python): <span className="mono">{catalog?.entrypoints?.python?.length ?? 0}</span>
                    </div>
                    <div>
                      LLM tasks used: <span className="mono">{catalog?.llm?.used_tasks?.length ?? 0}</span>
                    </div>
                  </div>
                </section>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
