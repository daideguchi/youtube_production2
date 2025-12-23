/* eslint-disable @typescript-eslint/no-unused-vars */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  LlmArtifactListItem,
  LlmTextArtifact,
  SrtVerifyResponse,
  TtsReplaceResponse,
  TtsSaveResponse,
  TtsValidationIssue,
  TtsValidationResponse,
  VideoDetail,
} from "../api/types";
import {
  API_BASE_URL,
  enhanceTts,
  fetchLlmArtifact,
  fetchPlainTtsScript,
  fetchHumanScripts,
  listLlmArtifacts,
  updateLlmArtifact,
  updateHumanScripts,
  fetchAText,
  updateVideoRedo,
} from "../api/client";
import { STAGE_ORDER, translateStage, translateStatus } from "../utils/i18n";
import { AudioWorkspace } from "./AudioWorkspace";
import { StageProgress } from "./StageProgress";

const STAGE_STATUS_OPTIONS = [
  { value: "pending", label: "未着手" },
  { value: "in_progress", label: "進行中" },
  { value: "blocked", label: "要対応" },
  { value: "review", label: "レビュー待ち" },
  { value: "completed", label: "完了" },
];

const DEFAULT_AI_CHECK_INSTRUCTION = `YouTube向けナレーション台本として適切かを次の観点で評価してください。\n- 冒頭の引き込み力\n- 構成と論理展開の明瞭さ\n- 表現の自然さと語尾・敬体の統一\n- 情緒とテンポ（冗長さや重複の有無）\n\n50〜120文字程度の要約と、改善の優先提案を3点以内で日本語で示してください。`;

export type DetailTab = "overview" | "script" | "audio" | "video" | "history";
type DetailMode = "diff";

type DetailTabTone = "info" | "warning" | "danger" | "success" | undefined;

type DetailTabItem = {
  key: DetailTab;
  label: string;
  badge?: string | null;
  tone?: DetailTabTone;
  hint?: string | null;
};

type ValidationStatus = "idle" | "running" | "success" | "warning" | "error";

type AudioHistoryEntry = {
  event?: string | null;
  status?: string | null;
  message?: string | null;
  timestamp?: string | null;
  final_wav?: string | null;
  final_srt?: string | null;
  log_json?: string | null;
  log_text?: string | null;
};

interface VideoDetailPanelProps {
  detail: VideoDetail;
  onSaveAssembled: (content: string) => Promise<unknown>;
  onSaveTts: (request: {
    plainContent?: string;
    taggedContent?: string;
    mode: "plain" | "tagged";
    regenerateAudio: boolean;
    updateAssembled: boolean;
  }) => Promise<TtsSaveResponse>;
  onValidateTts: (content: string) => Promise<TtsValidationResponse>;
  onSaveSrt: (content: string) => Promise<unknown>;
  onVerifySrt: (toleranceMs?: number) => Promise<SrtVerifyResponse>;
  onUpdateStatus: (status: string) => Promise<unknown>;
  onUpdateReady: (ready: boolean) => Promise<unknown>;
  onUpdateStages: (stages: Record<string, string>) => Promise<unknown>;
  onReplaceTts: (request: {
    original: string;
    replacement: string;
    scope: "first" | "all";
    updateAssembled: boolean;
    regenerateAudio: boolean;
  }) => Promise<TtsReplaceResponse>;
  refreshing: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  activeTab?: DetailTab;
  onTabChange?: (tab: DetailTab) => void;
  mode?: DetailMode;
}

function stripPauseSeparators(raw: string): string {
  const normalized = (raw ?? "").replace(/\r\n?/g, "\n");
  const filtered = normalized
    .split("\n")
    .filter((line) => line.trim() !== "---")
    .join("\n");
  return filtered.replace(/\n{3,}/g, "\n\n").trim();
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_error) {
      // Fall back below (some browsers expose navigator.clipboard but deny access)
    }
  }
  if (typeof document === "undefined") {
    throw new Error("clipboard is not available");
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) {
    throw new Error("copy failed");
  }
}

export function VideoDetailPanel({
  detail,
  onSaveAssembled: _onSaveAssembled,
  onSaveTts: _onSaveTts,
  onValidateTts,
  onSaveSrt,
  onVerifySrt,
  onUpdateStatus,
  onUpdateReady,
  onUpdateStages,
  onReplaceTts,
  refreshing,
  onDirtyChange,
  activeTab: activeTabProp,
  onTabChange,
}: VideoDetailPanelProps) {
  const [assembledAiContent, setAssembledAiContent] = useState(detail.assembled_content ?? "");
  const [assembledDraft, setAssembledDraft] = useState(
    detail.assembled_human_content ?? detail.assembled_content ?? ""
  );
  const [assembledBase, setAssembledBase] = useState(
    detail.assembled_human_content ?? detail.assembled_content ?? ""
  );

  const [ttsAiContent, setTtsAiContent] = useState(
    detail.script_audio_content ?? detail.tts_plain_content ?? detail.tts_content ?? ""
  );
const [ttsDraft, setTtsDraft] = useState(
  detail.script_audio_human_content ??
    detail.script_audio_content ??
    detail.tts_plain_content ??
    detail.tts_content ??
    ""
);
const [ttsBase, setTtsBase] = useState(
  detail.script_audio_human_content ??
    detail.script_audio_content ??
    detail.tts_plain_content ??
    detail.tts_content ??
    ""
);
const [aTextModalOpen, setATextModalOpen] = useState(false);
const [aTextModalContent, setATextModalContent] = useState<string>("");
const [aTextModalLoading, setATextModalLoading] = useState(false);
const [aTextModalError, setATextModalError] = useState<string | null>(null);
const [llmBoxesOpen, setLlmBoxesOpen] = useState(false);
const [llmArtifacts, setLlmArtifacts] = useState<LlmArtifactListItem[]>([]);
const [llmArtifactsLoading, setLlmArtifactsLoading] = useState(false);
const [llmArtifactsError, setLlmArtifactsError] = useState<string | null>(null);
const [llmEditorOpen, setLlmEditorOpen] = useState(false);
const [llmEditorName, setLlmEditorName] = useState<string | null>(null);
const [llmEditorLoading, setLlmEditorLoading] = useState(false);
const [llmEditorSaving, setLlmEditorSaving] = useState(false);
const [llmEditorError, setLlmEditorError] = useState<string | null>(null);
const [llmEditorArtifact, setLlmEditorArtifact] = useState<LlmTextArtifact | null>(null);
const [llmEditorStatus, setLlmEditorStatus] = useState<"pending" | "ready">("pending");
const [llmEditorApplyOutput, setLlmEditorApplyOutput] = useState(true);
const [llmEditorContent, setLlmEditorContent] = useState<string>("");

  // 音声タブの操作を常時有効にするため、人手チェックフラグは常に true で扱う
  const [audioReviewed, setAudioReviewed] = useState<boolean>(true);
  const [audioReviewedBase, setAudioReviewedBase] = useState<boolean>(true);


  const [audioScriptUpdatedAt, setAudioScriptUpdatedAt] = useState<string | null>(detail.audio_updated_at ?? null);
  const [audioScriptLoading, setAudioScriptLoading] = useState(false);
  const [audioScriptError, setAudioScriptError] = useState<string | null>(null);
  const [statusDraft, setStatusDraft] = useState(detail.status ?? "");
  const [readyDraft, setReadyDraft] = useState(detail.ready_for_audio);
  const [stageDrafts, setStageDrafts] = useState<Record<string, string>>(detail.stages ?? {});
  const [redoScript, setRedoScript] = useState(detail.redo_script ?? true);
  const [redoAudio, setRedoAudio] = useState(detail.redo_audio ?? true);
  const [redoNote, setRedoNote] = useState(detail.redo_note ?? "");
  const [redoSaving, setRedoSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [ttsValidation, setTtsValidation] = useState<TtsValidationResponse | null>(null);
  const [ttsValidationError, setTtsValidationError] = useState<string | null>(null);
  const [ttsValidating, setTtsValidating] = useState(false);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "error" | "unsupported">("idle");
  const [copyAssembledNoSepStatus, setCopyAssembledNoSepStatus] = useState<"idle" | "copied" | "error">("idle");
  const [copyTtsNoSepStatus, setCopyTtsNoSepStatus] = useState<"idle" | "copied" | "error">("idle");
  const [copyATextNoSepStatus, setCopyATextNoSepStatus] = useState<"idle" | "copied" | "error">("idle");
const [aiInstruction, setAiInstruction] = useState(DEFAULT_AI_CHECK_INSTRUCTION);
const [aiBusy, setAiBusy] = useState(false);
const [aiResult, setAiResult] = useState<string | null>(null);
const [aiError, setAiError] = useState<string | null>(null);
const [aiCopyStatus, setAiCopyStatus] = useState<"idle" | "copied" | "error" | "unsupported">("idle");
const [validationStatus, setValidationStatus] = useState<ValidationStatus>("idle");
const [activeTabInternal, setActiveTabInternal] = useState<DetailTab>(activeTabProp ?? "script");
const [showAudioHistory, setShowAudioHistory] = useState(false);
const [showStageDetails, setShowStageDetails] = useState(false);
const [humanLoading, setHumanLoading] = useState(false);
const [humanError, setHumanError] = useState<string | null>(null);
const [copyDescStatus, setCopyDescStatus] = useState<"idle" | "copied" | "error">("idle");
  const warningMessages = useMemo(() => detail.warnings?.filter(Boolean) ?? [], [detail.warnings]);
  useEffect(() => {
    setRedoScript(detail.redo_script ?? true);
    setRedoAudio(detail.redo_audio ?? true);
    setRedoNote(detail.redo_note ?? "");
  }, [detail.redo_script, detail.redo_audio, detail.redo_note]);
const SHOW_AI_SECTION = false; // AI生成版は非表示
const openATextModal = useCallback(async () => {
  setATextModalOpen(true);
  setATextModalContent("");
  setATextModalError(null);
  setATextModalLoading(true);
  try {
    const text = await fetchAText(detail.channel, detail.video);
    setATextModalContent(text);
  } catch (err) {
    setATextModalError(err instanceof Error ? err.message : String(err));
  } finally {
    setATextModalLoading(false);
  }
}, [detail.channel, detail.video]);

const refreshLlmArtifacts = useCallback(async () => {
  setLlmArtifactsLoading(true);
  setLlmArtifactsError(null);
  try {
    const items = await listLlmArtifacts(detail.channel, detail.video);
    setLlmArtifacts(items);
    if (items.some((item) => item.status === "pending")) {
      setLlmBoxesOpen(true);
    }
  } catch (err) {
    setLlmArtifactsError(err instanceof Error ? err.message : String(err));
  } finally {
    setLlmArtifactsLoading(false);
  }
}, [detail.channel, detail.video]);

const openLlmEditor = useCallback(
  async (artifactName: string) => {
    setLlmEditorOpen(true);
    setLlmEditorName(artifactName);
    setLlmEditorLoading(true);
    setLlmEditorError(null);
    setLlmEditorArtifact(null);
    setLlmEditorContent("");
    try {
      const art = await fetchLlmArtifact(detail.channel, detail.video, artifactName);
      setLlmEditorArtifact(art);
      setLlmEditorContent(art.content ?? "");
      setLlmEditorStatus(art.status === "ready" ? "ready" : "pending");
      setLlmEditorApplyOutput(true);
    } catch (err) {
      setLlmEditorError(err instanceof Error ? err.message : String(err));
    } finally {
      setLlmEditorLoading(false);
    }
  },
  [detail.channel, detail.video]
);

const saveLlmEditor = useCallback(async () => {
  if (!llmEditorName) {
    return;
  }
  setLlmEditorSaving(true);
  setLlmEditorError(null);
  try {
    const updated = await updateLlmArtifact(detail.channel, detail.video, llmEditorName, {
      status: llmEditorStatus,
      content: llmEditorContent,
      applyOutput: llmEditorApplyOutput && llmEditorStatus === "ready",
    });
    setLlmEditorArtifact(updated);
    setMessage("LLM Box を保存しました");
    setLlmEditorOpen(false);
    await refreshLlmArtifacts();
  } catch (err) {
    setLlmEditorError(err instanceof Error ? err.message : String(err));
  } finally {
    setLlmEditorSaving(false);
  }
}, [
  detail.channel,
  detail.video,
  llmEditorApplyOutput,
  llmEditorContent,
  llmEditorName,
  llmEditorStatus,
  refreshLlmArtifacts,
]);

useEffect(() => {
  const currentTab = activeTabProp ?? activeTabInternal;
  if (currentTab !== "script") {
    return;
  }
  void refreshLlmArtifacts();
}, [activeTabProp, activeTabInternal, refreshLlmArtifacts]);

  const ttsTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const detailKeyRef = useRef<string | null>(null);

  const assembledDirty = useMemo(() => assembledDraft !== assembledBase, [assembledDraft, assembledBase]);
  const audioDirty = useMemo(
    () => ttsDraft !== ttsBase || audioReviewed !== audioReviewedBase,
    [audioReviewed, audioReviewedBase, ttsBase, ttsDraft]
  );
  const ttsDirty = assembledDirty || audioDirty;
  const llmPendingCount = useMemo(
    () => llmArtifacts.filter((item) => item.status === "pending").length,
    [llmArtifacts]
  );
  const redoDirty =
    redoScript !== (detail.redo_script ?? true) ||
    redoAudio !== (detail.redo_audio ?? true) ||
    redoNote !== (detail.redo_note ?? "");

  const refreshAudioScript = useCallback(async () => {
    setAudioScriptLoading(true);
    setAudioScriptError(null);
    try {
      const data = await fetchPlainTtsScript(detail.channel, detail.video);
      const fetched = data.content ?? "";
      setTtsAiContent(fetched);
      if (!audioDirty) {
        setTtsDraft(fetched);
        setTtsBase(fetched);
      }
      setAudioScriptUpdatedAt(data.updated_at ?? detail.audio_updated_at ?? null);
    } catch (refreshError) {
      const message =
        refreshError instanceof Error
          ? refreshError.message
          : String(refreshError ?? "音声用テキストの取得に失敗しました。");
      setAudioScriptError(message);
    } finally {
      setAudioScriptLoading(false);
    }
  }, [audioDirty, detail.audio_updated_at, detail.channel, detail.video]);

  useEffect(() => {
    if (copyStatus === "idle") {
      return;
    }
    const timer = window.setTimeout(() => setCopyStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  useEffect(() => {
    if (copyAssembledNoSepStatus === "idle") {
      return;
    }
    const timer = window.setTimeout(() => setCopyAssembledNoSepStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyAssembledNoSepStatus]);

  useEffect(() => {
    if (copyTtsNoSepStatus === "idle") {
      return;
    }
    const timer = window.setTimeout(() => setCopyTtsNoSepStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyTtsNoSepStatus]);

  useEffect(() => {
    if (copyATextNoSepStatus === "idle") {
      return;
    }
    const timer = window.setTimeout(() => setCopyATextNoSepStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyATextNoSepStatus]);

  useEffect(() => {
    if (aiCopyStatus === "idle") {
      return;
    }
    const timer = window.setTimeout(() => setAiCopyStatus("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [aiCopyStatus]);

  useEffect(() => {
    let cancelled = false;
    const loadHumanScripts = async () => {
      setHumanLoading(true);
      setHumanError(null);
      try {
        const data = await fetchHumanScripts(detail.channel, detail.video);
        if (cancelled) {
          return;
        }
        const aiA = data.assembled_content ?? detail.assembled_content ?? "";
        const humanA = data.assembled_human_content ?? aiA;
        setAssembledAiContent(aiA);
        setAssembledDraft(humanA);
        setAssembledBase(humanA);

        const aiB = data.script_audio_content ?? detail.script_audio_content ?? detail.tts_plain_content ?? detail.tts_content ?? "";
        const humanB = data.script_audio_human_content ?? aiB;
        setTtsAiContent(aiB);
        setTtsDraft(humanB);
        setTtsBase(humanB);

        const reviewed = data.audio_reviewed ?? false;
        setAudioReviewed(reviewed);
        setAudioReviewedBase(reviewed);
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setHumanError(loadError instanceof Error ? loadError.message : String(loadError ?? "台本取得に失敗しました"));
        const fallbackA = detail.assembled_human_content ?? detail.assembled_content ?? "";
        setAssembledAiContent(detail.assembled_content ?? "");
        setAssembledDraft(fallbackA);
        setAssembledBase(fallbackA);

        const fallbackB =
          detail.script_audio_human_content ?? detail.script_audio_content ?? detail.tts_plain_content ?? detail.tts_content ?? "";
        setTtsAiContent(detail.script_audio_content ?? detail.tts_plain_content ?? detail.tts_content ?? "");
        setTtsDraft(fallbackB);
        setTtsBase(fallbackB);
        setAudioReviewed(detail.audio_reviewed ?? false);
        setAudioReviewedBase(detail.audio_reviewed ?? false);
      } finally {
        if (!cancelled) {
          setHumanLoading(false);
        }
      }
    };
    void loadHumanScripts();
    return () => {
      cancelled = true;
    };
  }, [detail.assembled_content, detail.assembled_human_content, detail.audio_reviewed, detail.channel, detail.script_audio_content, detail.script_audio_human_content, detail.tts_content, detail.tts_plain_content, detail.updated_at, detail.video]);

  useEffect(() => {
    setStatusDraft(detail.status ?? "");
    setReadyDraft(detail.ready_for_audio);
    setStageDrafts(detail.stages ?? {});
    setMessage(null);
    setError(null);
    setTtsValidation(null);
    setTtsValidationError(null);
    setValidationStatus("idle");
    setAiResult(null);
    setAiError(null);
    setCopyStatus("idle");
    setCopyAssembledNoSepStatus("idle");
    setCopyTtsNoSepStatus("idle");
    setCopyATextNoSepStatus("idle");
    setAiCopyStatus("idle");
    setAudioScriptUpdatedAt(detail.audio_updated_at ?? null);
    setAudioScriptError(null);
    setRedoScript(detail.redo_script ?? true);
    setRedoAudio(detail.redo_audio ?? true);
    setRedoNote(detail.redo_note ?? "");
  }, [detail]);

  useEffect(() => {
    const key = `${detail.channel ?? ""}::${detail.video ?? ""}`;
    if (detailKeyRef.current !== key) {
      detailKeyRef.current = key;
      setActiveTabInternal(activeTabProp ?? "script");
    }
  }, [activeTabProp, detail.channel, detail.video]);

  useEffect(() => {
    if (activeTabProp && activeTabProp !== activeTabInternal) {
      setActiveTabInternal(activeTabProp);
    }
  }, [activeTabProp, activeTabInternal]);

  useEffect(() => {
    onDirtyChange?.(ttsDirty);
  }, [onDirtyChange, ttsDirty]);

  useEffect(() => {
    if (!ttsDirty) {
      return;
    }
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "未保存の変更があります。離脱すると変更が失われます。";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [ttsDirty]);

  const handleCopyTts = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      setCopyStatus("unsupported");
      return;
    }
    try {
      await navigator.clipboard.writeText(ttsDraft);
      setCopyStatus("copied");
    } catch (copyError) {
      console.error("Failed to copy TTS text", copyError);
      setCopyStatus("error");
    }
  }, [ttsDraft]);

  const handleCopyAssembledWithoutSeparators = useCallback(async () => {
    const cleaned = stripPauseSeparators(assembledDraft);
    if (!cleaned) {
      setCopyAssembledNoSepStatus("error");
      return;
    }
    try {
      await copyTextToClipboard(cleaned);
      setCopyAssembledNoSepStatus("copied");
    } catch (copyError) {
      console.error("Failed to copy A text", copyError);
      setCopyAssembledNoSepStatus("error");
    }
  }, [assembledDraft]);

  const handleCopyTtsWithoutSeparators = useCallback(async () => {
    const cleaned = stripPauseSeparators(ttsDraft);
    if (!cleaned) {
      setCopyTtsNoSepStatus("error");
      return;
    }
    try {
      await copyTextToClipboard(cleaned);
      setCopyTtsNoSepStatus("copied");
    } catch (copyError) {
      console.error("Failed to copy B text", copyError);
      setCopyTtsNoSepStatus("error");
    }
  }, [ttsDraft]);

  const handleCopyATextModalWithoutSeparators = useCallback(async () => {
    const cleaned = stripPauseSeparators(aTextModalContent);
    if (!cleaned) {
      setCopyATextNoSepStatus("error");
      return;
    }
    try {
      await copyTextToClipboard(cleaned);
      setCopyATextNoSepStatus("copied");
    } catch (copyError) {
      console.error("Failed to copy modal A text", copyError);
      setCopyATextNoSepStatus("error");
    }
  }, [aTextModalContent]);

  const handleCopyAiResult = useCallback(async () => {
    if (!aiResult) {
      return;
    }
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      setAiCopyStatus("unsupported");
      return;
    }
    try {
      await navigator.clipboard.writeText(aiResult);
      setAiCopyStatus("copied");
    } catch (copyError) {
      console.error("Failed to copy AI summary", copyError);
      setAiCopyStatus("error");
    }
  }, [aiResult]);

  const handleRunAiCheck = useCallback(async () => {
    const normalized = ttsDraft.trim();
    if (!normalized) {
      setAiError("評価対象となる台本がありません。");
      setAiResult(null);
      return;
    }
    setAiBusy(true);
    setAiError(null);
    try {
      const response = await enhanceTts(detail.channel, detail.video, {
        text: normalized,
        instruction: aiInstruction.trim() || DEFAULT_AI_CHECK_INSTRUCTION,
      });
      setAiResult(response.suggestion ?? "");
    } catch (aiErr) {
      const message = aiErr instanceof Error ? aiErr.message : String(aiErr ?? "AI評価に失敗しました。");
      setAiError(message);
      setAiResult(null);
    } finally {
      setAiBusy(false);
    }
  }, [aiInstruction, detail.channel, detail.video, ttsDraft]);

  const handleValidateDraft = useCallback(
    async () => {
      const normalized = ttsDraft.trim();
      if (!normalized) {
        setTtsValidation(null);
        setTtsValidationError("台本テキストが空です。編集してから検証を実行してください。");
        setValidationStatus("error");
        return;
      }
      setTtsValidationError(null);
      setValidationStatus("running");
      setTtsValidating(true);
      try {
        const result = await onValidateTts(normalized);
        setTtsValidation(result);
        setValidationStatus(result.valid ? "success" : "warning");
        if (result.valid) {
          setMessage("音声用テキストに問題は見つかりませんでした。");
        }
      } catch (validationError) {
        const message =
          validationError instanceof Error
            ? validationError.message
            : String(validationError ?? "検証に失敗しました。");
        setTtsValidation(null);
        setTtsValidationError(message);
        setValidationStatus("error");
      } finally {
        setTtsValidating(false);
      }
    },
    [onValidateTts, ttsDraft]
  );

  const handleApplyValidatedContent = useCallback(() => {
    if (ttsValidation?.sanitized_content) {
      setTtsDraft(ttsValidation.sanitized_content);
      setMessage("検証済みテキストを反映しました。");
      setValidationStatus("success");
    }
  }, [ttsValidation]);

  const wrapAction = useCallback(
    async (label: string, fn: () => Promise<unknown>) => {
      setBusyAction(label);
      setMessage(null);
      setError(null);
      try {
        await fn();
        setMessage(`${label} を保存しました`);
      } catch (actionError) {
        const errorMessage =
          actionError instanceof Error ? actionError.message : String(actionError ?? "不明なエラー");
        const isConflict = errorMessage.includes("最新の情報を再取得");
        if (isConflict) {
          setError(
            "他の作業者が内容を更新したため保存できませんでした。画面を再読み込みし、最新の内容を確認してください。"
          );
        } else {
          setError(`保存に失敗しました: ${errorMessage}`);
        }
      } finally {
        setBusyAction(null);
      }
    },
    []
  );

  const handleSaveAssembledDraft = useCallback(async () => {
    await wrapAction("表示用テキスト", async () => {
      const res = await updateHumanScripts(detail.channel, detail.video, {
        assembled_human: assembledDraft,
        audio_reviewed: true,
        expectedUpdatedAt: detail.updated_at ?? null,
      });
      const reviewed = true;
      setAudioReviewed(reviewed);
      setAudioReviewedBase(reviewed);
      setAssembledBase(assembledDraft);
      // 台本リテイクを自動解除
      setRedoScript(false);
      setRedoSaving(true);
      try {
        await updateVideoRedo(detail.channel, detail.video, { redo_script: false, redo_note: redoNote });
      } catch {
        /* best effort */
      } finally {
        setRedoSaving(false);
      }
    });
  }, [assembledDraft, detail.channel, detail.updated_at, detail.video, redoNote, wrapAction]);

  const handleSaveAudioDraft = useCallback(async () => {
    await wrapAction("音声用テキスト", async () => {
      const res = await updateHumanScripts(detail.channel, detail.video, {
        script_audio_human: ttsDraft,
        audio_reviewed: true,
        expectedUpdatedAt: detail.updated_at ?? null,
      });
      const reviewed = true;
      setAudioReviewed(reviewed);
      setAudioReviewedBase(reviewed);
      setTtsBase(ttsDraft);
      // 音声リテイクを自動解除
      setRedoAudio(false);
      setRedoSaving(true);
      try {
        await updateVideoRedo(detail.channel, detail.video, { redo_audio: false, redo_note: redoNote });
      } catch {
        /* best effort */
      } finally {
        setRedoSaving(false);
      }
    });
  }, [detail.channel, detail.updated_at, detail.video, redoNote, ttsDraft, wrapAction]);

  const handleSaveBothScripts = useCallback(async () => {
    await wrapAction("A・Bテキスト", async () => {
      const res = await updateHumanScripts(detail.channel, detail.video, {
        assembled_human: assembledDraft,
        script_audio_human: ttsDraft,
        audio_reviewed: true,
        expectedUpdatedAt: detail.updated_at ?? null,
      });
      const reviewed = true;
      setAudioReviewed(reviewed);
      setAudioReviewedBase(reviewed);
      setAssembledBase(assembledDraft);
      setTtsBase(ttsDraft);
      // 台本/音声リテイクを自動解除
      setRedoScript(false);
      setRedoAudio(false);
      setRedoSaving(true);
      try {
        await updateVideoRedo(detail.channel, detail.video, {
          redo_script: false,
          redo_audio: false,
          redo_note: redoNote,
        });
      } catch {
        /* best effort */
      } finally {
        setRedoSaving(false);
      }
    });
  }, [assembledDraft, detail.channel, detail.updated_at, detail.video, redoNote, ttsDraft, wrapAction]);

  const handleSaveStatus = useCallback(
    () => wrapAction("案件ステータス", () => onUpdateStatus(statusDraft)),
    [onUpdateStatus, statusDraft, wrapAction]
  );

  const handleSaveStages = useCallback(
    () => wrapAction("ステージ進捗", () => onUpdateStages(stageDrafts)),
    [onUpdateStages, stageDrafts, wrapAction]
  );

  const handleSaveReady = useCallback(
    (ready: boolean) => wrapAction("音声準備フラグ", () => onUpdateReady(ready)),
    [onUpdateReady, wrapAction]
  );

  const activeTab = activeTabProp ?? activeTabInternal;

  const handleSelectTab = useCallback(
    (tab: DetailTab) => {
      setActiveTabInternal(tab);
      onTabChange?.(tab);
    },
    [onTabChange]
  );

  const formatHistoryTimestamp = useCallback((value?: unknown) => {
    if (typeof value !== "string" || !value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString("ja-JP");
  }, []);

  const audioHistory = useMemo<AudioHistoryEntry[]>(() => {
    const raw = (detail.audio_metadata as Record<string, unknown> | null)?.history;
    if (!Array.isArray(raw)) {
      return [];
    }
    return raw
      .map((entry) => {
        const obj = entry as Record<string, unknown>;
        return {
          event: typeof obj.event === "string" ? obj.event : null,
          status: typeof obj.status === "string" ? obj.status : null,
          message: typeof obj.message === "string" ? obj.message : null,
          timestamp: typeof obj.timestamp === "string" ? obj.timestamp : null,
          final_wav: typeof obj.final_wav === "string" ? obj.final_wav : null,
          final_srt: typeof obj.final_srt === "string" ? obj.final_srt : null,
          log_json: typeof obj.log_json === "string" ? obj.log_json : null,
          log_text: typeof obj.log_text === "string" ? obj.log_text : null,
        } as AudioHistoryEntry;
      })
      .reverse();
  }, [detail.audio_metadata]);

  const audioHistoryAvailable = audioHistory.length > 0;

  const audioDurationLabel = useMemo(() => {
    if (typeof detail.audio_duration_seconds === "number") {
      return `${detail.audio_duration_seconds.toFixed(1)} 秒`;
    }
    return "未計測";
  }, [detail.audio_duration_seconds]);

  const audioUpdatedLabel = useMemo(() => {
    if (!detail.audio_updated_at) {
      return "未更新";
    }
    const date = new Date(detail.audio_updated_at);
    if (Number.isNaN(date.getTime())) {
      return detail.audio_updated_at;
    }
    return date.toLocaleString("ja-JP");
  }, [detail.audio_updated_at]);

  const audioQualityLabel = detail.audio_quality_status ?? "未評価";
  const audioQualitySummary = detail.audio_quality_summary;
  const audioStageStatus = detail.stages?.audio_synthesis ?? "pending";
  const audioStageLabel = translateStatus(audioStageStatus);
  const audioDownloadUrl = useMemo(() => {
    if (!detail.audio_url) {
      return null;
    }
    if (/^https?:/i.test(detail.audio_url)) {
      return detail.audio_url;
    }
    const normalized = detail.audio_url.startsWith("/") ? detail.audio_url : `/${detail.audio_url}`;
    return `${API_BASE_URL}${normalized}`;
  }, [detail.audio_url]);

  const srtDownloadUrl = useMemo(() => {
    const base = API_BASE_URL?.replace(/\/$/, "") ?? "";
    return `${base}/api/channels/${encodeURIComponent(detail.channel)}/videos/${encodeURIComponent(detail.video)}/srt`;
  }, [detail.channel, detail.video]);

  const audioScriptUpdatedLabel = useMemo(() => {
    const source = audioScriptUpdatedAt ?? detail.audio_updated_at ?? detail.updated_at;
    if (!source) {
      return "未更新";
    }
    const date = new Date(source);
    if (Number.isNaN(date.getTime())) {
      return source;
    }
    return date.toLocaleString("ja-JP");
  }, [audioScriptUpdatedAt, detail.audio_updated_at, detail.updated_at]);

  const detailUpdatedLabel = useMemo(() => {
    if (!detail.updated_at) {
      return "未更新";
    }
    const date = new Date(detail.updated_at);
    if (Number.isNaN(date.getTime())) {
      return detail.updated_at;
    }
    return date.toLocaleString("ja-JP");
  }, [detail.updated_at]);

  const planningLink = useMemo(() => {
    return `/progress?channel=${encodeURIComponent(detail.channel)}&video=${encodeURIComponent(detail.video)}`;
  }, [detail.channel, detail.video]);
  const studioLink = useMemo(() => {
    return `/studio?channel=${encodeURIComponent(detail.channel)}&video=${encodeURIComponent(detail.video)}`;
  }, [detail.channel, detail.video]);

  const completedLabel = useMemo(() => {
    if (!detail.completed_at) {
      return null;
    }
    const date = new Date(detail.completed_at);
    if (Number.isNaN(date.getTime())) {
      return detail.completed_at;
    }
    return date.toLocaleString("ja-JP");
  }, [detail.completed_at]);

  const scriptStageKeys = useMemo(() => STAGE_ORDER.slice(0, 7), []);
  const audioStageKeys = useMemo(() => ["script_audio_ai", "script_tts_prepare", "audio_synthesis"], []);
  const subtitleStageKeys = useMemo(() => ["srt_generation", "timeline_copy"], []);

  const progressSummary = useMemo(() => {
    const stages = detail.stages ?? {};
    const countCompleted = (keys: string[]) => keys.filter((key) => stages[key] === "completed").length;
    const toPercent = (completed: number, total: number) => (total === 0 ? 0 : Math.min(100, Math.round((completed / total) * 100)));
    const scriptCompleted = countCompleted(scriptStageKeys);
    const audioCompleted = countCompleted(audioStageKeys);
    const subtitleCompleted = countCompleted(subtitleStageKeys);
    return {
      script: {
        completed: scriptCompleted,
        total: scriptStageKeys.length,
        percent: toPercent(scriptCompleted, scriptStageKeys.length),
        status: translateStatus(detail.status),
      },
      audio: {
        completed: audioCompleted,
        total: audioStageKeys.length,
        percent: toPercent(audioCompleted, audioStageKeys.length),
        status: audioStageLabel,
        ready: readyDraft,
      },
      subtitle: {
        completed: subtitleCompleted,
        total: subtitleStageKeys.length,
        percent: toPercent(subtitleCompleted, subtitleStageKeys.length),
        status: subtitleCompleted === subtitleStageKeys.length ? "完了" : "調整中",
      },
      timestamps: {
        updated: detailUpdatedLabel,
        audioUpdated: audioUpdatedLabel,
        completed: completedLabel,
      },
      quality: {
        label: audioQualityLabel,
        summary: audioQualitySummary,
        duration: audioDurationLabel,
      },
    };
  }, [audioQualityLabel, audioQualitySummary, audioStageLabel, audioDurationLabel, audioUpdatedLabel, completedLabel, detail.status, detail.stages, detailUpdatedLabel, readyDraft, scriptStageKeys, audioStageKeys, subtitleStageKeys]);

  const overviewProgress = useMemo(
    () => [
      {
        key: "script",
        icon: "📝",
        title: "台本",
        description: "研究〜検証",
        percent: progressSummary.script.percent,
        status: progressSummary.script.status,
      },
      {
        key: "audio",
        icon: "🎙️",
        title: "音声",
        description: progressSummary.audio.ready ? "音声準備済み" : "音声未準備",
        percent: progressSummary.audio.percent,
        status: progressSummary.audio.status,
        tone: progressSummary.audio.ready ? "success" : "warning",
      },
      {
        key: "subtitle",
        icon: "💬",
        title: "字幕",
        description: progressSummary.subtitle.status,
        percent: progressSummary.subtitle.percent,
        status: progressSummary.subtitle.status,
        tone: progressSummary.subtitle.percent === 100 ? "info" : undefined,
      },
    ],
    [progressSummary]
  );

  const stageEntries = useMemo(
    () =>
      STAGE_ORDER.filter((stage) => stage in detail.stages || stageDrafts[stage] !== undefined).map((stage) => {
        const raw = stageDrafts[stage] ?? detail.stages?.[stage] ?? "";
        const label = translateStage(stage);
        const statusLabel = translateStatus(raw || "");
        const tone =
          raw === "completed"
            ? "success"
            : raw === "review"
              ? "warning"
              : raw === "blocked"
                ? "danger"
                : raw === "in_progress"
                  ? "info"
                  : undefined;
        return { stage, label, status: raw ?? "", statusLabel, tone };
      }),
    [detail.stages, stageDrafts]
  );

  const youtubeDescription = detail.youtube_description ?? "";
  const episodeId = `${detail.channel}-${detail.video}`;
  const workflowLink = `/workflow?channel=${encodeURIComponent(detail.channel)}&video=${encodeURIComponent(detail.video)}`;
  const capcutDraftLink = `/capcut-edit/draft?channel=${encodeURIComponent(detail.channel)}&video=${encodeURIComponent(detail.video)}`;
  const videoProductionLink = `/capcut-edit/production?channel=${encodeURIComponent(detail.channel)}&video=${encodeURIComponent(detail.video)}&project=${encodeURIComponent(episodeId)}`;



  const tabItems = useMemo<DetailTabItem[]>(() => {
    const scriptBadge = ttsDirty ? "未保存" : null;
    const audioBadge =
      audioStageStatus === "blocked"
        ? "要対応"
        : audioStageStatus === "in_progress"
          ? "生成中"
          : audioStageStatus === "review"
            ? "レビュー"
            : null;
    return [
      { key: "overview", label: "概要" },
      { key: "script", label: "台本・音声字幕", badge: scriptBadge, tone: ttsDirty ? "warning" : undefined },
      { key: "audio", label: "音声レビュー", badge: audioBadge, tone: audioBadge ? "warning" : undefined },
      { key: "video", label: "動画" },
      { key: "history", label: "履歴" },
    ];
  }, [audioStageStatus, ttsDirty]);

  const primarySaveHandler = useMemo<(() => void) | null>(
    () =>
      activeTab === "script"
        ? () => {
            void handleSaveBothScripts();
          }
        : null,
    [activeTab, handleSaveBothScripts]
  );

  const primarySaveLabel = useMemo(
    () => (activeTab === "script" ? "A・Bテキストを保存" : "保存"),
    [activeTab]
  );

  const primarySaveDisabled = primarySaveHandler === null || busyAction !== null || (!assembledDirty && !audioDirty && audioReviewed === audioReviewedBase);

  const audioWorkspaceHandlers = useMemo(
    () => ({
      onSaveSrt,
      onVerifySrt,
      onUpdateStatus,
      onUpdateReady,
      onUpdateStages,
      onReplaceTts,
      onValidateTts,
    }),
    [
      onReplaceTts,
      onSaveSrt,
      onUpdateReady,
      onUpdateStages,
      onUpdateStatus,
      onValidateTts,
      onVerifySrt,
    ]
  );

  const sanitizedContentDiffers = useMemo(
    () => Boolean(ttsValidation?.sanitized_content && ttsValidation.sanitized_content !== ttsDraft),
    [ttsDraft, ttsValidation]
  );

  const handleCopyDescription = useCallback(async () => {
    if (!youtubeDescription) {
      return;
    }
    try {
      await navigator.clipboard.writeText(youtubeDescription);
      setCopyDescStatus("copied");
      window.setTimeout(() => setCopyDescStatus("idle"), 2000);
    } catch (_error) {
      setCopyDescStatus("error");
      window.setTimeout(() => setCopyDescStatus("idle"), 2000);
    }
  }, [youtubeDescription]);

  return (
    <div className="panel detail-panel" id="video-detail">
      <header className="detail-header">
        <div>
          <h2>
            {detail.channel} / {detail.video}
          </h2>
          <p className="muted">{detail.script_id ?? "スクリプトID未設定"}</p>
          <p className="detail-title">{detail.title ?? "タイトル未設定"}</p>
          <p className="muted">
            最終更新: {detailUpdatedLabel}
            {completedLabel ? ` ／ 完了登録: ${completedLabel}` : ""}
          </p>
        </div>
        <div className="status-box">
          <label className="label">案件ステータス</label>
          <div className="inline-group">
            <input
              type="text"
              value={statusDraft}
              onChange={(event) => setStatusDraft(event.target.value)}
              placeholder="例: in_progress"
            />
            <button type="button" onClick={handleSaveStatus} disabled={busyAction !== null}>
              保存
            </button>
          </div>
          <div className="inline-group">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={readyDraft}
                onChange={(event) => {
                  const readyValue = event.target.checked;
                  setReadyDraft(readyValue);
                  void handleSaveReady(readyValue);
                }}
              />
              音声収録の準備が完了
            </label>
          </div>
          <p className="muted status-note">現在: {translateStatus(detail.status)}</p>
        </div>
      </header>

      <nav className="detail-tabs" role="tablist">
        {tabItems.map((item) => {
          const active = activeTab === item.key;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              className={active ? "detail-tab detail-tab--active" : "detail-tab"}
              onClick={() => handleSelectTab(item.key)}
              aria-selected={active}
            >
              <span>{item.label}</span>
              {item.badge ? <span className="detail-tab__badge">{item.badge}</span> : null}
            </button>
          );
        })}
      </nav>

      <div className="detail-action-bar">
        <div className="detail-action-bar__left">
          {primarySaveHandler ? (
            <button
              type="button"
              className="action-button"
              onClick={primarySaveHandler}
              disabled={primarySaveDisabled}
            >
              {primarySaveLabel} <span className="action-button__shortcut">⌘/Ctrl+S</span>
            </button>
          ) : null}
        </div>
        <div className="detail-action-bar__right">
          <Link className="action-chip" to={planningLink}>
            企画CSV
          </Link>
          <Link className="action-chip" to={workflowLink}>
            制作フロー
          </Link>
          <Link className="action-chip" to={studioLink}>
            Studio
          </Link>
          <Link className="action-chip" to={capcutDraftLink}>
            CapCutドラフト
          </Link>
          <Link className="action-chip" to={videoProductionLink}>
            CapCut管理
          </Link>
          <Link className="action-chip" to="/thumbnails">
            サムネ
          </Link>
          {activeTab === "audio" && audioHistoryAvailable ? (
            <button
              type="button"
              className={`action-chip${showAudioHistory ? " action-chip--active" : ""}`}
              onClick={() => setShowAudioHistory((value) => !value)}
              aria-pressed={showAudioHistory}
            >
              履歴
            </button>
          ) : null}
        </div>
      </div>

      <div className="detail-tab-panels">
        {activeTab === "overview" && (
          <div className="detail-tab-panel detail-tab-panel--overview" role="tabpanel">
            <section className="overview-grid">
              <div className="panel-card overview-progress-card">
                <header className="panel-card__header">
                  <h3>この台本の進行状況</h3>
                  <span className="muted small-text">最終更新: {progressSummary.timestamps.updated ?? "未更新"}</span>
                </header>
                <ul className="progress-list">
                  {overviewProgress.map((item) => (
                    <li key={item.key} className={`progress-list__item${item.tone ? ` progress-list__item--${item.tone}` : ""}`}>
                      <div className="progress-list__label">
                        <span className="progress-list__icon" aria-hidden>
                          {item.icon}
                        </span>
                        <div>
                          <p className="progress-list__title">{item.title}</p>
                          <p className="progress-list__description">{item.description}</p>
                        </div>
                      </div>
                      <div className="progress-list__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.percent}>
                        <div className="progress-list__bar-fill" style={{ width: `${item.percent}%` }} />
                      </div>
                      <span className="progress-list__status">{item.status}</span>
                    </li>
                  ))}
                </ul>
                <div className="progress-meta">
                  <span className="progress-meta__item">音声最終生成: {progressSummary.timestamps.audioUpdated ?? "未更新"}</span>
                  <span className="progress-meta__item">音声品質: {progressSummary.quality.label}</span>
                  {progressSummary.quality.duration ? (
                    <span className="progress-meta__item">長さ: {progressSummary.quality.duration}</span>
                  ) : null}
                  {progressSummary.quality.summary ? (
                    <span className="progress-meta__item">メモ: {progressSummary.quality.summary}</span>
                  ) : null}
                </div>
              </div>

              <div className="panel-card overview-stage-card">
                <header className="panel-card__header stage-card__header">
                  <div>
                    <h3>進捗ステージ</h3>
                    <p className="muted small-text">ステージ変更は必要な場合のみ行ってください。</p>
                  </div>
                  <button type="button" className="stage-card__toggle" onClick={() => setShowStageDetails((value) => !value)}>
                    {showStageDetails ? "折りたたむ" : "詳細を表示"}
                  </button>
                </header>
                <StageProgress stages={detail.stages} />
                {showStageDetails ? (
                  <div className="stage-detail-grid">
                    {stageEntries.map((entry) => (
                      <label key={entry.stage} className={`stage-detail${entry.tone ? ` stage-detail--${entry.tone}` : ""}`}>
                        <span className="stage-detail__label">{entry.label}</span>
                        <select
                          value={entry.status ?? ""}
                          onChange={(event) =>
                            setStageDrafts((current) => ({
                              ...current,
                              [entry.stage]: event.target.value,
                            }))
                          }
                        >
                          <option value="">未設定</option>
                          {STAGE_STATUS_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <span className="stage-detail__status">{entry.statusLabel}</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="muted small-text">詳細を開くとステージの編集が可能です。</p>
                )}
                <div className="actions actions--compact">
                  <button type="button" onClick={handleSaveStages} disabled={busyAction !== null}>
                    進捗を保存
                  </button>
                </div>
              </div>

              <div className="panel-card overview-assets-card">
                <header className="panel-card__header">
                  <h3>音声・字幕ファイル</h3>
                  <span className="muted small-text">生成済みの最新版にアクセスできます</span>
                </header>
                <div className="overview-assets__actions">
                  <button
                    type="button"
                    className="workspace-button workspace-button--ghost"
                    onClick={() => {
                      if (audioDownloadUrl) {
                        window.open(audioDownloadUrl, "_blank", "noreferrer");
                      }
                    }}
                    disabled={!audioDownloadUrl}
                  >
                    音声を開く
                  </button>
                  <button
                    type="button"
                    className="workspace-button workspace-button--ghost"
                    onClick={() => {
                      void openATextModal();
                    }}
                  >
                    Aテキストを見る
                  </button>
                  <button
                    type="button"
                    className="workspace-button workspace-button--ghost"
                    onClick={() => {
                      if (srtDownloadUrl) {
                        window.open(srtDownloadUrl, "_blank", "noreferrer");
                      }
                    }}
                  >
                    字幕SRTを開く
                  </button>
                </div>
                {!audioDownloadUrl ? <p className="muted small-text">音声がまだ生成されていません。</p> : null}
              </div>

              <div className="panel-card overview-description-card">
                <header className="panel-card__header">
                  <h3>YouTube説明文</h3>
                  <span className="muted small-text">投稿時にコピペできます（自動生成）</span>
                </header>
                <textarea
                  className="youtube-description-textarea"
                  value={youtubeDescription}
                  readOnly
                  placeholder="説明文が生成されていません"
                />
                <div className="actions actions--compact">
                  <button type="button" onClick={handleCopyDescription} disabled={!youtubeDescription}>
                    コピー
                  </button>
                  <span className="muted small-text">
                    {copyDescStatus === "copied"
                      ? "コピーしました"
                      : copyDescStatus === "error"
                        ? "コピーに失敗しました"
                        : ""}
                  </span>
                </div>
              </div>
            </section>
          </div>
        )}

        {activeTab === "script" && (
          <section className="detail-tab-panel detail-tab-panel--script script-tab" role="tabpanel">
            {warningMessages.length > 0 ? (
              <div className="main-alert main-alert--warning" role="alert">
                <strong>未整備:</strong> {warningMessages.join(" / ")}
              </div>
            ) : null}
            <CollapseCard
              title="LLM Boxes（埋める箱）"
              subtitle={
                llmArtifactsLoading
                  ? "読み込み中…"
                  : llmArtifactsError
                    ? "取得失敗"
                    : llmArtifacts.length === 0
                      ? "なし"
                      : llmPendingCount > 0
                        ? `pending ${llmPendingCount}`
                        : `ready ${llmArtifacts.length}`
              }
              open={llmBoxesOpen}
              onToggle={setLlmBoxesOpen}
              highlight={llmPendingCount > 0}
            >
              <p className="muted small-text">
                THINK/AGENT などで止まった LLM 出力（箱）を UI から埋めて <code>status=ready</code> にできます。
              </p>
              <div className="actions actions--compact">
                <button type="button" onClick={() => void refreshLlmArtifacts()} disabled={llmArtifactsLoading}>
                  更新
                </button>
              </div>
              {llmArtifactsError ? <p className="error">{llmArtifactsError}</p> : null}
              {!llmArtifactsLoading && llmArtifacts.length === 0 ? (
                <p className="muted small-text">この動画の LLM Boxes はありません。</p>
              ) : null}
              {llmArtifacts.length > 0 ? (
                <div style={{ display: "grid", gap: 8 }}>
                  {llmArtifacts
                    .slice()
                    .sort((a, b) => {
                      const rank = (status: string) =>
                        status === "pending" ? 0 : status === "ready" ? 1 : 2;
                      const byStatus = rank(a.status) - rank(b.status);
                      if (byStatus !== 0) return byStatus;
                      const byStage = String(a.stage ?? "").localeCompare(String(b.stage ?? ""));
                      if (byStage !== 0) return byStage;
                      return a.name.localeCompare(b.name);
                    })
                    .map((item) => (
                      <div key={item.name} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                        <div style={{ minWidth: 0 }}>
                          <strong style={{ fontSize: 13 }}>
                            {item.stage ?? item.name}
                            {item.task ? ` / ${item.task}` : ""}
                          </strong>
                          {item.output_path ? (
                            <div className="muted small-text" style={{ wordBreak: "break-all" }}>
                              <code>{item.output_path}</code>
                            </div>
                          ) : null}
                          {item.error ? <div className="error small-text">{item.error}</div> : null}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className="muted small-text">{item.status}</span>
                          <button
                            type="button"
                            className="workspace-button workspace-button--ghost workspace-button--sm"
                            onClick={() => void openLlmEditor(item.name)}
                            disabled={item.status === "error"}
                          >
                            編集
                          </button>
                        </div>
                      </div>
                    ))}
                </div>
              ) : null}
            </CollapseCard>
            {/* AI生成版は非表示 */}
            {SHOW_AI_SECTION && (
              <div className="script-row">
                <h2 className="script-row__title">AI生成版（参照用）</h2>
                <div className="script-tab__layout">
                  <div className="script-editor-card script-editor-card--ghost">
                    <header className="script-editor__header">
                      <div>
                        <h3>Aテキスト（表示用）</h3>
                        <p className="script-editor__hint">視聴者へ見せる台本のAI生成版</p>
                      </div>
                      <div className="script-editor__meta" aria-live="polite">
                        <span className="script-editor__counter">
                          文字数: {assembledAiContent.replace(/\r/g, "").replace(/\n/g, "").length.toLocaleString("ja-JP")}
                        </span>
                      </div>
                    </header>
                    <textarea
                      className="script-editor__textarea"
                      value={assembledAiContent}
                      readOnly
                      aria-readonly="true"
                      aria-label="AI生成台本（表示用）"
                      placeholder="AI生成台本がここに表示されます"
                    />
                  </div>
                  <div className="script-editor-card script-editor-card--ghost">
                    <header className="script-editor__header">
                      <div>
                        <h3>Bテキスト（音声用）</h3>
                        <p className="script-editor__hint">音声読み上げ用のAI生成版</p>
                      </div>
                      <div className="script-editor__meta" aria-live="polite">
                        <span className="script-editor__counter">
                          文字数: {ttsAiContent.replace(/\r/g, "").replace(/\n/g, "").length.toLocaleString("ja-JP")}
                        </span>
                      </div>
                    </header>
                    <textarea
                      className="script-editor__textarea"
                      value={ttsAiContent}
                      readOnly
                      aria-readonly="true"
                      aria-label="音声用テキスト AI版"
                      placeholder="AI生成の音声用テキストがここに表示されます"
                    />
                  </div>
                </div>
              </div>
            )}

            {/* 人間編集版の行: A' | B' */}
            <div className="script-row">
              <h2 className="script-row__title">人間編集版（編集可能）</h2>
              <div className="redo-panel">
                <div className="redo-panel__controls">
                  <label className="redo-panel__toggle">
                    <input
                      type="checkbox"
                      checked={redoScript}
                      onChange={(e) => setRedoScript(e.target.checked)}
                    />
                    台本リテイクが必要
                  </label>
                  <label className="redo-panel__toggle">
                    <input
                      type="checkbox"
                      checked={redoAudio}
                      onChange={(e) => setRedoAudio(e.target.checked)}
                    />
                    音声リテイクが必要
                  </label>
                </div>
                <div className="redo-panel__note">
                  <textarea
                    value={redoNote}
                    onChange={(e) => setRedoNote(e.target.value)}
                    placeholder="リテイク理由や指示をメモ"
                    rows={2}
                  />
                  <button
                    type="button"
                    className="workspace-button workspace-button--primary workspace-button--sm"
                    disabled={redoSaving || (!redoDirty)}
                    onClick={async () => {
                      setRedoSaving(true);
                      try {
                        await updateVideoRedo(detail.channel, detail.video, {
                          redo_script: redoScript,
                          redo_audio: redoAudio,
                          redo_note: redoNote,
                        });
                        setMessage("リテイク情報を保存しました");
                      } finally {
                        setRedoSaving(false);
                      }
                    }}
                  >
                    {redoSaving ? "保存中..." : "リテイク情報を保存"}
                  </button>
                </div>
              </div>
              <div className="script-tab__layout">
                {/* A' テキスト 人間版 */}
                <div className="script-editor-card">
                  <header className="script-editor__header">
                    <div>
                      <h3>Aテキスト（表示用）</h3>
                      <p className="script-editor__hint">視聴者へ見せる台本を編集</p>
                    </div>
                    <div className="script-editor__meta" aria-live="polite">
                      <span className="script-editor__counter">
                        文字数: {assembledDraft.replace(/\r/g, "").replace(/\n/g, "").length.toLocaleString("ja-JP")}
                      </span>
                      <button
                        type="button"
                        className="workspace-button workspace-button--ghost workspace-button--sm"
                        onClick={() => void handleCopyAssembledWithoutSeparators()}
                        disabled={busyAction !== null || !assembledDraft.trim()}
                        title="区切り線（---）を除去してコピー"
                      >
                        ---なしでコピー
                      </button>
                      <span className="muted small-text">
                        {copyAssembledNoSepStatus === "copied"
                          ? "コピーしました"
                          : copyAssembledNoSepStatus === "error"
                            ? "コピーに失敗しました"
                            : ""}
                      </span>
                      <button
                        type="button"
                        className="workspace-button workspace-button--ghost workspace-button--sm"
                        onClick={() => {
                          setAssembledDraft(assembledAiContent);
                          setMessage("AI版を人間編集版へコピーしました。");
                        }}
                        disabled={busyAction !== null}
                      >
                        ↑ AI版をコピー
                      </button>
                    </div>
                  </header>

                  <textarea
                    className="script-editor__textarea"
                    value={assembledDraft}
                    onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setAssembledDraft(event.target.value)}
                    aria-label="人間編集版の表示用テキスト"
                    placeholder="人間編集版の台本を入力してください"
                  />
                </div>

                {/* B' テキスト 人間版 */}
                <div className="script-editor-card">
                  <header className="script-editor__header">
                    <div>
                      <h3>Bテキスト（音声用）</h3>
                      <p className="script-editor__hint">耳で聴く内容はAと同一にしてください</p>
                    </div>
                    <div className="script-editor__meta" aria-live="polite">
                      <span className="script-editor__counter">
                        文字数: {ttsDraft.replace(/\r/g, "").replace(/\n/g, "").length.toLocaleString("ja-JP")}
                      </span>
                      <button
                        type="button"
                        className="workspace-button workspace-button--ghost workspace-button--sm"
                        onClick={() => void handleCopyTtsWithoutSeparators()}
                        disabled={busyAction !== null || !ttsDraft.trim()}
                        title="区切り線（---）を除去してコピー"
                      >
                        ---なしでコピー
                      </button>
                      <span className="muted small-text">
                        {copyTtsNoSepStatus === "copied"
                          ? "コピーしました"
                          : copyTtsNoSepStatus === "error"
                            ? "コピーに失敗しました"
                            : ""}
                      </span>
                      <button
                        type="button"
                        className="workspace-button workspace-button--ghost workspace-button--sm"
                        onClick={() => {
                          setTtsDraft(ttsAiContent);
                          setMessage("AI版を人間編集版へコピーしました。");
                        }}
                        disabled={busyAction !== null}
                      >
                        ↑ AI版をコピー
                      </button>
                    </div>
                  </header>

                  <textarea
                    ref={ttsTextareaRef}
                    className="script-editor__textarea"
                    value={ttsDraft}
                    onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setTtsDraft(event.target.value)}
                    aria-label="音声用テキスト（人間編集版）"
                    placeholder="音声読み上げ用テキスト（人間編集版）を入力してください"
                  />
                </div>
              </div>
            </div>

            {/* 共通の保存エリア */}
            <div className="script-save-area">
              <div className="script-editor__messages" aria-live="polite">
                {humanLoading && <p className="muted">台本を読み込み中です…</p>}
                {humanError && <p className="error">{humanError}</p>}
                {message && <p className="success">{message}</p>}
                {error && <p className="error">{error}</p>}
              </div>

              <div className="script-editor__actions script-editor__actions--horizontal">
                <button
                  type="button"
                  className="workspace-button workspace-button--primary"
                  onClick={() => void handleSaveBothScripts()}
                  disabled={busyAction !== null || (!assembledDirty && !audioDirty && audioReviewed === audioReviewedBase)}
                >
                  {assembledDirty || audioDirty || audioReviewed !== audioReviewedBase ? "変更を保存" : "保存済み"}
                </button>
              </div>
            </div>
          </section>
        )}


        {activeTab === "audio" && (
          <div className="detail-tab-panel detail-tab-panel--audio" role="tabpanel">
            <AudioWorkspace
              detail={detail}
              handlers={audioWorkspaceHandlers}
              refreshing={refreshing}
              onDirtyChange={onDirtyChange}
              showSrtColumn
              title="音声生成・確認"
              hint="最終WAV/SRT/ログの確認と字幕チェックができます。右側で確定SRTを直接編集・保存できます。"
            />
            {audioHistoryAvailable ? (
              <CollapseCard
                title="音声生成履歴"
                open={showAudioHistory}
                onToggle={(open: boolean) => setShowAudioHistory(open)}
              >
                <div className="audio-history">
                  <ul>
                    {audioHistory.map((entry, index) => {
                      const eventNode = entry.event ? <span className="audio-history__event">{entry.event}</span> : null;
                      const statusNode = entry.status ? (
                        <span className={`audio-history__status audio-history__status--${String(entry.status)}`}>
                          {String(entry.status)}
                        </span>
                      ) : null;
                      const messageNode = entry.message ? (
                        <p className="audio-history__message">{String(entry.message)}</p>
                      ) : null;
                      const links = [
                        entry.final_wav ? (
                          <a className="link" href={`/${entry.final_wav}`} target="_blank" rel="noreferrer">
                            音声ファイル
                          </a>
                        ) : null,
                        entry.final_srt ? (
                          <a className="link" href={`/${entry.final_srt}`} target="_blank" rel="noreferrer">
                            字幕ファイル
                          </a>
                        ) : null,
                        entry.log_json ? (
                          <a className="link" href={`/${entry.log_json}`} target="_blank" rel="noreferrer">
                            ログ(JSON)
                          </a>
                        ) : null,
                        entry.log_text ? (
                          <a className="link" href={`/${entry.log_text}`} target="_blank" rel="noreferrer">
                            ログ(TXT)
                          </a>
                        ) : null,
                      ].filter(Boolean) as ReactNode[];
                      return (
                        <li key={`history-${index}-${entry.event ?? ""}`}>
                          <div className="audio-history__header">
                            <span className="audio-history__time">{formatHistoryTimestamp(entry.timestamp)}</span>
                            {eventNode}
                            {statusNode}
                          </div>
                          {messageNode}
                          {links.length > 0 ? (
                            <div className="audio-history__links">
                              {links.map((linkNode, linkIndex) => (
                                <span key={`history-${index}-link-${linkIndex}`} className="audio-history__link">
                                  {linkNode}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </CollapseCard>
            ) : null}
          </div>
        )}

        {activeTab === "video" && (
          <div className="detail-tab-panel detail-tab-panel--video" role="tabpanel">
            <section className="detail-section">
              <h3>動画（CapCut）</h3>
              <p className="muted">
                final SRT を基準に、AutoDraft（最短）またはプロジェクト管理（再実行/編集）へ進めます。
              </p>
              <ul>
                <li>音声: {detail.audio_url ? "READY" : "未生成"}</li>
                <li>SRT: {detail.srt_path ? "READY" : "未生成"}</li>
                <li>
                  推奨 project_id: <code>{episodeId}</code>
                </li>
              </ul>
              <div className="actions actions--compact" style={{ marginTop: 10 }}>
                <Link className="workspace-button workspace-button--primary" to={capcutDraftLink}>
                  AutoDraft（新規ドラフト）
                </Link>
                <Link className="workspace-button" to={videoProductionLink}>
                  プロジェクト管理
                </Link>
                <Link className="workspace-button workspace-button--ghost" to={workflowLink}>
                  制作フロー
                </Link>
              </div>
              {!detail.srt_path ? (
                <p className="muted" style={{ marginTop: 10 }}>
                  先に音声生成で SRT を作成してください（SoT: <code>workspaces/audio/final</code> / 互換: <code>audio_tts_v2/artifacts/final</code>）。
                </p>
              ) : null}
            </section>
          </div>
        )}

        {activeTab === "history" && (
          <div className="detail-tab-panel detail-tab-panel--history" role="tabpanel">
            <section className="detail-section">
              <h3>音声メタデータ</h3>
              {detail.audio_metadata ? (
                <div className="metadata-grid">
                  <pre className="metadata-json">{JSON.stringify(detail.audio_metadata, null, 2)}</pre>
                </div>
              ) : (
                <p className="muted">音声メタ情報が未登録です。</p>
              )}
            </section>
          </div>
        )}
      </div>

      <footer className="detail-footer">
        {refreshing && <span className="muted">最新情報を取得中…</span>}
        {busyAction && <span className="muted">{busyAction} を保存しています…</span>}
        {message && <span className="success">{message}</span>}
        {error && <span className="error">{error}</span>}
      </footer>
      {llmEditorOpen ? (
        <div className="modal-backdrop" onClick={() => setLlmEditorOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal__header">
              <h3>LLM Box</h3>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="workspace-button workspace-button--ghost" onClick={() => setLlmEditorOpen(false)}>
                  閉じる
                </button>
                <button
                  className="workspace-button workspace-button--primary"
                  onClick={() => void saveLlmEditor()}
                  disabled={
                    llmEditorSaving ||
                    llmEditorLoading ||
                    (llmEditorStatus === "ready" && llmEditorContent.trim().length === 0)
                  }
                >
                  {llmEditorSaving ? "保存中..." : "保存"}
                </button>
              </div>
            </header>
            <div className="modal__body" style={{ maxHeight: "70vh", overflow: "auto" }}>
              {llmEditorLoading ? <p>読み込み中…</p> : null}
              {llmEditorError ? <p className="error">{llmEditorError}</p> : null}
              {llmEditorArtifact ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <div className="muted small-text" style={{ wordBreak: "break-all" }}>
                    {llmEditorName ? (
                      <div>
                        <strong>artifact:</strong> <code>{llmEditorName}</code>
                      </div>
                    ) : null}
                    <div>
                      <strong>stage/task:</strong> {llmEditorArtifact.stage} / {llmEditorArtifact.task}
                    </div>
                    {llmEditorArtifact.output?.path ? (
                      <div>
                        <strong>output:</strong> <code>{llmEditorArtifact.output.path}</code>
                      </div>
                    ) : null}
                  </div>
                  <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                    <label className="muted small-text">
                      status{" "}
                      <select
                        value={llmEditorStatus}
                        onChange={(event) => setLlmEditorStatus(event.target.value as "pending" | "ready")}
                        disabled={llmEditorSaving || llmEditorLoading}
                      >
                        <option value="pending">pending</option>
                        <option value="ready">ready</option>
                      </select>
                    </label>
                    <label className="muted small-text">
                      <input
                        type="checkbox"
                        checked={llmEditorApplyOutput}
                        onChange={(event) => setLlmEditorApplyOutput(event.target.checked)}
                        disabled={llmEditorSaving || llmEditorLoading || llmEditorStatus !== "ready"}
                      />{" "}
                      出力ファイルへ反映（推奨）
                    </label>
                    <span className="muted small-text">
                      文字数: {llmEditorContent.replace(/\r/g, "").replace(/\n/g, "").length.toLocaleString("ja-JP")}
                    </span>
                  </div>
                  {llmEditorStatus === "ready" && llmEditorContent.trim().length === 0 ? (
                    <p className="error small-text">status=ready の場合は content が必須です。</p>
                  ) : null}
                  <textarea
                    className="script-editor__textarea"
                    value={llmEditorContent}
                    onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setLlmEditorContent(event.target.value)}
                    aria-label="LLM Box content"
                    placeholder="ここに内容を貼り付け/編集してください"
                    style={{ minHeight: "40vh" }}
                    disabled={llmEditorSaving || llmEditorLoading}
                  />
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
      {aTextModalOpen ? (
        <div className="modal-backdrop" onClick={() => setATextModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal__header">
              <h3>Aテキスト</h3>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                  className="workspace-button workspace-button--ghost"
                  onClick={() => void handleCopyATextModalWithoutSeparators()}
                  disabled={!aTextModalContent.trim()}
                  title="区切り線（---）を除去してコピー"
                >
                  ---なしでコピー
                </button>
                <span className="muted small-text">
                  {copyATextNoSepStatus === "copied" ? "コピーしました" : copyATextNoSepStatus === "error" ? "失敗" : ""}
                </span>
                <button className="workspace-button workspace-button--ghost" onClick={() => setATextModalOpen(false)}>
                  閉じる
                </button>
              </div>
            </header>
            <div className="modal__body" style={{ maxHeight: "60vh", overflow: "auto" }}>
              {aTextModalLoading ? <p>読み込み中…</p> : null}
              {aTextModalError ? <p className="error">{aTextModalError}</p> : null}
              {aTextModalContent ? <pre className="code-block" style={{ whiteSpace: "pre-wrap" }}>{aTextModalContent}</pre> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CollapseCard({
  title,
  subtitle,
  children,
  open,
  onToggle,
  highlight = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  open: boolean;
  onToggle: (open: boolean) => void;
  highlight?: boolean;
}) {
  return (
    <div className={`panel-card collapse-card${highlight ? " collapse-card--highlight" : ""}`}>
      <header className="panel-card__header collapse-card__header">
        <button type="button" className="collapse-card__toggle" onClick={() => onToggle(!open)} aria-expanded={open}>
          <span className="collapse-card__icon">{open ? "−" : "+"}</span>
          <span className="collapse-card__title">{title}</span>
          {subtitle && <span className="collapse-card__subtitle">{subtitle}</span>}
        </button>
      </header>
      {open && <div className="collapse-card__body">{children}</div>}
    </div>
  );
}
