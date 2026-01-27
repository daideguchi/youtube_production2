import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import "./ThumbnailChatEditorPage.css";

import {
  buildThumbnailLayerSpecs,
  buildThumbnailTwoUp,
  fetchThumbnailCommentPatch,
  fetchThumbnailEditorContext,
  fetchThumbnailOverview,
  resolveApiUrl,
  updateThumbnailThumbSpec,
} from "../api/client";
import type {
  ThumbnailChannelBlock,
  ThumbnailCommentPatch,
  ThumbnailCommentPatchOp,
  ThumbnailEditorContext,
} from "../api/types";
import { safeLocalStorage } from "../utils/safeStorage";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  patch?: ThumbnailCommentPatch | null;
};

const LAST_SELECTION_KEY = "thumbnailChat:lastSelection";
const LAST_PRESET_KEY = "thumbnailChat:lastPreset";
const PRESETS_KEY = "thumbnailChat:presets:v1";
const LAST_PRESET_ID_KEY = "thumbnailChat:lastPresetId:v1";

type LocalPreset = {
  id: string;
  name: string;
  createdAt: string;
  ops: ThumbnailCommentPatchOp[];
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

  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const channels = useMemo(() => overview?.channels ?? [], [overview]);
  const channelBlock = useMemo(
    () => channels.find((c) => c.channel === selectedChannel) ?? null,
    [channels, selectedChannel]
  );
  const availableVideos = useMemo(() => (channelBlock?.projects ?? []).slice(), [channelBlock?.projects]);
  const filteredVideos = useMemo(() => {
    const q = videoQuery.trim().toLowerCase();
    const base = availableVideos;
    if (!q) return base;
    const filtered = base.filter((p) => {
      const vid = String(p.video ?? "").padStart(3, "0");
      const title = String((p.title ?? p.sheet_title ?? "") as any).toLowerCase();
      return vid.includes(q) || title.includes(q);
    });
    const selected = base.find((p) => String(p.video ?? "").padStart(3, "0") === selectedVideo);
    if (selected && !filtered.some((p) => String(p.video ?? "").padStart(3, "0") === selectedVideo)) {
      return [selected, ...filtered];
    }
    return filtered;
  }, [availableVideos, selectedVideo, videoQuery]);

  const selectedVideoIndex = useMemo(
    () => filteredVideos.findIndex((p) => String(p.video ?? "").padStart(3, "0") === selectedVideo),
    [filteredVideos, selectedVideo]
  );
  const prevVideo = selectedVideoIndex > 0 ? filteredVideos[selectedVideoIndex - 1] : null;
  const nextVideo =
    selectedVideoIndex >= 0 && selectedVideoIndex < filteredVideos.length - 1 ? filteredVideos[selectedVideoIndex + 1] : null;

  const canUndo = overridesState.undo.length > 0;
  const canRedo = overridesState.redo.length > 0;
  const isDirty = !leafOverridesEqual(overridesLeaf, baseOverridesLeaf);
  const dirtyOps = useMemo(() => diffLeafOverridesToOps(baseOverridesLeaf, overridesLeaf), [baseOverridesLeaf, overridesLeaf]);

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
      return;
    }
    setContextLoading(true);
    setContextError(null);
    try {
      const ctx = await fetchThumbnailEditorContext(selectedChannel, selectedVideo, { stable: stableId || null });
      setContext(ctx);
      dispatchOverrides({ type: "reset_base", base: ctx.overrides_leaf ?? {} });
    } catch (error) {
      setContext(null);
      dispatchOverrides({ type: "reset_base", base: {} });
      setContextError(error instanceof Error ? error.message : String(error));
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
  }, [loadContext]);

  useEffect(() => {
    if (!selectedChannel || !selectedVideo) return;
    safeLocalStorage.setItem(
      LAST_SELECTION_KEY,
      JSON.stringify({ channel: selectedChannel, video: selectedVideo, stable: stableId || "" })
    );
  }, [selectedChannel, selectedVideo, stableId]);

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
  }, [draft, includeThumbCaption, selectedChannel, selectedVideo, sending]);

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
    [allowGenerate, captureBeforeSnapshot, loadContext, previewUrl, saving, selectedChannel, selectedVideo, stableId]
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

            <label className="thumbnail-chat-editor-page__toolbar-field thumbnail-chat-editor-page__toolbar-field--video">
              <span className="thumbnail-chat-editor-page__toolbar-label">Video</span>
              <select
                value={selectedVideo}
                onChange={(e) => applyParams({ video: e.target.value })}
                disabled={!selectedChannel || overviewLoading}
              >
                <option value="">{selectedChannel ? "選択" : "CHを選択"}</option>
                {filteredVideos.map((p) => (
                  <option key={`${p.channel}-${p.video}`} value={String(p.video ?? "").padStart(3, "0")}>
                    {String(p.video ?? "").padStart(3, "0")} — {(p.title ?? p.sheet_title ?? "").trim() || "（無題）"}
                  </option>
                ))}
              </select>
            </label>

            <label className="thumbnail-chat-editor-page__toolbar-field thumbnail-chat-editor-page__toolbar-field--search">
              <span className="thumbnail-chat-editor-page__toolbar-label">検索</span>
              <input
                type="text"
                value={videoQuery}
                onChange={(e) => setVideoQuery(e.target.value)}
                placeholder="003 / タイトル…"
                disabled={!selectedChannel || overviewLoading}
              />
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

      <div className="thumbnail-chat-editor-page__layout">

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
              <button
                className="action-button"
                type="button"
                onClick={() => lastPatch && applyPatchToLocal(lastPatch)}
                disabled={!selectedChannel || !selectedVideo || !lastPatch?.ops?.length || saving}
              >
                最後の提案を適用
              </button>
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
              {displayPreviewUrl ? (
                <img
                  src={displayPreviewUrl}
                  alt="thumbnail preview"
                  onError={() => {
                    /* keep the frame */
                  }}
                />
              ) : (
                <div className="thumbnail-chat-editor-page__preview-placeholder">まずCHとVideoを選んでください。</div>
              )}
            </div>

            <div className="thumbnail-chat-editor-page__preview-footer">
              <div className="thumbnail-chat-editor-page__preview-actions">
                <div className="thumbnail-chat-editor-page__segmented" aria-label="Before/After">
                  <button
                    type="button"
                    className={`thumbnail-chat-editor-page__segment ${compareMode === "after" ? "is-active" : ""}`}
                    onClick={() => setCompareMode("after")}
                    aria-pressed={compareMode === "after"}
                  >
                    After（最新）
                  </button>
                  <button
                    type="button"
                    className={`thumbnail-chat-editor-page__segment ${compareMode === "before" ? "is-active" : ""}`}
                    onClick={() => setCompareMode("before")}
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
                    未保存: <code>{dirtyOps.length}</code>
                  </span>
                ) : (
                  <span className="status-chip">保存済み</span>
                )}
              </div>
              <div className="thumbnail-chat-editor-page__preview-actions">
                {contextLoading ? <span className="status-chip status-chip--warning">context読込中…</span> : null}
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
              </div>
            </div>

            <div className="thumbnail-chat-editor-page__hint">
              変更は自動保存されません（「保存して再合成」を押した時だけ thumb_spec が更新されます）。
            </div>
          </div>
	        </section>

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
                {isDirty ? (
                  <span className="thumbnail-chat-editor-page__tools-badge">未保存 {dirtyOps.length}</span>
                ) : (
                  <span className="thumbnail-chat-editor-page__tools-badge is-clean">保存済み</span>
                )}
              </summary>
              <div className="thumbnail-chat-editor-page__tools-body">
                <div className="thumbnail-chat-editor-page__preview-actions">
                  <button
                    className="action-button"
                    type="button"
                    onClick={() => dispatchOverrides({ type: "undo" })}
                    disabled={!canUndo}
                  >
                    戻す
                  </button>
                  <button
                    className="action-button"
                    type="button"
                    onClick={() => dispatchOverrides({ type: "redo" })}
                    disabled={!canRedo}
                  >
                    やり直し
                  </button>
                  <button
                    className="action-button"
                    type="button"
                    onClick={() => {
                      dispatchOverrides({ type: "reset_to_base" });
                      setToast({ type: "success", message: "保存状態に戻しました。" });
                    }}
                    disabled={!isDirty}
                  >
                    保存状態に戻す
                  </button>
                </div>

                <details className="thumbnail-chat-editor-page__ops">
                  <summary>未保存の変更 ({dirtyOps.length})</summary>
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
              <button
                className="action-button action-button--primary"
                type="button"
                onClick={() => void handleSend()}
                disabled={!draft.trim() || sending || !selectedChannel || !selectedVideo}
              >
                {sending ? "送信中…" : "送信"}
              </button>
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
