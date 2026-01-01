import { useCallback, useEffect, useMemo } from "react";
import type { SsotCatalogFlowStep } from "../api/types";

export type SsotFlowEdge = { from: string; to: string };

type Orientation = "horizontal" | "vertical";

function pad2(n: number | undefined) {
  if (!n) return "";
  return String(n).padStart(2, "0");
}

function nodeBadge(step: SsotCatalogFlowStep): string {
  if (step.phase && step.order) return `${step.phase}-${pad2(step.order)}`;
  return step.phase || "";
}

function nodeTask(step: SsotCatalogFlowStep): string | null {
  const llm = step.llm as any;
  const t = llm?.task ? String(llm.task) : "";
  return t || null;
}

function phaseKey(phase: string): string {
  const s = (phase || "").trim();
  if (!s) return "";
  return s.slice(0, 1).toUpperCase();
}

function colorsForPhase(phase: string): { fill: string; stroke: string; text: string } {
  switch (phaseKey(phase)) {
    case "A":
      return { fill: "rgba(245, 158, 11, 0.07)", stroke: "rgba(245, 158, 11, 0.25)", text: "rgba(180, 83, 9, 0.95)" };
    case "B":
      return { fill: "rgba(99, 102, 241, 0.07)", stroke: "rgba(99, 102, 241, 0.25)", text: "rgba(67, 56, 202, 0.95)" };
    case "C":
      return { fill: "rgba(14, 165, 233, 0.06)", stroke: "rgba(14, 165, 233, 0.22)", text: "rgba(2, 132, 199, 0.95)" };
    case "D":
      return { fill: "rgba(34, 197, 94, 0.06)", stroke: "rgba(34, 197, 94, 0.22)", text: "rgba(21, 128, 61, 0.95)" };
    case "F":
      return { fill: "rgba(236, 72, 153, 0.06)", stroke: "rgba(236, 72, 153, 0.22)", text: "rgba(190, 24, 93, 0.95)" };
    case "G":
      return { fill: "rgba(107, 114, 128, 0.06)", stroke: "rgba(107, 114, 128, 0.22)", text: "rgba(55, 65, 81, 0.92)" };
    default:
      return { fill: "rgba(148, 163, 184, 0.05)", stroke: "rgba(148, 163, 184, 0.22)", text: "rgba(71, 85, 105, 0.92)" };
  }
}

function stableNodeSort(a: SsotCatalogFlowStep, b: SsotCatalogFlowStep) {
  const ao = typeof a.order === "number" ? a.order : 0;
  const bo = typeof b.order === "number" ? b.order : 0;
  if (ao !== bo) return ao - bo;
  return `${a.name || ""}${a.node_id}`.localeCompare(`${b.name || ""}${b.node_id}`);
}

type LayoutNode = {
  id: string;
  step: SsotCatalogFlowStep;
  rank: number;
  row: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

function domIdForNode(nodeId: string): string {
  const safe = (nodeId || "").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 160);
  return `ssot-node-${safe || "unknown"}`;
}

function computeLayout(
  steps: SsotCatalogFlowStep[],
  edges: SsotFlowEdge[],
  orientation: Orientation,
): { nodes: LayoutNode[]; paths: Array<{ from: LayoutNode; to: LayoutNode; d: string }> } {
  const nodeWidth = 230;
  const nodeHeight = 66;
  const gapMain = 90;
  const gapCross = 80;
  const margin = 24;

  const idToStep = new Map<string, SsotCatalogFlowStep>();
  for (const s of steps) idToStep.set(s.node_id, s);

  const nodeIds = steps.map((s) => s.node_id);
  const inDeg = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  const outgoing = new Map<string, string[]>(nodeIds.map((id) => [id, []]));

  const usableEdges = edges.filter((e) => idToStep.has(e.from) && idToStep.has(e.to));
  for (const e of usableEdges) {
    outgoing.get(e.from)!.push(e.to);
    inDeg.set(e.to, (inDeg.get(e.to) || 0) + 1);
  }

  // Topological rank = longest distance from any root (best-effort; cycles fall back to 0).
  const rank = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  const q: string[] = nodeIds.filter((id) => (inDeg.get(id) || 0) === 0);
  const popped: string[] = [];
  while (q.length > 0) {
    const id = q.shift()!;
    popped.push(id);
    const r = rank.get(id) || 0;
    for (const to of outgoing.get(id) || []) {
      rank.set(to, Math.max(rank.get(to) || 0, r + 1));
      inDeg.set(to, (inDeg.get(to) || 0) - 1);
      if ((inDeg.get(to) || 0) === 0) q.push(to);
    }
  }

  const ranks = new Map<number, SsotCatalogFlowStep[]>();
  for (const s of steps) {
    const r = rank.get(s.node_id) || 0;
    const list = ranks.get(r) || [];
    list.push(s);
    ranks.set(r, list);
  }
  for (const [r, list] of Array.from(ranks.entries())) {
    list.sort(stableNodeSort);
    ranks.set(r, list);
  }

  const laidOut: LayoutNode[] = [];
  for (const [r, list] of Array.from(ranks.entries()).sort((a, b) => a[0] - b[0])) {
    for (let row = 0; row < list.length; row++) {
      const step = list[row];
      const primary = orientation === "horizontal" ? r : row;
      const secondary = orientation === "horizontal" ? row : r;
      const x = margin + primary * (nodeWidth + gapMain);
      const y = margin + secondary * (nodeHeight + gapCross);
      laidOut.push({
        id: step.node_id,
        step,
        rank: r,
        row,
        x,
        y,
        width: nodeWidth,
        height: nodeHeight,
      });
    }
  }

  const idToNode = new Map<string, LayoutNode>(laidOut.map((n) => [n.id, n]));

  const paths: Array<{ from: LayoutNode; to: LayoutNode; d: string }> = [];
  for (const e of usableEdges) {
    const from = idToNode.get(e.from);
    const to = idToNode.get(e.to);
    if (!from || !to) continue;

    if (orientation === "horizontal") {
      const sx = from.x + from.width;
      const sy = from.y + from.height / 2;
      const ex = to.x;
      const ey = to.y + to.height / 2;
      const dx = Math.max(40, Math.min(140, Math.abs(ex - sx) / 2));
      const d = `M ${sx} ${sy} C ${sx + dx} ${sy}, ${ex - dx} ${ey}, ${ex} ${ey}`;
      paths.push({ from, to, d });
    } else {
      const sx = from.x + from.width / 2;
      const sy = from.y + from.height;
      const ex = to.x + to.width / 2;
      const ey = to.y;
      const dy = Math.max(40, Math.min(140, Math.abs(ey - sy) / 2));
      const d = `M ${sx} ${sy} C ${sx} ${sy + dy}, ${ex} ${ey - dy}, ${ex} ${ey}`;
      paths.push({ from, to, d });
    }
  }

  return { nodes: laidOut, paths };
}

export function SsotFlowGraph({
  steps,
  edges,
  selectedNodeId,
  onSelect,
  orientation,
  highlightedNodeIds,
  onSize,
  executed,
  executedEdges,
}: {
  steps: SsotCatalogFlowStep[];
  edges: SsotFlowEdge[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  orientation: Orientation;
  highlightedNodeIds?: string[];
  onSize?: (size: { width: number; height: number }) => void;
  executed?: Record<string, { firstIndex: number; count: number }>;
  executedEdges?: Record<string, { firstIndex: number; count: number }>;
}) {
  const highlighted = useMemo(() => new Set(highlightedNodeIds || []), [highlightedNodeIds]);
  const { nodes, paths } = useMemo(() => computeLayout(steps, edges, orientation), [edges, orientation, steps]);

  const phaseGroups = useMemo(() => {
    const groups = new Map<
      string,
      { minX: number; minY: number; maxX: number; maxY: number; colors: { fill: string; stroke: string; text: string } }
    >();
    for (const n of nodes) {
      const phase = (n.step.phase || "").trim();
      if (!phase) continue;
      const colors = colorsForPhase(phase);
      const cur = groups.get(phase);
      const x1 = n.x;
      const y1 = n.y;
      const x2 = n.x + n.width;
      const y2 = n.y + n.height;
      if (!cur) {
        groups.set(phase, { minX: x1, minY: y1, maxX: x2, maxY: y2, colors });
      } else {
        cur.minX = Math.min(cur.minX, x1);
        cur.minY = Math.min(cur.minY, y1);
        cur.maxX = Math.max(cur.maxX, x2);
        cur.maxY = Math.max(cur.maxY, y2);
      }
    }

    if (groups.size <= 1) return [];
    const pad = 18;
    return Array.from(groups.entries()).map(([phase, box]) => ({
      phase,
      x: box.minX - pad,
      y: box.minY - pad,
      width: box.maxX - box.minX + pad * 2,
      height: box.maxY - box.minY + pad * 2,
      colors: box.colors,
    }));
  }, [nodes]);

  const width = useMemo(() => {
    if (nodes.length === 0) return 640;
    return Math.max(...nodes.map((n) => n.x + n.width)) + 24;
  }, [nodes]);
  const height = useMemo(() => {
    if (nodes.length === 0) return 240;
    return Math.max(...nodes.map((n) => n.y + n.height)) + 24;
  }, [nodes]);

  useEffect(() => {
    onSize?.({ width, height });
  }, [height, onSize, width]);

  const selectedPath = useMemo(() => {
    const selected = (selectedNodeId || "").trim();
    if (!selected) return { upstream: new Set<string>(), downstream: new Set<string>() };

    const nodeIds = new Set(steps.map((s) => s.node_id));
    if (!nodeIds.has(selected)) return { upstream: new Set<string>(), downstream: new Set<string>() };

    const fwd = new Map<string, string[]>();
    const rev = new Map<string, string[]>();
    for (const id of Array.from(nodeIds)) {
      fwd.set(id, []);
      rev.set(id, []);
    }
    for (const e of edges) {
      if (!nodeIds.has(e.from) || !nodeIds.has(e.to)) continue;
      fwd.get(e.from)!.push(e.to);
      rev.get(e.to)!.push(e.from);
    }

    const bfs = (start: string, adj: Map<string, string[]>) => {
      const seen = new Set<string>();
      const q: string[] = [start];
      while (q.length > 0) {
        const cur = q.shift()!;
        for (const nxt of adj.get(cur) || []) {
          if (seen.has(nxt) || nxt === start) continue;
          seen.add(nxt);
          q.push(nxt);
        }
      }
      return seen;
    };

    return { upstream: bfs(selected, rev), downstream: bfs(selected, fwd) };
  }, [edges, selectedNodeId, steps]);

  const edgeStyle = useCallback(
    (fromId: string, toId: string) => {
      const selected = (selectedNodeId || "").trim();
      const execKey = `${fromId} -> ${toId}`;
      const exec = executedEdges ? executedEdges[execKey] : undefined;
      const hasExec = Boolean(exec);
      if (!selected) {
        if (hasExec) return { stroke: "rgba(14, 165, 233, 0.9)", strokeWidth: 3, opacity: 1, marker: "exec", title: `executed: run#${(exec?.firstIndex ?? 0) + 1} ×${exec?.count ?? 1}` };
        return { stroke: "var(--color-border)", strokeWidth: 2, opacity: 0.9, marker: "normal", title: "" };
      }

      const upstream = selectedPath.upstream;
      const downstream = selectedPath.downstream;
      const isUpEdge = upstream.has(fromId) && (toId === selected || upstream.has(toId));
      const isDownEdge = (fromId === selected || downstream.has(fromId)) && downstream.has(toId);

      if (isDownEdge) return { stroke: "rgba(67, 160, 71, 0.9)", strokeWidth: 3, opacity: 1, marker: "down", title: "" };
      if (isUpEdge) return { stroke: "rgba(156, 39, 176, 0.85)", strokeWidth: 3, opacity: 1, marker: "up", title: "" };
      if (hasExec) return { stroke: "rgba(14, 165, 233, 0.85)", strokeWidth: 3, opacity: 0.85, marker: "exec", title: `executed: run#${(exec?.firstIndex ?? 0) + 1} ×${exec?.count ?? 1}` };
      return { stroke: "var(--color-border)", strokeWidth: 2, opacity: 0.45, marker: "normal", title: "" };
    },
    [executedEdges, selectedNodeId, selectedPath.downstream, selectedPath.upstream],
  );

  return (
    <div style={{ position: "relative", width, height, minHeight: 220 }}>
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
        <defs>
          <marker id="ssotArrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
            <path d="M0,0 L12,6 L0,12 Z" fill="var(--color-border)" />
          </marker>
          <marker id="ssotArrowDown" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
            <path d="M0,0 L12,6 L0,12 Z" fill="rgba(67, 160, 71, 0.95)" />
          </marker>
          <marker id="ssotArrowUp" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
            <path d="M0,0 L12,6 L0,12 Z" fill="rgba(156, 39, 176, 0.9)" />
          </marker>
          <marker id="ssotArrowExec" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
            <path d="M0,0 L12,6 L0,12 Z" fill="rgba(14, 165, 233, 0.95)" />
          </marker>
        </defs>
        {phaseGroups.map((g) => (
          <g key={g.phase}>
            <rect
              x={g.x}
              y={g.y}
              width={g.width}
              height={g.height}
              rx={18}
              fill={g.colors.fill}
              stroke={g.colors.stroke}
              strokeWidth={2}
            />
            <text x={g.x + 12} y={g.y + 8} fill={g.colors.text} fontSize={12} fontWeight={800} dominantBaseline="hanging">
              Phase {g.phase}
            </text>
          </g>
        ))}
        {paths.map((p, idx) => (
          (() => {
            const st = edgeStyle(p.from.id, p.to.id);
            const marker =
              st.marker === "down"
                ? "url(#ssotArrowDown)"
                : st.marker === "up"
                  ? "url(#ssotArrowUp)"
                  : st.marker === "exec"
                    ? "url(#ssotArrowExec)"
                    : "url(#ssotArrow)";
            return (
              <path
                key={idx}
                d={p.d}
                fill="none"
                stroke={st.stroke}
                strokeWidth={st.strokeWidth}
                markerEnd={marker}
                opacity={st.opacity}
              >
                {st.title ? <title>{st.title}</title> : null}
              </path>
            );
          })()
        ))}
      </svg>

      {nodes.map((n) => {
        const badge = nodeBadge(n.step);
        const task = nodeTask(n.step);
        const isSelected = selectedNodeId === n.id;
        const isHighlighted = highlighted.has(n.id);
        const isUp = selectedPath.upstream.has(n.id);
        const isDown = selectedPath.downstream.has(n.id);
        const exec = executed ? executed[n.id] : undefined;
        const isExec = Boolean(exec);
        const border = isSelected
          ? "var(--color-primary)"
          : isDown
            ? "rgba(67, 160, 71, 0.85)"
            : isUp
              ? "rgba(156, 39, 176, 0.85)"
              : isExec
                ? "rgba(14, 165, 233, 0.85)"
              : "var(--color-border-muted)";
        const background = isSelected
          ? "rgba(25, 118, 210, 0.12)"
          : isDown
            ? "rgba(67, 160, 71, 0.08)"
            : isUp
              ? "rgba(156, 39, 176, 0.08)"
              : isExec
                ? "rgba(14, 165, 233, 0.08)"
              : isHighlighted
                ? "rgba(255, 200, 0, 0.10)"
                : "var(--color-surface)";
        return (
          <button
            key={n.id}
            id={domIdForNode(n.id)}
            data-ssot-node-id={n.id}
            type="button"
            onClick={() => onSelect(n.id)}
            title={`${n.id}${n.step.description ? `\n${n.step.description}` : ""}`}
            style={{
              position: "absolute",
              left: n.x,
              top: n.y,
              width: n.width,
              height: n.height,
              padding: 10,
              borderRadius: 14,
              border: `2px solid ${border}`,
              background,
              display: "grid",
              gridTemplateRows: "auto auto",
              gap: 6,
              textAlign: "left",
              overflow: "hidden",
              cursor: "pointer",
            }}
          >
            {isExec ? (
              <span
                className="mono"
                style={{
                  position: "absolute",
                  right: 8,
                  top: 8,
                  fontSize: 11,
                  padding: "2px 6px",
                  borderRadius: 999,
                  border: "1px solid rgba(14, 165, 233, 0.35)",
                  background: "rgba(14, 165, 233, 0.10)",
                  color: "rgba(2, 132, 199, 0.95)",
                }}
              >
                run#{(exec?.firstIndex ?? 0) + 1}×{exec?.count ?? 1}
              </span>
            ) : null}
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
              {badge ? (
                <span className="badge dir" style={{ flex: "none" }}>
                  {badge}
                </span>
              ) : null}
              <div style={{ fontWeight: 800, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {n.step.name || n.step.node_id}
              </div>
            </div>
            <div className="muted small-text" style={{ display: "flex", justifyContent: "space-between", gap: 10, minWidth: 0 }}>
              <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {task ? `${n.id} · TASK:${task}` : n.id}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
