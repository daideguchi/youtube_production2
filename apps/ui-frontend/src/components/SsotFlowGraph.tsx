import { useMemo } from "react";
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
}: {
  steps: SsotCatalogFlowStep[];
  edges: SsotFlowEdge[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  orientation: Orientation;
  highlightedNodeIds?: string[];
}) {
  const highlighted = useMemo(() => new Set(highlightedNodeIds || []), [highlightedNodeIds]);
  const { nodes, paths } = useMemo(() => computeLayout(steps, edges, orientation), [edges, orientation, steps]);

  const width = useMemo(() => {
    if (nodes.length === 0) return 640;
    return Math.max(...nodes.map((n) => n.x + n.width)) + 24;
  }, [nodes]);
  const height = useMemo(() => {
    if (nodes.length === 0) return 240;
    return Math.max(...nodes.map((n) => n.y + n.height)) + 24;
  }, [nodes]);

  return (
    <div style={{ position: "relative", width, height, minHeight: 220 }}>
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
        <defs>
          <marker id="ssotArrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
            <path d="M0,0 L12,6 L0,12 Z" fill="var(--color-border)" />
          </marker>
        </defs>
        {paths.map((p, idx) => (
          <path
            key={idx}
            d={p.d}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={2}
            markerEnd="url(#ssotArrow)"
            opacity={0.9}
          />
        ))}
      </svg>

      {nodes.map((n) => {
        const badge = nodeBadge(n.step);
        const task = nodeTask(n.step);
        const isSelected = selectedNodeId === n.id;
        const isHighlighted = highlighted.has(n.id);
        return (
          <button
            key={n.id}
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
              border: `2px solid ${isSelected ? "var(--color-primary)" : "var(--color-border-muted)"}`,
              background: isSelected
                ? "rgba(25, 118, 210, 0.12)"
                : isHighlighted
                  ? "rgba(255, 200, 0, 0.10)"
                  : "var(--color-surface)",
              display: "grid",
              gridTemplateRows: "auto auto",
              gap: 6,
              textAlign: "left",
              overflow: "hidden",
            }}
          >
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
                {task ? `LLM:${task}` : n.id}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
