import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type WheelEvent as ReactWheelEvent } from "react";
import { Link } from "react-router-dom";
import { fetchResearchFileChunk, fetchResearchList, fetchSsotCatalog } from "../api/client";
import type { ResearchFileEntry, SsotCatalog, SsotCatalogFlowStep } from "../api/types";
import { SsotFilePreview } from "./SsotFilePreview";
import { SsotFlowGraph } from "./SsotFlowGraph";

type FlowKey =
  | "mainline"
  | "system"
  | "planning"
  | "script_pipeline"
  | "audio_tts"
  | "video_auto_capcut_run"
  | "video_srt2images"
  | "remotion"
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

type TraceMessage = { role: string; content: string };

type TraceEvent = {
  kind: "llm" | "image";
  task: string;
  at_ms: number | null;
  provider?: string | null;
  model?: string | null;
  request_id?: string | null;
  messages?: TraceMessage[];
  prompt?: string | null;
};

type TraceEntry = TraceEvent & { index: number };

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
      const base: TraceEvent = {
        kind,
        task,
        at_ms: parseIsoMs(obj?.generated_at),
        provider: obj?.provider ? String(obj.provider) : null,
        model: obj?.model ? String(obj.model) : obj?.model_key ? String(obj.model_key) : null,
        request_id: obj?.request_id ? String(obj.request_id) : null,
        messages: undefined,
        prompt: null,
      };

      if (kind === "llm") {
        const msgs = Array.isArray(obj?.messages) ? (obj.messages as any[]) : [];
        const parsed = msgs
          .slice(0, 20)
          .map((m) => {
            const role = m?.role ? String(m.role) : "";
            const content = m?.content ? String(m.content) : "";
            if (!role || !content) return null;
            return { role, content };
          })
          .filter(Boolean) as TraceMessage[];
        base.messages = parsed.length > 0 ? parsed : undefined;
      } else {
        const p = obj?.input?.prompt ? String(obj.input.prompt) : "";
        base.prompt = p ? p : null;
      }

      out.push(base);
    } catch {
      // ignore
    }
  }
  return out;
}

function truncateText(text: string, maxChars: number): string {
  const s = String(text || "");
  if (maxChars <= 0) return "";
  if (s.length <= maxChars) return s;
  return `${s.slice(0, Math.max(0, maxChars - 1))}…`;
}

function formatTraceMessages(messages: TraceMessage[] | undefined, maxChars: number): string {
  const lines = (messages || []).map((m) => `${m.role}:\n${m.content}`);
  return truncateText(lines.join("\n\n"), maxChars);
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

function domIdForTimelineNode(nodeId: string): string {
  const safe = (nodeId || "").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 160);
  return `ssot-timeline-${safe || "unknown"}`;
}

function domIdForRunbookNode(nodeId: string): string {
  const safe = (nodeId || "").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 160);
  return `ssot-runbook-${safe || "unknown"}`;
}

function shortUiPath(path: string, keepParts = 3): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= keepParts) return normalized;
  return parts.slice(-keepParts).join("/");
}

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}

// Keep zoom-out usable while avoiding unreadably tiny text. For very large flows, prefer Timeline.
const MIN_GRAPH_SCALE = 0.05;
const MAX_GRAPH_SCALE = 3.0;
const GRAPH_ZOOM_STEP = 0.05;

export function SsotSystemMap() {
  const [catalog, setCatalog] = useState<SsotCatalog | null>(null);
  const [flow, setFlow] = useState<FlowKey>("mainline");
  const [flowView, setFlowView] = useState<"timeline" | "graph">("timeline");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<"horizontal" | "vertical">("vertical");
  const [graphScale, setGraphScale] = useState(1);
  const [graphSize, setGraphSize] = useState<{ width: number; height: number }>({ width: 640, height: 240 });
  const [autoFit, setAutoFit] = useState(true);
  const [fitClamped, setFitClamped] = useState(false);
  const [focusMode, setFocusMode] = useState(true);
  const [showQuickView, setShowQuickView] = useState(true);
  const [showCatalogPanel, setShowCatalogPanel] = useState(true);
  const [substepsParentNodeId, setSubstepsParentNodeId] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const graphViewportRef = useRef<HTMLDivElement | null>(null);
  const timelineViewportRef = useRef<HTMLDivElement | null>(null);
  const pendingFocusNodeIdRef = useRef<string | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const panStartRef = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  const [traceKey, setTraceKey] = useState("");
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [traceLoadedKey, setTraceLoadedKey] = useState<string | null>(null);
  const [traceTaskSummary, setTraceTaskSummary] = useState<Record<string, { firstIndex: number; count: number }>>({});
  const [traceEventCount, setTraceEventCount] = useState(0);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [traceIsPartial, setTraceIsPartial] = useState(false);
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

  const baseNodes: SsotCatalogFlowStep[] = useMemo(() => {
    if (!catalog) return [];
    if (flow === "mainline") return catalog.mainline.nodes || [];
    if (flow === "system") return catalog.system?.nodes || [];
    if (flow === "planning") return catalog.flows.planning?.steps || [];
    if (flow === "script_pipeline") return catalog.flows.script_pipeline?.steps || [];
    if (flow === "audio_tts") return catalog.flows.audio_tts?.steps || [];
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run?.steps || [];
    if (flow === "video_srt2images") return catalog.flows.video_srt2images?.steps || [];
    if (flow === "remotion") return catalog.flows.remotion?.steps || [];
    if (flow === "thumbnails") return catalog.flows.thumbnails?.steps || [];
    if (flow === "publish") return catalog.flows.publish?.steps || [];
    return [];
  }, [catalog, flow]);

  const baseEdges = useMemo(() => {
    if (!catalog) return [];
    if (flow === "mainline") return catalog.mainline.edges || [];
    if (flow === "system") return catalog.system?.edges || [];
    if (flow === "planning") return catalog.flows.planning?.edges || [];
    if (flow === "script_pipeline") return catalog.flows.script_pipeline?.edges || [];
    if (flow === "audio_tts") return catalog.flows.audio_tts?.edges || [];
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run?.edges || [];
    if (flow === "video_srt2images") return catalog.flows.video_srt2images?.edges || [];
    if (flow === "remotion") return catalog.flows.remotion?.edges || [];
    if (flow === "thumbnails") return catalog.flows.thumbnails?.edges || [];
    if (flow === "publish") return catalog.flows.publish?.edges || [];
    return [];
  }, [catalog, flow]);

  const substepsParent = useMemo(() => {
    const id = (substepsParentNodeId || "").trim();
    if (!id) return null;
    return baseNodes.find((n) => n.node_id === id) || null;
  }, [baseNodes, substepsParentNodeId]);

  const nodes: SsotCatalogFlowStep[] = useMemo(() => {
    const parentId = (substepsParentNodeId || "").trim();
    if (!parentId) return baseNodes;
    const parent = substepsParent;
    if (!parent) return baseNodes;
    const raw = (parent as any)?.substeps;
    const substeps = Array.isArray(raw) ? (raw as any[]) : [];
    if (substeps.length === 0) return baseNodes;
    return substeps
      .map((ss, idx) => {
        const sid = ss?.id ? String(ss.id) : ss?.node_id ? String(ss.node_id) : `substep#${idx + 1}`;
        const name = ss?.name ? String(ss.name) : sid;
        return {
          phase: String(parent.phase || ""),
          node_id: sid,
          order: idx + 1,
          name,
          description: ss?.description ? String(ss.description) : undefined,
          outputs: ss?.outputs,
          llm: ss?.llm,
          template: ss?.template ?? null,
          impl_refs: ss?.impl_refs,
          sot: ss?.sot,
        } as SsotCatalogFlowStep;
      })
      .filter((n) => Boolean((n.node_id || "").trim()));
  }, [baseNodes, substepsParent, substepsParentNodeId]);

  const edges = useMemo(() => {
    const parentId = (substepsParentNodeId || "").trim();
    if (!parentId) return baseEdges;
    if (nodes.length <= 1) return [];
    return nodes.slice(0, -1).map((n, idx) => ({ from: n.node_id, to: nodes[idx + 1].node_id, label: "next" }));
  }, [baseEdges, nodes, substepsParentNodeId]);

  const nodesForFlowKey = useCallback((k: FlowKey): SsotCatalogFlowStep[] => {
    if (!catalog) return [];
    if (k === "mainline") return catalog.mainline.nodes || [];
    if (k === "system") return catalog.system?.nodes || [];
    if (k === "planning") return catalog.flows.planning?.steps || [];
    if (k === "script_pipeline") return catalog.flows.script_pipeline?.steps || [];
    if (k === "audio_tts") return catalog.flows.audio_tts?.steps || [];
    if (k === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run?.steps || [];
    if (k === "video_srt2images") return catalog.flows.video_srt2images?.steps || [];
    if (k === "remotion") return catalog.flows.remotion?.steps || [];
    if (k === "thumbnails") return catalog.flows.thumbnails?.steps || [];
    if (k === "publish") return catalog.flows.publish?.steps || [];
    return [];
  }, [catalog]);

  const firstNodeIdForFlowKey = useCallback((k: FlowKey): string | null => {
    const list = nodesForFlowKey(k)
      .slice()
      .sort((a, b) => {
        const ao = typeof a.order === "number" ? a.order : 0;
        const bo = typeof b.order === "number" ? b.order : 0;
        if (ao !== bo) return ao - bo;
        return (a.node_id || "").localeCompare(b.node_id || "");
      });
    return list[0]?.node_id || null;
  }, [nodesForFlowKey]);

  const openFlow = useCallback((k: FlowKey) => {
    const first = firstNodeIdForFlowKey(k);
    setFlow(k);
    if (first) {
      setSelectedNodeId(first);
      pendingFocusNodeIdRef.current = first;
    } else {
      setSelectedNodeId(null);
      pendingFocusNodeIdRef.current = null;
    }
  }, [firstNodeIdForFlowKey]);

  const orderedNodes = useMemo(() => {
    return nodes
      .slice()
      .sort((a, b) => {
        const ao = typeof a.order === "number" ? a.order : 0;
        const bo = typeof b.order === "number" ? b.order : 0;
        if (ao !== bo) return ao - bo;
        return (a.node_id || "").localeCompare(b.node_id || "");
      });
  }, [nodes]);

  const mainlineTaskSets = useMemo(() => {
    if (!catalog) return {} as Record<string, Set<string>>;

    const collect = (m: any): Set<string> => {
      const out = new Set<string>();
      const steps = m?.steps;
      if (Array.isArray(steps)) {
        for (const s of steps) {
          const t = s?.llm?.task ? String(s.llm.task) : "";
          if (t) out.add(t);
        }
      }
      return out;
    };

    const out: Record<string, Set<string>> = {};
    out["A/planning"] = collect((catalog as any)?.flows?.planning);
    out["B/script_pipeline"] = collect((catalog as any)?.flows?.script_pipeline);
    out["C/audio_tts"] = collect((catalog as any)?.flows?.audio_tts);
    const d = new Set<string>();
    collect((catalog as any)?.flows?.video_srt2images).forEach((t) => d.add(t));
    collect((catalog as any)?.flows?.video_auto_capcut_run).forEach((t) => d.add(t));
    out["D/video"] = d;
    out["E/remotion"] = collect((catalog as any)?.flows?.remotion);
    out["F/thumbnails"] = collect((catalog as any)?.flows?.thumbnails);
    out["G/publish"] = collect((catalog as any)?.flows?.publish);
    return out;
  }, [catalog]);

  const flowTasks = useMemo(() => {
    const set = new Set<string>();
    if (flow === "mainline" && !substepsParentNodeId) {
      for (const taskSet of Object.values(mainlineTaskSets)) {
        taskSet.forEach((t) => set.add(t));
      }
      return set;
    }
    for (const n of nodes) {
      const llm = (n as any).llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (t) set.add(t);
    }
    return set;
  }, [flow, mainlineTaskSets, nodes, substepsParentNodeId]);

  const flowTaskList = useMemo(() => Array.from(flowTasks).sort((a, b) => a.localeCompare(b)), [flowTasks]);

  const flowMeta = useMemo(() => {
    if (!catalog) return null;
    if (flow === "mainline") return catalog.mainline as any;
    if (flow === "system") return catalog.system as any;
    if (flow === "planning") return catalog.flows.planning as any;
    if (flow === "script_pipeline") return catalog.flows.script_pipeline as any;
    if (flow === "audio_tts") return catalog.flows.audio_tts as any;
    if (flow === "video_auto_capcut_run") return catalog.flows.video_auto_capcut_run as any;
    if (flow === "video_srt2images") return catalog.flows.video_srt2images as any;
    if (flow === "remotion") return catalog.flows.remotion as any;
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
      m?.tool_path,
      m?.pipeline_path,
      m?.config_path,
      m?.templates_root,
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

  const flowEntrypoints = useMemo(() => {
    const raw = (flowMeta as any)?.entrypoints;
    if (!Array.isArray(raw)) return [];
    return raw.map((x) => String(x)).filter((s) => Boolean(s.trim()));
  }, [flowMeta]);

  const selectedMainlineFlow = useMemo<FlowKey | null>(() => {
    if (flow !== "mainline") return null;
    const id = (selectedNodeId || "").trim();
    if (!id) return null;
    if (id === "A/planning") return "planning";
    if (id === "B/script_pipeline") return "script_pipeline";
    if (id === "C/audio_tts") return "audio_tts";
    if (id === "D/video") return "video_auto_capcut_run";
    if (id === "E/remotion") return "remotion";
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
    if (selectedMainlineFlow === "remotion") return catalog.flows.remotion as any;
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
      m?.tool_path,
      m?.pipeline_path,
      m?.config_path,
      m?.templates_root,
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

  const selectedMainlineEntrypoints = useMemo(() => {
    const raw = (selectedMainlineFlowMeta as any)?.entrypoints;
    if (!Array.isArray(raw)) return [];
    return raw.map((x) => String(x)).filter((s) => Boolean(s.trim()));
  }, [selectedMainlineFlowMeta]);

  const executedByNodeId = useMemo(() => {
    if (!traceLoadedKey) return {};
    const out: Record<string, { firstIndex: number; count: number }> = {};
    if (flow === "mainline" && !substepsParentNodeId) {
      for (const n of nodes) {
        const taskSet = mainlineTaskSets[n.node_id];
        if (!taskSet || taskSet.size === 0) continue;
        let firstIndex = Number.POSITIVE_INFINITY;
        let count = 0;
        taskSet.forEach((t) => {
          const info = traceTaskSummary[t];
          if (!info) return;
          firstIndex = Math.min(firstIndex, info.firstIndex);
          count += info.count;
        });
        if (Number.isFinite(firstIndex) && count > 0) out[n.node_id] = { firstIndex, count };
      }
      return out;
    }
    for (const n of nodes) {
      const llm = (n as any).llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (!t) continue;
      const info = traceTaskSummary[t];
      if (info) out[n.node_id] = info;
    }
    return out;
  }, [flow, mainlineTaskSets, nodes, substepsParentNodeId, traceLoadedKey, traceTaskSummary]);

  const executedEdges = useMemo(() => {
    if (!traceLoadedKey) return {};
    if (!traceEvents || traceEvents.length === 0) return {};

    const taskToNodeId: Record<string, string> = {};
    if (flow === "mainline" && !substepsParentNodeId) {
      for (const [nodeId, taskSet] of Object.entries(mainlineTaskSets)) {
        taskSet.forEach((t) => {
          if (t && !taskToNodeId[t]) taskToNodeId[t] = nodeId;
        });
      }
    } else {
      for (const n of nodes) {
        const llm = (n as any).llm as any;
        const t = llm?.task ? String(llm.task) : "";
        if (!t) continue;
        if (!taskToNodeId[t]) taskToNodeId[t] = n.node_id;
      }
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
  }, [edges, flow, mainlineTaskSets, nodes, substepsParentNodeId, traceEvents, traceLoadedKey]);

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
    // Default to vertical to avoid horizontal overflow; users can switch to horizontal manually.
    setOrientation("vertical");
  }, [flow]);

  useEffect(() => {
    // In List mode, Graph needs width. Auto-collapse the catalog list panel when switching to Graph.
    if (focusMode) return;
    setShowCatalogPanel(flowView !== "graph");
  }, [focusMode, flowView]);

  useEffect(() => {
    // Reset drill-down scope when switching flows.
    setSubstepsParentNodeId(null);
  }, [flow]);

  const applyFitToSize = useCallback(
    (size: { width: number; height: number }) => {
      const el = graphViewportRef.current;
      if (!el) return;
      const w = el.clientWidth - 40;
      const h = el.clientHeight - 40;
      if (w <= 0 || h <= 0) return;
      const scale = Math.min(w / size.width, h / size.height);
      const next = clamp(Number(scale.toFixed(3)), MIN_GRAPH_SCALE, MAX_GRAPH_SCALE);
      setFitClamped(next <= MIN_GRAPH_SCALE + 1e-6);
      setGraphScale((prev) => (Math.abs(prev - next) < 0.001 ? prev : next));
    },
    [],
  );

  const centerGraph = useCallback(() => {
    const el = graphViewportRef.current;
    if (!el) return;
    const dx = Math.max(0, (el.scrollWidth - el.clientWidth) / 2);
    const dy = Math.max(0, (el.scrollHeight - el.clientHeight) / 2);
    el.scrollLeft = dx;
    el.scrollTop = dy;
  }, []);

  const zoomIn = () => {
    setAutoFit(false);
    setGraphScale((s) => clamp(Number((s + GRAPH_ZOOM_STEP).toFixed(2)), MIN_GRAPH_SCALE, MAX_GRAPH_SCALE));
  };
  const zoomOut = () => {
    setAutoFit(false);
    setGraphScale((s) => clamp(Number((s - GRAPH_ZOOM_STEP).toFixed(2)), MIN_GRAPH_SCALE, MAX_GRAPH_SCALE));
  };
  const zoomReset = () => {
    setAutoFit(false);
    setGraphScale(1);
  };
  const zoomFit = () => {
    setAutoFit(true);
    applyFitToSize(graphSize);
    requestAnimationFrame(() => centerGraph());
  };

  const zoomAtCursor = useCallback(
    (e: ReactWheelEvent<HTMLDivElement>) => {
      if (!e.ctrlKey && !e.metaKey) return;
      const el = graphViewportRef.current;
      if (!el) return;
      e.preventDefault();

      const rect = el.getBoundingClientRect();
      const cursorX = e.clientX - rect.left;
      const cursorY = e.clientY - rect.top;
      const direction = e.deltaY < 0 ? 1 : -1;

      setAutoFit(false);
      setGraphScale((prev) => {
        const next = clamp(
          Number((prev + direction * GRAPH_ZOOM_STEP).toFixed(3)),
          MIN_GRAPH_SCALE,
          MAX_GRAPH_SCALE,
        );
        if (Math.abs(next - prev) < 0.0001) return prev;

        const unscaledX = (el.scrollLeft + cursorX) / prev;
        const unscaledY = (el.scrollTop + cursorY) / prev;
        requestAnimationFrame(() => {
          el.scrollLeft = Math.max(0, unscaledX * next - cursorX);
          el.scrollTop = Math.max(0, unscaledY * next - cursorY);
        });
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    setAutoFit(true);
  }, [flow, orientation]);

  useEffect(() => {
    if (flowView !== "graph") return;
    if (!autoFit) return;
    requestAnimationFrame(() => {
      applyFitToSize(graphSize);
      requestAnimationFrame(() => centerGraph());
    });
  }, [applyFitToSize, autoFit, centerGraph, flowView, graphSize]);

  useEffect(() => {
    if (!autoFit) return;
    requestAnimationFrame(() => {
      applyFitToSize(graphSize);
      requestAnimationFrame(() => centerGraph());
    });
  }, [applyFitToSize, autoFit, centerGraph, graphSize]);

  useEffect(() => {
    if (!autoFit) return;
    if (typeof ResizeObserver === "undefined") return;
    const el = graphViewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      applyFitToSize(graphSize);
      requestAnimationFrame(() => centerGraph());
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [applyFitToSize, autoFit, centerGraph, graphSize]);

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

  const scrollTimelineToNode = useCallback((nodeId: string) => {
    const container = timelineViewportRef.current;
    if (!container) return;
    const el = container.querySelector(`#${domIdForTimelineNode(nodeId)}`) as HTMLElement | null;
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, []);

  const focusOnNode = useCallback(
    (nodeId: string) => {
      if (flowView === "graph") centerOnNode(nodeId);
      else scrollTimelineToNode(nodeId);
    },
    [centerOnNode, flowView, scrollTimelineToNode],
  );

  const openRunbookForNode = useCallback((nodeId: string) => {
    if (!nodeId) return;
    const runbook = document.getElementById("ssot-flow-runbook") as HTMLDetailsElement | null;
    if (runbook) runbook.open = true;
    const el = document.getElementById(domIdForRunbookNode(nodeId)) as HTMLDetailsElement | null;
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ block: "start", behavior: "smooth" });
  }, []);

  useEffect(() => {
    const id = pendingFocusNodeIdRef.current;
    if (!id) return;
    pendingFocusNodeIdRef.current = null;
    requestAnimationFrame(() => focusOnNode(id));
  }, [flow, flowView, focusOnNode]);

  const traceStartNodeId = useMemo(() => {
    if (!traceLoadedKey) return null;
    const entries = Object.entries(executedByNodeId || {});
    if (entries.length === 0) return null;
    const best = entries.reduce(
      (acc, [id, info]) => ((info?.firstIndex ?? 0) < (acc.info?.firstIndex ?? 0) ? { id, info } : acc),
      { id: entries[0][0], info: entries[0][1] },
    );
    return best.id || null;
  }, [executedByNodeId, traceLoadedKey]);

  const traceAutoAppliedKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (!traceLoadedKey) return;
    if (traceAutoAppliedKeyRef.current === traceLoadedKey) return;
    traceAutoAppliedKeyRef.current = traceLoadedKey;

    // Trace読込直後は「全体像」を優先して Fit する（startへ勝手に寄せると全体が見えなくなる）。
    setAutoFit(true);
    requestAnimationFrame(() => {
      applyFitToSize(graphSize);
      requestAnimationFrame(() => centerGraph());
    });

    if (traceStartNodeId) setSelectedNodeId(traceStartNodeId);
  }, [applyFitToSize, centerGraph, graphSize, traceLoadedKey, traceStartNodeId]);

  const beginPan = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    const container = graphViewportRef.current;
    if (!container) return;
    if (e.button !== 0) return;
    const target = e.target as HTMLElement | null;
    if (target && target.closest("[data-ssot-node-id]")) return;
    panStartRef.current = { x: e.clientX, y: e.clientY, left: container.scrollLeft, top: container.scrollTop };
    setIsPanning(true);
    e.preventDefault();
  }, []);

  const movePan = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    const container = graphViewportRef.current;
    const start = panStartRef.current;
    if (!container || !start) return;
    container.scrollLeft = start.left - (e.clientX - start.x);
    container.scrollTop = start.top - (e.clientY - start.y);
  }, []);

  const endPan = useCallback(() => {
    panStartRef.current = null;
    setIsPanning(false);
  }, []);

  const handleGraphSize = useCallback((size: { width: number; height: number }) => {
    if (!size.width || !size.height) return;
    setGraphSize((prev) => {
      if (prev.width === size.width && prev.height === size.height) return prev;
      return size;
    });
    if (!autoFit) return;
    requestAnimationFrame(() => {
      applyFitToSize(size);
      requestAnimationFrame(() => centerGraph());
    });
  }, [applyFitToSize, autoFit, centerGraph]);

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
      setTraceIsPartial(Boolean(llm?.is_partial || image?.is_partial));

      if (all.length === 0) {
        setTraceError("trace が見つかりません（logs/traces/ に JSONL がありません）");
      }
    } catch (err) {
      setTraceLoadedKey(null);
      setTraceTaskSummary({});
      setTraceEventCount(0);
      setTraceEvents([]);
      setTraceIsPartial(false);
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
    setTraceIsPartial(false);
    setTraceError(null);
  };

  const filteredNodes = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return nodes;
    return nodes.filter((n) => `${n.node_id} ${n.name} ${n.description || ""}`.toLowerCase().includes(q));
  }, [keyword, nodes]);

  const matchedNodeIds = useMemo(() => {
    if (!keyword.trim()) return new Set<string>();
    return new Set(filteredNodes.map((n) => n.node_id));
  }, [filteredNodes, keyword]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    return nodes.find((n) => n.node_id === selectedNodeId) ?? null;
  }, [nodes, selectedNodeId]);

  const selectedRelatedFlow = useMemo<FlowKey | null>(() => {
    if (!selectedNode) return null;
    const raw = selectedNode.related_flow;
    const v = typeof raw === "string" ? raw.trim() : "";
    if (!v) return null;
    if (v === "mainline") return "mainline";
    if (v === "planning") return "planning";
    if (v === "script_pipeline") return "script_pipeline";
    if (v === "audio_tts") return "audio_tts";
    if (v === "video_auto_capcut_run") return "video_auto_capcut_run";
    if (v === "video_srt2images") return "video_srt2images";
    if (v === "remotion") return "remotion";
    if (v === "thumbnails") return "thumbnails";
    if (v === "publish") return "publish";
    return null;
  }, [selectedNode]);

  useEffect(() => {
    if (nodes.length === 0) return;
    const cur = (selectedNodeId || "").trim();
    if (cur && nodes.some((n) => n.node_id === cur)) return;
    setSelectedNodeId(nodes[0].node_id);
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
    const lineRaw = tpl?.line;
    const line = lineRaw ? Number(lineRaw) : null;
    return p ? { path: p, line: Number.isFinite(line || NaN) ? line : null } : null;
  }, [selectedNode]);

  const stageLlmTask = useMemo(() => {
    if (!selectedNode) return null;
    const llm = selectedNode.llm as any;
    return llm?.task ? String(llm.task) : null;
  }, [selectedNode]);

  const stageTaskKind = useMemo(() => {
    if (!selectedNode) return null;
    const llm = selectedNode.llm as any;
    const kind = llm?.kind ? String(llm.kind) : "";
    return kind || null;
  }, [selectedNode]);

  const selectedOutputDecls = useMemo(() => (selectedNode ? parseOutputDecls(selectedNode.outputs) : []), [selectedNode]);

  const selectedSubsteps = useMemo(() => {
    if (!selectedNode) return [];
    const raw = (selectedNode as any).substeps;
    return Array.isArray(raw) ? raw : [];
  }, [selectedNode]);

  const enterSubstepsScope = useCallback(() => {
    if (!selectedNode) return;
    if (substepsParentNodeId) return;
    const raw = (selectedNode as any).substeps;
    const substeps = Array.isArray(raw) ? (raw as any[]) : [];
    if (substeps.length === 0) return;
    const first = substeps[0] as any;
    const firstId = first?.id ? String(first.id) : first?.node_id ? String(first.node_id) : "";
    setSubstepsParentNodeId(selectedNode.node_id);
    if (firstId) {
      setSelectedNodeId(firstId);
      pendingFocusNodeIdRef.current = firstId;
    } else {
      setSelectedNodeId(null);
      pendingFocusNodeIdRef.current = null;
    }
  }, [selectedNode, substepsParentNodeId]);

  const exitSubstepsScope = useCallback(() => {
    const parent = (substepsParentNodeId || "").trim();
    if (!parent) return;
    setSubstepsParentNodeId(null);
    setSelectedNodeId(parent);
    pendingFocusNodeIdRef.current = parent;
  }, [substepsParentNodeId]);

  const selectedPlaceholderPairs = useMemo(() => {
    if (!selectedNode) return [];
    const llm = selectedNode.llm as any;
    return parsePlaceholderPairs(llm?.placeholders);
  }, [selectedNode]);

  const selectedTaskCallsites = useMemo(() => {
    const task = (stageLlmTask || "").trim();
    if (!catalog || !task) return [];
    const pool = stageTaskKind === "image_client" ? catalog.image?.callsites || [] : catalog.llm?.callsites || [];
    const list = pool.filter((c) => String((c as any).task || "") === task);
    return list.slice(0, 50);
  }, [catalog, stageLlmTask, stageTaskKind]);

  const selectedTaskDef = useMemo(() => {
    const task = (stageLlmTask || "").trim();
    if (!catalog || !task) return null;
    const defs = stageTaskKind === "image_client" ? ((catalog.image as any)?.task_defs as any) : ((catalog.llm as any)?.task_defs as any);
    return defs && typeof defs === "object" ? defs[task] || null : null;
  }, [catalog, stageLlmTask, stageTaskKind]);

  const missingTasks = useMemo(() => catalog?.llm?.missing_task_defs || [], [catalog]);
  const missingImageTasks = useMemo(() => catalog?.image?.missing_task_defs || [], [catalog]);
  const policies = useMemo(() => catalog?.policies || [], [catalog]);

  const selectedTraceTasks = useMemo(() => {
    if (!selectedNode) return [];
    const tasks = new Set<string>();
    if (flow === "mainline" && !substepsParentNodeId) {
      const set = mainlineTaskSets[selectedNode.node_id];
      if (set) set.forEach((t) => (t ? tasks.add(String(t)) : null));
    } else {
      const llm = selectedNode.llm as any;
      const t = llm?.task ? String(llm.task) : "";
      if (t) tasks.add(t);
      for (const ss of selectedSubsteps) {
        const llm2 = (ss as any)?.llm as any;
        const t2 = llm2?.task ? String(llm2.task) : "";
        if (t2) tasks.add(t2);
      }
    }
    return Array.from(tasks)
      .map((t) => String(t).trim())
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }, [flow, mainlineTaskSets, selectedNode, selectedSubsteps, substepsParentNodeId]);

  const selectedTraceEntries = useMemo<TraceEntry[]>(() => {
    if (!traceLoadedKey) return [];
    if (!selectedNode) return [];
    if (!traceEvents || traceEvents.length === 0) return [];
    if (selectedTraceTasks.length === 0) return [];
    const taskSet = new Set(selectedTraceTasks);
    return traceEvents.map((e, index) => ({ ...e, index })).filter((e) => taskSet.has(e.task));
  }, [selectedNode, selectedTraceTasks, traceEvents, traceLoadedKey]);

  return (
    <section className="research-workspace research-workspace--wide ssot-map">
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
              className={`research-chip ${flow === "system" ? "is-active" : ""}`}
              onClick={() => setFlow("system")}
              disabled={!catalog?.system}
              title="全Flowのsteps/edgesを統合した“全体像”です（Graphは重いのでTimeline推奨）"
            >
              System
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
              className={`research-chip ${flow === "video_srt2images" ? "is-active" : ""}`}
              onClick={() => setFlow("video_srt2images")}
              disabled={!catalog?.flows?.video_srt2images}
            >
              Video srt2images
            </button>
            <button
              type="button"
              className={`research-chip ${flow === "remotion" ? "is-active" : ""}`}
              onClick={() => setFlow("remotion")}
              disabled={!catalog?.flows?.remotion}
            >
              Remotion
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
            <button type="button" className={`research-chip ${focusMode ? "is-active" : ""}`} onClick={() => setFocusMode(true)}>
              Graph Focus
            </button>
            <button type="button" className={`research-chip ${!focusMode ? "is-active" : ""}`} onClick={() => setFocusMode(false)}>
              List
            </button>
          </div>
        </div>
      </header>

      <div className="research-body" style={focusMode || (!focusMode && !showCatalogPanel) ? { gridTemplateColumns: "1fr" } : undefined}>
        {!focusMode && showCatalogPanel ? <div className="research-list">
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
          {missingImageTasks.length > 0 ? (
            <div className="main-alert main-alert--warning">
              Image task定義が見つからないものがあります: <span className="mono">{missingImageTasks.join(", ")}</span>
            </div>
          ) : null}
          <ul className="research-list__items">
            {filteredNodes.map((n) => (
              <li key={n.node_id}>
                <button
                  className="research-entry"
                  onClick={() => {
                    setSelectedNodeId(n.node_id);
                    focusOnNode(n.node_id);
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
                <h3 style={{ marginTop: 0 }}>Flow</h3>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" className={`research-chip ${flowView === "timeline" ? "is-active" : ""}`} onClick={() => setFlowView("timeline")}>
                    Timeline
                  </button>
                  <button type="button" className={`research-chip ${flowView === "graph" ? "is-active" : ""}`} onClick={() => setFlowView("graph")}>
                    Graph
                  </button>
                  {selectedSubsteps.length > 0 || substepsParentNodeId ? (
                    <>
                      <button
                        type="button"
                        className={`research-chip ${!substepsParentNodeId ? "is-active" : ""}`}
                        onClick={exitSubstepsScope}
                        title="Flowのステップ一覧に戻します"
                      >
                        Steps
                      </button>
                      <button
                        type="button"
                        className={`research-chip ${substepsParentNodeId ? "is-active" : ""}`}
                        onClick={enterSubstepsScope}
                        disabled={Boolean(substepsParentNodeId) || selectedSubsteps.length === 0}
                        title="選択ノードの substeps（内部処理）にドリルダウンします"
                      >
                        Substeps
                      </button>
                      {substepsParentNodeId ? (
                        <span className="mono muted small-text" style={{ alignSelf: "center", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          of {substepsParentNodeId}
                        </span>
                      ) : null}
                    </>
                  ) : null}
                  {flowView === "graph" ? (
                    <>
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
                      <button
                        type="button"
                        className={`research-chip ${autoFit ? "is-active" : ""}`}
                        onClick={() => setAutoFit((v) => !v)}
                        title="ONの間は表示領域に合わせて自動で拡大縮小します"
                      >
                        AutoFit
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
                      {!focusMode ? (
                        <button
                          type="button"
                          className={`research-chip ${showCatalogPanel ? "is-active" : ""}`}
                          onClick={() => setShowCatalogPanel((v) => !v)}
                          title="左のカタログ（ノード一覧）を表示/非表示"
                        >
                          Catalog
                        </button>
                      ) : null}
                      <button type="button" className="research-chip" onClick={() => selectedNodeId && focusOnNode(selectedNodeId)} disabled={!selectedNodeId}>
                        Center
                      </button>
                      <button
                        type="button"
                        className={`research-chip ${showQuickView ? "is-active" : ""}`}
                        onClick={() => setShowQuickView((v) => !v)}
                        title="Selectedパネル（目的/LLM/Prompt/Outputs）を表示/非表示"
                      >
                        Details
                      </button>
                    </>
                  ) : (
                    <button type="button" className="research-chip" onClick={() => selectedNodeId && focusOnNode(selectedNodeId)} disabled={!selectedNodeId}>
                      Scroll
                    </button>
                  )}
                  <button
                    type="button"
                    className="research-chip"
                    onClick={() => {
                      if (!traceStartNodeId) return;
                      setSelectedNodeId(traceStartNodeId);
                      focusOnNode(traceStartNodeId);
                    }}
                    disabled={!traceStartNodeId}
                    title="Traceの最初に実行されたノードへ移動します"
                  >
                    Trace Start
                  </button>
                </div>
              </div>
              {flowView === "graph" ? (
                <div className="muted small-text" style={{ marginTop: 8 }}>
                  Drag to pan / Ctrl(or ⌘)+Wheel to zoom / Double-click to Fit
                </div>
              ) : null}
              {flowView === "graph" && autoFit && fitClamped ? (
                <div className="main-alert main-alert--warning" style={{ marginTop: 10 }}>
                  Graphが大きく、Fitが最小倍率（{MIN_GRAPH_SCALE.toFixed(2)}x）に到達しています。読みやすさ優先なら{" "}
                  <span className="mono">Timeline</span> に切替 or <span className="mono">Substeps</span> で分割してください。
                </div>
              ) : null}
              {focusMode || (flowView === "graph" && !showCatalogPanel) ? (
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
                  <label className="muted small-text">Filter</label>
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="node_id / 名前 / 説明"
                    style={{
                      width: 280,
                      maxWidth: "100%",
                      flex: "1 1 280px",
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
              ) : null}

              <details style={{ marginTop: 10 }}>
                <summary
                  style={{
                    cursor: "pointer",
                    fontWeight: 900,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 10,
                    flexWrap: "wrap",
                  }}
                >
                  <span>Trace（実行ログでハイライト）</span>
                  {traceLoadedKey ? (
                    <span className="mono muted small-text">
                      loaded: {traceLoadedKey}
                      {traceIsPartial ? " (partial)" : ""} / events={traceEventCount} / matched_tasks={traceMatchedTaskCount} /
                      executed_nodes={Object.keys(executedByNodeId).length} / executed_edges={Object.keys(executedEdges).length}
                    </span>
                  ) : (
                    <span className="muted small-text">任意: logs/traces/ の JSONL から実行済みを表示</span>
                  )}
                </summary>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
                  <label className="muted small-text">Trace key</label>
                  <input
                    list="ssot-trace-keys"
                    value={traceKey}
                    onChange={(e) => setTraceKey(e.target.value)}
                    placeholder="例: CH01-251"
                    style={{
                      width: 220,
                      maxWidth: "100%",
                      flex: "1 1 220px",
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
                </div>
                {traceError ? <div className="main-alert main-alert--warning">Trace: {traceError}</div> : null}
                {traceLoadedKey && traceUnmatchedTasks.length > 0 ? (
                  <div className="muted small-text" style={{ marginTop: 6 }}>
                    このFlowに未マップの task（先頭のみ）: <span className="mono">{traceUnmatchedTasks.join(", ")}</span>
                  </div>
                ) : null}
              </details>
              {focusMode && error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
              {focusMode && missingTasks.length > 0 ? (
                <div className="main-alert main-alert--warning">
                  LLMタスク定義が見つからないものがあります: <span className="mono">{missingTasks.join(", ")}</span>
                </div>
              ) : null}
              {focusMode && missingImageTasks.length > 0 ? (
                <div className="main-alert main-alert--warning">
                  Image task定義が見つからないものがあります: <span className="mono">{missingImageTasks.join(", ")}</span>
                </div>
              ) : null}
              <details style={{ marginTop: 12 }} open>
                <summary style={{ cursor: "pointer", fontWeight: 900 }}>Flow Overview（このフェーズが何をするか）</summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  <div className="mono muted small-text">
                    flow_id={flowMeta?.flow_id || flow} / phase={(flowMeta as any)?.phase || "—"} / steps={nodes.length} / edges={edges.length} / llm_tasks={flowTaskList.length}
                  </div>
                  {substepsParentNodeId ? (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <span className="badge subtle">scope</span>
                      <span className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                        substeps of {substepsParent ? nodeTitle(substepsParent) : substepsParentNodeId}
                      </span>
                      <button type="button" className="research-chip" onClick={exitSubstepsScope}>
                        Back
                      </button>
                    </div>
                  ) : null}
                  {(flowMeta as any)?.summary ? <div className="muted" style={{ whiteSpace: "pre-wrap" }}>{String((flowMeta as any).summary)}</div> : null}
                  {policies.length > 0 ? (
                    <details open>
                      <summary style={{ cursor: "pointer", fontWeight: 900 }}>Policies（絶対ルール）</summary>
                      <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                        {policies.map((p) => {
                          const refs = Array.isArray(p.impl_refs) ? p.impl_refs : [];
                          return (
                            <div
                              key={String(p.id || "policy")}
                              className="research-quick"
                              style={{ display: "grid", gap: 8 }}
                            >
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                                <div style={{ fontWeight: 900, minWidth: 0 }}>
                                  {String(p.title || p.id || "policy")}
                                </div>
                                <span className="badge subtle">{String(p.id || "")}</span>
                              </div>
                              {p.description ? (
                                <div className="muted" style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
                                  {String(p.description)}
                                </div>
                              ) : null}
                              {refs.length > 0 ? (
                                <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                                  impl:{" "}
                                  {refs
                                    .slice(0, 4)
                                    .map((r) => `${String(r.path)}:${Number(r.line) || 1}${r.symbol ? `(${String(r.symbol)})` : ""}`)
                                    .join(", ")}
                                  {refs.length > 4 ? ", …" : ""}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  ) : null}
                  {nodes.length > 0 ? (
                    <div>
                      <div className="muted small-text" style={{ marginBottom: 6 }}>
                        Steps（クリックでノード選択）
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
                        {nodes
                          .slice()
                          .sort((a, b) => {
                            const ao = typeof a.order === "number" ? a.order : 0;
                            const bo = typeof b.order === "number" ? b.order : 0;
                            if (ao !== bo) return ao - bo;
                            return (a.node_id || "").localeCompare(b.node_id || "");
                          })
                          .map((s) => {
                            const order = s.order ? String(s.order).padStart(2, "0") : "";
                            const prefix = s.phase && order ? `${s.phase}-${order}` : s.phase || "";
                            const llm = s.llm as any;
                            const task = llm?.task ? String(llm.task) : "";
                            const kind = llm?.kind ? String(llm.kind) : "";
                            const mode = task ? (kind === "image_client" ? "IMAGE" : "LLM") : "CODE";
                            const taskDefs = kind === "image_client" ? ((catalog as any)?.image?.task_defs || {}) : ((catalog as any)?.llm?.task_defs || {});
                            const taskDef = task ? (taskDefs[task] as any) : null;
                            const tier = taskDef?.tier ? String(taskDef.tier) : "";
                            const modelKeys = Array.isArray(taskDef?.model_keys) ? (taskDef.model_keys as string[]) : [];
                            const modelHint = tier ? `tier=${tier}` : modelKeys.length > 0 ? `models=${modelKeys.slice(0, 2).join(", ")}${modelKeys.length > 2 ? ", …" : ""}` : "";
                            const outs = parseOutputDecls(s.outputs).slice(0, 2);
                            return (
                              <button
                                key={`flow-step-${s.node_id}`}
                                type="button"
                                onClick={() => {
                                  setSelectedNodeId(s.node_id);
                                  focusOnNode(s.node_id);
                                }}
                                style={{
                                  border: `1px solid ${selectedNodeId === s.node_id ? "rgba(29, 78, 216, 0.55)" : "var(--color-border-muted)"}`,
                                  background: "var(--color-surface)",
                                  borderRadius: 12,
                                  padding: 10,
                                  textAlign: "left",
                                  color: "var(--color-text-strong)",
                                  display: "grid",
                                  gap: 6,
                                  minWidth: 0,
                                  transform: "none",
                                  transition: "none",
                                }}
                              >
                                <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
                                  {prefix ? (
                                    <span
                                      className="mono"
                                      style={{
                                        flex: "none",
                                        fontSize: 11,
                                        fontWeight: 800,
                                        padding: "2px 8px",
                                        borderRadius: 999,
                                        border: "1px solid rgba(15, 23, 42, 0.14)",
                                        background: "rgba(15, 23, 42, 0.06)",
                                        color: "rgba(15, 23, 42, 0.92)",
                                      }}
                                    >
                                      {prefix}
                                    </span>
                                  ) : null}
                                  <div style={{ fontWeight: 900, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {s.name || s.node_id}
                                  </div>
                                </div>
                                <div className="mono muted small-text" style={{ lineHeight: 1.35, overflowWrap: "anywhere" }}>
                                  {mode}
                                  {task ? ` task=${task}` : ""}
                                  {modelHint ? ` / ${modelHint}` : ""}
                                </div>
                                {s.description ? (
                                  <div
                                    className="muted small-text"
                                    style={{
                                      lineHeight: 1.45,
                                      display: "-webkit-box",
                                      WebkitLineClamp: 2,
                                      WebkitBoxOrient: "vertical",
                                      overflow: "hidden",
                                    }}
                                  >
                                    {s.description}
                                  </div>
                                ) : null}
                                {outs.length > 0 ? (
                                  <div className="mono muted small-text" style={{ lineHeight: 1.35, overflowWrap: "anywhere" }}>
                                    out: {outs.map((o) => o.path).join(", ")}
                                  </div>
                                ) : null}
                              </button>
                            );
                          })}
                      </div>
                    </div>
                  ) : null}
                  {flowCodePaths.length > 0 ? (
                    <div className="muted small-text">
                      code: <span className="mono">{flowCodePaths.join(", ")}</span>
                    </div>
                  ) : null}
                  {flowEntrypoints.length > 0 ? (
                    <div>
                      <div className="muted small-text" style={{ marginBottom: 4 }}>
                        Entrypoints（入口）
                      </div>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {flowEntrypoints.map((e) => (
                          <li key={`flow-entry-${e}`} className="mono muted small-text">
                            {e}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {catalog?.llm?.router_config?.path ? (
                    <div className="muted small-text">
                      llm_router: <span className="mono">{String(catalog.llm.router_config.path)}</span>
                      {catalog?.llm?.task_overrides?.path ? <span className="mono"> / overrides: {String(catalog.llm.task_overrides.path)}</span> : null}
                    </div>
                  ) : null}
                  {catalog?.image?.router_config?.path ? (
                    <div className="muted small-text">
                      image_models: <span className="mono">{String(catalog.image.router_config.path)}</span>
                      {catalog?.image?.task_overrides?.path ? <span className="mono"> / overrides: {String(catalog.image.task_overrides.path)}</span> : null}
                      {catalog?.image?.task_overrides?.profile ? <span className="mono"> / profile: {String(catalog.image.task_overrides.profile)}</span> : null}
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
                          const imageDef = ((catalog as any)?.image?.task_defs || {})[t] as any;
                          const llmDef = ((catalog as any)?.llm?.task_defs || {})[t] as any;
                          const def = imageDef || llmDef;
                          const kind = imageDef ? "image" : "llm";
                          const tier = def?.tier ? String(def.tier) : "";
                          const modelKeys = Array.isArray(def?.model_keys) ? (def.model_keys as string[]) : [];
                          const modelsShort = modelKeys.length > 0 ? modelKeys.slice(0, 5).join(", ") : "";
                          return (
                            <div key={t} className="mono muted small-text">
                              {t} {kind === "image" ? "(image)" : ""}
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
              <div
                className={`ssot-graph-split${showQuickView ? "" : " ssot-graph-split--single"}${focusMode ? " ssot-graph-split--focus" : " ssot-graph-split--stack"}`}
                style={{ marginTop: 10 }}
              >
                {flowView === "graph" ? (
                  <div
                    ref={graphViewportRef}
                    onMouseDown={beginPan}
                    onMouseMove={movePan}
                    onMouseUp={endPan}
                    onMouseLeave={endPan}
                    onWheel={zoomAtCursor}
                    onDoubleClick={zoomFit}
                    style={{
                      border: "1px solid var(--color-border-muted)",
                      borderRadius: 14,
                      background: "#f8fafc",
                      overflow: "auto",
                      height: "65vh",
                      minHeight: 360,
                      maxHeight: 860,
                      cursor: isPanning ? "grabbing" : "grab",
                      userSelect: "none",
                      minWidth: 0,
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
                            focusOnNode(id);
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
                ) : (
                  <div
                    ref={timelineViewportRef}
                    style={{
                      border: "1px solid var(--color-border-muted)",
                      borderRadius: 14,
                      background: "#f8fafc",
                      overflow: "auto",
                      height: "65vh",
                      minHeight: 360,
                      maxHeight: 860,
                      padding: 12,
                      minWidth: 0,
                    }}
                  >
                    <div className="ssot-timeline">
                      {orderedNodes.map((s, idx) => {
                        const isSelected = selectedNodeId === s.node_id;
                        const isMatch = matchedNodeIds.has(s.node_id);
                        const exec = traceLoadedKey ? executedByNodeId[s.node_id] : undefined;
                        const isExec = Boolean(exec);

                        const order = typeof s.order === "number" ? String(s.order).padStart(2, "0") : "";
                        const badge = s.phase && order ? `${s.phase}-${order}` : s.phase || "";

                        const llm = s.llm as any;
                        const task = llm?.task ? String(llm.task) : "";
                        const kind = llm?.kind ? String(llm.kind) : "";
                        const mode = task ? (kind === "image_client" ? "IMAGE" : "LLM") : "CODE";
                        const taskDefs = kind === "image_client" ? ((catalog as any)?.image?.task_defs || {}) : ((catalog as any)?.llm?.task_defs || {});
                        const taskDef = task ? (taskDefs[task] as any) : null;
                        const resolved = Array.isArray(taskDef?.resolved_models) ? (taskDef.resolved_models[0] as any) : null;
                        const resolvedLabel = resolved
                          ? (() => {
                              const provider = resolved?.provider ? String(resolved.provider) : "";
                              const model = resolved?.model_name ? String(resolved.model_name) : resolved?.key ? String(resolved.key) : "";
                              const dep = resolved?.deployment ? String(resolved.deployment) : "";
                              const base = provider && model ? `${provider}:${model}` : model || provider;
                              return base ? `${base}${dep ? `(${dep})` : ""}` : "";
                            })()
                          : "";

                        const outputsAll = parseOutputDecls(s.outputs);
                        const outCount = outputsAll.length;
                        const outFirst = outCount > 0 ? String(outputsAll[0]?.path || "") : "";
                        const outLabel = outCount > 0 ? `${shortUiPath(outFirst, 3)}${outCount > 1 ? ` +${outCount - 1}` : ""}` : "";
                        const outTitle = outCount > 0 ? outputsAll.slice(0, 10).map((o) => o.path).join("\n") + (outCount > 10 ? "\n…" : "") : "";

                        const substeps = Array.isArray((s as any).substeps) ? (((s as any).substeps as any[]) || []) : [];
                        const related = typeof s.related_flow === "string" ? String(s.related_flow) : "";
                        const dotLabel = order ? order : String(idx + 1).padStart(2, "0");
                        const substepsTitle = substeps.length > 0
                          ? substeps
                              .slice(0, 8)
                              .map((x: any) => String(x?.name || x?.id || ""))
                              .filter(Boolean)
                              .join("\n") + (substeps.length > 8 ? "\n…" : "")
                          : "";

                        const classes = ["ssot-timeline-card"];
                        if (isSelected) classes.push("is-selected");
                        else if (isExec) classes.push("is-exec");
                        else if (isMatch) classes.push("is-match");

                        return (
                          <div key={s.node_id} className="ssot-timeline-step">
                            <div className="ssot-timeline-rail" aria-hidden="true">
                              <div className={`ssot-timeline-dot${isSelected ? " is-selected" : isExec ? " is-exec" : isMatch ? " is-match" : ""}`}>
                                <span className="ssot-timeline-dottext">{dotLabel}</span>
                              </div>
                              {idx < orderedNodes.length - 1 ? <div className="ssot-timeline-line" /> : null}
                            </div>
                            <button
                              id={domIdForTimelineNode(s.node_id)}
                              type="button"
                              className={classes.join(" ")}
                              onClick={() => {
                                setSelectedNodeId(s.node_id);
                                focusOnNode(s.node_id);
                              }}
                              title={`${s.node_id}${s.description ? `\n${s.description}` : ""}`}
                            >
                              <div className="ssot-timeline-header">
                                {badge ? <span className="mono ssot-timeline-badge">{badge}</span> : null}
                                <div className="ssot-timeline-title">{s.name || s.node_id}</div>
                                {isExec ? (
                                  <span className="mono ssot-timeline-exec">
                                    run#{(exec?.firstIndex ?? 0) + 1}×{exec?.count ?? 1}
                                  </span>
                                ) : null}
                              </div>
                              <div className="ssot-timeline-meta">
                                <span className="ssot-timeline-tag mono" title={s.node_id}>
                                  <span className="ssot-timeline-tag__label">id</span>
                                  <span>{s.node_id}</span>
                                </span>
                                <span className={`ssot-timeline-tag mono ssot-timeline-tag--${mode.toLowerCase()}`}>{mode}</span>
                                {task ? (
                                  <span className="ssot-timeline-tag mono" title={task}>
                                    <span className="ssot-timeline-tag__label">task</span>
                                    <span>{task}</span>
                                  </span>
                                ) : null}
                                {resolvedLabel ? (
                                  <span className="ssot-timeline-tag mono" title={resolvedLabel}>
                                    <span className="ssot-timeline-tag__label">model</span>
                                    <span>{resolvedLabel}</span>
                                  </span>
                                ) : null}
                                {outCount > 0 ? (
                                  <span className="ssot-timeline-tag mono" title={outTitle ? `out:\n${outTitle}` : undefined}>
                                    <span className="ssot-timeline-tag__label">out</span>
                                    <span>{outLabel || String(outCount)}</span>
                                  </span>
                                ) : null}
                                {substeps.length > 0 ? (
                                  <span className="ssot-timeline-tag mono" title={substepsTitle || undefined}>
                                    <span className="ssot-timeline-tag__label">substeps</span>
                                    <span>{substeps.length}</span>
                                  </span>
                                ) : null}
                                {related ? (
                                  <span className="ssot-timeline-tag mono">
                                    <span className="ssot-timeline-tag__label">open</span>
                                    <span>{related}</span>
                                  </span>
                                ) : null}
                              </div>
                              {s.description ? (
                                <div
                                  className="ssot-timeline-desc"
                                  style={{
                                    lineHeight: 1.6,
                                    display: "-webkit-box",
                                    WebkitLineClamp: 2,
                                    WebkitBoxOrient: "vertical",
                                    overflow: "hidden",
                                    whiteSpace: "pre-wrap",
                                  }}
                                >
                                  {s.description}
                                </div>
                              ) : null}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {showQuickView ? (
                  <aside
                    style={{
                      border: "1px solid var(--color-border-muted)",
                      borderRadius: 14,
                      background: "var(--color-surface)",
                      padding: 12,
                      height: "65vh",
                      minHeight: 360,
                      maxHeight: 860,
                      overflow: "auto",
                      minWidth: 0,
                    }}
                  >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                    <h4 style={{ margin: 0 }}>Selected（クイックビュー）</h4>
                    {selectedNode ? <span className="badge subtle">node</span> : <span className="badge subtle">help</span>}
                  </div>
                  {!selectedNode ? (
                    <div className="muted small-text" style={{ marginTop: 10, lineHeight: 1.6 }}>
                      1) Flow Graph のノードをクリック → 2) 右側に要点（目的/LLM/Prompt/Outputs）を表示 → 3) 下にスクロールすると詳細（Implementation Sources など）を確認できます。
                    </div>
                  ) : (
                    <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                      <div>
                        <div style={{ fontWeight: 900 }}>{nodeTitle(selectedNode)}</div>
                        {selectedNode.description ? (
                          <div className="muted" style={{ marginTop: 6, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                            {selectedNode.description}
                          </div>
                        ) : null}
                      </div>

                      <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                        node_id={selectedNode.node_id}
                        {typeof selectedNode.order === "number" ? ` / order=${selectedNode.order}` : ""}
                        {selectedNode.phase ? ` / phase=${selectedNode.phase}` : ""}
                      </div>

                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button type="button" className="research-chip" onClick={() => openRunbookForNode(selectedNode.node_id)}>
                          Runbook
                        </button>
                      </div>

                      {selectedRelatedFlow ? (
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="research-chip"
                            onClick={() => openFlow(selectedRelatedFlow)}
                            disabled={flow === selectedRelatedFlow}
                            title="このノードの中身（関連フロー）へ移動します"
                          >
                            Open flow: {selectedRelatedFlow}
                          </button>
                        </div>
                      ) : null}

                        {stageLlmTask ? (
                          <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                            {stageTaskKind === "image_client" ? "IMAGE" : "LLM"} task={stageLlmTask}
                            {selectedTaskDef?.tier ? ` / tier=${String(selectedTaskDef.tier)}` : ""}
                            {Array.isArray((selectedTaskDef as any)?.model_keys) && (selectedTaskDef as any).model_keys.length > 0
                              ? ` / models=${String((selectedTaskDef as any).model_keys.slice(0, 3).join(", "))}${(selectedTaskDef as any).model_keys.length > 3 ? ", …" : ""}`
                              : ""}
                            {(selectedTaskDef as any)?.override_profile ? ` / profile=${String((selectedTaskDef as any).override_profile)}` : ""}
                            {typeof (selectedTaskDef as any)?.allow_fallback === "boolean"
                              ? ` / allow_fallback=${String(Boolean((selectedTaskDef as any).allow_fallback))}`
                              : ""}
                            {Array.isArray((selectedTaskDef as any)?.resolved_models) && (selectedTaskDef as any).resolved_models.length > 0
                              ? (() => {
                                  const m = (selectedTaskDef as any).resolved_models[0] || {};
                                  const provider = m?.provider ? String(m.provider) : "";
                                  const model = m?.model_name ? String(m.model_name) : m?.key ? String(m.key) : "";
                                  const dep = m?.deployment ? String(m.deployment) : "";
                                  const base = provider && model ? `${provider}:${model}` : model || provider;
                                  return base ? ` / resolved=${dep ? `${base}(${dep})` : base}` : "";
                                })()
                              : ""}
                          </div>
                        ) : (
                          <div className="mono muted small-text">CODE step（LLMなし）</div>
                        )}

                        {traceLoadedKey && selectedNode.node_id && executedByNodeId[selectedNode.node_id] ? (
                          <div className="mono muted small-text">
                            trace: run#{(executedByNodeId[selectedNode.node_id]?.firstIndex ?? 0) + 1} ×{executedByNodeId[selectedNode.node_id]?.count ?? 1}
                          </div>
                        ) : null}

                        {traceLoadedKey ? (
                          <details open={selectedTraceEntries.length > 0 && selectedTraceEntries.length <= 1}>
                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                              Trace Prompts（実行ログ）: {selectedTraceEntries.length || 0}
                            </summary>
                            <div className="mono muted small-text" style={{ marginTop: 8, overflowWrap: "anywhere" }}>
                              loaded={traceLoadedKey}
                              {traceIsPartial ? " (partial)" : ""}
                              {selectedTraceTasks.length > 0
                                ? ` / tasks=${selectedTraceTasks.slice(0, 6).join(", ")}${selectedTraceTasks.length > 6 ? ", …" : ""}`
                                : ""}
                            </div>
                            {selectedTraceEntries.length > 0 ? (
                              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                                {selectedTraceEntries.slice(0, 2).map((ev) => {
                                  const modelLabel =
                                    ev.provider && ev.model ? `${ev.provider}:${ev.model}` : ev.model || ev.provider || "";
                                  const header = `#${ev.index + 1} ${ev.kind === "image" ? "IMAGE" : "LLM"} task=${ev.task}${
                                    modelLabel ? ` / ${modelLabel}` : ""
                                  }${ev.request_id ? ` / rid=${ev.request_id}` : ""}`;
                                  const body =
                                    ev.kind === "image"
                                      ? truncateText(ev.prompt || "", 1200)
                                      : formatTraceMessages(ev.messages, 1200);
                                  return (
                                    <details
                                      key={`quick-trace-${ev.index}-${ev.task}-${ev.kind}`}
                                      style={{
                                        border: "1px solid rgba(15, 23, 42, 0.10)",
                                        borderRadius: 10,
                                        padding: "8px 10px",
                                        background: "var(--color-surface-subtle)",
                                      }}
                                    >
                                      <summary className="mono muted small-text" style={{ cursor: "pointer", overflowWrap: "anywhere" }}>
                                        {header}
                                      </summary>
                                      <pre className="mono" style={{ margin: "8px 0 0 0", whiteSpace: "pre-wrap" }}>
                                        {body || "—"}
                                      </pre>
                                    </details>
                                  );
                                })}
                                {selectedTraceEntries.length > 2 ? <div className="muted small-text">…</div> : null}
                              </div>
                            ) : (
                              <div className="muted small-text" style={{ marginTop: 10, lineHeight: 1.6 }}>
                                このノードに紐づく task が trace から見つかりません（taskマッピング不足 or trace範囲外の可能性）。必要なら Open Trace で全文を確認してください。
                              </div>
                            )}
                          </details>
                        ) : null}

                      {selectedTemplatePath ? (
                        <details open>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            Prompt Template（プレビュー）
                          </summary>
                          <div style={{ marginTop: 8 }}>
                            <SsotFilePreview repoPath={selectedTemplatePath.path} highlightLine={selectedTemplatePath.line} title="Prompt Template" />
                          </div>
                        </details>
                      ) : null}

                        {selectedOutputDecls.length > 0 ? (
                          <details open>
                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                              Outputs（宣言）: {selectedOutputDecls.length}
                            </summary>
                          <ul style={{ margin: "8px 0 0 0", paddingLeft: 18 }}>
                            {selectedOutputDecls.slice(0, 6).map((o, i) => (
                              <li key={`quick-out-${i}`}>
                                <span className="mono">{o.path}</span>
                                {o.required === true ? <span className="muted small-text"> (required)</span> : null}
                              </li>
                            ))}
                          </ul>
                          {selectedOutputDecls.length > 6 ? <div className="muted small-text" style={{ marginTop: 6 }}>…</div> : null}
                          </details>
                        ) : null}

                        {selectedSubsteps.length > 0 ? (
                          <details>
                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                              substeps（内部処理）: {selectedSubsteps.length}
                            </summary>
                            <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                              {selectedSubsteps.slice(0, 6).map((ss: any, i: number) => {
                                const ssId = ss?.id ? String(ss.id) : `substep#${i + 1}`;
                                const ssName = ss?.name ? String(ss.name) : ssId;
                                const ssLlm = ss?.llm as any;
                                const ssTask = ssLlm?.task ? String(ssLlm.task) : "";
                                const ssKind = ssLlm?.kind ? String(ssLlm.kind) : "";
                                const ssTpl = ss?.template as any;
                                const ssTplPath = ssTpl?.path ? String(ssTpl.path) : "";
                                const ssTplLine = typeof ssTpl?.line === "number" ? Number(ssTpl.line) : undefined;
                                return (
                                  <details
                                    key={`quick-sub-${ssId}-${i}`}
                                    style={{
                                      border: "1px solid rgba(15, 23, 42, 0.10)",
                                      borderRadius: 10,
                                      padding: "8px 10px",
                                      background: "var(--color-surface-subtle)",
                                    }}
                                  >
                                    <summary style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                                      <span style={{ fontWeight: 900, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {ssName}
                                      </span>
                                      <span className="mono muted small-text" style={{ flex: "none", maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {ssTask ? `${ssKind === "image_client" ? "image" : "llm"} task=${ssTask}` : ssId}
                                      </span>
                                    </summary>
                                    {ssTplPath ? (
                                      <div style={{ marginTop: 8 }}>
                                        <SsotFilePreview repoPath={ssTplPath} highlightLine={ssTplLine} title="Prompt Template" />
                                      </div>
                                    ) : null}
                                  </details>
                                );
                              })}
                              {selectedSubsteps.length > 6 ? <div className="muted small-text">…</div> : null}
                            </div>
                          </details>
                        ) : null}

                        {selectedImplRefs.length > 0 ? (
                          <details>
                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                              Implementation（参照）: {selectedImplRefs.length}
                            </summary>
                            <div className="mono muted small-text" style={{ marginTop: 8, overflowWrap: "anywhere" }}>
                              {selectedImplRefs
                                .slice(0, 4)
                                .map((r) => `${r.path}:${r.line}${r.symbol ? `(${r.symbol})` : ""}`)
                                .join(", ")}
                              {selectedImplRefs.length > 4 ? ", …" : ""}
                            </div>
                          </details>
                        ) : null}

                        {selectedPlaceholderPairs.length > 0 ? (
                          <details>
                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                              placeholders（差し込み）: {selectedPlaceholderPairs.length}
                            </summary>
                            <pre className="mono" style={{ margin: "8px 0 0 0", whiteSpace: "pre-wrap" }}>
                              {selectedPlaceholderPairs.map((p) => `${p.key}: ${p.value}`).join("\n")}
                            </pre>
                          </details>
                        ) : null}

                        {(selectedNode as any)?.sot?.path ? (
                          <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                            SoT: {String((selectedNode as any).sot.path)}
                          </div>
                        ) : null}

                        {selectedRelatedFlow ? (
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <button
                              type="button"
                              className="research-chip"
                              onClick={() => {
                                setFlow(selectedRelatedFlow);
                                setSelectedNodeId(null);
                              }}
                            >
                              Open Related Flow
                            </button>
                          </div>
                        ) : null}
                      </div>
                    )}
                    </aside>
                ) : null}
              </div>
              <div
                className="muted small-text"
                style={{ marginTop: 8, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
              >
                <span>クリックで詳細 / 凡例:</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(255, 200, 0, 0.20)", border: "1px solid rgba(255, 200, 0, 0.55)" }} />
                  検索一致
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(14, 165, 233, 0.20)", border: "1px solid rgba(14, 165, 233, 0.75)" }} />
                  実行済み（Trace）
                </span>
                {flowView === "graph" ? (
                  <>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(67, 160, 71, 0.25)", border: "1px solid rgba(67, 160, 71, 0.80)" }} />
                      下流
                    </span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span style={{ width: 10, height: 10, borderRadius: 4, background: "rgba(156, 39, 176, 0.20)", border: "1px solid rgba(156, 39, 176, 0.75)" }} />
                      上流
                    </span>
                    <span>背景ドラッグ: pan</span>
                  </>
                ) : (
                  <span>上から順に流れます（Timeline）</span>
                )}
              </div>

              <details id="ssot-flow-runbook" style={{ marginTop: 10 }}>
                <summary style={{ cursor: "pointer", fontWeight: 900 }}>Flow Runbook（全ステップ詳細）</summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  {nodes.map((s) => {
                  const llm = s.llm as any;
                  const task = llm?.task ? String(llm.task) : "";
                  const kind = llm?.kind ? String(llm.kind) : "";
                  const mode = task ? (kind === "image_client" ? "IMAGE" : "LLM") : "CODE";
                  const taskDefs = kind === "image_client" ? (catalog as any)?.image?.task_defs || {} : (catalog as any)?.llm?.task_defs || {};
                  const taskDef = task ? (taskDefs[task] as any) : null;
                  const tier = taskDef?.tier ? String(taskDef.tier) : "";
                  const modelKeys = Array.isArray(taskDef?.model_keys) ? (taskDef.model_keys as any[]).map((x) => String(x)) : [];
                  const resolvedModels = Array.isArray(taskDef?.resolved_models) ? ((taskDef.resolved_models as any[]) || []) : [];
                  const overrideProfile = taskDef?.override_profile ? String(taskDef.override_profile) : "";
                  const allowFallback = typeof taskDef?.allow_fallback === "boolean" ? Boolean(taskDef.allow_fallback) : null;
                    const tpl = s.template as any;
                    const tplName = tpl?.name ? String(tpl.name) : llm?.template ? String(llm.template) : "";
                    const tplPath = tpl?.path ? String(tpl.path) : "";
                    const placeholders = parsePlaceholderPairs(llm?.placeholders);
                    const outputs = parseOutputDecls(s.outputs);
                    const sotPath = (s as any)?.sot?.path ? String((s as any).sot.path) : "";
                    const implRefs = Array.isArray((s as any).impl_refs) ? ((s as any).impl_refs as any[]) : [];
                    const substeps = Array.isArray((s as any).substeps) ? (((s as any).substeps as any[]) || []) : [];
                    return (
                      <details
                        id={domIdForRunbookNode(s.node_id)}
                        key={s.node_id}
                        style={{
                          border: "1px solid var(--color-border-muted)",
                          borderRadius: 12,
                          background: "var(--color-surface)",
                          padding: "10px 12px",
                        }}
                      >
                        <summary
                          style={{
                            cursor: "pointer",
                            display: "grid",
                            gridTemplateColumns: "minmax(0, 1fr) auto",
                            gap: 10,
                            alignItems: "start",
                          }}
                        >
                          <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
                            <span
                              style={{
                                fontWeight: 900,
                                minWidth: 0,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {nodeTitle(s)}
                            </span>
                            {s.description ? (
                              <span
                                className="muted small-text"
                                style={{
                                  minWidth: 0,
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {truncateText(String(s.description), 140)}
                              </span>
                            ) : null}
                          </div>
                          <div style={{ display: "grid", gap: 4, justifyItems: "end", alignItems: "start" }}>
                            <span className="badge subtle">{mode}</span>
                            <span
                              className="mono muted small-text"
                              style={{
                                flex: "none",
                                maxWidth: 520,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {task ? `${kind === "image_client" ? "image" : "llm"} task=${task}` : "no-task"}
                            </span>
                          </div>
                        </summary>
                        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                          {s.description ? (
                            <div className="muted" style={{ whiteSpace: "pre-wrap" }}>
                              {s.description}
                            </div>
                          ) : null}
                          {task ? (
                            <div className="mono muted small-text">
                              {kind === "image_client" ? "IMAGE" : "LLM"}: task={task}
                              {tplName ? ` / template=${tplName}` : ""}
                              {tplPath ? ` / template_path=${tplPath}` : ""}
                            </div>
                          ) : null}
                          {task && (tier || modelKeys.length > 0) ? (
                            <div className="mono muted small-text">
                              routing: {tier ? `tier=${tier}` : "tier=—"}
                              {overrideProfile ? ` / profile=${overrideProfile}` : ""}
                              {allowFallback === true ? " / allow_fallback=true" : allowFallback === false ? " / allow_fallback=false" : ""}
                              {modelKeys.length > 0 ? ` / models=${modelKeys.slice(0, 6).join(", ")}${modelKeys.length > 6 ? ", …" : ""}` : ""}
                            </div>
                          ) : null}
                          {task && resolvedModels.length > 0 ? (
                            <div className="mono muted small-text" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              resolved:{" "}
                              {resolvedModels
                                .slice(0, 3)
                                .map((m: any) => {
                                  const provider = m?.provider ? String(m.provider) : "";
                                  const model = m?.model_name ? String(m.model_name) : m?.key ? String(m.key) : "";
                                  const dep = m?.deployment ? String(m.deployment) : "";
                                  const base = provider && model ? `${provider}:${model}` : model || provider || "";
                                  return dep ? `${base}(${dep})` : base;
                                })
                                .filter((s: string) => Boolean(s.trim()))
                                .join(" | ")}
                              {resolvedModels.length > 3 ? " | …" : ""}
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
                          {substeps.length > 0 ? (
                            <details open>
                              <summary className="muted small-text" style={{ cursor: "pointer" }}>
                                substeps（内部処理）: {substeps.length}
                              </summary>
                              <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                                {substeps.map((ss: any, i: number) => {
                                  const ssId = ss?.id ? String(ss.id) : `substep#${i + 1}`;
                                  const ssName = ss?.name ? String(ss.name) : ssId;
                                  const ssDesc = ss?.description ? String(ss.description) : "";
                                  const ssOutputs = parseOutputDecls(ss?.outputs);
                                  const ssLlm = ss?.llm as any;
                                  const ssTask = ssLlm?.task ? String(ssLlm.task) : "";
                                  const ssKind = ssLlm?.kind ? String(ssLlm.kind) : "";
                                  const ssTaskDefs = ssKind === "image_client" ? (catalog as any)?.image?.task_defs || {} : (catalog as any)?.llm?.task_defs || {};
                                  const ssTaskDef = ssTask ? (ssTaskDefs[ssTask] as any) : null;
                                  const ssTier = ssTaskDef?.tier ? String(ssTaskDef.tier) : "";
                                  const ssModelKeys = Array.isArray(ssTaskDef?.model_keys) ? (ssTaskDef.model_keys as any[]).map((x) => String(x)) : [];
                                  const ssResolvedModels = Array.isArray(ssTaskDef?.resolved_models) ? ((ssTaskDef.resolved_models as any[]) || []) : [];
                                  const ssTpl = ss?.template as any;
                                  const ssTplPath = ssTpl?.path ? String(ssTpl.path) : "";
                                  const ssTplLine = typeof ssTpl?.line === "number" ? Number(ssTpl.line) : undefined;
                                  const ssPlaceholders = parsePlaceholderPairs(ssLlm?.placeholders);
                                  const ssImplRefs = Array.isArray(ss?.impl_refs) ? (ss.impl_refs as any[]) : [];
                                  return (
                                    <details
                                      key={`${s.node_id}-sub-${ssId}-${i}`}
                                      style={{
                                        border: "1px solid rgba(15, 23, 42, 0.10)",
                                        borderRadius: 10,
                                        padding: "8px 10px",
                                        background: "var(--color-surface-subtle)",
                                      }}
                                    >
                                      <summary
                                        style={{
                                          cursor: "pointer",
                                          display: "flex",
                                          justifyContent: "space-between",
                                          gap: 10,
                                          alignItems: "baseline",
                                          flexWrap: "wrap",
                                        }}
                                      >
                                        <span style={{ fontWeight: 900, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {ssName}
                                        </span>
                                        <span className="mono muted small-text" style={{ flex: "none", maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {ssTask ? `${ssKind === "image_client" ? "image" : "llm"} task=${ssTask}` : ssId}
                                        </span>
                                      </summary>
                                      <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                                        {ssDesc ? <div className="muted small-text" style={{ whiteSpace: "pre-wrap" }}>{ssDesc}</div> : null}
                                        {ssTask && (ssTier || ssModelKeys.length > 0) ? (
                                          <div className="mono muted small-text">
                                            routing: {ssTier ? `tier=${ssTier}` : "tier=—"}
                                            {ssModelKeys.length > 0 ? ` / models=${ssModelKeys.slice(0, 6).join(", ")}${ssModelKeys.length > 6 ? ", …" : ""}` : ""}
                                          </div>
                                        ) : null}
                                        {ssTask && ssResolvedModels.length > 0 ? (
                                          <div className="mono muted small-text" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            resolved:{" "}
                                            {ssResolvedModels
                                              .slice(0, 3)
                                              .map((m: any) => {
                                                const provider = m?.provider ? String(m.provider) : "";
                                                const model = m?.model_name ? String(m.model_name) : m?.key ? String(m.key) : "";
                                                const dep = m?.deployment ? String(m.deployment) : "";
                                                const base = provider && model ? `${provider}:${model}` : model || provider || "";
                                                return dep ? `${base}(${dep})` : base;
                                              })
                                              .filter((x: string) => Boolean(x.trim()))
                                              .join(" | ")}
                                            {ssResolvedModels.length > 3 ? " | …" : ""}
                                          </div>
                                        ) : null}
                                        {ssTplPath ? (
                                          <details>
                                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                                              Prompt Template（プレビュー）
                                            </summary>
                                            <div style={{ marginTop: 8 }}>
                                              <SsotFilePreview repoPath={ssTplPath} highlightLine={ssTplLine} title="Prompt Template" />
                                            </div>
                                          </details>
                                        ) : null}
                                        {ssPlaceholders.length > 0 ? (
                                          <details>
                                            <summary className="muted small-text" style={{ cursor: "pointer" }}>
                                              placeholders: {ssPlaceholders.length}
                                            </summary>
                                            <pre className="mono" style={{ margin: "8px 0 0 0", whiteSpace: "pre-wrap" }}>
                                              {ssPlaceholders.map((p) => `${p.key}: ${p.value}`).join("\n")}
                                            </pre>
                                          </details>
                                        ) : null}
                                        {ssOutputs.length > 0 ? (
                                          <div>
                                            <div className="muted small-text" style={{ marginBottom: 4 }}>
                                              outputs
                                            </div>
                                            <ul style={{ margin: 0, paddingLeft: 18 }}>
                                              {ssOutputs.map((o, oi) => (
                                                <li key={`${s.node_id}-sub-${ssId}-out-${oi}`}>
                                                  <span className="mono">{o.path}</span>
                                                  {o.required === true ? <span className="muted small-text"> (required)</span> : null}
                                                  {o.required === false ? <span className="muted small-text"> (optional)</span> : null}
                                                </li>
                                              ))}
                                            </ul>
                                          </div>
                                        ) : null}
                                        {ssImplRefs.length > 0 ? (
                                          <div className="muted small-text">
                                            impl:{" "}
                                            <span className="mono">
                                              {ssImplRefs
                                                .slice(0, 4)
                                                .map((r: any) => `${r.path}:${r.line}${r.symbol ? `(${r.symbol})` : ""}`)
                                                .join(", ")}
                                            </span>
                                            {ssImplRefs.length > 4 ? <span className="muted small-text"> …</span> : null}
                                          </div>
                                        ) : null}
                                      </div>
                                    </details>
                                  );
                                })}
                              </div>
                            </details>
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
                                focusOnNode(s.node_id);
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
                  {selectedNode.description ? <p style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{selectedNode.description}</p> : null}
                </section>

                {flow === "mainline" && selectedMainlineFlowMeta ? (
                  <section className="shell-panel shell-panel--placeholder">
                    <h3 style={{ marginTop: 0 }}>Phase Details（実装から合成）</h3>
                    <div className="mono muted small-text">
                      flow_id={(selectedMainlineFlowMeta as any)?.flow_id || "—"} / phase={(selectedMainlineFlowMeta as any)?.phase || "—"} / steps=
                      {Array.isArray((selectedMainlineFlowMeta as any)?.steps) ? (selectedMainlineFlowMeta as any).steps.length : 0}
                    </div>
                    {(selectedMainlineFlowMeta as any)?.summary ? (
                      <div className="muted" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                        {String((selectedMainlineFlowMeta as any).summary)}
                      </div>
                    ) : null}
                    {selectedMainlineCodePaths.length > 0 ? (
                      <div className="muted small-text" style={{ marginTop: 8 }}>
                        code: <span className="mono">{selectedMainlineCodePaths.join(", ")}</span>
                      </div>
                    ) : null}
                    {selectedMainlineEntrypoints.length > 0 ? (
                      <div style={{ marginTop: 10 }}>
                        <div className="muted small-text" style={{ marginBottom: 4 }}>
                          Entrypoints（入口）
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {selectedMainlineEntrypoints.map((e) => (
                            <li key={`mainline-entry-${e}`} className="mono muted small-text">
                              {e}
                            </li>
                          ))}
                        </ul>
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
                            const imageDef = ((catalog as any)?.image?.task_defs || {})[t] as any;
                            const llmDef = ((catalog as any)?.llm?.task_defs || {})[t] as any;
                            const def = imageDef || llmDef;
                            const kind = imageDef ? "image" : "llm";
                            const tier = def?.tier ? String(def.tier) : "";
                            const modelKeys = Array.isArray(def?.model_keys) ? (def.model_keys as string[]) : [];
                            const modelsShort = modelKeys.length > 0 ? modelKeys.slice(0, 6).join(", ") : "";
                            return (
                              <div key={`mainline-task-${t}`} className="mono muted small-text">
                                {t} {kind === "image" ? "(image)" : ""}
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
                  <h3 style={{ marginTop: 0 }}>Task Routing</h3>
                  {stageLlmTask ? <div className="mono muted small-text">task={stageLlmTask}</div> : null}
                  {stageTaskKind ? <div className="mono muted small-text">kind={stageTaskKind}</div> : null}
                  {(selectedNode.llm as any)?.template ? <div className="mono muted small-text">template={String((selectedNode.llm as any).template)}</div> : null}
                  {(selectedNode.llm as any)?.max_tokens ? (
                    <div className="mono muted small-text">max_tokens={String((selectedNode.llm as any).max_tokens)}</div>
                  ) : null}
                  {selectedTaskDef ? (
                    <details style={{ marginTop: 10 }}>
                      <summary className="muted small-text" style={{ cursor: "pointer" }}>
                        {stageTaskKind === "image_client" ? "routing（image_modelsから解決）" : "routing（llm_routerから解決）"}
                      </summary>
                      <div className="mono muted small-text">tier={String((selectedTaskDef as any)?.tier || "—")}</div>
                      {stageTaskKind === "image_client" && (selectedTaskDef as any)?.override_profile ? (
                        <div className="mono muted small-text">override_profile={String((selectedTaskDef as any).override_profile)}</div>
                      ) : null}
                      {stageTaskKind === "image_client" && typeof (selectedTaskDef as any)?.allow_fallback === "boolean" ? (
                        <div className="mono muted small-text">allow_fallback={String((selectedTaskDef as any).allow_fallback)}</div>
                      ) : null}
                      {Array.isArray((selectedTaskDef as any)?.model_keys) && (selectedTaskDef as any).model_keys.length > 0 ? (
                        <div className="mono muted small-text">model_keys={String((selectedTaskDef as any).model_keys.join(", "))}</div>
                      ) : null}
                      {Array.isArray((selectedTaskDef as any)?.resolved_models) && (selectedTaskDef as any).resolved_models.length > 0 ? (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {(selectedTaskDef as any).resolved_models.map((m: any) => (
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
                      {(selectedTaskDef as any)?.router_task ? (
                        <details style={{ marginTop: 8 }}>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            {stageTaskKind === "image_client" ? "router_task（configs/image_models.yaml）" : "router_task（configs/llm_router.yaml）"}
                          </summary>
                          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                            {JSON.stringify((selectedTaskDef as any).router_task, null, 2)}
                          </pre>
                        </details>
                      ) : null}
                      {(selectedTaskDef as any)?.override_task ? (
                        <details style={{ marginTop: 8 }}>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            {stageTaskKind === "image_client" ? "override_task（image_task_overrides）" : "override_task（llm_task_overrides）"}
                          </summary>
                          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                            {JSON.stringify((selectedTaskDef as any).override_task, null, 2)}
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
                  {selectedTaskCallsites.length > 0 ? (
                    <div style={{ marginTop: 10 }}>
                      <div className="muted small-text" style={{ marginBottom: 4 }}>
                        callsites（コード上の呼び出し箇所・先頭のみ）
                      </div>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {selectedTaskCallsites.map((c, i) => (
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

              {traceLoadedKey ? (
                <section className="shell-panel shell-panel--placeholder">
                  <h3 style={{ marginTop: 0 }}>Trace Prompts（実行ログ）</h3>
                  <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                    loaded={traceLoadedKey}
                    {traceIsPartial ? " (partial)" : ""} / tasks={selectedTraceTasks.length} / events={selectedTraceEntries.length}
                  </div>
                  {selectedTraceTasks.length > 0 ? (
                    <div className="muted small-text" style={{ marginTop: 8 }}>
                      tasks: <span className="mono">{selectedTraceTasks.slice(0, 12).join(", ")}</span>
                      {selectedTraceTasks.length > 12 ? <span className="muted small-text"> …</span> : null}
                    </div>
                  ) : (
                    <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
                      このノードは task（LLM/Image）を宣言していません（= CODE step の可能性）。サブステップや mainline の task マッピングを増やすと trace と紐づきます。
                    </div>
                  )}
                  {selectedTraceEntries.length > 0 ? (
                    <details style={{ marginTop: 10 }} open={selectedTraceEntries.length <= 2}>
                      <summary className="muted small-text" style={{ cursor: "pointer" }}>
                        Matched Events（先頭のみ / プレビューは各 8k 文字で切り詰め）: {selectedTraceEntries.length}
                      </summary>
                      <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                        {selectedTraceEntries.slice(0, 6).map((ev) => {
                          const modelLabel = ev.provider && ev.model ? `${ev.provider}:${ev.model}` : ev.model || ev.provider || "";
                          const at = typeof ev.at_ms === "number" ? new Date(ev.at_ms).toISOString() : "";
                          const header = `#${ev.index + 1} ${ev.kind === "image" ? "IMAGE" : "LLM"} task=${ev.task}${
                            modelLabel ? ` / ${modelLabel}` : ""
                          }${ev.request_id ? ` / rid=${ev.request_id}` : ""}${at ? ` / at=${at}` : ""}`;
                          const body =
                            ev.kind === "image"
                              ? truncateText(ev.prompt || "", 8000)
                              : formatTraceMessages(ev.messages, 8000);
                          return (
                            <details
                              key={`trace-${ev.index}-${ev.task}-${ev.kind}`}
                              style={{
                                border: "1px solid rgba(15, 23, 42, 0.10)",
                                borderRadius: 12,
                                padding: "10px 12px",
                                background: "var(--color-surface-subtle)",
                              }}
                            >
                              <summary className="mono muted small-text" style={{ cursor: "pointer", overflowWrap: "anywhere" }}>
                                {header}
                              </summary>
                              <pre className="mono" style={{ margin: "10px 0 0 0", whiteSpace: "pre-wrap" }}>
                                {body || "—"}
                              </pre>
                            </details>
                          );
                        })}
                        {selectedTraceEntries.length > 6 ? <div className="muted small-text">…</div> : null}
                      </div>
                    </details>
                  ) : (
                    <div className="muted small-text" style={{ marginTop: 10, lineHeight: 1.6 }}>
                      このノードに紐づく task が trace から見つかりません。task マッピング不足 or trace の読み込み範囲（先頭 5000 行）外の可能性があります。
                    </div>
                  )}
                </section>
              ) : null}

              {selectedTemplatePath ? (
                <details style={{ marginTop: 12 }} open>
                  <summary style={{ cursor: "pointer", fontWeight: 900 }}>Prompt Template（内容プレビュー）</summary>
                  <div style={{ marginTop: 10 }}>
                    <SsotFilePreview repoPath={selectedTemplatePath.path} highlightLine={selectedTemplatePath.line} title="Prompt Template" />
                  </div>
                </details>
              ) : null}
              {selectedImplRefs.length > 0 ? (
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 900 }}>
                    Implementation Sources（{selectedImplRefs.length}）
                  </summary>
                  <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                    {selectedImplRefs.map((ref, idx) => (
                      <SsotFilePreview
                        key={`${ref.path}:${ref.line ?? 0}:${idx}`}
                        repoPath={ref.path}
                        highlightLine={ref.line}
                        title={ref.symbol ? `Implementation (${ref.symbol})` : "Implementation (source)"}
                      />
                    ))}
                  </div>
                </details>
              ) : null}

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
                    <div>
                      Image tasks used: <span className="mono">{catalog?.image?.used_tasks?.length ?? 0}</span>
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
