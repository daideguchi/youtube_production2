import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { fetchResearchFileChunk, fetchResearchList, fetchSsotCatalog } from "../api/client";
import type { ResearchFileEntry, SsotCatalog, SsotCatalogFlowStep } from "../api/types";
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

type TraceEvent = { kind: "llm" | "image"; task: string; at_ms: number | null };

function parseIsoMs(raw: unknown): number | null {
  if (!raw) return null;
  const s = String(raw);
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
}

function stripJsonl(name: string): string {
  return name.endsWith(".jsonl") ? name.slice(0, -".jsonl".length) : name;
}

function parseJsonlEvents(content: string, kind: "llm" | "image"): TraceEvent[] {
  const out: TraceEvent[] = [];
  for (const line of (content || "").split("\n")) {
    const s = line.trim();
    if (!s) continue;
    try {
      const obj = JSON.parse(s) as any;
      const task = obj?.task ? String(obj.task) : "";
      if (!task) continue;
      out.push({ kind, task, at_ms: parseIsoMs(obj?.generated_at) });
    } catch {
      // ignore
    }
  }
  return out;
}

type OutputDecl = { path: string; required: boolean | null; raw: unknown };

function parseOutputDecls(raw: unknown): OutputDecl[] {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : [];
  const out: OutputDecl[] = [];
  for (const item of arr) {
    if (typeof item === "string") {
      out.push({ path: item, required: null, raw: item });
      continue;
    }
    if (item && typeof item === "object") {
      const obj = item as any;
      const path = obj?.path ? String(obj.path) : JSON.stringify(item);
      const required = typeof obj?.required === "boolean" ? Boolean(obj.required) : null;
      out.push({ path, required, raw: item });
      continue;
    }
    out.push({ path: String(item), required: null, raw: item });
  }
  return out.filter((o) => Boolean((o.path || "").trim()));
}

function parsePlaceholderPairs(raw: unknown): Array<{ key: string; value: string }> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const obj = raw as Record<string, unknown>;
  return Object.keys(obj)
    .sort((a, b) => a.localeCompare(b))
    .map((k) => ({ key: k, value: typeof obj[k] === "string" ? String(obj[k]) : JSON.stringify(obj[k]) }));
}

type SotDecl = { path: string; kind: string | null; notes: string | null; raw: unknown };

function parseSotDecls(raw: unknown): SotDecl[] {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : [];
  const out: SotDecl[] = [];
  for (const item of arr) {
    if (typeof item === "string") {
      out.push({ path: item, kind: null, notes: null, raw: item });
      continue;
    }
    if (item && typeof item === "object") {
      const obj = item as any;
      const path = obj?.path ? String(obj.path) : JSON.stringify(item);
      const kind = obj?.kind ? String(obj.kind) : null;
      const notes = obj?.notes ? String(obj.notes) : null;
      out.push({ path, kind, notes, raw: item });
      continue;
    }
    out.push({ path: String(item), kind: null, notes: null, raw: item });
  }
  return out.filter((o) => Boolean((o.path || "").trim()));
}

function domIdForNode(nodeId: string): string {
  const safe = (nodeId || "").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 160);
  return `ssot-node-${safe || "unknown"}`;
}

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}

export function SsotSystemMap() {
  const [catalog, setCatalog] = useState<SsotCatalog | null>(null);
  const [flow, setFlow] = useState<FlowKey>("mainline");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<"horizontal" | "vertical">("horizontal");
  const [graphScale, setGraphScale] = useState(1);
  const [graphSize, setGraphSize] = useState<{ width: number; height: number }>({ width: 640, height: 240 });
  const [focusMode, setFocusMode] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const graphViewportRef = useRef<HTMLDivElement | null>(null);
  const autoFitPendingRef = useRef(true);

  const [traceKey, setTraceKey] = useState("");
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [traceLoadedKey, setTraceLoadedKey] = useState<string | null>(null);
  const [traceTaskSummary, setTraceTaskSummary] = useState<Record<string, { firstIndex: number; count: number }>>({});
  const [traceEventCount, setTraceEventCount] = useState(0);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [traceKeySuggestions, setTraceKeySuggestions] = useState<Array<{ key: string; modified_ms: number }>>([]);
  const [traceListLoading, setTraceListLoading] = useState(false);

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

  const flowTasks = useMemo(() => {
    const set = new Set<string>();
    for (const n of nodes) {
      const llm = (n as any).llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (t) set.add(t);
    }
    return set;
  }, [nodes]);

  const flowTaskList = useMemo(() => Array.from(flowTasks).sort((a, b) => a.localeCompare(b)), [flowTasks]);

  const flowMeta = useMemo(() => {
    if (!catalog) return null;
    if (flow === "mainline") return catalog.mainline as any;
    if (flow === "planning") return catalog.flows.planning as any;
    if (flow === "script_pipeline") return catalog.flows.script_pipeline as any;
    if (flow === "audio_tts") return catalog.flows.audio_tts as any;
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run as any;
    if (flow === "thumbnails") return catalog.flows.thumbnails as any;
    if (flow === "publish") return catalog.flows.publish as any;
    return null;
  }, [catalog, flow]);

  const flowSotDecls = useMemo(() => parseSotDecls((flowMeta as any)?.sot), [flowMeta]);

  const flowCodePaths = useMemo(() => {
    const out: string[] = [];
    const m = flowMeta as any;
    const candidates = [
      m?.runner_path,
      m?.stages_path,
      m?.templates_path,
      m?.run_tts_path,
      m?.llm_adapter_path,
      m?.auto_capcut_run_path,
      m?.path,
    ];
    for (const p of candidates) {
      if (typeof p === "string" && p.trim()) out.push(p.trim());
    }
    return Array.from(new Set(out));
  }, [flowMeta]);

  const selectedMainlineFlow = useMemo<FlowKey | null>(() => {
    if (flow !== "mainline") return null;
    const id = (selectedNodeId || "").trim();
    if (!id) return null;
    if (id === "A/planning") return "planning";
    if (id === "B/script_pipeline") return "script_pipeline";
    if (id === "C/audio_tts") return "audio_tts";
    if (id === "D/video") return "video_auto_capcut_run";
    if (id === "F/thumbnails") return "thumbnails";
    if (id === "G/publish") return "publish";
    return null;
  }, [flow, selectedNodeId]);

  const selectedMainlineFlowMeta = useMemo(() => {
    if (!catalog || !selectedMainlineFlow) return null;
    if (selectedMainlineFlow === "planning") return catalog.flows.planning as any;
    if (selectedMainlineFlow === "script_pipeline") return catalog.flows.script_pipeline as any;
    if (selectedMainlineFlow === "audio_tts") return catalog.flows.audio_tts as any;
    if (selectedMainlineFlow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run as any;
    if (selectedMainlineFlow === "thumbnails") return catalog.flows.thumbnails as any;
    if (selectedMainlineFlow === "publish") return catalog.flows.publish as any;
    return null;
  }, [catalog, selectedMainlineFlow]);

  const selectedMainlineSotDecls = useMemo(() => parseSotDecls((selectedMainlineFlowMeta as any)?.sot), [selectedMainlineFlowMeta]);

  const selectedMainlineTasks = useMemo(() => {
    const steps = (selectedMainlineFlowMeta as any)?.steps;
    const out = new Set<string>();
    if (Array.isArray(steps)) {
      for (const s of steps) {
        const task = s?.llm?.task ? String(s.llm.task) : "";
        if (task) out.add(task);
      }
    }
    return Array.from(out).sort((a, b) => a.localeCompare(b));
  }, [selectedMainlineFlowMeta]);

  const selectedMainlineCodePaths = useMemo(() => {
    const out: string[] = [];
    const m = selectedMainlineFlowMeta as any;
    const candidates = [
      m?.runner_path,
      m?.stages_path,
      m?.templates_path,
      m?.run_tts_path,
      m?.llm_adapter_path,
      m?.auto_capcut_run_path,
      m?.path,
    ];
    for (const p of candidates) {
      if (typeof p === "string" && p.trim()) out.push(p.trim());
    }
    return Array.from(new Set(out));
  }, [selectedMainlineFlowMeta]);

  const executedByNodeId = useMemo(() => {
    if (!traceLoadedKey) return {};
    const out: Record<string, { firstIndex: number; count: number }> = {};
    for (const n of nodes) {
      const llm = (n as any).llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (!t) continue;
      const info = traceTaskSummary[t];
      if (info) out[n.node_id] = info;
    }
    return out;
  }, [nodes, traceLoadedKey, traceTaskSummary]);

  const executedEdges = useMemo(() => {
    if (!traceLoadedKey) return {};
    if (!traceEvents || traceEvents.length === 0) return {};

    const taskToNodeId: Record<string, string> = {};
    for (const n of nodes) {
      const llm = (n as any).llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (!t) continue;
      if (!taskToNodeId[t]) taskToNodeId[t] = n.node_id;
    }

    const edgeKeys = new Set<string>();
    for (const e of edges as any[]) {
      const fromId = e?.from ? String(e.from) : "";
      const toId = e?.to ? String(e.to) : "";
      if (fromId && toId) edgeKeys.add(`${fromId} -> ${toId}`);
    }

    const out: Record<string, { firstIndex: number; count: number }> = {};
    let prevNodeId: string | null = null;
    for (let idx = 0; idx < traceEvents.length; idx++) {
      const nodeId = taskToNodeId[traceEvents[idx].task] || null;
      if (!nodeId) continue;
      if (!prevNodeId) {
        prevNodeId = nodeId;
        continue;
      }
      if (prevNodeId === nodeId) continue;
      const key = `${prevNodeId} -> ${nodeId}`;
      prevNodeId = nodeId;
      if (!edgeKeys.has(key)) continue;
      const cur = out[key];
      if (!cur) out[key] = { firstIndex: idx, count: 1 };
      else out[key] = { firstIndex: cur.firstIndex, count: cur.count + 1 };
    }
    return out;
  }, [edges, nodes, traceEvents, traceLoadedKey]);

  const traceUnmatchedTasks = useMemo(() => {
    if (!traceLoadedKey) return [];
    const tasks = Object.keys(traceTaskSummary || {});
    return tasks.filter((t) => !flowTasks.has(t)).slice(0, 50);
  }, [flowTasks, traceLoadedKey, traceTaskSummary]);

  const traceMatchedTaskCount = useMemo(() => {
    if (!traceLoadedKey) return 0;
    let c = 0;
    for (const t of Object.keys(traceTaskSummary || {})) {
      if (flowTasks.has(t)) c += 1;
    }
    return c;
  }, [flowTasks, traceLoadedKey, traceTaskSummary]);

  useEffect(() => {
    setOrientation(flow === "mainline" ? "horizontal" : "vertical");
  }, [flow]);

  const applyFitToSize = useCallback(
    (size: { width: number; height: number }) => {
      const el = graphViewportRef.current;
      if (!el) return;
      const w = el.clientWidth - 40;
      const h = el.clientHeight - 40;
      if (w <= 0 || h <= 0) return;
      const scale = Math.min(w / size.width, h / size.height);
      const next = clamp(Number(scale.toFixed(2)), 0.3, 2.5);
      setGraphScale((prev) => (Math.abs(prev - next) < 0.01 ? prev : next));
    },
    [],
  );

  const zoomIn = () => {
    autoFitPendingRef.current = false;
    setGraphScale((s) => clamp(Number((s + 0.1).toFixed(2)), 0.3, 2.5));
  };
  const zoomOut = () => {
    autoFitPendingRef.current = false;
    setGraphScale((s) => clamp(Number((s - 0.1).toFixed(2)), 0.3, 2.5));
  };
  const zoomReset = () => {
    autoFitPendingRef.current = false;
    setGraphScale(1);
  };
  const zoomFit = () => {
    autoFitPendingRef.current = false;
    applyFitToSize(graphSize);
  };

  useEffect(() => {
    autoFitPendingRef.current = true;
  }, [flow, focusMode, orientation]);

  useEffect(() => {
    if (!autoFitPendingRef.current) return;
    requestAnimationFrame(() => applyFitToSize(graphSize));
    autoFitPendingRef.current = false;
  }, [applyFitToSize, focusMode, graphSize]);

  const centerOnNode = useCallback((nodeId: string) => {
    const container = graphViewportRef.current;
    if (!container) return;
    const el = container.querySelector(`#${domIdForNode(nodeId)}`) as HTMLElement | null;
    if (!el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const dx = eRect.left + eRect.width / 2 - (cRect.left + cRect.width / 2);
    const dy = eRect.top + eRect.height / 2 - (cRect.top + cRect.height / 2);
    container.scrollLeft += dx;
    container.scrollTop += dy;
  }, []);

  const handleGraphSize = useCallback((size: { width: number; height: number }) => {
    if (!size.width || !size.height) return;
    setGraphSize((prev) => {
      if (prev.width === size.width && prev.height === size.height) return prev;
      return size;
    });
    if (!autoFitPendingRef.current) return;
    autoFitPendingRef.current = false;
    requestAnimationFrame(() => applyFitToSize(size));
  }, [applyFitToSize]);

  const loadTraceKeyList = useCallback(async () => {
    setTraceListLoading(true);
    try {
      const [llmList, imageList] = await Promise.all([
        fetchResearchList("logs", "traces/llm").catch(() => null),
        fetchResearchList("logs", "traces/image").catch(() => null),
      ]);

      const merged = new Map<string, number>();
      const add = (entries: ResearchFileEntry[] | undefined) => {
        for (const e of entries || []) {
          if (e.is_dir) continue;
          if (!e.name.endsWith(".jsonl")) continue;
          const key = stripJsonl(e.name);
          const ms = parseIsoMs(e.modified) || 0;
          merged.set(key, Math.max(merged.get(key) || 0, ms));
        }
      };

      add(llmList?.entries);
      add(imageList?.entries);

      const list = Array.from(merged.entries())
        .map(([key, modified_ms]) => ({ key, modified_ms }))
        .sort((a, b) => (b.modified_ms - a.modified_ms ? b.modified_ms - a.modified_ms : a.key.localeCompare(b.key)))
        .slice(0, 200);
      setTraceKeySuggestions(list);
    } catch {
      setTraceKeySuggestions([]);
    } finally {
      setTraceListLoading(false);
    }
  }, []);

  const loadTrace = useCallback(async () => {
    const key = traceKey.trim();
    if (!key) return;
    setTraceLoading(true);
    setTraceError(null);
    try {
      const llmPath = `traces/llm/${key}.jsonl`;
      const imagePath = `traces/image/${key}.jsonl`;

      const [llm, image] = await Promise.all([
        fetchResearchFileChunk("logs", llmPath, { offset: 0, length: 5000 }).catch(() => null),
        fetchResearchFileChunk("logs", imagePath, { offset: 0, length: 5000 }).catch(() => null),
      ]);

      const llmEvents = llm?.content ? parseJsonlEvents(llm.content, "llm") : [];
      const imageEvents = image?.content ? parseJsonlEvents(image.content, "image") : [];
      const all = [...llmEvents, ...imageEvents].sort((a, b) => {
        const atA = a.at_ms ?? Number.POSITIVE_INFINITY;
        const atB = b.at_ms ?? Number.POSITIVE_INFINITY;
        if (atA !== atB) return atA - atB;
        return a.kind.localeCompare(b.kind);
      });

      const summary: Record<string, { firstIndex: number; count: number }> = {};
      for (let idx = 0; idx < all.length; idx++) {
        const t = all[idx].task;
        const cur = summary[t];
        if (!cur) summary[t] = { firstIndex: idx, count: 1 };
        else summary[t] = { firstIndex: cur.firstIndex, count: cur.count + 1 };
      }

      setTraceLoadedKey(key);
      setTraceTaskSummary(summary);
      setTraceEventCount(all.length);
      setTraceEvents(all);

      if (all.length === 0) {
        setTraceError("trace が見つかりません（logs/traces/ に JSONL がありません）");
      }
    } catch (err) {
      setTraceLoadedKey(null);
      setTraceTaskSummary({});
      setTraceEventCount(0);
      setTraceEvents([]);
      setTraceError(err instanceof Error ? err.message : String(err));
    } finally {
      setTraceLoading(false);
    }
  }, [traceKey]);

  const clearTrace = () => {
    setTraceLoadedKey(null);
    setTraceTaskSummary({});
    setTraceEventCount(0);
    setTraceEvents([]);
    setTraceError(null);
  };

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

  const selectedOutputDecls = useMemo(() => (selectedNode ? parseOutputDecls(selectedNode.outputs) : []), [selectedNode]);

  const selectedPlaceholderPairs = useMemo(() => {
    if (!selectedNode) return [];
    const llm = selectedNode.llm as any;
    return parsePlaceholderPairs(llm?.placeholders);
  }, [selectedNode]);

  const selectedLlmCallsites = useMemo(() => {
    const task = (stageLlmTask || "").trim();
    if (!catalog || !task) return [];
    const list = (catalog.llm?.callsites || []).filter((c) => String((c as any).task || "") === task);
    return list.slice(0, 50);
  }, [catalog, stageLlmTask]);

  const selectedLlmTaskDef = useMemo(() => {
    const task = (stageLlmTask || "").trim();
    if (!catalog || !task) return null;
    const defs = (catalog.llm as any)?.task_defs as any;
    return defs && typeof defs === "object" ? defs[task] || null : null;
  }, [catalog, stageLlmTask]);

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
            <button type="button" className={`research-chip ${flow === "mainline" ? "is-active" : ""}`} onClick={() => setFlow("mainline")}>
              Mainline
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "planning" ? "is-active" : ""}`}
              onClick={() => setFlow("planning")}
              disabled={!catalog?.flows?.planning}
            >
              Planning
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "script_pipeline" ? "is-active" : ""}`}
              onClick={() => setFlow("script_pipeline")}
              disabled={!catalog?.flows?.script_pipeline}
            >
              Script Pipeline
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "audio_tts" ? "is-active" : ""}`}
              onClick={() => setFlow("audio_tts")}
              disabled={!catalog?.flows?.audio_tts}
            >
              Audio/TTS
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "video_auto_capcut_run" ? "is-active" : ""}`}
              onClick={() => setFlow("video_auto_capcut_run")}
              disabled={!catalog?.flows?.video_auto_capcut_run}
            >
              Video auto_capcut_run
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "thumbnails" ? "is-active" : ""}`}
              onClick={() => setFlow("thumbnails")}
              disabled={!catalog?.flows?.thumbnails}
            >
              Thumbnails
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "publish" ? "is-active" : ""}`}
              onClick={() => setFlow("publish")}
              disabled={!catalog?.flows?.publish}
            >
              Publish
            </button>
            <button type="button" className="research-chip" onClick={() => void loadCatalog(true)} disabled={loading}>
              {loading ? "更新中…" : "再生成"}
            </button>
            <button type="button" className={`research-chip ${focusMode ? "is-active" : ""}`} onClick={() => setFocusMode((v) => !v)}>
              {focusMode ? "List表示" : "Graph Focus"}
            </button>
          </div>
        </div>
      </header>

      <div className="research-body" style={focusMode ? { gridTemplateColumns: "1fr" } : undefined}>
        {!focusMode ? <div className="research-list">
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
                  onClick={() => {
                    setSelectedNodeId(n.node_id);
                    centerOnNode(n.node_id);
                  }}
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
        </div> : null}

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
                    className={`research-chip ${orientation === "horizontal" ? "is-active" : ""}`}
                    onClick={() => setOrientation("horizontal")}
                  >
                    横
                  </button>
                  <button
                    type="button"
                    className={`research-chip ${orientation === "vertical" ? "is-active" : ""}`}
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
                  <button type="button" className="research-chip" onClick={() => selectedNodeId && centerOnNode(selectedNodeId)} disabled={!selectedNodeId}>
                    Center
                  </button>
                </div>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
                <label className="muted small-text">Trace key</label>
                <input
                  list="ssot-trace-keys"
                  value={traceKey}
                  onChange={(e) => setTraceKey(e.target.value)}
                  placeholder="例: CH01-251"
                  style={{
                    width: 220,
                    borderRadius: 10,
                    border: "1px solid #d0d7de",
                    padding: "8px 10px",
                    fontSize: 13,
                    background: "#f6f8fa",
                  }}
                />
                <datalist id="ssot-trace-keys">
                  {traceKeySuggestions.map((k) => (
                    <option key={k.key} value={k.key} />
                  ))}
                </datalist>
                <button type="button" className="research-chip" onClick={() => void loadTraceKeyList()} disabled={traceListLoading}>
                  {traceListLoading ? "候補取得…" : traceKeySuggestions.length > 0 ? `候補(${traceKeySuggestions.length})` : "候補"}
                </button>
                <button type="button" className="research-chip" onClick={() => void loadTrace()} disabled={traceLoading || !traceKey.trim()}>
                  {traceLoading ? "読み込み中…" : "Load"}
                </button>
                <button type="button" className="research-chip" onClick={clearTrace} disabled={!traceLoadedKey && !traceError}>
                  Clear
                </button>
                {traceLoadedKey ? (
                  <Link className="research-chip" to={`/ssot/trace/${encodeURIComponent(traceLoadedKey)}`}>
                    Open Trace
                  </Link>
                ) : null}
                {traceLoadedKey ? (
                  <span className="mono muted small-text">
                    events={traceEventCount} / matched_tasks={traceMatchedTaskCount} / executed_nodes={Object.keys(executedByNodeId).length} / executed_edges={Object.keys(executedEdges).length}
                  </span>
                ) : null}
                <span className="crumb-sep" style={{ opacity: 0.4 }}>
                  /
                </span>
                <label className="muted small-text">Filter</label>
                <input
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="node_id / 名前 / 説明"
                  style={{
                    width: 260,
                    borderRadius: 10,
                    border: "1px solid #d0d7de",
                    padding: "8px 10px",
                    fontSize: 13,
                    background: "#f6f8fa",
                  }}
                />
                <button type="button" className="research-chip" onClick={() => setKeyword("")} disabled={!keyword.trim()}>
                  Filter Clear
                </button>
              </div>
              {focusMode && error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
              {focusMode && missingTasks.length > 0 ? (
                <div className="main-alert main-alert--warning">
                  LLMタスク定義が見つからないものがあります: <span className="mono">{missingTasks.join(", ")}</span>
                </div>
              ) : null}
              {traceError ? <div className="main-alert main-alert--warning">Trace: {traceError}</div> : null}
              {traceLoadedKey && traceUnmatchedTasks.length > 0 ? (
                <div className="muted small-text" style={{ marginTop: 6 }}>
                  このFlowに未マップの task（先頭のみ）: <span className="mono">{traceUnmatchedTasks.join(", ")}</span>
                </div>
              ) : null}
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
                      onSelect={(id) => {
                        setSelectedNodeId(id);
                        centerOnNode(id);
                      }}
                      orientation={orientation}
                      highlightedNodeIds={keyword.trim() ? filteredNodes.map((n) => n.node_id) : []}
                      onSize={handleGraphSize}
                      executed={traceLoadedKey ? executedByNodeId : undefined}
                      executedEdges={traceLoadedKey ? executedEdges : undefined}
                    />
                  </div>
                </div>
              </div>
              <div
                className="muted small-text"
                style={{ marginTop: 8, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
              >
                <span>クリックで詳細 / 凡例:</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(148, 163, 184, 0.14)", border: "1px solid rgba(148, 163, 184, 0.55)" }} />
                  Phase枠（複数ノード時）
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(255, 200, 0, 0.20)", border: "1px solid rgba(255, 200, 0, 0.55)" }} />
                  検索一致
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(67, 160, 71, 0.25)", border: "1px solid rgba(67, 160, 71, 0.80)" }} />
                  下流
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(156, 39, 176, 0.20)", border: "1px solid rgba(156, 39, 176, 0.75)" }} />
                  上流
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(14, 165, 233, 0.20)", border: "1px solid rgba(14, 165, 233, 0.75)" }} />
                  実行済み（Trace）
                </span>
              </div>

              <details style={{ marginTop: 12 }}>
                <summary style={{ cursor: "pointer", fontWeight: 900 }}>Flow Overview（このフェーズが何をするか）</summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  <div className="mono muted small-text">
                    flow_id={flowMeta?.flow_id || flow} / phase={(flowMeta as any)?.phase || "—"} / steps={nodes.length} / edges={edges.length} / llm_tasks={flowTaskList.length}
                  </div>
                  {(flowMeta as any)?.summary ? <div className="muted">{String((flowMeta as any).summary)}</div> : null}
                  {flowCodePaths.length > 0 ? (
                    <div className="muted small-text">
                      code: <span className="mono">{flowCodePaths.join(", ")}</span>
                    </div>
                  ) : null}
                  {catalog?.llm?.router_config?.path ? (
                    <div className="muted small-text">
                      llm_router: <span className="mono">{String(catalog.llm.router_config.path)}</span>
                      {catalog?.llm?.task_overrides?.path ? <span className="mono"> / overrides: {String(catalog.llm.task_overrides.path)}</span> : null}
                    </div>
                  ) : null}
                  {flowSotDecls.length > 0 ? (
                    <div>
                      <div className="muted small-text" style={{ marginBottom: 4 }}>
                        SoT（このFlowの正本パス）
                      </div>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {flowSotDecls.map((s, i) => (
                          <li key={`${flowMeta?.flow_id || flow}-sot-${i}`}>
                            <span className="mono">{s.path}</span>
                            {s.notes ? <span className="muted small-text"> — {s.notes}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {flowTaskList.length > 0 ? (
                    <details>
                      <summary className="muted small-text" style={{ cursor: "pointer" }}>
                        llm_tasks（このFlowで使うタスク）
                      </summary>
                      <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
                        {flowTaskList.map((t) => {
                          const def = ((catalog as any)?.llm?.task_defs || {})[t] as any;
                          const tier = def?.tier ? String(def.tier) : "";
                          const modelKeys = Array.isArray(def?.model_keys) ? (def.model_keys as string[]) : [];
                          const modelsShort = modelKeys.length > 0 ? modelKeys.slice(0, 5).join(", ") : "";
                          return (
                            <div key={t} className="mono muted small-text">
                              {t}
                              {tier ? `  tier=${tier}` : ""}
                              {modelsShort ? `  models=${modelsShort}${modelKeys.length > 5 ? ", …" : ""}` : ""}
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  ) : null}
                </div>
              </details>

              <details style={{ marginTop: 10 }}>
                <summary style={{ cursor: "pointer", fontWeight: 900 }}>Flow Runbook（全ステップ詳細）</summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  {nodes.map((s) => {
                    const llm = s.llm as any;
                    const task = llm?.task ? String(llm.task) : "";
                    const taskDef = task ? (((catalog as any)?.llm?.task_defs || {})[task] as any) : null;
                    const tier = taskDef?.tier ? String(taskDef.tier) : "";
                    const modelKeys = Array.isArray(taskDef?.model_keys) ? (taskDef.model_keys as any[]).map((x) => String(x)) : [];
                    const tpl = s.template as any;
                    const tplName = tpl?.name ? String(tpl.name) : llm?.template ? String(llm.template) : "";
                    const tplPath = tpl?.path ? String(tpl.path) : "";
                    const placeholders = parsePlaceholderPairs(llm?.placeholders);
                    const outputs = parseOutputDecls(s.outputs);
                    const sotPath = (s as any)?.sot?.path ? String((s as any).sot.path) : "";
                    const implRefs = Array.isArray((s as any).impl_refs) ? ((s as any).impl_refs as any[]) : [];
                    return (
                      <details
                        key={s.node_id}
                        style={{
                          border: "1px solid var(--color-border-muted)",
                          borderRadius: 12,
                          background: "var(--color-surface)",
                          padding: "10px 12px",
                        }}
                      >
                        <summary style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                          <span style={{ fontWeight: 900 }}>{nodeTitle(s)}</span>
                          <span className="mono muted small-text" style={{ whiteSpace: "nowrap" }}>
                            {task ? `task=${task}` : "no-llm"}
                          </span>
                        </summary>
                        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                          {s.description ? <div className="muted">{s.description}</div> : null}
                          {task ? (
                            <div className="mono muted small-text">
                              LLM: task={task}
                              {tplName ? ` / template=${tplName}` : ""}
                              {tplPath ? ` / template_path=${tplPath}` : ""}
                            </div>
                          ) : null}
                          {task && (tier || modelKeys.length > 0) ? (
                            <div className="mono muted small-text">
                              routing: {tier ? `tier=${tier}` : "tier=—"}
                              {modelKeys.length > 0 ? ` / models=${modelKeys.slice(0, 6).join(", ")}${modelKeys.length > 6 ? ", …" : ""}` : ""}
                            </div>
                          ) : null}
                          {placeholders.length > 0 ? (
                            <div>
                              <div className="muted small-text" style={{ marginBottom: 4 }}>
                                placeholders（プロンプトへ差し込まれる入力）
                              </div>
                              <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                                {placeholders.map((p) => `${p.key}: ${p.value}`).join("\n")}
                              </pre>
                            </div>
                          ) : null}
                          {outputs.length > 0 ? (
                            <div>
                              <div className="muted small-text" style={{ marginBottom: 4 }}>
                                outputs（生成/更新されるファイル）
                              </div>
                              <ul style={{ margin: 0, paddingLeft: 18 }}>
                                {outputs.map((o, i) => (
                                  <li key={`${s.node_id}-out-${i}`}>
                                    <span className="mono">{o.path}</span>
                                    {o.required === true ? <span className="muted small-text"> (required)</span> : null}
                                    {o.required === false ? <span className="muted small-text"> (optional)</span> : null}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {sotPath ? (
                            <div className="muted small-text">
                              SoT: <span className="mono">{sotPath}</span>
                            </div>
                          ) : null}
                          {implRefs.length > 0 ? (
                            <div className="muted small-text">
                              impl:{" "}
                              <span className="mono">
                                {implRefs
                                  .slice(0, 4)
                                  .map((r) => `${r.path}:${r.line}${r.symbol ? `(${r.symbol})` : ""}`)
                                  .join(", ")}
                              </span>
                              {implRefs.length > 4 ? <span className="muted small-text"> …</span> : null}
                            </div>
                          ) : null}
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <button
                              type="button"
                              className="research-chip"
                              onClick={() => {
                                setSelectedNodeId(s.node_id);
                                centerOnNode(s.node_id);
                              }}
                            >
                              Select
                            </button>
                            {tplPath ? (
                              <button type="button" className="research-chip" onClick={() => setSelectedNodeId(s.node_id)}>
                                Prompt
                              </button>
                            ) : null}
                          </div>
                        </div>
                      </details>
                    );
                  })}
                </div>
              </details>
            </section>

            {!selectedNode ? <div className="main-alert">Flow Graph のノードをクリックするか、左から選択してください。</div> : null}

            {selectedNode ? (
              <>
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>概要</h3>
                  <div className="mono muted">node_id: {selectedNode.node_id}</div>
                  {selectedNode.description ? <p style={{ marginBottom: 0 }}>{selectedNode.description}</p> : null}
                </section>

                {flow === "mainline" && selectedMainlineFlowMeta ? (
                  <section className="shell-panel shell-panel--placeholder">
                    <h3 style={{ marginTop: 0 }}>Phase Details（実装から合成）</h3>
                    <div className="mono muted small-text">
                      flow_id={(selectedMainlineFlowMeta as any)?.flow_id || "—"} / phase={(selectedMainlineFlowMeta as any)?.phase || "—"} / steps=
                      {Array.isArray((selectedMainlineFlowMeta as any)?.steps) ? (selectedMainlineFlowMeta as any).steps.length : 0}
                    </div>
                    {(selectedMainlineFlowMeta as any)?.summary ? <div className="muted" style={{ marginTop: 8 }}>{String((selectedMainlineFlowMeta as any).summary)}</div> : null}
                    {selectedMainlineCodePaths.length > 0 ? (
                      <div className="muted small-text" style={{ marginTop: 8 }}>
                        code: <span className="mono">{selectedMainlineCodePaths.join(", ")}</span>
                      </div>
                    ) : null}
                    {selectedMainlineSotDecls.length > 0 ? (
                      <div style={{ marginTop: 10 }}>
                        <div className="muted small-text" style={{ marginBottom: 4 }}>
                          SoT（正本）
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {selectedMainlineSotDecls.map((s, i) => (
                            <li key={`mainline-sot-${i}`}>
                              <span className="mono">{s.path}</span>
                              {s.notes ? <span className="muted small-text"> — {s.notes}</span> : null}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {selectedMainlineTasks.length > 0 ? (
                      <details style={{ marginTop: 10 }}>
                        <summary className="muted small-text" style={{ cursor: "pointer" }}>
                          LLM tasks（このPhaseで使うタスク）: {selectedMainlineTasks.length}
                        </summary>
                        <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
                          {selectedMainlineTasks.map((t) => {
                            const def = ((catalog as any)?.llm?.task_defs || {})[t] as any;
                            const tier = def?.tier ? String(def.tier) : "";
                            const modelKeys = Array.isArray(def?.model_keys) ? (def.model_keys as string[]) : [];
                            const modelsShort = modelKeys.length > 0 ? modelKeys.slice(0, 6).join(", ") : "";
                            return (
                              <div key={`mainline-task-${t}`} className="mono muted small-text">
                                {t}
                                {tier ? `  tier=${tier}` : ""}
                                {modelsShort ? `  models=${modelsShort}${modelKeys.length > 6 ? ", …" : ""}` : ""}
                              </div>
                            );
                          })}
                        </div>
                      </details>
                    ) : null}
                    {selectedMainlineFlow ? (
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                        <button
                          type="button"
                          className="research-chip"
                          onClick={() => {
                            setFlow(selectedMainlineFlow);
                            setSelectedNodeId(null);
                          }}
                        >
                          Open Flow
                        </button>
                      </div>
                    ) : null}
                  </section>
                ) : null}

              {selectedOutputDecls.length > 0 ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>Outputs（宣言）</h3>
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {selectedOutputDecls.map((o, i) => (
                      <li key={`${selectedNode.node_id}-out-${i}`}>
                        <span className="mono">{o.path}</span>
                        {o.required === true ? <span className="muted small-text"> (required)</span> : null}
                        {o.required === false ? <span className="muted small-text"> (optional)</span> : null}
                      </li>
                    ))}
                  </ul>
                  <details style={{ marginTop: 10 }}>
                    <summary className="muted small-text" style={{ cursor: "pointer" }}>
                      raw JSON
                    </summary>
                    <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                      {JSON.stringify(selectedNode.outputs, null, 2)}
                    </pre>
                  </details>
                </section>
              ) : null}

              {(selectedNode as any).sot ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>SoT（正本）</h3>
                  {(selectedNode as any)?.sot?.path ? (
                    <div className="mono muted small-text">path={String((selectedNode as any).sot.path)}</div>
                  ) : null}
                  <details style={{ marginTop: 10 }}>
                    <summary className="muted small-text" style={{ cursor: "pointer" }}>
                      raw JSON
                    </summary>
                    <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                      {JSON.stringify((selectedNode as any).sot, null, 2)}
                    </pre>
                  </details>
                </section>
              ) : null}

              {selectedNode.llm ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>LLM</h3>
                  {stageLlmTask ? <div className="mono muted small-text">task={stageLlmTask}</div> : null}
                  {(selectedNode.llm as any)?.template ? <div className="mono muted small-text">template={String((selectedNode.llm as any).template)}</div> : null}
                  {(selectedNode.llm as any)?.max_tokens ? (
                    <div className="mono muted small-text">max_tokens={String((selectedNode.llm as any).max_tokens)}</div>
                  ) : null}
                  {selectedLlmTaskDef ? (
                    <details style={{ marginTop: 10 }}>
                      <summary className="muted small-text" style={{ cursor: "pointer" }}>
                        routing（llm_routerから解決）
                      </summary>
                      <div className="mono muted small-text">tier={String((selectedLlmTaskDef as any)?.tier || "—")}</div>
                      {Array.isArray((selectedLlmTaskDef as any)?.model_keys) && (selectedLlmTaskDef as any).model_keys.length > 0 ? (
                        <div className="mono muted small-text">model_keys={String((selectedLlmTaskDef as any).model_keys.join(", "))}</div>
                      ) : null}
                      {Array.isArray((selectedLlmTaskDef as any)?.resolved_models) && (selectedLlmTaskDef as any).resolved_models.length > 0 ? (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {(selectedLlmTaskDef as any).resolved_models.map((m: any) => (
                            <li key={String(m?.key || "")}>
                              <span className="mono">{String(m?.key || "")}</span>
                              <span className="muted small-text">
                                {m?.provider ? ` provider=${String(m.provider)}` : ""}
                                {m?.model_name ? ` model=${String(m.model_name)}` : ""}
                                {m?.deployment ? ` deployment=${String(m.deployment)}` : ""}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {(selectedLlmTaskDef as any)?.router_task ? (
                        <details style={{ marginTop: 8 }}>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            router_task（configs/llm_router.yaml）
                          </summary>
                          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                            {JSON.stringify((selectedLlmTaskDef as any).router_task, null, 2)}
                          </pre>
                        </details>
                      ) : null}
                      {(selectedLlmTaskDef as any)?.override_task ? (
                        <details style={{ marginTop: 8 }}>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            override_task（llm_task_overrides）
                          </summary>
                          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                            {JSON.stringify((selectedLlmTaskDef as any).override_task, null, 2)}
                          </pre>
                        </details>
                      ) : null}
                    </details>
                  ) : null}
                  {selectedPlaceholderPairs.length > 0 ? (
                    <div style={{ marginTop: 10 }}>
                      <div className="muted small-text" style={{ marginBottom: 4 }}>
                        placeholders（プロンプトへ差し込まれる入力）
                      </div>
                      <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                        {selectedPlaceholderPairs.map((p) => `${p.key}: ${p.value}`).join("\n")}
                      </pre>
                    </div>
                  ) : null}
                  {selectedLlmCallsites.length > 0 ? (
                    <div style={{ marginTop: 10 }}>
                      <div className="muted small-text" style={{ marginBottom: 4 }}>
                        callsites（コード上の呼び出し箇所・先頭のみ）
                      </div>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {selectedLlmCallsites.map((c, i) => (
                          <li key={`${stageLlmTask || "llm"}-callsite-${i}`}>
                            <span className="mono">
                              {String((c as any)?.source?.path || "")}:{String((c as any)?.source?.line || "")}
                            </span>
                            <span className="muted small-text"> {String((c as any)?.call || "")}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <details style={{ marginTop: 10 }}>
                    <summary className="muted small-text" style={{ cursor: "pointer" }}>
                      raw JSON
                    </summary>
                    <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                      {JSON.stringify(selectedNode.llm, null, 2)}
                    </pre>
                  </details>
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
