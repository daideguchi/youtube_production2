import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { fetchImageModelRouting, fetchSsotCatalog } from "../api/client";
import { getFireworksKeyStatus } from "../api/llmUsage";
import type { ChannelImageModelRouting, ChannelSummary, ImageModelRoutingSelection, ImageModelRoutingResponse, SsotCatalog } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

function normalizeKey(value?: string | null): string {
  return (value ?? "").trim();
}

function selectionLabel(sel: ImageModelRoutingSelection): string {
  const mk = normalizeKey(sel.model_key ?? "");
  if (!mk) return "（未設定）";
  const provider = normalizeKey(sel.provider ?? "");
  const model = normalizeKey(sel.model_name ?? "");
  if (provider && model) return `${mk} (${provider} / ${model})`;
  if (provider) return `${mk} (${provider})`;
  return mk;
}

function channelNameFromList(channels: ChannelSummary[], code: string): string {
  const hit = channels.find((c) => (c.code ?? "").toUpperCase() === code.toUpperCase());
  return (hit?.name ?? "").trim() || code;
}

function channelSortKey(code: string): [number, string] {
  const s = String(code || "").trim().toUpperCase();
  const m = s.match(/^CH(\d+)$/);
  if (m?.[1]) return [parseInt(m[1], 10), s];
  return [9999, s];
}

function isShortImageCode(id: string): boolean {
  return /^[a-z]-\d+$/.test(String(id || "").trim());
}

function tasksSignature(tasks: Record<string, unknown> | null | undefined): string {
  if (!tasks || typeof tasks !== "object" || Array.isArray(tasks)) return "";
  return Object.keys(tasks)
    .sort((a, b) => a.localeCompare(b))
    .map((k) => `${k}=${String(tasks[k] ?? "")}`)
    .join("|");
}

type ImageSlotMeta = {
  id: string;
  label: string;
  description: string;
  tasksSig: string;
};

function buildImageSlotMaps(slots: Array<{ id: string; label?: string; description?: string; tasks?: Record<string, unknown> | null }>) {
  const byId: Record<string, ImageSlotMeta> = {};
  const idsBySig: Record<string, string[]> = {};

  for (const s of slots) {
    const id = String(s.id ?? "").trim();
    if (!id) continue;
    const meta: ImageSlotMeta = {
      id,
      label: String(s.label ?? "").trim(),
      description: String(s.description ?? "").trim(),
      tasksSig: tasksSignature(s.tasks ?? null),
    };
    byId[id] = meta;
    const sig = meta.tasksSig || `id:${id}`;
    if (!idsBySig[sig]) idsBySig[sig] = [];
    idsBySig[sig].push(id);
  }

  const canonicalById: Record<string, string> = {};
  for (const sig of Object.keys(idsBySig)) {
    const ids = idsBySig[sig] ?? [];
    const canonical =
      ids
        .slice()
        .sort((a, b) => {
          const aShort = isShortImageCode(a) ? 0 : 1;
          const bShort = isShortImageCode(b) ? 0 : 1;
          if (aShort !== bShort) return aShort - bShort;
          if (a.length !== b.length) return a.length - b.length;
          return a.localeCompare(b);
        })[0] ?? (ids[0] ?? "");
    for (const id of ids) {
      canonicalById[id] = canonical;
    }
  }
  return { byId, canonicalById };
}

function canonicalizeImageCode(raw: string | null | undefined, canonicalById: Record<string, string>): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  return canonicalById[s] ?? s;
}

function describeImageCode(code: string, byId: Record<string, ImageSlotMeta>): string {
  const meta = byId[code];
  if (!meta) return "";
  const label = meta.label || "";
  const desc = meta.description || "";
  if (label && desc) return `${label} · ${desc}`;
  return label || desc || "";
}

type ScriptPolicyInfo = {
  task: string | null;
  codes: string[];
  primary_code: string;
  primary_provider: string;
  primary_model: string;
  primary_deployment: string;
};

function resolveScriptPolicy(catalog: SsotCatalog | null): ScriptPolicyInfo {
  const empty: ScriptPolicyInfo = {
    task: null,
    codes: [],
    primary_code: "",
    primary_provider: "",
    primary_model: "",
    primary_deployment: "",
  };
  const defs = catalog?.llm?.task_defs ?? null;
  if (!defs || typeof defs !== "object") return empty;

  const candidates = ["script_outline", "script_chapter_draft", "script_a_text_final_polish"];
  const task = candidates.find((k) => Boolean((defs as any)[k])) ?? null;
  if (!task) return empty;
  const def = (defs as any)[task] as any;
  const codes = Array.isArray(def?.model_keys) ? (def.model_keys as string[]).map((x) => String(x || "").trim()).filter(Boolean) : [];
  const primary = codes[0] ?? "";
  const resolved = Array.isArray(def?.resolved_models) ? (def.resolved_models as any[]) : [];
  const r0 = resolved.find((r) => String(r?.key ?? "").trim() === primary) ?? resolved[0] ?? null;
  const provider = r0?.provider ? String(r0.provider) : "";
  const modelName = r0?.model_name ? String(r0.model_name) : "";
  const deployment = r0?.deployment ? String(r0.deployment) : "";
  return {
    task,
    codes,
    primary_code: primary,
    primary_provider: provider,
    primary_model: modelName,
    primary_deployment: deployment,
  };
}

type VideoImagePolicy = { requirement: string };

const VIDEO_IMAGE_POLICY_DEFAULT: VideoImagePolicy = {
  requirement: "デフォ: img-flux-schnell-1（= f-1）",
};

const VIDEO_IMAGE_POLICY_BY_CHANNEL: Record<string, VideoImagePolicy> = {
  CH01: {
    requirement: "絶対に高品質: img-flux-max-1（= f-4） or img-gemini-flash-1（= g-1）",
  },
  CH02: {
    requirement: "img-flux-pro-1（= f-3） or img-flux-max-1（= f-4）",
  },
  CH04: {
    requirement: "img-flux-pro-1（= f-3） / img-flux-max-1（= f-4） / img-gemini-flash-1（= g-1）",
  },
  CH06: {
    requirement: "img-flux-pro-1（= f-3） / img-flux-max-1（= f-4） / img-gemini-flash-1（= g-1）",
  },
  CH08: {
    requirement: "schnellメインでOK: img-flux-schnell-1（= f-1）",
  },
};

function resolveVideoImagePolicy(code: string): VideoImagePolicy {
  const key = String(code || "").trim().toUpperCase();
  return VIDEO_IMAGE_POLICY_BY_CHANNEL[key] ?? VIDEO_IMAGE_POLICY_DEFAULT;
}

function resolvePolicyNowAssumption(code: string): string {
  const key = String(code || "").trim().toUpperCase();
  if (key === "CH08") return "いまは Gemini（g-1）で回す（品質要件が緩いのでOK）";
  return "いまは Gemini（g-1）で回す";
}

function resolveSsotModelOverrides(catalog: SsotCatalog | null | undefined): Array<{ env: string; task: string; selector: string }> {
  const list = catalog?.image?.model_slots?.active_overrides;
  if (!Array.isArray(list)) return [];
  return list
    .map((o) => ({
      env: String(o?.env ?? "").trim(),
      task: String(o?.task ?? "").trim() || "*",
      selector: String(o?.selector ?? "").trim(),
    }))
    .filter((o) => Boolean(o.env && o.selector));
}

function rowForChannel(channels: ChannelImageModelRouting[], code: string): ChannelImageModelRouting | null {
  const up = String(code || "").trim().toUpperCase();
  return channels.find((c) => String(c.channel || "").trim().toUpperCase() === up) ?? null;
}

function pickImageOverride(
  overrides: Array<{ env: string; task: string; selector: string }>,
  task: string
): { env: string; selector: string } | null {
  const t = String(task || "").trim();
  const specific = overrides.find((o) => String(o.task || "").trim() === t);
  if (specific?.selector) return { env: specific.env, selector: specific.selector };
  const global = overrides.find((o) => String(o.task || "").trim() === "*");
  if (global?.selector) return { env: global.env, selector: global.selector };
  return null;
}

type TaskMeta = { category: string; label: string; purpose?: string };

const TASK_META: Record<string, TaskMeta> = {
  script_outline: { category: "script", label: "台本アウトライン", purpose: "章立て/流れ（骨組み）を作る" },
  script_chapter_draft: { category: "script", label: "台本下書き（章）", purpose: "章ごとに本文（Aテキスト）を書き起こす" },
  script_a_text_final_polish: { category: "script", label: "台本 最終整形", purpose: "Aテキストを読みやすく整えて完成版にする" },

  belt_generation: { category: "video", label: "Bテキスト（ベルト）", purpose: "SRT等からベルト字幕用のJSONを作る" },
  title_generation: { category: "video", label: "タイトル案", purpose: "動画タイトル候補を作る" },
  visual_image_cues_plan: { category: "visual", label: "SRT→images キュー計画", purpose: "どの秒数にどんな画像が必要かを決める" },
  visual_section_plan: { category: "visual", label: "章→シーン計画", purpose: "章/セクションを映像用に組み立てる" },
  visual_prompt_refine: { category: "visual", label: "画像プロンプト整形", purpose: "人物/場面の一貫性が崩れないようプロンプトを整える" },
  visual_thumbnail_caption: { category: "visual", label: "サムネ要約（vision）", purpose: "サムネの内容を短く説明する" },
  thumbnail_comment_patch: { category: "visual", label: "サムネ修正（レビュー反映）", purpose: "人間のレビューコメントをJSONパッチにする" },

  tts_text_prepare: { category: "tts", label: "TTS前処理", purpose: "読み上げ向けに台本を整える" },
  tts_reading: { category: "tts", label: "読み補正", purpose: "固有名詞/読み/表記ゆれを補正する" },
  tts_pause: { category: "tts", label: "ポーズ設計", purpose: "聞きやすい間を入れる" },
  tts_segment: { category: "tts", label: "分割（セグメント）", purpose: "音声生成の単位に分割する" },
  tts_annotate: { category: "tts", label: "タグ付け", purpose: "TTSエンジン用のタグを付ける" },
  tts_natural_command: { category: "tts", label: "自然言語→命令(JSON)", purpose: "人間の指示を機械実行できるJSONにする" },
};

const CATEGORY_LABELS: Record<string, string> = {
  visual: "画像/映像（プロンプト/計画）",
  video: "動画（Bテキスト/タイトル）",
  tts: "音声（TTS補助）",
  script: "台本（script_*）",
  other: "その他",
};

const CATEGORY_ORDER: string[] = ["visual", "video", "tts", "script", "other"];

function taskCategory(task: string): string {
  const t = String(task || "").trim();
  if (!t) return "other";
  const meta = TASK_META[t];
  if (meta?.category) return meta.category;
  const prefix = t.split("_", 1)[0] ?? "";
  if (prefix === "visual" || prefix === "tts" || prefix === "script") return prefix;
  if (prefix === "belt" || prefix === "title") return "video";
  return "other";
}

function taskLabel(task: string): string {
  const t = String(task || "").trim();
  if (!t) return "";
  const meta = TASK_META[t];
  return meta?.label ? meta.label : t;
}

function taskPurpose(task: string): string {
  const t = String(task || "").trim();
  if (!t) return "";
  const meta = TASK_META[t];
  return meta?.purpose ? meta.purpose : "";
}

function formatResolvedModel(provider?: string | null, modelName?: string | null, deployment?: string | null): string {
  const p = String(provider ?? "").trim();
  const m = String(modelName ?? "").trim();
  const d = String(deployment ?? "").trim();
  if (!p && !m && !d) return "";
  if (p === "azure") {
    if (d) return `${p} / ${d}`;
    if (m) return `${p} / ${m}`;
    return p;
  }
  if (m) return `${p || "?"} / ${m}`;
  return p || "";
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((x) => String(x ?? "").trim())
    .filter(Boolean);
}

function normalizeTierMap(raw: Record<string, unknown> | null | undefined): Record<string, string[]> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string[]> = {};
  for (const [k, v] of Object.entries(raw)) {
    const tier = String(k ?? "").trim();
    if (!tier) continue;
    const codes = normalizeStringArray(v);
    if (codes.length === 0) continue;
    out[tier] = codes;
  }
  return out;
}

function resolveLlmSelectorInfo(
  selector: string,
  codeToModelKey: Record<string, string>,
  modelRegistry: Record<string, { provider?: string; model_name?: string; deployment?: string }>
): { selector: string; modelKey: string; resolvedText: string } {
  const sel = String(selector ?? "").trim();
  if (!sel) return { selector: "", modelKey: "", resolvedText: "" };
  const modelKey = codeToModelKey[sel] ?? sel;
  const meta = modelRegistry[modelKey] ?? null;
  const resolvedText = meta ? formatResolvedModel(meta.provider ?? "", meta.model_name ?? "", meta.deployment ?? "") : "";
  return { selector: sel, modelKey, resolvedText };
}

function resolveImageModelKeyText(
  modelKey: string,
  modelRegistry: Record<string, { provider?: string; model_name?: string }>
): string {
  const key = String(modelKey ?? "").trim();
  if (!key) return "";
  const meta = modelRegistry[key] ?? null;
  if (!meta) return "";
  const provider = String(meta.provider ?? "").trim();
  const modelName = String(meta.model_name ?? "").trim();
  if (provider && modelName) return `${provider} / ${modelName}`;
  return provider || modelName || "";
}

function humanizeModelSource(raw: string | null | undefined): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  if (s === "task_override.models") return "固定（task override）";
  if (s === "task_config.models") return "固定（task config）";
  if (s.startsWith("llm_model_slots:")) {
    const parts = s.split(":");
    const slot = parts[1] ?? "?";
    const kind = parts[2] ?? "tiers";
    const tier = parts[3] ?? "?";
    return `slot ${slot} / ${tier}${kind === "script_tiers" ? "（script）" : ""}`;
  }
  if (s.startsWith("llm_router.tiers:")) {
    return `router tiers（${s.replace("llm_router.tiers:", "")}）`;
  }
  return s;
}

function isCodexExecEnabled(codexExec: any): boolean {
  return Boolean(codexExec?.effective?.enabled);
}

function codexExecAppliesToTask(task: string, codexExec: any): boolean {
  if (!isCodexExecEnabled(codexExec)) return false;
  const t = String(task || "").trim();
  if (!t) return false;
  const excludes = normalizeStringArray(codexExec?.effective?.exclude_tasks ?? codexExec?.selection?.exclude_tasks ?? []);
  if (excludes.includes(t)) return false;
  const includeTasks = normalizeStringArray(codexExec?.selection?.include_tasks ?? []);
  if (includeTasks.includes(t)) return true;
  const prefixes = normalizeStringArray(codexExec?.selection?.include_task_prefixes ?? []);
  return prefixes.some((p) => (p ? t.startsWith(p) : false));
}

function execPathLabel(task: string, codexExec: any, agentMode: any): string {
  const mode = String(agentMode?.mode ?? "api").trim().toLowerCase();
  if (mode === "agent" || mode === "think") return "THINK/AGENT（pending）";
  if (codexExecAppliesToTask(task, codexExec)) return "codex exec → API";
  return "API";
}

function fireworksCount(counts: any[] | null | undefined, status: string): number {
  if (!Array.isArray(counts)) return 0;
  const key = String(status || "").trim();
  const hit = counts.find((c) => String((c as any)?.status ?? "").trim() === key) as any;
  const n = hit?.count;
  if (typeof n === "number") return n;
  const parsed = Number(n ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatFireworksCounts(counts: any[] | null | undefined): string {
  const ok = fireworksCount(counts, "ok");
  const suspended = fireworksCount(counts, "suspended");
  const exhausted = fireworksCount(counts, "exhausted");
  const invalid = fireworksCount(counts, "invalid");
  const error = fireworksCount(counts, "error");
  const unknown = fireworksCount(counts, "unknown");
  const parts = [`ok=${ok}`];
  if (suspended) parts.push(`suspended=${suspended}`);
  if (exhausted) parts.push(`exhausted=${exhausted}`);
  if (invalid) parts.push(`invalid=${invalid}`);
  if (error) parts.push(`error=${error}`);
  if (unknown) parts.push(`unknown=${unknown}`);
  return parts.join(" / ");
}

type PolicyTab = "channels" | "images" | "scripts" | "tasks" | "diagnostics";

type UiLevel = "simple" | "detail";

const POLICY_TABS: Array<{ id: PolicyTab; label: string; hint: string }> = [
  { id: "channels", label: "チャンネル別", hint: "まずはここ（3点だけ）" },
  { id: "images", label: "画像（動画/サムネ）", hint: "コードの意味・要件・設定場所" },
  { id: "scripts", label: "台本（script_*）", hint: "台本モデルの見方・スロット" },
  { id: "tasks", label: "共通タスク", hint: "Bテキスト / 画像計画 / TTS補助" },
  { id: "diagnostics", label: "診断/トラブル", hint: "412・キー・漏れチェック" },
];

function isPolicyTab(value: unknown): value is PolicyTab {
  const v = String(value ?? "").trim();
  return POLICY_TABS.some((t) => t.id === (v as any));
}

export function ChannelModelPolicyPage() {
  const { channels: channelSummaries } = useOutletContext<ShellOutletContext>();
  const [routing, setRouting] = useState<ImageModelRoutingResponse | null>(null);
  const [catalog, setCatalog] = useState<SsotCatalog | null>(null);
  const [fireworksStatus, setFireworksStatus] = useState<any>(null);
  const [fireworksStatusError, setFireworksStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [taskQuery, setTaskQuery] = useState("");
  const [channelQuery, setChannelQuery] = useState("");
  const [showChannelDetails, setShowChannelDetails] = useState(false);
  const [includeExecSlotInCode, setIncludeExecSlotInCode] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [tab, setTab] = useState<PolicyTab>(() => {
    try {
      const raw = window.localStorage.getItem("modelPolicy.tab");
      return isPolicyTab(raw) ? raw : "channels";
    } catch {
      return "channels";
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem("modelPolicy.tab", tab);
    } catch {
      // ignore storage errors
    }
  }, [tab]);

  const [uiLevel, setUiLevel] = useState<UiLevel>(() => {
    try {
      const raw = window.localStorage.getItem("modelPolicy.uiLevel");
      const v = String(raw ?? "").trim();
      return v === "detail" ? "detail" : "simple";
    } catch {
      return "simple";
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem("modelPolicy.uiLevel", uiLevel);
    } catch {
      // ignore storage errors
    }
  }, [uiLevel]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setFireworksStatusError(null);
    try {
      const fwPromise = getFireworksKeyStatus({ pools: "script,image" }).catch((e) => ({
        __error: e instanceof Error ? e.message : String(e),
      }));
      const [routingResp, catalogResp, fwResp] = await Promise.all([fetchImageModelRouting(), fetchSsotCatalog(), fwPromise]);
      setRouting(routingResp);
      setCatalog(catalogResp);
      if (fwResp && typeof (fwResp as any).__error === "string") {
        setFireworksStatus(null);
        setFireworksStatusError(String((fwResp as any).__error));
      } else {
        setFireworksStatus(fwResp || null);
        setFireworksStatusError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const sortedChannels = useMemo(() => {
    const codes = new Set<string>();
    for (const c of channelSummaries) {
      const code = String(c.code || "").trim().toUpperCase();
      if (code) codes.add(code);
    }
    for (const c of routing?.channels ?? []) {
      const code = String(c.channel || "").trim().toUpperCase();
      if (code) codes.add(code);
    }
    return Array.from(codes).sort((a, b) => {
      const [an, as] = channelSortKey(a);
      const [bn, bs] = channelSortKey(b);
      if (an !== bn) return an - bn;
      return as.localeCompare(bs);
    });
  }, [channelSummaries, routing]);

  const filteredChannels = useMemo(() => {
    const q = channelQuery.trim().toLowerCase();
    if (!q) return sortedChannels;
    return sortedChannels.filter((ch) => {
      const name = channelNameFromList(channelSummaries, ch);
      return `${ch} ${name}`.toLowerCase().includes(q);
    });
  }, [sortedChannels, channelQuery, channelSummaries]);

  const activeOverrides = useMemo(() => resolveSsotModelOverrides(catalog), [catalog]);

  const imageSlots = useMemo(() => catalog?.image?.model_slots?.slots ?? [], [catalog]);
  const llmSlots = useMemo(() => catalog?.llm?.model_slots?.slots ?? [], [catalog]);
  const scriptPolicy = useMemo(() => resolveScriptPolicy(catalog), [catalog]);

  const llmActiveSlot = useMemo(() => catalog?.llm?.model_slots?.active_slot ?? null, [catalog]);
  const llmDefaultSlot = useMemo(() => catalog?.llm?.model_slots?.default_slot ?? null, [catalog]);
  const llmSlotPath = useMemo(() => catalog?.llm?.model_slots?.path ?? null, [catalog]);
  const llmRouterPath = useMemo(() => catalog?.llm?.router_config?.path ?? null, [catalog]);
  const llmOverridesPath = useMemo(() => catalog?.llm?.task_overrides?.path ?? null, [catalog]);
  const execSlots = useMemo(() => (catalog as any)?.llm?.exec_slots ?? null, [catalog]);
  const codexExec = useMemo(() => catalog?.llm?.codex_exec ?? null, [catalog]);
  const agentMode = useMemo(() => catalog?.llm?.agent_mode ?? null, [catalog]);

  const llmModelRegistry = useMemo(() => (catalog?.llm?.model_registry ?? {}) as Record<string, any>, [catalog]);
  const llmProviderStatus = useMemo(() => (catalog?.llm?.providers ?? []) as any[], [catalog]);
  const llmCodeToModelKey = useMemo(() => {
    const out: Record<string, string> = {};
    const codes = catalog?.llm?.model_codes?.codes ?? [];
    for (const c of codes) {
      const code = String((c as any)?.code ?? "").trim();
      const modelKey = String((c as any)?.model_key ?? "").trim();
      if (code && modelKey) out[code] = modelKey;
    }
    return out;
  }, [catalog]);

  const llmCodeToLabel = useMemo(() => {
    const out: Record<string, string> = {};
    const codes = catalog?.llm?.model_codes?.codes ?? [];
    for (const c of codes) {
      const code = String((c as any)?.code ?? "").trim();
      const label = String((c as any)?.label ?? "").trim();
      if (code && label) out[code] = label;
    }
    return out;
  }, [catalog]);

  const imageModelRegistry = useMemo(() => (catalog?.image?.model_registry ?? {}) as Record<string, any>, [catalog]);
  const imageProviderStatus = useMemo(() => (catalog?.image?.providers ?? []) as any[], [catalog]);

  const llmTaskRows = useMemo(() => {
    const defs = catalog?.llm?.task_defs ?? null;
    if (!defs || typeof defs !== "object") return [];
    const q = taskQuery.trim().toLowerCase();
    const rows = Object.keys(defs)
      .map((task) => {
        const def = (defs as any)[task] as any;
        const tier = def?.tier ? String(def.tier) : "";
        const models = Array.isArray(def?.model_keys) ? (def.model_keys as string[]).map((x) => String(x || "").trim()).filter(Boolean) : [];
        const resolved = Array.isArray(def?.resolved_models) ? (def.resolved_models as any[]) : [];
        const primary = resolved[0] ?? null;
        const provider = primary?.provider ? String(primary.provider) : "";
        const modelName = primary?.model_name ? String(primary.model_name) : "";
        const deployment = primary?.deployment ? String(primary.deployment) : "";
        const source = def?.model_source ? String(def.model_source) : "";
        const category = taskCategory(task);
        const label = taskLabel(task);
        const purpose = taskPurpose(task);
        const execPath = execPathLabel(task, codexExec, agentMode);
        const modelChain = models.join(" → ");
        const resolvedText = formatResolvedModel(provider, modelName, deployment);
        const sourceText = humanizeModelSource(source);
        const hay = `${task} ${label} ${purpose} ${tier} ${execPath} ${modelChain} ${resolvedText} ${sourceText}`.toLowerCase();
        return { task, category, label, purpose, tier, execPath, models, modelChain, resolvedText, sourceText, _hay: hay };
      })
      .filter((r) => (q ? r._hay.includes(q) : true))
      .sort((a, b) => {
        const ao = CATEGORY_ORDER.indexOf(a.category);
        const bo = CATEGORY_ORDER.indexOf(b.category);
        const ax = ao >= 0 ? ao : 999;
        const bx = bo >= 0 ? bo : 999;
        if (ax !== bx) return ax - bx;
        return a.task.localeCompare(b.task);
      });
    return rows;
  }, [catalog, taskQuery, codexExec, agentMode]);

  const { byId: imageSlotById, canonicalById: imageCanonicalById } = useMemo(() => buildImageSlotMaps(imageSlots), [imageSlots]);
  const forcedThumb = useMemo(() => pickImageOverride(activeOverrides, "thumbnail_image_gen"), [activeOverrides]);
  const forcedVideo = useMemo(() => pickImageOverride(activeOverrides, "visual_image_gen"), [activeOverrides]);
  const forcedAny = useMemo(() => pickImageOverride(activeOverrides, "*"), [activeOverrides]);

  const defaultVideoSelector = useMemo(() => canonicalizeImageCode("img-flux-schnell-1", imageCanonicalById) || "f-1", [imageCanonicalById]);
  const defaultThumbSelector = useMemo(() => canonicalizeImageCode("img-flux-max-1", imageCanonicalById) || "f-4", [imageCanonicalById]);

  const imageModelKeyByCodeAndTask = useMemo(() => {
    const out: Record<string, Record<string, string>> = {};
    for (const s of imageSlots as any[]) {
      const rawId = String((s as any)?.id ?? "").trim();
      if (!rawId) continue;
      const canonical = canonicalizeImageCode(rawId, imageCanonicalById) || rawId;
      const tasks = (s as any)?.tasks;
      if (!tasks || typeof tasks !== "object" || Array.isArray(tasks)) continue;
      if (!out[canonical]) out[canonical] = {};
      for (const [k, v] of Object.entries(tasks as Record<string, unknown>)) {
        const task = String(k ?? "").trim();
        const modelKey = String(v ?? "").trim();
        if (task && modelKey) out[canonical][task] = modelKey;
      }
    }
    return out;
  }, [imageSlots, imageCanonicalById]);

  const resolveImageTaskModelText = useCallback(
    (code: string, task: string): string => {
      const c = String(code ?? "").trim();
      const t = String(task ?? "").trim();
      if (!c || !t) return "";
      const mk = imageModelKeyByCodeAndTask[c]?.[t] ?? "";
      if (!mk) return "";
      return resolveImageModelKeyText(mk, imageModelRegistry);
    },
    [imageModelKeyByCodeAndTask, imageModelRegistry]
  );

  const llmMissing = useMemo(() => (catalog?.llm?.missing_task_defs ?? []) as string[], [catalog]);
  const imageMissing = useMemo(() => (catalog?.image?.missing_task_defs ?? []) as string[], [catalog]);

  const llmUnresolvedSelectors = useMemo(() => {
    const selectors = new Set<string>();
    const defs = catalog?.llm?.task_defs ?? null;
    if (defs && typeof defs === "object") {
      for (const task of Object.keys(defs)) {
        const def = (defs as any)[task] as any;
        const keys = Array.isArray(def?.model_keys) ? (def.model_keys as string[]) : [];
        for (const k of keys) selectors.add(String(k ?? "").trim());
      }
    }
    const slotList = catalog?.llm?.model_slots?.slots ?? [];
    for (const s of slotList as any[]) {
      const tiers = normalizeTierMap((s as any)?.tiers ?? null);
      const scriptTiers = normalizeTierMap((s as any)?.script_tiers ?? null);
      for (const codes of Object.values(tiers)) for (const c of codes) selectors.add(String(c ?? "").trim());
      for (const codes of Object.values(scriptTiers)) for (const c of codes) selectors.add(String(c ?? "").trim());
    }

    const unresolved: string[] = [];
    Array.from(selectors).forEach((sel) => {
      if (!sel) return;
      const modelKey = llmCodeToModelKey[sel] ?? sel;
      if (!llmModelRegistry[modelKey]) unresolved.push(sel);
    });
    unresolved.sort((a, b) => a.localeCompare(b));
    return unresolved;
  }, [catalog, llmCodeToModelKey, llmModelRegistry]);

  const imageUnresolvedModelKeys = useMemo(() => {
    const unresolved: string[] = [];
    for (const s of imageSlots as any[]) {
      const tasks = (s as any)?.tasks;
      if (!tasks || typeof tasks !== "object" || Array.isArray(tasks)) continue;
      for (const v of Object.values(tasks)) {
        const mk = String(v ?? "").trim();
        if (!mk) continue;
        if (!imageModelRegistry[mk]) unresolved.push(mk);
      }
    }
    return Array.from(new Set(unresolved)).sort((a, b) => a.localeCompare(b));
  }, [imageSlots, imageModelRegistry]);

  const llmActiveSlotEntry = useMemo(() => {
    const id = (llmActiveSlot as any)?.id;
    if (typeof id !== "number") return null;
    return (llmSlots as any[]).find((s) => Number((s as any)?.id) === id) ?? null;
  }, [llmActiveSlot, llmSlots]);

  const llmDefaultSlotEntry = useMemo(() => {
    const id = llmDefaultSlot;
    if (typeof id !== "number") return null;
    return (llmSlots as any[]).find((s) => Number((s as any)?.id) === id) ?? null;
  }, [llmDefaultSlot, llmSlots]);

  const copyToClipboard = useCallback(
    async (text: string) => {
      const value = String(text ?? "");
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        setCopied(value);
        window.setTimeout(() => setCopied((cur) => (cur === value ? null : cur)), 1200);
      } catch {
        try {
          const ta = document.createElement("textarea");
          ta.value = value;
          ta.style.position = "fixed";
          ta.style.top = "-1000px";
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          setCopied(value);
          window.setTimeout(() => setCopied((cur) => (cur === value ? null : cur)), 1200);
        } catch {
          // ignore
        }
      }
    },
    [setCopied]
  );

  const llmModeNow = String(agentMode?.mode ?? "api").trim().toLowerCase() || "api";
  const llmFailoverToThink = agentMode?.failover_to_think === undefined ? true : Boolean(agentMode.failover_to_think);
  const codexEnabled = isCodexExecEnabled(codexExec);
  const codexEnabledSource = String(codexExec?.effective?.enabled_source ?? "").trim();
  const codexProfileEffective = String(codexExec?.effective?.profile ?? codexExec?.profile ?? "").trim();
  const codexModelEffective = String(codexExec?.effective?.model ?? codexExec?.model ?? "").trim();
  const codexSandboxEffective = String(codexExec?.effective?.sandbox ?? codexExec?.sandbox ?? "").trim();
  const execSlotPath = String((execSlots as any)?.path ?? "").trim();
  const execLocalPath = String((execSlots as any)?.local_path ?? "").trim();
  const execDefaultSlot = (execSlots as any)?.default_slot;
  const execActive = (execSlots as any)?.active_slot ?? null;
  const execActiveId = typeof execActive?.id === "number" ? execActive.id : null;
  const execActiveLabel = String(execActive?.label ?? "").trim();
  const execActiveSource = String(execActive?.source ?? "").trim();
  const execCodexOverride = (execSlots as any)?.effective?.codex_exec_enabled_override;
  const execFailoverEffective = (execSlots as any)?.effective?.api_failover_to_think;

  const llmModelCountsByProvider = useMemo(() => {
    const out: Record<string, number> = {};
    for (const ent of Object.values(llmModelRegistry || {})) {
      const p = String((ent as any)?.provider ?? "").trim() || "?";
      out[p] = (out[p] || 0) + 1;
    }
    return out;
  }, [llmModelRegistry]);

  const imageModelCountsByProvider = useMemo(() => {
    const out: Record<string, number> = {};
    for (const ent of Object.values(imageModelRegistry || {})) {
      const p = String((ent as any)?.provider ?? "").trim() || "?";
      out[p] = (out[p] || 0) + 1;
    }
    return out;
  }, [imageModelRegistry]);

  const llmCodesCount = (catalog?.llm?.model_codes?.codes ?? []).length;
  const imageCodesCount = (catalog?.image?.model_slots?.slots ?? []).length;
  const llmModelsCount = Object.keys(llmModelRegistry || {}).length;
  const imageModelsCount = Object.keys(imageModelRegistry || {}).length;

  const defaultSlotTiers = useMemo(() => normalizeTierMap((llmDefaultSlotEntry as any)?.tiers ?? null), [llmDefaultSlotEntry]);
  const defaultSlotScriptTiers = useMemo(() => normalizeTierMap((llmDefaultSlotEntry as any)?.script_tiers ?? null), [llmDefaultSlotEntry]);

  const fwScriptPool = (fireworksStatus as any)?.pools?.script ?? null;
  const fwImagePool = (fireworksStatus as any)?.pools?.image ?? null;
  const fwGeneratedAt = String((fireworksStatus as any)?.generated_at ?? "").trim();
  const fwScriptCounts = fwScriptPool ? (fwScriptPool as any)?.counts : null;
  const fwImageCounts = fwImagePool ? (fwImagePool as any)?.counts : null;
  const fwScriptOk = fireworksCount(fwScriptCounts, "ok");
  const fwImageOk = fireworksCount(fwImageCounts, "ok");

  const isDetail = uiLevel === "detail";

  const showChannels = tab === "channels";
  const showImages = tab === "images";
  const showScripts = tab === "scripts";
  const showTasks = tab === "tasks";
  const showDiagnostics = tab === "diagnostics";

  const diagnosticsIssuesCount =
    llmMissing.length + imageMissing.length + llmUnresolvedSelectors.length + imageUnresolvedModelKeys.length;

  const scriptTaskRows = useMemo(() => llmTaskRows.filter((r) => r.category === "script"), [llmTaskRows]);
  const commonTaskRows = useMemo(() => llmTaskRows.filter((r) => r.category !== "script"), [llmTaskRows]);

	  return (
	    <section className="main-content" style={{ padding: 18 }}>
	      <div className="main-status" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 14 }}>
	        <div style={{ display: "grid", gap: 8 }}>
	          <div style={{ fontSize: 18, fontWeight: 950 }}>モデル方針</div>
	          <div className="muted small-text" style={{ lineHeight: 1.65 }}>
	            「どの処理が、どのモデルで動くか」を“人間が判断できる形”にまとめたページです。{" "}
	            {!isDetail ? (
	              <span>（いまは「やさしい」表示。必要なときだけ「詳細」へ切替）</span>
	            ) : (
	              <span>（詳細=コード/設定ファイル/ENV も表示）</span>
	            )}
	          </div>
	          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
	            <span className="status-chip status-chip--danger">いまは Gemini-only（画像生成）</span>
	            {scriptPolicy.primary_code ? (
	              <span className="status-chip" style={{ opacity: 0.9 }}>
	                台本: <span className="mono">{scriptPolicy.primary_code}</span>
	              </span>
	            ) : null}
	            {isDetail ? (
	              <span className="status-chip" style={{ opacity: 0.8 }}>
	                SoT: <span className="mono">ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md</span>
	              </span>
	            ) : null}
	          </div>
	        </div>

	        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
	          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
	            <button
	              type="button"
	              className={`workspace-button workspace-button--compact ${uiLevel === "simple" ? "workspace-button--primary" : "workspace-button--ghost"}`}
	              onClick={() => setUiLevel("simple")}
	            >
	              やさしい
	            </button>
	            <button
	              type="button"
	              className={`workspace-button workspace-button--compact ${uiLevel === "detail" ? "workspace-button--primary" : "workspace-button--ghost"}`}
	              onClick={() => setUiLevel("detail")}
	            >
	              詳細
	            </button>
	          </div>
	          <button
	            type="button"
	            className="workspace-button workspace-button--ghost workspace-button--compact"
	            onClick={() => void refresh()}
	            disabled={loading}
	          >
	            再読み込み
	          </button>
	          <Link to="/image-model-routing" className="workspace-button workspace-button--ghost workspace-button--compact" style={{ textDecoration: "none" }}>
	            画像設定
	          </Link>
	          <button
	            type="button"
	            className="workspace-button workspace-button--ghost workspace-button--compact"
	            onClick={() => setTab("diagnostics")}
	          >
	            診断
	          </button>
	          <Link to="/ssot" className="workspace-button workspace-button--ghost workspace-button--compact" style={{ textDecoration: "none" }}>
	            SSOT
	          </Link>
	        </div>
	      </div>

      {loading || error ? (
        <div className="main-status" style={{ marginTop: 12, gap: 10, flexWrap: "wrap" }}>
          {loading ? <span className="status-chip">読み込み中…</span> : null}
          {error ? <span className="status-chip status-chip--danger">{error}</span> : null}
        </div>
      ) : null}

	      <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
	        {POLICY_TABS.map((t) => {
	          const active = tab === t.id;
	          const cls = active ? "workspace-button workspace-button--primary workspace-button--compact" : "workspace-button workspace-button--ghost workspace-button--compact";
	          return (
	            <button
	              key={`policy-tab-${t.id}`}
	              type="button"
	              className={cls}
	              onClick={() => setTab(t.id)}
	              aria-pressed={active}
	              title={t.hint}
	            >
	              {t.label}
	            </button>
	          );
	        })}
	        <span className="muted small-text" style={{ marginLeft: 4 }}>
	          {POLICY_TABS.find((t) => t.id === tab)?.hint ?? ""}
	        </span>
	      </div>

	      <div className="main-status" style={{ marginTop: 10, gap: 10, flexWrap: "wrap", alignItems: "center" }}>
	        <span className="status-chip" style={{ opacity: 0.85 }}>
	          チャンネル: {filteredChannels.length}/{sortedChannels.length}
	        </span>
	        <button
	          type="button"
	          className={`workspace-button workspace-button--compact ${diagnosticsIssuesCount > 0 ? "workspace-button--primary" : "workspace-button--ghost"}`}
	          onClick={() => setTab("diagnostics")}
	          title="漏れ/未解決/412などの確認"
	        >
	          診断: <span className="mono">{diagnosticsIssuesCount > 0 ? `${diagnosticsIssuesCount}件` : "OK"}</span>
	        </button>
	        <span className="status-chip" style={{ opacity: 0.85 }}>
	          実行: <span className="mono">{llmModeNow}</span> / exec slot{" "}
	          <span className="mono">{execActiveId !== null ? String(execActiveId) : "?"}</span>
	          {execActiveLabel ? <span className="muted">（{execActiveLabel}）</span> : null}
	          {codexEnabled ? <span className="muted">（codex=ON）</span> : null}
	        </span>
        <span className="status-chip" style={{ opacity: 0.85 }}>
          LLM slot: <span className="mono">{llmActiveSlot ? String((llmActiveSlot as any).id) : "?"}</span>
          {llmActiveSlotEntry && (llmActiveSlotEntry as any)?.label ? (
            <span className="muted">（{String((llmActiveSlotEntry as any).label)}）</span>
          ) : null}
        </span>
        {copied ? (
          <span className="status-chip" style={{ opacity: 0.85 }}>
            copied: <span className="mono">{copied}</span>
          </span>
        ) : null}
      </div>

      {showDiagnostics ? (
        <div className="main-alert" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 950, marginBottom: 6 }}>デフォルト設定 / 設定済みモデル / 利用可否</div>
        <div className="muted small-text" style={{ lineHeight: 1.65 }}>
          ここだけ見れば「何が動くか」を判断できます。運用の切替は <span className="mono">slot</span>（数値）と{" "}
          <span className="mono">code</span>（短い記号）で行い、YAMLのモデル名は書き換えません。
        </div>

        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span className="status-chip">
            default LLM_MODEL_SLOT=<span className="mono">{typeof llmDefaultSlot === "number" ? String(llmDefaultSlot) : "?"}</span>
            {llmDefaultSlotEntry && (llmDefaultSlotEntry as any)?.label ? (
              <span className="muted">（{String((llmDefaultSlotEntry as any).label)}）</span>
            ) : null}
          </span>
          <span className="status-chip">
            active LLM_MODEL_SLOT=<span className="mono">{llmActiveSlot ? String((llmActiveSlot as any).id) : "?"}</span>
            {llmActiveSlotEntry && (llmActiveSlotEntry as any)?.label ? (
              <span className="muted">（{String((llmActiveSlotEntry as any).label)}）</span>
            ) : null}
          </span>
          <span className="status-chip">
            default LLM_EXEC_SLOT=<span className="mono">{typeof execDefaultSlot === "number" ? String(execDefaultSlot) : "?"}</span>
          </span>
          <span className="status-chip">
            active LLM_EXEC_SLOT=<span className="mono">{execActiveId !== null ? String(execActiveId) : "?"}</span>{" "}
            <span className="muted">（mode={llmModeNow}）</span>
          </span>
          <span className="status-chip">
            default thumb=<span className="mono">{defaultThumbSelector}</span> / default video=<span className="mono">{defaultVideoSelector}</span>
          </span>
          <span className="status-chip">
            configured: LLM models=<span className="mono">{String(llmModelsCount)}</span> / codes=<span className="mono">{String(llmCodesCount)}</span> · image models=
            <span className="mono">{String(imageModelsCount)}</span> / codes=<span className="mono">{String(imageCodesCount)}</span>
          </span>
          {Object.keys(defaultSlotTiers).length > 0 ? (
            <span className="status-chip" style={{ opacity: 0.9 }}>
              default tier: <span className="mono">hr={defaultSlotTiers.heavy_reasoning?.[0] ?? "—"}</span> /{" "}
              <span className="mono">std={defaultSlotTiers.standard?.[0] ?? "—"}</span> /{" "}
              <span className="mono">cheap={defaultSlotTiers.cheap?.[0] ?? "—"}</span>
              {Object.keys(defaultSlotScriptTiers).length > 0 ? (
                <>
                  {" "}
                  / <span className="mono">script={defaultSlotScriptTiers.heavy_reasoning?.[0] ?? "—"}</span>
                </>
              ) : null}
            </span>
          ) : null}
        </div>

        <div style={{ marginTop: 10 }}>
          <div className="muted small-text" style={{ marginBottom: 6 }}>
            プロバイダ利用可否（env/キーが揃っているか）
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            {llmProviderStatus.map((p) => {
              const provider = String((p as any)?.provider ?? "").trim() || "?";
              const ready = Boolean((p as any)?.ready);
              const missing = Array.isArray((p as any)?.missing_envs) ? ((p as any).missing_envs as string[]).join(", ") : "";
              const cand = (p as any)?.candidate_keys_count;
              const extra = provider === "fireworks" && typeof cand === "number" ? `keys=${String(cand)}` : "";
              const chipClass = ready ? "status-chip" : "status-chip status-chip--danger";
              return (
                <span key={`llm-provider-${provider}`} className={chipClass} style={{ opacity: 0.9 }}>
                  LLM:{provider} <span className="mono">{ready ? "OK" : "NG"}</span>
                  {extra ? <span className="muted">（{extra}）</span> : null}
                  {!ready && missing ? <span className="muted">（missing: {missing}）</span> : null}
                </span>
              );
            })}
            {imageProviderStatus.map((p) => {
              const provider = String((p as any)?.provider ?? "").trim() || "?";
              const ready = Boolean((p as any)?.ready);
              const missing = Array.isArray((p as any)?.missing_envs) ? ((p as any).missing_envs as string[]).join(", ") : "";
              const cand = (p as any)?.candidate_keys_count;
              const extra = provider === "fireworks" && typeof cand === "number" ? `keys=${String(cand)}` : "";
              const chipClass = ready ? "status-chip" : "status-chip status-chip--danger";
              return (
                <span key={`img-provider-${provider}`} className={chipClass} style={{ opacity: 0.9 }}>
                  IMG:{provider} <span className="mono">{ready ? "OK" : "NG"}</span>
                  {extra ? <span className="muted">（{extra}）</span> : null}
                  {!ready && missing ? <span className="muted">（missing: {missing}）</span> : null}
                </span>
              );
            })}
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          <div className="muted small-text" style={{ marginBottom: 6 }}>
            Fireworksキー状態（412/ban等の切り分け用）
            {fwGeneratedAt ? (
              <>
                {" "}
                / generated_at=<span className="mono">{fwGeneratedAt}</span>
              </>
            ) : null}
          </div>
          {fireworksStatusError ? <div className="muted small-text">（取得失敗: {fireworksStatusError}）</div> : null}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <span className={`status-chip ${fwScriptOk > 0 ? "" : "status-chip--danger"}`} style={{ opacity: 0.9 }}>
              FW:script <span className="mono">{fwScriptPool ? formatFireworksCounts(fwScriptCounts) : "（未取得）"}</span>
            </span>
            <span className={`status-chip ${fwImageOk > 0 ? "" : "status-chip--danger"}`} style={{ opacity: 0.9 }}>
              FW:image <span className="mono">{fwImagePool ? formatFireworksCounts(fwImageCounts) : "（未取得）"}</span>
            </span>
            <Link to="/llm-usage" className="workspace-button" style={{ textDecoration: "none" }}>
              詳細（probe/leases）
            </Link>
          </div>
        </div>

        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: "pointer", fontWeight: 900 }}>設定済みモデルの内訳</summary>
          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            <div className="muted small-text">
              LLM models by provider:{" "}
              <span className="mono">
                {Object.keys(llmModelCountsByProvider)
                  .sort((a, b) => a.localeCompare(b))
                  .map((k) => `${k}=${llmModelCountsByProvider[k]}`)
                  .join(", ") || "—"}
              </span>
            </div>
            <div className="muted small-text">
              image models by provider:{" "}
              <span className="mono">
                {Object.keys(imageModelCountsByProvider)
                  .sort((a, b) => a.localeCompare(b))
                  .map((k) => `${k}=${imageModelCountsByProvider[k]}`)
                  .join(", ") || "—"}
              </span>
            </div>
            <div className="muted small-text">
              コード辞書の詳細は、このページ下部の <span className="mono">LLMコード辞書</span> / <span className="mono">画像モデルコード</span> を参照。
            </div>
          </div>
        </details>
        </div>
      ) : null}

      <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
        {showImages ? (
          <div className="main-status" style={{ margin: 0, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
            <div style={{ fontWeight: 950 }}>画像の運用（いま何を使う？）</div>
            <div className="muted small-text" style={{ lineHeight: 1.7 }}>
              現在は <span className="mono">Gemini（g-1）</span> だけが安定して通る前提なので、画像生成は Gemini-only で回します。
              <br />
              画像の設定変更は <Link to="/image-model-routing">画像設定</Link> から。
            </div>
            {isDetail ? (
              <>
                <div className="muted small-text" style={{ marginTop: 4 }}>
                  ENV（実行時 override / 一時的な強制）
                </div>
                {activeOverrides.length > 0 ? (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {activeOverrides.map((o) => (
                      <li key={`${o.env}:${o.task}`} className="mono" style={{ opacity: 0.95 }}>
                        {o.env}={o.selector} <span style={{ opacity: 0.75 }}>（task={o.task}）</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="mono muted">（設定なし）</div>
                )}
                <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.55 }}>
                  推奨（Gemini-onlyを強制）:
                  <span className="mono"> IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=g-1</span> /{" "}
                  <span className="mono">IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=g-1</span>{" "}
                  <span className="muted">（alias: img-gemini-flash-1）</span>
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        {showChannels ? (
          <div className="main-status" style={{ margin: 0, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
            <div style={{ fontWeight: 950 }}>迷わないための要点（ここだけ見ればOK）</div>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.75 }}>
              <li>
                チャンネルごとに違うのは <span className="mono">3つ</span> だけ：<span style={{ fontWeight: 800 }}>サムネ</span> /{" "}
                <span style={{ fontWeight: 800 }}>台本</span> / <span style={{ fontWeight: 800 }}>動画内画像</span>
              </li>
              <li>
                それ以外（Bテキスト・画像計画・TTS補助など）は <span style={{ fontWeight: 800 }}>共通タスク</span>（チャンネル別ではない）
              </li>
              <li>
                APIが落ちたときの動き（THINK/AGENTに回すか、停止するか）は{" "}
                <span className="mono">LLM_EXEC_SLOT</span> とルールで決まる
              </li>
            </ul>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <button type="button" className="workspace-button workspace-button--ghost workspace-button--compact" onClick={() => setTab("images")}>
                画像のルールを見る
              </button>
              <button type="button" className="workspace-button workspace-button--ghost workspace-button--compact" onClick={() => setTab("tasks")}>
                共通タスクを見る
              </button>
              <button type="button" className="workspace-button workspace-button--ghost workspace-button--compact" onClick={() => setTab("diagnostics")}>
                診断を見る
              </button>
            </div>
            {isDetail ? (
              <details>
                <summary style={{ cursor: "pointer", fontWeight: 800 }}>詳細（設定の参照先/slot）</summary>
                <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.7 }}>
                  LLM_MODEL_SLOT: default{" "}
                  <span className="mono">{typeof llmDefaultSlot === "number" ? String(llmDefaultSlot) : "?"}</span> / active{" "}
                  <span className="mono">{llmActiveSlot ? String((llmActiveSlot as any).id) : "?"}</span>
                  {llmSlotPath ? <span className="mono"> · slots: {String(llmSlotPath)}</span> : null}
                  <br />
                  LLM_EXEC_SLOT: default <span className="mono">{typeof execDefaultSlot === "number" ? String(execDefaultSlot) : "?"}</span> / active{" "}
                  <span className="mono">{execActiveId !== null ? String(execActiveId) : "?"}</span>
                  {execSlotPath ? <span className="mono"> · exec slots: {execSlotPath}</span> : null}
                  <br />
                  router: <span className="mono">{llmRouterPath || "configs/llm_router.yaml"}</span>
                  {llmOverridesPath ? <span className="mono"> / overrides: {String(llmOverridesPath)}</span> : null}
                </div>
              </details>
            ) : null}
          </div>
        ) : null}

        {showDiagnostics ? (
          <div className="main-alert" style={{ margin: 0 }}>
          <div style={{ fontWeight: 950, marginBottom: 6 }}>実行モード（どこで動く？）</div>
          <div className="muted small-text" style={{ lineHeight: 1.65, marginBottom: 10 }}>
            優先順（概念）: <span className="mono">codex exec（許可taskのみ）</span> → <span className="mono">LLM API</span> →{" "}
            <span className="mono">THINK/AGENT（pending）</span>
            <br />
            固定ルール: Aテキスト本文を書き換える <span className="mono">script_*</span> は{" "}
            <span className="mono">codex exec</span> に回さない / APIが落ちたら停止。
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>mode</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>いまの状態</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>設定（コピー）</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>参照</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono" style={{ fontWeight: 900 }}>
                      exec slot
                    </span>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "grid", gap: 4 }}>
                      <div>
                        LLM_EXEC_SLOT=<span className="mono" style={{ fontWeight: 900 }}>{execActiveId !== null ? String(execActiveId) : "?"}</span>
                        {execActiveLabel ? <span className="muted small-text">（{execActiveLabel}）</span> : null}
                        {execActiveSource ? <span className="muted small-text">（source={execActiveSource}）</span> : null}
                      </div>
                      <div className="muted small-text">
                        mode=<span className="mono">{llmModeNow}</span> / codex override=
                        <span className="mono">
                          {typeof execCodexOverride === "boolean" ? (execCodexOverride ? "ON" : "OFF") : "—"}
                        </span>{" "}
                        / API failover=<span className="mono">{typeof execFailoverEffective === "boolean" ? (execFailoverEffective ? "ON" : "OFF") : llmFailoverToThink ? "ON" : "OFF"}</span>
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=0")}
                        style={{ padding: "6px 10px" }}
                      >
                        slot 0
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=1")}
                        style={{ padding: "6px 10px" }}
                      >
                        codex on
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=2")}
                        style={{ padding: "6px 10px" }}
                      >
                        codex off
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=3")}
                        style={{ padding: "6px 10px" }}
                      >
                        think
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=4")}
                        style={{ padding: "6px 10px" }}
                      >
                        agent
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_EXEC_SLOT=5")}
                        style={{ padding: "6px 10px" }}
                      >
                        failover off
                      </button>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono" style={{ overflowWrap: "anywhere" }}>
                      {execSlotPath || "configs/llm_exec_slots.yaml"}
                      {execLocalPath ? ` (+ ${execLocalPath})` : ""}
                    </span>
                  </td>
                </tr>

                <tr>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono" style={{ fontWeight: 900 }}>
                      codex exec
                    </span>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "grid", gap: 4 }}>
                      <div>
                        <span className="mono" style={{ fontWeight: 900 }}>
                          {codexEnabled ? "ON" : "OFF"}
                        </span>
                        {codexEnabledSource ? <span className="muted small-text">（{codexEnabledSource}）</span> : null}
                      </div>
                      <div className="muted small-text">
                        profile=<span className="mono">{codexProfileEffective || "—"}</span> / sandbox=
                        <span className="mono">{codexSandboxEffective || "—"}</span>
                        {codexModelEffective ? (
                          <>
                            {" "}
                            / model=<span className="mono">{codexModelEffective}</span>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export YTM_CODEX_EXEC_ENABLED=1")}
                        style={{ padding: "6px 10px" }}
                      >
                        enable
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export YTM_CODEX_EXEC_ENABLED=0")}
                        style={{ padding: "6px 10px" }}
                      >
                        disable
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export YTM_CODEX_EXEC_DISABLE=1")}
                        style={{ padding: "6px 10px" }}
                      >
                        emergency off
                      </button>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono" style={{ overflowWrap: "anywhere" }}>
                      {codexExec?.path || "configs/codex_exec.yaml"}
                    </span>
                  </td>
                </tr>

                <tr>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono" style={{ fontWeight: 900 }}>
                      THINK/AGENT
                    </span>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "grid", gap: 4 }}>
                      <div>
                        LLM_MODE=<span className="mono" style={{ fontWeight: 900 }}>{llmModeNow}</span>
                      </div>
                      <div className="muted small-text">
                        queue=<span className="mono">{String(agentMode?.queue_dir ?? "workspaces/logs/agent_tasks")}</span> / API failover=
                        <span className="mono">{llmFailoverToThink ? "ON" : "OFF"}</span>
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_MODE=think")}
                        style={{ padding: "6px 10px" }}
                      >
                        think on
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_MODE=api")}
                        style={{ padding: "6px 10px" }}
                      >
                        api on
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() => void copyToClipboard("export LLM_API_FAILOVER_TO_THINK=0")}
                        style={{ padding: "6px 10px" }}
                      >
                        failover off
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() =>
                          void copyToClipboard("./scripts/think.sh --all-text -- python -m script_pipeline.cli run-all --channel CH06 --video 033")
                        }
                        style={{ padding: "6px 10px" }}
                      >
                        run think.sh
                      </button>
                    </div>
                  </td>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <span className="mono">ssot/ops/OPS_ENV_VARS.md</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>codex exec 対象タスク（概要）</summary>
            <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
              <div className="muted small-text">
                include prefixes: <span className="mono">{normalizeStringArray(codexExec?.selection?.include_task_prefixes ?? []).join(", ") || "—"}</span>
              </div>
              <div className="muted small-text">
                include tasks: <span className="mono">{normalizeStringArray(codexExec?.selection?.include_tasks ?? []).join(", ") || "—"}</span>
              </div>
              <div className="muted small-text">
                exclude tasks（effective）:{" "}
                <span className="mono">{normalizeStringArray(codexExec?.effective?.exclude_tasks ?? []).length}</span>
              </div>
              {normalizeStringArray(codexExec?.effective?.exclude_tasks ?? []).length > 0 ? (
                <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
                  {normalizeStringArray(codexExec?.effective?.exclude_tasks ?? []).slice(0, 40).join(", ")}
                  {normalizeStringArray(codexExec?.effective?.exclude_tasks ?? []).length > 40 ? ", …" : ""}
                </div>
              ) : null}
            </div>
          </details>
          </div>
        ) : null}

        {showDiagnostics ? (
          <div className="main-alert" style={{ margin: 0 }}>
            <div style={{ fontWeight: 950, marginBottom: 6 }}>漏れチェック（自動）</div>
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
            <li>
              LLM task_defs: {(catalog?.llm?.used_tasks?.length ?? 0) || 0} / missing_task_defs:{" "}
              <span className={llmMissing.length > 0 ? "mono" : "mono muted"}>{String(llmMissing.length)}</span>
              {llmMissing.length > 0 ? <span className="muted small-text">（{llmMissing.join(", ")}）</span> : null}
            </li>
            <li>
              image task_defs: {(catalog?.image?.used_tasks?.length ?? 0) || 0} / missing_task_defs:{" "}
              <span className={imageMissing.length > 0 ? "mono" : "mono muted"}>{String(imageMissing.length)}</span>
              {imageMissing.length > 0 ? <span className="muted small-text">（{imageMissing.join(", ")}）</span> : null}
            </li>
            <li>
              未解決LLMコード: <span className={llmUnresolvedSelectors.length > 0 ? "mono" : "mono muted"}>{String(llmUnresolvedSelectors.length)}</span>
              {llmUnresolvedSelectors.length > 0 ? (
                <span className="muted small-text">（{llmUnresolvedSelectors.slice(0, 12).join(", ")}{llmUnresolvedSelectors.length > 12 ? ", …" : ""}）</span>
              ) : null}
            </li>
            <li>
              未解決image model_key:{" "}
              <span className={imageUnresolvedModelKeys.length > 0 ? "mono" : "mono muted"}>{String(imageUnresolvedModelKeys.length)}</span>
              {imageUnresolvedModelKeys.length > 0 ? (
                <span className="muted small-text">（{imageUnresolvedModelKeys.join(", ")}）</span>
              ) : null}
            </li>
            <li>
              台本（script_*）代表task:{" "}
              <span className={scriptPolicy.task ? "mono" : "mono muted"}>{scriptPolicy.task || "（未検出）"}</span>
            </li>
          </ul>
          </div>
        ) : null}

	        {showChannels ? (
	          <div style={{ overflowX: "auto" }}>
	          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
	            <div style={{ fontWeight: 950 }}>チャンネル別の3点（サムネ / 台本 / 動画内画像）</div>
	            <span className="muted small-text" style={{ lineHeight: 1.6 }}>
	              {!isDetail ? (
	                <>各チャンネルで見るのはこの3つだけ。コードや設定差分は「詳細」に切替すると出ます。</>
	              ) : (
	                <>
	                  形式（メモ用コード）: <span className="mono">thumb_script_video</span> + <span className="mono">@xN</span>（任意）
	                </>
	              )}
	            </span>
	            <Link to="/image-model-routing" className="workspace-button workspace-button--ghost workspace-button--compact" style={{ textDecoration: "none" }}>
	              画像設定を開く
	            </Link>
	            <input
	              value={channelQuery}
	              onChange={(e) => setChannelQuery(e.target.value)}
	              placeholder="CH/名前で検索…"
	              style={{
	                padding: "8px 10px",
	                borderRadius: 8,
	                border: "1px solid rgba(148,163,184,0.35)",
	                background: "rgba(15,23,42,0.35)",
	                color: "inherit",
	                minWidth: 180,
	              }}
	            />
	            {isDetail ? (
	              <label className="muted small-text" style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
	                <input type="checkbox" checked={showChannelDetails} onChange={(e) => setShowChannelDetails(e.target.checked)} />
	                設定差分/強制（env）も表示
	              </label>
	            ) : null}
	            {isDetail ? (
	              <label className="muted small-text" style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
	                <input type="checkbox" checked={includeExecSlotInCode} onChange={(e) => setIncludeExecSlotInCode(e.target.checked)} />
	                コードに <span className="mono">@xN</span> を付ける
	              </label>
	            ) : null}
	          </div>
	          <table style={{ width: "100%", minWidth: 1260, borderCollapse: "collapse" }}>
	            <thead>
	              <tr>
	                <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>CH</th>
	                {isDetail ? (
	                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>コード（メモ）</th>
	                ) : null}
	                <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>サムネ</th>
	                <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>台本</th>
	                <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>動画内画像</th>
	                <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>操作</th>
	              </tr>
	            </thead>
            <tbody>
              {filteredChannels.map((ch) => {
                const row = routing ? rowForChannel(routing.channels ?? [], ch) : null;

                const thumbConfiguredRaw = row?.thumbnail?.model_key ?? null;
                const thumbConfigured = canonicalizeImageCode(thumbConfiguredRaw, imageCanonicalById) || "";
                const thumbEffectiveRaw = (forcedThumb?.selector ?? forcedAny?.selector ?? null) || thumbConfiguredRaw;
                const thumbEffective = canonicalizeImageCode(thumbEffectiveRaw, imageCanonicalById) || "";

                const videoConfiguredRaw = row?.video_image?.model_key ?? null;
                const videoConfigured = canonicalizeImageCode(videoConfiguredRaw, imageCanonicalById) || "";
                const videoEffectiveRaw = (forcedVideo?.selector ?? forcedAny?.selector ?? null) || (videoConfiguredRaw || "img-flux-schnell-1");
                const videoEffective = canonicalizeImageCode(videoEffectiveRaw, imageCanonicalById) || "";

                const thumbConfigCode = thumbConfigured || (thumbConfiguredRaw ? String(thumbConfiguredRaw) : "");
                const thumbEffCode = thumbEffective || (thumbEffectiveRaw ? String(thumbEffectiveRaw) : "");

                const videoConfigCode = videoConfigured || (videoConfiguredRaw ? String(videoConfiguredRaw) : defaultVideoSelector);
                const videoEffCode = videoEffective || (videoEffectiveRaw ? String(videoEffectiveRaw) : defaultVideoSelector);

                const scriptCode = scriptPolicy.primary_code || "";
                const scriptEff = scriptCode || "?";

	                const bundleEffective = `${thumbEffCode || "?"}_${scriptEff}_${videoEffCode || "?"}`;
	                const bundleConfigured = `${thumbConfigCode || (thumbConfiguredRaw ? "?" : "?")}_${scriptEff}_${videoConfigCode || "?"}`;
	                const execSuffix = isDetail && includeExecSlotInCode ? `@x${execActiveId !== null ? String(execActiveId) : "?"}` : "";
	                const bundleEffectiveDisplay = `${bundleEffective}${execSuffix}`;
	                const bundleConfiguredDisplay = `${bundleConfigured}${execSuffix}`;
	
	                const thumbMeta = (imageSlotById as any)[thumbEffCode] as ImageSlotMeta | undefined;
	                const thumbLabel = String(thumbMeta?.label ?? "").trim() || (thumbEffCode || "未設定");
	                const thumbDescription = String(thumbMeta?.description ?? "").trim();
	                const thumbReal = resolveImageTaskModelText(thumbEffCode, "thumbnail_image_gen");
	
	                const videoMeta = (imageSlotById as any)[videoEffCode] as ImageSlotMeta | undefined;
	                const videoLabel = String(videoMeta?.label ?? "").trim() || (videoEffCode || "未設定");
	                const videoDescription = String(videoMeta?.description ?? "").trim();
	                const videoReal = resolveImageTaskModelText(videoEffCode, "visual_image_gen");
	
	                const scriptInfo = resolveLlmSelectorInfo(scriptEff, llmCodeToModelKey, llmModelRegistry);
	                const scriptLabel = (llmCodeToLabel[scriptEff] ?? "").trim() || scriptInfo.resolvedText || scriptEff;
	
	                const thumbConfiguredLine =
	                  thumbConfigCode && thumbConfigCode !== thumbEffCode ? `config: ${thumbConfigCode}` : "";
	                const videoConfiguredLine =
	                  videoConfigCode && videoConfigCode !== videoEffCode ? `config: ${videoConfigCode}` : "";

                const scriptMore = scriptPolicy.codes.length > 1 ? scriptPolicy.codes.slice(1).join(", ") : "";
	                return (
	                  <tr key={`bundle-${ch}`}>
	                    <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                      <div style={{ display: "grid", gap: 4 }}>
	                        <span className="mono" style={{ fontWeight: 900 }}>
	                          {ch}
	                        </span>
	                        <span className="muted small-text">{channelNameFromList(channelSummaries, ch)}</span>
	                      </div>
	                    </td>
	                    {isDetail ? (
	                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                        <div style={{ display: "grid", gap: 6 }}>
	                          <div style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
	                            <div className="mono" style={{ fontWeight: 900, overflowWrap: "anywhere" }}>
	                              {bundleEffectiveDisplay}
	                            </div>
	                            <button
	                              type="button"
	                              className="workspace-button workspace-button--ghost workspace-button--compact"
	                              onClick={() => void copyToClipboard(bundleEffectiveDisplay)}
	                              style={{ whiteSpace: "nowrap" }}
	                            >
	                              コードをコピー
	                            </button>
	                          </div>
	                          {showChannelDetails && bundleConfiguredDisplay !== bundleEffectiveDisplay ? (
	                            <div className="mono muted small-text" style={{ overflowWrap: "anywhere" }}>
	                              config: {bundleConfiguredDisplay}
	                            </div>
	                          ) : null}
	                        </div>
	                      </td>
	                    ) : null}
	                    <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                      <div style={{ display: "grid", gap: 6 }}>
	                        <div style={{ fontWeight: 900 }}>{thumbLabel || "未設定"}</div>
	                        {thumbDescription ? <div className="muted small-text">{thumbDescription}</div> : null}
	                        {isDetail && thumbReal ? (
	                          <div className="muted small-text">
	                            実モデル: <span className="mono">{thumbReal}</span>
	                          </div>
	                        ) : null}
	                        {isDetail ? (
	                          <div className="muted small-text">
	                            code: <span className="mono">{thumbEffCode || "?"}</span>
	                          </div>
	                        ) : null}
	                        {isDetail && showChannelDetails ? (
	                          <>
	                            {forcedThumb?.selector ? (
	                              <div className="mono muted small-text">
                                env: {forcedThumb.env}={String(forcedThumb.selector)}
                              </div>
                            ) : null}
                            {!forcedThumb?.selector && forcedAny?.selector ? (
                              <div className="mono muted small-text">
                                env: {forcedAny.env}={String(forcedAny.selector)}
                              </div>
                            ) : null}
	                            {thumbConfiguredLine ? <div className="mono muted small-text">{thumbConfiguredLine}</div> : null}
	                            {!thumbConfigCode && !thumbConfiguredRaw ? (
	                              <div className="mono muted small-text">config: ?（templates.json未初期化） / default: {defaultThumbSelector}</div>
	                            ) : null}
	                          </>
	                        ) : null}
	                      </div>
	                    </td>
	                    <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                      <div style={{ display: "grid", gap: 6 }}>
	                        <div style={{ fontWeight: 900 }}>{scriptLabel || "未設定"}</div>
	                        {scriptInfo.resolvedText ? <div className="muted small-text">{scriptInfo.resolvedText}</div> : null}
	                        {isDetail ? (
	                          <div className="muted small-text">
	                            code: <span className="mono">{scriptEff}</span>
	                          </div>
	                        ) : null}
	                        {isDetail && showChannelDetails && scriptMore ? (
	                          <div className="mono muted small-text">fallback: {scriptMore}</div>
	                        ) : null}
	                      </div>
	                    </td>
	                    <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                      <div style={{ display: "grid", gap: 6 }}>
	                        <div style={{ fontWeight: 900 }}>{videoLabel || "未設定"}</div>
	                        {videoDescription ? <div className="muted small-text">{videoDescription}</div> : null}
	                        {isDetail && videoReal ? (
	                          <div className="muted small-text">
	                            実モデル: <span className="mono">{videoReal}</span>
	                          </div>
	                        ) : null}
	                        {isDetail ? (
	                          <div className="muted small-text">
	                            code: <span className="mono">{videoEffCode || "?"}</span>
	                          </div>
	                        ) : null}
	                        {isDetail && showChannelDetails ? (
	                          <>
	                            {forcedVideo?.selector ? (
	                              <div className="mono muted small-text">
                                env: {forcedVideo.env}={String(forcedVideo.selector)}
                              </div>
                            ) : null}
                            {!forcedVideo?.selector && forcedAny?.selector ? (
                              <div className="mono muted small-text">
                                env: {forcedAny.env}={String(forcedAny.selector)}
                              </div>
                            ) : null}
	                            {videoConfiguredLine ? <div className="mono muted small-text">{videoConfiguredLine}</div> : null}
	                            {!videoConfiguredRaw ? (
	                              <div className="mono muted small-text">config: tier default / default: {defaultVideoSelector}</div>
	                            ) : null}
	                          </>
	                        ) : null}
	                      </div>
	                    </td>
	                    <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
	                      <Link to="/image-model-routing" className="workspace-button workspace-button--ghost workspace-button--compact" style={{ textDecoration: "none" }}>
	                        画像設定
	                      </Link>
	                    </td>
                  </tr>
                );
              })}
	            </tbody>
	          </table>
	        </div>

	        ) : null}

	        {showImages ? (
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontWeight: 950 }}>動画用画像の要件（CH別）/ 現在の設定</summary>
          <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
            目的: 「このチャンネルは画質要件が高いか？」と「いま実際にどのコードで回っているか？」を同時に見える化。
          </div>
          <div style={{ overflowX: "auto", marginTop: 10 }}>
            <table style={{ width: "100%", minWidth: 1180, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>CH</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>チャンネル名</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>動画用画像（要件）</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>いま通る前提</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>現在の設定（動画内画像）</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>現在の設定（サムネ）</th>
                </tr>
              </thead>
              <tbody>
                {filteredChannels.map((ch) => {
                  const name = channelNameFromList(channelSummaries, ch);
                  const policy = resolveVideoImagePolicy(ch);
                  const nowAssumption = resolvePolicyNowAssumption(ch);
                  const row = routing ? rowForChannel(routing.channels ?? [], ch) : null;
                  const videoSel = row?.video_image;
                  const thumbSel = row?.thumbnail;

                  const videoLabel = videoSel ? selectionLabel(videoSel) : "（未取得）";
                  const thumbLabel = thumbSel ? selectionLabel(thumbSel) : "（未取得）";
                  const videoSource = videoSel ? normalizeKey(videoSel.source) : "";
                  const thumbSource = thumbSel ? normalizeKey(thumbSel.source) : "";
                  const videoNote = videoSel?.note ? normalizeKey(videoSel.note) : "";
                  const thumbNote = thumbSel?.note ? normalizeKey(thumbSel.note) : "";

                  return (
                    <tr key={ch}>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span className="mono" style={{ fontWeight: 800 }}>
                          {ch}
                        </span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span style={{ opacity: 0.9 }}>{name}</span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <div style={{ display: "grid", gap: 4 }}>
                          <span style={{ fontWeight: 700 }}>{policy.requirement}</span>
                          <span className="muted small-text">（SRT→images / visual_image_gen）</span>
                        </div>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span style={{ fontWeight: 700 }}>{nowAssumption}</span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <div style={{ display: "grid", gap: 6 }}>
                          <div className="mono" style={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                            {videoLabel}
                          </div>
                          <div className="muted small-text" style={{ overflowWrap: "anywhere" }}>
                            source: <span className="mono">{videoSource || "?"}</span>
                            {videoNote ? <span> · {videoNote}</span> : null}
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <div style={{ display: "grid", gap: 6 }}>
                          <div className="mono" style={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                            {thumbLabel}
                          </div>
                          <div className="muted small-text" style={{ overflowWrap: "anywhere" }}>
                            source: <span className="mono">{thumbSource || "?"}</span>
                            {thumbNote ? <span> · {thumbNote}</span> : null}
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          </details>
        ) : null}

        {showScripts ? (
          <div className="main-alert" style={{ margin: 0 }}>
            <div style={{ fontWeight: 950, marginBottom: 6 }}>台本モデル（script_*）</div>
            <div style={{ display: "grid", gap: 8, lineHeight: 1.7 }}>
              <div>
                primary:{" "}
                <span className="mono" style={{ fontWeight: 900 }}>
                  {scriptPolicy.primary_code || "?"}
                </span>
                {scriptPolicy.primary_provider || scriptPolicy.primary_model ? (
                  <span className="muted">
                    {" "}
                    （{scriptPolicy.primary_provider || "?"}
                    {scriptPolicy.primary_model ? ` / ${scriptPolicy.primary_model}` : ""}
                    {scriptPolicy.primary_deployment ? ` / ${scriptPolicy.primary_deployment}` : ""}）
                  </span>
                ) : null}
              </div>
              {scriptPolicy.codes.length > 1 ? (
                <div className="muted small-text">
                  fallback: <span className="mono">{scriptPolicy.codes.slice(1).join(" → ")}</span>
                </div>
              ) : null}
              <div className="muted small-text">
                固定ルール: <span className="mono">script_*</span> は <span className="mono">codex exec</span> に回さない / APIが落ちたら停止（THINKへは行かない）
              </div>
              <div className="muted small-text">
                切替は <span className="mono">LLM_MODEL_SLOT</span>（数値）で行う（モデル名は書き換えない）
              </div>
            </div>
          </div>
        ) : null}

        {showScripts || showTasks ? (
          <div className="main-alert" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
              <div style={{ fontWeight: 950 }}>
                {showScripts ? "台本のLLM処理（script_*）: task → tier → model" : "その他のLLM処理（共通）: task → tier → model"}
              </div>
              <input
                value={taskQuery}
                onChange={(e) => setTaskQuery(e.target.value)}
                placeholder="task/用途/tier/modelで検索…"
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid rgba(148,163,184,0.35)",
                  background: "rgba(15,23,42,0.18)",
                  color: "inherit",
                  minWidth: 280,
                }}
              />
            </div>
            {showScripts ? (
              <div className="muted small-text" style={{ marginTop: 6, lineHeight: 1.6 }}>
                台本の中身（Aテキスト本文）を書き換える処理は <span className="mono">script_*</span> に集約。失敗時に勝手に別モデルへは行かない（停止して復旧）。
              </div>
            ) : (
              <div className="muted small-text" style={{ marginTop: 6, lineHeight: 1.6 }}>
                例: <span className="mono">belt_generation</span>（Bテキスト）や <span className="mono">visual_prompt_refine</span>{" "}
                （画像プロンプト整形）は、チャンネル別ではなく共通タスクです。使うモデルは{" "}
                <span className="mono">tier</span> と <span className="mono">LLM_MODEL_SLOT</span> で決まります。
              </div>
            )}
            <div style={{ marginTop: 10, overflowX: "auto" }}>
              <table style={{ width: "100%", minWidth: 1240, borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>カテゴリ</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>task</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>用途</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>tier</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>実行</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>model（code）</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>provider / model</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>決まり方</th>
                  </tr>
                </thead>
                <tbody>
                  {(showScripts ? scriptTaskRows : commonTaskRows).map((r) => (
                    <tr key={`task-${r.task}`}>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span style={{ fontWeight: 800 }}>{CATEGORY_LABELS[r.category] ?? r.category}</span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <div style={{ display: "grid", gap: 4 }}>
                          <span className="mono" style={{ fontWeight: 900 }}>
                            {r.task}
                          </span>
                          {r.label && r.label !== r.task ? <span className="muted small-text">{r.label}</span> : null}
                        </div>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)", minWidth: 260 }}>
                        <span style={{ opacity: 0.9 }}>{r.purpose || "—"}</span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span className="mono" style={{ fontWeight: 800 }}>
                          {r.tier || "—"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)", minWidth: 160 }}>
                        <span className="mono" style={{ fontWeight: 800 }}>
                          {r.execPath || "—"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)", minWidth: 280 }}>
                        <span className="mono" style={{ fontWeight: 900, overflowWrap: "anywhere" }}>
                          {r.modelChain || "—"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)", minWidth: 260 }}>
                        <span className="mono" style={{ overflowWrap: "anywhere" }}>
                          {r.resolvedText || "—"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                        <span className="muted small-text">{r.sourceText || "—"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {showImages ? (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 900 }}>画像モデルコード（image_model_slots.yaml）</summary>
          <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
            基本は short code（<span className="mono">g-1 / f-1 / f-3 / f-4</span>）。alias（<span className="mono">img-*</span>）は読みやすい名前用です。
          </div>

          <div style={{ marginTop: 10, overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>code</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>label / description</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>
                    tasks（task=real model_key）
                  </th>
                </tr>
              </thead>
              <tbody>
                {imageSlots
                  .slice()
                  .filter((s) => {
                    const id = String((s as any)?.id ?? "").trim();
                    if (!id) return false;
                    return canonicalizeImageCode(id, imageCanonicalById) === id;
                  })
                  .sort((a, b) => String((a as any)?.id ?? "").localeCompare(String((b as any)?.id ?? "")))
                  .map((s) => {
                    const id = String((s as any)?.id ?? "").trim();
                    const label = String((s as any)?.label ?? "").trim();
                    const desc = String((s as any)?.description ?? "").trim();
                    const tasks =
                      (s as any)?.tasks && typeof (s as any).tasks === "object" && !Array.isArray((s as any).tasks)
                        ? ((s as any).tasks as Record<string, unknown>)
                        : null;
                    const taskKeys = tasks ? Object.keys(tasks).sort((a, b) => a.localeCompare(b)) : [];
                    return (
                      <tr key={`img-code-${id}`}>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono" style={{ fontWeight: 900 }}>
                            {id}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <div style={{ display: "grid", gap: 4 }}>
                            {label ? <span style={{ fontWeight: 800 }}>{label}</span> : <span className="muted">—</span>}
                            {desc ? <span className="muted small-text">{desc}</span> : null}
                          </div>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          {taskKeys.length > 0 ? (
                            <div style={{ display: "grid", gap: 4 }}>
                              {taskKeys.map((k) => {
                                const mk = String(tasks?.[k] ?? "").trim();
                                const resolved = mk ? resolveImageModelKeyText(mk, imageModelRegistry) : "";
                                return (
                                  <div key={`img-code-${id}-task-${k}`} className="mono" style={{ overflowWrap: "anywhere" }}>
                                    {k}={mk || "?"}
                                    {resolved ? <span className="muted">（{resolved}）</span> : null}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <span className="mono muted">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>

          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>alias（参考）</summary>
            <div style={{ marginTop: 10, overflowX: "auto" }}>
              <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>alias</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>canonical</th>
                    <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>label</th>
                  </tr>
                </thead>
                <tbody>
                  {imageSlots
                    .slice()
                    .filter((s) => {
                      const id = String((s as any)?.id ?? "").trim();
                      if (!id) return false;
                      return canonicalizeImageCode(id, imageCanonicalById) !== id;
                    })
                    .sort((a, b) => String((a as any)?.id ?? "").localeCompare(String((b as any)?.id ?? "")))
                    .map((s) => {
                      const id = String((s as any)?.id ?? "").trim();
                      const canonical = canonicalizeImageCode(id, imageCanonicalById);
                      const label = String((s as any)?.label ?? "").trim();
                      return (
                        <tr key={`img-alias-${id}`}>
                          <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                            <span className="mono" style={{ fontWeight: 800 }}>
                              {id}
                            </span>
                          </td>
                          <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                            <span className="mono" style={{ fontWeight: 800 }}>
                              {canonical || "?"}
                            </span>
                          </td>
                          <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>{label || "—"}</td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </details>
          </details>
        ) : null}

        {showDiagnostics ? (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 900 }}>実行スロット（llm_exec_slots.yaml）</summary>
          <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
            <span className="mono">LLM_EXEC_SLOT</span> は「どこで動くか（api / think / agent / codex exec / failover）」を数字で切替えるレバーです。
            明示の <span className="mono">LLM_MODE</span> / <span className="mono">YTM_CODEX_EXEC_ENABLED</span> などがある場合はそれが優先されます。
          </div>

          <div style={{ marginTop: 10 }} className="main-alert">
            <div style={{ fontWeight: 900, marginBottom: 6 }}>現在のslot</div>
            <div className="mono" style={{ fontWeight: 900, marginBottom: 6 }}>
              slot {execActiveId !== null ? String(execActiveId) : "?"}
              {execActiveLabel ? ` (${execActiveLabel})` : ""}
            </div>
            <div className="muted small-text" style={{ lineHeight: 1.7 }}>
              mode=<span className="mono">{llmModeNow}</span> / API failover=<span className="mono">{llmFailoverToThink ? "ON" : "OFF"}</span> / codex exec=
              <span className="mono">{codexEnabled ? "ON" : "OFF"}</span>
            </div>
          </div>

          <div style={{ marginTop: 10, overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>slot</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>label</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>effect</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>description</th>
                </tr>
              </thead>
              <tbody>
                {((execSlots as any)?.slots ?? [])
                  .slice()
                  .sort((a: any, b: any) => Number(a?.id ?? 0) - Number(b?.id ?? 0))
                  .map((s: any) => {
                    const id = typeof s?.id === "number" ? s.id : null;
                    const label = String(s?.label ?? "").trim();
                    const desc = String(s?.description ?? "").trim();
                    const llmMode = String(s?.llm_mode ?? "").trim();
                    const codexOverride = s?.codex_exec_enabled;
                    const apiFailover = s?.api_failover_to_think;
                    const effectParts: string[] = [];
                    if (llmMode) effectParts.push(`mode=${llmMode}`);
                    if (typeof codexOverride === "boolean") effectParts.push(`codex=${codexOverride ? "ON" : "OFF"}`);
                    if (typeof apiFailover === "boolean") effectParts.push(`failover=${apiFailover ? "ON" : "OFF"}`);
                    const effect = effectParts.length > 0 ? effectParts.join(" / ") : "—";
                    return (
                      <tr key={`exec-slot-${String(id ?? "x")}`}>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono" style={{ fontWeight: 900 }}>
                            {id !== null ? String(id) : "?"}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>{label || "—"}</td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono">{effect}</span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>{desc || "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
	            </table>
	          </div>

	          <div className="muted small-text" style={{ marginTop: 10 }}>
	            config: <span className="mono">{execSlotPath || "configs/llm_exec_slots.yaml"}</span>
	            {execLocalPath ? <span className="mono"> / local: {execLocalPath}</span> : null}
	          </div>
          </details>
        ) : null}

        {showScripts ? (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 900 }}>LLMスロット（llm_model_slots.yaml）</summary>
          <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
            <span className="mono">LLM_MODEL_SLOT</span> は「tierごとの代表モデル」を数字で切替えるレバーです（モデル名を書き換えない）。
          </div>

          <div style={{ marginTop: 10 }} className="main-alert">
            <div style={{ fontWeight: 900, marginBottom: 6 }}>現在のslot</div>
            {llmActiveSlotEntry ? (
              <>
                <div className="mono" style={{ fontWeight: 900, marginBottom: 6 }}>
                  slot {(llmActiveSlotEntry as any).id}
                  {(llmActiveSlotEntry as any).label ? ` (${String((llmActiveSlotEntry as any).label)})` : ""}
                </div>
                {(llmActiveSlotEntry as any).description ? (
                  <div className="muted small-text" style={{ marginBottom: 10 }}>
                    {String((llmActiveSlotEntry as any).description)}
                  </div>
                ) : null}
                <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
                  {Object.entries(normalizeTierMap((llmActiveSlotEntry as any).tiers ?? null))
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([tier, codes]) => (
                      <li key={`slot-active-${tier}`}>
                        <span className="mono" style={{ fontWeight: 800 }}>
                          {tier}
                        </span>
                        :{" "}
                        <span className="mono">
                          {codes
                            .map((c) => {
                              const info = resolveLlmSelectorInfo(c, llmCodeToModelKey, llmModelRegistry);
                              return info.resolvedText ? `${info.selector}（${info.resolvedText}）` : info.selector;
                            })
                            .join(" → ")}
                        </span>
                      </li>
                    ))}
                  {Object.keys(normalizeTierMap((llmActiveSlotEntry as any).script_tiers ?? null)).length > 0 ? (
                    <li>
                      <span className="mono" style={{ fontWeight: 800 }}>
                        script_tiers
                      </span>
                      :{" "}
                      <span className="mono">
                        {Object.entries(normalizeTierMap((llmActiveSlotEntry as any).script_tiers ?? null))
                          .sort(([a], [b]) => a.localeCompare(b))
                          .map(([tier, codes]) => {
                            const chain = codes
                              .map((c) => {
                                const info = resolveLlmSelectorInfo(c, llmCodeToModelKey, llmModelRegistry);
                                return info.resolvedText ? `${info.selector}（${info.resolvedText}）` : info.selector;
                              })
                              .join(" → ");
                            return `${tier}: ${chain}`;
                          })
                          .join(" / ")}
                      </span>
                      {" "}
                      <span className="muted small-text">
                        （script_allow_openrouter={String(Boolean((llmActiveSlotEntry as any).script_allow_openrouter))}）
                      </span>
                    </li>
                  ) : null}
                </ul>
              </>
            ) : (
              <div className="mono muted">（未取得）</div>
            )}
          </div>

          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>全スロット一覧</summary>
            <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
              {(llmSlots as any[]).map((s) => {
                const tiers = normalizeTierMap((s as any)?.tiers ?? null);
                const scriptTiers = normalizeTierMap((s as any)?.script_tiers ?? null);
                const isActive = (llmActiveSlotEntry as any)?.id === (s as any)?.id;
                return (
                  <div key={`slot-${String((s as any)?.id ?? "")}`} className="main-alert" style={{ margin: 0 }}>
                    <div className="mono" style={{ fontWeight: 900, marginBottom: 6 }}>
                      slot {String((s as any)?.id ?? "?")}
                      {(s as any)?.label ? ` (${String((s as any).label)})` : ""}
                      {isActive ? <span className="muted">（active）</span> : null}
                    </div>
                    {(s as any)?.description ? (
                      <div className="muted small-text" style={{ marginBottom: 10 }}>
                        {String((s as any).description)}
                      </div>
                    ) : null}
                    <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
                      {Object.entries(tiers)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([tier, codes]) => (
                          <li key={`slot-${String((s as any)?.id ?? "")}-${tier}`}>
                            <span className="mono" style={{ fontWeight: 800 }}>
                              {tier}
                            </span>
                            :{" "}
                            <span className="mono">
                              {codes
                                .map((c) => {
                                  const info = resolveLlmSelectorInfo(c, llmCodeToModelKey, llmModelRegistry);
                                  return info.resolvedText ? `${info.selector}（${info.resolvedText}）` : info.selector;
                                })
                                .join(" → ")}
                            </span>
                          </li>
                        ))}
                      {Object.keys(scriptTiers).length > 0 ? (
                        <li>
                          <span className="mono" style={{ fontWeight: 800 }}>
                            script_tiers
                          </span>
                          :{" "}
                          <span className="mono">
                            {Object.entries(scriptTiers)
                              .sort(([a], [b]) => a.localeCompare(b))
                              .map(([tier, codes]) => {
                                const chain = codes
                                  .map((c) => {
                                    const info = resolveLlmSelectorInfo(c, llmCodeToModelKey, llmModelRegistry);
                                    return info.resolvedText ? `${info.selector}（${info.resolvedText}）` : info.selector;
                                  })
                                  .join(" → ");
                                return `${tier}: ${chain}`;
                              })
                              .join(" / ")}
                          </span>
                          {" "}
                          <span className="muted small-text">
                            （script_allow_openrouter={String(Boolean((s as any)?.script_allow_openrouter))}）
                          </span>
                        </li>
                      ) : null}
                    </ul>
                  </div>
                );
              })}
            </div>
          </details>
          </details>
        ) : null}

        {showScripts ? (
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 900 }}>LLMコード辞書（llm_model_codes.yaml）</summary>
          <div className="muted small-text" style={{ marginTop: 8, lineHeight: 1.6 }}>
            ここにある <span className="mono">code</span> を slot / task override に書く（モデル名は書かない）。
          </div>
          <div style={{ marginTop: 10, overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>code</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>model_key</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>provider / model</th>
                  <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>label</th>
                </tr>
              </thead>
              <tbody>
                {(catalog?.llm?.model_codes?.codes ?? [])
                  .slice()
                  .sort((a, b) => String((a as any)?.code ?? "").localeCompare(String((b as any)?.code ?? "")))
                  .map((c) => {
                    const code = String((c as any)?.code ?? "").trim();
                    const modelKey = String((c as any)?.model_key ?? "").trim();
                    const label = String((c as any)?.label ?? "").trim();
                    const meta = modelKey ? (llmModelRegistry as any)[modelKey] : null;
                    const providerModel = meta ? formatResolvedModel(meta.provider ?? "", meta.model_name ?? "", meta.deployment ?? "") : "";
                    return (
                      <tr key={`llm-code-${code}`}>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono" style={{ fontWeight: 900 }}>
                            {code || "?"}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono" style={{ overflowWrap: "anywhere" }}>
                            {modelKey || "—"}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                          <span className="mono" style={{ overflowWrap: "anywhere" }}>
                            {providerModel || "—"}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>{label || "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
          </details>
        ) : null}
      </div>
    </section>
  );
}
