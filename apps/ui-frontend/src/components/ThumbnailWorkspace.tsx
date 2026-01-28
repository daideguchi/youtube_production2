import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  assignThumbnailLibraryAsset,
  buildThumbnailLayerSpecs,
  buildThumbnailTwoUp,
  composeThumbnailVariant,
  createPlanningRow,
  createThumbnailVariant,
  fetchPlanningChannelCsv,
  fetchThumbnailEditorContext,
  fetchThumbnailImageModels,
  fetchThumbnailLibrary,
  fetchThumbnailQcNotes,
  fetchThumbnailElementsSpec,
  fetchThumbnailTextLineSpec,
  fetchThumbnailVideoLayerSpecs,
  fetchThumbnailOverview,
  fetchThumbnailTemplates,
  generateThumbnailVariants,
  importThumbnailLibraryAsset,
  patchThumbnailVariant,
  previewThumbnailTextLayerSlots,
  replaceThumbnailVideoAsset,
  resolveApiUrl,
  updateThumbnailThumbSpec,
  updateThumbnailElementsSpec,
  updateThumbnailTextLineSpec,
  updateThumbnailQcNote,
  updatePlanning,
  updateThumbnailProject,
  updateThumbnailTemplates,
  uploadThumbnailVariantAsset,
  uploadThumbnailLibraryAssets,
  type ThumbnailElementSpec,
} from "../api/client";
import { ThumbnailBulkPanel } from "./ThumbnailBulkPanel";
import {
  ChannelSummary,
  PlanningCreatePayload,
  ThumbnailChannelBlock,
  ThumbnailChannelVideo,
  ThumbnailChannelTemplates,
  ThumbnailEditorContext,
  ThumbnailImageModelInfo,
  ThumbnailLibraryAsset,
  ThumbnailLayerSpecsBuildOutputMode,
  ThumbnailQcNotes,
  ThumbnailOverview,
  ThumbnailProject,
  ThumbnailProjectStatus,
  ThumbnailVariant,
  ThumbnailVariantStatus,
} from "../api/types";
import { safeLocalStorage } from "../utils/safeStorage";

type StatusFilter = "all" | "draft" | "in_progress" | "review" | "approved" | "archived";

type ThumbnailWorkspaceTab = "bulk" | "projects" | "gallery" | "qc" | "templates" | "library" | "channel";

type GalleryVariantMode = "selected" | "all" | "two_up" | "three_up";

type ThumbnailGalleryItem = {
  key: string;
  project: ThumbnailProject;
  variant: ThumbnailVariant | null;
  slotLabel?: string;
};

type VariantFormState = {
  projectKey: string;
  label: string;
  status: ThumbnailVariantStatus;
  imageUrl: string;
  imagePath: string;
  notes: string;
  tags: string;
  prompt: string;
  makeSelected: boolean;
  showAdvanced: boolean;
};

type ProjectFormState = {
  projectKey: string;
  owner: string;
  summary: string;
  notes: string;
  tags: string;
  dueAt: string;
};

type PlanningDialogState = {
  projectKey: string;
  channel: string;
  projectTitle: string;
  variantLabel?: string;
  videoNumber: string;
  no: string;
  title: string;
  thumbnailUpper: string;
  thumbnailLower: string;
  thumbnailTitle: string;
  thumbnailPrompt: string;
  dallePrompt: string;
  conceptIntent: string;
  outlineNotes: string;
  primaryTag: string;
  secondaryTag: string;
  lifeScene: string;
  keyConcept: string;
  benefit: string;
  analogy: string;
  descriptionLead: string;
  descriptionTakeaways: string;
  saving: boolean;
  error?: string;
};

type GenerateDialogState = {
  projectKey: string;
  channel: string;
  video: string;
  templateId: string;
  prompt: string;
  sourceTitle: string;
  thumbnailPrompt: string;
  imageModelKey: string;
  count: number;
  label: string;
  copyUpper: string;
  copyTitle: string;
  copyLower: string;
  saveToPlanning: boolean;
  status: ThumbnailVariantStatus;
  makeSelected: boolean;
  tags: string;
  notes: string;
  saving: boolean;
  error?: string;
};

type GalleryCopyEditState = {
  projectKey: string;
  channel: string;
  video: string;
  projectTitle: string;
  copyUpper: string;
  copyTitle: string;
  copyLower: string;
  saving: boolean;
  error?: string;
};

type LayerTuningDialogState = {
  projectKey: string;
  cardKey: string;
  channel: string;
  video: string;
  stable: string | null;
  projectTitle: string;
  commentDraft: string;
  commentDraftByStable: Record<string, string>;
  loading: boolean;
  saving: boolean;
  building: boolean;
  allowGenerate: boolean;
  regenBg: boolean;
  outputMode: ThumbnailLayerSpecsBuildOutputMode;
  error?: string;
  context?: ThumbnailEditorContext;
  overridesLeaf: Record<string, any>;
};

type LayerTuningResizeHandle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

type LayerTuningPreviewDragState =
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
      kind: "portrait_scale";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      centerClientX: number;
      centerClientY: number;
      startZoom: number;
      startDist: number;
    }
  | {
      kind: "text";
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
      slotKey: string;
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startOffX: number;
      startOffY: number;
      width: number;
      height: number;
    }
  | {
      kind: "text_slot_scale";
      slotKey: string;
      pointerId: number;
      startClientX: number;
      startClientY: number;
      centerClientX: number;
      centerClientY: number;
      startScale: number;
      startDist: number;
    }
  | {
      kind: "text_slot_rotate";
      slotKey: string;
      pointerId: number;
      centerClientX: number;
      centerClientY: number;
      startRotationDeg: number;
      startAngleRad: number;
    }
  | {
      kind: "element";
      elementId: string;
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startX: number;
      startY: number;
      elementW: number;
      elementH: number;
      width: number;
      height: number;
    }
  | {
      kind: "element_resize";
      elementId: string;
      handle: LayerTuningResizeHandle;
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startX: number;
      startY: number;
      startW: number;
      startH: number;
      rotationDeg: number;
      width: number;
      height: number;
    }
  | {
      kind: "element_rotate";
      elementId: string;
      pointerId: number;
      centerClientX: number;
      centerClientY: number;
      startRotationDeg: number;
      startAngleRad: number;
    };

type PlanningEditableField = Exclude<
  keyof PlanningDialogState,
  "projectKey" | "channel" | "projectTitle" | "variantLabel" | "saving" | "error"
>;

type CardFeedback = {
  type: "success" | "error";
  message: ReactNode;
  timestamp: number;
};

type LibraryFormState = {
  video: string;
  pending: boolean;
  error?: string;
  success?: string;
};

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "draft", label: "ドラフト" },
  { key: "in_progress", label: "作業中" },
  { key: "review", label: "レビュー" },
  { key: "approved", label: "承認済み" },
  { key: "archived", label: "アーカイブ" },
];

const THUMBNAIL_WORKSPACE_TABS: { key: ThumbnailWorkspaceTab; label: string; description?: string }[] = [
  { key: "bulk", label: "量産", description: "コピー編集→Canva一括CSV" },
  { key: "projects", label: "案件", description: "サムネ案の登録・生成・採用" },
  { key: "gallery", label: "ギャラリー", description: "選択サムネ一覧 / ZIPダウンロード" },
  { key: "qc", label: "QC", description: "コンタクトシートで一括確認" },
  { key: "templates", label: "テンプレ", description: "チャンネルの型（AI生成用）" },
  { key: "library", label: "ライブラリ", description: "参考サムネの登録・紐付け" },
  { key: "channel", label: "チャンネル", description: "KPI / 最新動画プレビュー" },
];

const PROJECT_STATUS_OPTIONS: { value: ThumbnailProjectStatus; label: string }[] = [
  { value: "draft", label: "ドラフト" },
  { value: "in_progress", label: "作業中" },
  { value: "review", label: "レビュー中" },
  { value: "approved", label: "承認済み" },
  { value: "published", label: "公開済み" },
  { value: "archived", label: "アーカイブ" },
];

const PROJECT_STATUS_LABELS: Record<ThumbnailProjectStatus, string> = {
  draft: "ドラフト",
  in_progress: "作業中",
  review: "レビュー中",
  approved: "承認済み",
  published: "公開済み",
  archived: "アーカイブ",
};

const VARIANT_STATUS_OPTIONS: { value: ThumbnailVariantStatus; label: string }[] = [
  { value: "draft", label: "ドラフト" },
  { value: "candidate", label: "候補" },
  { value: "review", label: "レビュー中" },
  { value: "approved", label: "承認済み" },
  { value: "archived", label: "アーカイブ" },
];

const VARIANT_STATUS_LABELS: Record<ThumbnailVariantStatus, string> = VARIANT_STATUS_OPTIONS.reduce(
  (acc, option) => {
    acc[option.value] = option.label;
    return acc;
  },
  {} as Record<ThumbnailVariantStatus, string>
);

const SUPPORTED_THUMBNAIL_EXTENSIONS = /\.(png|jpe?g|webp)$/i;
const THUMBNAIL_ASSET_BASE_PATH = "workspaces/thumbnails/assets";
const DEFAULT_GALLERY_LIMIT = 30;
const VARIANT_REJECT_TAG = "rejected";

const isQcLibraryAsset = (asset: ThumbnailLibraryAsset): boolean => {
  const rel = (asset.relative_path ?? "").replace(/\\/g, "/");
  if (
    rel.startsWith("_qc/")
    || rel.startsWith("library/qc/")
    || rel.startsWith("qc/")
  ) {
    return true;
  }
  const fileName = asset.file_name ?? "";
  return fileName.startsWith("qc__") || fileName.startsWith("contactsheet");
};

const withCacheBust = (url: string, token?: string | null): string => {
  const value = (token ?? "").trim();
  if (!value) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(value)}`;
};

const normalizeVideoInput = (value?: string | null): string => {
  if (!value) {
    return "";
  }
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return "";
  }
  return String(parseInt(trimmed, 10));
};

const extractHumanCommentFromNotes = (notes?: string | null): string => {
  const raw = String(notes ?? "").trim();
  if (!raw) return "";
  const idx = raw.indexOf("修正済み:");
  if (idx >= 0) {
    return raw.slice(0, idx).trim();
  }
  return raw;
};

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

function resolveLayerTuningLeafValue(dialog: LayerTuningDialogState, path: string, fallback: any): any {
  const overrides = dialog.overridesLeaf ?? {};
  if (Object.prototype.hasOwnProperty.call(overrides, path)) {
    return overrides[path];
  }
  const defaults = dialog.context?.defaults_leaf ?? {};
  if (Object.prototype.hasOwnProperty.call(defaults, path)) {
    return defaults[path];
  }
  return fallback;
}

function hasLayerTuningLeafValue(dialog: LayerTuningDialogState, path: string): boolean {
  const overrides = dialog.overridesLeaf ?? {};
  if (Object.prototype.hasOwnProperty.call(overrides, path)) {
    return true;
  }
  const defaults = dialog.context?.defaults_leaf ?? {};
  return Object.prototype.hasOwnProperty.call(defaults, path);
}

function normalizeThumbnailStableId(raw: unknown): string | null {
  const rawValue = String(raw ?? "").trim();
  if (!rawValue) {
    return null;
  }
  const cleaned = rawValue.split("?")[0].split("#")[0].trim();
  if (!cleaned) {
    return null;
  }
  const base = cleaned.split("/").filter(Boolean).slice(-1)[0] ?? cleaned;
  const withoutExt = base.replace(/\.(png|jpg|jpeg|webp)$/i, "").trim();
  const lowered = withoutExt.toLowerCase();
  if (["default", "__default__", "00_thumb", "thumb"].includes(lowered)) {
    return null;
  }
  if (["thumb_1", "thumb1", "1", "a"].includes(lowered)) {
    return "00_thumb_1";
  }
  if (["thumb_2", "thumb2", "2", "b"].includes(lowered)) {
    return "00_thumb_2";
  }
  if (/^00_thumb_\d+$/i.test(withoutExt)) {
    return withoutExt;
  }
  const match = lowered.match(/(00_thumb_\d+)/);
  if (match) {
    return match[1];
  }
  return null;
}

function isLayerTuningLeafOverridden(dialog: LayerTuningDialogState, path: string): boolean {
  const overrides = dialog.overridesLeaf ?? {};
  return Object.prototype.hasOwnProperty.call(overrides, path);
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

const CHANNEL_ICON_MAP: Record<string, string> = {
  CH01: "🎯",
  CH02: "📚",
  CH03: "💡",
  CH04: "🧭",
  CH05: "💞",
  CH06: "🕯️",
  CH07: "🌿",
  CH08: "🌙",
  CH09: "🏛️",
  CH10: "🧠",
  CH11: "📜",
  CH12: "🪷",
  CH13: "👪",
  CH14: "🧩",
  CH15: "⚖️",
  CH16: "🪶",
  CH17: "🕊️",
  CH18: "🧿",
  CH19: "🔮",
  CH20: "🌊",
  CH21: "🌸",
  CH22: "🧭",
  CH23: "🧵",
  CH24: "🙏",
  CH25: "🧲",
  CH26: "🪄",
};

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function channelIconLabel(channelCode: string): string {
  const code = (channelCode ?? "").trim().toUpperCase();
  const digits = code.replace(/[^0-9]/g, "");
  if (digits) {
    return digits.length > 3 ? digits.slice(-3) : digits;
  }
  return code ? code.slice(0, 3) : "CH";
}

function channelIconColor(channelCode: string): string {
  const hue = hashString((channelCode ?? "").trim().toUpperCase()) % 360;
  return `hsl(${hue} 70% 44%)`;
}

function channelIconText(channelCode: string): string {
  const code = (channelCode ?? "").trim().toUpperCase();
  return CHANNEL_ICON_MAP[code] ?? channelIconLabel(code);
}

function channelSortKey(channelCode: string): number {
  const normalized = (channelCode ?? "").trim().toUpperCase();
  const match = normalized.match(/^CH(\d+)$/);
  if (match) {
    return Number(match[1]);
  }
  return Number.POSITIVE_INFINITY;
}

function sortThumbnailChannels(channels: ThumbnailChannelBlock[]): ThumbnailChannelBlock[] {
  return [...channels].sort((a, b) => {
    const diff = channelSortKey(a.channel) - channelSortKey(b.channel);
    if (diff !== 0) {
      return diff;
    }
    return a.channel.localeCompare(b.channel);
  });
}

function renderPromptTemplate(template: string, context: Record<string, string>): string {
  let rendered = template ?? "";
  Object.entries(context).forEach(([key, value]) => {
    rendered = rendered.split(`{{${key}}}`).join(value ?? "");
  });
  return rendered;
}

function parsePricingNumber(value?: string | null): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatUsdAmount(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  if (value === 0) {
    return "$0";
  }
  const abs = Math.abs(value);
  if (abs < 1e-6) {
    return `$${value.toExponential(2)}`;
  }
  if (abs < 0.0001) {
    return `$${value.toFixed(8)}`;
  }
  if (abs < 0.01) {
    return `$${value.toFixed(6)}`;
  }
  if (abs < 1) {
    return `$${value.toFixed(3)}`;
  }
  return `$${value.toFixed(2)}`;
}

function formatUsdPerMillionTokens(pricePerToken: number): string {
  if (!Number.isFinite(pricePerToken)) {
    return "—";
  }
  const perMillion = pricePerToken * 1_000_000;
  const decimals = perMillion >= 10 ? 0 : perMillion >= 1 ? 2 : 3;
  return `$${perMillion.toFixed(decimals)}/1Mtok`;
}

function formatDate(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatNumber(value?: number | null): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString("ja-JP");
  }
  return "—";
}

function formatPercent(value?: number | null): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value.toFixed(2)}%`;
  }
  return "—";
}

function formatDuration(seconds?: number | null): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return "—";
  }
  const total = Math.round(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatBytes(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const display = unitIndex === 0 ? Math.round(size).toString() : size.toFixed(1);
  return `${display} ${units[unitIndex]}`;
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  if (value < min) {
    return min;
  }
  if (value > max) {
    return max;
  }
  return value;
}

const LAYER_TUNING_BG_DEFAULT_ZOOM = 1.6;
const LAYER_TUNING_BG_MAX_ZOOM = 6.0;
// Pan range: -1..1 stays within "cover"; wider values reveal the base fill (pasteboard-style).
const LAYER_TUNING_BG_PAN_MIN = -5;
const LAYER_TUNING_BG_PAN_MAX = 5;
// Element coordinates are normalized to the canvas (0..1). Keep symmetric range around 0.5.
const LAYER_TUNING_ELEMENT_XY_MIN = -5;
const LAYER_TUNING_ELEMENT_XY_MAX = 6;
// Offsets are normalized to the canvas (0..1). Allow moving out of frame freely.
const LAYER_TUNING_OFFSET_MIN = -5;
const LAYER_TUNING_OFFSET_MAX = 5;

function createLocalId(prefix: string): string {
  const safePrefix = (prefix ?? "").trim() || "id";
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 10);
  return `${safePrefix}_${ts}_${rand}`;
}

function resolveElementSrcUrl(channel: string, video: string, srcPath: string): string | null {
  const raw = String(srcPath ?? "").trim();
  if (!raw) {
    return null;
  }
  const cleaned = raw.replace(/^\/+/, "").replace(/\\/g, "/");
  const parts = cleaned.split("/").filter(Boolean);
  if (!parts.length) {
    return null;
  }
  if (parts.length >= 2 && /^CH\\d+$/.test(parts[0].toUpperCase())) {
    const ch = parts[0].toUpperCase();
    const rest = parts.slice(1).join("/");
    if (rest.startsWith("library/")) {
      return `/thumbnails/library/${encodeURIComponent(ch)}/${rest}`;
    }
    return `/thumbnails/assets/${encodeURIComponent(ch)}/${encodeURIComponent(video)}/${rest}`;
  }
  if (cleaned.startsWith("library/")) {
    return `/thumbnails/library/${encodeURIComponent(channel)}/${cleaned}`;
  }
  return `/thumbnails/assets/${encodeURIComponent(channel)}/${encodeURIComponent(video)}/${cleaned}`;
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

function getProjectKey(project: ThumbnailProject): string {
  return `${project.channel}/${project.video}`;
}

function parseGalleryVariantMode(value: string | null | undefined): GalleryVariantMode | null {
  const v = (value ?? "").trim();
  if (v === "selected" || v === "all" || v === "two_up" || v === "three_up") {
    return v;
  }
  return null;
}

function resolveSelectedVariant(project: ThumbnailProject): ThumbnailVariant | null {
  const selected =
    project.variants.find((variant) => variant.is_selected) ??
    (project.selected_variant_id
      ? project.variants.find((variant) => variant.id === project.selected_variant_id)
      : undefined) ??
    project.variants[0];
  return selected ?? null;
}

function hasThumbFileSuffix(value: string | null | undefined, fileName: string): boolean {
  if (!value) {
    return false;
  }
  const clean = String(value).split("?")[0].split("#")[0];
  return clean === fileName || clean.endsWith(`/${fileName}`);
}

function findVariantByThumbFile(project: ThumbnailProject, fileName: string): ThumbnailVariant | null {
  return (
    project.variants.find(
      (variant) => hasThumbFileSuffix(variant.image_path, fileName) || hasThumbFileSuffix(variant.image_url, fileName)
    ) ?? null
  );
}

function isSupportedThumbnailFile(file: File): boolean {
  if (file.type && file.type.startsWith("image/")) {
    return true;
  }
  return SUPPORTED_THUMBNAIL_EXTENSIONS.test(file.name);
}

type ThumbnailWorkspaceProps = {
  compact?: boolean;
  channelSummaries?: ChannelSummary[] | null;
};

export function ThumbnailWorkspace({ compact = false, channelSummaries }: ThumbnailWorkspaceProps = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const channelSummaryMap = useMemo(() => {
    const map = new Map<string, ChannelSummary>();
    (channelSummaries ?? []).forEach((summary) => map.set(summary.code, summary));
    return map;
  }, [channelSummaries]);
  const [overview, setOverview] = useState<ThumbnailOverview | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(() => {
    const params = new URLSearchParams(location.search);
    const fromQuery = (params.get("channel") ?? "").trim().toUpperCase();
    if (fromQuery) {
      return fromQuery;
    }
    const stored = (safeLocalStorage.getItem("ui.channel.selected") ?? "").trim().toUpperCase();
    return stored || null;
  });
  const [activeTab, setActiveTab] = useState<ThumbnailWorkspaceTab>("gallery");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [galleryLimit, setGalleryLimit] = useState<number>(DEFAULT_GALLERY_LIMIT);
  const [galleryHideMissingEnabled, setGalleryHideMissingEnabled] = useState<boolean>(() => {
    const stored = (safeLocalStorage.getItem("ui.thumbnails.gallery.hide_missing") ?? "").trim();
    if (!stored) {
      return true;
    }
    return stored === "1";
  });
  const [galleryImageErrors, setGalleryImageErrors] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [updatingProjectId, setUpdatingProjectId] = useState<string | null>(null);
  const [galleryProjectSaving, setGalleryProjectSaving] = useState<Record<string, boolean>>({});
  const [galleryNotesDraft, setGalleryNotesDraft] = useState<Record<string, string>>({});
  const [variantForm, setVariantForm] = useState<VariantFormState | null>(null);
  const [projectForm, setProjectForm] = useState<ProjectFormState | null>(null);
  const [planningDialog, setPlanningDialog] = useState<PlanningDialogState | null>(null);
  const [cardFeedback, setCardFeedback] = useState<Record<string, CardFeedback>>({});
  const [libraryAssets, setLibraryAssets] = useState<ThumbnailLibraryAsset[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [qcNotes, setQcNotes] = useState<ThumbnailQcNotes>({});
  const [qcNotesDraft, setQcNotesDraft] = useState<Record<string, string>>({});
  const [qcNotesSaving, setQcNotesSaving] = useState<Record<string, boolean>>({});
  const [qcNotesError, setQcNotesError] = useState<string | null>(null);
  const [libraryForms, setLibraryForms] = useState<Record<string, LibraryFormState>>({});
  const libraryUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [libraryUploadStatus, setLibraryUploadStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const [libraryImportUrl, setLibraryImportUrl] = useState("");
  const [libraryImportName, setLibraryImportName] = useState("");
  const [libraryImportStatus, setLibraryImportStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const feedbackTimers = useRef<Map<string, number>>(new Map());
  const dropzoneFileInputs = useRef(new Map<string, HTMLInputElement>());
  const [activeDropProject, setActiveDropProject] = useState<string | null>(null);
  const [expandedProjectKey, setExpandedProjectKey] = useState<string | null>(null);
  const [galleryVariantMode, setGalleryVariantMode] = useState<GalleryVariantMode>("selected");
  const libraryRequestRef = useRef(0);
  const qcNotesRequestRef = useRef(0);
  const layerTuningRequestRef = useRef(0);
  const [imageModels, setImageModels] = useState<ThumbnailImageModelInfo[]>([]);
  const [imageModelsError, setImageModelsError] = useState<string | null>(null);
  const [channelTemplates, setChannelTemplates] = useState<ThumbnailChannelTemplates | null>(null);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesDirty, setTemplatesDirty] = useState(false);
  const [templatesStatus, setTemplatesStatus] = useState<{
    pending: boolean;
    error: string | null;
    success: string | null;
  }>({ pending: false, error: null, success: null });
  const [generateDialog, setGenerateDialog] = useState<GenerateDialogState | null>(null);
  const [galleryCopyEdit, setGalleryCopyEdit] = useState<GalleryCopyEditState | null>(null);
  const [layerTuningDialog, setLayerTuningDialog] = useState<LayerTuningDialogState | null>(null);
  const layerTuningPreviewRef = useRef<HTMLDivElement | null>(null);
  const layerTuningPreviewDragRef = useRef<LayerTuningPreviewDragState | null>(null);
  const layerTuningPreviewRafRef = useRef<number | null>(null);
  const layerTuningPreviewPendingPatchRef = useRef<Record<string, unknown> | null>(null);
  const layerTuningPreviewDropDepthRef = useRef(0);
  const [layerTuningPreviewDropActive, setLayerTuningPreviewDropActive] = useState(false);
  const [layerTuningSelectedAsset, setLayerTuningSelectedAsset] = useState<"bg" | "portrait" | "text" | "element">(
    "bg"
  );
  const layerTuningDialogRef = useRef<LayerTuningDialogState | null>(null);
  const layerTuningSelectedAssetRef = useRef<"bg" | "portrait" | "text" | "element">("bg");
  const [layerTuningPreviewSize, setLayerTuningPreviewSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  const [layerTuningBgPreviewSrc, setLayerTuningBgPreviewSrc] = useState<string | null>(null);
  const [layerTuningPortraitPreviewSrc, setLayerTuningPortraitPreviewSrc] = useState<string | null>(null);
  const [layerTuningTextSlotImages, setLayerTuningTextSlotImages] = useState<Record<string, string>>({});
  const [layerTuningTextSlotStatus, setLayerTuningTextSlotStatus] = useState<{
    loading: boolean;
    error: string | null;
  }>({ loading: false, error: null });
  const layerTuningTextSlotRequestRef = useRef(0);
  const [layerTuningTextLineSpecLines, setLayerTuningTextLineSpecLines] = useState<
    Record<string, { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }>
  >({});
  const layerTuningTextLineSpecRef = useRef<
    Record<string, { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }>
  >({});
  const layerTuningTextSlotBoxesRef = useRef<Record<string, number[]>>({});
  const [layerTuningTextLineSpecStatus, setLayerTuningTextLineSpecStatus] = useState<{
    loading: boolean;
    error: string | null;
  }>({ loading: false, error: null });
  const layerTuningTextLineSpecRequestRef = useRef(0);
  const layerTuningTextLegacyMigrationRef = useRef<Record<string, boolean>>({});
  const [layerTuningSelectedTextSlot, setLayerTuningSelectedTextSlot] = useState<string | null>(null);
  const layerTuningSelectedTextSlotRef = useRef<string | null>(null);
  const [layerTuningElements, setLayerTuningElements] = useState<ThumbnailElementSpec[]>([]);
  const layerTuningElementsRef = useRef<ThumbnailElementSpec[]>([]);
  const [layerTuningElementsStatus, setLayerTuningElementsStatus] = useState<{ loading: boolean; error: string | null }>(
    { loading: false, error: null }
  );
  const layerTuningElementsRequestRef = useRef(0);
  const [layerTuningSelectedElementId, setLayerTuningSelectedElementId] = useState<string | null>(null);
  const layerTuningSelectedElementIdRef = useRef<string | null>(null);
  const layerTuningElementUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [layerTuningSnapEnabled, setLayerTuningSnapEnabled] = useState<boolean>(true);
  const layerTuningSnapEnabledRef = useRef<boolean>(true);
  const [layerTuningGuidesEnabled, setLayerTuningGuidesEnabled] = useState<boolean>(false);
  const [layerTuningHoveredTextSlot, setLayerTuningHoveredTextSlot] = useState<string | null>(null);
  const [planningRowsByVideo, setPlanningRowsByVideo] = useState<Record<string, Record<string, string>>>({});
  const [planningLoading, setPlanningLoading] = useState(false);
  const [planningError, setPlanningError] = useState<string | null>(null);
  const [channelAvatarErrors, setChannelAvatarErrors] = useState<Record<string, boolean>>({});
  const [channelPickerOpen, setChannelPickerOpen] = useState(true);
  const [channelPickerQuery, setChannelPickerQuery] = useState("");
  const channelPickerButtonRef = useRef<HTMLButtonElement | null>(null);
  const channelPickerPanelRef = useRef<HTMLDivElement | null>(null);
  const autoOpenLayerTuningRef = useRef<string | null>(null);

  const setLayerTuningTextLineSpecLinesImmediate = useCallback(
    (updater: React.SetStateAction<Record<string, { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }>>) => {
      setLayerTuningTextLineSpecLines((current) => {
        const resolved = typeof updater === "function" ? (updater as any)(current) : updater;
        const next =
          resolved && typeof resolved === "object"
            ? (resolved as Record<string, { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }>)
            : {};
        layerTuningTextLineSpecRef.current = next;
        return next;
      });
    },
    []
  );

  const setLayerTuningElementsImmediate = useCallback((updater: React.SetStateAction<ThumbnailElementSpec[]>) => {
    setLayerTuningElements((current) => {
      const base = current ?? [];
      const resolved = typeof updater === "function" ? (updater as any)(base) : updater;
      const next = Array.isArray(resolved) ? (resolved as ThumbnailElementSpec[]) : [];
      layerTuningElementsRef.current = next;
      return next;
    });
  }, []);

  const selectChannel = useCallback(
    (channelCode: string) => {
      const normalized = channelCode.trim().toUpperCase();
      setSelectedChannel(normalized);

      const params = new URLSearchParams(location.search);
      if (normalized) {
        params.set("channel", normalized);
      } else {
        params.delete("channel");
      }
      const query = params.toString();
      const nextUrl = `${location.pathname}${query ? `?${query}` : ""}`;
      const currentUrl = `${location.pathname}${location.search}`;
      if (nextUrl !== currentUrl) {
        navigate(nextUrl, { replace: true });
      }
    },
    [location.pathname, location.search, navigate]
  );

  const activeChannel: ThumbnailChannelBlock | undefined = useMemo(() => {
    if (!overview || overview.channels.length === 0) {
      return undefined;
    }
    const firstChannel = overview.channels[0];
    if (!selectedChannel) {
      return firstChannel;
    }
    return overview.channels.find((item) => item.channel === selectedChannel) ?? firstChannel;
  }, [overview, selectedChannel]);

  const summary = activeChannel?.summary;
  const activeChannelName = activeChannel?.channel_title ?? activeChannel?.channel ?? null;
  const channelVideos = activeChannel?.videos ?? [];
  const channelPickerChannels: ThumbnailChannelBlock[] = useMemo(() => {
    if (!overview) {
      return [];
    }
    const query = channelPickerQuery.trim().toLowerCase();
    if (!query) {
      return overview.channels;
    }
    return overview.channels.filter((channel) => {
      const title = (channel.channel_title ?? "").trim();
      const channelInfo = channelSummaryMap.get(channel.channel);
      const fallbackTitle = (
        channelInfo?.name ??
        channelInfo?.branding?.title ??
        channelInfo?.youtube_title ??
        ""
      ).trim();
      const resolvedTitle = title || fallbackTitle;
      const hay = `${channel.channel} ${resolvedTitle}`.toLowerCase();
      return hay.includes(query);
    });
  }, [channelPickerQuery, channelSummaryMap, overview]);

  useEffect(() => {
    if (!channelPickerOpen) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      event.preventDefault();
      setChannelPickerOpen(false);
      channelPickerButtonRef.current?.focus();
    };

    const onPointerDown = (event: MouseEvent | PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (channelPickerPanelRef.current?.contains(target)) {
        return;
      }
      if (channelPickerButtonRef.current?.contains(target)) {
        return;
      }
      setChannelPickerOpen(false);
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [channelPickerOpen]);
  const galleryProjects = useMemo(() => {
    if (!activeChannel) {
      return [];
    }
    const projects = [...activeChannel.projects];
    projects.sort((a, b) => {
      const aVideo = Number(normalizeVideoInput(a.video));
      const bVideo = Number(normalizeVideoInput(b.video));
      if (Number.isFinite(aVideo) && Number.isFinite(bVideo) && aVideo !== bVideo) {
        return bVideo - aVideo; // desc
      }
      return (b.video ?? "").localeCompare(a.video ?? "");
    });

    const query = searchTerm.trim().toLowerCase();
    if (!query) {
      return projects;
    }
    return projects.filter((project) => {
      const hay = `${project.video} ${project.title ?? ""} ${project.sheet_title ?? ""}`.toLowerCase();
      return hay.includes(query);
    });
  }, [activeChannel, searchTerm]);

  const channelHasTwoUpVariants = useMemo(() => {
    if (!activeChannel) {
      return false;
    }
    return activeChannel.projects.some((project) =>
      (project.variants ?? []).some((variant) => {
        return (
          hasThumbFileSuffix(variant.image_path, "00_thumb_1.png") ||
          hasThumbFileSuffix(variant.image_url, "00_thumb_1.png") ||
          hasThumbFileSuffix(variant.image_path, "00_thumb_2.png") ||
          hasThumbFileSuffix(variant.image_url, "00_thumb_2.png")
        );
      })
    );
  }, [activeChannel]);

  const channelHasThreeUpVariants = useMemo(() => {
    if (!activeChannel) {
      return false;
    }
    return activeChannel.projects.some((project) =>
      (project.variants ?? []).some((variant) => {
        return (
          hasThumbFileSuffix(variant.image_path, "00_thumb_3.png") ||
          hasThumbFileSuffix(variant.image_url, "00_thumb_3.png")
        );
      })
    );
  }, [activeChannel]);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }

    const storageKey = `ui.thumbnails.gallery_variant_mode.${channelCode}`;
    const stored = parseGalleryVariantMode(safeLocalStorage.getItem(storageKey));
    if (stored) {
      // Prefer fixed-slot views for channels that ship multiple stable variants.
      const resolved = (() => {
        if (stored !== "selected") {
          return stored;
        }
        if (channelHasThreeUpVariants) {
          return "three_up";
        }
        if (channelHasTwoUpVariants) {
          return "two_up";
        }
        return stored;
      })();
      setGalleryVariantMode((current) => (current === resolved ? current : resolved));
      return;
    }
    setGalleryVariantMode(channelHasThreeUpVariants ? "three_up" : channelHasTwoUpVariants ? "two_up" : "selected");
  }, [activeChannel?.channel, channelHasThreeUpVariants, channelHasTwoUpVariants]);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    const storageKey = `ui.thumbnails.gallery_variant_mode.${channelCode}`;
    safeLocalStorage.setItem(storageKey, galleryVariantMode);
  }, [activeChannel?.channel, galleryVariantMode]);

  useEffect(() => {
    safeLocalStorage.setItem("ui.thumbnails.gallery.hide_missing", galleryHideMissingEnabled ? "1" : "0");
  }, [galleryHideMissingEnabled]);

  useEffect(() => {
    if (galleryVariantMode !== "two_up" && galleryVariantMode !== "three_up") {
      return;
    }
    const projectCount = activeChannel?.projects.length ?? 0;
    if (!projectCount) {
      return;
    }
    const perProject = galleryVariantMode === "three_up" ? 3 : 2;
    const target = Math.max(DEFAULT_GALLERY_LIMIT, projectCount * perProject);
    setGalleryLimit((prev) => (prev >= target ? prev : target));
  }, [activeChannel?.channel, activeChannel?.projects.length, galleryVariantMode]);

  const isTwoUpMode = galleryVariantMode === "two_up";
  const isThreeUpMode = galleryVariantMode === "three_up";

  const galleryItems: ThumbnailGalleryItem[] = useMemo(() => {
    if (!activeChannel) {
      return [];
    }

    const items: ThumbnailGalleryItem[] = [];
    for (const project of galleryProjects) {
      const projectKey = getProjectKey(project);
      if (galleryVariantMode === "all") {
        const variants = Array.isArray(project.variants) ? project.variants : [];
        if (variants.length === 0) {
          items.push({ key: `${projectKey}#empty`, project, variant: null });
          continue;
        }
        for (const variant of variants) {
          items.push({ key: `${projectKey}#${variant.id}`, project, variant });
        }
        continue;
      }
      if (galleryVariantMode === "three_up") {
        items.push({
          key: `${projectKey}#thumb_1`,
          project,
          variant: findVariantByThumbFile(project, "00_thumb_1.png"),
          slotLabel: "00_thumb_1",
        });
        items.push({
          key: `${projectKey}#thumb_2`,
          project,
          variant: findVariantByThumbFile(project, "00_thumb_2.png"),
          slotLabel: "00_thumb_2",
        });
        items.push({
          key: `${projectKey}#thumb_3`,
          project,
          variant: findVariantByThumbFile(project, "00_thumb_3.png"),
          slotLabel: "00_thumb_3",
        });
        continue;
      }
      if (galleryVariantMode === "two_up") {
        items.push({
          key: `${projectKey}#thumb_1`,
          project,
          variant: findVariantByThumbFile(project, "00_thumb_1.png"),
          slotLabel: "00_thumb_1",
        });
        items.push({
          key: `${projectKey}#thumb_2`,
          project,
          variant: findVariantByThumbFile(project, "00_thumb_2.png"),
          slotLabel: "00_thumb_2",
        });
        continue;
      }
      items.push({ key: `${projectKey}#selected`, project, variant: resolveSelectedVariant(project) });
    }
    return items;
  }, [activeChannel, galleryProjects, galleryVariantMode]);

  const visibleGalleryItems = useMemo(() => {
    if (!galleryHideMissingEnabled) {
      return galleryItems;
    }
    return galleryItems.filter((item) => {
      const variant = item.variant;
      if (!variant) {
        return false;
      }
      if (!variant.preview_url && !variant.image_url && !variant.image_path) {
        return false;
      }
      return !galleryImageErrors[item.key];
    });
  }, [galleryHideMissingEnabled, galleryImageErrors, galleryItems]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const channelParam = (params.get("channel") ?? "").trim().toUpperCase();
    if (channelParam) {
      if (selectedChannel !== channelParam) {
        setSelectedChannel(channelParam);
      }
      return;
    }
  }, [location.search, selectedChannel]);

  useEffect(() => {
    if (!selectedChannel) {
      return;
    }
    const normalized = selectedChannel.trim().toUpperCase();
    if (normalized !== selectedChannel) {
      setSelectedChannel(normalized);
      return;
    }
    // Persist last-used channel without forcing URL rewrites (avoids navigation loops).
    safeLocalStorage.setItem("ui.channel.selected", normalized);
  }, [selectedChannel]);

  useEffect(() => {
    setGalleryProjectSaving({});
    setGalleryNotesDraft({});
    setGalleryImageErrors({});
    setExpandedProjectKey(null);
    const projectCount = activeChannel?.projects.length ?? 0;
    const nextLimit = channelHasTwoUpVariants ? Math.max(DEFAULT_GALLERY_LIMIT, projectCount * 2) : DEFAULT_GALLERY_LIMIT;
    setGalleryLimit(nextLimit);
    setQcNotes({});
    setQcNotesDraft({});
    setQcNotesSaving({});
    setQcNotesError(null);
  }, [activeChannel?.channel, activeChannel?.projects.length, channelHasTwoUpVariants]);

  const loadPlanning = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      if (!silent) {
        setPlanningLoading(true);
        setPlanningError(null);
      }
      try {
        const result = await fetchPlanningChannelCsv(channelCode);
        const map: Record<string, Record<string, string>> = {};
        (result.rows ?? []).forEach((row) => {
          const rawVideo = row["動画番号"] ?? row["VideoNumber"] ?? "";
          const normalizedVideo = normalizeVideoInput(rawVideo);
          if (!normalizedVideo) {
            return;
          }
          map[normalizedVideo] = row;
        });
        setPlanningRowsByVideo(map);
        return map;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setPlanningError(message);
        setPlanningRowsByVideo({});
        throw error;
      } finally {
        if (!silent) {
          setPlanningLoading(false);
        }
      }
    },
    []
  );

  const handleRefreshPlanning = useCallback(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    loadPlanning(channelCode).catch(() => {
      // error is shown in planningError
    });
  }, [activeChannel?.channel, loadPlanning]);

  const handleUpdateLocalPlanningRow = useCallback((video: string, patch: Partial<Record<string, string>>) => {
    setPlanningRowsByVideo((current) => {
      const existing = current[video];
      const nextRow: Record<string, string> = { ...(existing ?? {}) } as Record<string, string>;

      Object.entries(patch).forEach(([key, value]) => {
        if (value === undefined) {
          return;
        }
        nextRow[key] = value;
      });
      return {
        ...current,
        [video]: {
          ...nextRow,
        },
      };
    });
  }, []);

  const patchProjectInOverview = useCallback(
    (channelCode: string, video: string, patch: Partial<ThumbnailProject>) => {
      setOverview((current) => {
        if (!current) {
          return current;
        }
        const nextChannels = current.channels.map((channel) => {
          if (channel.channel !== channelCode) {
            return channel;
          }
          const nextProjects = channel.projects.map((project) => {
            if (project.video !== video) {
              return project;
            }
            return { ...project, ...patch };
          });
          return { ...channel, projects: nextProjects };
        });
        return { ...current, channels: nextChannels };
      });
    },
    []
  );

  const patchVariantInOverview = useCallback(
    (channelCode: string, video: string, variantId: string, patch: Partial<ThumbnailVariant>) => {
      if (!variantId) {
        return;
      }
      setOverview((current) => {
        if (!current) {
          return current;
        }
        const nextChannels = current.channels.map((channel) => {
          if (channel.channel !== channelCode) {
            return channel;
          }
          const nextProjects = channel.projects.map((project) => {
            if (project.video !== video) {
              return project;
            }
            const variants = Array.isArray(project.variants) ? project.variants : [];
            const nextVariants = variants.map((variant) => {
              if (variant.id !== variantId) {
                return variant;
              }
              return { ...variant, ...patch };
            });
            return { ...project, variants: nextVariants };
          });
          return { ...channel, projects: nextProjects };
        });
        return { ...current, channels: nextChannels };
      });
    },
    []
  );

  const handleCopyAssetPath = useCallback((path: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).catch(() => {
        // no-op fallback below
      });
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = path;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);
    }
  }, []);

  const setProjectFeedback = useCallback((projectKey: string, feedback: CardFeedback | null) => {
    setCardFeedback((current) => {
      const next = { ...current };
      if (!feedback) {
        if (feedbackTimers.current.has(projectKey)) {
          window.clearTimeout(feedbackTimers.current.get(projectKey));
          feedbackTimers.current.delete(projectKey);
        }
        delete next[projectKey];
        return next;
      }
      next[projectKey] = feedback;
      if (feedbackTimers.current.has(projectKey)) {
        window.clearTimeout(feedbackTimers.current.get(projectKey));
      }
      const timeoutId = window.setTimeout(() => {
        setCardFeedback((latest) => {
          if (!latest[projectKey]) {
            return latest;
          }
          const copy = { ...latest };
          delete copy[projectKey];
          return copy;
        });
        feedbackTimers.current.delete(projectKey);
      }, feedback.type === "success" ? 2800 : 4800);
      feedbackTimers.current.set(projectKey, timeoutId);
      return next;
    });
  }, []);

  const handleGalleryProjectStatusChange = useCallback(
    async (project: ThumbnailProject, cardKey: string, status: ThumbnailProjectStatus) => {
      const draftNotes = galleryNotesDraft[cardKey];
      const currentNotes = project.notes ?? "";
      const notesDirty = draftNotes !== undefined && draftNotes !== currentNotes;
      const trimmedNotes = notesDirty ? draftNotes.trim() : "";
      const notesPayload = notesDirty ? (trimmedNotes ? trimmedNotes : null) : undefined;
      setGalleryProjectSaving((current) => ({ ...current, [cardKey]: true }));
      setProjectFeedback(cardKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, {
          status,
          ...(notesPayload !== undefined ? { notes: notesPayload } : {}),
        });
        patchProjectInOverview(project.channel, project.video, {
          status,
          ...(notesPayload !== undefined ? { notes: notesPayload } : {}),
        });
        if (notesPayload !== undefined) {
          setGalleryNotesDraft((current) => {
            const next = { ...current };
            delete next[cardKey];
            return next;
          });
        }
        setProjectFeedback(cardKey, {
          type: "success",
          message: notesPayload !== undefined ? "ステータスとコメントを保存しました。" : "保存しました。",
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setGalleryProjectSaving((current) => ({ ...current, [cardKey]: false }));
      }
    },
    [galleryNotesDraft, patchProjectInOverview, setProjectFeedback]
  );

  const handleGalleryVariantStatusChange = useCallback(
    async (project: ThumbnailProject, variant: ThumbnailVariant, cardKey: string, status: ThumbnailVariantStatus) => {
      const draftNotes = galleryNotesDraft[cardKey];
      const currentNotes = variant.notes ?? "";
      const notesDirty = draftNotes !== undefined && draftNotes !== currentNotes;
      const trimmedNotes = notesDirty ? draftNotes.trim() : "";
      const notesPayload = notesDirty ? (trimmedNotes ? trimmedNotes : null) : undefined;
      setGalleryProjectSaving((current) => ({ ...current, [cardKey]: true }));
      setProjectFeedback(cardKey, null);
      try {
        const updated = await patchThumbnailVariant(project.channel, project.video, variant.id, {
          status,
          ...(notesPayload !== undefined ? { notes: notesPayload } : {}),
        });
        patchVariantInOverview(project.channel, project.video, variant.id, updated);
        if (notesPayload !== undefined) {
          setGalleryNotesDraft((current) => {
            const next = { ...current };
            delete next[cardKey];
            return next;
          });
        }
        setProjectFeedback(cardKey, {
          type: "success",
          message: notesPayload !== undefined ? "ステータスとコメントを保存しました。" : "保存しました。",
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setGalleryProjectSaving((current) => ({ ...current, [cardKey]: false }));
      }
    },
    [galleryNotesDraft, patchVariantInOverview, setProjectFeedback]
  );

  const handleGalleryVariantRejectedChange = useCallback(
    async (project: ThumbnailProject, variant: ThumbnailVariant, cardKey: string, rejected: boolean) => {
      const currentTags = Array.isArray(variant.tags) ? variant.tags : [];
      const cleaned = currentTags
        .map((tag) => String(tag ?? "").trim())
        .filter(Boolean)
        .filter((tag) => tag.toLowerCase() !== VARIANT_REJECT_TAG);
      const nextTags = rejected ? [VARIANT_REJECT_TAG, ...cleaned] : cleaned;
      const seen = new Set<string>();
      const unique: string[] = [];
      nextTags.forEach((tag) => {
        const key = tag.toLowerCase();
        if (seen.has(key)) {
          return;
        }
        seen.add(key);
        unique.push(tag);
      });
      const tagsPayload = unique.length ? unique : null;

      setGalleryProjectSaving((current) => ({ ...current, [cardKey]: true }));
      setProjectFeedback(cardKey, null);
      try {
        const updated = await patchThumbnailVariant(project.channel, project.video, variant.id, { tags: tagsPayload });
        patchVariantInOverview(project.channel, project.video, variant.id, updated);
        setProjectFeedback(cardKey, {
          type: "success",
          message: rejected ? "ボツにしました。" : "ボツを解除しました。",
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setGalleryProjectSaving((current) => ({ ...current, [cardKey]: false }));
      }
    },
    [patchVariantInOverview, setProjectFeedback]
  );

  const handleGalleryNotesChange = useCallback((cardKey: string, value: string) => {
    setGalleryNotesDraft((current) => ({
      ...current,
      [cardKey]: value,
    }));
  }, []);

  const handleGalleryNotesSave = useCallback(
    async (project: ThumbnailProject, cardKey: string) => {
      const draft = galleryNotesDraft[cardKey];
      const currentNotes = project.notes ?? "";
      if (draft === undefined || draft === currentNotes) {
        return;
      }
      setGalleryProjectSaving((current) => ({ ...current, [cardKey]: true }));
      setProjectFeedback(cardKey, null);
      const trimmed = draft.trim();
      try {
        await updateThumbnailProject(project.channel, project.video, {
          notes: trimmed ? trimmed : null,
        });
        patchProjectInOverview(project.channel, project.video, { notes: trimmed ? trimmed : null });
        setGalleryNotesDraft((current) => {
          const next = { ...current };
          delete next[cardKey];
          return next;
        });
        setProjectFeedback(cardKey, {
          type: "success",
          message: "コメントを保存しました。",
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setGalleryProjectSaving((current) => ({ ...current, [cardKey]: false }));
      }
    },
    [galleryNotesDraft, patchProjectInOverview, setProjectFeedback]
  );

  const handleGalleryVariantNotesSave = useCallback(
    async (project: ThumbnailProject, variant: ThumbnailVariant, cardKey: string) => {
      const draft = galleryNotesDraft[cardKey];
      const currentNotes = variant.notes ?? "";
      if (draft === undefined || draft === currentNotes) {
        return;
      }
      setGalleryProjectSaving((current) => ({ ...current, [cardKey]: true }));
      setProjectFeedback(cardKey, null);
      const trimmed = draft.trim();
      try {
        const updated = await patchThumbnailVariant(project.channel, project.video, variant.id, {
          notes: trimmed ? trimmed : null,
        });
        patchVariantInOverview(project.channel, project.video, variant.id, updated);
        setGalleryNotesDraft((current) => {
          const next = { ...current };
          delete next[cardKey];
          return next;
        });
        setProjectFeedback(cardKey, {
          type: "success",
          message: "コメントを保存しました。",
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setGalleryProjectSaving((current) => ({ ...current, [cardKey]: false }));
      }
    },
    [galleryNotesDraft, patchVariantInOverview, setProjectFeedback]
  );

  const handleQcNotesChange = useCallback((relativePath: string, value: string) => {
    setQcNotesDraft((current) => ({
      ...current,
      [relativePath]: value,
    }));
  }, []);

  const handleQcNotesSave = useCallback(
    async (relativePath: string) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const draft = qcNotesDraft[relativePath];
      const currentNote = qcNotes[relativePath] ?? "";
      if (draft === undefined || draft === currentNote) {
        return;
      }
      const trimmed = draft.trim();
      setQcNotesSaving((current) => ({ ...current, [relativePath]: true }));
      setQcNotesError(null);
      try {
        const next = await updateThumbnailQcNote(channelCode, {
          relative_path: relativePath,
          note: trimmed ? trimmed : null,
        });
        setQcNotes(next);
        setQcNotesDraft((current) => {
          const copy = { ...current };
          delete copy[relativePath];
          return copy;
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setQcNotesError(message);
      } finally {
        setQcNotesSaving((current) => ({ ...current, [relativePath]: false }));
      }
    },
    [activeChannel?.channel, qcNotes, qcNotesDraft]
  );

  useEffect(() => {
    const timers = feedbackTimers.current;
    return () => {
      timers.forEach((timerId) => window.clearTimeout(timerId));
      timers.clear();
    };
  }, []);

  const fetchData = useCallback(
    async (options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      if (!silent) {
        setLoading(true);
        setErrorMessage(null);
      }
      try {
        const data = await fetchThumbnailOverview();
        const sortedChannels = sortThumbnailChannels(data.channels ?? []);
        const sortedOverview = { ...data, channels: sortedChannels };
        setOverview(sortedOverview);
        setSelectedChannel((prev) => {
          if (!sortedChannels.length) {
            return null;
          }
          if (prev && sortedChannels.some((channel) => channel.channel === prev)) {
            return prev;
          }
          return sortedChannels[0].channel;
        });
        return sortedOverview;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent) {
          setErrorMessage(message);
        }
        throw error;
      } finally {
        if (!silent) {
          setLoading(false);
        }
      }
    },
    []
  );

  const loadLibrary = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      const requestId = Date.now();
      libraryRequestRef.current = requestId;
      if (!silent) {
        setLibraryLoading(true);
        setLibraryError(null);
      }
      try {
        const assets = await fetchThumbnailLibrary(channelCode);
        if (libraryRequestRef.current !== requestId) {
          return assets;
        }
        setLibraryAssets(assets);
        setLibraryForms((current) => {
          const next: Record<string, LibraryFormState> = {};
          assets.forEach((asset) => {
            const existing = current[asset.id];
            next[asset.id] = {
              video: existing?.video ?? "",
              pending: false,
            };
          });
          return next;
        });
        return assets;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent && libraryRequestRef.current === requestId) {
          setLibraryError(message);
        }
        throw error;
      } finally {
        if (!silent && libraryRequestRef.current === requestId) {
          setLibraryLoading(false);
        }
      }
    },
    []
  );

  const loadQcNotes = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      const requestId = Date.now();
      qcNotesRequestRef.current = requestId;
      if (!silent) {
        setQcNotesError(null);
      }
      try {
        const notes = await fetchThumbnailQcNotes(channelCode);
        if (qcNotesRequestRef.current !== requestId) {
          return notes;
        }
        setQcNotes(notes);
        return notes;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (qcNotesRequestRef.current === requestId) {
          setQcNotes({});
          setQcNotesError(message);
        }
        throw error;
      }
    },
    []
  );

  const loadTemplates = useCallback(
    async (channelCode: string, options?: { silent?: boolean }) => {
      const silent = options?.silent ?? false;
      if (!silent) {
        setTemplatesLoading(true);
        setTemplatesStatus({ pending: false, error: null, success: null });
      }
      try {
        const templates = await fetchThumbnailTemplates(channelCode);
        setChannelTemplates(templates);
        setTemplatesDirty(false);
        return templates;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!silent) {
          setTemplatesStatus({ pending: false, error: message, success: null });
        }
        throw error;
      } finally {
        if (!silent) {
          setTemplatesLoading(false);
        }
      }
    },
    []
  );

  useEffect(() => {
    if (activeTab !== "library" && activeTab !== "qc") {
      return;
    }
    if (!activeChannel?.channel) {
      setLibraryAssets([]);
      setLibraryForms({});
      setLibraryError(null);
      setLibraryLoading(false);
      return;
    }
    loadLibrary(activeChannel.channel).catch(() => {
      // loadLibrary 内でエラー表示済み
    });
  }, [activeChannel?.channel, activeTab, loadLibrary]);

  useEffect(() => {
    if (activeTab !== "qc") {
      return;
    }
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      setQcNotes({});
      setQcNotesError(null);
      return;
    }
    loadQcNotes(channelCode).catch(() => {
      // loadQcNotes 内でエラー表示済み
    });
  }, [activeChannel?.channel, activeTab, loadQcNotes]);

  useEffect(() => {
    fetchData().catch(() => {
      // エラーは fetchData 内で処理済み
    });
  }, [fetchData]);

  useEffect(() => {
    fetchThumbnailImageModels()
      .then((models) => {
        setImageModels(models);
        setImageModelsError(null);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        setImageModelsError(message);
      });
  }, []);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      setChannelTemplates(null);
      setTemplatesDirty(false);
      setTemplatesLoading(false);
      setTemplatesStatus({ pending: false, error: null, success: null });
      return;
    }
    if (activeTab !== "templates" && activeTab !== "projects" && activeTab !== "bulk") {
      return;
    }
    loadTemplates(channelCode).catch(() => {
      // loadTemplates 内でエラー表示済み
    });
  }, [activeChannel?.channel, activeTab, loadTemplates]);

  useEffect(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      setPlanningRowsByVideo({});
      setPlanningLoading(false);
      setPlanningError(null);
      return;
    }
    if (activeTab === "templates" || activeTab === "library" || activeTab === "qc" || activeTab === "channel") {
      return;
    }
    loadPlanning(channelCode).catch(() => {
      // error is shown in planningError
    });
  }, [activeChannel?.channel, activeTab, loadPlanning]);

  const bulkPanel = activeChannel ? (
    <ThumbnailBulkPanel
      channel={activeChannel.channel}
      channelName={activeChannel.channel_title}
      channelTemplates={channelTemplates}
      planningRowsByVideo={planningRowsByVideo}
      planningLoading={planningLoading}
      planningError={planningError}
      onRefreshPlanning={handleRefreshPlanning}
      onUpdateLocalPlanningRow={handleUpdateLocalPlanningRow}
      onRefreshWorkspace={async () => {
        await fetchData({ silent: true });
      }}
    />
  ) : (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>量産（Canva）</h3>
          <p>チャンネルを選択すると企画CSVからコピー一覧を読み込みます。</p>
        </div>
      </div>
    </section>
  );

  const handleOpenGalleryCopyEdit = useCallback(
    (project: ThumbnailProject) => {
      const normalizedVideo = normalizeVideoInput(project.video) || project.video;
      const row = planningRowsByVideo[normalizedVideo] ?? {};
      setGalleryCopyEdit({
        projectKey: getProjectKey(project),
        channel: project.channel,
        video: normalizedVideo,
        projectTitle: project.title ?? "（タイトル未設定）",
        copyUpper: row["サムネタイトル上"] ?? "",
        copyTitle: row["サムネタイトル"] ?? "",
        copyLower: row["サムネタイトル下"] ?? "",
        saving: false,
        error: undefined,
      });
    },
    [planningRowsByVideo]
  );

  const handleCloseGalleryCopyEdit = useCallback(() => {
    setGalleryCopyEdit(null);
  }, []);

  const handleGalleryCopyEditFieldChange = useCallback((field: "copyUpper" | "copyTitle" | "copyLower", value: string) => {
    setGalleryCopyEdit((current) => {
      if (!current) {
        return current;
      }
      return { ...current, [field]: value, error: undefined };
    });
  }, []);

  const handleGalleryCopyEditSubmit = useCallback(
    async (mode: "save" | "save_and_compose") => {
      if (!galleryCopyEdit) {
        return;
      }
      const upper = (galleryCopyEdit.copyUpper ?? "").replace(/\s+/g, " ").trim();
      const title = (galleryCopyEdit.copyTitle ?? "").replace(/\s+/g, " ").trim();
      const lower = (galleryCopyEdit.copyLower ?? "").replace(/\s+/g, " ").trim();
      setGalleryCopyEdit((current) => (current ? { ...current, saving: true, error: undefined } : current));

      try {
        await updatePlanning(galleryCopyEdit.channel, galleryCopyEdit.video, {
          fields: {
            thumbnail_upper: upper ? upper : null,
            thumbnail_title: title ? title : null,
            thumbnail_lower: lower ? lower : null,
          },
        });

        handleUpdateLocalPlanningRow(galleryCopyEdit.video, {
          サムネタイトル上: upper,
          サムネタイトル: title,
          サムネタイトル下: lower,
        });

        if (mode === "save_and_compose") {
          if (!upper || !title || !lower) {
            throw new Error("コピー（上/中/下）が揃っていません。");
          }
          await composeThumbnailVariant(galleryCopyEdit.channel, galleryCopyEdit.video, {
            copy_upper: upper,
            copy_title: title,
            copy_lower: lower,
            label: "文字合成",
            status: "review",
            make_selected: true,
          });
          await fetchData({ silent: true });
        }

        setGalleryCopyEdit(null);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setGalleryCopyEdit((current) => (current ? { ...current, saving: false, error: message } : current));
      }
    },
    [fetchData, galleryCopyEdit, handleUpdateLocalPlanningRow]
  );

  const galleryPanel = activeChannel ? (
    <section className="thumbnail-gallery-panel">
      <div className="thumbnail-gallery-panel__header">
        <div>
          <h3>ギャラリー</h3>
          <p>
            {galleryVariantMode === "three_up"
              ? "00_thumb_1/00_thumb_2/00_thumb_3（3案）を一覧表示します。"
              : galleryVariantMode === "two_up"
                ? "00_thumb_1/00_thumb_2（2案）を一覧表示します。"
              : galleryVariantMode === "all"
                ? "全バリアントを一覧表示し、ZIPでまとめてダウンロードできます。"
                : "選択中サムネを一覧表示し、ZIPでまとめてダウンロードできます。"}{" "}
            <span className="muted small-text">
              （
              {(() => {
                const visibleCount = visibleGalleryItems.length;
                const hiddenCount = galleryHideMissingEnabled ? Math.max(0, galleryItems.length - visibleCount) : 0;
                if (!visibleCount) {
                  return hiddenCount ? `0枚（欠損${hiddenCount}枚非表示）` : "0枚";
                }
                const shownCount = Math.min(visibleCount, galleryLimit);
                return `${shownCount} / ${visibleCount}枚${hiddenCount ? `（欠損${hiddenCount}枚非表示）` : ""}`;
              })()}
              ）
            </span>
          </p>
          {channelHasThreeUpVariants && galleryVariantMode !== "three_up" ? (
            <p className="muted small-text" style={{ marginTop: 6 }}>
              このチャンネルは <strong>3案（00_thumb_1/00_thumb_2/00_thumb_3）</strong> を持っています。{" "}
              <button type="button" className="link-button" onClick={() => setGalleryVariantMode("three_up")}>
                3案を表示
              </button>
            </p>
          ) : channelHasTwoUpVariants && galleryVariantMode !== "two_up" ? (
            <p className="muted small-text" style={{ marginTop: 6 }}>
              このチャンネルは <strong>2案（00_thumb_1/00_thumb_2）</strong> を持っています。{" "}
              <button type="button" className="link-button" onClick={() => setGalleryVariantMode("two_up")}>
                2案を表示
              </button>
            </p>
          ) : null}
        </div>
        <div className="thumbnail-gallery-panel__actions">
          <label className="muted small-text" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span>表示</span>
            <select
              value={galleryVariantMode}
              onChange={(event) => setGalleryVariantMode(parseGalleryVariantMode(event.target.value) ?? "selected")}
            >
              <option value="selected">選択中のみ</option>
              <option value="all">全バリアント</option>
              <option value="two_up">2案（00_thumb_1/2）</option>
              {channelHasThreeUpVariants ? <option value="three_up">3案（00_thumb_1/2/3）</option> : null}
            </select>
          </label>
          <input
            type="search"
            className="thumbnail-gallery-panel__search"
            placeholder="番号・タイトルで検索"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
          />
          <label className="thumbnail-gallery-panel__toggle" title="404などで壊れているサムネカードを非表示">
            <input
              type="checkbox"
              checked={galleryHideMissingEnabled}
              onChange={(event) => setGalleryHideMissingEnabled(event.target.checked)}
            />
            欠損を隠す
          </label>
          <a
            className="btn btn--ghost"
            href={resolveApiUrl(
              `/api/workspaces/thumbnails/${encodeURIComponent(activeChannel.channel)}/download.zip?mode=selected`
            )}
            target="_blank"
            rel="noreferrer"
            title="各企画の選択中サムネだけをZIPでまとめてダウンロード"
          >
            ZIP（選択中）
          </a>
          {channelHasTwoUpVariants ? (
            <a
              className="btn btn--ghost"
              href={resolveApiUrl(
                `/api/workspaces/thumbnails/${encodeURIComponent(activeChannel.channel)}/download.zip?mode=two_up`
              )}
              target="_blank"
              rel="noreferrer"
              title="00_thumb_1 / 00_thumb_2（2案）だけをZIPでまとめてダウンロード"
            >
              ZIP（2案）
            </a>
          ) : null}
          <a
            className="btn btn--primary"
            href={resolveApiUrl(
              `/api/workspaces/thumbnails/${encodeURIComponent(activeChannel.channel)}/download.zip?mode=all`
            )}
            target="_blank"
            rel="noreferrer"
            title="全バリアントをZIPでまとめてダウンロード"
          >
            ZIP（全部）
          </a>
        </div>
      </div>
      <div className="thumbnail-gallery-grid">
        {visibleGalleryItems.length ? (
          visibleGalleryItems.slice(0, galleryLimit).map((item) => {
		          const project = item.project;
		          const itemKey = item.key;
		          const cardKey = itemKey;
		          const selectedVariant = item.variant;
		          const slotLabel = (item.slotLabel ?? "").trim();
	          const displayVariantLabel = (() => {
            const base = selectedVariant ? (selectedVariant.label ?? selectedVariant.id) : "";
            if (!base) {
              return slotLabel;
            }
            if ((isTwoUpMode || isThreeUpMode) && slotLabel) {
              return `${slotLabel} / ${base}`;
            }
            return base;
          })();

          if (!selectedVariant) {
            return (
              <article key={itemKey} className="thumbnail-gallery-card thumbnail-gallery-card--empty">
                <div className="thumbnail-gallery-card__meta">
                  <div className="thumbnail-gallery-card__code">{project.channel}-{project.video}</div>
                  <div className="thumbnail-gallery-card__title" title={project.title ?? undefined}>
                    {project.title ?? "（タイトル未設定）"}
                  </div>
                  {(isTwoUpMode || isThreeUpMode) && slotLabel ? (
                    <div className="thumbnail-gallery-card__variant">{slotLabel}</div>
                  ) : null}
                  <div className="thumbnail-gallery-card__note">
                    {(isTwoUpMode || isThreeUpMode) && slotLabel ? `${slotLabel} 未生成` : "サムネ未登録"}
                  </div>
                  {(isTwoUpMode || isThreeUpMode) && slotLabel ? (
                    <div className="thumbnail-gallery-card__buttons">
                      <button
                        type="button"
                        className="btn btn--primary"
                        onClick={() =>
                          handleOpenLayerTuningDialog(project, {
                            stable: slotLabel,
                            cardKey,
                          })
                        }
                      >
                        調整（ドラッグ）
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() =>
                          handleOpenLayerTuningDialog(project, {
                            stable: slotLabel,
                            initialSelectedAsset: "text",
                            cardKey,
                          })
                        }
                      >
                        文字を編集
                      </button>
                    </div>
                  ) : null}
                </div>
              </article>
            );
          }

          const stableForEdit = (() => {
            if ((isTwoUpMode || isThreeUpMode) && slotLabel) {
              return slotLabel;
            }
            if (
              hasThumbFileSuffix(selectedVariant.image_path, "00_thumb_1.png") ||
              hasThumbFileSuffix(selectedVariant.image_url, "00_thumb_1.png") ||
              hasThumbFileSuffix(selectedVariant.preview_url, "00_thumb_1.png")
            ) {
              return "00_thumb_1";
            }
            if (
              hasThumbFileSuffix(selectedVariant.image_path, "00_thumb_2.png") ||
              hasThumbFileSuffix(selectedVariant.image_url, "00_thumb_2.png") ||
              hasThumbFileSuffix(selectedVariant.preview_url, "00_thumb_2.png")
            ) {
              return "00_thumb_2";
            }
            if (
              hasThumbFileSuffix(selectedVariant.image_path, "00_thumb_3.png") ||
              hasThumbFileSuffix(selectedVariant.image_url, "00_thumb_3.png") ||
              hasThumbFileSuffix(selectedVariant.preview_url, "00_thumb_3.png")
            ) {
              return "00_thumb_3";
            }
            return null;
          })();

          const cacheBustToken =
            selectedVariant.updated_at ?? project.updated_at ?? project.status_updated_at ?? null;
          const imageUrlBase =
            selectedVariant.preview_url
              ? resolveApiUrl(selectedVariant.preview_url)
              : selectedVariant.image_url
                ? resolveApiUrl(selectedVariant.image_url)
                : selectedVariant.image_path
                  ? resolveApiUrl(`/thumbnails/assets/${selectedVariant.image_path}`)
                  : null;
          const imageUrl = imageUrlBase ? withCacheBust(imageUrlBase, cacheBustToken) : null;
          const imageBroken = Boolean(galleryImageErrors[itemKey]);
          const variantMode = galleryVariantMode === "selected" ? "project" : "variant";
          const statusRaw = variantMode === "project" ? project.status : selectedVariant.status;
          const statusForStyle = (() => {
            if (statusRaw === "candidate") return "draft";
            if (statusRaw === "published") return "approved";
            return statusRaw;
          })();
          const statusLabel =
            variantMode === "project"
              ? PROJECT_STATUS_LABELS[project.status] ?? project.status
              : VARIANT_STATUS_LABELS[selectedVariant.status] ?? selectedVariant.status;
          const feedback = cardFeedback[cardKey];
          const busy = galleryProjectSaving[cardKey] ?? false;
          const rejected = Array.isArray(selectedVariant.tags)
            ? selectedVariant.tags.some((tag) => String(tag ?? "").trim().toLowerCase() === VARIANT_REJECT_TAG)
            : false;
          const notesSource = variantMode === "project" ? project.notes ?? "" : selectedVariant.notes ?? "";
          const notesValue = galleryNotesDraft[cardKey] ?? notesSource;
          const notesDirty = notesValue !== notesSource;
          const downloadName = (() => {
            if (galleryVariantMode !== "selected") {
              const raw = selectedVariant.image_path ?? selectedVariant.image_url ?? "";
              const clean = raw.split("?")[0];
              const fileName = clean.split("/").filter(Boolean).slice(-1)[0] ?? "";
              if (fileName) {
                return `${project.channel}-${project.video}-${fileName}`;
              }
              return `${project.channel}-${project.video}-${selectedVariant.id}.png`;
            }
            return `${project.channel}-${project.video}.png`;
          })();

	          return (
	            <article
	              key={itemKey}
	              className={[
	                "thumbnail-gallery-card",
	                `thumbnail-gallery-card--${statusForStyle}`,
	                busy ? "is-updating" : "",
	                rejected ? "is-rejected" : "",
	              ]
	                .filter(Boolean)
	                .join(" ")}
	            >
		              <div className="thumbnail-gallery-card__media">
		                {imageUrl && !imageBroken ? (
		                  <button
		                    type="button"
		                    className="thumbnail-gallery-card__media-button"
		                    onClick={() =>
		                      handleOpenLayerTuningDialog(project, {
		                        stable: stableForEdit,
		                        cardKey,
		                      })
		                    }
		                    title="クリックで調整を開く"
		                  >
		                    <img
		                      src={imageUrl}
		                      alt={`${project.channel}-${project.video}`}
		                      loading="lazy"
		                      draggable={false}
		                      onError={() =>
		                        setGalleryImageErrors((current) =>
		                          current[itemKey] ? current : { ...current, [itemKey]: true }
		                        )
		                      }
		                    />
		                  </button>
		                ) : (
		                  <div className="thumbnail-gallery-card__placeholder">欠損</div>
		                )}
		              </div>
              <div className="thumbnail-gallery-card__meta">
                <div className="thumbnail-gallery-card__code">{project.channel}-{project.video}</div>
                <div className="thumbnail-gallery-card__title" title={project.title ?? undefined}>
                  {project.title ?? "（タイトル未設定）"}
                </div>
                <div className="thumbnail-gallery-card__variant">{displayVariantLabel}</div>
                <div className="thumbnail-gallery-card__review-row">
                  <span className={`thumbnail-card__status-badge thumbnail-card__status-badge--${statusForStyle}`}>
                    {statusLabel}
                  </span>
                  <div className="thumbnail-gallery-card__review-actions" role="group" aria-label="レビュー判定">
                    <label
                      className={`thumbnail-gallery-card__reject-toggle ${rejected ? "is-active" : ""}`}
                      title="不採用（ボツ）"
                    >
                      <input
                        type="checkbox"
                        checked={rejected}
                        onChange={(event) =>
                          void handleGalleryVariantRejectedChange(project, selectedVariant, cardKey, event.target.checked)
                        }
                        disabled={busy}
                      />
                      ボツ
                    </label>
                    <button
                      type="button"
                      className={`btn btn--ghost thumbnail-gallery-card__review-btn ${statusRaw === "approved" ? "is-active" : ""}`}
                      onClick={() => {
                        if (variantMode === "project") {
                          void handleGalleryProjectStatusChange(project, cardKey, "approved");
                          return;
                        }
                        void handleGalleryVariantStatusChange(project, selectedVariant, cardKey, "approved");
                      }}
                      disabled={busy}
                    >
                      OK
                    </button>
                    <button
                      type="button"
                      className={`btn btn--ghost thumbnail-gallery-card__review-btn ${statusRaw === "in_progress" ? "is-active" : ""}`}
                      onClick={() => {
                        if (variantMode === "project") {
                          void handleGalleryProjectStatusChange(project, cardKey, "in_progress");
                          return;
                        }
                        void handleGalleryVariantStatusChange(project, selectedVariant, cardKey, "in_progress");
                      }}
                      disabled={busy}
                    >
                      やり直し
                    </button>
                    <button
                      type="button"
                      className={`btn btn--ghost thumbnail-gallery-card__review-btn ${statusRaw === "review" ? "is-active" : ""}`}
                      onClick={() => {
                        if (variantMode === "project") {
                          void handleGalleryProjectStatusChange(project, cardKey, "review");
                          return;
                        }
                        void handleGalleryVariantStatusChange(project, selectedVariant, cardKey, "review");
                      }}
                      disabled={busy}
                    >
                      保留
                    </button>
                  </div>
                </div>
                <div className="thumbnail-gallery-card__notes">
                  <textarea
                    value={notesValue}
                    placeholder="コメント（任意）"
                    rows={2}
                    onChange={(event) => handleGalleryNotesChange(cardKey, event.target.value)}
                    onKeyDown={(event) => {
                      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                        event.preventDefault();
                        if (variantMode === "project") {
                          void handleGalleryNotesSave(project, cardKey);
                          return;
                        }
                        void handleGalleryVariantNotesSave(project, selectedVariant, cardKey);
                      }
                    }}
                    disabled={busy}
                  />
                  <div className="thumbnail-gallery-card__notes-actions">
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => {
                        if (variantMode === "project") {
                          void handleGalleryNotesSave(project, cardKey);
                          return;
                        }
                        void handleGalleryVariantNotesSave(project, selectedVariant, cardKey);
                      }}
                      disabled={busy || !notesDirty}
                    >
                      {notesDirty ? "コメント保存" : "保存済み"}
                    </button>
                  </div>
                </div>
	                {imageUrl ? (
	                  <div className="thumbnail-gallery-card__buttons">
	                    <button
	                      type="button"
	                      className="btn btn--primary"
	                      onClick={() =>
	                        handleOpenLayerTuningDialog(project, {
	                          stable: stableForEdit,
	                          cardKey,
	                        })
	                      }
	                      title="Canvaみたいにドラッグで位置調整できます"
	                    >
	                      調整（ドラッグ）
	                    </button>
                    {isTwoUpMode || isThreeUpMode ? (
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() =>
                          handleOpenLayerTuningDialog(project, {
                            stable: stableForEdit,
                            initialSelectedAsset: "text",
                            cardKey,
                          })
                        }
                      >
                        文字を編集
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() => handleOpenGalleryCopyEdit(project)}
	                      >
	                        文字を編集
	                      </button>
	                    )}
	                    {imageUrl && !imageBroken ? (
	                      <>
	                        <a className="btn btn--ghost" href={imageUrl} target="_blank" rel="noreferrer">
	                          開く
	                        </a>
	                        <a className="btn" href={imageUrl} download={downloadName}>
	                          DL
	                        </a>
	                      </>
	                    ) : null}
                  </div>
                ) : null}
                {feedback ? (
                  <div
                    className={`thumbnail-card__feedback thumbnail-card__feedback--${feedback.type === "success" ? "success" : "error"}`}
                  >
                    {feedback.message}
                  </div>
                ) : null}
              </div>
            </article>
          );
          })
        ) : (
          <div className="muted small-text" style={{ gridColumn: "1 / -1", padding: 18 }}>
            {(() => {
              const query = searchTerm.trim();
              if (query) {
                return `「${query}」に一致するサムネがありません。`;
              }

              const visibleCount = visibleGalleryItems.length;
              const hiddenCount = galleryHideMissingEnabled ? Math.max(0, galleryItems.length - visibleCount) : 0;
              if (galleryHideMissingEnabled && hiddenCount > 0 && visibleCount === 0) {
                return `欠損を隠す がONのため、欠損${hiddenCount}枚を非表示にしています。OFFにすると確認できます。`;
              }

              if (galleryVariantMode === "selected") {
                return "選択中のサムネがありません。まず「量産（Canva）」か「生成」から作成してください。";
              }
              return "未作成のサムネは非表示です。まず「量産（Canva）」か「生成」から作成してください。";
            })()}
          </div>
        )}
      </div>
      {visibleGalleryItems.length > galleryLimit ? (
        <div className="thumbnail-gallery-panel__more">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setGalleryLimit((prev) => prev + DEFAULT_GALLERY_LIMIT)}
          >
            さらに表示
          </button>
          <span className="muted small-text">
            {Math.min(galleryLimit, visibleGalleryItems.length)} / {visibleGalleryItems.length}
          </span>
        </div>
      ) : null}
    </section>
  ) : (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>ギャラリー</h3>
          <p>チャンネルを選択すると選択中サムネを一覧で表示します。</p>
        </div>
      </div>
    </section>
  );

  const handleLibraryVideoChange = useCallback((assetId: string, value: string) => {
    setLibraryForms((current) => {
      const existing = current[assetId] ?? { video: "", pending: false };
      return {
        ...current,
        [assetId]: {
          ...existing,
          video: value,
          error: undefined,
          success: undefined,
        },
      };
    });
  }, []);

  const handleLibraryUploadClick = useCallback(() => {
    libraryUploadInputRef.current?.click();
  }, []);

  const handleLibraryUploadChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const { files } = event.target;
      if (!files || files.length === 0) {
        return;
      }
      const fileArray = Array.from(files);
      setLibraryUploadStatus({ pending: true, error: null, success: null });
      try {
        await uploadThumbnailLibraryAssets(channelCode, fileArray);
        setLibraryUploadStatus({
          pending: false,
          error: null,
          success: `${fileArray.length} 件の画像を追加しました。`,
        });
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // handled inside loadLibrary
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryUploadStatus({ pending: false, error: message, success: null });
      } finally {
        event.target.value = "";
      }
    },
    [activeChannel, loadLibrary]
  );

  const handleLibraryImportSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const url = libraryImportUrl.trim();
      if (!url) {
        setLibraryImportStatus({ pending: false, error: "URLを入力してください。", success: null });
        return;
      }
      setLibraryImportStatus({ pending: true, error: null, success: null });
      try {
        await importThumbnailLibraryAsset(channelCode, {
          url,
          fileName: libraryImportName.trim() || undefined,
        });
        setLibraryImportStatus({ pending: false, error: null, success: "ライブラリに追加しました。" });
        setLibraryImportUrl("");
        setLibraryImportName("");
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // handled inside loadLibrary
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryImportStatus({ pending: false, error: message, success: null });
      }
    },
    [activeChannel, libraryImportName, libraryImportUrl, loadLibrary]
  );

  const handleLibraryAssignSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, asset: ThumbnailLibraryAsset) => {
      event.preventDefault();
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      const formState = libraryForms[asset.id] ?? { video: "", pending: false };
      const normalizedVideo = normalizeVideoInput(formState.video);
      if (!normalizedVideo) {
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { ...formState, error: "動画番号を入力してください。", success: undefined, pending: false },
        }));
        return;
      }
      setLibraryForms((current) => ({
        ...current,
        [asset.id]: { ...formState, pending: true, error: undefined, success: undefined },
      }));
      try {
        await assignThumbnailLibraryAsset(channelCode, asset.relative_path, {
            video: normalizedVideo,
            label: asset.file_name.replace(/\.[^.]+$/, ""),
            make_selected: true,
        });
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { video: "", pending: false, error: undefined, success: `動画${normalizedVideo}へ紐付け完了` },
        }));
        await fetchData({ silent: true });
        await loadLibrary(channelCode, { silent: true }).catch(() => {
          // silent refresh
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryForms((current) => ({
          ...current,
          [asset.id]: { ...current[asset.id], pending: false, error: message, success: undefined } as LibraryFormState,
        }));
      }
    },
    [activeChannel, fetchData, libraryForms, loadLibrary]
  );

  const handleLibraryRefresh = useCallback(() => {
    if (!activeChannel?.channel) {
      return;
    }
    loadLibrary(activeChannel.channel).catch(() => {
      // loadLibrary 内でエラー表示済み
    });
  }, [activeChannel, loadLibrary]);

  const handleTemplatesRefresh = useCallback(() => {
    if (!activeChannel?.channel) {
      return;
    }
    loadTemplates(activeChannel.channel).catch(() => {
      // loadTemplates 内でエラー表示済み
    });
  }, [activeChannel, loadTemplates]);

  const handleAddTemplate = useCallback(() => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    const defaultModelKey = imageModels[0]?.key ?? "";
    const now = Date.now();
    const newTemplate = {
      id: `tmpl_ui_${now.toString(16)}`,
      name: "新規テンプレ",
      image_model_key: defaultModelKey,
      prompt_template:
        "YouTubeサムネ(16:9)を生成してください。テーマ: {{title}}\n"
        + "文字要素(あれば): {{thumbnail_upper}} / {{thumbnail_lower}}\n"
        + "構図: 強いコントラスト、視認性優先、人物 or シンボルを大きく。\n"
        + "出力: サムネとして使える鮮明な画像。",
      negative_prompt: "",
      notes: "",
      created_at: null,
      updated_at: null,
    };
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      return { ...base, templates: [...(base.templates ?? []), newTemplate] };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel, imageModels]);

  const handleDeleteTemplate = useCallback((templateId: string) => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      const nextTemplates = (base.templates ?? []).filter((tpl) => tpl.id !== templateId);
      const nextDefault =
        base.default_template_id && base.default_template_id === templateId ? null : base.default_template_id ?? null;
      return {
        ...base,
        templates: nextTemplates,
        default_template_id: nextDefault,
      };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel]);

  const handleTemplateFieldChange = useCallback(
    (
      templateId: string,
      field: "name" | "image_model_key" | "prompt_template" | "negative_prompt" | "notes",
      value: string
    ) => {
      const channelCode = activeChannel?.channel;
      if (!channelCode) {
        return;
      }
      setChannelTemplates((current) => {
        const base: ThumbnailChannelTemplates =
          current && current.channel === channelCode
            ? current
            : { channel: channelCode, default_template_id: null, templates: [] };
        const nextTemplates = (base.templates ?? []).map((tpl) => {
          if (tpl.id !== templateId) {
            return tpl;
          }
          return { ...tpl, [field]: value };
        });
        return { ...base, templates: nextTemplates };
      });
      setTemplatesDirty(true);
      setTemplatesStatus({ pending: false, error: null, success: null });
    },
    [activeChannel]
  );

  const handleTemplateDefaultChange = useCallback((templateId: string | null) => {
    const channelCode = activeChannel?.channel;
    if (!channelCode) {
      return;
    }
    setChannelTemplates((current) => {
      const base: ThumbnailChannelTemplates =
        current && current.channel === channelCode
          ? current
          : { channel: channelCode, default_template_id: null, templates: [] };
      return { ...base, default_template_id: templateId };
    });
    setTemplatesDirty(true);
    setTemplatesStatus({ pending: false, error: null, success: null });
  }, [activeChannel]);

  const handleSaveTemplates = useCallback(async () => {
    const channelCode = activeChannel?.channel;
    if (!channelCode || !channelTemplates || channelTemplates.channel !== channelCode) {
      return;
    }
    const templates = channelTemplates.templates ?? [];
    for (const tpl of templates) {
      if (!tpl.name?.trim()) {
        setTemplatesStatus({ pending: false, error: "テンプレ名が空です。", success: null });
        return;
      }
      if (!tpl.image_model_key?.trim()) {
        setTemplatesStatus({ pending: false, error: "画像モデルキーが未選択のテンプレがあります。", success: null });
        return;
      }
      if (!tpl.prompt_template?.trim()) {
        setTemplatesStatus({ pending: false, error: "プロンプトテンプレが空のテンプレがあります。", success: null });
        return;
      }
    }
    const defaultTemplateId = channelTemplates.default_template_id ?? null;
    if (defaultTemplateId && !templates.some((tpl) => tpl.id === defaultTemplateId)) {
      setTemplatesStatus({ pending: false, error: "デフォルトテンプレが templates に含まれていません。", success: null });
      return;
    }

    setTemplatesStatus({ pending: true, error: null, success: null });
    try {
      const updated = await updateThumbnailTemplates(channelCode, {
        default_template_id: defaultTemplateId,
        templates: templates.map((tpl) => ({
          id: tpl.id,
          name: tpl.name,
          image_model_key: tpl.image_model_key,
          prompt_template: tpl.prompt_template,
          negative_prompt: tpl.negative_prompt?.trim() ? tpl.negative_prompt : null,
          notes: tpl.notes?.trim() ? tpl.notes : null,
        })),
      });
      setChannelTemplates(updated);
      setTemplatesDirty(false);
      setTemplatesStatus({ pending: false, error: null, success: "テンプレを保存しました。" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplatesStatus({ pending: false, error: message, success: null });
    }
  }, [activeChannel, channelTemplates]);

  const handleOpenGenerateDialog = useCallback(
    (project: ThumbnailProject) => {
      const channelCode = project.channel;
      const normalizedVideo = normalizeVideoInput(project.video);
      const planningRow = normalizedVideo ? planningRowsByVideo[normalizedVideo] : undefined;
      const defaultUpper = planningRow?.["サムネタイトル上"] ?? "";
      const defaultTitle = planningRow?.["サムネタイトル"] ?? "";
      const defaultLower = planningRow?.["サムネタイトル下"] ?? "";
      const defaultSourceTitle = planningRow?.["タイトル"] ?? project.title ?? project.sheet_title ?? "";
      const defaultThumbnailPrompt =
        planningRow?.["サムネ画像プロンプト（URL・テキスト指示込み）"] ?? planningRow?.["サムネ画像プロンプト"] ?? "";

      const defaultTemplateId =
        (channelTemplates?.channel === channelCode ? channelTemplates.default_template_id : null)
          ?? (channelTemplates?.channel === channelCode ? channelTemplates.templates?.[0]?.id : null)
          ?? "";
      const selectedTemplate =
        channelTemplates?.channel === channelCode
          ? channelTemplates.templates.find((tpl) => tpl.id === defaultTemplateId)
          : undefined;
      const defaultModelKey = selectedTemplate?.image_model_key ?? imageModels[0]?.key ?? "";
      setGenerateDialog({
        projectKey: getProjectKey(project),
        channel: project.channel,
        video: project.video,
        templateId: defaultTemplateId,
        prompt: "",
        sourceTitle: defaultSourceTitle,
        thumbnailPrompt: defaultThumbnailPrompt,
        imageModelKey: defaultModelKey,
        count: 1,
        label: "",
        copyUpper: defaultUpper,
        copyTitle: defaultTitle,
        copyLower: defaultLower,
        saveToPlanning: false,
        status: "draft",
        makeSelected: project.variants.length === 0,
        tags: (project.tags ?? []).join(", "),
        notes: "",
        saving: false,
        error: undefined,
      });
    },
    [channelTemplates, imageModels, planningRowsByVideo]
  );

  const generateDialogChannel = generateDialog?.channel;
  const generateDialogVideo = generateDialog?.video;

  useEffect(() => {
    let cancelled = false;
    if (!generateDialogChannel || !generateDialogVideo) {
      return () => {
        cancelled = true;
      };
    }
    fetchThumbnailVideoLayerSpecs(generateDialogChannel, generateDialogVideo)
      .then((spec) => {
        if (cancelled || !spec?.planning_suggestions) {
          return;
        }
        const suggestions = spec.planning_suggestions;
        setGenerateDialog((current) => {
          if (!current) {
            return current;
          }
          if (current.channel !== generateDialogChannel || current.video !== generateDialogVideo) {
            return current;
          }
          const next = { ...current };
          if (!next.thumbnailPrompt.trim() && suggestions.thumbnail_prompt?.trim()) {
            next.thumbnailPrompt = suggestions.thumbnail_prompt;
          }
          if (!next.copyUpper.trim() && suggestions.thumbnail_upper?.trim()) {
            next.copyUpper = suggestions.thumbnail_upper;
          }
          if (!next.copyTitle.trim() && suggestions.thumbnail_title?.trim()) {
            next.copyTitle = suggestions.thumbnail_title;
          }
          if (!next.copyLower.trim() && suggestions.thumbnail_lower?.trim()) {
            next.copyLower = suggestions.thumbnail_lower;
          }
          return next;
        });
      })
      .catch(() => {
        // best-effort: layer_specs is optional
      });
    return () => {
      cancelled = true;
    };
  }, [generateDialogChannel, generateDialogVideo]);

  const handleComposeVariant = useCallback(
    async (project: ThumbnailProject) => {
      const projectKey = getProjectKey(project);
      const normalizedVideo = normalizeVideoInput(project.video);
      if (!normalizedVideo) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "動画番号が不正です。",
          timestamp: Date.now(),
        });
        return;
      }

      setUpdatingProjectId(projectKey);
      try {
        let planningRow: Record<string, string> | undefined = planningRowsByVideo[normalizedVideo];
        if (!planningRow) {
          try {
            const refreshed = await loadPlanning(project.channel, { silent: true });
            planningRow = refreshed?.[normalizedVideo];
          } catch {
            planningRow = undefined;
          }
        }

        const upper = (planningRow?.["サムネタイトル上"] ?? "").trim();
        const title = (planningRow?.["サムネタイトル"] ?? "").trim();
        const lower = (planningRow?.["サムネタイトル下"] ?? "").trim();
        if (!upper || !title || !lower) {
          throw new Error("企画CSVのサムネコピー（上/中/下）が必要です（量産タブで入力してください）。");
        }

        const variant = await composeThumbnailVariant(project.channel, project.video, {
          copy_upper: upper,
          copy_title: title,
          copy_lower: lower,
          label: "文字合成",
          status: "draft",
          make_selected: project.variants.length === 0,
        });

        const previewUrlBase =
          variant.preview_url?.trim()
            ? resolveApiUrl(variant.preview_url)
            : variant.image_path?.trim()
              ? resolveApiUrl(`/thumbnails/assets/${variant.image_path}`)
              : null;
        const previewUrl = previewUrlBase ? withCacheBust(previewUrlBase, variant.updated_at) : null;

        setProjectFeedback(projectKey, {
          type: "success",
          message: (
            <span>
              文字サムネを作成しました。
              {previewUrl ? (
                <>
                  {" "}
                  <a href={previewUrl} target="_blank" rel="noreferrer">
                    プレビュー
                  </a>
                </>
              ) : null}
            </span>
          ),
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, { type: "error", message, timestamp: Date.now() });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, loadPlanning, planningRowsByVideo, setProjectFeedback]
  );

  const handleCloseGenerateDialog = useCallback(() => {
    setGenerateDialog(null);
  }, []);

  const handleGenerateDialogFieldChange = useCallback(
    (
      field: keyof Omit<GenerateDialogState, "projectKey" | "channel" | "video" | "saving" | "error">,
      value: string | number | boolean
    ) => {
      setGenerateDialog((current) => {
        if (!current) {
          return current;
        }
        return { ...current, [field]: value };
      });
    },
    []
  );

  const handleGenerateDialogSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (!generateDialog) {
        return;
      }
      const trimmedPrompt = generateDialog.prompt.trim();
      const templateId = generateDialog.templateId.trim();
      const modelKey = generateDialog.imageModelKey.trim();
      const selectedTemplate =
        templateId && channelTemplates?.channel === generateDialog.channel
          ? channelTemplates.templates.find((tpl) => tpl.id === templateId)
          : undefined;
      const resolvedModelKey = modelKey || selectedTemplate?.image_model_key?.trim() || "";
      if (!templateId && !trimmedPrompt) {
        setGenerateDialog((current) => (current ? { ...current, error: "テンプレまたはプロンプトを指定してください。" } : current));
        return;
      }
      if (!templateId && !resolvedModelKey) {
        setGenerateDialog((current) => (current ? { ...current, error: "テンプレなしの場合は画像モデルを選択してください。" } : current));
        return;
      }

      setGenerateDialog((current) => (current ? { ...current, saving: true, error: undefined } : current));

      const tags = generateDialog.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);

      try {
        if (generateDialog.saveToPlanning) {
          const normalizeField = (value: string): string | null => {
            const trimmed = value.trim();
            return trimmed ? trimmed : null;
          };
          await updatePlanning(generateDialog.channel, generateDialog.video, {
            fields: {
              thumbnail_upper: normalizeField(generateDialog.copyUpper),
              thumbnail_title: normalizeField(generateDialog.copyTitle),
              thumbnail_lower: normalizeField(generateDialog.copyLower),
              thumbnail_prompt: normalizeField(generateDialog.thumbnailPrompt),
            },
          });
          const normalizedVideo = normalizeVideoInput(generateDialog.video);
          if (normalizedVideo) {
            setPlanningRowsByVideo((current) => {
              const existing = current[normalizedVideo] ?? {};
              return {
                ...current,
                [normalizedVideo]: {
                  ...existing,
                  サムネタイトル上: generateDialog.copyUpper,
                  サムネタイトル: generateDialog.copyTitle,
                  サムネタイトル下: generateDialog.copyLower,
                  "サムネ画像プロンプト（URL・テキスト指示込み）": generateDialog.thumbnailPrompt,
                },
              };
            });
          }
        }

        let finalPrompt = trimmedPrompt;
        if (!finalPrompt) {
          if (!selectedTemplate) {
            throw new Error("テンプレが見つかりませんでした。テンプレを再読み込みしてください。");
          }
          const ctx: Record<string, string> = {
            channel: generateDialog.channel,
            video: normalizeVideoInput(generateDialog.video) || generateDialog.video,
            title: generateDialog.sourceTitle,
            thumbnail_upper: generateDialog.copyUpper,
            thumbnail_title: generateDialog.copyTitle,
            thumbnail_lower: generateDialog.copyLower,
            thumbnail_prompt: generateDialog.thumbnailPrompt,
          };
          finalPrompt = renderPromptTemplate(selectedTemplate.prompt_template, ctx).trim();
          const negative = selectedTemplate.negative_prompt?.trim();
          if (negative) {
            finalPrompt = `${finalPrompt}\n\n【避けるべき要素】\n${negative}`.trim();
          }
        }
        if (!finalPrompt) {
          throw new Error("プロンプトが空です。");
        }

        const payload = {
          template_id: templateId || undefined,
          image_model_key: resolvedModelKey || undefined,
          prompt: finalPrompt,
          count: generateDialog.count,
          label: generateDialog.label.trim() || undefined,
          status: generateDialog.status,
          make_selected: generateDialog.makeSelected,
          notes: generateDialog.notes.trim() || undefined,
          tags: tags.length ? tags : undefined,
        };
        const generated = await generateThumbnailVariants(generateDialog.channel, generateDialog.video, payload);
        const totalCostUsd = (generated ?? []).reduce((sum, variant) => {
          if (typeof variant.cost_usd === "number" && Number.isFinite(variant.cost_usd)) {
            return sum + variant.cost_usd;
          }
          return sum;
        }, 0);
        const hasCost = (generated ?? []).some((variant) => typeof variant.cost_usd === "number" && Number.isFinite(variant.cost_usd));
        setProjectFeedback(generateDialog.projectKey, {
          type: "success",
          message: `AI生成が完了しました（${generateDialog.count}件${hasCost ? ` / 実コスト ${formatUsdAmount(totalCostUsd)}` : ""}）。`,
          timestamp: Date.now(),
        });
        setGenerateDialog(null);
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setGenerateDialog((current) => (current ? { ...current, saving: false, error: message } : current));
      }
    },
    [channelTemplates, fetchData, generateDialog, setProjectFeedback]
  );

  const filteredProjects: ThumbnailProject[] = useMemo(() => {
    if (!activeChannel) {
      return [];
    }

    const projects = [...activeChannel.projects];
    projects.sort((a, b) => {
      const aVideo = Number(normalizeVideoInput(a.video));
      const bVideo = Number(normalizeVideoInput(b.video));
      if (Number.isFinite(aVideo) && Number.isFinite(bVideo) && aVideo !== bVideo) {
        return bVideo - aVideo; // desc
      }
      return (b.video ?? "").localeCompare(a.video ?? "");
    });

    let result = projects;
    if (statusFilter !== "all") {
      result = result.filter((project) => {
        if (statusFilter === "approved") {
          return project.status === "approved" || project.status === "published";
        }
        return project.status === statusFilter;
      });
    }
    const query = searchTerm.trim().toLowerCase();
    if (!query) {
      return result;
    }
    return result.filter((project) => {
      const projectFields = [
        project.title,
        project.sheet_title,
        project.video,
        project.owner,
        ...(project.tags ?? []),
      ]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase());
      if (projectFields.some((value) => value.includes(query))) {
        return true;
      }
      return project.variants.some((variant) => {
        const label = (variant.label ?? variant.id).toLowerCase();
        if (label.includes(query)) {
          return true;
        }
        if (variant.tags && variant.tags.some((tag) => tag.toLowerCase().includes(query))) {
          return true;
        }
        return Boolean(variant.notes && variant.notes.toLowerCase().includes(query));
      });
    });
  }, [activeChannel, searchTerm, statusFilter]);

  const statusCounters = useMemo<Record<StatusFilter, number>>(() => {
    const counters: Record<StatusFilter, number> = {
      all: 0,
      draft: 0,
      in_progress: 0,
      review: 0,
      approved: 0,
      archived: 0,
    };
    if (!activeChannel) {
      return counters;
    }
    counters.all = activeChannel.projects.length;
    for (const project of activeChannel.projects) {
      switch (project.status) {
        case "draft":
        case "in_progress":
        case "review":
        case "archived":
          counters[project.status] += 1;
          break;
        case "approved":
        case "published":
          counters.approved += 1;
          break;
        default:
          break;
      }
    }
    return counters;
  }, [activeChannel]);

  const handleRefresh = useCallback(() => {
    setGalleryImageErrors({});
    fetchData().catch(() => {
      // fetchData 内で記録済み
    });
  }, [fetchData]);

  const handleApplyVideoThumbnail = useCallback((video: ThumbnailChannelVideo) => {
    setVariantForm((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        label: current.label || video.title,
        imageUrl: video.thumbnail_url ?? current.imageUrl,
        prompt: current.prompt || video.title,
      };
    });
  }, []);

  const handleOpenVariantForm = useCallback((project: ThumbnailProject) => {
    const projectKey = getProjectKey(project);
    const defaultPath = `assets/${project.channel}/${project.video}/`;
    setVariantForm({
      projectKey,
      label: "",
      status: "draft",
      imageUrl: "",
      imagePath: defaultPath,
      notes: "",
      tags: "",
      prompt: "",
      makeSelected: project.variants.length === 0,
      showAdvanced: false,
    });
    setProjectForm((current) => (current?.projectKey === projectKey ? current : null));
    setProjectFeedback(projectKey, null);
  }, [setProjectFeedback]);

  const handleOpenLayerTuningDialog = useCallback(
    (
      project: ThumbnailProject,
      options?: { stable?: string | null; initialSelectedAsset?: "bg" | "portrait" | "text"; cardKey?: string }
    ) => {
      const supportsStableVariants =
        galleryVariantMode === "two_up" ||
        galleryVariantMode === "three_up" ||
        channelHasTwoUpVariants ||
        channelHasThreeUpVariants;
      const stableCandidate = supportsStableVariants ? normalizeThumbnailStableId(options?.stable) : null;
      const stable = supportsStableVariants ? stableCandidate ?? "00_thumb_1" : null;
      const projectKey = getProjectKey(project);
      const cardKey = String(options?.cardKey || "").trim() || projectKey;
      const initialSelectedAsset = options?.initialSelectedAsset ?? "bg";
      const stableKey = stable ?? "__default__";
      const stableVariant = stable ? findVariantByThumbFile(project, `${stable}.png`) : null;
      const initialComment = extractHumanCommentFromNotes(stableVariant?.notes ?? project.notes);
      setLayerTuningSelectedAsset(initialSelectedAsset);
      setLayerTuningDialog({
        projectKey,
        cardKey,
        channel: project.channel,
        video: normalizeVideoInput(project.video) || project.video,
        stable,
        projectTitle: project.title ?? project.sheet_title ?? "（タイトル未設定）",
        commentDraft: initialComment,
        commentDraftByStable: { [stableKey]: initialComment },
        loading: true,
        saving: false,
        building: false,
        allowGenerate: true,
        regenBg: false,
        outputMode: "draft",
        error: undefined,
        context: undefined,
        overridesLeaf: {},
      });
      setProjectFeedback(cardKey, null);
    },
    [channelHasThreeUpVariants, channelHasTwoUpVariants, galleryVariantMode, setProjectFeedback]
  );

  useEffect(() => {
    if (!activeChannel) {
      return;
    }
    const params = new URLSearchParams(location.search);
    const videoParam = normalizeVideoInput(params.get("video") ?? "");
    if (!videoParam) {
      return;
    }
    const stableParam = (params.get("stable") ?? params.get("variant") ?? "").trim();
    const stable = normalizeThumbnailStableId(stableParam);
    const key = `${activeChannel.channel}-${videoParam}-${stable ?? ""}`;
    if (autoOpenLayerTuningRef.current === key) {
      return;
    }
    const project = activeChannel.projects.find((item) => normalizeVideoInput(item.video) === videoParam);
    if (!project) {
      return;
    }
    autoOpenLayerTuningRef.current = key;
    setActiveTab("gallery");
    handleOpenLayerTuningDialog(project, { stable, initialSelectedAsset: "bg" });
  }, [activeChannel, handleOpenLayerTuningDialog, location.search]);

  const handleCloseLayerTuningDialog = useCallback(() => {
    setLayerTuningDialog(null);
  }, []);

  const handleLayerTuningStableChange = useCallback((nextStableRaw: string) => {
    const nextStable = normalizeThumbnailStableId(nextStableRaw);
    if (!nextStable) {
      return;
    }
    setLayerTuningDialog((current) => {
      if (!current) {
        return current;
      }
      if (current.stable === nextStable) {
        return current;
      }
      const stableKey = nextStable ?? "__default__";
      const commentDraft = (current.commentDraftByStable ?? {})[stableKey] ?? "";
      const nextCardKey = (() => {
        const base = String(current.cardKey || current.projectKey || "").trim();
        if (!base) {
          return base;
        }
        if (/#thumb_[123]$/.test(base)) {
          const suffix =
            nextStable === "00_thumb_3" ? "#thumb_3" : nextStable === "00_thumb_2" ? "#thumb_2" : "#thumb_1";
          return base.replace(/#thumb_[123]$/, suffix);
        }
        return base;
      })();
      return { ...current, stable: nextStable, cardKey: nextCardKey || current.cardKey, commentDraft, loading: true, error: undefined };
    });
    setLayerTuningSelectedAsset("bg");
  }, []);

	  const layerTuningProjectKey = layerTuningDialog?.projectKey ?? null;
	  const layerTuningChannel = layerTuningDialog?.channel ?? null;
	  const layerTuningVideo = layerTuningDialog?.video ?? null;
	  const layerTuningStable = layerTuningDialog?.stable ?? null;
  const layerTuningForcedTextTemplateId = String(
    layerTuningDialog?.overridesLeaf?.["overrides.text_template_id"] ?? ""
  ).trim();

  const layerTuningTextTemplateId = useMemo(() => {
    const ctx = layerTuningDialog?.context;
    const options = (ctx?.template_options ?? []) as Array<{ id: string; slots?: Record<string, unknown> }>;
    if (!options.length) {
      return "";
    }
    const forced = layerTuningForcedTextTemplateId;
    const fallback = String(ctx?.template_id_default ?? "").trim();
    return forced || fallback || String(options[0]?.id ?? "");
  }, [layerTuningDialog?.context, layerTuningForcedTextTemplateId]);

  const layerTuningTextSlotBoxes = useMemo(() => {
    const ctx = layerTuningDialog?.context;
    const options = (ctx?.template_options ?? []) as Array<{ id: string; slots?: Record<string, { box?: number[] | null }> }>;
    if (!options.length || !layerTuningTextTemplateId) {
      return {};
    }
    const tpl =
      options.find((opt) => String(opt.id || "").trim() === layerTuningTextTemplateId) ?? options[0];
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
  }, [layerTuningDialog?.context, layerTuningTextTemplateId]);

  const layerTuningTextPreviewSignature = useMemo(() => {
    const overrides = pickThumbnailTextPreviewOverrides(layerTuningDialog?.overridesLeaf ?? {});
    const entries = Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify({ stable: layerTuningStable ?? "", entries });
  }, [layerTuningDialog?.overridesLeaf, layerTuningStable]);

  const layerTuningTextPreviewLineSignature = useMemo(() => {
    const entries = Object.entries(layerTuningTextLineSpecLines ?? {})
      .map(([slotKey, line]) => [slotKey, Number((line as any)?.scale ?? 1)] as const)
      .sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify(entries);
  }, [layerTuningTextLineSpecLines]);

  const layerTuningTextPreviewOverrides = useMemo(() => {
    try {
      const parsed = JSON.parse(layerTuningTextPreviewSignature) as { entries?: Array<[string, any]> };
      const entries = Array.isArray(parsed?.entries) ? parsed.entries : [];
      if (!Array.isArray(entries)) {
        return {};
      }
      return Object.fromEntries(entries);
    } catch {
      return {};
    }
  }, [layerTuningTextPreviewSignature]);

  useEffect(() => {
    layerTuningDialogRef.current = layerTuningDialog;
  }, [layerTuningDialog]);

  useEffect(() => {
    layerTuningSelectedAssetRef.current = layerTuningSelectedAsset;
  }, [layerTuningSelectedAsset]);

  useEffect(() => {
    layerTuningSelectedTextSlotRef.current = layerTuningSelectedTextSlot;
  }, [layerTuningSelectedTextSlot]);

  useEffect(() => {
    layerTuningTextLineSpecRef.current = layerTuningTextLineSpecLines;
  }, [layerTuningTextLineSpecLines]);

  useEffect(() => {
    layerTuningTextSlotBoxesRef.current = layerTuningTextSlotBoxes;
  }, [layerTuningTextSlotBoxes]);

  useEffect(() => {
    layerTuningElementsRef.current = layerTuningElements;
  }, [layerTuningElements]);

  useEffect(() => {
    layerTuningSelectedElementIdRef.current = layerTuningSelectedElementId;
  }, [layerTuningSelectedElementId]);

  useEffect(() => {
    layerTuningSnapEnabledRef.current = layerTuningSnapEnabled;
  }, [layerTuningSnapEnabled]);

  useEffect(() => {
    if (!layerTuningChannel || !layerTuningVideo) {
      setLayerTuningBgPreviewSrc(null);
      setLayerTuningPortraitPreviewSrc(null);
      setLayerTuningTextSlotImages({});
      setLayerTuningTextSlotStatus({ loading: false, error: null });
      layerTuningTextSlotRequestRef.current += 1;
      setLayerTuningTextLineSpecLinesImmediate({});
      setLayerTuningTextLineSpecStatus({ loading: false, error: null });
      setLayerTuningSelectedTextSlot(null);
      setLayerTuningElementsImmediate([]);
      setLayerTuningElementsStatus({ loading: false, error: null });
      layerTuningElementsRequestRef.current += 1;
      setLayerTuningSelectedElementId(null);
      layerTuningPreviewDragRef.current = null;
      if (layerTuningPreviewRafRef.current !== null) {
        window.cancelAnimationFrame(layerTuningPreviewRafRef.current);
        layerTuningPreviewRafRef.current = null;
      }
	      layerTuningPreviewPendingPatchRef.current = null;
	      return;
	    }
	    setLayerTuningBgPreviewSrc(resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/10_bg.png`));
    setLayerTuningPortraitPreviewSrc(
      resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/20_portrait.png`)
    );
    setLayerTuningTextSlotImages({});
    setLayerTuningTextSlotStatus({ loading: false, error: null });
    layerTuningTextSlotRequestRef.current += 1;
    setLayerTuningTextLineSpecLinesImmediate({});
    setLayerTuningTextLineSpecStatus({ loading: false, error: null });
    layerTuningTextLineSpecRequestRef.current += 1;
    setLayerTuningSelectedTextSlot(null);
    setLayerTuningElementsImmediate([]);
    setLayerTuningElementsStatus({ loading: false, error: null });
    layerTuningElementsRequestRef.current += 1;
    setLayerTuningSelectedElementId(null);
    layerTuningPreviewDragRef.current = null;
    if (layerTuningPreviewRafRef.current !== null) {
      window.cancelAnimationFrame(layerTuningPreviewRafRef.current);
      layerTuningPreviewRafRef.current = null;
    }
    layerTuningPreviewPendingPatchRef.current = null;
  }, [
    layerTuningChannel,
    layerTuningStable,
    layerTuningVideo,
    setLayerTuningElementsImmediate,
    setLayerTuningTextLineSpecLinesImmediate,
  ]);

  useEffect(() => {
    if (!layerTuningProjectKey) {
      return;
    }
    if (layerTuningDialog?.loading) {
      return;
    }
    const el = layerTuningPreviewRef.current;
    if (!el) {
      return;
    }

    const setLeaf = (path: string, value: unknown) => {
      const key = (path ?? "").trim();
      if (!key) {
        return;
      }
      setLayerTuningDialog((current) => {
        if (!current) {
          return current;
        }
        const next = { ...(current.overridesLeaf ?? {}) };
        if (value === null || value === undefined || value === "") {
          delete next[key];
        } else {
          next[key] = value;
        }
        return { ...current, overridesLeaf: next, error: undefined };
      });
    };

    const handleWheel = (event: WheelEvent) => {
      if (!event.deltaY) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();

      const dialog = layerTuningDialogRef.current;
      if (!dialog) {
        return;
      }
      const selected = layerTuningSelectedAssetRef.current;
      const factor = Math.exp(-event.deltaY * 0.001);
      if (selected === "portrait") {
        const currentZoom = Number(resolveLayerTuningLeafValue(dialog, "overrides.portrait.zoom", 1.0));
        const nextZoom = clampNumber(currentZoom * factor, 0.5, 2.0);
        setLeaf("overrides.portrait.zoom", Number(nextZoom.toFixed(3)));
        return;
      }
      if (selected === "text") {
        const slotKey =
          layerTuningSelectedTextSlotRef.current ??
          Object.keys(layerTuningTextLineSpecRef.current ?? {})
            .filter(Boolean)
            .sort((a, b) => a.localeCompare(b))[0] ??
          null;
        if (!slotKey) {
          return;
        }
        const currentLine = layerTuningTextLineSpecRef.current?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
        const currentScale = Number(currentLine.scale ?? 1);
        const nextScale = clampNumber(currentScale * factor, 0.25, 4.0);
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
          next[slotKey] = { ...existing, scale: Number(nextScale.toFixed(3)) };
          return next;
        });
        return;
      }
      const currentZoom = Number(resolveLayerTuningLeafValue(dialog, "overrides.bg_pan_zoom.zoom", 1.0));
      const nextZoom = clampNumber(currentZoom * factor, 1.0, LAYER_TUNING_BG_MAX_ZOOM);
      setLeaf("overrides.bg_pan_zoom.zoom", Number(nextZoom.toFixed(3)));
    };
    el.addEventListener("wheel", handleWheel, { passive: false, capture: true });

    const update = () => {
      const rect = el.getBoundingClientRect();
      setLayerTuningPreviewSize({
        width: Math.max(0, rect.width),
        height: Math.max(0, rect.height),
      });
    };
    update();

    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => {
      el.removeEventListener("wheel", handleWheel, { capture: true } as any);
      ro.disconnect();
    };
	  }, [layerTuningDialog?.loading, layerTuningProjectKey, setLayerTuningTextLineSpecLinesImmediate]);

  useEffect(() => {
    if (!layerTuningProjectKey || !layerTuningChannel || !layerTuningVideo) {
      return;
    }
    const requestId = (layerTuningRequestRef.current += 1);
    const requestedStable = layerTuningStable ?? null;
    const projectKey = layerTuningProjectKey;
    setLayerTuningDialog((current) => {
      if (!current || current.projectKey !== projectKey) {
        return current;
      }
      return { ...current, loading: true, error: undefined };
    });
    fetchThumbnailEditorContext(layerTuningChannel, layerTuningVideo, { stable: requestedStable })
      .then((context) => {
        if (layerTuningRequestRef.current !== requestId) {
          return;
        }
        setLayerTuningDialog((current) => {
          if (!current || current.projectKey !== projectKey) {
            return current;
          }
          if ((current.stable ?? null) !== requestedStable) {
            return current;
          }
          return {
            ...current,
            loading: false,
            context,
            overridesLeaf: { ...(context?.overrides_leaf ?? {}) },
          };
        });
      })
      .catch((error) => {
        if (layerTuningRequestRef.current !== requestId) {
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        setLayerTuningDialog((current) => {
          if (!current || current.projectKey !== projectKey) {
            return current;
          }
          if ((current.stable ?? null) !== requestedStable) {
            return current;
          }
          return { ...current, loading: false, error: message };
        });
      });
  }, [layerTuningChannel, layerTuningProjectKey, layerTuningStable, layerTuningVideo]);

  useEffect(() => {
    if (!layerTuningProjectKey || !layerTuningChannel || !layerTuningVideo) {
      return;
    }
    if (layerTuningDialog?.loading) {
      return;
    }
    const requestId = (layerTuningTextLineSpecRequestRef.current += 1);
    setLayerTuningTextLineSpecStatus({ loading: true, error: null });
    fetchThumbnailTextLineSpec(layerTuningChannel, layerTuningVideo, layerTuningStable)
      .then((result) => {
        if (layerTuningTextLineSpecRequestRef.current !== requestId) {
          return;
        }
        const rawLines = (result?.lines ?? {}) as Record<
          string,
          { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }
        >;
        const slotKeys = (() => {
          const dialog = layerTuningDialogRef.current;
          const ctx = dialog?.context;
          const options = (ctx?.template_options ?? []) as Array<{ id: string; slots?: Record<string, unknown> }>;
          if (!options.length) {
            return Object.keys(rawLines ?? {});
          }
          const forced = String(dialog?.overridesLeaf?.["overrides.text_template_id"] ?? "").trim();
          const fallback = String(ctx?.template_id_default ?? "").trim();
          const templateId = forced || fallback || options[0]?.id || "";
          const tpl = options.find((opt) => String(opt.id || "").trim() === templateId) ?? options[0];
          const slots = (tpl?.slots ?? {}) as Record<string, unknown>;
          const keys = Object.keys(slots).filter(Boolean);
          return keys.length ? keys : Object.keys(rawLines ?? {});
        })();

        const asNum = (value: any, fallback: number) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : fallback;
        };

        const merged: Record<string, { offset_x: number; offset_y: number; scale: number; rotate_deg?: number }> = {};
        for (const slotKey of slotKeys) {
          const line = rawLines?.[slotKey];
          merged[slotKey] = {
            offset_x: clampNumber(asNum(line?.offset_x, 0), LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
            offset_y: clampNumber(asNum(line?.offset_y, 0), LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
            scale: clampNumber(asNum(line?.scale, 1), 0.25, 4),
            rotate_deg: clampNumber(asNum(line?.rotate_deg, 0), -180, 180),
          };
        }
        Object.entries(rawLines ?? {}).forEach(([slotKey, line]) => {
          if (!slotKey || Object.prototype.hasOwnProperty.call(merged, slotKey)) {
            return;
          }
          merged[slotKey] = {
            offset_x: clampNumber(asNum(line?.offset_x, 0), LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
            offset_y: clampNumber(asNum(line?.offset_y, 0), LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
            scale: clampNumber(asNum(line?.scale, 1), 0.25, 4),
            rotate_deg: clampNumber(asNum(line?.rotate_deg, 0), -180, 180),
          };
        });

        setLayerTuningTextLineSpecLinesImmediate(merged);
        setLayerTuningTextLineSpecStatus({ loading: false, error: null });
        setLayerTuningSelectedTextSlot((current) => {
          if (current && Object.prototype.hasOwnProperty.call(merged, current)) {
            return current;
          }
          const keys = Object.keys(merged);
          return keys.length ? keys[0] : current;
        });
      })
      .catch((error) => {
        if (layerTuningTextLineSpecRequestRef.current !== requestId) {
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        setLayerTuningTextLineSpecStatus({ loading: false, error: message });
      });
	  }, [
	    layerTuningChannel,
	    layerTuningDialog?.loading,
	    layerTuningProjectKey,
	    layerTuningStable,
	    layerTuningVideo,
	    setLayerTuningTextLineSpecLinesImmediate,
	  ]);

  useEffect(() => {
    if (!layerTuningProjectKey || !layerTuningChannel || !layerTuningVideo) {
      return;
    }
    if (layerTuningDialog?.loading) {
      return;
    }
    const requestId = (layerTuningElementsRequestRef.current += 1);
    setLayerTuningElementsStatus({ loading: true, error: null });
    fetchThumbnailElementsSpec(layerTuningChannel, layerTuningVideo, layerTuningStable)
      .then((result) => {
        if (layerTuningElementsRequestRef.current !== requestId) {
          return;
        }
        const nextElements = Array.isArray(result?.elements) ? (result.elements as ThumbnailElementSpec[]) : [];
        setLayerTuningElementsImmediate(nextElements);
        setLayerTuningElementsStatus({ loading: false, error: null });
        setLayerTuningSelectedElementId((current) => {
          if (current && nextElements.some((el) => el.id === current)) {
            return current;
          }
          return nextElements[0]?.id ?? null;
        });
      })
      .catch((error) => {
        if (layerTuningElementsRequestRef.current !== requestId) {
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        setLayerTuningElementsStatus({ loading: false, error: message });
      });
	  }, [
	    layerTuningChannel,
	    layerTuningDialog?.loading,
	    layerTuningProjectKey,
	    layerTuningStable,
	    layerTuningVideo,
	    setLayerTuningElementsImmediate,
	  ]);

  useEffect(() => {
    if (!layerTuningDialog || layerTuningDialog.loading) {
      return;
    }
    if (!layerTuningProjectKey || !layerTuningChannel || !layerTuningVideo) {
      return;
    }
    if (layerTuningTextLineSpecStatus.loading) {
      return;
    }
    const key = `${layerTuningChannel}-${layerTuningVideo}-${layerTuningStable ?? ""}`;
    if (layerTuningTextLegacyMigrationRef.current[key]) {
      return;
    }
    if (Object.keys(layerTuningTextLineSpecLines).length === 0) {
      return;
    }

    const rawOffX = Number(layerTuningDialog.overridesLeaf?.["overrides.text_offset_x"] ?? 0);
    const rawOffY = Number(layerTuningDialog.overridesLeaf?.["overrides.text_offset_y"] ?? 0);
    const rawScale = Number(layerTuningDialog.overridesLeaf?.["overrides.text_scale"] ?? 1);

    const offX = Number.isFinite(rawOffX) ? rawOffX : 0;
    const offY = Number.isFinite(rawOffY) ? rawOffY : 0;
    const scale = Number.isFinite(rawScale) ? rawScale : 1;

    const hasLegacyOffsets = Math.abs(offX) > 1e-9 || Math.abs(offY) > 1e-9;
    const hasLegacyScale = Math.abs(scale - 1) > 1e-6;
    if (!hasLegacyOffsets && !hasLegacyScale) {
      layerTuningTextLegacyMigrationRef.current[key] = true;
      return;
    }

    setLayerTuningTextLineSpecLinesImmediate((current) => {
      const next = { ...(current ?? {}) };
      const factor = clampNumber(scale, 0.25, 4.0);
      Object.entries(next).forEach(([slotKey, line]) => {
        if (!slotKey) {
          return;
        }
        const base = line ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
        next[slotKey] = {
          offset_x: clampNumber(Number(base.offset_x ?? 0) + offX, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
          offset_y: clampNumber(Number(base.offset_y ?? 0) + offY, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX),
          scale: clampNumber(Number(base.scale ?? 1) * factor, 0.25, 4),
          rotate_deg: clampNumber(Number((base as any).rotate_deg ?? 0), -180, 180),
        };
      });
      return next;
    });

    setLayerTuningDialog((current) => {
      if (!current || current.projectKey !== layerTuningProjectKey) {
        return current;
      }
      const nextLeaf = { ...(current.overridesLeaf ?? {}) };
      delete nextLeaf["overrides.text_offset_x"];
      delete nextLeaf["overrides.text_offset_y"];
      delete nextLeaf["overrides.text_scale"];
      return { ...current, overridesLeaf: nextLeaf };
    });

    layerTuningTextLegacyMigrationRef.current[key] = true;
  }, [
    layerTuningChannel,
    layerTuningDialog,
    layerTuningProjectKey,
    layerTuningStable,
	    layerTuningTextLineSpecLines,
	    layerTuningTextLineSpecStatus.loading,
	    layerTuningVideo,
      setLayerTuningTextLineSpecLinesImmediate,
	  ]);

  useEffect(() => {
    if (!layerTuningProjectKey || !layerTuningChannel || !layerTuningVideo) {
      return;
    }
    if (layerTuningDialog?.loading) {
      return;
    }

    const requestId = (layerTuningTextSlotRequestRef.current += 1);
    setLayerTuningTextSlotStatus({ loading: true, error: null });

    const timer = window.setTimeout(() => {
      previewThumbnailTextLayerSlots(layerTuningChannel, layerTuningVideo, layerTuningTextPreviewOverrides, {
        stable: layerTuningStable,
        lines: layerTuningTextLineSpecRef.current ?? {},
      })
        .then((result) => {
          if (layerTuningTextSlotRequestRef.current !== requestId) {
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
          setLayerTuningTextSlotImages(next);
          setLayerTuningTextSlotStatus({ loading: false, error: null });
          setLayerTuningSelectedTextSlot((current) => {
            if (current && Object.prototype.hasOwnProperty.call(next, current)) {
              return current;
            }
            const keys = Object.keys(next);
            return keys.length ? keys[0] : current;
          });
        })
        .catch((error) => {
          if (layerTuningTextSlotRequestRef.current !== requestId) {
            return;
          }
          const message = error instanceof Error ? error.message : String(error);
          setLayerTuningTextSlotStatus({ loading: false, error: message });
        });
    }, 180);

    return () => {
      window.clearTimeout(timer);
    };
  }, [
    layerTuningChannel,
    layerTuningDialog?.loading,
    layerTuningProjectKey,
    layerTuningStable,
    layerTuningTextPreviewLineSignature,
    layerTuningTextPreviewOverrides,
    layerTuningVideo,
  ]);

  const setLayerTuningOverrideLeaf = useCallback((path: string, value: unknown | null) => {
    setLayerTuningDialog((current) => {
      if (!current) {
        return current;
      }
      const key = (path ?? "").trim();
      if (!key) {
        return current;
      }
      const next = { ...(current.overridesLeaf ?? {}) };
      if (value === null || value === undefined || value === "") {
        delete next[key];
      } else {
        next[key] = value;
      }
      return { ...current, overridesLeaf: next, error: undefined };
    });
  }, []);

  const mergeLayerTuningOverridesLeaf = useCallback((patch: Record<string, unknown>, options?: { reset?: boolean }) => {
    setLayerTuningDialog((current) => {
      if (!current) {
        return current;
      }
      const next = options?.reset ? {} : { ...(current.overridesLeaf ?? {}) };
      Object.entries(patch ?? {}).forEach(([rawKey, rawValue]) => {
        const key = String(rawKey ?? "").trim();
        if (!key) {
          return;
        }
        if (rawValue === null || rawValue === undefined || rawValue === "") {
          delete next[key];
        } else {
          next[key] = rawValue;
        }
      });
      return { ...current, overridesLeaf: next, error: undefined };
    });
  }, []);

  const flushLayerTuningPreviewPatch = useCallback((): Record<string, unknown> | null => {
    if (layerTuningPreviewRafRef.current !== null) {
      window.cancelAnimationFrame(layerTuningPreviewRafRef.current);
      layerTuningPreviewRafRef.current = null;
    }
    const pending = layerTuningPreviewPendingPatchRef.current;
    layerTuningPreviewPendingPatchRef.current = null;
    if (pending) {
      mergeLayerTuningOverridesLeaf(pending);
    }
    return pending ?? null;
  }, [mergeLayerTuningOverridesLeaf]);

  const scheduleLayerTuningPreviewPatch = useCallback(
    (patch: Record<string, unknown>) => {
      layerTuningPreviewPendingPatchRef.current = patch;
      if (layerTuningPreviewRafRef.current !== null) {
        return;
      }
      layerTuningPreviewRafRef.current = window.requestAnimationFrame(() => {
        layerTuningPreviewRafRef.current = null;
        const pending = layerTuningPreviewPendingPatchRef.current;
        layerTuningPreviewPendingPatchRef.current = null;
        if (pending) {
          mergeLayerTuningOverridesLeaf(pending);
        }
      });
    },
    [mergeLayerTuningOverridesLeaf]
  );

  const beginLayerTuningPreviewBgDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      setLayerTuningSelectedAsset("bg");

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const rawZoom = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.zoom", 1.0));
      const zoomOverridden = isLayerTuningLeafOverridden(layerTuningDialog, "overrides.bg_pan_zoom.zoom");
      const zoomFallback =
        !zoomOverridden && (!Number.isFinite(rawZoom) || rawZoom <= 1.0001) ? LAYER_TUNING_BG_DEFAULT_ZOOM : rawZoom;
      const zoom = clampNumber(zoomFallback, 1.0, LAYER_TUNING_BG_MAX_ZOOM);
      if (!zoomOverridden && zoom !== rawZoom) {
        setLayerTuningOverrideLeaf("overrides.bg_pan_zoom.zoom", Number(zoom.toFixed(3)));
      }
      const panX = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_x", 0.0));
      const panY = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_y", 0.0));
      layerTuningPreviewDragRef.current = {
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
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width, setLayerTuningOverrideLeaf]
  );

  const beginLayerTuningPreviewPortraitDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      event.stopPropagation();
      setLayerTuningSelectedAsset("portrait");

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const offX = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_x", 0.0));
      const offY = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_y", 0.0));
      layerTuningPreviewDragRef.current = {
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
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewTextDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, slotKeyOverride?: string) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      event.stopPropagation();
      setLayerTuningSelectedAsset("text");

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const forcedKey = String(slotKeyOverride ?? "").trim();
      const slotKey =
        forcedKey ||
        layerTuningSelectedTextSlotRef.current ||
        Object.keys(layerTuningTextLineSpecRef.current ?? {})
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b))[0] ||
        Object.keys(layerTuningTextSlotImages).filter(Boolean).sort((a, b) => a.localeCompare(b))[0] ||
        null;
      if (!slotKey) {
        return;
      }
      if (layerTuningSelectedTextSlotRef.current !== slotKey) {
        setLayerTuningSelectedTextSlot(slotKey);
      }
      const currentLine = layerTuningTextLineSpecRef.current?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
      const offX = Number(currentLine.offset_x ?? 0);
      const offY = Number(currentLine.offset_y ?? 0);
      layerTuningPreviewDragRef.current = {
        kind: "text_slot",
        slotKey,
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
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width, layerTuningTextSlotImages]
  );

  const beginLayerTuningPreviewElementDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, elementId: string) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      const id = String(elementId || "").trim();
      if (!id) {
        return;
      }

      setLayerTuningSelectedAsset("element");
      if (layerTuningSelectedElementIdRef.current !== id) {
        setLayerTuningSelectedElementId(id);
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const currentElements = layerTuningElementsRef.current ?? [];
      const el = currentElements.find((item) => String(item?.id ?? "") === id);
      if (!el) {
        return;
      }
      const startX = Number.isFinite(Number((el as any).x)) ? Number((el as any).x) : 0.5;
      const startY = Number.isFinite(Number((el as any).y)) ? Number((el as any).y) : 0.5;
      const elementW = Number.isFinite(Number((el as any).w)) ? Number((el as any).w) : 0.2;
      const elementH = Number.isFinite(Number((el as any).h)) ? Number((el as any).h) : 0.2;
      layerTuningPreviewDragRef.current = {
        kind: "element",
        elementId: id,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX,
        startY,
        elementW,
        elementH,
        width,
        height,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.stopPropagation();
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewElementResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, elementId: string, handle: LayerTuningResizeHandle) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      const id = String(elementId || "").trim();
      const handleId = String(handle || "").trim() as LayerTuningResizeHandle;
      if (!id || !handleId) {
        return;
      }

      event.stopPropagation();
      setLayerTuningSelectedAsset("element");
      if (layerTuningSelectedElementIdRef.current !== id) {
        setLayerTuningSelectedElementId(id);
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const el = (layerTuningElementsRef.current ?? []).find((item) => String(item?.id ?? "") === id);
      if (!el) {
        return;
      }

      const startX = Number.isFinite(Number((el as any).x)) ? Number((el as any).x) : 0.5;
      const startY = Number.isFinite(Number((el as any).y)) ? Number((el as any).y) : 0.5;
      const startW = Number.isFinite(Number((el as any).w)) ? Number((el as any).w) : 0.2;
      const startH = Number.isFinite(Number((el as any).h)) ? Number((el as any).h) : 0.2;
      const rotationDeg = clampNumber(Number((el as any).rotation_deg ?? 0), -180, 180);

      layerTuningPreviewDragRef.current = {
        kind: "element_resize",
        elementId: id,
        handle: handleId,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX,
        startY,
        startW,
        startH,
        rotationDeg,
        width,
        height,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewElementRotate = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, elementId: string) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      const id = String(elementId || "").trim();
      if (!id) {
        return;
      }

      event.stopPropagation();
      setLayerTuningSelectedAsset("element");
      if (layerTuningSelectedElementIdRef.current !== id) {
        setLayerTuningSelectedElementId(id);
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!rect || !width || !height) {
        return;
      }

      const el = (layerTuningElementsRef.current ?? []).find((item) => String(item?.id ?? "") === id);
      if (!el) {
        return;
      }
      const x = Number.isFinite(Number((el as any).x)) ? Number((el as any).x) : 0.5;
      const y = Number.isFinite(Number((el as any).y)) ? Number((el as any).y) : 0.5;
      const centerClientX = rect.left + x * width;
      const centerClientY = rect.top + y * height;
      const startRotationDeg = clampNumber(Number((el as any).rotation_deg ?? 0), -180, 180);
      const startAngleRad = Math.atan2(event.clientY - centerClientY, event.clientX - centerClientX);

      layerTuningPreviewDragRef.current = {
        kind: "element_rotate",
        elementId: id,
        pointerId: event.pointerId,
        centerClientX,
        centerClientY,
        startRotationDeg,
        startAngleRad,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewPortraitScale = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }

      event.stopPropagation();
      setLayerTuningSelectedAsset("portrait");

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!rect || !width || !height) {
        return;
      }

      const rawBox = (layerTuningDialog.context as any)?.portrait_box;
      const box =
        Array.isArray(rawBox) && rawBox.length === 4 && rawBox.every((v: any) => Number.isFinite(Number(v)))
          ? rawBox.map((v: any) => Number(v))
          : [0.29, 0.06, 0.42, 0.76];
      const boxLeft = width * Number(box[0]);
      const boxTop = height * Number(box[1]);
      const boxW = width * Number(box[2]);
      const boxH = height * Number(box[3]);
      if (!Number.isFinite(boxLeft) || !Number.isFinite(boxTop) || !Number.isFinite(boxW) || !Number.isFinite(boxH)) {
        return;
      }

      const offX = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_x", 0.0));
      const offY = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_y", 0.0));
      const centerClientX = rect.left + (boxLeft + boxW / 2 + offX * width);
      const centerClientY = rect.top + (boxTop + boxH / 2 + offY * height);
      const startZoom = clampNumber(
        Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.zoom", 1.0)),
        0.25,
        4
      );
      const startDist = Math.hypot(event.clientX - centerClientX, event.clientY - centerClientY);
      if (!Number.isFinite(startDist) || startDist <= 0.5) {
        return;
      }

      layerTuningPreviewDragRef.current = {
        kind: "portrait_scale",
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        centerClientX,
        centerClientY,
        startZoom,
        startDist,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewTextSlotScale = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, slotKeyRaw: string) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      const slotKey = String(slotKeyRaw || "").trim();
      if (!slotKey) {
        return;
      }

      event.stopPropagation();
      setLayerTuningSelectedAsset("text");
      if (layerTuningSelectedTextSlotRef.current !== slotKey) {
        setLayerTuningSelectedTextSlot(slotKey);
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!rect || !width || !height) {
        return;
      }

      const box = layerTuningTextSlotBoxesRef.current?.[slotKey] ?? null;
      if (!Array.isArray(box) || box.length !== 4) {
        return;
      }
      const line = layerTuningTextLineSpecRef.current?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
      const centerClientX =
        rect.left + (Number(box[0]) + Number(box[2]) * 0.5 + Number(line.offset_x ?? 0)) * width;
      const centerClientY =
        rect.top + (Number(box[1]) + Number(box[3]) * 0.5 + Number(line.offset_y ?? 0)) * height;
      const startScale = clampNumber(Number(line.scale ?? 1), 0.05, 8);
      const startDist = Math.hypot(event.clientX - centerClientX, event.clientY - centerClientY);
      if (!Number.isFinite(startDist) || startDist <= 0.5) {
        return;
      }

      layerTuningPreviewDragRef.current = {
        kind: "text_slot_scale",
        slotKey,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        centerClientX,
        centerClientY,
        startScale,
        startDist,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const beginLayerTuningPreviewTextSlotRotate = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, slotKeyRaw: string) => {
      if (!layerTuningDialog) {
        return;
      }
      if (event.button !== 0) {
        return;
      }
      const slotKey = String(slotKeyRaw || "").trim();
      if (!slotKey) {
        return;
      }

      event.stopPropagation();
      setLayerTuningSelectedAsset("text");
      if (layerTuningSelectedTextSlotRef.current !== slotKey) {
        setLayerTuningSelectedTextSlot(slotKey);
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!rect || !width || !height) {
        return;
      }

      const box = layerTuningTextSlotBoxesRef.current?.[slotKey] ?? null;
      if (!Array.isArray(box) || box.length !== 4) {
        return;
      }
      const line = layerTuningTextLineSpecRef.current?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
      const centerClientX =
        rect.left + (Number(box[0]) + Number(box[2]) * 0.5 + Number(line.offset_x ?? 0)) * width;
      const centerClientY =
        rect.top + (Number(box[1]) + Number(box[3]) * 0.5 + Number(line.offset_y ?? 0)) * height;
      const startRotationDeg = clampNumber(Number(line.rotate_deg ?? 0), -180, 180);
      const startAngleRad = Math.atan2(event.clientY - centerClientY, event.clientX - centerClientX);

      layerTuningPreviewDragRef.current = {
        kind: "text_slot_rotate",
        slotKey,
        pointerId: event.pointerId,
        centerClientX,
        centerClientY,
        startRotationDeg,
        startAngleRad,
      };

      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // ignore
      }
      event.preventDefault();
    },
    [layerTuningDialog, layerTuningPreviewSize.height, layerTuningPreviewSize.width]
  );

  const handleLayerTuningPreviewDragMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = layerTuningPreviewDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) {
        return;
      }

      const dx = "startClientX" in drag ? event.clientX - drag.startClientX : 0;
      const dy = "startClientY" in drag ? event.clientY - drag.startClientY : 0;

      const normalizeRotationDeg = (deg: number) => {
        if (!Number.isFinite(deg)) {
          return 0;
        }
        let out = deg % 360;
        if (out > 180) {
          out -= 360;
        } else if (out < -180) {
          out += 360;
        }
        return clampNumber(out, -180, 180);
      };

      if (drag.kind === "element_rotate") {
        const elementId = String(drag.elementId || "").trim();
        if (!elementId) {
          return;
        }
        const angle = Math.atan2(event.clientY - drag.centerClientY, event.clientX - drag.centerClientX);
        const deltaDeg = ((angle - drag.startAngleRad) * 180) / Math.PI;
        let nextRotation = normalizeRotationDeg(drag.startRotationDeg + deltaDeg);
        if (event.shiftKey) {
          nextRotation = normalizeRotationDeg(Math.round(nextRotation / 15) * 15);
        }
        setLayerTuningElementsImmediate((current) =>
          (current ?? []).map((el) => {
            if (String(el?.id ?? "") !== elementId) {
              return el;
            }
            return { ...el, rotation_deg: Number(nextRotation.toFixed(3)) };
          })
        );
        return;
      }

      if (drag.kind === "element_resize") {
        const elementId = String(drag.elementId || "").trim();
        if (!elementId) {
          return;
        }
        const handle = drag.handle;
        const hx = handle.includes("e") ? 1 : handle.includes("w") ? -1 : 0;
        const hy = handle.includes("s") ? 1 : handle.includes("n") ? -1 : 0;
        if (hx === 0 && hy === 0) {
          return;
        }

        const theta = (Number(drag.rotationDeg) * Math.PI) / 180;
        const cos = Math.cos(theta);
        const sin = Math.sin(theta);
        const localDx = dx * cos + dy * sin;
        const localDy = -dx * sin + dy * cos;

        const startWpx = Number(drag.startW) * drag.width;
        const startHpx = Number(drag.startH) * drag.height;
        if (!Number.isFinite(startWpx) || !Number.isFinite(startHpx) || startWpx <= 0 || startHpx <= 0) {
          return;
        }

        const fromCenter = Boolean(event.altKey);
        let deltaWpx = localDx * hx;
        let deltaHpx = localDy * hy;
        let centerShiftLocalX = hx !== 0 ? localDx / 2 : 0;
        let centerShiftLocalY = hy !== 0 ? localDy / 2 : 0;
        if (fromCenter) {
          deltaWpx *= 2;
          deltaHpx *= 2;
          centerShiftLocalX = 0;
          centerShiftLocalY = 0;
        }

        let nextWpx = startWpx + deltaWpx;
        let nextHpx = startHpx + deltaHpx;

        if (event.shiftKey && hx !== 0 && hy !== 0) {
          const ratio = startHpx / startWpx;
          if (Number.isFinite(ratio) && ratio > 0) {
            if (Math.abs(deltaWpx) >= Math.abs(deltaHpx)) {
              nextHpx = nextWpx * ratio;
            } else {
              nextWpx = nextHpx / ratio;
            }
          }
        }

        const nextW = clampNumber(nextWpx / drag.width, 0.01, 4);
        const nextH = clampNumber(nextHpx / drag.height, 0.01, 4);

        const screenShiftX = centerShiftLocalX * cos - centerShiftLocalY * sin;
        const screenShiftY = centerShiftLocalX * sin + centerShiftLocalY * cos;
        const nextX = clampNumber(
          Number(drag.startX) + screenShiftX / drag.width,
          LAYER_TUNING_ELEMENT_XY_MIN,
          LAYER_TUNING_ELEMENT_XY_MAX
        );
        const nextY = clampNumber(
          Number(drag.startY) + screenShiftY / drag.height,
          LAYER_TUNING_ELEMENT_XY_MIN,
          LAYER_TUNING_ELEMENT_XY_MAX
        );

        setLayerTuningElementsImmediate((current) =>
          (current ?? []).map((el) => {
            if (String(el?.id ?? "") !== elementId) {
              return el;
            }
            return {
              ...el,
              x: Number(nextX.toFixed(4)),
              y: Number(nextY.toFixed(4)),
              w: Number(nextW.toFixed(4)),
              h: Number(nextH.toFixed(4)),
            };
          })
        );
        return;
      }

      if (drag.kind === "portrait_scale") {
        const dist = Math.hypot(event.clientX - drag.centerClientX, event.clientY - drag.centerClientY);
        if (!Number.isFinite(dist) || dist <= 0) {
          return;
        }
        const ratio = dist / Math.max(0.5, Number(drag.startDist));
        const nextZoom = clampNumber(Number(drag.startZoom) * ratio, 0.5, 2.0);
        scheduleLayerTuningPreviewPatch({ "overrides.portrait.zoom": Number(nextZoom.toFixed(3)) });
        return;
      }

      if (drag.kind === "text_slot_scale") {
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) {
          return;
        }
        const dist = Math.hypot(event.clientX - drag.centerClientX, event.clientY - drag.centerClientY);
        if (!Number.isFinite(dist) || dist <= 0) {
          return;
        }
        const ratio = dist / Math.max(0.5, Number(drag.startDist));
        const nextScale = clampNumber(Number(drag.startScale) * ratio, 0.25, 4);
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
          next[slotKey] = { ...existing, scale: Number(nextScale.toFixed(3)) };
          return next;
        });
        return;
      }

      if (drag.kind === "text_slot_rotate") {
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) {
          return;
        }
        const angle = Math.atan2(event.clientY - drag.centerClientY, event.clientX - drag.centerClientX);
        const deltaDeg = ((angle - drag.startAngleRad) * 180) / Math.PI;
        let nextRotation = normalizeRotationDeg(Number(drag.startRotationDeg) + deltaDeg);
        if (event.shiftKey) {
          nextRotation = normalizeRotationDeg(Math.round(nextRotation / 15) * 15);
        }
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
          next[slotKey] = { ...existing, rotate_deg: Number(nextRotation.toFixed(3)) };
          return next;
        });
        return;
      }

      if (drag.kind === "bg") {
        const zoom = Number(drag.zoom);
        const maxDx = zoom > 1.0001 ? (drag.width * (zoom - 1)) / 2 : drag.width / 2;
        const maxDy = zoom > 1.0001 ? (drag.height * (zoom - 1)) / 2 : drag.height / 2;
        if (!Number.isFinite(maxDx) || !Number.isFinite(maxDy) || maxDx <= 0 || maxDy <= 0) {
          return;
        }
        const nextPanX = clampNumber(drag.startPanX - dx / maxDx, LAYER_TUNING_BG_PAN_MIN, LAYER_TUNING_BG_PAN_MAX);
        const nextPanY = clampNumber(drag.startPanY - dy / maxDy, LAYER_TUNING_BG_PAN_MIN, LAYER_TUNING_BG_PAN_MAX);
        scheduleLayerTuningPreviewPatch({
          "overrides.bg_pan_zoom.pan_x": nextPanX,
          "overrides.bg_pan_zoom.pan_y": nextPanY,
        });
        return;
      }

      if (drag.kind === "element") {
        const elementId = String(drag.elementId || "").trim();
        if (!elementId) {
          return;
        }
        const wNorm = Number.isFinite(Number(drag.elementW)) ? Number(drag.elementW) : 0.2;
        const hNorm = Number.isFinite(Number(drag.elementH)) ? Number(drag.elementH) : 0.2;
        const thresholdX = drag.width ? 8 / drag.width : 0;
        const thresholdY = drag.height ? 8 / drag.height : 0;
        let nextX = clampNumber(drag.startX + dx / drag.width, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        let nextY = clampNumber(drag.startY + dy / drag.height, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        if (layerTuningSnapEnabledRef.current && !event.altKey && thresholdX > 0 && thresholdY > 0) {
          const left = nextX - wNorm / 2;
          const right = nextX + wNorm / 2;
          const top = nextY - hNorm / 2;
          const bottom = nextY + hNorm / 2;
          if (Math.abs(nextX - 0.5) < thresholdX) {
            nextX = 0.5;
          } else if (Math.abs(left - 0.0) < thresholdX) {
            nextX = wNorm / 2;
          } else if (Math.abs(right - 1.0) < thresholdX) {
            nextX = 1.0 - wNorm / 2;
          }
          if (Math.abs(nextY - 0.5) < thresholdY) {
            nextY = 0.5;
          } else if (Math.abs(top - 0.0) < thresholdY) {
            nextY = hNorm / 2;
          } else if (Math.abs(bottom - 1.0) < thresholdY) {
            nextY = 1.0 - hNorm / 2;
          }
        }
        setLayerTuningElementsImmediate((current) =>
          (current ?? []).map((el) => {
            if (String(el?.id ?? "") !== elementId) {
              return el;
            }
            return { ...el, x: Number(nextX.toFixed(4)), y: Number(nextY.toFixed(4)) };
          })
        );
        return;
      }

      if (drag.kind === "text_slot") {
        const nextOffX = clampNumber(drag.startOffX + dx / drag.width, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
        const nextOffY = clampNumber(drag.startOffY + dy / drag.height, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
        const slotKey = String(drag.slotKey || "").trim();
        if (!slotKey) {
          return;
        }
        const box = layerTuningTextSlotBoxesRef.current?.[slotKey];
        let resolvedOffX = nextOffX;
        let resolvedOffY = nextOffY;
        if (
          layerTuningSnapEnabledRef.current &&
          !event.altKey &&
          Array.isArray(box) &&
          box.length === 4 &&
          drag.width > 0 &&
          drag.height > 0
        ) {
          const thresholdX = 8 / drag.width;
          const thresholdY = 8 / drag.height;
          const boxLeft = Number(box[0]) + resolvedOffX;
          const boxTop = Number(box[1]) + resolvedOffY;
          const boxW = Number(box[2]);
          const boxH = Number(box[3]);
          const boxRight = boxLeft + boxW;
          const boxBottom = boxTop + boxH;
          const boxCx = boxLeft + boxW / 2;
          const boxCy = boxTop + boxH / 2;

          if (Math.abs(boxCx - 0.5) < thresholdX) {
            resolvedOffX = 0.5 - (Number(box[0]) + boxW / 2);
          } else if (Math.abs(boxLeft - 0.0) < thresholdX) {
            resolvedOffX = -Number(box[0]);
          } else if (Math.abs(boxRight - 1.0) < thresholdX) {
            resolvedOffX = 1.0 - (Number(box[0]) + boxW);
          }
          if (Math.abs(boxCy - 0.5) < thresholdY) {
            resolvedOffY = 0.5 - (Number(box[1]) + boxH / 2);
          } else if (Math.abs(boxTop - 0.0) < thresholdY) {
            resolvedOffY = -Number(box[1]);
          } else if (Math.abs(boxBottom - 1.0) < thresholdY) {
            resolvedOffY = 1.0 - (Number(box[1]) + boxH);
          }
          resolvedOffX = clampNumber(resolvedOffX, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
          resolvedOffY = clampNumber(resolvedOffY, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
        }
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
          next[slotKey] = {
            ...existing,
            offset_x: Number(resolvedOffX.toFixed(4)),
            offset_y: Number(resolvedOffY.toFixed(4)),
          };
          return next;
        });
        return;
      }

      const nextOffX = clampNumber(drag.startOffX + dx / drag.width, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
      const nextOffY = clampNumber(drag.startOffY + dy / drag.height, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
      if (drag.kind === "text") {
        scheduleLayerTuningPreviewPatch({
          "overrides.text_offset_x": nextOffX,
          "overrides.text_offset_y": nextOffY,
        });
        return;
      }
      scheduleLayerTuningPreviewPatch({
        "overrides.portrait.offset_x": nextOffX,
        "overrides.portrait.offset_y": nextOffY,
      });
    },
    [scheduleLayerTuningPreviewPatch, setLayerTuningElementsImmediate, setLayerTuningTextLineSpecLinesImmediate]
  );

  const handleLayerTuningPreviewDragEnd = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = layerTuningPreviewDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    flushLayerTuningPreviewPatch();
    layerTuningPreviewDragRef.current = null;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // ignore
    }
  }, [flushLayerTuningPreviewPatch]);

  const handleLayerTuningPreviewBgWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      if (!layerTuningDialog) {
        return;
      }
      if (!event.deltaY) {
        return;
      }
      event.stopPropagation();
      event.preventDefault();
      const currentZoom = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.zoom", 1.0));
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextZoom = clampNumber(currentZoom * factor, 1.0, LAYER_TUNING_BG_MAX_ZOOM);
      setLayerTuningOverrideLeaf("overrides.bg_pan_zoom.zoom", Number(nextZoom.toFixed(3)));
    },
    [layerTuningDialog, setLayerTuningOverrideLeaf]
  );

  const handleLayerTuningPreviewPortraitWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      if (!layerTuningDialog) {
        return;
      }
      if (!event.deltaY) {
        return;
      }
      event.stopPropagation();
      event.preventDefault();
      const currentZoom = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.zoom", 1.0));
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextZoom = clampNumber(currentZoom * factor, 0.5, 2.0);
      setLayerTuningOverrideLeaf("overrides.portrait.zoom", Number(nextZoom.toFixed(3)));
    },
    [layerTuningDialog, setLayerTuningOverrideLeaf]
  );

  const handleLayerTuningPreviewKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const key = event.key;
      const isArrow = key === "ArrowLeft" || key === "ArrowRight" || key === "ArrowUp" || key === "ArrowDown";
      const isDelete = key === "Backspace" || key === "Delete";
      if (!isArrow && !isDelete) {
        return;
      }

      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase() ?? "";
      if (tag === "input" || tag === "textarea" || (target as any)?.isContentEditable) {
        return;
      }

      const rect = layerTuningPreviewRef.current?.getBoundingClientRect() ?? null;
      const width = rect?.width ?? layerTuningPreviewSize.width;
      const height = rect?.height ?? layerTuningPreviewSize.height;
      if (!width || !height) {
        return;
      }

      const stepPx = event.altKey ? 1 : event.shiftKey ? 12 : 4;
      const stepX = stepPx / width;
      const stepY = stepPx / height;

      const selected = layerTuningSelectedAssetRef.current;

      if (isDelete) {
        if (selected !== "element") {
          return;
        }
        const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
        if (!elementId) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        setLayerTuningElementsImmediate((current) => (current ?? []).filter((el) => String(el?.id ?? "") !== elementId));
        setLayerTuningSelectedElementId((current) => (current === elementId ? null : current));
        return;
      }

      let dxNorm = 0;
      let dyNorm = 0;
      if (key === "ArrowLeft") {
        dxNorm = -stepX;
      } else if (key === "ArrowRight") {
        dxNorm = stepX;
      } else if (key === "ArrowUp") {
        dyNorm = -stepY;
      } else if (key === "ArrowDown") {
        dyNorm = stepY;
      }
      if (!dxNorm && !dyNorm) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      if (selected === "element") {
        const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
        if (!elementId) {
          return;
        }
        setLayerTuningElementsImmediate((current) =>
          (current ?? []).map((el) => {
            if (String(el?.id ?? "") !== elementId) {
              return el;
            }
            const nextX = clampNumber(
              Number((el as any)?.x ?? 0.5) + dxNorm,
              LAYER_TUNING_ELEMENT_XY_MIN,
              LAYER_TUNING_ELEMENT_XY_MAX
            );
            const nextY = clampNumber(
              Number((el as any)?.y ?? 0.5) + dyNorm,
              LAYER_TUNING_ELEMENT_XY_MIN,
              LAYER_TUNING_ELEMENT_XY_MAX
            );
            return { ...el, x: Number(nextX.toFixed(4)), y: Number(nextY.toFixed(4)) };
          })
        );
        return;
      }

      if (selected === "text") {
        const forcedKey = String(layerTuningSelectedTextSlotRef.current ?? "").trim();
        const slotKey =
          forcedKey ||
          Object.keys(layerTuningTextLineSpecRef.current ?? {})
            .filter(Boolean)
            .sort((a, b) => a.localeCompare(b))[0] ||
          Object.keys(layerTuningTextSlotImages)
            .filter(Boolean)
            .sort((a, b) => a.localeCompare(b))[0] ||
          null;
        if (!slotKey) {
          return;
        }
        if (layerTuningSelectedTextSlotRef.current !== slotKey) {
          setLayerTuningSelectedTextSlot(slotKey);
        }
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
          const nextOffX = clampNumber(
            Number(existing.offset_x ?? 0) + dxNorm,
            LAYER_TUNING_OFFSET_MIN,
            LAYER_TUNING_OFFSET_MAX
          );
          const nextOffY = clampNumber(
            Number(existing.offset_y ?? 0) + dyNorm,
            LAYER_TUNING_OFFSET_MIN,
            LAYER_TUNING_OFFSET_MAX
          );
          next[slotKey] = { ...existing, offset_x: Number(nextOffX.toFixed(4)), offset_y: Number(nextOffY.toFixed(4)) };
          return next;
        });
        return;
      }

      const dialog = layerTuningDialogRef.current;
      if (!dialog) {
        return;
      }

      if (selected === "portrait") {
        const offX = Number(resolveLayerTuningLeafValue(dialog, "overrides.portrait.offset_x", 0.0));
        const offY = Number(resolveLayerTuningLeafValue(dialog, "overrides.portrait.offset_y", 0.0));
        const nextOffX = clampNumber(offX + dxNorm, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
        const nextOffY = clampNumber(offY + dyNorm, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX);
        scheduleLayerTuningPreviewPatch({
          "overrides.portrait.offset_x": Number(nextOffX.toFixed(4)),
          "overrides.portrait.offset_y": Number(nextOffY.toFixed(4)),
        });
        return;
      }

      const rawZoom = Number(resolveLayerTuningLeafValue(dialog, "overrides.bg_pan_zoom.zoom", 1.0));
      const zoom = Number.isFinite(rawZoom) ? rawZoom : 1.0;
      const maxDx = zoom > 1.0001 ? (width * (zoom - 1)) / 2 : width / 2;
      const maxDy = zoom > 1.0001 ? (height * (zoom - 1)) / 2 : height / 2;
      if (!Number.isFinite(maxDx) || !Number.isFinite(maxDy) || maxDx <= 0 || maxDy <= 0) {
        return;
      }
      const panX = Number(resolveLayerTuningLeafValue(dialog, "overrides.bg_pan_zoom.pan_x", 0.0));
      const panY = Number(resolveLayerTuningLeafValue(dialog, "overrides.bg_pan_zoom.pan_y", 0.0));
      const deltaPanX = -((dxNorm * width) / maxDx);
      const deltaPanY = -((dyNorm * height) / maxDy);
      const nextPanX = clampNumber(panX + deltaPanX, LAYER_TUNING_BG_PAN_MIN, LAYER_TUNING_BG_PAN_MAX);
      const nextPanY = clampNumber(panY + deltaPanY, LAYER_TUNING_BG_PAN_MIN, LAYER_TUNING_BG_PAN_MAX);
      const patch: Record<string, unknown> = {
        "overrides.bg_pan_zoom.pan_x": Number(nextPanX.toFixed(4)),
        "overrides.bg_pan_zoom.pan_y": Number(nextPanY.toFixed(4)),
      };
      scheduleLayerTuningPreviewPatch(patch);
    },
    [
      layerTuningPreviewSize.height,
      layerTuningPreviewSize.width,
      scheduleLayerTuningPreviewPatch,
      layerTuningTextSlotImages,
      setLayerTuningElementsImmediate,
      setLayerTuningTextLineSpecLinesImmediate,
    ]
  );

  const handleLayerTuningCommentChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setLayerTuningDialog((current) => {
      if (!current) {
        return current;
      }
      const stableKey = current.stable ?? "__default__";
      return {
        ...current,
        commentDraft: value,
        commentDraftByStable: { ...(current.commentDraftByStable ?? {}), [stableKey]: value },
      };
    });
  }, []);

  const applyLayerTuningPreset = useCallback((presetId: string) => {
    const id = (presetId ?? "").trim();
    if (!id) {
      return;
    }
    if (id === "text_big" || id === "text_small") {
      const factor = id === "text_big" ? 1.12 : 0.92;
      const slotKey =
        layerTuningSelectedTextSlotRef.current ??
        Object.keys(layerTuningTextLineSpecRef.current ?? {})
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b))[0] ??
        null;
      if (!slotKey) {
        return;
      }
      if (layerTuningSelectedTextSlotRef.current !== slotKey) {
        setLayerTuningSelectedTextSlot(slotKey);
      }
      setLayerTuningSelectedAsset("text");
      setLayerTuningTextLineSpecLinesImmediate((current) => {
        const next = { ...(current ?? {}) };
        const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
        const currentScale = Number(existing.scale ?? 1);
        const nextScale = clampNumber(currentScale * factor, 0.25, 4);
        next[slotKey] = { ...existing, scale: Number(nextScale.toFixed(3)) };
        return next;
      });
      return;
    }
    const channel = layerTuningDialog?.channel ?? null;
    const presetTable: Record<string, { label: string; leaf: Record<string, unknown>; channel?: string | null }> = {
      reset_all: { label: "リセット（全部）", leaf: {} },
      bg_bright: {
        label: "背景: 明るめ",
        leaf: { "overrides.bg_enhance.brightness": 1.25, "overrides.bg_enhance.gamma": 0.95 },
      },
      bg_dark: {
        label: "背景: 暗め",
        leaf: { "overrides.bg_enhance.brightness": 0.92, "overrides.bg_enhance.gamma": 1.05 },
      },
      bg_vivid: { label: "背景: 彩度UP", leaf: { "overrides.bg_enhance.color": 1.18 } },
      bg_zoom_in: { label: "背景: 少しズーム", leaf: { "overrides.bg_pan_zoom.zoom": 1.4 } },
      portrait_zoom: { label: "肖像: アップ", leaf: { "overrides.portrait.zoom": 1.25 } },
      portrait_bright: {
        label: "肖像: 明るく",
        leaf: { "overrides.portrait.fg_brightness": 1.32, "overrides.portrait.fg_contrast": 1.12 },
      },
    };
    const preset = presetTable[id];
    if (!preset) {
      return;
    }
    if (preset.channel && channel && preset.channel !== channel) {
      return;
    }
    if (id === "reset_all") {
      mergeLayerTuningOverridesLeaf({}, { reset: true });
      return;
    }
    mergeLayerTuningOverridesLeaf(preset.leaf);
  }, [layerTuningDialog?.channel, mergeLayerTuningOverridesLeaf, setLayerTuningTextLineSpecLinesImmediate]);

  const updateLayerTuningSelectedElement = useCallback((patch: Partial<ThumbnailElementSpec>) => {
    const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
    if (!elementId) {
      return;
    }
    setLayerTuningElementsImmediate((current) =>
      (current ?? []).map((el) => {
        if (String(el?.id ?? "") !== elementId) {
          return el;
        }
        return { ...el, ...patch };
      })
    );
  }, [setLayerTuningElementsImmediate]);

  const addLayerTuningElement = useCallback(
    (kind: "rect" | "circle") => {
      const id = createLocalId("el");
      const next: ThumbnailElementSpec = {
        id,
        kind,
        layer: "above_portrait",
        z: 0,
        x: 0.5,
        y: 0.5,
        w: 0.22,
        h: 0.18,
        rotation_deg: 0,
        opacity: 0.9,
        fill: kind === "circle" ? "#ffffff" : "#ffffff",
        stroke: null,
      };
      setLayerTuningElementsImmediate((current) => [...(current ?? []), next]);
      setLayerTuningSelectedElementId(id);
      setLayerTuningSelectedAsset("element");
    },
    [setLayerTuningElementsImmediate]
  );

  const duplicateLayerTuningSelectedElement = useCallback(() => {
    const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
    if (!elementId) {
      return;
    }
    const elements = layerTuningElementsRef.current ?? [];
    const base = elements.find((el) => String(el?.id ?? "") === elementId);
    if (!base) {
      return;
    }
    const id = createLocalId("el");
    const clone: ThumbnailElementSpec = {
      ...base,
      id,
      x: clampNumber(
        Number((base as any).x ?? 0.5) + 0.02,
        LAYER_TUNING_ELEMENT_XY_MIN,
        LAYER_TUNING_ELEMENT_XY_MAX
      ),
      y: clampNumber(
        Number((base as any).y ?? 0.5) + 0.02,
        LAYER_TUNING_ELEMENT_XY_MIN,
        LAYER_TUNING_ELEMENT_XY_MAX
      ),
      z: Number((base as any).z ?? 0) + 1,
    };
    setLayerTuningElementsImmediate((current) => [...(current ?? []), clone]);
    setLayerTuningSelectedElementId(id);
    setLayerTuningSelectedAsset("element");
  }, [setLayerTuningElementsImmediate]);

  const deleteLayerTuningSelectedElement = useCallback(() => {
    const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
    if (!elementId) {
      return;
    }
    setLayerTuningElementsImmediate((current) => (current ?? []).filter((el) => String(el?.id ?? "") !== elementId));
    setLayerTuningSelectedElementId((current) => (current === elementId ? null : current));
  }, [setLayerTuningElementsImmediate]);

  const moveLayerTuningSelectedElementZ = useCallback((direction: "front" | "back") => {
    const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
    if (!elementId) {
      return;
    }
    const elements = layerTuningElementsRef.current ?? [];
    const current = elements.find((el) => String(el?.id ?? "") === elementId);
    if (!current) {
      return;
    }
    const layer = String((current as any).layer ?? "above_portrait");
    const sameLayer = elements.filter((el) => String((el as any).layer ?? "above_portrait") === layer);
    const zValues = sameLayer.map((el) => Number((el as any).z ?? 0)).filter((v) => Number.isFinite(v));
    const currentZ = Number((current as any).z ?? 0);
    const nextZ =
      direction === "front"
        ? (zValues.length ? Math.max(...zValues) : 0) + 1
        : (zValues.length ? Math.min(...zValues) : 0) - 1;
    updateLayerTuningSelectedElement({ z: Number.isFinite(nextZ) ? nextZ : currentZ });
  }, [updateLayerTuningSelectedElement]);

  const alignLayerTuningSelected = useCallback(
    (align: "left" | "center" | "right" | "top" | "middle" | "bottom") => {
      const dialog = layerTuningDialogRef.current;
      if (!dialog) {
        return;
      }
      const selected = layerTuningSelectedAssetRef.current;
      if (selected === "element") {
        const elementId = String(layerTuningSelectedElementIdRef.current ?? "").trim();
        if (!elementId) {
          return;
        }
        const elements = layerTuningElementsRef.current ?? [];
        const el = elements.find((item) => String(item?.id ?? "") === elementId);
        if (!el) {
          return;
        }
        const w = clampNumber(Number((el as any).w ?? 0.2), 0.01, 4);
        const h = clampNumber(Number((el as any).h ?? 0.2), 0.01, 4);
        const patch: Partial<ThumbnailElementSpec> = {};
        if (align === "left") {
          patch.x = clampNumber(w / 2, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        } else if (align === "center") {
          patch.x = 0.5;
        } else if (align === "right") {
          patch.x = clampNumber(1.0 - w / 2, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        } else if (align === "top") {
          patch.y = clampNumber(h / 2, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        } else if (align === "middle") {
          patch.y = 0.5;
        } else if (align === "bottom") {
          patch.y = clampNumber(1.0 - h / 2, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX);
        }
        if (Object.keys(patch).length) {
          updateLayerTuningSelectedElement(patch);
        }
        return;
      }

      if (selected === "text") {
        const slotKey = String(layerTuningSelectedTextSlotRef.current ?? "").trim();
        const box = slotKey ? layerTuningTextSlotBoxesRef.current?.[slotKey] : null;
        if (!slotKey || !Array.isArray(box) || box.length !== 4) {
          return;
        }
        const left0 = Number(box[0]);
        const top0 = Number(box[1]);
        const w0 = Number(box[2]);
        const h0 = Number(box[3]);
        if (![left0, top0, w0, h0].every((v) => Number.isFinite(v))) {
          return;
        }
        setLayerTuningTextLineSpecLinesImmediate((current) => {
          const next = { ...(current ?? {}) };
          const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
          let ox = Number(existing.offset_x ?? 0);
          let oy = Number(existing.offset_y ?? 0);
          if (align === "left") {
            ox = -left0;
          } else if (align === "center") {
            ox = 0.5 - (left0 + w0 / 2);
          } else if (align === "right") {
            ox = 1.0 - (left0 + w0);
          } else if (align === "top") {
            oy = -top0;
          } else if (align === "middle") {
            oy = 0.5 - (top0 + h0 / 2);
          } else if (align === "bottom") {
            oy = 1.0 - (top0 + h0);
          }
          next[slotKey] = {
            ...existing,
            offset_x: Number(clampNumber(ox, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX).toFixed(4)),
            offset_y: Number(clampNumber(oy, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX).toFixed(4)),
          };
          return next;
        });
        return;
      }

      if (selected === "portrait") {
        const rawPortraitBox = (dialog.context as any)?.portrait_dest_box_norm;
        const portraitBox =
          Array.isArray(rawPortraitBox) && rawPortraitBox.length === 4
            ? rawPortraitBox.map((v: any) => Number(v))
            : [0.29, 0.06, 0.42, 0.76];
        const left0 = Number(portraitBox[0]);
        const top0 = Number(portraitBox[1]);
        const w0 = Number(portraitBox[2]);
        const h0 = Number(portraitBox[3]);
        if (![left0, top0, w0, h0].every((v) => Number.isFinite(v))) {
          return;
        }
        const curX = Number(resolveLayerTuningLeafValue(dialog, "overrides.portrait.offset_x", 0.0));
        const curY = Number(resolveLayerTuningLeafValue(dialog, "overrides.portrait.offset_y", 0.0));
        let ox = Number.isFinite(curX) ? curX : 0.0;
        let oy = Number.isFinite(curY) ? curY : 0.0;
        if (align === "left") {
          ox = -left0;
        } else if (align === "center") {
          ox = 0.5 - (left0 + w0 / 2);
        } else if (align === "right") {
          ox = 1.0 - (left0 + w0);
        } else if (align === "top") {
          oy = -top0;
        } else if (align === "middle") {
          oy = 0.5 - (top0 + h0 / 2);
        } else if (align === "bottom") {
          oy = 1.0 - (top0 + h0);
        }
        mergeLayerTuningOverridesLeaf({
          "overrides.portrait.offset_x": Number(clampNumber(ox, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX).toFixed(4)),
          "overrides.portrait.offset_y": Number(clampNumber(oy, LAYER_TUNING_OFFSET_MIN, LAYER_TUNING_OFFSET_MAX).toFixed(4)),
        });
      }
    },
    [mergeLayerTuningOverridesLeaf, setLayerTuningTextLineSpecLinesImmediate, updateLayerTuningSelectedElement]
  );

  const handleLayerTuningElementUploadChange = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = input.files ? Array.from(input.files) : [];
    input.value = "";
    const dialog = layerTuningDialogRef.current;
    if (!dialog || !files.length) {
      return;
    }
    try {
      const assets = await uploadThumbnailLibraryAssets(dialog.channel, files);
      const asset = assets[0];
      if (!asset) {
        return;
      }
      const id = createLocalId("el");
      const next: ThumbnailElementSpec = {
        id,
        kind: "image",
        layer: "above_portrait",
        z: 0,
        x: 0.5,
        y: 0.5,
        w: 0.28,
        h: 0.28,
        rotation_deg: 0,
        opacity: 1,
        src_path: asset.relative_path,
      };
      setLayerTuningElementsImmediate((current) => [...(current ?? []), next]);
      setLayerTuningSelectedElementId(id);
      setLayerTuningSelectedAsset("element");
      setLayerTuningElementsStatus((current) => ({ ...current, error: null }));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setLayerTuningElementsStatus({ loading: false, error: message });
    }
  }, [setLayerTuningElementsImmediate]);

  const handleReplaceLayerTuningAsset = useCallback(
    async (slot: string, file: File) => {
      const dialog = layerTuningDialogRef.current;
      if (!dialog) {
        return;
      }
      const slotKey = String(slot ?? "").trim();
      if (!slotKey) {
        return;
      }
      const cardKey = String(dialog.cardKey || dialog.projectKey || "").trim() || dialog.projectKey;
      setProjectFeedback(cardKey, null);

      try {
        const result = await replaceThumbnailVideoAsset(dialog.channel, dialog.video, slotKey, file);
        const cacheBust = String(Date.now());

        if (slotKey === "10_bg") {
          setLayerTuningBgPreviewSrc(
            withCacheBust(resolveApiUrl(`/thumbnails/assets/${dialog.channel}/${dialog.video}/10_bg.png`), cacheBust)
          );
        } else if (slotKey === "20_portrait") {
          setLayerTuningPortraitPreviewSrc(
            withCacheBust(resolveApiUrl(`/thumbnails/assets/${dialog.channel}/${dialog.video}/20_portrait.png`), cacheBust)
          );
        }

        await fetchData({ silent: true });
        const url = resolveApiUrl(result.public_url);
        setProjectFeedback(cardKey, {
          type: "success",
          message: (
            <span>
              差し替えました（{slotKey}）。{" "}
              <a href={url} target="_blank" rel="noreferrer">
                開く
              </a>
            </span>
          ),
          timestamp: Date.now(),
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(cardKey, { type: "error", message, timestamp: Date.now() });
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleLayerTuningPreviewDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    const types = Array.from(event.dataTransfer?.types ?? []);
    if (!types.includes("Files")) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  const handleLayerTuningPreviewDragEnter = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    const types = Array.from(event.dataTransfer?.types ?? []);
    if (!types.includes("Files")) {
      return;
    }
    layerTuningPreviewDropDepthRef.current += 1;
    setLayerTuningPreviewDropActive(true);
  }, []);

  const handleLayerTuningPreviewDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    const types = Array.from(event.dataTransfer?.types ?? []);
    if (!types.includes("Files")) {
      return;
    }
    layerTuningPreviewDropDepthRef.current = Math.max(0, layerTuningPreviewDropDepthRef.current - 1);
    if (layerTuningPreviewDropDepthRef.current === 0) {
      setLayerTuningPreviewDropActive(false);
    }
  }, []);

  const handleLayerTuningPreviewDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const file = event.dataTransfer?.files?.[0] ?? null;
      if (!file) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      layerTuningPreviewDropDepthRef.current = 0;
      setLayerTuningPreviewDropActive(false);

      const dialog = layerTuningDialogRef.current;
      if (!dialog) {
        return;
      }

      const selected = layerTuningSelectedAssetRef.current;
      const portraitAvailable = Boolean(dialog.context?.portrait_available);
      const cardKey = String(dialog.cardKey || dialog.projectKey || "").trim() || dialog.projectKey;

      let slot: string | null = null;
      if (event.shiftKey) {
        slot = dialog.stable ? dialog.stable : "00_thumb";
      } else if (selected === "bg") {
        slot = "10_bg";
      } else if (selected === "portrait") {
        slot = portraitAvailable ? "20_portrait" : null;
      }

      if (!slot) {
        setProjectFeedback(cardKey, {
          type: "error",
          message: "背景/肖像を選択してドロップしてください（Shiftで出力）",
          timestamp: Date.now(),
        });
        return;
      }

      void handleReplaceLayerTuningAsset(slot, file);
    },
    [handleReplaceLayerTuningAsset, setProjectFeedback]
  );

		  const handleSaveLayerTuning = useCallback(
		    async (mode: "save" | "save_and_build") => {
		      if (!layerTuningDialog) {
		        return;
		      }
	        const pendingPreviewPatch = flushLayerTuningPreviewPatch();
		      const { projectKey, cardKey, channel, video, stable, allowGenerate, regenBg, outputMode } = layerTuningDialog;
          const stableId = normalizeThumbnailStableId(stable);
	        const overridesLeafForSave = (() => {
	          const base = { ...(layerTuningDialog.overridesLeaf ?? {}) };
	          if (!pendingPreviewPatch) {
	            return base;
          }
          Object.entries(pendingPreviewPatch).forEach(([rawKey, rawValue]) => {
            const key = String(rawKey ?? "").trim();
            if (!key) {
              return;
            }
            if (rawValue === null || rawValue === undefined || rawValue === "") {
              delete base[key];
            } else {
              base[key] = rawValue;
            }
          });
          return base;
        })();
	      const overrides = leafOverridesToThumbSpecOverrides(overridesLeafForSave);

      setLayerTuningDialog((current) => {
        if (!current || current.projectKey !== projectKey) {
          return current;
        }
        return { ...current, saving: true, building: mode === "save_and_build", error: undefined };
      });

		      try {
		        const textLineSpecLines = layerTuningTextLineSpecRef.current ?? {};
		        if (Object.keys(textLineSpecLines).length > 0) {
		          await updateThumbnailTextLineSpec(channel, video, stableId, textLineSpecLines);
		        }
	          const elements = layerTuningElementsRef.current ?? [];
	          await updateThumbnailElementsSpec(channel, video, stableId, elements);
		        await updateThumbnailThumbSpec(channel, video, overrides, stableId ? { stable: stableId } : undefined);
	
		        if (mode === "save_and_build") {
		          if (stableId) {
		            await buildThumbnailTwoUp(channel, video, {
		              allow_generate: Boolean(allowGenerate),
		              regen_bg: false,
		              output_mode: outputMode,
	            });
	          } else {
	            await buildThumbnailLayerSpecs(channel, video, {
	              allow_generate: Boolean(allowGenerate),
	              regen_bg: Boolean(regenBg),
	              output_mode: outputMode,
	            });
	          }
	        }

			        await fetchData({ silent: true });
			        const previewToken = String(Date.now());
			        const previewUrl = withCacheBust(
			          resolveApiUrl(`/thumbnails/assets/${channel}/${video}/${stableId ? `${stableId}.png` : "00_thumb.png"}`),
			          previewToken
			        );
			        const previewUrl1 = withCacheBust(
			          resolveApiUrl(`/thumbnails/assets/${channel}/${video}/00_thumb_1.png`),
			          previewToken
			        );
			        const previewUrl2 = withCacheBust(
			          resolveApiUrl(`/thumbnails/assets/${channel}/${video}/00_thumb_2.png`),
			          previewToken
			        );
		        setProjectFeedback(cardKey, {
		          type: "success",
		          message: (
		            <span>
		              {mode === "save_and_build" ? "保存して再生成しました。" : "保存しました（設定のみ / PNGは未更新）。"}
		              {mode === "save_and_build" ? (
		                <>
		                  {" "}
		                  {stableId ? (
			                  <>
			                    <a href={previewUrl1} target="_blank" rel="noreferrer">
			                      00_thumb_1
		                    </a>
		                    {" / "}
		                    <a href={previewUrl2} target="_blank" rel="noreferrer">
		                      00_thumb_2
		                    </a>
		                  </>
		                ) : (
		                  <a href={previewUrl} target="_blank" rel="noreferrer">
		                    プレビュー
		                  </a>
		                )}
		                </>
		              ) : null}
		            </span>
		          ),
		          timestamp: Date.now(),
		        });
		        setLayerTuningDialog((current) => {
		          if (!current || current.projectKey !== projectKey) {
		            return current;
		          }
		          return { ...current, saving: false, building: false, error: undefined };
		        });
	      } catch (error) {
	        const message = error instanceof Error ? error.message : String(error);
	        setLayerTuningDialog((current) => {
	          if (!current || current.projectKey !== projectKey) {
	            return current;
	          }
          return { ...current, saving: false, building: false, error: message };
        });
        setProjectFeedback(cardKey, { type: "error", message, timestamp: Date.now() });
      }
    },
    [fetchData, flushLayerTuningPreviewPatch, layerTuningDialog, setProjectFeedback]
  );

  const handleStartNewVariant = useCallback(() => {
    if (!filteredProjects.length) {
      return;
    }
    handleOpenVariantForm(filteredProjects[0]);
  }, [filteredProjects, handleOpenVariantForm]);

  const toggleProjectVariants = useCallback((projectKey: string) => {
    setExpandedProjectKey((current) => (current === projectKey ? null : projectKey));
  }, []);

  const handleCancelVariantForm = useCallback(() => {
    setVariantForm(null);
  }, []);

  const handleVariantFormFieldChange = useCallback(
    (field: keyof Omit<VariantFormState, "projectKey">, value: string | boolean) => {
      setVariantForm((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          [field]: value,
        };
      });
    },
    []
  );

  const handleVariantFormSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, project: ThumbnailProject) => {
      event.preventDefault();
      const projectKey = getProjectKey(project);
      if (!variantForm || variantForm.projectKey !== projectKey) {
        return;
      }
      const label = variantForm.label.trim();
      const imageUrl = variantForm.imageUrl.trim();
      const imagePath = variantForm.imagePath.trim();
      if (!label) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "サムネイル案の名前を入力してください。",
          timestamp: Date.now(),
        });
        return;
      }
      if (!imageUrl && !imagePath) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "画像URLまたは画像パスのいずれかを入力してください。",
          timestamp: Date.now(),
        });
        return;
      }
      if (imagePath && /\/+$/.test(imagePath)) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "画像パスにはファイル名まで指定してください。",
          timestamp: Date.now(),
        });
        return;
      }
      const tags = variantForm.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await createThumbnailVariant(project.channel, project.video, {
          label,
          image_url: imageUrl || undefined,
          image_path: imagePath || undefined,
          status: variantForm.status,
          notes: variantForm.notes.trim() || undefined,
          tags,
          prompt: variantForm.prompt.trim() || undefined,
          make_selected: variantForm.makeSelected,
        });
        setVariantForm(null);
        setProjectFeedback(projectKey, {
          type: "success",
          message: "サムネイル案を登録しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback, variantForm]
  );

  const handleOpenProjectForm = useCallback((project: ThumbnailProject) => {
    const projectKey = getProjectKey(project);
    setProjectForm({
      projectKey,
      owner: project.owner ?? "",
      summary: project.summary ?? "",
      notes: project.notes ?? "",
      tags: (project.tags ?? []).join(", "),
      dueAt: project.due_at ?? "",
    });
    setVariantForm((current) => (current?.projectKey === projectKey ? current : null));
    setProjectFeedback(projectKey, null);
  }, [setProjectFeedback]);

  const handleCancelProjectForm = useCallback(() => {
    setProjectForm(null);
  }, []);

  const handleOpenPlanningDialog = useCallback((project: ThumbnailProject, variant?: ThumbnailVariant) => {
    const projectKey = getProjectKey(project);
    const numericVideo = normalizeVideoInput(project.video);
    const variantTags = variant?.tags ?? [];
    const primaryTitle = project.title ?? project.sheet_title ?? "";
    const variantLabel = variant?.label ?? variant?.id ?? "";
    setPlanningDialog({
      projectKey,
      channel: project.channel,
      projectTitle: primaryTitle,
      variantLabel: variantLabel || undefined,
      videoNumber: numericVideo,
      no: numericVideo,
      title: variantLabel || primaryTitle || "新規企画",
      thumbnailUpper: "",
      thumbnailLower: "",
      thumbnailTitle: variantLabel || "",
      thumbnailPrompt: variant?.notes ?? project.summary ?? "",
      dallePrompt: "",
      conceptIntent: project.summary ?? "",
      outlineNotes: variant?.notes ?? project.notes ?? "",
      primaryTag: variantTags[0] ?? "",
      secondaryTag: variantTags[1] ?? "",
      lifeScene: "",
      keyConcept: "",
      benefit: "",
      analogy: "",
      descriptionLead: project.summary ?? "",
      descriptionTakeaways: "",
      saving: false,
      error: undefined,
    });
  }, []);

  const handleClosePlanningDialog = useCallback(() => {
    setPlanningDialog(null);
  }, []);

  const handlePlanningFieldChange = useCallback((field: PlanningEditableField, value: string) => {
    setPlanningDialog((current) => (current ? { ...current, [field]: value } : current));
  }, []);

  const handlePlanningSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (!planningDialog) {
        return;
      }
      const trimmedVideo = planningDialog.videoNumber.trim();
      if (!trimmedVideo) {
        setPlanningDialog((current) => (current ? { ...current, error: "動画番号を入力してください。" } : current));
        return;
      }
      setPlanningDialog((current) => (current ? { ...current, saving: true, error: undefined } : current));

      const toFieldValue = (value: string): string | null => {
        const trimmed = value.trim();
        return trimmed ? trimmed : null;
      };

      const fieldsPayload: Record<string, string | null> = {
        thumbnail_upper: toFieldValue(planningDialog.thumbnailUpper),
        thumbnail_lower: toFieldValue(planningDialog.thumbnailLower),
        thumbnail_title: toFieldValue(planningDialog.thumbnailTitle),
        thumbnail_prompt: toFieldValue(planningDialog.thumbnailPrompt),
        dalle_prompt: toFieldValue(planningDialog.dallePrompt),
        concept_intent: toFieldValue(planningDialog.conceptIntent),
        outline_notes: toFieldValue(planningDialog.outlineNotes),
        primary_pain_tag: toFieldValue(planningDialog.primaryTag),
        secondary_pain_tag: toFieldValue(planningDialog.secondaryTag),
        life_scene: toFieldValue(planningDialog.lifeScene),
        key_concept: toFieldValue(planningDialog.keyConcept),
        benefit_blurb: toFieldValue(planningDialog.benefit),
        analogy_image: toFieldValue(planningDialog.analogy),
        description_lead: toFieldValue(planningDialog.descriptionLead),
        description_takeaways: toFieldValue(planningDialog.descriptionTakeaways),
      };

      const filteredFields: Record<string, string | null> = {};
      Object.entries(fieldsPayload).forEach(([key, val]) => {
        if (val !== null) {
          filteredFields[key] = val;
        }
      });

      const payload: PlanningCreatePayload = {
        channel: planningDialog.channel,
        video_number: trimmedVideo,
        title: planningDialog.title.trim(),
        no: planningDialog.no.trim() || undefined,
        creation_flag: "3",
        progress: "topic_research: pending",
        fields: Object.keys(filteredFields).length > 0 ? filteredFields : undefined,
      };

      try {
        const result = await createPlanningRow(payload);
        const scriptFactoryUrl = `/projects?channel=${encodeURIComponent(result.channel ?? planningDialog.channel)}&video=${encodeURIComponent(result.video_number)}`;
        setProjectFeedback(planningDialog.projectKey, {
          type: "success",
          message: (
            <>
              {`${result.channel}-${result.video_number} の企画行を作成しました。`}
              <Link to={scriptFactoryUrl} className="thumbnail-card__feedback-link">
                ScriptFactoryで確認
              </Link>
            </>
          ),
          timestamp: Date.now(),
        });
        setPlanningDialog(null);
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setPlanningDialog((current) => (current ? { ...current, saving: false, error: message } : current));
      }
    },
    [fetchData, planningDialog, setProjectFeedback]
  );
  const handleProjectFormChange = useCallback(
    (field: keyof Omit<ProjectFormState, "projectKey">, value: string) => {
      setProjectForm((current) => {
        if (!current) {
          return current;
        }
        return { ...current, [field]: value };
      });
    },
    []
  );

  const handleProjectFormSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>, project: ThumbnailProject) => {
      event.preventDefault();
      const projectKey = getProjectKey(project);
      if (!projectForm || projectForm.projectKey !== projectKey) {
        return;
      }
      const tags = projectForm.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);

      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, {
          owner: projectForm.owner.trim() || null,
          summary: projectForm.summary.trim() || null,
          notes: projectForm.notes.trim() || null,
          tags,
          due_at: projectForm.dueAt.trim() || null,
        });
        setProjectForm(null);
        setProjectFeedback(projectKey, {
          type: "success",
          message: "案件情報を更新しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, projectForm, setProjectFeedback]
  );

  const handleStatusChange = useCallback(
    async (project: ThumbnailProject, status: ThumbnailProjectStatus) => {
      const projectKey = getProjectKey(project);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, { status });
        setProjectFeedback(projectKey, {
          type: "success",
          message: "ステータスを更新しました。",
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleSelectVariant = useCallback(
    async (project: ThumbnailProject, variant: ThumbnailVariant) => {
      const projectKey = getProjectKey(project);
      setUpdatingProjectId(projectKey);
      setProjectFeedback(projectKey, null);
      try {
        await updateThumbnailProject(project.channel, project.video, {
          selected_variant_id: variant.id,
        });
        setProjectFeedback(projectKey, {
          type: "success",
          message: `「${variant.label ?? variant.id}」を採用中に設定しました。`,
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleDropzoneFiles = useCallback(
    async (project: ThumbnailProject, fileList: FileList | File[]) => {
      const projectKey = getProjectKey(project);
      const rawFiles = Array.isArray(fileList) ? fileList : Array.from(fileList);
      const validFiles = rawFiles.filter(isSupportedThumbnailFile);
      if (validFiles.length === 0) {
        setProjectFeedback(projectKey, {
          type: "error",
          message: "PNG / JPG / WEBP の画像をアップロードしてください。",
          timestamp: Date.now(),
        });
        return;
      }
      setProjectFeedback(projectKey, null);
      setActiveDropProject((current) => (current === projectKey ? null : current));
      setVariantForm((current) => (current?.projectKey === projectKey ? null : current));
      setUpdatingProjectId(projectKey);
      try {
        let uploaded = 0;
        for (const file of validFiles) {
          const baseName = file.name.replace(/\.[^.]+$/, "");
          const labelCandidate = baseName.replace(/[_-]+/g, " ").trim() || `案 ${uploaded + 1}`;
          await uploadThumbnailVariantAsset(project.channel, project.video, {
            file,
            label: labelCandidate.slice(0, 120),
            makeSelected: project.variants.length === 0 && uploaded === 0,
          });
          uploaded += 1;
        }
        setProjectFeedback(projectKey, {
          type: "success",
          message: `${uploaded} 件のサムネイル案を追加しました。`,
          timestamp: Date.now(),
        });
        await fetchData({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setProjectFeedback(projectKey, {
          type: "error",
          message,
          timestamp: Date.now(),
        });
      } finally {
        setUpdatingProjectId(null);
      }
    },
    [fetchData, setProjectFeedback]
  );

  const handleDropzoneInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>, project: ThumbnailProject) => {
      const { files } = event.target;
      if (!files || files.length === 0) {
        return;
      }
      const projectKey = getProjectKey(project);
      handleDropzoneFiles(project, files).finally(() => {
        const input = dropzoneFileInputs.current.get(projectKey);
        if (input) {
          input.value = "";
        }
      });
    },
    [handleDropzoneFiles]
  );

  const handleDropzoneClick = useCallback((projectKey: string, disabled: boolean) => {
    if (disabled) {
      return;
    }
    const input = dropzoneFileInputs.current.get(projectKey);
    if (input) {
      input.click();
    }
  }, []);

  const handleDropzoneDragEnter = useCallback(
    (event: DragEvent<HTMLElement>, projectKey: string, disabled: boolean) => {
      event.preventDefault();
      if (disabled) {
        event.dataTransfer.dropEffect = "none";
        return;
      }
      event.dataTransfer.dropEffect = "copy";
      setActiveDropProject(projectKey);
    },
    []
  );

  const handleDropzoneDragOver = useCallback((event: DragEvent<HTMLElement>, disabled: boolean) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = disabled ? "none" : "copy";
  }, []);

  const handleDropzoneDragLeave = useCallback(
    (event: DragEvent<HTMLElement>, projectKey: string) => {
      event.preventDefault();
      const related = event.relatedTarget as Node | null;
      if (related && event.currentTarget.contains(related)) {
        return;
      }
      setActiveDropProject((current) => (current === projectKey ? null : current));
    },
    []
  );

  const handleDropzoneDrop = useCallback(
    (event: DragEvent<HTMLElement>, project: ThumbnailProject, disabled: boolean) => {
      event.preventDefault();
      if (disabled) {
        return;
      }
      setActiveDropProject(null);
      if (event.dataTransfer?.files && event.dataTransfer.files.length > 0) {
        void handleDropzoneFiles(project, event.dataTransfer.files);
      }
    },
    [handleDropzoneFiles]
  );

  const qcLibraryAssets = useMemo(() => {
    const assets = libraryAssets.filter(isQcLibraryAsset).slice();
    assets.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    return assets;
  }, [libraryAssets]);

  const visibleLibraryAssets = useMemo(() => {
    const assets = libraryAssets.filter((asset) => !isQcLibraryAsset(asset)).slice();
    assets.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    return assets;
  }, [libraryAssets]);

  const qcPanel = activeChannel ? (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>QC（コンタクトシート）</h3>
          <p>
            <code>python scripts/thumbnails/build.py qc</code> が生成する contactsheet を表示します（例:
            <code>workspaces/thumbnails/assets/{activeChannel.channel}/library/qc/contactsheet.png</code>）。
          </p>
        </div>
        <button
          type="button"
          className="thumbnail-refresh-button"
          onClick={() => {
            handleLibraryRefresh();
            loadQcNotes(activeChannel.channel, { silent: true }).catch(() => {
              // loadQcNotes 内でエラー表示済み
            });
          }}
          disabled={libraryLoading}
        >
          {libraryLoading ? "読込中…" : "QC再読み込み"}
        </button>
      </div>
      {libraryError ? <p className="thumbnail-library__alert">{libraryError}</p> : null}
      {qcNotesError ? <p className="thumbnail-library__alert">{qcNotesError}</p> : null}
      {qcLibraryAssets.length === 0 && !libraryError ? (
        <p className="thumbnail-library__placeholder">
          {libraryLoading ? "QC画像を読み込んでいます…" : "QC画像が見つかりませんでした。"}
        </p>
      ) : null}
      {qcLibraryAssets.length > 0 ? (
        <div className="thumbnail-library-grid thumbnail-library-grid--qc">
          {qcLibraryAssets.map((asset) => {
            const previewUrlBase = resolveApiUrl(asset.public_url);
            const previewUrl = withCacheBust(previewUrlBase, asset.updated_at);
            const relativePath = asset.relative_path;
            const currentNote = qcNotes[relativePath] ?? "";
            const draftNote = qcNotesDraft[relativePath];
            const noteValue = draftNote !== undefined ? draftNote : currentNote;
            const noteDirty = draftNote !== undefined && draftNote !== currentNote;
            const noteSaving = qcNotesSaving[relativePath] ?? false;
            const assetPath = `${THUMBNAIL_ASSET_BASE_PATH}/${activeChannel.channel}/${relativePath}`;
            return (
              <article key={asset.id} className="thumbnail-library-card thumbnail-library-card--qc">
                <div className="thumbnail-library-card__preview">
		                  <a href={previewUrl} target="_blank" rel="noreferrer" title="別タブで表示">
		                    <img src={previewUrl} alt={asset.file_name} loading="lazy" draggable={false} />
		                  </a>
                </div>
                <div className="thumbnail-library-card__meta">
                  <strong title={asset.file_name}>{asset.file_name}</strong>
                  <div className="thumbnail-library-card__meta-info">{asset.relative_path}</div>
                  <div className="thumbnail-library-card__meta-info">
                    {formatBytes(asset.size_bytes)}・{formatDate(asset.updated_at)}
                  </div>
                  <label className="thumbnail-library-card__describe">
                    <span>コメント</span>
                    <textarea
                      rows={2}
                      value={noteValue}
                      onChange={(event) => handleQcNotesChange(relativePath, event.target.value)}
                      onBlur={() => {
                        if (noteDirty && !noteSaving) {
                          void handleQcNotesSave(relativePath);
                        }
                      }}
                      placeholder="指示・メモ（例: 021-030 はフォント小さめに、帯を明るく）"
                    />
                  </label>
                  <div className="thumbnail-library-card__actions">
                    <button type="button" className="btn btn--ghost" onClick={() => handleCopyAssetPath(assetPath)}>
                      パスコピー
                    </button>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void handleQcNotesSave(relativePath)}
                      disabled={noteSaving || !noteDirty}
                    >
                      {noteSaving ? "保存中…" : noteDirty ? "コメント保存" : "保存済み"}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  ) : (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>QC（コンタクトシート）</h3>
          <p>チャンネルを選択するとQC画像が表示されます。</p>
        </div>
      </div>
    </section>
  );

  const libraryPanel = activeChannel ? (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>参考サムネ登録</h3>
          <p>
            {activeChannel.library_path
              ? `${activeChannel.library_path} 配下の PNG / JPG / WEBP が一覧に並びます。`
              : "PNG / JPG / WEBP をドラッグ & ドロップ / URL で追加できます。"}
          </p>
          <p className="muted small-text" style={{ marginTop: "6px" }}>
            QC（コンタクトシート）は <strong>QCタブ</strong> に集約しました。
          </p>
        </div>
        <button type="button" className="thumbnail-refresh-button" onClick={handleLibraryRefresh} disabled={libraryLoading}>
          {libraryLoading ? "読込中…" : "ライブラリ再読み込み"}
        </button>
      </div>
      <div className="thumbnail-library-panel__cards">
        <div className="thumbnail-library-panel__card">
          <h4>ローカルから追加</h4>
          <p>PNG / JPG / WEBP をまとめてドラッグ & ドロップできます。</p>
          <button
            type="button"
            className="thumbnail-upload-button"
            onClick={handleLibraryUploadClick}
            disabled={!activeChannel.channel || libraryUploadStatus.pending}
          >
            {libraryUploadStatus.pending ? "アップロード中…" : "ローカル画像を選ぶ"}
          </button>
          <input
            ref={libraryUploadInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            hidden
            onChange={handleLibraryUploadChange}
          />
        </div>
        <div className="thumbnail-library-panel__card">
          <h4>URLを取り込む</h4>
          <form className="thumbnail-library__import" onSubmit={handleLibraryImportSubmit}>
            <label>
              <span>画像URL</span>
              <input
                type="url"
                placeholder="https://example.com/thumbnail.jpg"
                value={libraryImportUrl}
                onChange={(event) => {
                  setLibraryImportUrl(event.target.value);
                  setLibraryImportStatus((current) => ({ ...current, error: null, success: null }));
                }}
                required
              />
            </label>
            <label>
              <span>保存名 (任意)</span>
              <input
                type="text"
                placeholder="my-thumbnail.png"
                value={libraryImportName}
                onChange={(event) => {
                  setLibraryImportName(event.target.value);
                  setLibraryImportStatus((current) => ({ ...current, error: null, success: null }));
                }}
              />
            </label>
            <button type="submit" disabled={libraryImportStatus.pending}>
              {libraryImportStatus.pending ? "取り込み中…" : "ライブラリへ追加"}
            </button>
          </form>
        </div>
      </div>
      {libraryError ? <p className="thumbnail-library__alert">{libraryError}</p> : null}
      {libraryUploadStatus.error ? <p className="thumbnail-library__alert">{libraryUploadStatus.error}</p> : null}
      {libraryUploadStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{libraryUploadStatus.success}</p>
      ) : null}
      {libraryImportStatus.error ? <p className="thumbnail-library__alert">{libraryImportStatus.error}</p> : null}
      {libraryImportStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{libraryImportStatus.success}</p>
      ) : null}
      {visibleLibraryAssets.length === 0 && !libraryError ? (
        <p className="thumbnail-library__placeholder">
          {libraryLoading ? "画像を読み込んでいます…" : "参考サムネがありません（QCはQCタブ）。"}
        </p>
      ) : null}
      {visibleLibraryAssets.length > 0 ? (
        <div className="thumbnail-library-grid">
          {visibleLibraryAssets.map((asset) => {
            const previewUrl = resolveApiUrl(asset.public_url);
            const formState = libraryForms[asset.id] ?? { video: "", pending: false };
            return (
              <article key={asset.id} className="thumbnail-library-card">
                <div className="thumbnail-library-card__preview">
	                  <img src={previewUrl} alt={asset.file_name} loading="lazy" draggable={false} />
                </div>
                <div className="thumbnail-library-card__meta">
                  <strong title={asset.file_name}>{asset.file_name}</strong>
                  <div className="thumbnail-library-card__meta-info">{asset.relative_path}</div>
                  <div className="thumbnail-library-card__meta-info">
                    {formatBytes(asset.size_bytes)}・{formatDate(asset.updated_at)}
                  </div>
                  <form
                    className="thumbnail-library-card__assign"
                    onSubmit={(event) => handleLibraryAssignSubmit(event, asset)}
                  >
                    <label>
                      <span>紐付け先の動画番号</span>
                      <input
                        type="text"
                        inputMode="numeric"
                        value={formState.video}
                        onChange={(event) => handleLibraryVideoChange(asset.id, event.target.value)}
                        placeholder="例: 191"
                        disabled={formState.pending}
                      />
                    </label>
                    <button type="submit" disabled={formState.pending || !formState.video.trim()}>
                      {formState.pending ? "紐付け中…" : "企画に紐付け"}
                    </button>
                    {formState.error ? (
                      <p className="thumbnail-library__message thumbnail-library__message--error">{formState.error}</p>
                    ) : null}
                    {formState.success ? (
                      <p className="thumbnail-library__message thumbnail-library__message--success">{formState.success}</p>
                    ) : null}
                  </form>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  ) : (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>参考サムネ登録</h3>
          <p>チャンネルを選択するとライブラリが開きます。</p>
        </div>
      </div>
    </section>
  );

  const templatesPanel = activeChannel ? (
    <section className="thumbnail-library-panel thumbnail-library-panel--templates">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>サムネテンプレ（型）</h3>
          <p>
            チャンネルごとに「型」を登録して、手動でAI生成できます。置換キー:
            <code> {"{{title}} {{thumbnail_upper}} {{thumbnail_title}} {{thumbnail_lower}} {{thumbnail_prompt}}"} </code>
          </p>
        </div>
        <div className="thumbnail-library-panel__header-actions">
          <button type="button" onClick={handleTemplatesRefresh} disabled={templatesLoading || templatesStatus.pending}>
            {templatesLoading ? "読込中…" : "再読み込み"}
          </button>
          <button type="button" onClick={handleAddTemplate} disabled={templatesStatus.pending}>
            追加
          </button>
          <button type="button" onClick={handleSaveTemplates} disabled={templatesStatus.pending || !templatesDirty}>
            {templatesStatus.pending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
      {imageModels.some((model) => model.provider === "openrouter") ? (
        <details className="thumbnail-library-panel__pricing">
          <summary>料金（OpenRouter /models）</summary>
          <div className="thumbnail-library-panel__pricing-body">
            <p className="thumbnail-library__placeholder">
              OpenRouter の <code>/api/v1/models</code> から取得した単価です（USD/token・USD/request・USD/image(unit)）。この画面のAI生成は{" "}
              <strong>1枚=1 request（N枚ならN回）</strong> で送信します。概算: <code>request</code> + <code>image</code> +（入力tok×
              <code>prompt</code>）+（出力tok×<code>completion</code>）。生成後は <code>/api/v1/generation</code> の <code>total_cost</code>{" "}
              を「実コスト」として保存します。
            </p>
            <table className="thumbnail-pricing-table">
              <thead>
                <tr>
                  <th>model_key</th>
                  <th>model_name</th>
                  <th>image</th>
                  <th>request</th>
                  <th>prompt</th>
                  <th>completion</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {imageModels
                  .filter((model) => model.provider === "openrouter")
                  .map((model) => {
                    const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                    const requestUnit = parsePricingNumber(model.pricing?.request ?? null);
                    const promptUnit = parsePricingNumber(model.pricing?.prompt ?? null);
                    const completionUnit = parsePricingNumber(model.pricing?.completion ?? null);
                    return (
                      <tr key={model.key}>
                        <td>
                          <code>{model.key}</code>
                        </td>
                        <td>
                          <code>{model.model_name}</code>
                        </td>
                        <td>{imageUnit !== null ? `${formatUsdAmount(imageUnit)}/unit` : "—"}</td>
                        <td>{requestUnit !== null ? `${formatUsdAmount(requestUnit)}/req` : "—"}</td>
                        <td>{promptUnit !== null ? formatUsdPerMillionTokens(promptUnit) : "—"}</td>
                        <td>{completionUnit !== null ? formatUsdPerMillionTokens(completionUnit) : "—"}</td>
                        <td>{model.pricing_updated_at ? formatDate(model.pricing_updated_at) : "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
      {imageModelsError ? <p className="thumbnail-library__alert">{imageModelsError}</p> : null}
      {templatesStatus.error ? <p className="thumbnail-library__alert">{templatesStatus.error}</p> : null}
      {templatesStatus.success ? (
        <p className="thumbnail-library__message thumbnail-library__message--success">{templatesStatus.success}</p>
      ) : null}
      {!channelTemplates || channelTemplates.templates.length === 0 ? (
        <p className="thumbnail-library__placeholder">テンプレがまだありません。「追加」→「保存」で登録します。</p>
      ) : (
        <div className="thumbnail-library-panel__cards">
          {channelTemplates.templates.map((tpl) => {
            const isDefault = channelTemplates.default_template_id === tpl.id;
            return (
              <details key={tpl.id} className="thumbnail-library-panel__card">
                <summary>
                  <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="radio"
                      name="thumbnail_default_template"
                      checked={isDefault}
                      onChange={() => handleTemplateDefaultChange(tpl.id)}
                    />
                    <strong>{tpl.name}</strong>
                  </label>
                  <span style={{ marginLeft: 8, color: "#64748b" }}>{tpl.image_model_key}</span>
                </summary>
                <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                  <label>
                    <span>テンプレ名</span>
                    <input
                      type="text"
                      value={tpl.name}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "name", event.target.value)}
                    />
                  </label>
                  <label>
                    <span>画像モデル</span>
                    <select
                      value={tpl.image_model_key}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "image_model_key", event.target.value)}
                    >
                      <option value="">選択してください</option>
                      {imageModels.map((model) => {
                        const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                        const costSuffix = imageUnit !== null ? ` / ${formatUsdAmount(imageUnit)}/unit` : "";
                        return (
                          <option key={model.key} value={model.key}>
                            {model.key} ({model.provider}{costSuffix})
                          </option>
                        );
                      })}
                    </select>
                  </label>
                  <label>
                    <span>プロンプトテンプレ</span>
                    <textarea
                      value={tpl.prompt_template}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "prompt_template", event.target.value)}
                      rows={6}
                    />
                  </label>
                  <label>
                    <span>ネガティブ（任意）</span>
                    <textarea
                      value={tpl.negative_prompt ?? ""}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "negative_prompt", event.target.value)}
                      rows={2}
                    />
                  </label>
                  <label>
                    <span>メモ（任意）</span>
                    <textarea
                      value={tpl.notes ?? ""}
                      onChange={(event) => handleTemplateFieldChange(tpl.id, "notes", event.target.value)}
                      rows={2}
                    />
                  </label>
                  <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <button type="button" onClick={() => handleDeleteTemplate(tpl.id)}>
                      削除
                    </button>
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      )}
    </section>
  ) : (
    <section className="thumbnail-library-panel thumbnail-library-panel--templates">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>サムネテンプレ（型）</h3>
          <p>チャンネルを選択するとテンプレが表示されます。</p>
        </div>
      </div>
    </section>
  );

  const channelInfoPanel = activeChannel ? (
    <section className="channel-profile-panel">
      <div className="channel-profile-panel__header">
        <div>
          <h2>{activeChannelName ?? activeChannel.channel}</h2>
          <p className="channel-profile-panel__subtitle">チャンネルの概況</p>
        </div>
      </div>
      {summary ? (
        <div className="channel-profile-metrics">
          <div className="channel-profile-metric">
            <span>登録者</span>
            <strong>{formatNumber(summary.subscriber_count)}</strong>
          </div>
          <div className="channel-profile-metric">
            <span>総再生</span>
            <strong>{formatNumber(summary.view_count)}</strong>
          </div>
          <div className="channel-profile-metric">
            <span>案件</span>
            <strong>{summary.total.toLocaleString("ja-JP")}</strong>
          </div>
          {activeChannel.library_path ? (
            <div className="channel-profile-metric channel-profile-metric--wide">
              <span>ライブラリパス</span>
              <code>{activeChannel.library_path}</code>
            </div>
          ) : null}
        </div>
      ) : null}
      {channelVideos.length > 0 ? (
        <section className="thumbnail-channel-videos">
          <div className="thumbnail-channel-videos__header">
            <h3>最新動画プレビュー</h3>
            <span>{channelVideos.length} 件</span>
          </div>
          <p className="thumbnail-library__placeholder">
            ※案件で「案を登録」フォームを開いた状態のときに「このサムネを案に取り込む」が使えます。
          </p>
          <div className="thumbnail-channel-videos__list">
            {channelVideos.map((video) => {
              const disableApply = !variantForm;
              return (
                <div key={video.video_id} className="thumbnail-channel-video">
	                  <a className="thumbnail-channel-video__thumb" href={video.url} target="_blank" rel="noreferrer">
	                    {video.thumbnail_url ? (
	                      <img src={video.thumbnail_url} alt={video.title} loading="lazy" draggable={false} />
	                    ) : (
	                      <span>No Image</span>
	                    )}
	                  </a>
                  <div className="thumbnail-channel-video__info">
                    <div className="thumbnail-channel-video__title" title={video.title}>
                      {video.title}
                    </div>
                    <div className="thumbnail-channel-video__meta">
                      <span>{formatDate(video.published_at)}</span>
                      <span>再生: {formatNumber(video.view_count)}</span>
                      <span>推定CTR: {formatPercent(video.estimated_ctr)}</span>
                      <span>長さ: {formatDuration(video.duration_seconds)}</span>
                    </div>
                    <div className="thumbnail-channel-video__actions">
                      <button type="button" onClick={() => handleApplyVideoThumbnail(video)} disabled={disableApply}>
                        このサムネを案に取り込む
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : (
        <p className="thumbnail-library__placeholder">最新の動画プレビューがまだ取得されていません。</p>
      )}
    </section>
  ) : (
    <section className="channel-profile-panel">
      <div className="channel-profile-panel__header">
        <div>
          <h2>チャンネルを選択</h2>
          <p className="channel-profile-panel__subtitle">上のタブでチャンネルを選択してください。</p>
        </div>
      </div>
    </section>
  );

  const generateDialogTemplate =
    generateDialog && channelTemplates?.channel === generateDialog.channel
      ? channelTemplates.templates.find((tpl) => tpl.id === generateDialog.templateId)
      : undefined;
  const generateDialogResolvedModelKey = generateDialog
    ? (generateDialog.imageModelKey.trim() || generateDialogTemplate?.image_model_key?.trim() || "")
    : "";
  const generateDialogResolvedModel = generateDialogResolvedModelKey
    ? imageModels.find((model) => model.key === generateDialogResolvedModelKey)
    : undefined;
  const generateDialogResolvedPricing = generateDialogResolvedModel?.pricing ?? null;
  const generateDialogResolvedPricingUpdatedAt = generateDialogResolvedModel?.pricing_updated_at ?? null;
  const generateDialogImageUnitUsd = parsePricingNumber(generateDialogResolvedPricing?.image ?? null);
  const generateDialogRequestUnitUsd = parsePricingNumber(generateDialogResolvedPricing?.request ?? null);
  const generateDialogPromptTokenUsd = parsePricingNumber(generateDialogResolvedPricing?.prompt ?? null);
  const generateDialogCompletionTokenUsd = parsePricingNumber(generateDialogResolvedPricing?.completion ?? null);
  const generateDialogImageSubtotalUsd =
    generateDialog && generateDialogImageUnitUsd !== null ? generateDialogImageUnitUsd * generateDialog.count : null;
  const generateDialogRequestSubtotalUsd =
    generateDialog && generateDialogRequestUnitUsd !== null ? generateDialogRequestUnitUsd * generateDialog.count : null;

  return (
    <>
      <section className={`thumbnail-workspace${compact ? " thumbnail-workspace--compact" : ""}`}>
        <header className="thumbnail-workspace__header">
          <div>
            <h2 className="thumbnail-workspace__title">サムネイル管理</h2>
            <p className="thumbnail-workspace__subtitle">コピー編集→Canva用CSV→採用サムネ紐付け（必要ならAI生成）まで。</p>
          </div>
          <div className="thumbnail-workspace__header-actions">
            <button type="button" className="thumbnail-refresh-button" onClick={handleRefresh} disabled={loading}>
              最新の情報を再取得
            </button>
            {activeTab === "projects" ? (
              <button
                type="button"
                className="workspace-button workspace-button--primary"
                onClick={handleStartNewVariant}
                disabled={loading || filteredProjects.length === 0}
              >
                新しい案を作成
              </button>
            ) : null}
          </div>
        </header>
        <div className="thumbnail-hub">
          {overview && overview.channels.length > 1 ? (
            <div className="thumbnail-channel-picker" aria-label="チャンネル選択">
              <button
                ref={channelPickerButtonRef}
                type="button"
                className="thumbnail-channel-picker__trigger"
                onClick={() => setChannelPickerOpen((current) => !current)}
                aria-expanded={channelPickerOpen}
              >
                <span className="thumbnail-channel-picker__label">チャンネル</span>
                {activeChannel ? (() => {
                  const channelInfo = channelSummaryMap.get(activeChannel.channel);
                  const avatarUrl = channelInfo?.branding?.avatar_url ?? null;
                  const themeColor = channelInfo?.branding?.theme_color ?? null;
                  const avatarEnabled = Boolean(avatarUrl && !channelAvatarErrors[activeChannel.channel]);
                const iconStyle = { backgroundColor: themeColor ?? channelIconColor(activeChannel.channel) };
                return (
                  <span className="thumbnail-channel-picker__current">
                      <span className="thumbnail-hub__channel-icon" aria-hidden="true" style={iconStyle}>
                        {channelIconText(activeChannel.channel)}
                        {avatarEnabled ? (
                          <img
                            className="thumbnail-hub__channel-avatar"
                            src={avatarUrl ?? undefined}
                            alt=""
                            loading="lazy"
                            draggable={false}
                            onError={() =>
                              setChannelAvatarErrors((current) => ({ ...current, [activeChannel.channel]: true }))
                            }
                          />
                        ) : null}
                      </span>
                      <span className="thumbnail-channel-picker__current-meta">
                        <span className="thumbnail-channel-picker__current-code">{activeChannel.channel}</span>
                        {activeChannelName ? (
                          <span className="thumbnail-channel-picker__current-title">{activeChannelName}</span>
                        ) : null}
                      </span>
                      <span className="thumbnail-channel-picker__count">{activeChannel.summary.total}</span>
                    </span>
                  );
                })() : <span className="thumbnail-channel-picker__current">—</span>}
                <span className="thumbnail-channel-picker__chevron" aria-hidden="true">
                  {channelPickerOpen ? "▴" : "▾"}
                </span>
              </button>
              {channelPickerOpen ? (
                <div className="thumbnail-channel-picker__panel" ref={channelPickerPanelRef}>
                  <div className="thumbnail-channel-picker__controls">
                    <input
                      type="search"
                      value={channelPickerQuery}
                      onChange={(event) => setChannelPickerQuery(event.target.value)}
                      placeholder="CHコード・チャンネル名で検索"
                      autoFocus
                    />
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => setChannelPickerQuery("")}
                      disabled={!channelPickerQuery.trim()}
                    >
                      クリア
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => setChannelPickerOpen(false)}
                    >
                      閉じる
                    </button>
                  </div>
                  <div className="thumbnail-channel-picker__list" role="listbox" aria-label="チャンネル一覧">
                    {channelPickerChannels.map((channel) => {
                      const isActive = channel.channel === activeChannel?.channel;
                      const title = (channel.channel_title ?? "").trim();
                      const channelInfo = channelSummaryMap.get(channel.channel);
                      const fallbackTitle = (
                        channelInfo?.name ??
                        channelInfo?.branding?.title ??
                        channelInfo?.youtube_title ??
                        ""
                      ).trim();
                      const resolvedTitle = title || fallbackTitle;
                      const buttonTitle = resolvedTitle ? `${channel.channel} ${resolvedTitle}` : channel.channel;
                      const avatarUrl = channelInfo?.branding?.avatar_url ?? null;
                      const themeColor = channelInfo?.branding?.theme_color ?? null;
                      const avatarEnabled = Boolean(avatarUrl && !channelAvatarErrors[channel.channel]);
                      const iconStyle = { backgroundColor: themeColor ?? channelIconColor(channel.channel) };
                      return (
                        <button
                          key={channel.channel}
                          type="button"
                          className={`thumbnail-hub__tab thumbnail-hub__tab--channel ${isActive ? "thumbnail-hub__tab--active" : ""}`}
                          onClick={() => {
                            selectChannel(channel.channel);
                            setChannelPickerOpen(false);
                            setChannelPickerQuery("");
                          }}
                          aria-pressed={isActive}
                          title={buttonTitle}
                        >
                          <span className="thumbnail-hub__channel-icon" aria-hidden="true" style={iconStyle}>
                            {channelIconText(channel.channel)}
                            {avatarEnabled ? (
                              <img
                                className="thumbnail-hub__channel-avatar"
                                src={avatarUrl ?? undefined}
                                alt=""
                                loading="lazy"
                                draggable={false}
                                onError={() =>
                                  setChannelAvatarErrors((current) => ({ ...current, [channel.channel]: true }))
                                }
                              />
                            ) : null}
                          </span>
                          <span className="thumbnail-hub__channel-meta">
                            <span className="thumbnail-hub__channel-code">{channel.channel}</span>
                            {resolvedTitle ? <span className="thumbnail-hub__channel-title">{resolvedTitle}</span> : null}
                          </span>
                          <span className="thumbnail-hub__tab-count">{channel.summary.total}</span>
                        </button>
                      );
                    })}
                    {channelPickerChannels.length === 0 ? (
                      <p className="thumbnail-channel-picker__empty">該当するチャンネルが見つかりませんでした。</p>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
          <nav className="thumbnail-hub__tabs thumbnail-hub__tabs--views" aria-label="表示切替">
            {THUMBNAIL_WORKSPACE_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`thumbnail-hub__tab ${activeTab === tab.key ? "thumbnail-hub__tab--active" : ""}`}
                onClick={() => setActiveTab(tab.key)}
                aria-pressed={activeTab === tab.key}
                title={tab.description}
              >
                {tab.label}
              </button>
            ))}
          </nav>
          <div className="thumbnail-hub__panes">
            {activeTab === "bulk" ? <div className="thumbnail-hub__pane thumbnail-hub__pane--bulk">{bulkPanel}</div> : null}
            {activeTab === "projects" ? (
              <section className="thumbnail-hub__pane thumbnail-hub__pane--projects">
            <div className="thumbnail-actions">
              <div className="thumbnail-actions__left">
                <h3 className="thumbnail-actions__title">{activeChannelName ?? "チャンネル一覧"}</h3>
              </div>
              <div className="thumbnail-actions__search">
                <input
                  type="search"
                  placeholder="企画タイトル・タグ・案名で検索"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                />
              </div>
            </div>
            <div className="thumbnail-toolbar thumbnail-toolbar--filters">
              <div className="thumbnail-toolbar__filters">
                {STATUS_FILTERS.map((filter) => (
                  <button
                    key={filter.key}
                    type="button"
                    className={`thumbnail-filter ${statusFilter === filter.key ? "is-active" : ""}`}
                    onClick={() => setStatusFilter(filter.key)}
                    aria-pressed={statusFilter === filter.key}
                  >
                    <span>{filter.label}</span>
                    <span className="thumbnail-filter__count">{statusCounters[filter.key]}</span>
                  </button>
                ))}
              </div>
            </div>
            {activeChannel && channelHasTwoUpVariants ? (
              <div className="thumbnail-alert" style={{ marginTop: 12 }}>
                このチャンネルは <strong>2案（00_thumb_1 / 00_thumb_2）</strong> を想定しています。{" "}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => {
                    setGalleryVariantMode("two_up");
                    setActiveTab("gallery");
                  }}
                >
                  ギャラリーで{activeChannel.projects.length * 2}枚を見る
                </button>
              </div>
            ) : null}
            {errorMessage ? <div className="thumbnail-alert thumbnail-alert--error">{errorMessage}</div> : null}
            {loading ? <div className="thumbnail-loading">読み込み中…</div> : null}
            {!loading && filteredProjects.length === 0 ? (
              <div className="thumbnail-empty">
                {overview && overview.channels.length > 0
                  ? "選択中の条件に該当するサムネイルがありません。"
                  : "チャンネルを登録するとサムネイル管理がここに表示されます。"}
              </div>
            ) : null}
            <div className="thumbnail-card-list">
              {filteredProjects.map((project) => {
                const projectKey = getProjectKey(project);
                const projectUpdating = updatingProjectId === projectKey;
                const isCreatingVariant = variantForm?.projectKey === projectKey;
                const currentVariantForm = isCreatingVariant ? variantForm : null;
                const disableVariantActions = projectUpdating || loading;
                const statusLabel = PROJECT_STATUS_LABELS[project.status] ?? project.status;
                const readyLabel = project.ready_for_publish ? "公開OK" : "—";
                const primaryTitle = project.title ?? project.sheet_title ?? "タイトル未設定";
                const secondaryTitle =
                  project.sheet_title && project.sheet_title !== primaryTitle ? project.sheet_title : null;
                const selectedVariant =
                  project.variants.find((variant) => variant.is_selected) ??
                  (project.selected_variant_id
                    ? project.variants.find((variant) => variant.id === project.selected_variant_id)
                    : undefined) ??
                  project.variants[0];
                const selectedVariantLabel = selectedVariant ? selectedVariant.label ?? selectedVariant.id : null;
                const expanded = expandedProjectKey === projectKey;
                const selectedVariantToken = selectedVariant?.updated_at ?? project.updated_at ?? null;
                const selectedVariantImageBase = selectedVariant
                  ? selectedVariant.preview_url
                    ? resolveApiUrl(selectedVariant.preview_url)
                    : selectedVariant.image_url
                      ? resolveApiUrl(selectedVariant.image_url)
                      : selectedVariant.image_path
                        ? resolveApiUrl(`/thumbnails/assets/${selectedVariant.image_path}`)
                        : null
                  : null;
                const selectedVariantImage = selectedVariantImageBase
                  ? withCacheBust(selectedVariantImageBase, selectedVariantToken)
                  : null;
                const feedback = cardFeedback[projectKey];
                const assetPath = `${THUMBNAIL_ASSET_BASE_PATH}/${project.channel}/${project.video}/`;
                const hasExtraInfo = Boolean(
                  secondaryTitle || project.summary || project.notes || (project.tags && project.tags.length > 0)
                );
                const cardClasses = [
                  "thumbnail-card",
                  projectUpdating ? "is-updating" : "",
                  project.variants.length === 0 ? "is-empty" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <article
                    key={projectKey}
                    className={cardClasses}
                  >
                    <div className="thumbnail-card__inner">
                      <header className="thumbnail-card__header">
                        <div className="thumbnail-card__header-main">
                          <div className="thumbnail-card__identity">
                            <span className="thumbnail-card__code-main">
                              {project.script_id ?? `${project.channel}-${project.video}`}
                            </span>
                            <span className="thumbnail-card__code-sub">{project.channel}</span>
                            {project.variants.length === 0 ? (
                              <span className="thumbnail-card__badge">未登録</span>
                            ) : null}
                          </div>
                          <div className="thumbnail-card__status-group">
                            <span className={`thumbnail-card__status-badge thumbnail-card__status-badge--${project.status}`}>
                              {statusLabel}
                            </span>
                            <select
                              value={project.status}
                              onChange={(event) =>
                                handleStatusChange(project, event.target.value as ThumbnailProjectStatus)
                              }
                              disabled={disableVariantActions}
                              aria-label="ステータス変更"
                            >
                              {PROJECT_STATUS_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>
                        <div className="thumbnail-card__quick-info">
                          <div className="thumbnail-card__meta-row">
                            <span>Ready: {readyLabel}</span>
                            <span>Variants: {project.variants.length}</span>
                          </div>
                          {selectedVariantLabel ? (
                            <div className="thumbnail-card__meta-row">選択中: {selectedVariantLabel}</div>
                          ) : null}
                        </div>
                      </header>
                      <div className="thumbnail-card__actions">
                        <button
                          type="button"
                          className="btn"
                          onClick={() => handleDropzoneClick(projectKey, disableVariantActions)}
                          disabled={disableVariantActions}
                        >
                          画像を差し替える
                        </button>
                        <button
                          type="button"
                          className="btn btn--primary"
                          onClick={() => handleComposeVariant(project)}
                          disabled={disableVariantActions}
                          title="企画CSVのコピー（上/中/下）を使って、文字サムネを無料で合成します"
                        >
                          文字サムネ
                        </button>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => handleOpenGenerateDialog(project)}
                          disabled={disableVariantActions}
                        >
                          AI生成
                        </button>
                        <button
                          type="button"
                          className="btn btn--primary"
                          onClick={() => handleOpenLayerTuningDialog(project)}
                          disabled={disableVariantActions}
                          title="Canvaみたいにドラッグで位置調整できます"
                        >
                          調整（ドラッグ）
                        </button>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => handleOpenVariantForm(project)}
                          disabled={disableVariantActions}
                        >
                          案を登録
                        </button>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => handleOpenPlanningDialog(project, selectedVariant)}
                          disabled={!selectedVariant || disableVariantActions}
                        >
                          企画に書き出す
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost"
                          onClick={() => handleOpenProjectForm(project)}
                          disabled={disableVariantActions}
                        >
                          メモを編集
                        </button>
                      </div>
                      <div className="thumbnail-card__storage">
                        <div className="thumbnail-card__storage-label">画像パス</div>
                        <div className="thumbnail-card__storage-path">
                          <code>{assetPath}</code>
                        </div>
                        <div className="thumbnail-card__storage-actions">
                          <button
                            type="button"
                            className="thumbnail-card__storage-copy"
                            onClick={() => handleCopyAssetPath(assetPath)}
                          >
                            パスをコピー
                          </button>
                        </div>
                      </div>
                      {hasExtraInfo ? (
                        <details className="thumbnail-card__details">
                          <summary>メモ / タグ</summary>
                          <div className="thumbnail-card__details-body">
                            {secondaryTitle ? <p>サブタイトル: {secondaryTitle}</p> : null}
                            {project.summary ? <p>概要: {project.summary}</p> : null}
                            {project.notes ? <p>メモ: {project.notes}</p> : null}
                            {project.tags && project.tags.length ? (
                              <p>
                                {project.tags.map((tag) => (
                                  <span key={tag} className="thumbnail-tag">
                                    {tag}
                                  </span>
                                ))}
                              </p>
                            ) : null}
                          </div>
                        </details>
                      ) : null}
                      <div className="thumbnail-card__variants">
                        {project.variants.length === 0 ? (
                          <p className="thumbnail-library__placeholder">まだサムネイル案が登録されていません。</p>
                        ) : (
                          <>
                            <div className="thumbnail-card__selected">
	                              <div className="thumbnail-card__selected-media">
	                                {selectedVariantImage ? (
	                                  <button
	                                    type="button"
	                                    className="thumbnail-card__selected-button"
	                                    onClick={() => {
	                                      const stableForEdit = (() => {
	                                        if (
	                                          hasThumbFileSuffix(selectedVariant?.image_path, "00_thumb_1.png") ||
	                                          hasThumbFileSuffix(selectedVariant?.image_url, "00_thumb_1.png") ||
	                                          hasThumbFileSuffix(selectedVariant?.preview_url, "00_thumb_1.png")
	                                        ) {
	                                          return "00_thumb_1";
	                                        }
	                                        if (
	                                          hasThumbFileSuffix(selectedVariant?.image_path, "00_thumb_2.png") ||
	                                          hasThumbFileSuffix(selectedVariant?.image_url, "00_thumb_2.png") ||
	                                          hasThumbFileSuffix(selectedVariant?.preview_url, "00_thumb_2.png")
	                                        ) {
	                                          return "00_thumb_2";
	                                        }
	                                        return null;
	                                      })();
	                                      handleOpenLayerTuningDialog(project, { stable: stableForEdit });
	                                    }}
	                                    title="クリックで調整を開く"
	                                  >
	                                    <img
	                                      src={selectedVariantImage}
	                                      alt={selectedVariantLabel ?? `${project.channel}-${project.video}`}
	                                      loading="lazy"
	                                      draggable={false}
	                                    />
	                                  </button>
	                                ) : (
	                                  <div className="thumbnail-card__selected-placeholder">No Image</div>
	                                )}
	                              </div>
                              <div className="thumbnail-card__selected-meta">
                                <div className="thumbnail-card__selected-title">
                                  <strong>{selectedVariantLabel ?? "（案名なし）"}</strong>
                                  {selectedVariant ? (
                                    <span className="thumbnail-card__selected-badge">
                                      {VARIANT_STATUS_LABELS[selectedVariant.status]}
                                    </span>
                                  ) : null}
                                </div>
                                <div className="thumbnail-card__selected-actions">
                                  <button
                                    type="button"
                                    className="btn btn--ghost"
                                    onClick={() => toggleProjectVariants(projectKey)}
                                    disabled={disableVariantActions}
                                  >
                                    {expanded ? "案一覧を閉じる" : `案一覧を開く (${project.variants.length})`}
                                  </button>
                                </div>
                              </div>
                            </div>
                            {expanded ? (
                              <div className="thumbnail-variant-grid thumbnail-variant-grid--expanded">
                                {project.variants.map((variant) => {
                                  const variantImageBase =
                                    variant.preview_url
                                      ? resolveApiUrl(variant.preview_url)
                                      : variant.image_url
                                        ? resolveApiUrl(variant.image_url)
                                        : variant.image_path
                                          ? resolveApiUrl(`/thumbnails/assets/${variant.image_path}`)
                                          : null;
                                  const variantImage = variantImageBase
                                    ? withCacheBust(variantImageBase, variant.updated_at ?? project.updated_at)
                                    : null;
                                  const variantSelected =
                                    Boolean(variant.is_selected) || project.selected_variant_id === variant.id;
                                  return (
                                    <button
                                      type="button"
                                      key={variant.id}
                                      className={`thumbnail-variant-tile${variantSelected ? " is-selected" : ""}`}
                                      onClick={() => handleSelectVariant(project, variant)}
                                      disabled={disableVariantActions}
                                    >
                                      <div className="thumbnail-variant-tile__media">
                                        {variantImage ? (
	                                          <img
	                                            src={variantImage}
	                                            alt={variant.label ?? variant.id}
	                                            loading="lazy"
	                                            draggable={false}
	                                          />
                                        ) : (
                                          <span className="thumbnail-variant-tile__placeholder">No Image</span>
                                        )}
                                      </div>
                                      <div className="thumbnail-variant-tile__content">
                                        <div className="thumbnail-variant-tile__title">{variant.label ?? variant.id}</div>
                                        <div className="thumbnail-variant-tile__badge">
                                          {VARIANT_STATUS_LABELS[variant.status]}
                                        </div>
                                      </div>
                                      {typeof variant.cost_usd === "number" && Number.isFinite(variant.cost_usd) ? (
                                        <div
                                          className="thumbnail-variant-tile__meta"
                                          title={variant.model_key ?? variant.model ?? undefined}
                                        >
                                          実コスト {formatUsdAmount(variant.cost_usd)}
                                        </div>
                                      ) : null}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}
                          </>
                        )}
                      </div>
                      <div
                        className={`thumbnail-dropzone ${activeDropProject === projectKey ? "is-active" : ""}`}
                        onDragEnter={(event) => handleDropzoneDragEnter(event, projectKey, disableVariantActions)}
                        onDragOver={(event) => handleDropzoneDragOver(event, disableVariantActions)}
                        onDragLeave={(event) => handleDropzoneDragLeave(event, projectKey)}
                        onDrop={(event) => handleDropzoneDrop(event, project, disableVariantActions)}
                      >
                        <p className="thumbnail-dropzone__hint">ここへドラッグすると即差し替えできます。</p>
                        <input
                          ref={(element) => {
                            if (element) {
                              dropzoneFileInputs.current.set(projectKey, element);
                            } else {
                              dropzoneFileInputs.current.delete(projectKey);
                            }
                          }}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          hidden
                          onChange={(event) => handleDropzoneInputChange(event, project)}
                        />
                        <button
                          type="button"
                          className="thumbnail-card__manual-button"
                          onClick={() => handleDropzoneClick(projectKey, disableVariantActions)}
                          disabled={disableVariantActions}
                        >
                          画像を選択して追加
                        </button>
                      </div>
                      {feedback ? (
                        <div
                          className={`thumbnail-card__feedback thumbnail-card__feedback--${feedback.type}`}
                          role="status"
                        >
                          {feedback.message}
                        </div>
                      ) : null}
                      {currentVariantForm ? (
                        <form
                          className="thumbnail-variant-form"
                          onSubmit={(event) => handleVariantFormSubmit(event, project)}
                        >
                          <div className="thumbnail-variant-form__primary">
                            <label className="thumbnail-variant-form__field">
                              <span>案の名前</span>
                              <input
                                type="text"
                                value={currentVariantForm.label}
                                onChange={(event) => handleVariantFormFieldChange("label", event.target.value)}
                                placeholder="例: 参考A案"
                              />
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>状態</span>
                              <select
                                value={currentVariantForm.status}
                                onChange={(event) =>
                                  handleVariantFormFieldChange("status", event.target.value as ThumbnailVariantStatus)
                                }
                              >
                                {VARIANT_STATUS_OPTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>メモ</span>
                              <textarea
                                value={currentVariantForm.notes}
                                onChange={(event) => handleVariantFormFieldChange("notes", event.target.value)}
                                rows={3}
                                placeholder="デザイナー向けの補足や気づきなど"
                              />
                            </label>
                            <label className="thumbnail-variant-form__field">
                              <span>タグ（カンマ区切り）</span>
                              <input
                                type="text"
                                value={currentVariantForm.tags}
                                onChange={(event) => handleVariantFormFieldChange("tags", event.target.value)}
                                placeholder="人物, 共感"
                              />
                            </label>
                          </div>
                          <div className="thumbnail-variant-form__actions">
                            <button
                              type="button"
                              className="thumbnail-variant-form__button thumbnail-variant-form__button--secondary"
                              onClick={handleCancelVariantForm}
                            >
                              キャンセル
                            </button>
                            <button
                              type="submit"
                              className="thumbnail-variant-form__button thumbnail-variant-form__button--primary"
                              disabled={disableVariantActions}
                            >
                              保存
                            </button>
                          </div>
                        </form>
                      ) : null}
                      {projectForm?.projectKey === projectKey ? (
                        <form
                          className="thumbnail-project-form"
                          onSubmit={(event) => handleProjectFormSubmit(event, project)}
                        >
                          <div className="thumbnail-project-form__fields">
                            <label className="thumbnail-project-form__field">
                              <span>担当</span>
                              <input
                                type="text"
                                value={projectForm.owner}
                                onChange={(event) => handleProjectFormChange("owner", event.target.value)}
                                placeholder="担当者"
                              />
                            </label>
                            <label className="thumbnail-project-form__field thumbnail-project-form__field--wide">
                              <span>サマリ</span>
                              <textarea
                                rows={2}
                                value={projectForm.summary}
                                onChange={(event) => handleProjectFormChange("summary", event.target.value)}
                                placeholder="案件の概要やターゲット"
                              />
                            </label>
                            <label className="thumbnail-project-form__field thumbnail-project-form__field--wide">
                              <span>メモ</span>
                              <textarea
                                rows={3}
                                value={projectForm.notes}
                                onChange={(event) => handleProjectFormChange("notes", event.target.value)}
                                placeholder="進行メモや懸念点"
                              />
                            </label>
                            <label className="thumbnail-project-form__field">
                              <span>タグ（カンマ区切り）</span>
                              <input
                                type="text"
                                value={projectForm.tags}
                                onChange={(event) => handleProjectFormChange("tags", event.target.value)}
                                placeholder="仏教, 人間関係"
                              />
                            </label>
                            <label className="thumbnail-project-form__field">
                              <span>期日</span>
                              <input
                                type="date"
                                value={projectForm.dueAt}
                                onChange={(event) => handleProjectFormChange("dueAt", event.target.value)}
                              />
                            </label>
                          </div>
                          <div className="thumbnail-project-form__actions">
                            <button type="button" onClick={handleCancelProjectForm}>
                              閉じる
                            </button>
                            <button type="submit" disabled={disableVariantActions}>
                              保存
                            </button>
                          </div>
                        </form>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        ) : null}
            {activeTab === "gallery" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--gallery">{galleryPanel}</div>
            ) : null}
            {activeTab === "qc" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--qc">{qcPanel}</div>
            ) : null}
            {activeTab === "templates" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--templates">{templatesPanel}</div>
            ) : null}
            {activeTab === "library" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--library">{libraryPanel}</div>
            ) : null}
            {activeTab === "channel" ? (
              <div className="thumbnail-hub__pane thumbnail-hub__pane--channel">{channelInfoPanel}</div>
            ) : null}
          </div>
        </div>
      </section>
      {planningDialog ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={handleClosePlanningDialog} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                {planningDialog.channel} / {planningDialog.projectTitle || "タイトル未設定"}
              </div>
              <h2>サムネから企画行を作成</h2>
              {planningDialog.variantLabel ? (
                <p className="thumbnail-planning-dialog__meta">案: {planningDialog.variantLabel}</p>
              ) : null}
            </header>
            <form className="thumbnail-planning-form" onSubmit={(event) => handlePlanningSubmit(event)}>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>動画番号</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={planningDialog.videoNumber}
                    onChange={(event) => handlePlanningFieldChange("videoNumber", event.target.value)}
                    placeholder="例: 191"
                    required
                  />
                </label>
                <label>
                  <span>No.</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={planningDialog.no}
                    onChange={(event) => handlePlanningFieldChange("no", event.target.value)}
                    placeholder="例: 191"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>企画タイトル</span>
                  <input
                    type="text"
                    value={planningDialog.title}
                    onChange={(event) => handlePlanningFieldChange("title", event.target.value)}
                    placeholder="【○○】〜"
                    required
                  />
                </label>
              </div>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>サムネタイトル上</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailUpper}
                    onChange={(event) => handlePlanningFieldChange("thumbnailUpper", event.target.value)}
                    placeholder="呼びかけ"
                  />
                </label>
                <label>
                  <span>サムネタイトル下</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailLower}
                    onChange={(event) => handlePlanningFieldChange("thumbnailLower", event.target.value)}
                    placeholder="行動やベネフィット"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>サムネタイトル（中央）</span>
                  <input
                    type="text"
                    value={planningDialog.thumbnailTitle}
                    onChange={(event) => handlePlanningFieldChange("thumbnailTitle", event.target.value)}
                    placeholder="その人間関係、もう捨てていい。"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>サムネ生成プロンプト / 指示</span>
                <textarea
                  value={planningDialog.thumbnailPrompt}
                  onChange={(event) => handlePlanningFieldChange("thumbnailPrompt", event.target.value)}
                  rows={3}
                  placeholder="情景や掲載したいURL、文字配置の指定など"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>サムネ用 DALL·E プロンプト</span>
                <textarea
                  value={planningDialog.dallePrompt}
                  onChange={(event) => handlePlanningFieldChange("dallePrompt", event.target.value)}
                  rows={3}
                  placeholder="AI画像生成向けの詳細指示があれば記入"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>企画意図</span>
                <textarea
                  value={planningDialog.conceptIntent}
                  onChange={(event) => handlePlanningFieldChange("conceptIntent", event.target.value)}
                  rows={3}
                  placeholder="どんな悩みを持つ人に、何を提供する企画か"
                />
              </label>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>悩みタグ</span>
                  <input
                    type="text"
                    value={planningDialog.primaryTag}
                    onChange={(event) => handlePlanningFieldChange("primaryTag", event.target.value)}
                    placeholder="孤独 / 断捨離 など"
                  />
                </label>
                <label>
                  <span>サブタグ</span>
                  <input
                    type="text"
                    value={planningDialog.secondaryTag}
                    onChange={(event) => handlePlanningFieldChange("secondaryTag", event.target.value)}
                    placeholder="罪悪感 / お金 など"
                  />
                </label>
                <label>
                  <span>ライフシーン</span>
                  <input
                    type="text"
                    value={planningDialog.lifeScene}
                    onChange={(event) => handlePlanningFieldChange("lifeScene", event.target.value)}
                    placeholder="就寝前 / 朝の台所 など"
                  />
                </label>
              </div>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>キーコンセプト</span>
                  <input
                    type="text"
                    value={planningDialog.keyConcept}
                    onChange={(event) => handlePlanningFieldChange("keyConcept", event.target.value)}
                    placeholder="慈悲 / 断捨離 / 養生 など"
                  />
                </label>
                <label>
                  <span>ベネフィット一言</span>
                  <input
                    type="text"
                    value={planningDialog.benefit}
                    onChange={(event) => handlePlanningFieldChange("benefit", event.target.value)}
                    placeholder="罪悪感なく距離を取れる"
                  />
                </label>
                <label>
                  <span>たとえ話イメージ</span>
                  <input
                    type="text"
                    value={planningDialog.analogy}
                    onChange={(event) => handlePlanningFieldChange("analogy", event.target.value)}
                    placeholder="糸を静かにほどく"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>説明文（リード）</span>
                <textarea
                  value={planningDialog.descriptionLead}
                  onChange={(event) => handlePlanningFieldChange("descriptionLead", event.target.value)}
                  rows={3}
                  placeholder="視聴者への呼びかけや動画のゴールを記載"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>説明文（この動画でわかること）</span>
                <textarea
                  value={planningDialog.descriptionTakeaways}
                  onChange={(event) => handlePlanningFieldChange("descriptionTakeaways", event.target.value)}
                  rows={3}
                  placeholder="・ポイントを箇条書きで記入"
                />
              </label>
              {planningDialog.error ? (
                <div className="thumbnail-planning-form__error" role="alert">
                  {planningDialog.error}
                </div>
              ) : null}
              <div className="thumbnail-planning-form__actions">
                <button type="button" onClick={handleClosePlanningDialog} disabled={planningDialog.saving}>
                  キャンセル
                </button>
                <button type="submit" className="thumbnail-planning-form__submit" disabled={planningDialog.saving}>
                  {planningDialog.saving ? "作成中…" : "企画行を作成"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
      {generateDialog ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={handleCloseGenerateDialog} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                {generateDialog.channel} / {generateDialog.video}
              </div>
              <h2>AIでサムネを生成</h2>
            </header>
            <form className="thumbnail-planning-form" onSubmit={(event) => handleGenerateDialogSubmit(event)}>
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>テンプレ</span>
                  <select
                    value={generateDialog.templateId}
                    onChange={(event) => {
                      const nextId = event.target.value;
                      setGenerateDialog((current) => {
                        if (!current) {
                          return current;
                        }
                        const selected =
                          channelTemplates?.channel === current.channel
                            ? channelTemplates.templates.find((tpl) => tpl.id === nextId)
                            : undefined;
                        return {
                          ...current,
                          templateId: nextId,
                          imageModelKey: selected?.image_model_key ?? current.imageModelKey,
                        };
                      });
                    }}
                  >
                    <option value="">（テンプレなし）</option>
                    {(channelTemplates?.channel === generateDialog.channel ? channelTemplates.templates : []).map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>枚数</span>
                  <input
                    type="number"
                    min={1}
                    max={4}
                    value={generateDialog.count}
                    onChange={(event) => handleGenerateDialogFieldChange("count", Number(event.target.value))}
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>ラベル（任意）</span>
                  <input
                    type="text"
                    value={generateDialog.label}
                    onChange={(event) => handleGenerateDialogFieldChange("label", event.target.value)}
                    placeholder="空なら自動で命名"
                  />
                </label>
              </div>
              {planningLoading ? <p className="thumbnail-library__placeholder">企画CSV読込中…</p> : null}
              {planningError ? <p className="thumbnail-library__alert">{planningError}</p> : null}
              <div className="thumbnail-planning-form__grid">
                <label className="thumbnail-planning-form__field--wide">
                  <span>上段（赤）</span>
                  <input
                    type="text"
                    value={generateDialog.copyUpper}
                    onChange={(event) => handleGenerateDialogFieldChange("copyUpper", event.target.value)}
                    placeholder="例: 知らないと危険"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>中段（黄）</span>
                  <input
                    type="text"
                    value={generateDialog.copyTitle}
                    onChange={(event) => handleGenerateDialogFieldChange("copyTitle", event.target.value)}
                    placeholder="例: 99%が誤解"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>下段（白）</span>
                  <input
                    type="text"
                    value={generateDialog.copyLower}
                    onChange={(event) => handleGenerateDialogFieldChange("copyLower", event.target.value)}
                    placeholder="例: 人間関係の本質"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>個別指示（企画CSV: サムネ画像プロンプト）</span>
                <textarea
                  value={generateDialog.thumbnailPrompt}
                  onChange={(event) => handleGenerateDialogFieldChange("thumbnailPrompt", event.target.value)}
                  rows={3}
                  placeholder="空でもOK。URLや追加の指示があれば記入。"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked" style={{ flexDirection: "row", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={generateDialog.saveToPlanning}
                  onChange={(event) => handleGenerateDialogFieldChange("saveToPlanning", event.target.checked)}
                />
                <span>この内容を企画CSVに保存してから生成する</span>
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>プロンプト（任意）</span>
                <textarea
                  value={generateDialog.prompt}
                  onChange={(event) => handleGenerateDialogFieldChange("prompt", event.target.value)}
                  rows={6}
                  placeholder="空ならテンプレ + 企画CSVの値から組み立てます（上の3段テキスト等）。"
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked">
                <span>画像モデル（任意）</span>
                <select
                  value={generateDialog.imageModelKey}
                  onChange={(event) => handleGenerateDialogFieldChange("imageModelKey", event.target.value)}
                >
                  <option value="">（テンプレの指定を使う）</option>
                  {imageModels.map((model) => {
                    const imageUnit = parsePricingNumber(model.pricing?.image ?? null);
                    const costSuffix = imageUnit !== null ? ` / ${formatUsdAmount(imageUnit)}/unit` : "";
                    return (
                      <option key={model.key} value={model.key}>
                        {model.key} ({model.provider}{costSuffix})
                      </option>
                    );
                  })}
                </select>
              </label>
              {generateDialogResolvedModelKey ? (
                generateDialogResolvedModel ? (
                    generateDialogResolvedModel.provider === "openrouter" ? (
                      generateDialogResolvedPricing ? (
                      <p className="thumbnail-library__placeholder">
                        料金(OpenRouter /models, USD): image{" "}
                        {generateDialogImageUnitUsd !== null ? `${formatUsdAmount(generateDialogImageUnitUsd)}/unit` : "—"}
                        {generateDialogRequestUnitUsd !== null
                          ? `, request ${formatUsdAmount(generateDialogRequestUnitUsd)}/req`
                          : ""}
                        {generateDialogPromptTokenUsd !== null
                          ? `, 入力 ${formatUsdPerMillionTokens(generateDialogPromptTokenUsd)}`
                          : ""}
                        {generateDialogCompletionTokenUsd !== null
                          ? `, 出力 ${formatUsdPerMillionTokens(generateDialogCompletionTokenUsd)}`
                          : ""}
                        {generateDialogImageSubtotalUsd !== null
                          ? ` / 今回(${generateDialog.count}枚)のimage単価分: ${formatUsdAmount(generateDialogImageSubtotalUsd)}`
                          : ""}
                        {generateDialogRequestSubtotalUsd !== null && generateDialogRequestSubtotalUsd !== 0
                          ? ` (request合計: ${formatUsdAmount(generateDialogRequestSubtotalUsd)})`
                          : ""}
                        {generateDialogResolvedPricingUpdatedAt
                          ? ` (単価更新: ${formatDate(generateDialogResolvedPricingUpdatedAt)})`
                          : ""}
                        {" ※実コストは生成後に variants の「実コスト」に記録"}
                      </p>
                    ) : (
                      <p className="thumbnail-library__placeholder">
                        料金(OpenRouter): 単価情報を取得できませんでした（{generateDialogResolvedModelKey}）。
                      </p>
                    )
                  ) : (
                    <p className="thumbnail-library__placeholder">
                      料金: {generateDialogResolvedModelKey} は OpenRouter 以外のプロバイダ（{generateDialogResolvedModel.provider}）のため、単価表示対象外です。
                    </p>
                  )
                ) : (
                  <p className="thumbnail-library__placeholder">
                    料金: モデル情報が見つかりませんでした（{generateDialogResolvedModelKey}）。
                  </p>
                )
              ) : null}
              <div className="thumbnail-planning-form__grid">
                <label>
                  <span>ステータス</span>
                  <select
                    value={generateDialog.status}
                    onChange={(event) =>
                      handleGenerateDialogFieldChange("status", event.target.value as ThumbnailVariantStatus)
                    }
                  >
                    {VARIANT_STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>タグ（カンマ区切り）</span>
                  <input
                    type="text"
                    value={generateDialog.tags}
                    onChange={(event) => handleGenerateDialogFieldChange("tags", event.target.value)}
                    placeholder="例: 人物, 共感"
                  />
                </label>
              </div>
              <label className="thumbnail-planning-form__field--stacked">
                <span>メモ（任意）</span>
                <textarea
                  value={generateDialog.notes}
                  onChange={(event) => handleGenerateDialogFieldChange("notes", event.target.value)}
                  rows={2}
                />
              </label>
              <label className="thumbnail-planning-form__field--stacked" style={{ flexDirection: "row", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={generateDialog.makeSelected}
                  onChange={(event) => handleGenerateDialogFieldChange("makeSelected", event.target.checked)}
                />
                <span>生成した1枚目を「採用中」にする</span>
              </label>
              {generateDialog.error ? (
                <div className="thumbnail-planning-form__error" role="alert">
                  {generateDialog.error}
                </div>
              ) : null}
              <div className="thumbnail-planning-form__actions">
                <button type="button" onClick={handleCloseGenerateDialog} disabled={generateDialog.saving}>
                  キャンセル
                </button>
                <button type="submit" className="thumbnail-planning-form__submit" disabled={generateDialog.saving}>
                  {generateDialog.saving ? "生成中…" : "生成"}
                </button>
              </div>
            </form>
          </div>
        </div>
        ) : null}
        {layerTuningDialog ? (
          <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
            <div className="thumbnail-planning-dialog__backdrop" onClick={handleCloseLayerTuningDialog} />
            <div className="thumbnail-planning-dialog__panel">
	              <header className="thumbnail-planning-dialog__header">
	                <div className="thumbnail-planning-dialog__eyebrow">
	                  {layerTuningDialog.channel} / {layerTuningDialog.video}
	                  {layerTuningDialog.stable ? ` / ${layerTuningDialog.stable}` : ""}
	                </div>
	                <h2>サムネ調整（Layer Specs）</h2>
	                <p className="thumbnail-planning-dialog__meta">{layerTuningDialog.projectTitle}</p>
                {channelHasTwoUpVariants ||
                channelHasThreeUpVariants ||
                galleryVariantMode === "two_up" ||
                galleryVariantMode === "three_up" ? (
	                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: 8 }}>
	                    <span className="muted small-text">
	                      {channelHasThreeUpVariants || galleryVariantMode === "three_up" ? "3案:" : "2案:"}
	                    </span>
	                    <button
	                      type="button"
	                      className={`btn btn--ghost ${layerTuningDialog.stable === "00_thumb_1" ? "is-active" : ""}`}
	                      onClick={() => handleLayerTuningStableChange("00_thumb_1")}
	                      disabled={layerTuningDialog.loading || layerTuningDialog.saving || layerTuningDialog.building}
	                    >
	                      00_thumb_1
	                    </button>
	                    <button
	                      type="button"
	                      className={`btn btn--ghost ${layerTuningDialog.stable === "00_thumb_2" ? "is-active" : ""}`}
	                      onClick={() => handleLayerTuningStableChange("00_thumb_2")}
	                      disabled={layerTuningDialog.loading || layerTuningDialog.saving || layerTuningDialog.building}
	                    >
	                      00_thumb_2
	                    </button>
	                    {channelHasThreeUpVariants || galleryVariantMode === "three_up" ? (
	                      <button
	                        type="button"
	                        className={`btn btn--ghost ${layerTuningDialog.stable === "00_thumb_3" ? "is-active" : ""}`}
	                        onClick={() => handleLayerTuningStableChange("00_thumb_3")}
	                        disabled={layerTuningDialog.loading || layerTuningDialog.saving || layerTuningDialog.building}
	                      >
	                        00_thumb_3
	                      </button>
	                    ) : null}
	                  </div>
	                ) : null}
	              </header>
              {layerTuningDialog.loading ? (
                <div className="thumbnail-planning-form">
                  <p>読み込み中…</p>
                </div>
              ) : (
	                <form
	                  className="thumbnail-planning-form"
	                  onSubmit={(event) => {
	                    event.preventDefault();
	                    handleSaveLayerTuning("save_and_build");
	                  }}
	                >
                  <label className="thumbnail-planning-form__field--stacked">
                    <span>コメント（メモ）</span>
                    <textarea
                      value={layerTuningDialog.commentDraft}
                      onChange={handleLayerTuningCommentChange}
                      rows={2}
                      placeholder="例: 画像をもう少し下に / もっと明るく"
                    />
                  </label>
                  <p className="thumbnail-library__placeholder" style={{ marginTop: -8, marginBottom: 12 }}>
                    コメントの解釈（パラメータ反映）はこのチャットで行います（UI/API での自動反映はしません）。
                  </p>
                  {(() => {
                    const feedback = cardFeedback[layerTuningDialog.cardKey];
                    if (!feedback) {
                      return null;
                    }
                    return (
                      <div className={`thumbnail-card__feedback thumbnail-card__feedback--${feedback.type}`} role="status">
                        {feedback.message}
                      </div>
                    );
                  })()}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("reset_all")}>
                      リセット
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("bg_bright")}>
                      背景: 明るめ
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("bg_dark")}>
                      背景: 暗め
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("bg_vivid")}>
                      背景: 彩度UP
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("bg_zoom_in")}>
                      背景: ズーム
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("text_big")}>
                      文字: 大きめ
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={() => applyLayerTuningPreset("text_small")}>
                      文字: 小さめ
                    </button>
                    {layerTuningDialog.context?.portrait_available ? (
                      <>
                        <button
                          type="button"
                          className="btn btn--ghost"
                          onClick={() => applyLayerTuningPreset("portrait_zoom")}
                        >
                          肖像: アップ
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost"
                          onClick={() => applyLayerTuningPreset("portrait_bright")}
                        >
                          肖像: 明るく
                        </button>
                      </>
	                    ) : null}
	                  </div>

	                  <h3 style={{ marginTop: 18, marginBottom: 8 }}>文字（上/中/下）</h3>
	                  <div className="thumbnail-planning-form__grid" style={{ marginBottom: 12 }}>
	                    {(() => {
	                      const videoKey = normalizeVideoInput(layerTuningDialog.video) || layerTuningDialog.video;
	                      const planningRow = planningRowsByVideo[videoKey] ?? {};
	                      const planningUpper = String(planningRow["サムネタイトル上"] ?? "").trim();
	                      const planningTitle = String(planningRow["サムネタイトル"] ?? "").trim();
	                      const planningLower = String(planningRow["サムネタイトル下"] ?? "").trim();
	                      const slots = layerTuningDialog.context?.text_slots ?? {};
	                      const defaultUpper = String(slots["line1"] ?? slots["upper"] ?? slots["top"] ?? planningUpper ?? "").trim();
	                      const defaultTitle = String(slots["line2"] ?? slots["title"] ?? slots["main"] ?? planningTitle ?? "").trim();
	                      const defaultLower = String(slots["line3"] ?? slots["lower"] ?? slots["accent"] ?? planningLower ?? "").trim();

	                      const upperOverridden = isLayerTuningLeafOverridden(layerTuningDialog, "overrides.copy_override.upper");
	                      const titleOverridden = isLayerTuningLeafOverridden(layerTuningDialog, "overrides.copy_override.title");
	                      const lowerOverridden = isLayerTuningLeafOverridden(layerTuningDialog, "overrides.copy_override.lower");

	                      const upperValue = upperOverridden
	                        ? String(layerTuningDialog.overridesLeaf["overrides.copy_override.upper"] ?? "")
	                        : defaultUpper;
	                      const titleValue = titleOverridden
	                        ? String(layerTuningDialog.overridesLeaf["overrides.copy_override.title"] ?? "")
	                        : defaultTitle;
	                      const lowerValue = lowerOverridden
	                        ? String(layerTuningDialog.overridesLeaf["overrides.copy_override.lower"] ?? "")
	                        : defaultLower;

	                      return (
	                        <>
		                    <label className="thumbnail-planning-form__field--wide">
		                      <span>上段（copy_override.upper / line1）</span>
		                      <input
		                        type="text"
		                        value={upperValue}
	                        onChange={(event) => {
	                          const raw = event.target.value;
	                          setLayerTuningOverrideLeaf("overrides.copy_override.upper", raw.trim() ? raw : null);
	                        }}
		                        style={upperOverridden ? { background: "rgba(59, 130, 246, 0.06)" } : undefined}
		                      />
		                    </label>
		                    <label className="thumbnail-planning-form__field--wide">
		                      <span>中段（copy_override.title / line2）</span>
		                      <input
		                        type="text"
		                        value={titleValue}
	                        onChange={(event) => {
	                          const raw = event.target.value;
	                          setLayerTuningOverrideLeaf("overrides.copy_override.title", raw.trim() ? raw : null);
	                        }}
		                        style={titleOverridden ? { background: "rgba(59, 130, 246, 0.06)" } : undefined}
		                      />
		                    </label>
		                    <label className="thumbnail-planning-form__field--wide">
		                      <span>下段（copy_override.lower / line3）</span>
		                      <input
		                        type="text"
		                        value={lowerValue}
	                        onChange={(event) => {
	                          const raw = event.target.value;
	                          setLayerTuningOverrideLeaf("overrides.copy_override.lower", raw.trim() ? raw : null);
	                        }}
		                        style={lowerOverridden ? { background: "rgba(59, 130, 246, 0.06)" } : undefined}
		                      />
		                    </label>
	                        </>
		                      );
		                    })()}
	                  </div>

	                  <h3 style={{ marginTop: 18, marginBottom: 8 }}>プレビュー（ドラッグで位置調整）</h3>
	                  <details style={{ marginBottom: 8 }} open>
	                    <summary className="muted small-text" style={{ cursor: "pointer" }}>
	                      素材の差し替え（画像アップロード / CapCut書き出しOK）
	                    </summary>
	                    <div className="thumbnail-planning-form__grid" style={{ marginTop: 10 }}>
	                      <label className="thumbnail-planning-form__field--wide">
	                        <span>背景（10_bg.png）</span>
	                        <input
	                          type="file"
	                          accept="image/png,image/jpeg,image/webp"
	                          onChange={(event) => {
	                            const input = event.currentTarget;
	                            const file = input.files?.[0];
	                            input.value = "";
	                            if (!file) {
	                              return;
	                            }
	                            void handleReplaceLayerTuningAsset("10_bg", file);
	                          }}
	                        />
	                      </label>
	                      {layerTuningDialog.context?.portrait_available ? (
	                        <label className="thumbnail-planning-form__field--wide">
	                          <span>肖像（20_portrait.png）</span>
	                          <input
	                            type="file"
	                            accept="image/png,image/jpeg,image/webp"
	                            onChange={(event) => {
	                              const input = event.currentTarget;
	                              const file = input.files?.[0];
	                              input.value = "";
	                              if (!file) {
	                                return;
	                              }
	                              void handleReplaceLayerTuningAsset("20_portrait", file);
	                            }}
	                          />
	                        </label>
	                      ) : null}
	                      <label className="thumbnail-planning-form__field--wide">
	                        <span>
	                          出力（{layerTuningDialog.stable ? `${layerTuningDialog.stable}.png` : "00_thumb.png"}）
	                        </span>
	                        <input
	                          type="file"
	                          accept="image/png,image/jpeg,image/webp"
	                          onChange={(event) => {
	                            const input = event.currentTarget;
	                            const file = input.files?.[0];
	                            input.value = "";
	                            if (!file) {
	                              return;
	                            }
	                            const slot = layerTuningDialog.stable ? layerTuningDialog.stable : "00_thumb";
	                            void handleReplaceLayerTuningAsset(slot, file);
	                          }}
	                        />
	                      </label>
	                      <div className="muted small-text" style={{ gridColumn: "1 / -1" }}>
	                        CapCutで書き出したPNG/JPG/WebPをそのまま差し替えできます（保存先: workspaces/thumbnails/assets/{layerTuningDialog.channel}/{layerTuningDialog.video}/）。
	                      </div>
	                    </div>
	                  </details>
	                  <div style={{ display: "grid", gap: 12 }}>
		                      <div
		                        style={{
		                          width: "min(1040px, 100%)",
		                          padding: "clamp(18px, 5vw, 180px)",
		                          borderRadius: 14,
		                          background: layerTuningGuidesEnabled
		                            ? "linear-gradient(180deg, rgba(15, 23, 42, 0.06) 0%, rgba(15, 23, 42, 0.03) 100%)"
		                            : "rgba(15, 23, 42, 0.02)",
	                          backgroundImage: layerTuningGuidesEnabled
	                            ? "linear-gradient(rgba(15, 23, 42, 0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(15, 23, 42, 0.06) 1px, transparent 1px)"
	                            : undefined,
	                          backgroundSize: layerTuningGuidesEnabled ? "24px 24px" : undefined,
	                          border: "1px solid rgba(15, 23, 42, 0.14)",
	                          overflow: "hidden",
	                          boxSizing: "border-box",
	                        }}
                        onDragEnter={handleLayerTuningPreviewDragEnter}
                        onDragLeave={handleLayerTuningPreviewDragLeave}
                        onDragOver={handleLayerTuningPreviewDragOver}
                        onDrop={handleLayerTuningPreviewDrop}
                      >
	                    <div
                      ref={layerTuningPreviewRef}
                      tabIndex={0}
                      aria-label="レイヤ調整プレビュー"
                      onKeyDown={handleLayerTuningPreviewKeyDown}
                      onPointerDownCapture={() => {
                        layerTuningPreviewRef.current?.focus({ preventScroll: true });
                      }}
                      style={{
                        width: "100%",
                        aspectRatio: "16 / 9",
                        borderRadius: 10,
                        overflow: "visible",
                        border: "1px solid rgba(15, 23, 42, 0.25)",
                        background: "#0b0b0f",
                        position: "relative",
                        userSelect: "none",
                        touchAction: "none",
                      }}
                    >
                      {(() => {
                        const width = layerTuningPreviewSize.width;
                        const height = layerTuningPreviewSize.height;

                        const bgZoom = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.zoom", 1.0));
                        const bgPanX = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_x", 0.0)),
                          LAYER_TUNING_BG_PAN_MIN,
                          LAYER_TUNING_BG_PAN_MAX
                        );
                        const bgPanY = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_y", 0.0)),
                          LAYER_TUNING_BG_PAN_MIN,
                          LAYER_TUNING_BG_PAN_MAX
                        );
                        const bgBrightness = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.brightness", 1.0)
                        );
                        const bgContrast = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.contrast", 1.0));
                        const bgColor = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.color", 1.0));
                        const bgFilter = `brightness(${bgBrightness}) contrast(${bgContrast}) saturate(${bgColor})`;

                        const shiftX = (() => {
                          if (!width) {
                            return 0;
                          }
                          if (bgZoom > 1.0001) {
                            return -((bgZoom - 1) * width * 0.5 * (1 + bgPanX));
                          }
                          return -(width * 0.5 * bgPanX);
                        })();
                        const shiftY = (() => {
                          if (!height) {
                            return 0;
                          }
                          if (bgZoom > 1.0001) {
                            return -((bgZoom - 1) * height * 0.5 * (1 + bgPanY));
                          }
                          return -(height * 0.5 * bgPanY);
                        })();

                        const overlaysEnabled = (() => {
                          const enabledKey = "overrides.overlays.left_tsz.enabled";
                          if (hasLayerTuningLeafValue(layerTuningDialog, enabledKey)) {
                            return Boolean(resolveLayerTuningLeafValue(layerTuningDialog, enabledKey, false));
                          }
                          return (
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.color") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_left") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_right") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x0") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x1")
                          );
                        })();
                        const overlaysLeftColor = String(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.color", "#000000")
                        );
                        const overlaysLeftAlphaLeft = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_left", 0.65)
                        );
                        const overlaysLeftAlphaRight = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_right", 0.0)
                        );
                        const overlaysLeftX0 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x0", 0.0)),
                          0,
                          1
                        );
                        const overlaysLeftX1 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x1", 0.52)),
                          0,
                          1
                        );

                        const topBandEnabled = (() => {
                          const enabledKey = "overrides.overlays.top_band.enabled";
                          if (hasLayerTuningLeafValue(layerTuningDialog, enabledKey)) {
                            return Boolean(resolveLayerTuningLeafValue(layerTuningDialog, enabledKey, false));
                          }
                          return (
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.color") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_top") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_bottom") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y0") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y1")
                          );
                        })();
                        const topBandColor = String(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.color", "#000000")
                        );
                        const topBandAlphaTop = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_top", 0.7)
                        );
                        const topBandAlphaBottom = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_bottom", 0.0)
                        );
                        const topBandY0 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y0", 0.0)),
                          0,
                          1
                        );
                        const topBandY1 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y1", 0.25)),
                          0,
                          1
                        );

                        const bottomBandEnabled = (() => {
                          const enabledKey = "overrides.overlays.bottom_band.enabled";
                          if (hasLayerTuningLeafValue(layerTuningDialog, enabledKey)) {
                            return Boolean(resolveLayerTuningLeafValue(layerTuningDialog, enabledKey, false));
                          }
                          return (
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.color") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_top") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_bottom") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y0") ||
                            hasLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y1")
                          );
                        })();
                        const bottomBandColor = String(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.color", "#000000")
                        );
                        const bottomBandAlphaTop = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_top", 0.0)
                        );
                        const bottomBandAlphaBottom = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_bottom", 0.8)
                        );
                        const bottomBandY0 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y0", 0.7)),
                          0,
                          1
                        );
                        const bottomBandY1 = clampNumber(
                          Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y1", 1.0)),
                          0,
                          1
                        );

                        const portraitDefaultEnabled = layerTuningStable !== "00_thumb_2";
                        const portraitEnabled =
                          Boolean(layerTuningDialog.context?.portrait_available) &&
                          Boolean(
                            resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.portrait.enabled",
                              portraitDefaultEnabled
                            )
                          );
                        const portraitSuppressBgDefault =
                          layerTuningDialog.channel === "CH26" && portraitEnabled;
                        const portraitSuppressBgRaw = Boolean(
                          resolveLayerTuningLeafValue(
                            layerTuningDialog,
                            "overrides.portrait.suppress_bg",
                            portraitSuppressBgDefault
                          )
                        );
                        // CH26 backgrounds may include a face; when portrait is enabled we must suppress it to avoid "double face".
                        const portraitSuppressBgForced = portraitEnabled && layerTuningDialog.channel === "CH26";
                        const portraitSuppressBg = portraitEnabled && (portraitSuppressBgForced || portraitSuppressBgRaw);
                        const portraitZoom = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.zoom", 1.0));
                        const portraitOffX = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_x", 0.0)
                        );
                        const portraitOffY = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_y", 0.0)
                        );
                        const portraitBrightness = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_brightness", 1.2)
                        );
                        const portraitContrast = Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_contrast", 1.08)
                        );
                        const portraitColor = Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_color", 0.98));
                        const portraitFilter = `brightness(${portraitBrightness}) contrast(${portraitContrast}) saturate(${portraitColor})`;

                        const rawPortraitBox = (layerTuningDialog.context as any)?.portrait_dest_box_norm;
                        const portraitBox = Array.isArray(rawPortraitBox) && rawPortraitBox.length === 4
                          ? rawPortraitBox.map((v: any) => Number(v))
                          : [0.29, 0.06, 0.42, 0.76];
                        const portraitAnchor = String((layerTuningDialog.context as any)?.portrait_anchor ?? "bottom_center");
                        const anchorIsCenter = portraitAnchor.toLowerCase() === "center";
                        const objectPos = anchorIsCenter ? "50% 50%" : "50% 100%";
                        const origin = anchorIsCenter ? "50% 50%" : "50% 100%";

                        const boxLeft = width ? Math.round(width * portraitBox[0]) : 0;
                        const boxTop = height ? Math.round(height * portraitBox[1]) : 0;
                        const boxW = width ? Math.round(width * portraitBox[2]) : 0;
                        const boxH = height ? Math.round(height * portraitBox[3]) : 0;
                        const offPxX = width ? portraitOffX * width : 0;
                        const offPxY = height ? portraitOffY * height : 0;

                        const suppressBgOverlayCss = (() => {
                          if (!portraitSuppressBg) {
                            return null;
                          }
                          if (!width || !height || !boxW || !boxH) {
                            return null;
                          }
                          const minDim = Math.max(1, Math.min(width, height));
                          // Hard suppression: ensure no background "ghost face" remains visible around the portrait.
                          // Must cover both the original box and the offset box so dragging doesn't reveal the old face.
                          const pad = Math.max(0, Math.round(minDim * 0.35));
                          const baseLeft = boxLeft;
                          const baseTop = boxTop;
                          const shiftedLeft = boxLeft + offPxX;
                          const shiftedTop = boxTop + offPxY;
                          const left = Math.min(baseLeft, shiftedLeft);
                          const top = Math.min(baseTop, shiftedTop);
                          const right = Math.max(baseLeft + boxW, shiftedLeft + boxW);
                          const bottom = Math.max(baseTop + boxH, shiftedTop + boxH);
                          const cx = Math.round(left + (right - left) * 0.5);
                          const cy = Math.round(top + (bottom - top) * 0.5);
                          const rx = Math.max(1, Math.round((right - left) * 0.5 + pad));
                          const ry = Math.max(1, Math.round((bottom - top) * 0.5 + pad));
                          return `radial-gradient(ellipse ${rx}px ${ry}px at ${cx}px ${cy}px, rgba(0,0,0,1) 0%, rgba(0,0,0,1) 96%, rgba(0,0,0,0) 100%)`;
                        })();

                        const leftTszGradient = `linear-gradient(90deg, ${hexToRgba(
                          overlaysLeftColor,
                          overlaysLeftAlphaLeft
                        )} 0%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaLeft)} ${(overlaysLeftX0 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaRight)} ${(overlaysLeftX1 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(overlaysLeftColor, overlaysLeftAlphaRight)} 100%)`;
                        const topBandGradient = `linear-gradient(180deg, ${hexToRgba(
                          topBandColor,
                          topBandAlphaTop
                        )} 0%, ${hexToRgba(topBandColor, topBandAlphaTop)} ${(topBandY0 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(topBandColor, topBandAlphaBottom)} ${(topBandY1 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(topBandColor, topBandAlphaBottom)} 100%)`;
                        const bottomBandGradient = `linear-gradient(180deg, ${hexToRgba(
                          bottomBandColor,
                          bottomBandAlphaTop
                        )} 0%, ${hexToRgba(bottomBandColor, bottomBandAlphaTop)} ${(bottomBandY0 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(bottomBandColor, bottomBandAlphaBottom)} ${(bottomBandY1 * 100).toFixed(
                          2
                        )}%, ${hexToRgba(bottomBandColor, bottomBandAlphaBottom)} 100%)`;

                        return (
                          <>
                            <div
                              style={{
                                position: "absolute",
                                inset: 0,
                                cursor: "grab",
                                zIndex: 0,
                              }}
                              onPointerDown={beginLayerTuningPreviewBgDrag}
                              onPointerMove={handleLayerTuningPreviewDragMove}
                              onPointerUp={handleLayerTuningPreviewDragEnd}
                              onPointerCancel={handleLayerTuningPreviewDragEnd}
                              onWheel={handleLayerTuningPreviewBgWheel}
                            >
                              {layerTuningBgPreviewSrc ? (
                                <div style={{ position: "absolute", inset: 0 }}>
                                  <div style={{ position: "absolute", left: shiftX, top: shiftY, width: "100%", height: "100%" }}>
                                    <div style={{ width: "100%", height: "100%", transform: `scale(${bgZoom})`, transformOrigin: "top left" }}>
                                      <img
                                        src={layerTuningBgPreviewSrc}
                                        alt="bg"
                                        draggable={false}
                                        style={{
                                          width: "100%",
                                          height: "100%",
                                          objectFit: "cover",
                                          filter: bgFilter,
                                          pointerEvents: "none",
                                        }}
                                        onError={() => {
                                          if (!layerTuningChannel || !layerTuningVideo) {
                                            setLayerTuningBgPreviewSrc(null);
                                            return;
                                          }
                                          const candidates = [
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/10_bg.png`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/10_bg.jpg`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/10_bg.jpeg`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/10_bg.webp`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/90_bg_ai_raw.png`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/90_bg_ai_raw.jpg`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/90_bg_ai_raw.jpeg`),
                                            resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/90_bg_ai_raw.webp`),
                                          ];
                                          setLayerTuningBgPreviewSrc((current) => {
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
                                  {suppressBgOverlayCss ? (
                                    <div
                                      style={{
                                        position: "absolute",
                                        inset: 0,
                                        pointerEvents: "none",
                                        backgroundImage: suppressBgOverlayCss,
                                        zIndex: 1,
                                      }}
                                    />
                                  ) : null}
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
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  backgroundImage: leftTszGradient,
                                  pointerEvents: "none",
                                  zIndex: 20,
                                }}
                              />
                            ) : null}
                            {topBandEnabled ? (
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  backgroundImage: topBandGradient,
                                  pointerEvents: "none",
                                  zIndex: 20,
                                }}
                              />
                            ) : null}
                            {bottomBandEnabled ? (
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  backgroundImage: bottomBandGradient,
                                  pointerEvents: "none",
                                  zIndex: 20,
                                }}
                              />
                            ) : null}

                            {layerTuningElements.length && layerTuningChannel && layerTuningVideo ? (
                              <>
                                <div style={{ position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none" }}>
                                  {layerTuningElements
                                    .filter((el) => String((el as any)?.layer ?? "above_portrait") === "below_portrait")
                                    .slice()
                                    .sort((a, b) => Number((a as any)?.z ?? 0) - Number((b as any)?.z ?? 0))
                                    .map((el) => {
                                      const id = String(el?.id ?? "").trim();
                                      if (!id) {
                                        return null;
                                      }
                                      const kind = String((el as any)?.kind ?? "").trim();
                                      const x = clampNumber(
                                        Number((el as any)?.x ?? 0.5),
                                        LAYER_TUNING_ELEMENT_XY_MIN,
                                        LAYER_TUNING_ELEMENT_XY_MAX
                                      );
                                      const y = clampNumber(
                                        Number((el as any)?.y ?? 0.5),
                                        LAYER_TUNING_ELEMENT_XY_MIN,
                                        LAYER_TUNING_ELEMENT_XY_MAX
                                      );
                                      const wNorm = clampNumber(Number((el as any)?.w ?? 0.2), 0.01, 4);
                                      const hNorm = clampNumber(Number((el as any)?.h ?? 0.2), 0.01, 4);
                                      const left = (x - wNorm / 2) * width;
                                      const top = (y - hNorm / 2) * height;
                                      const wPx = wNorm * width;
                                      const hPx = hNorm * height;
                                      const rotation = clampNumber(Number((el as any)?.rotation_deg ?? 0), -180, 180);
                                      const opacity = clampNumber(Number((el as any)?.opacity ?? 1), 0, 1);
                                      const fill = String((el as any)?.fill ?? "").trim() || "#ffffff";
                                      const stroke = (el as any)?.stroke ?? null;
                                      const strokeWidth = stroke ? Number(stroke.width_px ?? 0) : 0;
                                      const strokeColor = stroke ? String(stroke.color ?? "").trim() : "";
                                      const borderRadius = kind === "circle" ? "50%" : 12;
                                      const selected =
                                        layerTuningSelectedAsset === "element" && layerTuningSelectedElementId === id;
                                      const srcPath = String((el as any)?.src_path ?? "").trim();
                                      const resolvedUrl = srcPath ? resolveElementSrcUrl(layerTuningChannel, layerTuningVideo, srcPath) : null;
                                      const srcUrl = resolvedUrl ? resolveApiUrl(resolvedUrl) : null;

                                      return (
                                        <div
                                          key={id}
                                          style={{
                                            position: "absolute",
                                            left,
                                            top,
                                            width: wPx,
                                            height: hPx,
                                            transform: `rotate(${rotation}deg)`,
                                            transformOrigin: "50% 50%",
                                            opacity,
                                            cursor: "grab",
                                            touchAction: "none",
                                            pointerEvents: "auto",
                                            borderRadius,
                                            overflow: "hidden",
                                            boxSizing: "border-box",
                                            background: kind === "image" ? "transparent" : fill,
                                            border:
                                              Number.isFinite(strokeWidth) && strokeWidth > 0
                                                ? `${clampNumber(strokeWidth, 0, 64)}px solid ${strokeColor || "#000000"}`
                                                : "none",
                                            outline: selected ? "2px solid rgba(59, 130, 246, 0.95)" : "none",
                                            outlineOffset: 2,
                                          }}
                                          onPointerDown={(event) => beginLayerTuningPreviewElementDrag(event, id)}
                                          onPointerMove={handleLayerTuningPreviewDragMove}
                                          onPointerUp={handleLayerTuningPreviewDragEnd}
                                          onPointerCancel={handleLayerTuningPreviewDragEnd}
                                        >
                                          {kind === "image" ? (
                                            srcUrl ? (
                                              <img
                                                src={srcUrl}
                                                alt=""
                                                draggable={false}
                                                style={{
                                                  width: "100%",
                                                  height: "100%",
                                                  objectFit: "cover",
                                                  pointerEvents: "none",
                                                }}
                                              />
                                            ) : (
                                              <div
                                                style={{
                                                  width: "100%",
                                                  height: "100%",
                                                  display: "flex",
                                                  alignItems: "center",
                                                  justifyContent: "center",
                                                  fontSize: 12,
                                                  color: "rgba(255,255,255,0.8)",
                                                  background: "rgba(0,0,0,0.35)",
                                                }}
                                              >
                                                image missing
                                              </div>
                                            )
                                          ) : null}
                                        </div>
                                      );
                                    })}
                                </div>
                                <div style={{ position: "absolute", inset: 0, zIndex: 18, pointerEvents: "none" }}>
                                  {layerTuningElements
                                    .filter((el) => String((el as any)?.layer ?? "above_portrait") !== "below_portrait")
                                    .slice()
                                    .sort((a, b) => Number((a as any)?.z ?? 0) - Number((b as any)?.z ?? 0))
                                    .map((el) => {
                                      const id = String(el?.id ?? "").trim();
                                      if (!id) {
                                        return null;
                                      }
                                      const kind = String((el as any)?.kind ?? "").trim();
                                      const x = clampNumber(
                                        Number((el as any)?.x ?? 0.5),
                                        LAYER_TUNING_ELEMENT_XY_MIN,
                                        LAYER_TUNING_ELEMENT_XY_MAX
                                      );
                                      const y = clampNumber(
                                        Number((el as any)?.y ?? 0.5),
                                        LAYER_TUNING_ELEMENT_XY_MIN,
                                        LAYER_TUNING_ELEMENT_XY_MAX
                                      );
                                      const wNorm = clampNumber(Number((el as any)?.w ?? 0.2), 0.01, 4);
                                      const hNorm = clampNumber(Number((el as any)?.h ?? 0.2), 0.01, 4);
                                      const left = (x - wNorm / 2) * width;
                                      const top = (y - hNorm / 2) * height;
                                      const wPx = wNorm * width;
                                      const hPx = hNorm * height;
                                      const rotation = clampNumber(Number((el as any)?.rotation_deg ?? 0), -180, 180);
                                      const opacity = clampNumber(Number((el as any)?.opacity ?? 1), 0, 1);
                                      const fill = String((el as any)?.fill ?? "").trim() || "#ffffff";
                                      const stroke = (el as any)?.stroke ?? null;
                                      const strokeWidth = stroke ? Number(stroke.width_px ?? 0) : 0;
                                      const strokeColor = stroke ? String(stroke.color ?? "").trim() : "";
                                      const borderRadius = kind === "circle" ? "50%" : 12;
                                      const selected =
                                        layerTuningSelectedAsset === "element" && layerTuningSelectedElementId === id;
                                      const srcPath = String((el as any)?.src_path ?? "").trim();
                                      const resolvedUrl = srcPath ? resolveElementSrcUrl(layerTuningChannel, layerTuningVideo, srcPath) : null;
                                      const srcUrl = resolvedUrl ? resolveApiUrl(resolvedUrl) : null;

                                      return (
                                        <div
                                          key={id}
                                          style={{
                                            position: "absolute",
                                            left,
                                            top,
                                            width: wPx,
                                            height: hPx,
                                            transform: `rotate(${rotation}deg)`,
                                            transformOrigin: "50% 50%",
                                            opacity,
                                            cursor: "grab",
                                            touchAction: "none",
                                            pointerEvents: "auto",
                                            borderRadius,
                                            overflow: "hidden",
                                            boxSizing: "border-box",
                                            background: kind === "image" ? "transparent" : fill,
                                            border:
                                              Number.isFinite(strokeWidth) && strokeWidth > 0
                                                ? `${clampNumber(strokeWidth, 0, 64)}px solid ${strokeColor || "#000000"}`
                                                : "none",
                                            outline: selected ? "2px solid rgba(59, 130, 246, 0.95)" : "none",
                                            outlineOffset: 2,
                                          }}
                                          onPointerDown={(event) => beginLayerTuningPreviewElementDrag(event, id)}
                                          onPointerMove={handleLayerTuningPreviewDragMove}
                                          onPointerUp={handleLayerTuningPreviewDragEnd}
                                          onPointerCancel={handleLayerTuningPreviewDragEnd}
                                        >
                                          {kind === "image" ? (
                                            srcUrl ? (
                                              <img
                                                src={srcUrl}
                                                alt=""
                                                draggable={false}
                                                style={{
                                                  width: "100%",
                                                  height: "100%",
                                                  objectFit: "cover",
                                                  pointerEvents: "none",
                                                }}
                                              />
                                            ) : (
                                              <div
                                                style={{
                                                  width: "100%",
                                                  height: "100%",
                                                  display: "flex",
                                                  alignItems: "center",
                                                  justifyContent: "center",
                                                  fontSize: 12,
                                                  color: "rgba(255,255,255,0.8)",
                                                  background: "rgba(0,0,0,0.35)",
                                                }}
                                              >
                                                image missing
                                              </div>
                                            )
                                          ) : null}
                                        </div>
                                      );
                                    })}
                                </div>
                              </>
                            ) : null}

		                            <div
		                              style={{
		                                position: "absolute",
		                                inset: 0,
		                                pointerEvents: "none",
		                                zIndex: 30,
		                              }}
		                            >
	                              {Object.keys(layerTuningTextSlotImages).length > 0 ? (
	                                Object.entries(layerTuningTextSlotImages).map(([slotKey, url]) => {
	                                  const line = layerTuningTextLineSpecLines[slotKey];
	                                  const box = layerTuningTextSlotBoxes?.[slotKey] ?? null;
	                                  const rot = clampNumber(Number(line?.rotate_deg ?? 0), -180, 180);
	                                  const dx = width ? Number(line?.offset_x ?? 0) * width : 0;
	                                  const dy = height ? Number(line?.offset_y ?? 0) * height : 0;
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
	                                        style={{
	                                          width: "100%",
	                                          height: "100%",
	                                          objectFit: "cover",
	                                          pointerEvents: "none",
	                                        }}
	                                        onError={() => {
	                                          setLayerTuningTextSlotImages((current) => {
	                                            const next = { ...current };
	                                            delete next[slotKey];
	                                            return next;
	                                          });
	                                          setLayerTuningTextSlotStatus((current) => ({
	                                            loading: false,
	                                            error: current.error ?? "文字レイヤの読み込みに失敗しました",
	                                          }));
	                                        }}
	                                      />
	                                      </div>
	                                    </div>
	                                  );
	                                })
	                              ) : layerTuningTextSlotStatus.loading ? (
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
	                              ) : layerTuningTextSlotStatus.error ? (
	                                <div
	                                  style={{
	                                    position: "absolute",
	                                    left: 12,
	                                    bottom: 12,
                                    padding: "6px 10px",
                                    borderRadius: 10,
                                    background: "rgba(127, 29, 29, 0.7)",
                                    color: "rgba(255,255,255,0.95)",
                                    fontSize: 12,
                                    letterSpacing: 0.2,
	                                  }}
	                                >
	                                  文字レイヤ: {layerTuningTextSlotStatus.error}
	                                </div>
	                              ) : null}
	                            </div>

                            {(() => {
                              if (!width || !height) {
                                return null;
                              }
                              const ctx = layerTuningDialog.context;
                              const options = (ctx?.template_options ?? []) as Array<{
                                id: string;
                                slots?: Record<string, { box?: number[] | null }>;
                              }>;
                              if (!options.length) {
                                return null;
                              }
                              const forced = String(layerTuningDialog.overridesLeaf?.["overrides.text_template_id"] ?? "").trim();
                              const fallback = String(ctx?.template_id_default ?? "").trim();
                              const templateId = forced || fallback || String(options[0]?.id ?? "");
                              const tpl =
                                options.find((opt) => String(opt.id || "").trim() === templateId) ?? options[0];
                              const slots = (tpl?.slots ?? {}) as Record<string, { box?: number[] | null }>;
                              const entries = Object.entries(slots);
                              if (!entries.length) {
                                return null;
                              }
                              return (
                                <div style={{ position: "absolute", inset: 0, zIndex: 40, pointerEvents: "none" }}>
                                  {entries.map(([slotKey, meta]) => {
                                    const box = meta?.box ?? null;
                                    if (!Array.isArray(box) || box.length !== 4) {
                                      return null;
                                    }
                                    const line = layerTuningTextLineSpecLines?.[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1 };
                                    const left = Number(box[0]) * width + Number(line.offset_x ?? 0) * width;
                                    const top = Number(box[1]) * height + Number(line.offset_y ?? 0) * height;
                                    const wPx = Number(box[2]) * width;
                                    const hPx = Number(box[3]) * height;
                                    if (
                                      !Number.isFinite(left) ||
                                      !Number.isFinite(top) ||
                                      !Number.isFinite(wPx) ||
                                      !Number.isFinite(hPx)
                                    ) {
                                      return null;
                                    }
	                                    const selected =
	                                      layerTuningSelectedAsset === "text" && layerTuningSelectedTextSlot === slotKey;
	                                    const hovered = layerTuningHoveredTextSlot === slotKey;
	                                    const showGuides = layerTuningGuidesEnabled || hovered || selected;
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
	                                          borderRadius: 8,
	                                          boxSizing: "border-box",
	                                          border: selected
	                                            ? "2px solid rgba(59, 130, 246, 0.95)"
	                                            : showGuides
	                                              ? `1px dashed ${hovered ? "rgba(255,255,255,0.32)" : "rgba(255,255,255,0.18)"}`
	                                              : "none",
	                                          background: selected
	                                            ? "rgba(59, 130, 246, 0.06)"
	                                            : hovered
	                                              ? "rgba(255,255,255,0.05)"
	                                              : "transparent",
	                                          pointerEvents: "auto",
	                                        }}
	                                        onPointerEnter={() => setLayerTuningHoveredTextSlot(slotKey)}
	                                        onPointerLeave={() =>
	                                          setLayerTuningHoveredTextSlot((current) => (current === slotKey ? null : current))
	                                        }
	                                        onPointerDown={(event) => beginLayerTuningPreviewTextDrag(event, slotKey)}
	                                        onPointerMove={handleLayerTuningPreviewDragMove}
	                                        onPointerUp={handleLayerTuningPreviewDragEnd}
	                                        onPointerCancel={handleLayerTuningPreviewDragEnd}
	                                      >
                                        {selected ? (
                                          <>
                                            <div
                                              style={{
                                                position: "absolute",
                                                left: 8,
                                                bottom: 8,
                                                padding: "2px 8px",
                                                borderRadius: 999,
                                                background: "rgba(0,0,0,0.35)",
                                                color: "rgba(255,255,255,0.85)",
                                                fontSize: 11,
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
                                                top: -20,
                                                width: 2,
                                                height: 18,
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
                                                top: -22,
                                                width: 14,
                                                height: 14,
                                                transform: "translate(-50%, 0)",
                                                borderRadius: 999,
                                                background: "rgba(255,255,255,0.92)",
                                                border: "1px solid rgba(15, 23, 42, 0.45)",
                                                boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
                                                cursor: "grab",
                                                touchAction: "none",
                                                pointerEvents: "auto",
                                              }}
                                              onPointerDown={(event) => beginLayerTuningPreviewTextSlotRotate(event, slotKey)}
                                              onPointerMove={handleLayerTuningPreviewDragMove}
                                              onPointerUp={handleLayerTuningPreviewDragEnd}
                                              onPointerCancel={handleLayerTuningPreviewDragEnd}
                                            />
                                            <div
                                              title="サイズ（ドラッグ）"
                                              style={{
                                                position: "absolute",
                                                left: "100%",
                                                top: "100%",
                                                width: 12,
                                                height: 12,
                                                transform: "translate(-50%, -50%)",
                                                borderRadius: 3,
                                                background: "rgba(255,255,255,0.92)",
                                                border: "1px solid rgba(15, 23, 42, 0.45)",
                                                boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
                                                cursor: "nwse-resize",
                                                touchAction: "none",
                                                pointerEvents: "auto",
                                              }}
                                              onPointerDown={(event) => beginLayerTuningPreviewTextSlotScale(event, slotKey)}
                                              onPointerMove={handleLayerTuningPreviewDragMove}
                                              onPointerUp={handleLayerTuningPreviewDragEnd}
                                              onPointerCancel={handleLayerTuningPreviewDragEnd}
                                            />
                                          </>
                                        ) : null}
                                      </div>
                                    );
                                  })}
                                </div>
                              );
                            })()}

                            {portraitEnabled ? (
                              <>
                                {layerTuningGuidesEnabled || layerTuningSelectedAsset === "portrait" ? (
                                  <div
                                    style={{
                                      position: "absolute",
                                      left: boxLeft,
                                      top: boxTop,
                                      width: boxW,
                                      height: boxH,
                                      border: "1px dashed rgba(255,255,255,0.35)",
                                      pointerEvents: "none",
                                      borderRadius: 6,
                                      zIndex: 15,
                                    }}
                                  />
                                ) : null}
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
                                    borderRadius: 6,
                                    outline:
                                      layerTuningSelectedAsset === "portrait"
                                        ? "2px solid rgba(59, 130, 246, 0.95)"
                                        : "none",
                                    outlineOffset: 2,
                                    zIndex: 15,
                                  }}
                                  onPointerDown={beginLayerTuningPreviewPortraitDrag}
                                  onPointerMove={handleLayerTuningPreviewDragMove}
                                  onPointerUp={handleLayerTuningPreviewDragEnd}
                                  onPointerCancel={handleLayerTuningPreviewDragEnd}
                                  onWheel={handleLayerTuningPreviewPortraitWheel}
                                >
                                  {layerTuningPortraitPreviewSrc ? (
                                    <img
                                      src={layerTuningPortraitPreviewSrc}
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
                                        if (!layerTuningChannel || !layerTuningVideo) {
                                          setLayerTuningPortraitPreviewSrc(null);
                                          return;
                                        }
                                        const candidates = [
                                          resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/20_portrait.png`),
                                          resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/20_portrait.jpg`),
                                          resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/20_portrait.jpeg`),
                                          resolveApiUrl(`/thumbnails/assets/${layerTuningChannel}/${layerTuningVideo}/20_portrait.webp`),
                                        ];
                                        setLayerTuningPortraitPreviewSrc((current) => {
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
                                  {layerTuningSelectedAsset === "portrait" ? (
                                    <div
                                      title="ズーム（ドラッグ）"
                                      style={{
                                        position: "absolute",
                                        left: "100%",
                                        top: "100%",
                                        width: 12,
                                        height: 12,
                                        transform: "translate(-50%, -50%)",
                                        borderRadius: 3,
                                        background: "rgba(255,255,255,0.92)",
                                        border: "1px solid rgba(15, 23, 42, 0.45)",
                                        boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
                                        cursor: "nwse-resize",
                                        touchAction: "none",
                                        pointerEvents: "auto",
                                      }}
                                      onPointerDown={beginLayerTuningPreviewPortraitScale}
                                      onPointerMove={handleLayerTuningPreviewDragMove}
                                      onPointerUp={handleLayerTuningPreviewDragEnd}
                                      onPointerCancel={handleLayerTuningPreviewDragEnd}
                                    />
                                  ) : null}
                                </div>
                              </>
                            ) : null}

                            {(() => {
                              if (!width || !height) {
                                return null;
                              }
                              if (layerTuningSelectedAsset !== "element") {
                                return null;
                              }
                              const elementId = String(layerTuningSelectedElementId ?? "").trim();
                              if (!elementId) {
                                return null;
                              }
                              const el = (layerTuningElements ?? []).find((item) => String(item?.id ?? "") === elementId);
                              if (!el) {
                                return null;
                              }
                              const kind = String((el as any)?.kind ?? "").trim();
                              const x = clampNumber(
                                Number((el as any)?.x ?? 0.5),
                                LAYER_TUNING_ELEMENT_XY_MIN,
                                LAYER_TUNING_ELEMENT_XY_MAX
                              );
                              const y = clampNumber(
                                Number((el as any)?.y ?? 0.5),
                                LAYER_TUNING_ELEMENT_XY_MIN,
                                LAYER_TUNING_ELEMENT_XY_MAX
                              );
                              const wNorm = clampNumber(Number((el as any)?.w ?? 0.2), 0.01, 4);
                              const hNorm = clampNumber(Number((el as any)?.h ?? 0.2), 0.01, 4);
                              const rotation = clampNumber(Number((el as any)?.rotation_deg ?? 0), -180, 180);
                              const centerX = x * width;
                              const centerY = y * height;
                              const wPx = wNorm * width;
                              const hPx = hNorm * height;
                              const borderRadius = kind === "circle" ? "50%" : 8;

                              const handles: Array<{ id: LayerTuningResizeHandle; left: string; top: string; cursor: string }> = [
                                { id: "nw", left: "0%", top: "0%", cursor: "nwse-resize" },
                                { id: "n", left: "50%", top: "0%", cursor: "ns-resize" },
                                { id: "ne", left: "100%", top: "0%", cursor: "nesw-resize" },
                                { id: "e", left: "100%", top: "50%", cursor: "ew-resize" },
                                { id: "se", left: "100%", top: "100%", cursor: "nwse-resize" },
                                { id: "s", left: "50%", top: "100%", cursor: "ns-resize" },
                                { id: "sw", left: "0%", top: "100%", cursor: "nesw-resize" },
                                { id: "w", left: "0%", top: "50%", cursor: "ew-resize" },
                              ];

                              return (
                                <div
                                  style={{
                                    position: "absolute",
                                    left: centerX,
                                    top: centerY,
                                    width: wPx,
                                    height: hPx,
                                    transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
                                    transformOrigin: "50% 50%",
                                    pointerEvents: "none",
                                    zIndex: 55,
                                  }}
                                >
                                  <div
                                    style={{
                                      position: "absolute",
                                      inset: 0,
                                      border: "2px solid rgba(59, 130, 246, 0.95)",
                                      borderRadius,
                                      boxSizing: "border-box",
                                    }}
                                  />
                                  <div
                                    style={{
                                      position: "absolute",
                                      left: "50%",
                                      top: -20,
                                      width: 2,
                                      height: 18,
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
                                      top: -22,
                                      width: 14,
                                      height: 14,
                                      transform: "translate(-50%, 0)",
                                      borderRadius: 999,
                                      background: "rgba(255,255,255,0.92)",
                                      border: "1px solid rgba(15, 23, 42, 0.45)",
                                      boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
                                      cursor: "grab",
                                      touchAction: "none",
                                      pointerEvents: "auto",
                                    }}
                                    onPointerDown={(event) => beginLayerTuningPreviewElementRotate(event, elementId)}
                                    onPointerMove={handleLayerTuningPreviewDragMove}
                                    onPointerUp={handleLayerTuningPreviewDragEnd}
                                    onPointerCancel={handleLayerTuningPreviewDragEnd}
                                  />
                                  {handles.map((h) => (
                                    <div
                                      key={h.id}
                                      title="リサイズ（Shiftで比率固定 / Altで中心基準）"
                                      style={{
                                        position: "absolute",
                                        left: h.left,
                                        top: h.top,
                                        width: 12,
                                        height: 12,
                                        transform: "translate(-50%, -50%)",
                                        borderRadius: 3,
                                        background: "rgba(255,255,255,0.92)",
                                        border: "1px solid rgba(15, 23, 42, 0.45)",
                                        boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
                                        cursor: h.cursor,
                                        touchAction: "none",
                                        pointerEvents: "auto",
                                      }}
                                      onPointerDown={(event) => beginLayerTuningPreviewElementResize(event, elementId, h.id)}
                                      onPointerMove={handleLayerTuningPreviewDragMove}
                                      onPointerUp={handleLayerTuningPreviewDragEnd}
                                      onPointerCancel={handleLayerTuningPreviewDragEnd}
                                    />
                                  ))}
                                </div>
                              );
                            })()}

                            {layerTuningPreviewDropActive ? (
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  zIndex: 80,
                                  pointerEvents: "none",
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  background: "rgba(59, 130, 246, 0.12)",
                                  boxShadow: "inset 0 0 0 2px rgba(59, 130, 246, 0.75)",
                                }}
                              >
                                <div
                                  style={{
                                    padding: "10px 14px",
                                    borderRadius: 12,
                                    background: "rgba(0, 0, 0, 0.55)",
                                    color: "rgba(255,255,255,0.95)",
                                    fontSize: 13,
                                    letterSpacing: 0.2,
                                  }}
                                >
                                  {layerTuningSelectedAsset === "bg"
                                    ? "背景を差し替え（ドロップ）"
                                    : layerTuningSelectedAsset === "portrait"
                                      ? "肖像を差し替え（ドロップ）"
                                      : "ドロップで差し替え（背景/肖像を選択、Shiftで出力）"}
                                </div>
                              </div>
                            ) : null}

                            <div
                              style={{
                                position: "absolute",
                                left: 10,
                                top: 10,
                                padding: "4px 10px",
                                borderRadius: 999,
                                background: "rgba(0, 0, 0, 0.55)",
                                color: "rgba(255,255,255,0.9)",
                                fontSize: 12,
                                letterSpacing: 0.2,
                                pointerEvents: "none",
                              }}
                            >
                              選択:{" "}
                              {layerTuningSelectedAsset === "portrait"
                                ? "肖像"
                                : layerTuningSelectedAsset === "text"
                                  ? "文字"
                                  : layerTuningSelectedAsset === "element"
                                    ? "要素"
                                    : "背景"}
                              （クリックで切替 / ↑↓←→で微調整）
                            </div>

                            {layerTuningSelectedAsset === "bg" ? (
                              <div
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  boxShadow: "inset 0 0 0 2px rgba(59, 130, 246, 0.65)",
                                  pointerEvents: "none",
                                }}
                              />
                            ) : null}
                          </>
                        );
                      })()}
                    </div>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() =>
                          mergeLayerTuningOverridesLeaf({
                            "overrides.bg_pan_zoom.pan_x": null,
                            "overrides.bg_pan_zoom.pan_y": null,
                            "overrides.bg_pan_zoom.zoom": null,
                          })
                        }
                      >
                        背景: リセット
                      </button>
	                      <button
	                        type="button"
	                        className="btn btn--ghost"
	                        onClick={() => {
	                          mergeLayerTuningOverridesLeaf({
	                            "overrides.text_offset_x": null,
	                            "overrides.text_offset_y": null,
	                          });
	                          const slotKey = layerTuningSelectedTextSlotRef.current;
		                          if (slotKey) {
		                            setLayerTuningTextLineSpecLinesImmediate((current) => {
		                              const next = { ...(current ?? {}) };
		                              const existing = next[slotKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
		                              next[slotKey] = { ...existing, offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
		                              return next;
		                            });
		                          }
	                        }}
	                      >
	                        文字: リセット
	                      </button>
	                      {Object.keys(layerTuningTextSlotImages).length > 0 ? (
	                        <label className="muted small-text" style={{ display: "flex", alignItems: "center", gap: 6 }}>
	                          <span>行</span>
	                          <select
	                            value={layerTuningSelectedTextSlot ?? ""}
	                            onChange={(event) => {
	                              const next = event.target.value;
	                              setLayerTuningSelectedTextSlot(next || null);
	                              setLayerTuningSelectedAsset("text");
	                            }}
	                          >
	                            {Object.keys(layerTuningTextSlotImages)
	                              .sort((a, b) => a.localeCompare(b))
	                              .map((slotKey) => (
	                                <option key={slotKey} value={slotKey}>
	                                  {slotKey}
	                                </option>
	                              ))}
	                          </select>
	                        </label>
	                      ) : null}
	                      {layerTuningDialog.context?.portrait_available ? (
	                        <button
	                          type="button"
                          className="btn btn--ghost"
                          onClick={() =>
                            mergeLayerTuningOverridesLeaf({
                              "overrides.portrait.offset_x": null,
                              "overrides.portrait.offset_y": null,
                              "overrides.portrait.zoom": null,
                            })
                          }
                        >
                          肖像: リセット
                        </button>
                      ) : null}
	                    </div>
		                    <p className="thumbnail-library__placeholder" style={{ marginTop: -4 }}>
		                      クリックで選択 → ドラッグで移動。枠外（ペーストボード）にも置けます。ハンドル: 要素=リサイズ/回転、文字=サイズ/回転、肖像=ズーム。Shift=スナップ/比率固定、Alt=スナップ一時OFF、ガイド=枠/グリッド表示、↑↓←→=微調整。画像はドロップで差し替え（背景/肖像選択、Shiftで出力）。
		                    </p>
	                  </div>

                  <div className="thumbnail-planning-form__grid">
                    <label>
                      <span>生成を許可</span>
                      <input
                        type="checkbox"
                        checked={layerTuningDialog.allowGenerate}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setLayerTuningDialog((current) =>
                            current
                              ? { ...current, allowGenerate: checked, regenBg: checked ? current.regenBg : false }
                              : current
                          );
                        }}
                      />
                    </label>
                    <label>
                      <span>背景を作り直す（regen-bg）</span>
                      <input
                        type="checkbox"
                        checked={layerTuningDialog.regenBg}
                        disabled={!layerTuningDialog.allowGenerate}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setLayerTuningDialog((current) => (current ? { ...current, regenBg: checked } : current));
                        }}
                      />
                    </label>
                    <label>
                      <span>出力モード</span>
                      <select
                        value={layerTuningDialog.outputMode}
                        onChange={(event) => {
                          const value = event.target.value as ThumbnailLayerSpecsBuildOutputMode;
                          setLayerTuningDialog((current) => (current ? { ...current, outputMode: value } : current));
                        }}
                      >
                        <option value="draft">draft</option>
                        <option value="final">final</option>
                      </select>
                    </label>
                  </div>

                  <h3 style={{ marginTop: 18, marginBottom: 8 }}>要素（図形・画像）</h3>
                  <div className="thumbnail-planning-form__grid">
                    <div className="thumbnail-planning-form__field--wide" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                      <button type="button" className="btn btn--ghost" onClick={() => addLayerTuningElement("rect")}>
                        ＋四角
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => addLayerTuningElement("circle")}>
                        ＋丸
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() => layerTuningElementUploadInputRef.current?.click()}
                        disabled={layerTuningElementsStatus.loading}
                      >
                        ＋画像
                      </button>
                      <input
                        ref={layerTuningElementUploadInputRef}
                        type="file"
                        accept="image/*"
                        onChange={handleLayerTuningElementUploadChange}
                        style={{ display: "none" }}
                      />
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={duplicateLayerTuningSelectedElement}
                        disabled={!layerTuningSelectedElementId}
                      >
                        複製
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={deleteLayerTuningSelectedElement}
                        disabled={!layerTuningSelectedElementId}
                      >
                        削除
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() => moveLayerTuningSelectedElementZ("front")}
                        disabled={!layerTuningSelectedElementId}
                      >
                        前へ
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() => moveLayerTuningSelectedElementZ("back")}
                        disabled={!layerTuningSelectedElementId}
                      >
                        後ろへ
                      </button>
	                      <label className="muted small-text" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 8 }}>
	                        <input
	                          type="checkbox"
	                          checked={layerTuningSnapEnabled}
	                          onChange={(event) => setLayerTuningSnapEnabled(event.target.checked)}
	                        />
	                        <span>スナップ</span>
	                      </label>
	                      <label className="muted small-text" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
	                        <input
	                          type="checkbox"
	                          checked={layerTuningGuidesEnabled}
	                          onChange={(event) => setLayerTuningGuidesEnabled(event.target.checked)}
	                        />
	                        <span>ガイド</span>
	                      </label>
	                    </div>
                    <div className="thumbnail-planning-form__field--wide" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                      <span className="muted small-text">整列（選択中の素材）:</span>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("left")}>
                        左
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("center")}>
                        中央
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("right")}>
                        右
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("top")}>
                        上
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("middle")}>
                        中
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => alignLayerTuningSelected("bottom")}>
                        下
                      </button>
                    </div>
                    <label className="thumbnail-planning-form__field--wide">
                      <span>選択中の要素</span>
                      <select
                        value={layerTuningSelectedElementId ?? ""}
                        onChange={(event) => {
                          const next = event.target.value;
                          setLayerTuningSelectedElementId(next || null);
                          if (next) {
                            setLayerTuningSelectedAsset("element");
                          }
                        }}
                      >
                        <option value="">（なし）</option>
                        {(layerTuningElements ?? [])
                          .slice()
                          .sort((a, b) => String(a?.id ?? "").localeCompare(String(b?.id ?? "")))
                          .map((el) => {
                            const id = String(el?.id ?? "").trim();
                            if (!id) return null;
                            const kind = String((el as any)?.kind ?? "");
                            return (
                              <option key={id} value={id}>
                                {kind}:{id}
                              </option>
                            );
                          })}
                      </select>
                    </label>
                    {(() => {
                      const selectedId = String(layerTuningSelectedElementId ?? "").trim();
                      const el = selectedId ? (layerTuningElements ?? []).find((item) => String(item?.id ?? "") === selectedId) : null;
                      if (!el) {
                        return (
                          <p className="muted small-text thumbnail-planning-form__field--wide">
                            「＋四角 / ＋丸 / ＋画像」で追加 → クリックして選択 → ドラッグで移動。数値でも微調整できます。
                          </p>
                        );
                      }
                      const kind = String((el as any)?.kind ?? "rect");
                      const layer = String((el as any)?.layer ?? "above_portrait");
                      const x = Number((el as any)?.x ?? 0.5);
                      const y = Number((el as any)?.y ?? 0.5);
                      const w = Number((el as any)?.w ?? 0.2);
                      const h = Number((el as any)?.h ?? 0.2);
                      const rotation = Number((el as any)?.rotation_deg ?? 0);
                      const opacity = Number((el as any)?.opacity ?? 1);
                      const fill = String((el as any)?.fill ?? "").trim() || "#ffffff";
                      const stroke = (el as any)?.stroke ?? null;
                      const strokeWidth = Number(stroke?.width_px ?? 0);
                      const strokeColor = String(stroke?.color ?? "#000000") || "#000000";
                      const clampMaybe = (value: number, lo: number, hi: number) => clampNumber(Number(value), lo, hi);
                      return (
                        <>
                          <label>
                            <span>レイヤ</span>
                            <select
                              value={layer}
                              onChange={(event) =>
                                updateLayerTuningSelectedElement({ layer: event.target.value as any })
                              }
                            >
                              <option value="above_portrait">画像より上（肖像の上）</option>
                              <option value="below_portrait">画像より下（背景）</option>
                            </select>
                          </label>
                          <label>
                            <span>z</span>
                            <input
                              type="number"
                              value={Number.isFinite(Number((el as any)?.z)) ? Number((el as any)?.z) : 0}
                              onChange={(event) =>
                                updateLayerTuningSelectedElement({ z: Math.round(Number(event.target.value) || 0) })
                              }
                            />
                          </label>
                          <label>
                            <span>X（中心）</span>
                            <input
                              type="range"
                              min={LAYER_TUNING_ELEMENT_XY_MIN}
                              max={LAYER_TUNING_ELEMENT_XY_MAX}
                              step={0.001}
                              value={clampMaybe(x, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX)}
                              onChange={(event) => updateLayerTuningSelectedElement({ x: Number(event.target.value) })}
                            />
                          </label>
                          <label>
                            <span>Y（中心）</span>
                            <input
                              type="range"
                              min={LAYER_TUNING_ELEMENT_XY_MIN}
                              max={LAYER_TUNING_ELEMENT_XY_MAX}
                              step={0.001}
                              value={clampMaybe(y, LAYER_TUNING_ELEMENT_XY_MIN, LAYER_TUNING_ELEMENT_XY_MAX)}
                              onChange={(event) => updateLayerTuningSelectedElement({ y: Number(event.target.value) })}
                            />
                          </label>
                          <label>
                            <span>幅</span>
                            <input
                              type="range"
                              min={0.01}
                              max={4}
                              step={0.001}
                              value={clampMaybe(w, 0.01, 4)}
                              onChange={(event) => updateLayerTuningSelectedElement({ w: Number(event.target.value) })}
                            />
                          </label>
                          <label>
                            <span>高さ</span>
                            <input
                              type="range"
                              min={0.01}
                              max={4}
                              step={0.001}
                              value={clampMaybe(h, 0.01, 4)}
                              onChange={(event) => updateLayerTuningSelectedElement({ h: Number(event.target.value) })}
                            />
                          </label>
                          <label>
                            <span>回転</span>
                            <input
                              type="range"
                              min={-180}
                              max={180}
                              step={0.1}
                              value={clampMaybe(rotation, -180, 180)}
                              onChange={(event) =>
                                updateLayerTuningSelectedElement({ rotation_deg: Number(event.target.value) })
                              }
                            />
                          </label>
                          <label>
                            <span>不透明度</span>
                            <input
                              type="range"
                              min={0}
                              max={1}
                              step={0.01}
                              value={clampMaybe(opacity, 0, 1)}
                              onChange={(event) =>
                                updateLayerTuningSelectedElement({ opacity: Number(event.target.value) })
                              }
                            />
                          </label>
                          {kind !== "image" ? (
                            <label>
                              <span>塗り</span>
                              <input
                                type="color"
                                value={/^#[0-9a-fA-F]{6}$/.test(fill) ? fill : "#ffffff"}
                                onChange={(event) => updateLayerTuningSelectedElement({ fill: event.target.value })}
                              />
                            </label>
                          ) : null}
                          <label>
                            <span>枠線（幅）</span>
                            <input
                              type="range"
                              min={0}
                              max={64}
                              step={1}
                              value={clampMaybe(strokeWidth, 0, 64)}
                              onChange={(event) => {
                                const widthPx = Math.max(0, Number(event.target.value) || 0);
                                if (widthPx <= 0) {
                                  updateLayerTuningSelectedElement({ stroke: null as any });
                                  return;
                                }
                                updateLayerTuningSelectedElement({
                                  stroke: { color: strokeColor, width_px: widthPx } as any,
                                });
                              }}
                            />
                          </label>
                          <label>
                            <span>枠線（色）</span>
                            <input
                              type="color"
                              value={/^#[0-9a-fA-F]{6}$/.test(strokeColor) ? strokeColor : "#000000"}
                              onChange={(event) => {
                                const color = event.target.value;
                                if (!stroke || strokeWidth <= 0) {
                                  updateLayerTuningSelectedElement({
                                    stroke: { color, width_px: Math.max(1, strokeWidth || 1) } as any,
                                  });
                                  return;
                                }
                                updateLayerTuningSelectedElement({ stroke: { ...stroke, color } as any });
                              }}
                            />
                          </label>
                          {kind === "image" ? (
                            <p className="muted small-text thumbnail-planning-form__field--wide">
                              画像要素: 「＋画像」で差し替え（新規）できます。既存の差し替え UI は次フェーズで追加。
                            </p>
                          ) : null}
                        </>
                      );
                    })()}
                    {layerTuningElementsStatus.error ? (
                      <div className="thumbnail-planning-form__error thumbnail-planning-form__field--wide" role="alert">
                        {layerTuningElementsStatus.error}
                      </div>
                    ) : null}
                  </div>

                  <h3 style={{ marginTop: 18, marginBottom: 8 }}>文字</h3>
                  <div className="thumbnail-planning-form__grid">
                    <label className="thumbnail-planning-form__field--wide">
                      <span>
                        テンプレ（既定: {layerTuningDialog.context?.template_id_default ?? "—"}）
                      </span>
                      <select
                        value={
                          isLayerTuningLeafOverridden(layerTuningDialog, "overrides.text_template_id")
                            ? String(layerTuningDialog.overridesLeaf["overrides.text_template_id"] ?? "")
                            : ""
                        }
                        onChange={(event) => {
                          const next = event.target.value;
                          setLayerTuningOverrideLeaf("overrides.text_template_id", next ? next : null);
                        }}
                      >
                        <option value="">（既定を使う）</option>
                        {(layerTuningDialog.context?.template_options ?? []).map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.id}
                            {opt.description ? ` — ${opt.description}` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    {(() => {
                      const keys = Object.keys(layerTuningTextLineSpecLines ?? {})
                        .filter(Boolean)
                        .sort((a, b) => a.localeCompare(b));
                      const resolvedKey =
                        (layerTuningSelectedTextSlot && keys.includes(layerTuningSelectedTextSlot)
                          ? layerTuningSelectedTextSlot
                          : keys[0]) ?? "";
                      const line = resolvedKey ? layerTuningTextLineSpecLines?.[resolvedKey] : null;
                      const disabled = !resolvedKey;
                      const updateLine = (
                        patch: Partial<{ offset_x: number; offset_y: number; scale: number; rotate_deg: number }>
                      ) => {
                        if (!resolvedKey) {
                          return;
                        }
                        setLayerTuningTextLineSpecLinesImmediate((current) => {
                          const next = { ...(current ?? {}) };
                          const existing = next[resolvedKey] ?? { offset_x: 0, offset_y: 0, scale: 1, rotate_deg: 0 };
                          next[resolvedKey] = { ...existing, ...patch };
                          return next;
                        });
                      };
                      return (
                        <>
                          <label className="thumbnail-planning-form__field--wide">
                            <span>行（slot）</span>
                            <select
                              value={resolvedKey}
                              disabled={keys.length === 0}
                              onChange={(event) => {
                                const next = event.target.value;
                                setLayerTuningSelectedTextSlot(next || null);
                                setLayerTuningSelectedAsset("text");
                              }}
                            >
                              {keys.map((slotKey) => (
                                <option key={slotKey} value={slotKey}>
                                  {slotKey}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="thumbnail-planning-form__field--wide">
                            <span>文字サイズ（行scale）</span>
                            <input
                              type="range"
                              min={0.25}
                              max={4}
                              step={0.01}
                              disabled={disabled}
                              value={Number(line?.scale ?? 1)}
                              onChange={(event) => updateLine({ scale: Number(event.target.value) })}
                            />
                          </label>
                          <label className="thumbnail-planning-form__field--wide">
                            <span>文字位置X（行offset_x）</span>
                            <input
                              type="range"
                              min={-2}
                              max={2}
                              step={0.001}
                              disabled={disabled}
                              value={Number(line?.offset_x ?? 0)}
                              onChange={(event) => updateLine({ offset_x: Number(event.target.value) })}
                            />
                          </label>
                          <label className="thumbnail-planning-form__field--wide">
                            <span>回転（行rotate_deg）</span>
                            <input
                              type="range"
                              min={-180}
                              max={180}
                              step={0.1}
                              disabled={disabled}
                              value={Number(line?.rotate_deg ?? 0)}
                              onChange={(event) => updateLine({ rotate_deg: Number(event.target.value) })}
                            />
                          </label>
                          <label className="thumbnail-planning-form__field--wide">
                            <span>文字位置Y（行offset_y）</span>
                            <input
                              type="range"
                              min={-2}
                              max={2}
                              step={0.001}
                              disabled={disabled}
                              value={Number(line?.offset_y ?? 0)}
                              onChange={(event) => updateLine({ offset_y: Number(event.target.value) })}
                            />
                          </label>
                        </>
                      );
                    })()}
                    <label>
                      <span>上段色（red_fill）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_fills.red_fill.color", "#ff0000")
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#ff0000";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_fills.red_fill.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>中段色（yellow_fill）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_fills.yellow_fill.color",
                              "#ffff00"
                            )
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#ffff00";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_fills.yellow_fill.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>下段色（white_fill）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_fills.white_fill.color", "#ffffff")
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#ffffff";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_fills.white_fill.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>強調赤（hot_red_fill）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_fills.hot_red_fill.color",
                              "#ff0000"
                            )
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#ff0000";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_fills.hot_red_fill.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>紫（purple_fill）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_fills.purple_fill.color",
                              "#a020f0"
                            )
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#a020f0";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_fills.purple_fill.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>影（alpha）</span>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.shadow.alpha", 0.65))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.shadow.alpha", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>フチ（stroke px）</span>
                      <input
                        type="range"
                        min={0}
                        max={64}
                        step={1}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.stroke.width_px", 8))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.stroke.width_px", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>フチ色（stroke color）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.stroke.color", "#000000")
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.stroke.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>影（blur px）</span>
                      <input
                        type="range"
                        min={0}
                        max={128}
                        step={1}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.shadow.blur_px", 10))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.shadow.blur_px", Number(event.target.value))
                        }
                      />
                    </label>
                    <label className="thumbnail-planning-form__field--wide">
                      <span>影（offset px）</span>
                      <div style={{ display: "flex", gap: 8 }}>
                        <input
                          type="number"
                          step={1}
                          value={(() => {
                            const fallback = [6, 6];
                            const v = resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_effects.shadow.offset_px",
                              fallback
                            );
                            const pair = Array.isArray(v) ? v : fallback;
                            return Number(pair[0] ?? fallback[0]);
                          })()}
                          onChange={(event) => {
                            const fallback = [6, 6];
                            const v = resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_effects.shadow.offset_px",
                              fallback
                            );
                            const pair = Array.isArray(v) ? v : fallback;
                            const y = Number(pair[1] ?? fallback[1]);
                            setLayerTuningOverrideLeaf("overrides.text_effects.shadow.offset_px", [Number(event.target.value), y]);
                          }}
                        />
                        <input
                          type="number"
                          step={1}
                          value={(() => {
                            const fallback = [6, 6];
                            const v = resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_effects.shadow.offset_px",
                              fallback
                            );
                            const pair = Array.isArray(v) ? v : fallback;
                            return Number(pair[1] ?? fallback[1]);
                          })()}
                          onChange={(event) => {
                            const fallback = [6, 6];
                            const v = resolveLayerTuningLeafValue(
                              layerTuningDialog,
                              "overrides.text_effects.shadow.offset_px",
                              fallback
                            );
                            const pair = Array.isArray(v) ? v : fallback;
                            const x = Number(pair[0] ?? fallback[0]);
                            setLayerTuningOverrideLeaf("overrides.text_effects.shadow.offset_px", [x, Number(event.target.value)]);
                          }}
                        />
                      </div>
                    </label>
                    <label>
                      <span>影色（shadow color）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.shadow.color", "#000000")
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.shadow.color", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      <span>グロー（alpha）</span>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.glow.alpha", 0.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.glow.alpha", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>グロー（blur px）</span>
                      <input
                        type="range"
                        min={0}
                        max={128}
                        step={1}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.glow.blur_px", 0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.glow.blur_px", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>グロー色（glow color）</span>
                      <input
                        type="color"
                        value={(() => {
                          const v = String(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.text_effects.glow.color", "#ffffff")
                          );
                          return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#ffffff";
                        })()}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.text_effects.glow.color", event.target.value)
                        }
                      />
                    </label>
                  </div>

                  <h3 style={{ marginTop: 18, marginBottom: 8 }}>背景</h3>
                  <div className="thumbnail-planning-form__grid">
                    <label>
                      <span>明るさ</span>
                      <input
                        type="range"
                        min={0.5}
                        max={2}
                        step={0.01}
                        value={Number(
                          resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.brightness", 1.0)
                        )}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_enhance.brightness", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>コントラスト</span>
                      <input
                        type="range"
                        min={0.5}
                        max={2}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.contrast", 1.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_enhance.contrast", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>彩度</span>
                      <input
                        type="range"
                        min={0.5}
                        max={2}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.color", 1.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_enhance.color", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>ガンマ</span>
                      <input
                        type="range"
                        min={0.5}
                        max={2}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance.gamma", 1.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_enhance.gamma", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>ズーム</span>
                      <input
                        type="range"
                        min={1}
                        max={LAYER_TUNING_BG_MAX_ZOOM}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.zoom", 1.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_pan_zoom.zoom", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>位置X</span>
                      <input
                        type="range"
                        min={LAYER_TUNING_BG_PAN_MIN}
                        max={LAYER_TUNING_BG_PAN_MAX}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_x", 0.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_pan_zoom.pan_x", Number(event.target.value))
                        }
                      />
                    </label>
                    <label>
                      <span>位置Y</span>
                      <input
                        type="range"
                        min={LAYER_TUNING_BG_PAN_MIN}
                        max={LAYER_TUNING_BG_PAN_MAX}
                        step={0.01}
                        value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_pan_zoom.pan_y", 0.0))}
                        onChange={(event) =>
                          setLayerTuningOverrideLeaf("overrides.bg_pan_zoom.pan_y", Number(event.target.value))
                        }
                      />
                    </label>
                    </div>

                  <details style={{ marginTop: 12 }}>
                    <summary className="muted small-text" style={{ cursor: "pointer" }}>
                      背景（詳細: 部分補正/帯）
                    </summary>

                    <h4 style={{ marginTop: 12, marginBottom: 8 }}>部分補正（band）</h4>
                    <div className="thumbnail-planning-form__grid">
                      <label>
                        <span>x0</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.x0", 0.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.x0", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>x1</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.x1", 0.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.x1", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>強さ（power）</span>
                        <input
                          type="range"
                          min={0.1}
                          max={3}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.power", 1.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.power", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>明るさ</span>
                        <input
                          type="range"
                          min={0.5}
                          max={2}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.brightness", 1.0)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.brightness", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>コントラスト</span>
                        <input
                          type="range"
                          min={0.5}
                          max={2}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.contrast", 1.0)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.contrast", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>彩度</span>
                        <input
                          type="range"
                          min={0.5}
                          max={2}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.color", 1.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.color", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>ガンマ</span>
                        <input
                          type="range"
                          min={0.5}
                          max={2}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.bg_enhance_band.gamma", 1.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.bg_enhance_band.gamma", Number(event.target.value))
                          }
                        />
                      </label>
                    </div>

                    <h4 style={{ marginTop: 18, marginBottom: 8 }}>帯（overlays）</h4>

                    <h4 style={{ marginTop: 0, marginBottom: 8 }}>左TSZ（文字スペース）</h4>
                    <div className="thumbnail-planning-form__grid">
                      <label>
                        <span>有効</span>
                        <input
                          type="checkbox"
                          checked={Boolean(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.enabled", true)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.enabled", event.target.checked)
                          }
                        />
                      </label>
                      <label>
                        <span>色</span>
                        <input
                          type="color"
                          value={(() => {
                            const v = String(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.color", "#000000")
                            );
                            return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
                          })()}
                          onChange={(event) => setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.color", event.target.value)}
                        />
                      </label>
                      <label>
                        <span>濃さ（左）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_left", 0.65)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.alpha_left", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>濃さ（右）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.alpha_right", 0.0)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.alpha_right", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>x0</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x0", 0.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.x0", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>x1</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.left_tsz.x1", 0.52))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.left_tsz.x1", Number(event.target.value))
                          }
                        />
                      </label>
                    </div>

                    <h4 style={{ marginTop: 18, marginBottom: 8 }}>上帯</h4>
                    <div className="thumbnail-planning-form__grid">
                      <label>
                        <span>有効</span>
                        <input
                          type="checkbox"
                          checked={Boolean(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.enabled", true)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.top_band.enabled", event.target.checked)
                          }
                        />
                      </label>
                      <label>
                        <span>色</span>
                        <input
                          type="color"
                          value={(() => {
                            const v = String(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.color", "#000000")
                            );
                            return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
                          })()}
                          onChange={(event) => setLayerTuningOverrideLeaf("overrides.overlays.top_band.color", event.target.value)}
                        />
                      </label>
                      <label>
                        <span>濃さ（上）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_top", 0.7)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.top_band.alpha_top", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>濃さ（下）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.alpha_bottom", 0.0)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.top_band.alpha_bottom", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>y0</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y0", 0.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.top_band.y0", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>y1</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.top_band.y1", 0.25))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.top_band.y1", Number(event.target.value))
                          }
                        />
                      </label>
                    </div>

                    <h4 style={{ marginTop: 18, marginBottom: 8 }}>下帯</h4>
                    <div className="thumbnail-planning-form__grid">
                      <label>
                        <span>有効</span>
                        <input
                          type="checkbox"
                          checked={Boolean(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.enabled", true)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.bottom_band.enabled", event.target.checked)
                          }
                        />
                      </label>
                      <label>
                        <span>色</span>
                        <input
                          type="color"
                          value={(() => {
                            const v = String(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.color", "#000000")
                            );
                            return /^#[0-9a-fA-F]{6}$/.test(v) ? v : "#000000";
                          })()}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.bottom_band.color", event.target.value)
                          }
                        />
                      </label>
                      <label>
                        <span>濃さ（上）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_top", 0.0)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.bottom_band.alpha_top", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>濃さ（下）</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.alpha_bottom", 0.8)
                          )}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf(
                              "overrides.overlays.bottom_band.alpha_bottom",
                              Number(event.target.value)
                            )
                          }
                        />
                      </label>
                      <label>
                        <span>y0</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y0", 0.7))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.bottom_band.y0", Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        <span>y1</span>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.overlays.bottom_band.y1", 1.0))}
                          onChange={(event) =>
                            setLayerTuningOverrideLeaf("overrides.overlays.bottom_band.y1", Number(event.target.value))
                          }
                        />
                      </label>
                    </div>
                  </details>

	                    {layerTuningDialog.context?.portrait_available ? (
	                      <>
	                      <h3 style={{ marginTop: 18, marginBottom: 8 }}>肖像</h3>
                        {(() => {
                          const portraitDefaultEnabled = layerTuningStable !== "00_thumb_2";
                          const portraitEnabled = Boolean(
                            resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.enabled", portraitDefaultEnabled)
                          );
                          const suppressBgDefault = layerTuningDialog.channel === "CH26" && portraitEnabled;
                          const suppressBgForced = layerTuningDialog.channel === "CH26" && portraitEnabled;
                          const suppressBg =
                            suppressBgForced ||
                            Boolean(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.suppress_bg", suppressBgDefault));
                          return (
	                      <div className="thumbnail-planning-form__grid">
	                        <label>
	                          <span>有効</span>
	                          <input
	                            type="checkbox"
                              checked={portraitEnabled}
	                            onChange={(event) =>
	                              setLayerTuningOverrideLeaf("overrides.portrait.enabled", event.target.checked)
	                            }
	                          />
	                        </label>
	                        <label>
	                          <span>背景の顔を抑制</span>
	                          <input
	                            type="checkbox"
                              checked={suppressBg}
                              disabled={suppressBgForced}
                              onChange={(event) => {
                                if (suppressBgForced) {
                                  return;
                                }
                                setLayerTuningOverrideLeaf("overrides.portrait.suppress_bg", event.target.checked);
                              }}
	                          />
	                        </label>
                          {suppressBgForced ? (
                            <div className="muted small-text" style={{ gridColumn: "1 / -1" }}>
                              CH26 は背景に顔が含まれることがあるため、肖像が有効な間は「背景の顔を抑制」を固定でONにします。
                            </div>
                          ) : null}
	                        <label>
	                          <span>ズーム</span>
	                          <input
	                            type="range"
                            min={0.5}
                            max={2}
                            step={0.01}
                            value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.zoom", 1.0))}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.zoom", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>位置X</span>
                          <input
                            type="range"
                            min={LAYER_TUNING_OFFSET_MIN}
                            max={LAYER_TUNING_OFFSET_MAX}
                            step={0.001}
                            value={Number(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_x", 0.0)
                            )}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.offset_x", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>位置Y</span>
                          <input
                            type="range"
                            min={LAYER_TUNING_OFFSET_MIN}
                            max={LAYER_TUNING_OFFSET_MAX}
                            step={0.001}
                            value={Number(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.offset_y", 0.0)
                            )}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.offset_y", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>明るさ</span>
                          <input
                            type="range"
                            min={0.5}
                            max={2}
                            step={0.01}
                            value={Number(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_brightness", 1.2)
                            )}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.fg_brightness", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>コントラスト</span>
                          <input
                            type="range"
                            min={0.5}
                            max={2}
                            step={0.01}
                            value={Number(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_contrast", 1.08)
                            )}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.fg_contrast", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>彩度</span>
                          <input
                            type="range"
                            min={0.5}
                            max={2}
                            step={0.01}
                            value={Number(resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.fg_color", 0.98))}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.fg_color", Number(event.target.value))
                            }
                          />
                        </label>
                        <label>
                          <span>透明部分トリム</span>
                          <input
                            type="checkbox"
                            checked={Boolean(
                              resolveLayerTuningLeafValue(layerTuningDialog, "overrides.portrait.trim_transparent", false)
                            )}
                            onChange={(event) =>
                              setLayerTuningOverrideLeaf("overrides.portrait.trim_transparent", event.target.checked)
                            }
	                          />
	                        </label>
	                      </div>
                          );
                        })()}
	                      </>
	                    ) : null}

                  {layerTuningDialog.error ? (
                    <div className="thumbnail-planning-form__error" role="alert">
                      {layerTuningDialog.error}
                    </div>
                  ) : null}
	                  <div className="thumbnail-planning-form__actions">
	                    <p className="muted small-text" style={{ margin: "0 auto 0 0", alignSelf: "center" }}>
	                      保存: 設定だけ保存（PNGは更新しません）。保存して再生成: PNGを作り直して反映。
	                    </p>
	                    <button
	                      type="button"
	                      onClick={handleCloseLayerTuningDialog}
	                      disabled={layerTuningDialog.saving || layerTuningDialog.building}
	                    >
                      キャンセル
                    </button>
	                    <button
	                      type="button"
	                      onClick={() => handleSaveLayerTuning("save")}
	                      disabled={layerTuningDialog.saving || layerTuningDialog.building}
	                    >
	                      保存（設定のみ）
	                    </button>
	                    <button
	                      type="button"
	                      className="thumbnail-planning-form__submit"
	                      onClick={() => handleSaveLayerTuning("save_and_build")}
	                      disabled={
	                        layerTuningDialog.saving ||
	                        layerTuningDialog.building ||
	                        (layerTuningDialog.regenBg && !layerTuningDialog.allowGenerate)
	                      }
	                    >
	                      {layerTuningDialog.building ? "再生成中…" : "保存して再生成（PNG更新）"}
	                    </button>
	                  </div>
	                </form>
	              )}
            </div>
          </div>
        ) : null}
        {galleryCopyEdit ? (
          <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
            <div className="thumbnail-planning-dialog__backdrop" onClick={handleCloseGalleryCopyEdit} />
            <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                {galleryCopyEdit.channel} / {galleryCopyEdit.video}
              </div>
              <h2>文字を編集</h2>
              <p className="thumbnail-planning-dialog__meta">{galleryCopyEdit.projectTitle}</p>
            </header>
            <form
              className="thumbnail-planning-form"
              onSubmit={(event) => {
                event.preventDefault();
                handleGalleryCopyEditSubmit("save_and_compose");
              }}
            >
              <div className="thumbnail-planning-form__grid">
                <label className="thumbnail-planning-form__field--wide">
                  <span>上段（赤）</span>
                  <input
                    type="text"
                    value={galleryCopyEdit.copyUpper}
                    onChange={(event) => handleGalleryCopyEditFieldChange("copyUpper", event.target.value)}
                    placeholder="例: 放置は危険"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>中段（黄）</span>
                  <input
                    type="text"
                    value={galleryCopyEdit.copyTitle}
                    onChange={(event) => handleGalleryCopyEditFieldChange("copyTitle", event.target.value)}
                    placeholder="例: 夜の不安"
                  />
                </label>
                <label className="thumbnail-planning-form__field--wide">
                  <span>下段（白）</span>
                  <input
                    type="text"
                    value={galleryCopyEdit.copyLower}
                    onChange={(event) => handleGalleryCopyEditFieldChange("copyLower", event.target.value)}
                    placeholder="例: 今夜眠れる"
                  />
                </label>
              </div>
              {galleryCopyEdit.error ? <p className="thumbnail-library__alert">{galleryCopyEdit.error}</p> : null}
              <div className="thumbnail-planning-form__actions">
                <button type="button" onClick={handleCloseGalleryCopyEdit} disabled={galleryCopyEdit.saving}>
                  キャンセル
                </button>
                <button
                  type="button"
                  onClick={() => handleGalleryCopyEditSubmit("save")}
                  disabled={galleryCopyEdit.saving}
                >
                  保存だけ
                </button>
                <button type="submit" className="thumbnail-planning-form__submit" disabled={galleryCopyEdit.saving}>
                  {galleryCopyEdit.saving ? "反映中…" : "保存して再合成"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
