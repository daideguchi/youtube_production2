import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";

import "./ThumbnailChatEditorPage.css";

import {
  buildThumbnailLayerSpecs,
  buildThumbnailTwoUp,
  fetchThumbnailCommentPatch,
  fetchThumbnailEditorContext,
  fetchThumbnailOverview,
  fetchThumbnailTextLineSpec,
  previewThumbnailTextLayerSlots,
  resolveApiUrl,
  updateThumbnailTextLineSpec,
  updateThumbnailThumbSpec,
} from "../api/client";
import type { ThumbnailTextLineSpecLine } from "../api/client";
import type {
  ThumbnailChannelBlock,
  ThumbnailCommentPatch,
  ThumbnailCommentPatchOp,
  ThumbnailCommentPatchProviderPreference,
  ThumbnailEditorContext,
} from "../api/types";
import { safeLocalStorage } from "../utils/safeStorage";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  patch?: ThumbnailCommentPatch | null;
};

type CanvasSelectedAsset = "bg" | "portrait" | "text" | null;

type CanvasView = {
  scale: number;
  panX: number;
  panY: number;
};

type CanvasDragState =
  | {
      kind: "bg";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startPanX: number;
      startPanY: number;
      zoom: number;
      width: number;
      height: number;
    }
  | {
      kind: "portrait";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startOffX: number;
      startOffY: number;
      width: number;
      height: number;
    }
  | {
      kind: "text_slot";
      pointerId: number;
      slotKey: string;
      startClientX: number;
      startClientY: number;
      startOffX: number;
      startOffY: number;
      width: number;
      height: number;
    }
  | {
      kind: "text_slot_scale";
      pointerId: number;
      slotKey: string;
      centerClientX: number;
      centerClientY: number;
      startScale: number;
      startDist: number;
    }
  | {
      kind: "text_slot_rotate";
      pointerId: number;
      slotKey: string;
      centerClientX: number;
      centerClientY: number;
      startRotationDeg: number;
      startAngleRad: number;
    }
  | {
      kind: "viewport_pan";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startPanX: number;
      startPanY: number;
      width: number;
      height: number;
      scale: number;
    };

const LAST_SELECTION_KEY = "thumbnailChat:lastSelection";
const LAST_PRESET_KEY = "thumbnailChat:lastPreset";
const PRESETS_KEY = "thumbnailChat:presets:v1";
const LAST_PRESET_ID_KEY = "thumbnailChat:lastPresetId:v1";
const RECENTS_KEY = "thumbnailChat:recents:v1";
const RECENTS_LIMIT = 16;
const COMMENT_PATCH_PROVIDER_KEY = "thumbnailChat:commentPatchProvider:v1";
const COMMENT_PATCH_MODEL_KEY = "thumbnailChat:commentPatchModel:v1";
const SHOW_PICKER_KEY = "thumbnailChat:showPicker:v1";
const SHOW_CHAT_KEY = "thumbnailChat:showChat:v1";
const PREVIEW_MODE_KEY = "thumbnailChat:previewMode:v1";
const CANVAS_GUIDES_KEY = "thumbnailChat:canvasGuides:v1";

const CANVAS_BG_DEFAULT_ZOOM = 1.6;
const CANVAS_BG_MAX_ZOOM = 6.0;
const CANVAS_VIEW_MIN_ZOOM = 1.0;
const CANVAS_VIEW_MAX_ZOOM = 4.0;
const CANVAS_BG_PAN_MIN = -5;
const CANVAS_BG_PAN_MAX = 5;
const CANVAS_OFFSET_MIN = -5;
const CANVAS_OFFSET_MAX = 5;
const TEXT_HISTORY_LIMIT = 60;

type LocalPreset = {
  id: string;
  name: string;
  createdAt: string;
  ops: ThumbnailCommentPatchOp[];
};

type RecentSelection = {
  channel: string;
  video: string;
  stable?: string;
  openedAt: string;
};

type OverridesEditorState = {
  base: Record<string, any>;
  current: Record<string, any>;
  undo: Record<string, any>[];
  redo: Record<string, any>[];
};

type OverridesEditorAction =
  | { type: "reset_base"; base: Record<string, any> }
  | { type: "apply_ops"; ops: ThumbnailCommentPatchOp[] }
  | { type: "merge_patch"; patch: Record<string, any>; recordHistory?: boolean }
  | { type: "push_undo_snapshot"; snapshot: Record<string, any> }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "reset_to_base" };

const OVERRIDES_UNDO_LIMIT = 60;

function leafValueEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (typeof a !== "object") return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

function leafOverridesEqual(a: Record<string, any>, b: Record<string, any>): boolean {
  const aKeys = Object.keys(a ?? {});
  const bKeys = Object.keys(b ?? {});
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (!(key in (b ?? {}))) return false;
    if (!leafValueEqual((a ?? {})[key], (b ?? {})[key])) return false;
  }
  return true;
}

function diffLeafOverridesToOps(base: Record<string, any>, current: Record<string, any>): ThumbnailCommentPatchOp[] {
  const out: ThumbnailCommentPatchOp[] = [];
  const baseKeys = new Set(Object.keys(base ?? {}));
  const currentKeys = new Set(Object.keys(current ?? {}));
  const allKeys = new Set<string>([...Array.from(baseKeys), ...Array.from(currentKeys)]);
  for (const key of Array.from(allKeys).sort()) {
    const hasBase = baseKeys.has(key);
    const hasCurrent = currentKeys.has(key);
    if (!hasCurrent && hasBase) {
      out.push({ op: "unset", path: key });
      continue;
    }
    if (hasCurrent && !hasBase) {
      out.push({ op: "set", path: key, value: (current as any)[key] });
      continue;
    }
    if (hasCurrent && hasBase && !leafValueEqual((base as any)[key], (current as any)[key])) {
      out.push({ op: "set", path: key, value: (current as any)[key] });
    }
  }
  return out;
}

function overridesEditorReducer(state: OverridesEditorState, action: OverridesEditorAction): OverridesEditorState {
  switch (action.type) {
    case "reset_base": {
      const base = action.base ?? {};
      return { base, current: { ...base }, undo: [], redo: [] };
    }
    case "apply_ops": {
      const ops = action.ops ?? [];
      if (!ops.length) return state;
      const next = applyPatchOpsToLeaf(state.current, ops);
      if (leafOverridesEqual(next, state.current)) return state;
      const undo = [...state.undo, state.current].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, current: next, undo, redo: [] };
    }
    case "merge_patch": {
      const patch = action.patch ?? {};
      const entries = Object.entries(patch ?? {});
      if (!entries.length) {
        return state;
      }
      const next: Record<string, any> = { ...(state.current ?? {}) };
      let changed = false;
      entries.forEach(([rawKey, rawValue]) => {
        const key = String(rawKey ?? "").trim();
        if (!key) return;
        const shouldUnset = rawValue === null || rawValue === undefined || rawValue === "";
        if (shouldUnset) {
          if (Object.prototype.hasOwnProperty.call(next, key)) {
            delete next[key];
            changed = true;
          }
          return;
        }
        if (!Object.prototype.hasOwnProperty.call(next, key) || !Object.is(next[key], rawValue)) {
          next[key] = rawValue;
          changed = true;
        }
      });
      if (!changed) return state;
      if (action.recordHistory === false) {
        return { ...state, current: next };
      }
      const undo = [...state.undo, state.current].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, current: next, undo, redo: [] };
    }
    case "push_undo_snapshot": {
      const snapshot = action.snapshot ?? {};
      if (leafOverridesEqual(state.current, snapshot)) {
        return state;
      }
      const undo = [...state.undo, snapshot].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, undo, redo: [] };
    }
    case "reset_to_base": {
      if (leafOverridesEqual(state.current, state.base)) return state;
      const undo = [...state.undo, state.current].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, current: { ...state.base }, undo, redo: [] };
    }
    case "undo": {
      if (!state.undo.length) return state;
      const prev = state.undo[state.undo.length - 1];
      const undo = state.undo.slice(0, -1);
      const redo = [...state.redo, state.current].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, current: prev, undo, redo };
    }
    case "redo": {
      if (!state.redo.length) return state;
      const next = state.redo[state.redo.length - 1];
      const redo = state.redo.slice(0, -1);
      const undo = [...state.undo, state.current].slice(-OVERRIDES_UNDO_LIMIT);
      return { ...state, current: next, undo, redo };
    }
    default:
      return state;
  }
}

function readPresetsFromStorage(): LocalPreset[] {
  const raw = safeLocalStorage.getItem(PRESETS_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const obj = item as any;
        const id = String(obj.id ?? "").trim();
        const name = String(obj.name ?? "").trim();
        const createdAt = String(obj.createdAt ?? "").trim();
        const ops = Array.isArray(obj.ops) ? (obj.ops as ThumbnailCommentPatchOp[]) : [];
        if (!id || !name || !ops.length) return null;
        return { id, name, createdAt: createdAt || new Date().toISOString(), ops } satisfies LocalPreset;
      })
      .filter(Boolean) as LocalPreset[];
  } catch {
    return [];
  }
}

function readRecentsFromStorage(): RecentSelection[] {
  const raw = safeLocalStorage.getItem(RECENTS_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const obj = item as any;
        const channel = normalizeChannel(obj.channel);
        const video = normalizeVideo(obj.video);
        const stable = String(obj.stable ?? "").trim();
        const openedAt = String(obj.openedAt ?? obj.ts ?? "").trim() || new Date().toISOString();
        if (!channel || !video) return null;
        return { channel, video, stable, openedAt } satisfies RecentSelection;
      })
      .filter(Boolean) as RecentSelection[];
  } catch {
    return [];
  }
}

function readProviderPreferenceFromStorage(): ThumbnailCommentPatchProviderPreference {
  const raw = String(safeLocalStorage.getItem(COMMENT_PATCH_PROVIDER_KEY) ?? "").trim();
  switch (raw) {
    case "auto":
    case "codex_exec":
    case "gemini_cli":
    case "qwen_cli":
    case "ollama":
      return raw;
    default:
      return "ollama";
  }
}

function readModelFromStorage(): string {
  return String(safeLocalStorage.getItem(COMMENT_PATCH_MODEL_KEY) ?? "").trim();
}

function readBoolFromStorage(key: string, fallback: boolean): boolean {
  const raw = String(safeLocalStorage.getItem(key) ?? "").trim().toLowerCase();
  if (!raw) return fallback;
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  if (["0", "false", "no", "off"].includes(raw)) return false;
  return fallback;
}

function readPreviewModeFromStorage(): "canvas" | "rendered" {
  const raw = String(safeLocalStorage.getItem(PREVIEW_MODE_KEY) ?? "").trim().toLowerCase();
  if (raw === "rendered") return "rendered";
  return "canvas";
}

function nowId(): string {
  return String(Date.now()) + "." + String(Math.random()).slice(2, 8);
}

function normalizeChannel(value: string | null | undefined): string {
  const raw = String(value ?? "").trim().toUpperCase();
  if (!raw) return "";
  const m = raw.match(/CH\d+/);
  return (m?.[0] ?? raw).trim();
}

function normalizeVideo(value: string | null | undefined): string {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (!digits) return "";
  return String(Number.parseInt(digits, 10)).padStart(3, "0");
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

function hexToRgba(hex: string, alpha: number): string {
  const cleaned = (hex ?? "").trim();
  const match = /^#?([0-9a-fA-F]{6})$/.exec(cleaned);
  if (!match) {
    return `rgba(0,0,0,${clampNumber(alpha, 0, 1)})`;
  }
  const num = Number.parseInt(match[1], 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r},${g},${b},${clampNumber(alpha, 0, 1)})`;
}

function cloneTextLines(lines: Record<string, ThumbnailTextLineSpecLine> | null | undefined): Record<string, ThumbnailTextLineSpecLine> {
  const out: Record<string, ThumbnailTextLineSpecLine> = {};
  Object.entries(lines ?? {}).forEach(([slotKey, line]) => {
    if (!slotKey) return;
    const base = line ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as ThumbnailTextLineSpecLine);
    out[slotKey] = {
      offset_x: Number((base as any).offset_x ?? 0),
      offset_y: Number((base as any).offset_y ?? 0),
      scale: Number((base as any).scale ?? 1),
      rotate_deg: Number((base as any).rotate_deg ?? 0),
    };
  });
  return out;
}

function normalizeRotationDeg(value: number): number {
  if (!Number.isFinite(value)) return 0;
  let v = value;
  while (v > 180) v -= 360;
  while (v < -180) v += 360;
  return v;
}

function leafOverridesToThumbSpecOverrides(overridesLeaf: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [rawPath, value] of Object.entries(overridesLeaf ?? {})) {
    if (value === null || value === undefined || value === "") {
      continue;
    }
    const path = String(rawPath ?? "").trim();
    if (!path.startsWith("overrides.")) {
      continue;
    }
    const parts = path.split(".").filter(Boolean);
    if (parts.length < 2) {
      continue;
    }
    const keys = parts.slice(1);
    if (!keys.length) {
      continue;
    }
    if (keys.length === 1) {
      out[keys[0]] = value;
      continue;
    }
    let cursor: Record<string, any> = out;
    for (let idx = 0; idx < keys.length - 1; idx += 1) {
      const key = keys[idx];
      const next = cursor[key];
      if (next && typeof next === "object" && !Array.isArray(next)) {
        cursor = next as Record<string, any>;
        continue;
      }
      const created: Record<string, any> = {};
      cursor[key] = created;
      cursor = created;
    }
    cursor[keys[keys.length - 1]] = value;
  }
  return out;
}

function applyPatchOpsToLeaf(
  base: Record<string, any>,
  ops: ThumbnailCommentPatchOp[]
): Record<string, any> {
  const next: Record<string, any> = { ...(base ?? {}) };
  (ops ?? []).forEach((op) => {
    const path = String(op?.path ?? "").trim();
    if (!path) return;
    if (op.op === "unset") {
      delete next[path];
      return;
    }
    next[path] = (op as any)?.value;
  });
  return next;
}

function pickThumbnailTextPreviewOverrides(overridesLeaf: Record<string, any>): Record<string, any> {
  const picked: Record<string, any> = {};
  for (const [rawKey, value] of Object.entries(overridesLeaf ?? {})) {
    const key = String(rawKey ?? "");
    if (!key) {
      continue;
    }
    if (
      key === "overrides.text_template_id" ||
      key === "overrides.text_scale" ||
      key.startsWith("overrides.text_effects.") ||
      key.startsWith("overrides.text_fills.") ||
      key.startsWith("overrides.copy_override.")
    ) {
      picked[key] = value;
    }
  }
  return picked;
}

function resolveEditorLeafValue(
  context: ThumbnailEditorContext | null,
  overridesLeaf: Record<string, any>,
  path: string,
  fallback: any
): any {
  const overrides = overridesLeaf ?? {};
  if (Object.prototype.hasOwnProperty.call(overrides, path)) {
    return overrides[path];
  }
  const defaults = context?.defaults_leaf ?? {};
  if (Object.prototype.hasOwnProperty.call(defaults, path)) {
    return (defaults as any)[path];
  }
  return fallback;
}

function hasEditorLeafValue(context: ThumbnailEditorContext | null, overridesLeaf: Record<string, any>, path: string): boolean {
  const overrides = overridesLeaf ?? {};
  if (Object.prototype.hasOwnProperty.call(overrides, path)) {
    return true;
  }
  const defaults = context?.defaults_leaf ?? {};
  return Object.prototype.hasOwnProperty.call(defaults, path);
}

function thumbFileName(stableId: string | null): string {
  const stable = String(stableId ?? "").trim();
  if (stable) {
    return `${stable}.png`;
  }
  return "00_thumb.png";
}

export function ThumbnailChatEditorPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [overview, setOverview] = useState<{ channels: ThumbnailChannelBlock[] } | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);

  const selectedChannel = normalizeChannel(searchParams.get("channel"));
  const selectedVideo = normalizeVideo(searchParams.get("video"));
  const stableRaw = (searchParams.get("stable") ?? "").trim();
  const stableId = stableRaw === "00_thumb_1" || stableRaw === "00_thumb_2" ? stableRaw : "";

  const [context, setContext] = useState<ThumbnailEditorContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [overridesState, dispatchOverrides] = useReducer(overridesEditorReducer, {
    base: {},
    current: {},
    undo: [],
    redo: [],
  });
  const overridesLeaf = overridesState.current;
  const baseOverridesLeaf = overridesState.base;

  const forcedTextTemplateId = String(overridesLeaf?.["overrides.text_template_id"] ?? "").trim();
  const textTemplateId = useMemo(() => {
    const ctx = context;
    const options = (ctx?.template_options ?? []) as Array<{ id: string }>;
    if (!options.length) return "";
    const fallback = String(ctx?.template_id_default ?? "").trim();
    return forcedTextTemplateId || fallback || String(options[0]?.id ?? "");
  }, [context, forcedTextTemplateId]);

  const textSlotBoxes = useMemo(() => {
    const ctx = context;
    const options = (ctx?.template_options ?? []) as Array<{ id: string; slots?: Record<string, { box?: number[] | null }> }>;
    if (!options.length || !textTemplateId) {
      return {};
    }
    const tpl = options.find((opt) => String(opt.id || "").trim() === textTemplateId) ?? options[0];
    const slots = (tpl?.slots ?? {}) as Record<string, { box?: number[] | null }>;
    const out: Record<string, number[]> = {};
    Object.entries(slots).forEach(([slotKey, meta]) => {
      const box = meta?.box ?? null;
      if (!slotKey || !Array.isArray(box) || box.length !== 4) {
        return;
      }
      const nums = box.map((v) => Number(v));
      if (nums.some((v) => !Number.isFinite(v))) {
        return;
      }
      out[slotKey] = nums;
    });
    return out;
  }, [context, textTemplateId]);

  const textSlotKeys = useMemo(
    () => Object.keys(textSlotBoxes ?? {}).filter(Boolean).sort((a, b) => a.localeCompare(b)),
    [textSlotBoxes]
  );

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [previewToken, setPreviewToken] = useState<string>(() => String(Date.now()));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [allowGenerate, setAllowGenerate] = useState(false);
  const [includeThumbCaption, setIncludeThumbCaption] = useState(false);
  const [videoQuery, setVideoQuery] = useState("");
  const [compareMode, setCompareMode] = useState<"after" | "before">("after");
  const [beforeSnapshotUrl, setBeforeSnapshotUrl] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [presetName, setPresetName] = useState("");
  const [presets, setPresets] = useState<LocalPreset[]>(() => readPresetsFromStorage());
  const [recents, setRecents] = useState<RecentSelection[]>(() => readRecentsFromStorage());
  const [providerPreference, setProviderPreference] = useState<ThumbnailCommentPatchProviderPreference>(() =>
    readProviderPreferenceFromStorage()
  );
  const [providerModel, setProviderModel] = useState<string>(() => readModelFromStorage());
  const [showPicker, setShowPicker] = useState<boolean>(() => readBoolFromStorage(SHOW_PICKER_KEY, true));
  const [showChat, setShowChat] = useState<boolean>(() => readBoolFromStorage(SHOW_CHAT_KEY, true));
  const [previewMode, setPreviewMode] = useState<"canvas" | "rendered">(() => readPreviewModeFromStorage());

  const [canvasGuidesEnabled, setCanvasGuidesEnabled] = useState<boolean>(() => readBoolFromStorage(CANVAS_GUIDES_KEY, true));
  const [canvasSelectedAsset, setCanvasSelectedAsset] = useState<CanvasSelectedAsset>(null);
  const [canvasView, setCanvasView] = useState<CanvasView>({ scale: 1, panX: 0, panY: 0 });
  const canvasViewRef = useRef<CanvasView>({ scale: 1, panX: 0, panY: 0 });
  const [canvasHandMode, setCanvasHandMode] = useState(false);
  const [canvasSpaceHeld, setCanvasSpaceHeld] = useState(false);
  const canvasHandActive = canvasHandMode || canvasSpaceHeld;
  const canvasHandActiveRef = useRef(false);
  const [canvasPanningView, setCanvasPanningView] = useState(false);
  const [canvasSnapGuides, setCanvasSnapGuides] = useState<{ xNorm: number | null; yNorm: number | null } | null>(null);
  const [hoveredTextSlot, setHoveredTextSlot] = useState<string | null>(null);
  const [selectedTextSlot, setSelectedTextSlot] = useState<string | null>(null);
  const [textLineSpecLines, setTextLineSpecLines] = useState<Record<string, ThumbnailTextLineSpecLine>>({});
  const [baseTextLineSpecLines, setBaseTextLineSpecLines] = useState<Record<string, ThumbnailTextLineSpecLine>>({});
  const [textLineSpecStatus, setTextLineSpecStatus] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });
  const [textUndo, setTextUndo] = useState<Record<string, ThumbnailTextLineSpecLine>[]>([]);
  const [textRedo, setTextRedo] = useState<Record<string, ThumbnailTextLineSpecLine>[]>([]);
  const [textSlotImages, setTextSlotImages] = useState<Record<string, string>>({});
  const [textSlotStatus, setTextSlotStatus] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });
  const [bgPreviewSrc, setBgPreviewSrc] = useState<string | null>(null);
  const [portraitPreviewSrc, setPortraitPreviewSrc] = useState<string | null>(null);
  const [stageSize, setStageSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const focusPanelsRef = useRef<{ showPicker: boolean; showChat: boolean } | null>(null);
  const canvasDragRef = useRef<CanvasDragState | null>(null);
  const canvasDragStartOverridesRef = useRef<Record<string, any> | null>(null);
  const canvasDragStartTextLinesRef = useRef<Record<string, ThumbnailTextLineSpecLine> | null>(null);
  const overridesInteractionStartRef = useRef<Record<string, any> | null>(null);
  const textInteractionStartRef = useRef<Record<string, ThumbnailTextLineSpecLine> | null>(null);
  const textSlotRequestRef = useRef(0);
  const textLegacyMigrationRef = useRef<Record<string, boolean>>({});
  const textLineSpecRef = useRef<Record<string, ThumbnailTextLineSpecLine>>({});

  const channels = useMemo(() => overview?.channels ?? [], [overview]);
  const channelBlock = useMemo(
    () => channels.find((c) => c.channel === selectedChannel) ?? null,
    [channels, selectedChannel]
  );
  const availableVideos = useMemo(() => (channelBlock?.projects ?? []).slice(), [channelBlock?.projects]);
  const allVideos = useMemo(() => {
    const out: any[] = [];
    (channels ?? []).forEach((block) => {
      (block?.projects ?? []).forEach((p) => out.push(p));
    });
    return out;
  }, [channels]);
  const filteredVideos = useMemo(() => {
    const q = videoQuery.trim().toLowerCase();
    const base = selectedChannel ? availableVideos : allVideos;
    if (!q) return base;
    const filtered = base.filter((p) => {
      const vid = String(p.video ?? "").padStart(3, "0");
      const ch = String(p.channel ?? "").toLowerCase();
      const title = String((p.title ?? p.sheet_title ?? "") as any).toLowerCase();
      return vid.includes(q) || title.includes(q) || ch.includes(q);
    });
    const selected = base.find(
      (p) =>
        String(p.channel ?? "").toUpperCase() === selectedChannel &&
        String(p.video ?? "").padStart(3, "0") === selectedVideo
    );
    if (selected && !filtered.some((p) => String(p.video ?? "").padStart(3, "0") === selectedVideo)) {
      return [selected, ...filtered];
    }
    return filtered;
  }, [allVideos, availableVideos, selectedChannel, selectedVideo, videoQuery]);

  const selectedVideoIndex = useMemo(
    () =>
      filteredVideos.findIndex(
        (p) =>
          String(p.channel ?? "").toUpperCase() === selectedChannel &&
          String(p.video ?? "").padStart(3, "0") === selectedVideo
      ),
    [filteredVideos, selectedChannel, selectedVideo]
  );
  const prevVideo = selectedVideoIndex > 0 ? filteredVideos[selectedVideoIndex - 1] : null;
  const nextVideo =
    selectedVideoIndex >= 0 && selectedVideoIndex < filteredVideos.length - 1 ? filteredVideos[selectedVideoIndex + 1] : null;

  const canUndo = overridesState.undo.length > 0 || textUndo.length > 0;
  const canRedo = overridesState.redo.length > 0 || textRedo.length > 0;
  const focusModeActive = !showPicker && !showChat;
  const isDirty = !leafOverridesEqual(overridesLeaf, baseOverridesLeaf);
  const dirtyOps = useMemo(() => diffLeafOverridesToOps(baseOverridesLeaf, overridesLeaf), [baseOverridesLeaf, overridesLeaf]);
  const textLineSignature = useMemo(() => {
    const entries = Object.entries(textLineSpecLines ?? {})
      .map(([slotKey, line]) => [
        slotKey,
        Number((line as any)?.offset_x ?? 0),
        Number((line as any)?.offset_y ?? 0),
        Number((line as any)?.scale ?? 1),
        Number((line as any)?.rotate_deg ?? 0),
      ])
      .sort(([a], [b]) => String(a).localeCompare(String(b)));
    return JSON.stringify(entries);
  }, [textLineSpecLines]);
  const baseTextLineSignature = useMemo(() => {
    const entries = Object.entries(baseTextLineSpecLines ?? {})
      .map(([slotKey, line]) => [
        slotKey,
        Number((line as any)?.offset_x ?? 0),
        Number((line as any)?.offset_y ?? 0),
        Number((line as any)?.scale ?? 1),
        Number((line as any)?.rotate_deg ?? 0),
      ])
      .sort(([a], [b]) => String(a).localeCompare(String(b)));
    return JSON.stringify(entries);
  }, [baseTextLineSpecLines]);
  const isTextDirty = textLineSignature !== baseTextLineSignature;
  const isAnyDirty = isDirty || isTextDirty;

  const textPreviewOverrides = useMemo(() => pickThumbnailTextPreviewOverrides(overridesLeaf ?? {}), [overridesLeaf]);
  const textPreviewOverridesSignature = useMemo(() => {
    const entries = Object.entries(textPreviewOverrides ?? {}).sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify({ stable: stableId || "", entries });
  }, [stableId, textPreviewOverrides]);
  const textPreviewOverridesForSlots = useMemo(() => {
    try {
      const parsed = JSON.parse(textPreviewOverridesSignature) as { entries?: Array<[string, any]> };
      const entries = Array.isArray(parsed?.entries) ? parsed.entries : [];
      return Object.fromEntries(entries);
    } catch {
      return {};
    }
  }, [textPreviewOverridesSignature]);
  const textPreviewLineSignature = useMemo(() => {
    const entries = Object.entries(textLineSpecLines ?? {})
      .map(([slotKey, line]) => [slotKey, Number((line as any)?.scale ?? 1)] as const)
      .sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify(entries);
  }, [textLineSpecLines]);

  const applyParams = useCallback(
    (patch: { channel?: string; video?: string; stable?: string }, opts?: { replace?: boolean }) => {
      const params = new URLSearchParams(searchParams);
      if (patch.channel !== undefined) {
        const next = normalizeChannel(patch.channel);
        if (next) params.set("channel", next);
        else params.delete("channel");
      }
      if (patch.video !== undefined) {
        const next = normalizeVideo(patch.video);
        if (next) params.set("video", next);
        else params.delete("video");
      }
      if (patch.stable !== undefined) {
        const next = String(patch.stable ?? "").trim();
        if (next) params.set("stable", next);
        else params.delete("stable");
      }
      setSearchParams(params, { replace: opts?.replace ?? true });
    },
    [searchParams, setSearchParams]
  );

  const previewUrl = useMemo(() => {
    if (!selectedChannel || !selectedVideo) return "";
    const file = thumbFileName(stableId || null);
    const base = resolveApiUrl(`/thumbnails/assets/${encodeURIComponent(selectedChannel)}/${encodeURIComponent(selectedVideo)}/${encodeURIComponent(file)}`);
    const sep = base.includes("?") ? "&" : "?";
    return `${base}${sep}t=${encodeURIComponent(previewToken)}`;
  }, [previewToken, selectedChannel, selectedVideo, stableId]);
  const displayPreviewUrl = compareMode === "before" && beforeSnapshotUrl ? beforeSnapshotUrl : previewUrl;

  const bgCandidates = useMemo(() => {
    if (!selectedChannel || !selectedVideo) return [];
    const token = String(previewToken ?? "").trim();
    const add = (url: string) => {
      if (!url) return url;
      const sep = url.includes("?") ? "&" : "?";
      return token ? `${url}${sep}t=${encodeURIComponent(token)}` : url;
    };
    const base = (name: string) => resolveApiUrl(`/thumbnails/assets/${selectedChannel}/${selectedVideo}/${name}`);
    return [
      add(base("10_bg.png")),
      add(base("10_bg.jpg")),
      add(base("10_bg.jpeg")),
      add(base("10_bg.webp")),
      add(base("90_bg_ai_raw.png")),
      add(base("90_bg_ai_raw.jpg")),
      add(base("90_bg_ai_raw.jpeg")),
      add(base("90_bg_ai_raw.webp")),
    ];
  }, [previewToken, selectedChannel, selectedVideo]);

  const portraitCandidates = useMemo(() => {
    if (!selectedChannel || !selectedVideo) return [];
    const token = String(previewToken ?? "").trim();
    const add = (url: string) => {
      if (!url) return url;
      const sep = url.includes("?") ? "&" : "?";
      return token ? `${url}${sep}t=${encodeURIComponent(token)}` : url;
    };
    const base = (name: string) => resolveApiUrl(`/thumbnails/assets/${selectedChannel}/${selectedVideo}/${name}`);
    return [
      add(base("20_portrait.png")),
      add(base("20_portrait.jpg")),
      add(base("20_portrait.jpeg")),
      add(base("20_portrait.webp")),
    ];
  }, [previewToken, selectedChannel, selectedVideo]);

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    setOverviewError(null);
    try {
      const data = await fetchThumbnailOverview();
      setOverview(data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setOverviewError(msg);
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  const loadContext = useCallback(async () => {
    if (!selectedChannel || !selectedVideo) {
      setContext(null);
      setContextError(null);
      setContextLoading(false);
      dispatchOverrides({ type: "reset_base", base: {} });
      setTextLineSpecLines({});
      setBaseTextLineSpecLines({});
      setTextLineSpecStatus({ loading: false, error: null });
      return;
    }
    setContextLoading(true);
    setContextError(null);
    setTextLineSpecStatus({ loading: true, error: null });
    try {
      const ctx = await fetchThumbnailEditorContext(selectedChannel, selectedVideo, { stable: stableId || null });
      setContext(ctx);
      dispatchOverrides({ type: "reset_base", base: ctx.overrides_leaf ?? {} });

      try {
        const result = await fetchThumbnailTextLineSpec(selectedChannel, selectedVideo, stableId || null);
        const rawLines = (result?.lines ?? {}) as Record<
          string,
          { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }
        >;
        const slotKeys = (() => {
          const options = (ctx?.template_options ?? []) as Array<{ id: string; slots?: Record<string, unknown> }>;
          if (!options.length) {
            return Object.keys(rawLines ?? {});
          }
          const forced = String((ctx?.overrides_leaf ?? {})["overrides.text_template_id"] ?? "").trim();
          const fallback = String(ctx?.template_id_default ?? "").trim();
          const templateId = forced || fallback || String(options[0]?.id ?? "");
          const tpl = options.find((opt) => String(opt.id || "").trim() === templateId) ?? options[0];
          const slots = (tpl?.slots ?? {}) as Record<string, unknown>;
          const keys = Object.keys(slots).filter(Boolean);
          return keys.length ? keys : Object.keys(rawLines ?? {});
        })();

        const asNum = (value: any, fallback: number) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : fallback;
        };

        const merged: Record<string, ThumbnailTextLineSpecLine> = {};
        for (const slotKey of slotKeys) {
          const line = rawLines?.[slotKey];
          merged[slotKey] = {
            offset_x: clampNumber(asNum(line?.offset_x, 0), CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
            offset_y: clampNumber(asNum(line?.offset_y, 0), CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
            scale: clampNumber(asNum(line?.scale, 1), 0.25, 4),
            rotate_deg: clampNumber(asNum(line?.rotate_deg, 0), -180, 180),
          };
        }
        Object.entries(rawLines ?? {}).forEach(([slotKey, line]) => {
          if (!slotKey || Object.prototype.hasOwnProperty.call(merged, slotKey)) {
            return;
          }
          merged[slotKey] = {
            offset_x: clampNumber(asNum(line?.offset_x, 0), CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
            offset_y: clampNumber(asNum(line?.offset_y, 0), CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
            scale: clampNumber(asNum(line?.scale, 1), 0.25, 4),
            rotate_deg: clampNumber(asNum(line?.rotate_deg, 0), -180, 180),
          };
        });

        setTextLineSpecLines(merged);
        setBaseTextLineSpecLines(merged);
        setTextLineSpecStatus({ loading: false, error: null });
        setSelectedTextSlot((current) => {
          if (current && Object.prototype.hasOwnProperty.call(merged, current)) {
            return current;
          }
          const keys = Object.keys(merged);
          return keys.length ? keys[0] : current;
        });
      } catch (error) {
        setTextLineSpecLines({});
        setBaseTextLineSpecLines({});
        setTextLineSpecStatus({ loading: false, error: error instanceof Error ? error.message : String(error) });
      }
    } catch (error) {
      setContext(null);
      dispatchOverrides({ type: "reset_base", base: {} });
      setContextError(error instanceof Error ? error.message : String(error));
      setTextLineSpecLines({});
      setBaseTextLineSpecLines({});
      setTextLineSpecStatus({ loading: false, error: error instanceof Error ? error.message : String(error) });
    } finally {
      setContextLoading(false);
    }
  }, [dispatchOverrides, selectedChannel, selectedVideo, stableId]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    if (selectedChannel && selectedVideo) return;
    if (overviewLoading) return;
    const lastRaw = safeLocalStorage.getItem(LAST_SELECTION_KEY);
    if (!lastRaw) return;
    try {
      const parsed = JSON.parse(lastRaw) as { channel?: string; video?: string; stable?: string };
      const ch = normalizeChannel(parsed?.channel);
      const vid = normalizeVideo(parsed?.video);
      const st = String(parsed?.stable ?? "").trim();
      if (!ch || !vid) return;
      applyParams({ channel: ch, video: vid, stable: st }, { replace: true });
    } catch {
      /* ignore */
    }
  }, [applyParams, overviewLoading, selectedChannel, selectedVideo]);

  useEffect(() => {
    void loadContext();
    setMessages([]);
    setSaveError(null);
    setPreviewToken(String(Date.now()));
    setCompareMode("after");
    setBeforeSnapshotUrl(null);
    setToast(null);
    setCanvasSelectedAsset(null);
    setHoveredTextSlot(null);
    setSelectedTextSlot(null);
    setTextLineSpecLines({});
    setBaseTextLineSpecLines({});
    setTextLineSpecStatus({ loading: false, error: null });
    setTextUndo([]);
    setTextRedo([]);
    setTextSlotImages({});
    setTextSlotStatus({ loading: false, error: null });
    textSlotRequestRef.current += 1;
  }, [loadContext]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) {
      setBgPreviewSrc(null);
      setPortraitPreviewSrc(null);
      return;
    }
    setBgPreviewSrc(bgCandidates[0] ?? null);
    setPortraitPreviewSrc(portraitCandidates[0] ?? null);
  }, [bgCandidates, portraitCandidates, selectedChannel, selectedVideo]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) return;
    if (!context || contextLoading) return;
    if (!Object.keys(textLineSpecLines ?? {}).length) return;

    const key = `${selectedChannel}-${selectedVideo}-${stableId || ""}`;
    if (textLegacyMigrationRef.current[key]) return;

    const rawOffX = Number(overridesLeaf?.["overrides.text_offset_x"] ?? 0);
    const rawOffY = Number(overridesLeaf?.["overrides.text_offset_y"] ?? 0);
    const rawScale = Number(overridesLeaf?.["overrides.text_scale"] ?? 1);
    const offX = Number.isFinite(rawOffX) ? rawOffX : 0;
    const offY = Number.isFinite(rawOffY) ? rawOffY : 0;
    const scale = Number.isFinite(rawScale) ? rawScale : 1;

    const hasLegacyOffsets = Math.abs(offX) > 1e-9 || Math.abs(offY) > 1e-9;
    const hasLegacyScale = Math.abs(scale - 1) > 1e-6;
    if (!hasLegacyOffsets && !hasLegacyScale) {
      textLegacyMigrationRef.current[key] = true;
      return;
    }

    setTextLineSpecLines((current) => {
      const next = { ...(current ?? {}) };
      const factor = clampNumber(scale, 0.25, 4.0);
      Object.entries(next).forEach(([slotKey, line]) => {
        if (!slotKey) return;
        const base = (line ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as ThumbnailTextLineSpecLine;
        next[slotKey] = {
          offset_x: clampNumber(Number(base.offset_x ?? 0) + offX, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
          offset_y: clampNumber(Number(base.offset_y ?? 0) + offY, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX),
          scale: clampNumber(Number(base.scale ?? 1) * factor, 0.25, 4),
          rotate_deg: clampNumber(Number((base as any).rotate_deg ?? 0), -180, 180),
        };
      });
      return next;
    });

    dispatchOverrides({
      type: "merge_patch",
      patch: {
        "overrides.text_offset_x": null,
        "overrides.text_offset_y": null,
        "overrides.text_scale": null,
      },
      recordHistory: false,
    });

    textLegacyMigrationRef.current[key] = true;
  }, [context, contextLoading, dispatchOverrides, overridesLeaf, selectedChannel, selectedVideo, stableId, textLineSpecLines]);

  useEffect(() => {
    textLineSpecRef.current = textLineSpecLines ?? {};
  }, [textLineSpecLines]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) {
      setTextSlotImages({});
      setTextSlotStatus({ loading: false, error: null });
      return;
    }
    if (contextLoading) {
      return;
    }
    const requestId = (textSlotRequestRef.current += 1);
    setTextSlotStatus({ loading: true, error: null });

    const timer = window.setTimeout(() => {
      previewThumbnailTextLayerSlots(selectedChannel, selectedVideo, textPreviewOverridesForSlots, {
        stable: stableId || null,
        lines: textLineSpecRef.current ?? {},
      })
        .then((result) => {
          if (textSlotRequestRef.current !== requestId) {
            return;
          }
          const images = (result?.images ?? {}) as Record<string, { image_url: string }>;
          const next: Record<string, string> = {};
          Object.entries(images).forEach(([slotKey, value]) => {
            const urlRaw = value?.image_url;
            if (!slotKey || !urlRaw) {
              return;
            }
            next[slotKey] = resolveApiUrl(`${urlRaw}?v=${requestId}`);
          });
          setTextSlotImages(next);
          setTextSlotStatus({ loading: false, error: null });
          setSelectedTextSlot((current) => {
            if (current && Object.prototype.hasOwnProperty.call(next, current)) {
              return current;
            }
            const keys = Object.keys(next);
            return keys.length ? keys[0] : current;
          });
        })
        .catch((error) => {
          if (textSlotRequestRef.current !== requestId) {
            return;
          }
          const message = error instanceof Error ? error.message : String(error);
          setTextSlotStatus({ loading: false, error: message });
        });
    }, 180);

    return () => window.clearTimeout(timer);
  }, [
    contextLoading,
    selectedChannel,
    selectedVideo,
    stableId,
    textPreviewLineSignature,
    textPreviewOverridesSignature,
    textPreviewOverridesForSlots,
  ]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) return;
    safeLocalStorage.setItem(
      LAST_SELECTION_KEY,
      JSON.stringify({ channel: selectedChannel, video: selectedVideo, stable: stableId || "" })
    );
  }, [selectedChannel, selectedVideo, stableId]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) return;
    const entry: RecentSelection = {
      channel: selectedChannel,
      video: selectedVideo,
      stable: stableId || "",
      openedAt: new Date().toISOString(),
    };
    setRecents((prev) => {
      const base = Array.isArray(prev) ? prev : [];
      const next = [
        entry,
        ...base.filter(
          (item) =>
            !(item.channel === entry.channel && item.video === entry.video && String(item.stable ?? "") === String(entry.stable ?? ""))
        ),
      ];
      return next.slice(0, RECENTS_LIMIT);
    });
  }, [selectedChannel, selectedVideo, stableId]);

  useEffect(() => {
    safeLocalStorage.setItem(RECENTS_KEY, JSON.stringify(recents ?? []));
  }, [recents]);

  useEffect(() => {
    setVideoQuery("");
  }, [selectedChannel]);

  useEffect(() => {
    if (!chatEndRef.current) return;
    chatEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  useEffect(() => {
    if (!beforeSnapshotUrl) return;
    return () => {
      try {
        URL.revokeObjectURL(beforeSnapshotUrl);
      } catch {
        /* ignore */
      }
    };
  }, [beforeSnapshotUrl]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    safeLocalStorage.setItem(PRESETS_KEY, JSON.stringify(presets ?? []));
  }, [presets]);

  useEffect(() => {
    safeLocalStorage.setItem(COMMENT_PATCH_PROVIDER_KEY, providerPreference);
  }, [providerPreference]);

  useEffect(() => {
    safeLocalStorage.setItem(COMMENT_PATCH_MODEL_KEY, providerModel);
  }, [providerModel]);

  useEffect(() => {
    safeLocalStorage.setItem(SHOW_PICKER_KEY, showPicker ? "1" : "0");
  }, [showPicker]);

  useEffect(() => {
    safeLocalStorage.setItem(SHOW_CHAT_KEY, showChat ? "1" : "0");
  }, [showChat]);

  useEffect(() => {
    safeLocalStorage.setItem(PREVIEW_MODE_KEY, previewMode);
  }, [previewMode]);

  useEffect(() => {
    safeLocalStorage.setItem(CANVAS_GUIDES_KEY, canvasGuidesEnabled ? "1" : "0");
  }, [canvasGuidesEnabled]);

  useEffect(() => {
    canvasViewRef.current = canvasView;
  }, [canvasView]);

  useEffect(() => {
    canvasHandActiveRef.current = canvasHandActive;
  }, [canvasHandActive]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      const width = Math.max(0, Math.round(rect.width));
      const height = Math.max(0, Math.round(rect.height));
      setStageSize({ width, height });
    };

    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    if (!selectedChannel || !selectedVideo) return;
    setDraft("");
    setSending(true);
    setSaveError(null);
    const userMsg: ChatMessage = { id: nowId(), role: "user", text };
    setMessages((prev) => [...prev, userMsg]);
    try {
      const patch = await fetchThumbnailCommentPatch(selectedChannel, selectedVideo, {
        comment: text,
        include_thumb_caption: includeThumbCaption,
        provider_preference: providerPreference,
        model: providerPreference === "ollama" && providerModel.trim() ? providerModel.trim() : undefined,
      });
      const assistantMsg: ChatMessage = {
        id: nowId(),
        role: "assistant",
        text: patch.clarifying_questions?.length
          ? patch.clarifying_questions.join("\n")
          : patch.ops?.length
            ? `提案: ${patch.ops.length}件（provider=${patch.provider ?? "unknown"}）`
            : "提案が空でした。もう少し具体的に指示してください。",
        patch,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { id: nowId(), role: "assistant", text: `エラー: ${msg}`, patch: null },
      ]);
    } finally {
      setSending(false);
    }
  }, [draft, includeThumbCaption, providerModel, providerPreference, selectedChannel, selectedVideo, sending]);

  const lastPatch = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const p = messages[i]?.patch;
      if (p) return p;
    }
    return null;
  }, [messages]);

  const applyPatchToLocal = useCallback(
    (patch: ThumbnailCommentPatch) => {
      const ops = patch?.ops ?? [];
      if (!ops.length) return;
      dispatchOverrides({ type: "apply_ops", ops });
      safeLocalStorage.setItem(LAST_PRESET_KEY, JSON.stringify({ ops, label: patch.provider ?? "preset" }));
      setToast({ type: "success", message: `適用: ${ops.length} ops` });
    },
    [dispatchOverrides]
  );

  const applySavedPreset = useCallback(() => {
    const lastPresetId = safeLocalStorage.getItem(LAST_PRESET_ID_KEY);
    if (lastPresetId) {
      const preset = presets.find((p) => p.id === lastPresetId) ?? null;
      if (preset?.ops?.length) {
        dispatchOverrides({ type: "apply_ops", ops: preset.ops });
        safeLocalStorage.setItem(LAST_PRESET_KEY, JSON.stringify({ ops: preset.ops, label: preset.name }));
        setToast({ type: "success", message: `プリセット適用: ${preset.name}` });
        return;
      }
    }
    const raw = safeLocalStorage.getItem(LAST_PRESET_KEY);
    if (!raw) {
      setToast({ type: "error", message: "保存済みプリセットが見つかりませんでした。" });
      return;
    }
    try {
      const parsed = JSON.parse(raw) as { ops?: ThumbnailCommentPatchOp[] };
      const ops = Array.isArray(parsed?.ops) ? parsed.ops : [];
      if (!ops.length) {
        setToast({ type: "error", message: "プリセットが空でした。" });
        return;
      }
      dispatchOverrides({ type: "apply_ops", ops });
      setToast({ type: "success", message: `クイック適用: ${ops.length} ops` });
    } catch {
      /* ignore */
    }
  }, [dispatchOverrides, presets]);

  const pushTextUndoSnapshot = useCallback((snapshot: Record<string, ThumbnailTextLineSpecLine>) => {
    setTextUndo((prev) => [...(prev ?? []), cloneTextLines(snapshot)].slice(-TEXT_HISTORY_LIMIT));
    setTextRedo([]);
  }, []);

  const handleUndoAny = useCallback(() => {
    if ((canvasSelectedAsset === "text" || overridesState.undo.length === 0) && textUndo.length) {
      const prevSnap = textUndo[textUndo.length - 1];
      setTextUndo((prev) => prev.slice(0, -1));
      setTextRedo((prev) => [...(prev ?? []), cloneTextLines(textLineSpecRef.current)].slice(-TEXT_HISTORY_LIMIT));
      setTextLineSpecLines(cloneTextLines(prevSnap));
      return;
    }
    dispatchOverrides({ type: "undo" });
  }, [canvasSelectedAsset, dispatchOverrides, overridesState.undo.length, textUndo]);

  const handleRedoAny = useCallback(() => {
    if ((canvasSelectedAsset === "text" || overridesState.redo.length === 0) && textRedo.length) {
      const nextSnap = textRedo[textRedo.length - 1];
      setTextRedo((prev) => prev.slice(0, -1));
      setTextUndo((prev) => [...(prev ?? []), cloneTextLines(textLineSpecRef.current)].slice(-TEXT_HISTORY_LIMIT));
      setTextLineSpecLines(cloneTextLines(nextSnap));
      return;
    }
    dispatchOverrides({ type: "redo" });
  }, [canvasSelectedAsset, dispatchOverrides, overridesState.redo.length, textRedo]);

  const captureBeforeSnapshot = useCallback(async () => {
    if (!previewUrl) return;
    try {
      const response = await fetch(previewUrl, { cache: "no-store" });
      if (!response.ok) return;
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setBeforeSnapshotUrl(url);
    } catch {
      /* ignore */
    }
  }, [previewUrl]);

  const saveAndBuildLeaf = useCallback(
    async (leaf: Record<string, any>) => {
      if (!selectedChannel || !selectedVideo) return;
      if (saving) return;
      setSaving(true);
      setSaveError(null);
      setCompareMode("after");
      try {
        if (previewUrl) {
          await captureBeforeSnapshot();
        } else {
          setBeforeSnapshotUrl(null);
        }

        if (isTextDirty && Object.keys(textLineSpecLines ?? {}).length) {
          await updateThumbnailTextLineSpec(selectedChannel, selectedVideo, stableId || null, textLineSpecLines ?? {});
        }

        const overrides = leafOverridesToThumbSpecOverrides(leaf);
        await updateThumbnailThumbSpec(selectedChannel, selectedVideo, overrides, { stable: stableId || null });
        if (stableId) {
          await buildThumbnailTwoUp(selectedChannel, selectedVideo, {
            allow_generate: allowGenerate,
            regen_bg: false,
            output_mode: "draft",
          });
        } else {
          await buildThumbnailLayerSpecs(selectedChannel, selectedVideo, {
            allow_generate: allowGenerate,
            regen_bg: false,
            output_mode: "draft",
          });
        }
        setPreviewToken(String(Date.now()));
        await loadContext();
        setToast({ type: "success", message: "保存＆再合成が完了しました（Before/Afterで比較できます）" });
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        setSaveError(msg);
        setToast({ type: "error", message: `保存/再合成に失敗しました: ${msg}` });
      } finally {
        setSaving(false);
      }
    },
    [
      allowGenerate,
      captureBeforeSnapshot,
      isTextDirty,
      loadContext,
      previewUrl,
      saving,
      selectedChannel,
      selectedVideo,
      stableId,
      textLineSpecLines,
    ]
  );

  const handleSaveAndBuild = useCallback(async () => {
    await saveAndBuildLeaf(overridesLeaf);
  }, [overridesLeaf, saveAndBuildLeaf]);

  const handleApplyPatchAndSave = useCallback(
    async (patch: ThumbnailCommentPatch) => {
      const ops = patch?.ops ?? [];
      if (!ops.length) return;
      const leafNext = applyPatchOpsToLeaf(overridesLeaf, ops);
      dispatchOverrides({ type: "apply_ops", ops });
      safeLocalStorage.setItem(LAST_PRESET_KEY, JSON.stringify({ ops, label: patch.provider ?? "preset" }));
      await saveAndBuildLeaf(leafNext);
    },
    [dispatchOverrides, overridesLeaf, saveAndBuildLeaf]
  );

  const createPreset = useCallback(
    (ops: ThumbnailCommentPatchOp[], name: string) => {
      const cleaned = String(name ?? "").trim();
      const resolvedName = cleaned || `Preset ${new Date().toISOString().slice(0, 16).replace("T", " ")}`;
      const preset: LocalPreset = { id: nowId(), name: resolvedName, createdAt: new Date().toISOString(), ops };
      setPresets((prev) => [preset, ...(prev ?? [])].slice(0, 40));
      safeLocalStorage.setItem(LAST_PRESET_ID_KEY, preset.id);
      safeLocalStorage.setItem(LAST_PRESET_KEY, JSON.stringify({ ops, label: preset.name }));
      setPresetName("");
      setToast({ type: "success", message: `プリセット保存: ${preset.name}` });
    },
    []
  );

  const handleSavePresetFromCurrent = useCallback(() => {
    if (!dirtyOps.length) {
      setToast({ type: "error", message: "保存する変更がありません（未保存の変更が0件です）。" });
      return;
    }
    createPreset(dirtyOps, presetName || `Diff ${selectedChannel || ""}-${selectedVideo || ""}`.trim());
  }, [createPreset, dirtyOps, presetName, selectedChannel, selectedVideo]);

  const handleSavePresetFromLastSuggestion = useCallback(() => {
    const ops = (lastPatch?.ops ?? []).slice();
    if (!ops.length) {
      setToast({ type: "error", message: "保存する提案がありません（チャットの提案が空です）。" });
      return;
    }
    createPreset(ops, presetName || `AI ${lastPatch?.provider ?? "suggestion"}`);
  }, [createPreset, lastPatch, presetName]);

  const applyNamedPreset = useCallback(
    (preset: LocalPreset) => {
      if (!preset?.ops?.length) return;
      dispatchOverrides({ type: "apply_ops", ops: preset.ops });
      safeLocalStorage.setItem(LAST_PRESET_ID_KEY, preset.id);
      safeLocalStorage.setItem(LAST_PRESET_KEY, JSON.stringify({ ops: preset.ops, label: preset.name }));
      setToast({ type: "success", message: `プリセット適用: ${preset.name}` });
    },
    [dispatchOverrides]
  );

  const deletePreset = useCallback((preset: LocalPreset) => {
    const ok = window.confirm(`プリセット「${preset.name}」を削除しますか？`);
    if (!ok) return;
    setPresets((prev) => (prev ?? []).filter((p) => p.id !== preset.id));
    setToast({ type: "success", message: `削除しました: ${preset.name}` });
  }, []);

  const beginCanvasBgDrag = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!context) return;
      if (event.button !== 0) return;

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      canvasDragStartOverridesRef.current = { ...(overridesLeaf ?? {}) };
      setCanvasSelectedAsset("bg");

      const rawZoom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.zoom", 1.0));
      const zoomOverridden = Object.prototype.hasOwnProperty.call(overridesLeaf ?? {}, "overrides.bg_pan_zoom.zoom");
      const zoomFallback =
        !zoomOverridden && (!Number.isFinite(rawZoom) || rawZoom <= 1.0001) ? CANVAS_BG_DEFAULT_ZOOM : rawZoom;
      const zoom = clampNumber(zoomFallback, 1.0, CANVAS_BG_MAX_ZOOM);
      if (!zoomOverridden && zoom !== rawZoom) {
        dispatchOverrides({
          type: "merge_patch",
          patch: { "overrides.bg_pan_zoom.zoom": Number(zoom.toFixed(3)) },
          recordHistory: false,
        });
      }

      const panX = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.pan_x", 0.0));
      const panY = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.pan_y", 0.0));
      canvasDragRef.current = {
        kind: "bg",
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startPanX: panX,
        startPanY: panY,
        zoom,
        width,
        height,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      event.preventDefault();
    },
    [context, dispatchOverrides, overridesLeaf, stageSize.height, stageSize.width]
  );

  const beginCanvasPortraitDrag = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!context) return;
      if (event.button !== 0) return;
      event.stopPropagation();

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      canvasDragStartOverridesRef.current = { ...(overridesLeaf ?? {}) };
      setCanvasSelectedAsset("portrait");

      const offX = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_x", 0.0));
      const offY = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_y", 0.0));
      canvasDragRef.current = {
        kind: "portrait",
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startOffX: offX,
        startOffY: offY,
        width,
        height,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      event.preventDefault();
    },
    [context, overridesLeaf, stageSize.height, stageSize.width]
  );

  const beginCanvasTextSlotDrag = useCallback(
    (event: PointerEvent<HTMLDivElement>, slotKey: string) => {
      if (event.button !== 0) return;
      const key = String(slotKey ?? "").trim();
      if (!key) return;
      event.stopPropagation();

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      canvasDragStartTextLinesRef.current = cloneTextLines(textLineSpecRef.current);
      setCanvasSelectedAsset("text");
      setSelectedTextSlot(key);

      const currentLine = (textLineSpecRef.current?.[key] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as any;
      const offX = Number(currentLine.offset_x ?? 0);
      const offY = Number(currentLine.offset_y ?? 0);
      canvasDragRef.current = {
        kind: "text_slot",
        pointerId: event.pointerId,
        slotKey: key,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startOffX: offX,
        startOffY: offY,
        width,
        height,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      event.preventDefault();
    },
    [stageSize.height, stageSize.width]
  );

  const beginCanvasTextSlotScale = useCallback(
    (event: PointerEvent<HTMLDivElement>, slotKey: string) => {
      if (event.button !== 0) return;
      const key = String(slotKey ?? "").trim();
      if (!key) return;
      event.stopPropagation();
      canvasDragStartTextLinesRef.current = cloneTextLines(textLineSpecRef.current);
      setCanvasSelectedAsset("text");
      setSelectedTextSlot(key);

      const line = (textLineSpecRef.current?.[key] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as any;
      const rect = (event.currentTarget as HTMLDivElement).getBoundingClientRect();
      const centerClientX = rect.left + rect.width / 2;
      const centerClientY = rect.top + rect.height / 2;
      const startDist = Math.hypot(event.clientX - centerClientX, event.clientY - centerClientY);
      const startScale = Number(line.scale ?? 1);

      canvasDragRef.current = {
        kind: "text_slot_scale",
        pointerId: event.pointerId,
        slotKey: key,
        centerClientX,
        centerClientY,
        startScale,
        startDist,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      event.preventDefault();
    },
    []
  );

  const beginCanvasTextSlotRotate = useCallback(
    (event: PointerEvent<HTMLDivElement>, slotKey: string) => {
      if (event.button !== 0) return;
      const key = String(slotKey ?? "").trim();
      if (!key) return;
      event.stopPropagation();
      canvasDragStartTextLinesRef.current = cloneTextLines(textLineSpecRef.current);
      setCanvasSelectedAsset("text");
      setSelectedTextSlot(key);

      const line = (textLineSpecRef.current?.[key] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as any;
      const rect = (event.currentTarget as HTMLDivElement).getBoundingClientRect();
      const centerClientX = rect.left + rect.width / 2;
      const centerClientY = rect.top + rect.height / 2;
      const startAngleRad = Math.atan2(event.clientY - centerClientY, event.clientX - centerClientX);
      const startRotationDeg = Number(line.rotate_deg ?? 0);

      canvasDragRef.current = {
        kind: "text_slot_rotate",
        pointerId: event.pointerId,
        slotKey: key,
        centerClientX,
        centerClientY,
        startRotationDeg,
        startAngleRad,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      event.preventDefault();
    },
    []
  );

  const clampCanvasView = useCallback((next: CanvasView, width: number, height: number): CanvasView => {
    const scale = clampNumber(Number(next.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
    if (!width || !height) {
      return { scale: 1, panX: 0, panY: 0 };
    }
    if (scale <= 1.0001) {
      return { scale: 1, panX: 0, panY: 0 };
    }
    const minPanX = -(scale - 1) * width;
    const maxPanX = 0;
    const minPanY = -(scale - 1) * height;
    const maxPanY = 0;
    return {
      scale,
      panX: clampNumber(Number(next.panX ?? 0), minPanX, maxPanX),
      panY: clampNumber(Number(next.panY ?? 0), minPanY, maxPanY),
    };
  }, []);

  useEffect(() => {
    if (previewMode !== "canvas") return;
    if (!stageSize.width || !stageSize.height) return;
    setCanvasView((prev) => {
      const clamped = clampCanvasView(prev, stageSize.width, stageSize.height);
      const same =
        Math.abs(Number(prev.scale ?? 1) - clamped.scale) < 1e-6 &&
        Math.abs(Number(prev.panX ?? 0) - clamped.panX) < 1e-3 &&
        Math.abs(Number(prev.panY ?? 0) - clamped.panY) < 1e-3;
      return same ? prev : clamped;
    });
  }, [clampCanvasView, previewMode, stageSize.height, stageSize.width]);

  const resetCanvasView = useCallback(() => {
    setCanvasView({ scale: 1, panX: 0, panY: 0 });
  }, []);

  const focusCanvasStage = useCallback(() => {
    if (previewMode !== "canvas") return;
    stageRef.current?.focus({ preventScroll: true });
  }, [previewMode]);

  const toggleFocusMode = useCallback(() => {
    if (showPicker || showChat) {
      focusPanelsRef.current = { showPicker, showChat };
      setShowPicker(false);
      setShowChat(false);
      focusCanvasStage();
      return;
    }
    const prev = focusPanelsRef.current;
    setShowPicker(prev?.showPicker ?? true);
    setShowChat(prev?.showChat ?? true);
    focusPanelsRef.current = null;
    focusCanvasStage();
  }, [focusCanvasStage, showChat, showPicker]);

  const commitOverridesInteraction = useCallback(() => {
    const snapshot = overridesInteractionStartRef.current;
    if (!snapshot) return;
    dispatchOverrides({ type: "push_undo_snapshot", snapshot });
    overridesInteractionStartRef.current = null;
  }, [dispatchOverrides]);

  const beginOverridesInteraction = useCallback(() => {
    if (overridesInteractionStartRef.current) return;
    overridesInteractionStartRef.current = { ...(overridesLeaf ?? {}) };
    if (typeof window === "undefined") return;
    const handleEnd = () => {
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
      commitOverridesInteraction();
    };
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
  }, [commitOverridesInteraction, overridesLeaf]);

  const commitTextInteraction = useCallback(() => {
    const snapshot = textInteractionStartRef.current;
    if (!snapshot) return;
    const sig = (lines: Record<string, ThumbnailTextLineSpecLine>) => {
      const entries = Object.entries(lines ?? {})
        .map(([slotKey, line]) => [
          slotKey,
          Number((line as any)?.offset_x ?? 0),
          Number((line as any)?.offset_y ?? 0),
          Number((line as any)?.scale ?? 1),
          Number((line as any)?.rotate_deg ?? 0),
        ])
        .sort(([a], [b]) => String(a).localeCompare(String(b)));
      return JSON.stringify(entries);
    };
    if (sig(textLineSpecRef.current) !== sig(snapshot)) {
      pushTextUndoSnapshot(snapshot);
    }
    textInteractionStartRef.current = null;
  }, [pushTextUndoSnapshot]);

  const beginTextInteraction = useCallback(() => {
    if (textInteractionStartRef.current) return;
    textInteractionStartRef.current = cloneTextLines(textLineSpecRef.current);
    if (typeof window === "undefined") return;
    const handleEnd = () => {
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
      commitTextInteraction();
    };
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
  }, [commitTextInteraction]);

  const setCanvasViewScaleAtCenter = useCallback(
    (nextScaleRaw: number) => {
      if (previewMode !== "canvas") return;

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      const nextScale = clampNumber(Number(nextScaleRaw ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      const view = canvasViewRef.current;
      const prevScale = clampNumber(Number(view?.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      const anchorX = width * 0.5;
      const anchorY = height * 0.5;
      const prevPanX = Number(view?.panX ?? 0);
      const prevPanY = Number(view?.panY ?? 0);
      const worldX = (anchorX - prevPanX) / prevScale;
      const worldY = (anchorY - prevPanY) / prevScale;
      const nextPanX = anchorX - worldX * nextScale;
      const nextPanY = anchorY - worldY * nextScale;
      setCanvasView(clampCanvasView({ scale: nextScale, panX: nextPanX, panY: nextPanY }, width, height));
    },
    [clampCanvasView, previewMode, stageSize.height, stageSize.width]
  );

  const bumpCanvasViewScale = useCallback(
    (direction: -1 | 1) => {
      const view = canvasViewRef.current;
      const prevScale = clampNumber(Number(view?.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      const nextScale =
        direction < 0
          ? clampNumber(prevScale / 1.15, CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM)
          : clampNumber(prevScale * 1.15, CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      setCanvasViewScaleAtCenter(nextScale);
    },
    [setCanvasViewScaleAtCenter]
  );

  const handleCanvasSelectAsset = useCallback(
    (asset: CanvasSelectedAsset) => {
      setCanvasSelectedAsset(asset);
      if (asset === "text") {
        const fallback = textSlotKeys[0] ?? null;
        setSelectedTextSlot((current) => {
          if (current && textSlotKeys.includes(current)) return current;
          return fallback;
        });
      }
      focusCanvasStage();
    },
    [focusCanvasStage, textSlotKeys]
  );

  const handleCanvasResetSelected = useCallback(() => {
    if (canvasSelectedAsset === "bg") {
      if (!context) return;
      dispatchOverrides({
        type: "merge_patch",
        patch: {
          "overrides.bg_pan_zoom.zoom": Number(CANVAS_BG_DEFAULT_ZOOM.toFixed(3)),
          "overrides.bg_pan_zoom.pan_x": 0,
          "overrides.bg_pan_zoom.pan_y": 0,
        },
      });
      focusCanvasStage();
      return;
    }
    if (canvasSelectedAsset === "portrait") {
      if (!context) return;
      dispatchOverrides({
        type: "merge_patch",
        patch: {
          "overrides.portrait.offset_x": 0,
          "overrides.portrait.offset_y": 0,
          "overrides.portrait.zoom": 1.0,
        },
      });
      focusCanvasStage();
      return;
    }
    if (canvasSelectedAsset === "text") {
      const slotKey = String(selectedTextSlot ?? "").trim();
      if (!slotKey) return;
      pushTextUndoSnapshot(textLineSpecRef.current);
      setTextLineSpecLines((current) => {
        const next = { ...(current ?? {}) };
        const existing = next[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
        next[slotKey] = { ...existing, offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
        return next;
      });
      focusCanvasStage();
    }
  }, [canvasSelectedAsset, context, dispatchOverrides, focusCanvasStage, pushTextUndoSnapshot, selectedTextSlot]);

  const beginCanvasViewportPan = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0 && event.button !== 1) return;

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      const view = canvasViewRef.current;
      if (!view || Number(view.scale ?? 1) <= 1.0001) {
        return;
      }

      canvasDragRef.current = {
        kind: "viewport_pan",
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startPanX: Number(view.panX ?? 0),
        startPanY: Number(view.panY ?? 0),
        width,
        height,
        scale: Number(view.scale ?? 1),
      };
      setCanvasPanningView(true);

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
    },
    [stageSize.height, stageSize.width]
  );

  const handleCanvasStageWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (previewMode !== "canvas") return;
      if (!event.deltaY) return;
      if (event.altKey) return; // Alt+wheel is reserved for zooming layers (bg/portrait).
      const target = event.target as HTMLElement | null;
      if (target?.closest?.(".thumbnail-chat-editor-page__canvas-dock")) {
        return;
      }

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      event.preventDefault();

      const view = canvasViewRef.current;
      const prevScale = clampNumber(Number(view?.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextScale = clampNumber(prevScale * factor, CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
      if (Math.abs(nextScale - prevScale) < 1e-6) return;

      const left = rect?.left ?? 0;
      const top = rect?.top ?? 0;
      const pointerX = event.clientX - left;
      const pointerY = event.clientY - top;

      const prevPanX = Number(view?.panX ?? 0);
      const prevPanY = Number(view?.panY ?? 0);
      const worldX = (pointerX - prevPanX) / prevScale;
      const worldY = (pointerY - prevPanY) / prevScale;
      const nextPanX = pointerX - worldX * nextScale;
      const nextPanY = pointerY - worldY * nextScale;

      setCanvasView(clampCanvasView({ scale: nextScale, panX: nextPanX, panY: nextPanY }, width, height));
    },
    [clampCanvasView, previewMode, stageSize.height, stageSize.width]
  );

  const handleCanvasPointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const drag = canvasDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) {
        return;
      }

      if (drag.kind === "viewport_pan") {
        setCanvasSnapGuides(null);
        const dx = event.clientX - drag.startClientX;
        const dy = event.clientY - drag.startClientY;
        const nextPanX = drag.startPanX + dx;
        const nextPanY = drag.startPanY + dy;
        setCanvasView(clampCanvasView({ scale: drag.scale, panX: nextPanX, panY: nextPanY }, drag.width, drag.height));
        return;
      }

      if (drag.kind === "text_slot_scale") {
        setCanvasSnapGuides(null);
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) return;
        const dist = Math.hypot(event.clientX - drag.centerClientX, event.clientY - drag.centerClientY);
        const ratio = dist / Math.max(0.5, Number(drag.startDist));
        const nextScale = clampNumber(Number(drag.startScale) * ratio, 0.25, 4);
        setTextLineSpecLines((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
          next[slotKey] = { ...existing, scale: Number(nextScale.toFixed(3)) };
          return next;
        });
        return;
      }

      if (drag.kind === "text_slot_rotate") {
        setCanvasSnapGuides(null);
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) return;
        const angle = Math.atan2(event.clientY - drag.centerClientY, event.clientX - drag.centerClientX);
        const deltaDeg = ((angle - drag.startAngleRad) * 180) / Math.PI;
        let nextRotation = normalizeRotationDeg(Number(drag.startRotationDeg) + deltaDeg);
        if (event.shiftKey) {
          nextRotation = normalizeRotationDeg(Math.round(nextRotation / 15) * 15);
        }
        setTextLineSpecLines((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
          next[slotKey] = { ...existing, rotate_deg: Number(nextRotation.toFixed(3)) };
          return next;
        });
        return;
      }

      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      const viewScale = Math.max(0.0001, Number(canvasViewRef.current?.scale ?? 1));
      const dxWorld = dx / viewScale;
      const dyWorld = dy / viewScale;

      if (drag.kind === "bg") {
        setCanvasSnapGuides(null);
        const zoom = Number(drag.zoom);
        const maxDx = zoom > 1.0001 ? (drag.width * (zoom - 1)) / 2 : drag.width / 2;
        const maxDy = zoom > 1.0001 ? (drag.height * (zoom - 1)) / 2 : drag.height / 2;
        if (!Number.isFinite(maxDx) || !Number.isFinite(maxDy) || maxDx <= 0 || maxDy <= 0) {
          return;
        }
        const nextPanX = clampNumber(drag.startPanX - dxWorld / maxDx, CANVAS_BG_PAN_MIN, CANVAS_BG_PAN_MAX);
        const nextPanY = clampNumber(drag.startPanY - dyWorld / maxDy, CANVAS_BG_PAN_MIN, CANVAS_BG_PAN_MAX);
        dispatchOverrides({
          type: "merge_patch",
          patch: {
            "overrides.bg_pan_zoom.pan_x": nextPanX,
            "overrides.bg_pan_zoom.pan_y": nextPanY,
          },
          recordHistory: false,
        });
        return;
      }

      if (drag.kind === "portrait") {
        setCanvasSnapGuides(null);
        const nextOffX = clampNumber(drag.startOffX + dxWorld / drag.width, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        const nextOffY = clampNumber(drag.startOffY + dyWorld / drag.height, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        dispatchOverrides({
          type: "merge_patch",
          patch: {
            "overrides.portrait.offset_x": Number(nextOffX.toFixed(4)),
            "overrides.portrait.offset_y": Number(nextOffY.toFixed(4)),
          },
          recordHistory: false,
        });
        return;
      }

      if (drag.kind === "text_slot") {
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) return;
        let nextOffX = clampNumber(drag.startOffX + dxWorld / drag.width, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        let nextOffY = clampNumber(drag.startOffY + dyWorld / drag.height, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        const box = textSlotBoxes?.[slotKey];
        if (!event.altKey && Array.isArray(box) && box.length === 4 && drag.width > 0 && drag.height > 0) {
          let snapX: number | null = null;
          let snapY: number | null = null;
          const thresholdX = 8 / (drag.width * viewScale);
          const thresholdY = 8 / (drag.height * viewScale);
          const boxLeft0 = Number(box[0]);
          const boxTop0 = Number(box[1]);
          const boxW = Number(box[2]);
          const boxH = Number(box[3]);
          const left = boxLeft0 + nextOffX;
          const top = boxTop0 + nextOffY;
          const right = left + boxW;
          const bottom = top + boxH;
          const cx = left + boxW / 2;
          const cy = top + boxH / 2;
          if (Math.abs(cx - 0.5) < thresholdX) {
            nextOffX = 0.5 - (boxLeft0 + boxW / 2);
            snapX = 0.5;
          } else if (Math.abs(left - 0.0) < thresholdX) {
            nextOffX = -boxLeft0;
            snapX = 0.0;
          } else if (Math.abs(right - 1.0) < thresholdX) {
            nextOffX = 1.0 - (boxLeft0 + boxW);
            snapX = 1.0;
          }
          if (Math.abs(cy - 0.5) < thresholdY) {
            nextOffY = 0.5 - (boxTop0 + boxH / 2);
            snapY = 0.5;
          } else if (Math.abs(top - 0.0) < thresholdY) {
            nextOffY = -boxTop0;
            snapY = 0.0;
          } else if (Math.abs(bottom - 1.0) < thresholdY) {
            nextOffY = 1.0 - (boxTop0 + boxH);
            snapY = 1.0;
          }
          nextOffX = clampNumber(nextOffX, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
          nextOffY = clampNumber(nextOffY, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
          if (snapX !== null || snapY !== null) {
            setCanvasSnapGuides({ xNorm: snapX, yNorm: snapY });
          } else {
            setCanvasSnapGuides(null);
          }
        } else {
          setCanvasSnapGuides(null);
        }
        setTextLineSpecLines((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
          next[slotKey] = {
            ...existing,
            offset_x: Number(nextOffX.toFixed(4)),
            offset_y: Number(nextOffY.toFixed(4)),
          };
          return next;
        });
        return;
      }

      // No other kinds.
    },
    [clampCanvasView, dispatchOverrides, textSlotBoxes]
  );

  const handleCanvasPointerEnd = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const drag = canvasDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) {
        return;
      }

      if (drag.kind === "bg" || drag.kind === "portrait") {
        const snapshot = canvasDragStartOverridesRef.current;
        if (snapshot) {
          dispatchOverrides({ type: "push_undo_snapshot", snapshot });
        }
        canvasDragStartOverridesRef.current = null;
      }

      if (drag.kind === "text_slot" || drag.kind === "text_slot_scale" || drag.kind === "text_slot_rotate") {
        const snapshot = canvasDragStartTextLinesRef.current;
        if (snapshot) {
          const sig = (lines: Record<string, ThumbnailTextLineSpecLine>) => {
            const entries = Object.entries(lines ?? {})
              .map(([slotKey, line]) => [
                slotKey,
                Number((line as any)?.offset_x ?? 0),
                Number((line as any)?.offset_y ?? 0),
                Number((line as any)?.scale ?? 1),
                Number((line as any)?.rotate_deg ?? 0),
              ])
              .sort(([a], [b]) => String(a).localeCompare(String(b)));
            return JSON.stringify(entries);
          };
          if (sig(textLineSpecRef.current) !== sig(snapshot)) {
            pushTextUndoSnapshot(snapshot);
          }
        }
        canvasDragStartTextLinesRef.current = null;
      }

      if (drag.kind === "viewport_pan") {
        setCanvasPanningView(false);
      }

      setCanvasSnapGuides(null);
      canvasDragRef.current = null;
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
    },
    [dispatchOverrides, pushTextUndoSnapshot]
  );

  const handleCanvasBgWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (!context) return;
      if (!event.deltaY) return;
      if (!event.altKey) return;
      event.stopPropagation();
      event.preventDefault();
      setCanvasSelectedAsset("bg");
      const currentZoom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.zoom", 1.0));
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextZoom = clampNumber(currentZoom * factor, 1.0, CANVAS_BG_MAX_ZOOM);
      dispatchOverrides({ type: "merge_patch", patch: { "overrides.bg_pan_zoom.zoom": Number(nextZoom.toFixed(3)) } });
    },
    [context, dispatchOverrides, overridesLeaf]
  );

  const handleCanvasPortraitWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (!context) return;
      if (!event.deltaY) return;
      if (!event.altKey) return;
      event.stopPropagation();
      event.preventDefault();
      setCanvasSelectedAsset("portrait");
      const currentZoom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.zoom", 1.0));
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextZoom = clampNumber(currentZoom * factor, 0.5, 2.0);
      dispatchOverrides({ type: "merge_patch", patch: { "overrides.portrait.zoom": Number(nextZoom.toFixed(3)) } });
    },
    [context, dispatchOverrides, overridesLeaf]
  );

  const handleCanvasKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      const key = event.key;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase() ?? "";
      if (tag === "input" || tag === "textarea" || (target as any)?.isContentEditable) {
        return;
      }

      const meta = event.metaKey || event.ctrlKey;
      if (meta && key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) {
          handleRedoAny();
        } else {
          handleUndoAny();
        }
        return;
      }
      if (meta && key.toLowerCase() === "y") {
        event.preventDefault();
        handleRedoAny();
        return;
      }

      if (key === "Escape") {
        event.preventDefault();
        setCanvasSelectedAsset(null);
        setSelectedTextSlot(null);
        setHoveredTextSlot(null);
        setCanvasHandMode(false);
        setCanvasSpaceHeld(false);
        setCanvasPanningView(false);
        canvasDragRef.current = null;
        return;
      }

      if ((target as any)?.closest?.(".thumbnail-chat-editor-page__canvas-dock")) {
        return;
      }

      if (key === " ") {
        event.preventDefault();
        setCanvasSpaceHeld(true);
        return;
      }

      const lower = key.toLowerCase();
      if (lower === "g") {
        event.preventDefault();
        setCanvasGuidesEnabled((prev) => !prev);
        return;
      }
      if (lower === "h") {
        event.preventDefault();
        setCanvasHandMode((prev) => !prev);
        return;
      }
      if (lower === "v") {
        event.preventDefault();
        setCanvasHandMode(false);
        setCanvasSpaceHeld(false);
        return;
      }

      const rect = stageRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? stageSize.width;
      const height = rect?.height ?? stageSize.height;
      if (!width || !height) return;

      if (key === "0") {
        event.preventDefault();
        resetCanvasView();
        return;
      }
      if (key === "+" || key === "=" || key === "-") {
        event.preventDefault();
        const view = canvasViewRef.current;
        const prevScale = clampNumber(Number(view?.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
        const nextScale =
          key === "-" ? clampNumber(prevScale / 1.15, CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM) : clampNumber(prevScale * 1.15, CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
        const anchorX = width * 0.5;
        const anchorY = height * 0.5;
        const prevPanX = Number(view?.panX ?? 0);
        const prevPanY = Number(view?.panY ?? 0);
        const worldX = (anchorX - prevPanX) / prevScale;
        const worldY = (anchorY - prevPanY) / prevScale;
        const nextPanX = anchorX - worldX * nextScale;
        const nextPanY = anchorY - worldY * nextScale;
        setCanvasView(clampCanvasView({ scale: nextScale, panX: nextPanX, panY: nextPanY }, width, height));
        return;
      }

      if (key === "Tab") {
        if (canvasSelectedAsset !== "text") return;
        const slotKeys = Object.keys(textSlotBoxes ?? {}).filter(Boolean).sort((a, b) => a.localeCompare(b));
        if (!slotKeys.length) return;
        event.preventDefault();
        const currentKey = String(selectedTextSlot ?? "").trim();
        const idx = currentKey ? slotKeys.indexOf(currentKey) : -1;
        const nextIdx = (() => {
          if (event.shiftKey) {
            return idx <= 0 ? slotKeys.length - 1 : idx - 1;
          }
          return idx >= slotKeys.length - 1 ? 0 : idx + 1;
        })();
        const nextSlot = slotKeys[nextIdx] ?? slotKeys[0];
        setSelectedTextSlot(nextSlot);
        return;
      }

      const isArrow = key === "ArrowLeft" || key === "ArrowRight" || key === "ArrowUp" || key === "ArrowDown";
      if (!isArrow) return;

      const stepPx = event.altKey ? 1 : event.shiftKey ? 12 : 4;
      const dx = key === "ArrowLeft" ? -stepPx : key === "ArrowRight" ? stepPx : 0;
      const dy = key === "ArrowUp" ? -stepPx : key === "ArrowDown" ? stepPx : 0;
      const dxNorm = dx / width;
      const dyNorm = dy / height;

      if (canvasSelectedAsset === "portrait") {
        if (!context) return;
        event.preventDefault();
        const curX = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_x", 0.0));
        const curY = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_y", 0.0));
        const nextX = clampNumber(curX + dxNorm, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        const nextY = clampNumber(curY + dyNorm, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
        dispatchOverrides({
          type: "merge_patch",
          patch: {
            "overrides.portrait.offset_x": Number(nextX.toFixed(4)),
            "overrides.portrait.offset_y": Number(nextY.toFixed(4)),
          },
        });
        return;
      }

      if (canvasSelectedAsset === "text") {
        const slotKey = String(selectedTextSlot ?? "").trim();
        if (!slotKey) return;
        event.preventDefault();
        pushTextUndoSnapshot(textLineSpecRef.current);
        setTextLineSpecLines((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
          const nextOffX = clampNumber(Number(existing.offset_x ?? 0) + dxNorm, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
          const nextOffY = clampNumber(Number(existing.offset_y ?? 0) + dyNorm, CANVAS_OFFSET_MIN, CANVAS_OFFSET_MAX);
          next[slotKey] = { ...existing, offset_x: Number(nextOffX.toFixed(4)), offset_y: Number(nextOffY.toFixed(4)) };
          return next;
        });
      }
    },
    [
      canvasSelectedAsset,
      clampCanvasView,
      context,
      dispatchOverrides,
      handleRedoAny,
      handleUndoAny,
      overridesLeaf,
      pushTextUndoSnapshot,
      resetCanvasView,
      selectedTextSlot,
      stageSize.height,
      stageSize.width,
      textSlotBoxes,
    ]
  );

  const handleCanvasKeyUp = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const key = event.key;
    if (key === " ") {
      setCanvasSpaceHeld(false);
    }
  }, []);

  const handleCanvasStagePointerDownCapture = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (previewMode !== "canvas") return;

      const target = event.target as HTMLElement | null;
      if (target?.closest?.(".thumbnail-chat-editor-page__canvas-dock")) {
        return;
      }

      stageRef.current?.focus({ preventScroll: true });

      const hand =
        event.button === 1 || (event.button === 0 && canvasHandActiveRef.current);
      if (!hand) return;

      beginCanvasViewportPan(event);
      event.stopPropagation();
      event.preventDefault();
    },
    [beginCanvasViewportPan, previewMode]
  );

  return (
	    <section className="thumbnail-page workspace--thumbnail-clean thumbnail-chat-editor-page">
      <header className="thumbnail-page__header">
        <div className="thumbnail-chat-editor-page__header-top">
          <div>
            <h1 className="thumbnail-page__title">サムネ自然言語編集</h1>
            <p className="thumbnail-page__subtitle">
              会話でサムネを微調整できます。<strong>指示 → 提案 → 適用 → 保存/再合成</strong>
            </p>
          </div>
          <div className="thumbnail-page__header-actions">
            <Link className="action-chip" to="/thumbnails">
              サムネ一覧
            </Link>
            <Link className="action-chip" to="/dashboard">
              ダッシュボード
            </Link>
          </div>
        </div>

        <div className="thumbnail-chat-editor-page__toolbar" aria-label="ターゲット選択ツールバー">
          <div className="thumbnail-chat-editor-page__toolbar-left">
            <label className="thumbnail-chat-editor-page__toolbar-field">
              <span className="thumbnail-chat-editor-page__toolbar-label">CH</span>
              <select
                value={selectedChannel}
                onChange={(e) => applyParams({ channel: e.target.value, video: "" })}
                disabled={overviewLoading}
              >
                <option value="">{overviewLoading ? "読込中…" : "選択"}</option>
                {channels.map((c) => (
                  <option key={c.channel} value={c.channel}>
                    {c.channel}
                  </option>
                ))}
              </select>
            </label>

            <label className="thumbnail-chat-editor-page__toolbar-field">
              <span className="thumbnail-chat-editor-page__toolbar-label">Stable</span>
              <select value={stableId} onChange={(e) => applyParams({ stable: e.target.value })} disabled={!selectedVideo}>
                <option value="">00_thumb</option>
                <option value="00_thumb_1">00_thumb_1</option>
                <option value="00_thumb_2">00_thumb_2</option>
              </select>
            </label>

            <label className="thumbnail-chat-editor-page__toggle thumbnail-chat-editor-page__toggle--toolbar">
              <input type="checkbox" checked={allowGenerate} onChange={(e) => setAllowGenerate(e.target.checked)} />
              <span>allow_generate</span>
            </label>

            <label className="thumbnail-chat-editor-page__toggle thumbnail-chat-editor-page__toggle--toolbar">
              <input
                type="checkbox"
                checked={includeThumbCaption}
                onChange={(e) => setIncludeThumbCaption(e.target.checked)}
              />
              <span>caption</span>
            </label>
          </div>

          <div className="thumbnail-chat-editor-page__toolbar-right">
            {selectedChannel ? (
              <span className="status-chip">
                <code>{selectedVideoIndex >= 0 ? selectedVideoIndex + 1 : 0}</code> / <code>{filteredVideos.length}</code>
              </span>
            ) : null}
            <button
              className="action-button"
              type="button"
              onClick={() => prevVideo && applyParams({ video: String(prevVideo.video ?? "").padStart(3, "0") })}
              disabled={!prevVideo}
            >
              ←
            </button>
            <button
              className="action-button"
              type="button"
              onClick={() => nextVideo && applyParams({ video: String(nextVideo.video ?? "").padStart(3, "0") })}
              disabled={!nextVideo}
            >
              →
            </button>
            <button className="action-button" type="button" onClick={() => void loadOverview()} disabled={overviewLoading}>
              更新
            </button>
            <button
              className="action-button"
              type="button"
              onClick={() => void loadContext()}
              disabled={!selectedChannel || !selectedVideo || contextLoading}
            >
              再読込
            </button>
            <button className="action-button" type="button" onClick={applySavedPreset} disabled={!selectedChannel || !selectedVideo}>
              プリセット
            </button>
            <label className="thumbnail-chat-editor-page__toggle thumbnail-chat-editor-page__toggle--toolbar">
              <input type="checkbox" checked={showPicker} onChange={(e) => setShowPicker(e.target.checked)} />
              <span>一覧</span>
            </label>
            <label className="thumbnail-chat-editor-page__toggle thumbnail-chat-editor-page__toggle--toolbar">
              <input type="checkbox" checked={showChat} onChange={(e) => setShowChat(e.target.checked)} />
              <span>チャット</span>
            </label>
          </div>
        </div>
      </header>

      {overviewError ? (
        <div className="thumbnail-chat-editor-page__alert thumbnail-chat-editor-page__alert--danger" role="alert">
          overview取得に失敗しました: {overviewError}
        </div>
      ) : null}

      {contextError ? (
        <div className="thumbnail-chat-editor-page__alert thumbnail-chat-editor-page__alert--danger" role="alert">
          editor-context取得に失敗しました: {contextError}
        </div>
      ) : null}

      <div
        className={`thumbnail-chat-editor-page__layout${showPicker ? "" : " thumbnail-chat-editor-page__layout--no-picker"}${
          showChat ? "" : " thumbnail-chat-editor-page__layout--no-chat"
        }`}
      >
        {showPicker ? (
        <section className="thumbnail-chat-editor-page__card thumbnail-chat-editor-page__picker">
          <div className="thumbnail-chat-editor-page__card-header">
            <div className="thumbnail-chat-editor-page__card-title">
              <strong>動画</strong>
              <span className="thumbnail-chat-editor-page__card-subtitle">
                {selectedChannel ? `${selectedChannel} の一覧` : "全チャンネル（検索して選択）"}
              </span>
            </div>
            <div className="thumbnail-chat-editor-page__preview-actions">
              <button
                className="action-button"
                type="button"
                onClick={() => setRecents([])}
                disabled={!recents.length}
              >
                最近をクリア
              </button>
            </div>
          </div>

          <div className="thumbnail-chat-editor-page__picker-body">
            <label className="thumbnail-chat-editor-page__picker-search">
              <span className="thumbnail-chat-editor-page__toolbar-label">検索</span>
              <input
                type="text"
                value={videoQuery}
                onChange={(e) => setVideoQuery(e.target.value)}
                placeholder="CH06-034 / 034 / タイトル…"
                disabled={overviewLoading}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  const first = filteredVideos?.[0] ?? null;
                  if (!first) return;
                  applyParams({ channel: String(first.channel ?? ""), video: String(first.video ?? "") });
                }}
              />
            </label>

            {recents.length ? (
              <div className="thumbnail-chat-editor-page__recents" aria-label="最近開いたサムネ">
                {recents.map((item) => {
                  const file = thumbFileName(item.stable ?? "");
                  const base = resolveApiUrl(
                    `/thumbnails/assets/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.video)}/${encodeURIComponent(file)}`
                  );
                  const isActive =
                    item.channel === selectedChannel &&
                    item.video === selectedVideo &&
                    String(item.stable ?? "") === String(stableId ?? "");
                  const src = isActive ? `${base}${base.includes("?") ? "&" : "?"}t=${encodeURIComponent(previewToken)}` : base;
                  return (
                    <button
                      key={`${item.channel}-${item.video}-${item.stable ?? ""}`}
                      type="button"
                      className={`thumbnail-chat-editor-page__recent ${isActive ? "is-active" : ""}`}
                      onClick={() => applyParams({ channel: item.channel, video: item.video, stable: item.stable ?? "" })}
                      title={`${item.channel}-${item.video}${item.stable ? ` (${item.stable})` : ""}`}
                    >
                      <img src={src} alt={`${item.channel}-${item.video}`} />
                      <span>{`${item.channel}-${item.video}`}</span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="thumbnail-chat-editor-page__hint">最近開いたサムネがここに出ます。</div>
            )}

            <div className="thumbnail-chat-editor-page__picker-list" role="list">
              {filteredVideos.length ? (
                filteredVideos
                  .slice()
                  .sort((a, b) => {
                    const ac = String(a.channel ?? "");
                    const bc = String(b.channel ?? "");
                    if (selectedChannel) {
                      const av = Number.parseInt(String(a.video ?? "0"), 10) || 0;
                      const bv = Number.parseInt(String(b.video ?? "0"), 10) || 0;
                      return bv - av;
                    }
                    if (ac !== bc) return ac.localeCompare(bc);
                    const av = Number.parseInt(String(a.video ?? "0"), 10) || 0;
                    const bv = Number.parseInt(String(b.video ?? "0"), 10) || 0;
                    return bv - av;
                  })
                  .map((p) => {
                    const ch = normalizeChannel(String(p.channel ?? ""));
                    const vid = normalizeVideo(String(p.video ?? ""));
                    const isActive = ch === selectedChannel && vid === selectedVideo;
                    const file = "00_thumb.png";
                    const base = resolveApiUrl(
                      `/thumbnails/assets/${encodeURIComponent(ch)}/${encodeURIComponent(vid)}/${encodeURIComponent(file)}`
                    );
                    const src = isActive ? `${base}${base.includes("?") ? "&" : "?"}t=${encodeURIComponent(previewToken)}` : base;
                    const title = ((p.title ?? p.sheet_title ?? "") as any).trim?.() ? ((p.title ?? p.sheet_title ?? "") as any).trim() : String(p.title ?? p.sheet_title ?? "").trim();
                    return (
                      <button
                        key={`${ch}-${vid}`}
                        type="button"
                        className={`thumbnail-chat-editor-page__picker-item ${isActive ? "is-active" : ""}`}
                        onClick={() => applyParams({ channel: ch, video: vid })}
                        role="listitem"
                      >
                        <div className="thumbnail-chat-editor-page__picker-thumb">
                          <img src={src} alt={`${ch}-${vid}`} loading="lazy" />
                        </div>
                        <div className="thumbnail-chat-editor-page__picker-meta">
                          <div className="thumbnail-chat-editor-page__picker-id">
                            <code>{`${ch}-${vid}`}</code>
                          </div>
                          <div className="thumbnail-chat-editor-page__picker-title">{title || "（無題）"}</div>
                        </div>
                      </button>
                    );
                  })
              ) : (
                <div className="thumbnail-chat-editor-page__hint">
                  {overviewLoading ? "読み込み中…" : "候補がありません。検索条件を変えてください。"}
                </div>
              )}
            </div>
          </div>
        </section>
        ) : null}

        <section className="thumbnail-chat-editor-page__card thumbnail-chat-editor-page__preview">
          <div className="thumbnail-chat-editor-page__card-header">
            <div className="thumbnail-chat-editor-page__card-title">
              <strong>プレビュー</strong>
              <span className="thumbnail-chat-editor-page__card-subtitle">
                {selectedChannel && selectedVideo
                  ? `${selectedChannel}-${selectedVideo}${stableId ? ` (${stableId})` : ""}`
                  : "まずCH/Videoを選択"}
              </span>
            </div>
            <div className="thumbnail-chat-editor-page__preview-actions">
              <div className="thumbnail-chat-editor-page__segmented" aria-label="Preview mode">
                <button
                  type="button"
                  className={`thumbnail-chat-editor-page__segment ${previewMode === "canvas" ? "is-active" : ""}`}
                  onClick={() => setPreviewMode("canvas")}
                  aria-pressed={previewMode === "canvas"}
                >
                  Canvas
                </button>
                <button
                  type="button"
                  className={`thumbnail-chat-editor-page__segment ${previewMode === "rendered" ? "is-active" : ""}`}
                  onClick={() => setPreviewMode("rendered")}
                  aria-pressed={previewMode === "rendered"}
                >
                  Rendered
                </button>
              </div>
              <button
                className="action-button"
                type="button"
                onClick={() => lastPatch && applyPatchToLocal(lastPatch)}
                disabled={!selectedChannel || !selectedVideo || !lastPatch?.ops?.length || saving}
              >
                最後の提案を適用
              </button>
              {selectedChannel && selectedVideo ? (
                <Link
                  className="action-button"
                  to={`/thumbnails?channel=${encodeURIComponent(selectedChannel)}&video=${encodeURIComponent(selectedVideo)}${
                    stableId ? `&stable=${encodeURIComponent(stableId)}` : ""
                  }`}
                  target="_blank"
                  rel="noreferrer"
                >
                  フル編集
                </Link>
              ) : (
                <button className="action-button" type="button" disabled>
                  フル編集
                </button>
              )}
              <button
                className="action-button action-button--primary"
                type="button"
                onClick={() => void handleSaveAndBuild()}
                disabled={!selectedChannel || !selectedVideo || saving}
              >
                {saving ? "保存/再合成中…" : "保存して再合成"}
              </button>
            </div>
          </div>

          <div className="thumbnail-chat-editor-page__preview-body">
            {saveError ? (
              <div className="thumbnail-chat-editor-page__alert thumbnail-chat-editor-page__alert--danger" role="alert">
                保存/再合成に失敗しました: {saveError}
              </div>
            ) : null}

            <div className="thumbnail-chat-editor-page__preview-frame">
              <div
                ref={stageRef}
                className={`thumbnail-chat-editor-page__preview-stage${previewMode === "canvas" ? " is-canvas" : ""}${
                  canvasHandActive ? " is-hand-tool" : ""
                }${canvasPanningView ? " is-panning" : ""}`}
                tabIndex={previewMode === "canvas" ? 0 : undefined}
                role={previewMode === "canvas" ? "application" : undefined}
                aria-label={previewMode === "canvas" ? "サムネ編集キャンバス" : "サムネRenderedプレビュー"}
                onPointerDownCapture={handleCanvasStagePointerDownCapture}
                onPointerMove={previewMode === "canvas" ? handleCanvasPointerMove : undefined}
                onPointerUp={previewMode === "canvas" ? handleCanvasPointerEnd : undefined}
                onPointerCancel={previewMode === "canvas" ? handleCanvasPointerEnd : undefined}
                onKeyDown={previewMode === "canvas" ? handleCanvasKeyDown : undefined}
                onKeyUp={previewMode === "canvas" ? handleCanvasKeyUp : undefined}
                onWheel={previewMode === "canvas" ? handleCanvasStageWheel : undefined}
                onBlur={
                  previewMode === "canvas"
                    ? () => {
                        setCanvasSpaceHeld(false);
                        setCanvasPanningView(false);
                      }
                    : undefined
                }
              >
                {previewMode === "rendered" ? (
                  displayPreviewUrl ? (
                    <img
                      src={displayPreviewUrl}
                      alt="thumbnail preview"
                      onError={() => {
                        /* keep the frame */
                      }}
                    />
                  ) : (
                    <div className="thumbnail-chat-editor-page__preview-placeholder">まずCHとVideoを選んでください。</div>
                  )
                ) : selectedChannel && selectedVideo ? (
                  <div className="thumbnail-chat-editor-page__preview-canvas">
                    {(() => {
                      const width = stageSize.width;
                      const height = stageSize.height;
                      const viewScale = clampNumber(Number(canvasView.scale ?? 1), CANVAS_VIEW_MIN_ZOOM, CANVAS_VIEW_MAX_ZOOM);
                      const viewPanX = Number(canvasView.panX ?? 0);
                      const viewPanY = Number(canvasView.panY ?? 0);
                      const viewTransform = `translate3d(${viewPanX}px, ${viewPanY}px, 0) scale(${viewScale})`;
                      const uiScale = viewScale > 0 ? 1 / viewScale : 1;
                      const ui = (px: number) => Number((px * uiScale).toFixed(3));

                      const bgZoom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.zoom", 1.0));
                      const bgPanX = clampNumber(
                        Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.pan_x", 0.0)),
                        CANVAS_BG_PAN_MIN,
                        CANVAS_BG_PAN_MAX
                      );
                      const bgPanY = clampNumber(
                        Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_pan_zoom.pan_y", 0.0)),
                        CANVAS_BG_PAN_MIN,
                        CANVAS_BG_PAN_MAX
                      );
                      const bgBrightness = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_enhance.brightness", 1.0));
                      const bgContrast = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_enhance.contrast", 1.0));
                      const bgColor = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.bg_enhance.color", 1.0));
                      const bgFilter = `brightness(${bgBrightness}) contrast(${bgContrast}) saturate(${bgColor})`;

                      const shiftX = (() => {
                        if (!width) return 0;
                        if (bgZoom > 1.0001) {
                          return -((bgZoom - 1) * width * 0.5 * (1 + bgPanX));
                        }
                        return -(width * 0.5 * bgPanX);
                      })();
                      const shiftY = (() => {
                        if (!height) return 0;
                        if (bgZoom > 1.0001) {
                          return -((bgZoom - 1) * height * 0.5 * (1 + bgPanY));
                        }
                        return -(height * 0.5 * bgPanY);
                      })();

                      const overlaysEnabled = (() => {
                        const enabledKey = "overrides.overlays.left_tsz.enabled";
                        if (hasEditorLeafValue(context, overridesLeaf, enabledKey)) {
                          return Boolean(resolveEditorLeafValue(context, overridesLeaf, enabledKey, false));
                        }
                        return (
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.color") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.alpha_left") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.alpha_right") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.x0") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.x1")
                        );
                      })();
                      const overlaysLeftColor = String(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.color", "#000000"));
                      const overlaysLeftAlphaLeft = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.alpha_left", 0.65));
                      const overlaysLeftAlphaRight = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.alpha_right", 0.0));
                      const overlaysLeftX0 = clampNumber(
                        Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.x0", 0.0)),
                        0,
                        1
                      );
                      const overlaysLeftX1 = clampNumber(
                        Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.left_tsz.x1", 0.52)),
                        0,
                        1
                      );

                      const topBandEnabled = (() => {
                        const enabledKey = "overrides.overlays.top_band.enabled";
                        if (hasEditorLeafValue(context, overridesLeaf, enabledKey)) {
                          return Boolean(resolveEditorLeafValue(context, overridesLeaf, enabledKey, false));
                        }
                        return (
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.color") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.alpha_top") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.alpha_bottom") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.y0") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.y1")
                        );
                      })();
                      const topBandColor = String(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.color", "#000000"));
                      const topBandAlphaTop = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.alpha_top", 0.7));
                      const topBandAlphaBottom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.alpha_bottom", 0.0));
                      const topBandY0 = clampNumber(Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.y0", 0.0)), 0, 1);
                      const topBandY1 = clampNumber(Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.top_band.y1", 0.25)), 0, 1);

                      const bottomBandEnabled = (() => {
                        const enabledKey = "overrides.overlays.bottom_band.enabled";
                        if (hasEditorLeafValue(context, overridesLeaf, enabledKey)) {
                          return Boolean(resolveEditorLeafValue(context, overridesLeaf, enabledKey, false));
                        }
                        return (
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.color") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.alpha_top") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.alpha_bottom") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.y0") ||
                          hasEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.y1")
                        );
                      })();
                      const bottomBandColor = String(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.color", "#000000"));
                      const bottomBandAlphaTop = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.alpha_top", 0.4));
                      const bottomBandAlphaBottom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.alpha_bottom", 0.0));
                      const bottomBandY0 = clampNumber(Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.y0", 0.74)), 0, 1);
                      const bottomBandY1 = clampNumber(Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.overlays.bottom_band.y1", 1.0)), 0, 1);

                      const leftTszGradient = `linear-gradient(90deg, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaLeft)} 0%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaLeft)} ${(overlaysLeftX0 * 100).toFixed(2)}%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaRight)} ${(overlaysLeftX1 * 100).toFixed(2)}%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaRight)} 100%)`;
                      const topBandGradient = `linear-gradient(180deg, ${hexToRgba(topBandColor, topBandAlphaTop)} 0%, ${hexToRgba(topBandColor, topBandAlphaTop)} ${(topBandY0 * 100).toFixed(2)}%, ${hexToRgba(topBandColor, topBandAlphaBottom)} ${(topBandY1 * 100).toFixed(2)}%, ${hexToRgba(topBandColor, topBandAlphaBottom)} 100%)`;
                      const bottomBandGradient = `linear-gradient(180deg, ${hexToRgba(bottomBandColor, bottomBandAlphaTop)} 0%, ${hexToRgba(bottomBandColor, bottomBandAlphaTop)} ${(bottomBandY0 * 100).toFixed(2)}%, ${hexToRgba(bottomBandColor, bottomBandAlphaBottom)} ${(bottomBandY1 * 100).toFixed(2)}%, ${hexToRgba(bottomBandColor, bottomBandAlphaBottom)} 100%)`;

                      const portraitDefaultEnabled = stableId !== "00_thumb_2";
                      const portraitEnabled =
                        Boolean(context?.portrait_available) &&
                        Boolean(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.enabled", portraitDefaultEnabled));
                      const portraitZoom = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.zoom", 1.0));
                      const portraitOffX = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_x", 0.0));
                      const portraitOffY = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.offset_y", 0.0));
                      const portraitBrightness = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.fg_brightness", 1.2));
                      const portraitContrast = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.fg_contrast", 1.08));
                      const portraitColor = Number(resolveEditorLeafValue(context, overridesLeaf, "overrides.portrait.fg_color", 0.98));
                      const portraitFilter = `brightness(${portraitBrightness}) contrast(${portraitContrast}) saturate(${portraitColor})`;

                      const rawPortraitBox = (context as any)?.portrait_dest_box_norm;
                      const portraitBox =
                        Array.isArray(rawPortraitBox) && rawPortraitBox.length === 4
                          ? rawPortraitBox.map((v: any) => Number(v))
                          : [0.29, 0.06, 0.42, 0.76];
                      const portraitAnchor = String((context as any)?.portrait_anchor ?? "bottom_center");
                      const anchorIsCenter = portraitAnchor.toLowerCase() === "center";
                      const objectPos = anchorIsCenter ? "50% 50%" : "50% 100%";
                      const origin = anchorIsCenter ? "50% 50%" : "50% 100%";

                      const boxLeft = width ? Math.round(width * portraitBox[0]) : 0;
                      const boxTop = height ? Math.round(height * portraitBox[1]) : 0;
                      const boxW = width ? Math.round(width * portraitBox[2]) : 0;
                      const boxH = height ? Math.round(height * portraitBox[3]) : 0;
                      const offPxX = width ? portraitOffX * width : 0;
                      const offPxY = height ? portraitOffY * height : 0;

                      return (
                        <>
                          <div className="thumbnail-chat-editor-page__canvas-world" style={{ transform: viewTransform }}>
                          <div
                            style={{ position: "absolute", inset: 0, cursor: "grab", zIndex: 0 }}
                            onPointerDown={beginCanvasBgDrag}
                            onWheel={handleCanvasBgWheel}
                          >
                            {bgPreviewSrc ? (
                              <div style={{ position: "absolute", inset: 0 }}>
                                <div style={{ position: "absolute", left: shiftX, top: shiftY, width: "100%", height: "100%" }}>
                                  <div
                                    style={{
                                      width: "100%",
                                      height: "100%",
                                      transform: `scale(${bgZoom})`,
                                      transformOrigin: "top left",
                                    }}
                                  >
                                    <img
                                      src={bgPreviewSrc}
                                      alt="bg"
                                      draggable={false}
                                      style={{ width: "100%", height: "100%", objectFit: "cover", filter: bgFilter, pointerEvents: "none" }}
                                      onError={() => {
                                        const candidates = bgCandidates;
                                        setBgPreviewSrc((current) => {
                                          const idx = candidates.findIndex((c) => c === current);
                                          if (idx >= 0 && idx < candidates.length - 1) {
                                            return candidates[idx + 1];
                                          }
                                          return null;
                                        });
                                      }}
                                    />
                                  </div>
                                </div>
                              </div>
                            ) : (
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  color: "rgba(255,255,255,0.7)",
                                  fontSize: 13,
                                }}
                              >
                                背景画像が見つかりません（10_bg.* / 90_bg_ai_raw.*）
                              </div>
                            )}
                          </div>

                          {overlaysEnabled ? (
                            <div style={{ position: "absolute", inset: 0, backgroundImage: leftTszGradient, pointerEvents: "none", zIndex: 20 }} />
                          ) : null}
                          {topBandEnabled ? (
                            <div style={{ position: "absolute", inset: 0, backgroundImage: topBandGradient, pointerEvents: "none", zIndex: 20 }} />
                          ) : null}
                          {bottomBandEnabled ? (
                            <div style={{ position: "absolute", inset: 0, backgroundImage: bottomBandGradient, pointerEvents: "none", zIndex: 20 }} />
                          ) : null}

                          {canvasGuidesEnabled ? (
                            <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 25 }}>
                              <div
                                style={{
                                  position: "absolute",
                                  left: "5%",
                                  top: "5%",
                                  right: "5%",
                                  bottom: "5%",
                                  border: `${ui(1)}px dashed rgba(255,255,255,0.18)`,
                                  borderRadius: ui(10),
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  left: "33.333%",
                                  top: 0,
                                  bottom: 0,
                                  width: ui(1),
                                  background: "rgba(255,255,255,0.12)",
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  left: "66.666%",
                                  top: 0,
                                  bottom: 0,
                                  width: ui(1),
                                  background: "rgba(255,255,255,0.12)",
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  top: "33.333%",
                                  left: 0,
                                  right: 0,
                                  height: ui(1),
                                  background: "rgba(255,255,255,0.12)",
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  top: "66.666%",
                                  left: 0,
                                  right: 0,
                                  height: ui(1),
                                  background: "rgba(255,255,255,0.12)",
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  left: "50%",
                                  top: 0,
                                  bottom: 0,
                                  width: ui(1),
                                  transform: "translateX(-50%)",
                                  background: "rgba(255,255,255,0.18)",
                                }}
                              />
                              <div
                                style={{
                                  position: "absolute",
                                  top: "50%",
                                  left: 0,
                                  right: 0,
                                  height: ui(1),
                                  transform: "translateY(-50%)",
                                  background: "rgba(255,255,255,0.18)",
                                }}
                              />
                            </div>
                          ) : null}

                          {canvasSnapGuides ? (
                            <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 26 }}>
                              {canvasSnapGuides.xNorm !== null ? (
                                <div
                                  style={{
                                    position: "absolute",
                                    left: `${(clampNumber(Number(canvasSnapGuides.xNorm), 0, 1) * 100).toFixed(3)}%`,
                                    top: 0,
                                    bottom: 0,
                                    width: ui(2),
                                    transform:
                                      Number(canvasSnapGuides.xNorm) <= 0.0001
                                        ? "translateX(0)"
                                        : Number(canvasSnapGuides.xNorm) >= 0.9999
                                          ? "translateX(-100%)"
                                          : "translateX(-50%)",
                                    background: "rgba(34, 197, 94, 0.92)",
                                    boxShadow: `0 0 ${ui(10)}px rgba(34, 197, 94, 0.45)`,
                                  }}
                                />
                              ) : null}
                              {canvasSnapGuides.yNorm !== null ? (
                                <div
                                  style={{
                                    position: "absolute",
                                    top: `${(clampNumber(Number(canvasSnapGuides.yNorm), 0, 1) * 100).toFixed(3)}%`,
                                    left: 0,
                                    right: 0,
                                    height: ui(2),
                                    transform:
                                      Number(canvasSnapGuides.yNorm) <= 0.0001
                                        ? "translateY(0)"
                                        : Number(canvasSnapGuides.yNorm) >= 0.9999
                                          ? "translateY(-100%)"
                                          : "translateY(-50%)",
                                    background: "rgba(34, 197, 94, 0.92)",
                                    boxShadow: `0 0 ${ui(10)}px rgba(34, 197, 94, 0.45)`,
                                  }}
                                />
                              ) : null}
                            </div>
                          ) : null}

                          {portraitEnabled ? (
                            <div style={{ position: "absolute", inset: 0, zIndex: 15, pointerEvents: "none" }}>
                              {boxW > 0 && boxH > 0 ? (
                                <div
                                  style={{
                                    position: "absolute",
                                    left: boxLeft,
                                    top: boxTop,
                                    width: boxW,
                                    height: boxH,
                                    transform: `translate(${offPxX}px, ${offPxY}px)`,
                                    cursor: "grab",
                                    touchAction: "none",
                                    borderRadius: ui(6),
                                    border:
                                      canvasSelectedAsset === "portrait" ? `${ui(2)}px solid rgba(59, 130, 246, 0.95)` : "none",
                                    boxShadow:
                                      canvasSelectedAsset === "portrait"
                                        ? `0 0 0 ${ui(3)}px rgba(59, 130, 246, 0.18)`
                                        : "none",
                                    pointerEvents: "auto",
                                  }}
                                  onPointerDown={beginCanvasPortraitDrag}
                                  onWheel={handleCanvasPortraitWheel}
                                >
                                  {portraitPreviewSrc ? (
                                    <img
                                      src={portraitPreviewSrc}
                                      alt="portrait"
                                      draggable={false}
                                      style={{
                                        width: "100%",
                                        height: "100%",
                                        objectFit: "contain",
                                        objectPosition: objectPos,
                                        transform: `scale(${portraitZoom})`,
                                        transformOrigin: origin,
                                        filter: portraitFilter,
                                        pointerEvents: "none",
                                      }}
                                      onError={() => {
                                        const candidates = portraitCandidates;
                                        setPortraitPreviewSrc((current) => {
                                          const idx = candidates.findIndex((c) => c === current);
                                          if (idx >= 0 && idx < candidates.length - 1) {
                                            return candidates[idx + 1];
                                          }
                                          return null;
                                        });
                                      }}
                                    />
                                  ) : (
                                    <div
                                      style={{
                                        position: "absolute",
                                        inset: 0,
                                        display: "flex",
                                        alignItems: "center",
                                        justifyContent: "center",
                                        color: "rgba(255,255,255,0.7)",
                                        fontSize: 13,
                                      }}
                                    >
                                      肖像画像が見つかりません（20_portrait.*）
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 30 }}>
                            {Object.keys(textSlotImages).length > 0 ? (
                              Object.entries(textSlotImages).map(([slotKey, url]) => {
                                const line = textLineSpecLines[slotKey];
                                const box = textSlotBoxes?.[slotKey] ?? null;
                                const rot = clampNumber(Number((line as any)?.rotate_deg ?? 0), -180, 180);
                                const dx = width ? Number((line as any)?.offset_x ?? 0) * width : 0;
                                const dy = height ? Number((line as any)?.offset_y ?? 0) * height : 0;
                                const slotTranslate = `translate3d(${dx}px, ${dy}px, 0)`;
                                const cx =
                                  width && Array.isArray(box) && box.length === 4
                                    ? (Number(box[0]) + Number(box[2]) * 0.5) * width
                                    : width * 0.5;
                                const cy =
                                  height && Array.isArray(box) && box.length === 4
                                    ? (Number(box[1]) + Number(box[3]) * 0.5) * height
                                    : height * 0.5;
                                return (
                                  <div key={slotKey} style={{ position: "absolute", inset: 0, transform: slotTranslate }}>
                                    <div
                                      style={{
                                        position: "absolute",
                                        inset: 0,
                                        transform: rot ? `rotate(${rot}deg)` : undefined,
                                        transformOrigin: `${cx}px ${cy}px`,
                                      }}
                                    >
                                      <img
                                        src={url}
                                        alt={`text:${slotKey}`}
                                        draggable={false}
                                        style={{ width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none" }}
                                        onError={() => {
                                          setTextSlotImages((current) => {
                                            const next = { ...(current ?? {}) };
                                            delete next[slotKey];
                                            return next;
                                          });
                                          setTextSlotStatus((current) => ({
                                            loading: false,
                                            error: current.error ?? "文字レイヤの読み込みに失敗しました",
                                          }));
                                        }}
                                      />
                                    </div>
                                  </div>
                                );
                              })
                            ) : textSlotStatus.loading ? (
                              <div
                                style={{
                                  position: "absolute",
                                  left: 12,
                                  bottom: 12,
                                  padding: "6px 10px",
                                  borderRadius: 10,
                                  background: "rgba(0,0,0,0.55)",
                                  color: "rgba(255,255,255,0.9)",
                                  fontSize: 12,
                                  letterSpacing: 0.2,
                                }}
                              >
                                文字レイヤ生成中…
                              </div>
                            ) : textSlotStatus.error ? (
                              <div
                                style={{
                                  position: "absolute",
                                  left: 12,
                                  bottom: 12,
                                  padding: "6px 10px",
                                  borderRadius: 10,
                                  background: "rgba(239, 68, 68, 0.25)",
                                  color: "rgba(255,255,255,0.92)",
                                  fontSize: 12,
                                  letterSpacing: 0.2,
                                }}
                              >
                                文字レイヤ: {textSlotStatus.error}
                              </div>
                            ) : null}
                          </div>

                          <div style={{ position: "absolute", inset: 0, zIndex: 40, pointerEvents: "none" }}>
                            {Object.entries(textSlotBoxes ?? {}).map(([slotKey, box]) => {
                              if (!slotKey || !Array.isArray(box) || box.length !== 4) {
                                return null;
                              }
                              const line = (textLineSpecLines?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as any;
                              const left = Number(box[0]) * width + Number(line.offset_x ?? 0) * width;
                              const top = Number(box[1]) * height + Number(line.offset_y ?? 0) * height;
                              const wPx = Number(box[2]) * width;
                              const hPx = Number(box[3]) * height;
                              if (![left, top, wPx, hPx].every((v) => Number.isFinite(v))) {
                                return null;
                              }
                              const selected = canvasSelectedAsset === "text" && selectedTextSlot === slotKey;
                              const hovered = hoveredTextSlot === slotKey;
                              const showGuides = hovered || selected;
                              return (
                                <div
                                  key={slotKey}
                                  style={{
                                    position: "absolute",
                                    left,
                                    top,
                                    width: wPx,
                                    height: hPx,
                                    cursor: "grab",
                                    touchAction: "none",
                                    borderRadius: ui(8),
                                    boxSizing: "border-box",
                                    border: selected
                                      ? `${ui(2)}px solid rgba(59, 130, 246, 0.95)`
                                      : showGuides
                                        ? `${ui(1)}px dashed ${
                                            hovered ? "rgba(255,255,255,0.32)" : "rgba(255,255,255,0.18)"
                                          }`
                                        : "none",
                                    background: selected
                                      ? "rgba(59, 130, 246, 0.06)"
                                      : hovered
                                        ? "rgba(255,255,255,0.05)"
                                        : "transparent",
                                    pointerEvents: "auto",
                                  }}
                                  onPointerEnter={() => setHoveredTextSlot(slotKey)}
                                  onPointerLeave={() => setHoveredTextSlot((current) => (current === slotKey ? null : current))}
                                  onPointerDown={(event) => beginCanvasTextSlotDrag(event, slotKey)}
                                >
                                  {selected ? (
                                    <>
                                      <div
                                        style={{
                                          position: "absolute",
                                          left: ui(8),
                                          bottom: ui(8),
                                          padding: `${ui(2)}px ${ui(8)}px`,
                                          borderRadius: 999,
                                          background: "rgba(0,0,0,0.35)",
                                          color: "rgba(255,255,255,0.85)",
                                          fontSize: ui(11),
                                          letterSpacing: 0.3,
                                          pointerEvents: "none",
                                        }}
                                      >
                                        TEXT: {slotKey}
                                      </div>
                                      <div
                                        style={{
                                          position: "absolute",
                                          left: "50%",
                                          top: -ui(20),
                                          width: ui(2),
                                          height: ui(18),
                                          transform: "translate(-50%, 0)",
                                          background: "rgba(255,255,255,0.45)",
                                          pointerEvents: "none",
                                        }}
                                      />
                                      <div
                                        title="回転（Shiftでスナップ）"
                                        style={{
                                          position: "absolute",
                                          left: "50%",
                                          top: -ui(22),
                                          width: ui(14),
                                          height: ui(14),
                                          transform: "translate(-50%, 0)",
                                          borderRadius: 999,
                                          background: "rgba(255,255,255,0.92)",
                                          border: `${ui(1)}px solid rgba(15, 23, 42, 0.45)`,
                                          boxShadow: `0 ${ui(1)}px ${ui(2)}px rgba(0,0,0,0.35)`,
                                          cursor: "grab",
                                          touchAction: "none",
                                          pointerEvents: "auto",
                                        }}
                                        onPointerDown={(event) => beginCanvasTextSlotRotate(event, slotKey)}
                                      />
                                      <div
                                        title="サイズ（ドラッグ）"
                                        style={{
                                          position: "absolute",
                                          left: "100%",
                                          top: "100%",
                                          width: ui(12),
                                          height: ui(12),
                                          transform: "translate(-50%, -50%)",
                                          borderRadius: ui(3),
                                          background: "rgba(255,255,255,0.92)",
                                          border: `${ui(1)}px solid rgba(15, 23, 42, 0.45)`,
                                          boxShadow: `0 ${ui(1)}px ${ui(2)}px rgba(0,0,0,0.35)`,
                                          cursor: "nwse-resize",
                                          touchAction: "none",
                                          pointerEvents: "auto",
                                        }}
                                        onPointerDown={(event) => beginCanvasTextSlotScale(event, slotKey)}
                                      />
                                    </>
                                  ) : null}
                                </div>
                              );
                            })}
                          </div>

                          </div>

                          <div className="thumbnail-chat-editor-page__canvas-hud">
                            <div className="thumbnail-chat-editor-page__canvas-dock" role="toolbar" aria-label="Canvas controls">
                              <div className="thumbnail-chat-editor-page__canvas-dock-row">
                                <button
                                  type="button"
                                  className={`thumbnail-chat-editor-page__canvas-btn ${canvasHandActive ? "is-active" : ""}`}
                                  title="ハンドツール（Space / H）"
                                  onClick={() => {
                                    setCanvasHandMode((prev) => !prev);
                                    focusCanvasStage();
                                  }}
                                >
                                  Hand
                                </button>
                                <button
                                  type="button"
                                  className={`thumbnail-chat-editor-page__canvas-btn ${canvasGuidesEnabled ? "is-active" : ""}`}
                                  title="ガイド（G）"
                                  onClick={() => {
                                    setCanvasGuidesEnabled((prev) => !prev);
                                    focusCanvasStage();
                                  }}
                                >
                                  Guides
                                </button>
                                <span className="thumbnail-chat-editor-page__canvas-sep" />
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={handleUndoAny}
                                  disabled={!canUndo}
                                  title="戻す（Ctrl/⌘+Z）"
                                >
                                  Undo
                                </button>
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={handleRedoAny}
                                  disabled={!canRedo}
                                  title="やり直し（Ctrl/⌘+Shift+Z）"
                                >
                                  Redo
                                </button>
                                <span className="thumbnail-chat-editor-page__canvas-sep" />
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={() => bumpCanvasViewScale(-1)}
                                  title="表示ズームアウト（-）"
                                >
                                  −
                                </button>
                                <input
                                  className="thumbnail-chat-editor-page__canvas-range"
                                  type="range"
                                  min={CANVAS_VIEW_MIN_ZOOM}
                                  max={CANVAS_VIEW_MAX_ZOOM}
                                  step={0.01}
                                  value={Number.isFinite(canvasView.scale) ? canvasView.scale : 1}
                                  onChange={(e) => setCanvasViewScaleAtCenter(Number(e.target.value))}
                                  aria-label="表示ズーム"
                                />
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={() => bumpCanvasViewScale(1)}
                                  title="表示ズームイン（+）"
                                >
                                  +
                                </button>
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={() => {
                                    resetCanvasView();
                                    focusCanvasStage();
                                  }}
                                  title="表示をリセット（0）"
                                >
                                  Fit
                                </button>
                                <span className="thumbnail-chat-editor-page__canvas-zoom-label" aria-label="Zoom percent">
                                  {Math.round((Number(canvasView.scale ?? 1) || 1) * 100)}%
                                </span>
                              </div>

                              <div className="thumbnail-chat-editor-page__canvas-dock-row">
                                <div className="thumbnail-chat-editor-page__canvas-segmented" role="group" aria-label="Layer">
                                  <button
                                    type="button"
                                    className={`thumbnail-chat-editor-page__canvas-segment ${
                                      canvasSelectedAsset === "bg" ? "is-active" : ""
                                    }`}
                                    onClick={() => handleCanvasSelectAsset("bg")}
                                  >
                                    背景
                                  </button>
                                  <button
                                    type="button"
                                    className={`thumbnail-chat-editor-page__canvas-segment ${
                                      canvasSelectedAsset === "portrait" ? "is-active" : ""
                                    }`}
                                    onClick={() => handleCanvasSelectAsset("portrait")}
                                  >
                                    人物
                                  </button>
                                  <button
                                    type="button"
                                    className={`thumbnail-chat-editor-page__canvas-segment ${
                                      canvasSelectedAsset === "text" ? "is-active" : ""
                                    }`}
                                    onClick={() => handleCanvasSelectAsset("text")}
                                  >
                                    文字
                                  </button>
                                </div>
                                {canvasSelectedAsset === "text" ? (
                                  <select
                                    className="thumbnail-chat-editor-page__canvas-select"
                                    value={selectedTextSlot ?? ""}
                                    onChange={(e) => {
                                      handleCanvasSelectAsset("text");
                                      setSelectedTextSlot(e.target.value);
                                      focusCanvasStage();
                                    }}
                                    aria-label="Text slot"
                                  >
                                    {textSlotKeys.map((slotKey) => (
                                      <option key={slotKey} value={slotKey}>
                                        {slotKey}
                                      </option>
                                    ))}
                                  </select>
                                ) : null}
                                <button
                                  type="button"
                                  className="thumbnail-chat-editor-page__canvas-btn"
                                  onClick={handleCanvasResetSelected}
                                  disabled={!canvasSelectedAsset || (canvasSelectedAsset === "text" && !selectedTextSlot)}
                                  title="選択レイヤをリセット"
                                >
                                  リセット
                                </button>
                              </div>

                              {(() => {
                                if (canvasSelectedAsset === "bg") {
                                  const bgZoomValue = clampNumber(Number(bgZoom ?? 1), 1.0, CANVAS_BG_MAX_ZOOM);
                                  return (
                                    <div className="thumbnail-chat-editor-page__canvas-dock-row" aria-label="Background controls">
                                      <span className="thumbnail-chat-editor-page__canvas-field-label">BG Zoom</span>
                                      <input
                                        className="thumbnail-chat-editor-page__canvas-range thumbnail-chat-editor-page__canvas-range--wide"
                                        type="range"
                                        min={1}
                                        max={CANVAS_BG_MAX_ZOOM}
                                        step={0.01}
                                        value={bgZoomValue}
                                        onPointerDown={() => {
                                          beginOverridesInteraction();
                                          focusCanvasStage();
                                        }}
                                        onPointerUp={commitOverridesInteraction}
                                        onPointerCancel={commitOverridesInteraction}
                                        onChange={(e) => {
                                          const next = clampNumber(Number(e.target.value), 1.0, CANVAS_BG_MAX_ZOOM);
                                          dispatchOverrides({
                                            type: "merge_patch",
                                            patch: { "overrides.bg_pan_zoom.zoom": Number(next.toFixed(3)) },
                                            recordHistory: false,
                                          });
                                        }}
                                        aria-label="背景ズーム"
                                      />
                                      <span className="thumbnail-chat-editor-page__canvas-value">{bgZoomValue.toFixed(2)}x</span>
                                    </div>
                                  );
                                }

                                if (canvasSelectedAsset === "portrait") {
                                  const portraitZoomValue = clampNumber(Number(portraitZoom ?? 1), 0.5, 2.0);
                                  return (
                                    <div className="thumbnail-chat-editor-page__canvas-dock-row" aria-label="Portrait controls">
                                      <span className="thumbnail-chat-editor-page__canvas-field-label">人物 Zoom</span>
                                      <input
                                        className="thumbnail-chat-editor-page__canvas-range thumbnail-chat-editor-page__canvas-range--wide"
                                        type="range"
                                        min={0.5}
                                        max={2}
                                        step={0.01}
                                        value={portraitZoomValue}
                                        onPointerDown={() => {
                                          beginOverridesInteraction();
                                          focusCanvasStage();
                                        }}
                                        onPointerUp={commitOverridesInteraction}
                                        onPointerCancel={commitOverridesInteraction}
                                        onChange={(e) => {
                                          const next = clampNumber(Number(e.target.value), 0.5, 2.0);
                                          dispatchOverrides({
                                            type: "merge_patch",
                                            patch: { "overrides.portrait.zoom": Number(next.toFixed(3)) },
                                            recordHistory: false,
                                          });
                                        }}
                                        aria-label="人物ズーム"
                                      />
                                      <span className="thumbnail-chat-editor-page__canvas-value">{portraitZoomValue.toFixed(2)}x</span>
                                    </div>
                                  );
                                }

                                if (canvasSelectedAsset === "text") {
                                  const slotKey = String(selectedTextSlot ?? "").trim();
                                  if (!slotKey) return null;
                                  const line = (textLineSpecLines?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 }) as any;
                                  const scaleValue = clampNumber(Number(line.scale ?? 1), 0.25, 4);
                                  const rotValue = clampNumber(normalizeRotationDeg(Number(line.rotate_deg ?? 0)), -180, 180);
                                  return (
                                    <div className="thumbnail-chat-editor-page__canvas-dock-row" aria-label="Text controls">
                                      <span className="thumbnail-chat-editor-page__canvas-field-label">文字</span>
                                      <span className="thumbnail-chat-editor-page__canvas-field-label">Scale</span>
                                      <input
                                        className="thumbnail-chat-editor-page__canvas-range thumbnail-chat-editor-page__canvas-range--wide"
                                        type="range"
                                        min={0.25}
                                        max={4}
                                        step={0.01}
                                        value={scaleValue}
                                        onPointerDown={() => {
                                          beginTextInteraction();
                                          focusCanvasStage();
                                        }}
                                        onPointerUp={commitTextInteraction}
                                        onPointerCancel={commitTextInteraction}
                                        onChange={(e) => {
                                          const next = clampNumber(Number(e.target.value), 0.25, 4);
                                          setTextLineSpecLines((current) => {
                                            const nextLines = { ...(current ?? {}) };
                                            const existing = nextLines[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
                                            nextLines[slotKey] = { ...existing, scale: Number(next.toFixed(3)) };
                                            return nextLines;
                                          });
                                        }}
                                        aria-label="文字スケール"
                                      />
                                      <span className="thumbnail-chat-editor-page__canvas-value">{scaleValue.toFixed(2)}x</span>
                                      <span className="thumbnail-chat-editor-page__canvas-sep" />
                                      <span className="thumbnail-chat-editor-page__canvas-field-label">Rot</span>
                                      <input
                                        className="thumbnail-chat-editor-page__canvas-range thumbnail-chat-editor-page__canvas-range--wide"
                                        type="range"
                                        min={-180}
                                        max={180}
                                        step={1}
                                        value={rotValue}
                                        onPointerDown={() => {
                                          beginTextInteraction();
                                          focusCanvasStage();
                                        }}
                                        onPointerUp={commitTextInteraction}
                                        onPointerCancel={commitTextInteraction}
                                        onChange={(e) => {
                                          const next = clampNumber(Number(e.target.value), -180, 180);
                                          setTextLineSpecLines((current) => {
                                            const nextLines = { ...(current ?? {}) };
                                            const existing = nextLines[slotKey] ?? ({ offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 } as any);
                                            nextLines[slotKey] = { ...existing, rotate_deg: Number(next.toFixed(3)) };
                                            return nextLines;
                                          });
                                        }}
                                        aria-label="文字回転"
                                      />
                                      <span className="thumbnail-chat-editor-page__canvas-value">{Math.round(rotValue)}°</span>
                                    </div>
                                  );
                                }

                                return null;
                              })()}
                            </div>

                            <div className="thumbnail-chat-editor-page__canvas-hint">
                              {canvasPanningView || canvasHandActive
                                ? "表示: Space/中クリック+ドラッグ=移動 / ホイール=ズーム"
                                : canvasSelectedAsset === "bg"
                                  ? "背景: ドラッグ=位置 / Alt+ホイール=ズーム / ホイール=表示ズーム"
                                  : canvasSelectedAsset === "portrait"
                                    ? "人物: ドラッグ=位置 / Alt+ホイール=ズーム / ホイール=表示ズーム"
                                    : canvasSelectedAsset === "text"
                                      ? "文字: ドラッグ=位置 / 右下=拡大 / 上=回転 / Tab=次の文字"
                                      : "クリックして編集対象を選択（ホイール=表示ズーム / Spaceで表示移動）"}
                            </div>
                          </div>
                        </>
                      );
                    })()}
                  </div>
                ) : (
                  <div className="thumbnail-chat-editor-page__preview-placeholder">まずCHとVideoを選んでください。</div>
                )}
              </div>
            </div>

            <div className="thumbnail-chat-editor-page__preview-footer">
              <div className="thumbnail-chat-editor-page__preview-actions">
                <div className="thumbnail-chat-editor-page__segmented" aria-label="Before/After">
                  <button
                    type="button"
                    className={`thumbnail-chat-editor-page__segment ${compareMode === "after" ? "is-active" : ""}`}
                    onClick={() => {
                      setPreviewMode("rendered");
                      setCompareMode("after");
                    }}
                    aria-pressed={compareMode === "after"}
                  >
                    After（最新）
                  </button>
                  <button
                    type="button"
                    className={`thumbnail-chat-editor-page__segment ${compareMode === "before" ? "is-active" : ""}`}
                    onClick={() => {
                      setPreviewMode("rendered");
                      setCompareMode("before");
                    }}
                    disabled={!beforeSnapshotUrl}
                    aria-pressed={compareMode === "before"}
                  >
                    Before（直前）
                  </button>
                </div>
                {!beforeSnapshotUrl && selectedChannel && selectedVideo ? (
                  <span className="status-chip">Beforeは保存後に有効</span>
                ) : null}
                {isDirty ? (
                  <span className="status-chip status-chip--warning">
                    thumb_spec: <code>{dirtyOps.length}</code>
                  </span>
                ) : (
                  <span className="status-chip">thumb_spec: OK</span>
                )}
                {isTextDirty ? (
                  <span className="status-chip status-chip--warning">text: 未保存</span>
                ) : (
                  <span className="status-chip">text: OK</span>
                )}
                {previewMode === "canvas" ? <span className="status-chip">Canvas編集中</span> : null}
                <button
                  type="button"
                  className="thumbnail-chat-editor-page__canvas-btn"
                  onClick={toggleFocusMode}
                  title={focusModeActive ? "一覧/チャットを復帰" : "一覧/チャットを隠してプレビューを最大化"}
                >
                  {focusModeActive ? "UI表示" : "Focus"}
                </button>
              </div>
              <div className="thumbnail-chat-editor-page__preview-actions">
                {contextLoading ? <span className="status-chip status-chip--warning">context読込中…</span> : null}
                {textLineSpecStatus.loading ? <span className="status-chip status-chip--warning">text spec読込中…</span> : null}
                {textLineSpecStatus.error ? (
                  <span className="status-chip status-chip--warning">text spec error</span>
                ) : null}
                {context ? (
                  <span className="status-chip">
                    overrides: <code>{Object.keys(overridesLeaf ?? {}).length}</code> / effective:{" "}
                    <code>{Object.keys(context.effective_leaf ?? {}).length}</code>
                  </span>
                ) : null}
                <span className="status-chip">
                  provider: <code>{lastPatch?.provider ?? "—"}</code>
                </span>
                <span className="status-chip">
                  confidence: <code>{lastPatch ? String(lastPatch.confidence ?? 0).slice(0, 4) : "—"}</code>
                </span>
                <span
                  className="status-chip"
                  title="変更は自動保存されません（「保存して再合成」を押した時だけ thumb_spec が更新されます）。"
                >
                  保存: 手動
                </span>
              </div>
            </div>
          </div>
	        </section>

	        {showChat ? (
	        <section className="thumbnail-chat-editor-page__card thumbnail-chat-editor-page__chat">
	          <div className="thumbnail-chat-editor-page__card-header">
	            <div className="thumbnail-chat-editor-page__card-title">
	              <strong>チャット</strong>
              <span className="thumbnail-chat-editor-page__card-subtitle">Ctrl/⌘ + Enter で送信</span>
            </div>
            <div className="thumbnail-chat-editor-page__preview-actions">
              <button className="action-button" type="button" onClick={() => setMessages([])} disabled={sending}>
                履歴クリア
              </button>
	            </div>
	          </div>

            <details className="thumbnail-chat-editor-page__tools">
              <summary>
                <span>編集ツール（Undo / 未保存 / プリセット）</span>
                {isAnyDirty ? (
                  <span className="thumbnail-chat-editor-page__tools-badge">
                    未保存 spec:{dirtyOps.length} / text:{isTextDirty ? "1" : "0"}
                  </span>
                ) : (
                  <span className="thumbnail-chat-editor-page__tools-badge is-clean">保存済み</span>
                )}
              </summary>
              <div className="thumbnail-chat-editor-page__tools-body">
                <div className="thumbnail-chat-editor-page__preview-actions">
                  <button
                    className="action-button"
                    type="button"
                    onClick={handleUndoAny}
                    disabled={!canUndo}
                  >
                    戻す
                  </button>
                  <button
                    className="action-button"
                    type="button"
                    onClick={handleRedoAny}
                    disabled={!canRedo}
                  >
                    やり直し
                  </button>
                  <button
                    className="action-button"
                    type="button"
                    onClick={() => {
                      dispatchOverrides({ type: "reset_to_base" });
                      setTextLineSpecLines(cloneTextLines(baseTextLineSpecLines));
                      setTextUndo([]);
                      setTextRedo([]);
                      setToast({ type: "success", message: "保存状態に戻しました。" });
                    }}
                    disabled={!isAnyDirty}
                  >
                    保存状態に戻す
                  </button>
                </div>

                <details className="thumbnail-chat-editor-page__ops">
                  <summary>thumb_spec 未保存の変更 ({dirtyOps.length})</summary>
                  {dirtyOps.length ? (
                    <ul className="thumbnail-chat-editor-page__ops-list">
                      {dirtyOps.map((op, idx) => (
                        <li key={`dirty.${idx}`}>
                          <code>{op.op}</code> <code>{op.path}</code>{" "}
                          {op.op === "set" ? <code>{JSON.stringify(op.value)}</code> : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="thumbnail-chat-editor-page__hint">未保存の変更はありません。</div>
                  )}
                </details>
                <details className="thumbnail-chat-editor-page__ops">
                  <summary>text-line 未保存</summary>
                  {isTextDirty ? (
                    <div className="thumbnail-chat-editor-page__hint">
                      Canvasで動かした文字位置/サイズ/回転は未保存です（保存して再合成で反映されます）。
                    </div>
                  ) : (
                    <div className="thumbnail-chat-editor-page__hint">text-line は保存済みです。</div>
                  )}
                </details>

                <label className="thumbnail-chat-editor-page__field">
                  <span className="thumbnail-chat-editor-page__label">プリセット名</span>
                  <input
                    type="text"
                    value={presetName}
                    onChange={(e) => setPresetName(e.target.value)}
                    placeholder="例: 赤枠＋文字大"
                  />
                </label>

                <div className="thumbnail-chat-editor-page__preview-actions">
                  <button
                    className="action-button"
                    type="button"
                    onClick={handleSavePresetFromCurrent}
                    disabled={!dirtyOps.length}
                  >
                    変更をプリセット保存
                  </button>
                  <button
                    className="action-button"
                    type="button"
                    onClick={handleSavePresetFromLastSuggestion}
                    disabled={!lastPatch?.ops?.length}
                  >
                    最後の提案をプリセット保存
                  </button>
                </div>

                <div className="thumbnail-chat-editor-page__preset-list">
                  {presets.length ? (
                    presets.map((preset) => (
                      <div key={preset.id} className="thumbnail-chat-editor-page__preset-row">
                        <div className="thumbnail-chat-editor-page__preset-meta">
                          <div className="thumbnail-chat-editor-page__preset-name">{preset.name}</div>
                          <div className="thumbnail-chat-editor-page__preset-sub">{preset.ops.length} ops</div>
                        </div>
                        <div className="thumbnail-chat-editor-page__preset-actions">
                          <button className="action-button" type="button" onClick={() => applyNamedPreset(preset)}>
                            適用
                          </button>
                          <button className="action-button" type="button" onClick={() => deletePreset(preset)}>
                            削除
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="thumbnail-chat-editor-page__hint">プリセットはまだありません。上のボタンで保存できます。</div>
                  )}
                </div>

                <dl className="thumbnail-chat-editor-page__kv">
                  <dt>選択</dt>
                  <dd>{selectedChannel && selectedVideo ? `${selectedChannel}-${selectedVideo}` : "—"}</dd>
                  <dt>stable</dt>
                  <dd>{stableId || "00_thumb"}</dd>
                  <dt>caption</dt>
                  <dd>{includeThumbCaption ? "true" : "false"}</dd>
                </dl>
              </div>
            </details>

	          <div className="thumbnail-chat-editor-page__chat-body">
	            {messages.length ? (
	              messages.map((m) => (
	                <article
                  key={m.id}
                  className={`thumbnail-chat-editor-page__message ${
                    m.role === "user" ? "thumbnail-chat-editor-page__message--user" : "thumbnail-chat-editor-page__message--assistant"
                  }`}
                >
	                  <div className="thumbnail-chat-editor-page__message-meta">
	                    <div className={`thumbnail-chat-editor-page__message-role ${m.role === "user" ? "is-user" : ""}`}>
	                      {m.role === "user" ? "あなた" : "AIアシスタント"}
	                    </div>
	                    <div className="thumbnail-chat-editor-page__message-actions">
	                      {m.patch?.provider ? (
	                        <span className="status-chip">
	                          provider: <code>{m.patch.provider}</code>
	                        </span>
                      ) : null}
                      {m.patch?.model ? (
                        <span className="status-chip">
                          model: <code>{m.patch.model}</code>
                        </span>
                      ) : null}
                      {m.patch ? (
                        <span className="status-chip">
                          conf: <code>{String(m.patch.confidence ?? 0).slice(0, 4)}</code>
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="thumbnail-chat-editor-page__message-text">{m.text}</div>
                  {m.patch?.ops?.length ? (
                    <div className="thumbnail-chat-editor-page__message-actions">
                      <button
                        className="action-button"
                        type="button"
                        onClick={() => applyPatchToLocal(m.patch as ThumbnailCommentPatch)}
                        disabled={!selectedChannel || !selectedVideo || saving}
                      >
                        適用
                      </button>
                      <button
                        className="action-button action-button--primary"
                        type="button"
                        onClick={() => void handleApplyPatchAndSave(m.patch as ThumbnailCommentPatch)}
                        disabled={!selectedChannel || !selectedVideo || saving}
                      >
                        適用して保存/再合成
                      </button>
                    </div>
                  ) : null}
                  {m.patch?.ops?.length ? (
                    <details className="thumbnail-chat-editor-page__ops">
                      <summary>ops ({m.patch.ops.length})</summary>
                      <ul className="thumbnail-chat-editor-page__ops-list">
                        {m.patch.ops.map((op, idx) => (
                          <li key={`${m.id}.${idx}`}>
                            <code>{op.op}</code> <code>{op.path}</code>{" "}
                            {op.op === "set" ? <code>{JSON.stringify(op.value)}</code> : null}{" "}
                            {op.reason ? <span className="thumbnail-chat-editor-page__hint">— {op.reason}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                </article>
              ))
            ) : (
              <div className="thumbnail-chat-editor-page__hint">
                例: 「背景を少し明るく」「人物をもう少し下」「文字を1.1倍」「縁を太く」
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="thumbnail-chat-editor-page__composer">
            <div className="thumbnail-chat-editor-page__composer-row">
	              <textarea
	                value={draft}
	                onChange={(e) => setDraft(e.target.value)}
	                rows={2}
	                placeholder="例: 背景を少し明るくして、文字を1.1倍。人物は少し下へ。"
	                disabled={!selectedChannel || !selectedVideo || sending}
	                onKeyDown={(e) => {
	                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
	                    e.preventDefault();
	                    void handleSend();
                  }
                }}
              />
              <div className="thumbnail-chat-editor-page__composer-actions">
                <label className="thumbnail-chat-editor-page__composer-ai">
                  <span className="thumbnail-chat-editor-page__toolbar-label">AI</span>
                  <select
                    value={providerPreference}
                    onChange={(e) => setProviderPreference(e.target.value as ThumbnailCommentPatchProviderPreference)}
                    disabled={sending}
                  >
                    <option value="ollama">Local (Ollama)</option>
                    <option value="auto">Auto</option>
                    <option value="codex_exec">codex exec</option>
                    <option value="gemini_cli">Gemini CLI</option>
                    <option value="qwen_cli">Qwen CLI</option>
                  </select>
                </label>
                {providerPreference === "ollama" ? (
                  <label className="thumbnail-chat-editor-page__composer-ai">
                    <span className="thumbnail-chat-editor-page__toolbar-label">model</span>
                    <input
                      type="text"
                      value={providerModel}
                      onChange={(e) => setProviderModel(e.target.value)}
                      placeholder="qwen2.5:7b-instruct"
                      disabled={sending}
                    />
                  </label>
                ) : null}
                <button
                  className="action-button action-button--primary"
                  type="button"
                  onClick={() => void handleSend()}
                  disabled={!draft.trim() || sending || !selectedChannel || !selectedVideo}
                >
                  {sending ? "送信中…" : "送信"}
                </button>
              </div>
            </div>

            <div className="thumbnail-chat-editor-page__example-chips">
              {[
                "背景を少し明るく",
                "文字を1.1倍",
                "人物を少し下へ",
                "縁を太く",
              ].map((example) => (
                <button
                  key={example}
                  type="button"
                  className="thumbnail-chat-editor-page__example-chip"
                  disabled={sending || !selectedChannel || !selectedVideo}
                  onClick={() => setDraft((prev) => (prev.trim() ? prev : example))}
                >
                  {example}
                </button>
              ))}
            </div>

	            <div className="thumbnail-chat-editor-page__hint">
	              Ctrl/⌘ + Enter で送信。提案は保存されません（「保存して再合成」を押した時だけ thumb_spec が更新されます）。
	            </div>
	          </div>
	        </section>
	        ) : null}
      </div>

      {toast ? (
        <div
          className={`thumbnail-chat-editor-page__toast ${toast.type === "error" ? "is-error" : "is-success"}`}
          role="status"
          aria-live="polite"
        >
          {toast.message}
        </div>
      ) : null}
    </section>
  );
}
