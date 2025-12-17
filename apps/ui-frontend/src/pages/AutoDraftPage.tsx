import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createAutoDraft,
  fetchAutoDraftSrts,
  fetchVideoProductionChannels,
  fetchChannelPreset,
  fetchAutoDraftPromptTemplates,
  fetchAutoDraftPromptTemplateContent,
  fetchAutoDraftSrtContent,
  updateAutoDraftSrtContent,
} from "../api/client";
import {
  AutoDraftCreatePayload,
  AutoDraftCreateResponse,
  AutoDraftSrtItem,
  AutoDraftSrtContent,
  VideoProductionChannelPreset,
} from "../api/types";

type FormState = {
  srtPath: string;
  channel: string;
  runName: string;
  capcutTemplate: string;
  promptTemplate: string;
  beltMode: "llm";
  imgDuration: number;
};

type RunState = {
  submitting: boolean;
  error: string | null;
  result: AutoDraftCreateResponse | null;
};

const initialForm: FormState = {
  srtPath: "",
  channel: "",
  runName: "",
  capcutTemplate: "",
  promptTemplate: "",
  beltMode: "llm",
  imgDuration: 20,
};

function deriveDefaults(item: AutoDraftSrtItem): Partial<FormState> {
  const rel = item.name || item.path;
  const parts = rel.split("/");
  const stem = rel.split("/").pop() || "";
  const base = stem.replace(/\.srt$/i, "");
  const guessedChannel = parts[0] || "";
  return {
    srtPath: item.path,
    channel:
      guessedChannel.startsWith("CH") && guessedChannel.length >= 4 && /^\d+$/.test(guessedChannel.slice(2, 4))
        ? guessedChannel.slice(0, 4)
        : guessedChannel,
    runName: base,
    promptTemplate: "",
  };
}

export function AutoDraftPage() {
  const [searchParams] = useSearchParams();
  const shortName = (p: string) => {
    if (!p) return "";
    const parts = p.split(/[\\/]/);
    return parts[parts.length - 1] || p;
  };
  const [srts, setSrts] = useState<AutoDraftSrtItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState<FormState>(initialForm);
  const [runState, setRunState] = useState<RunState>({ submitting: false, error: null, result: null });
  const [toast, setToast] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [channelOptions, setChannelOptions] = useState<VideoProductionChannelPreset[]>([]);
  const [channelOptionsStatus, setChannelOptionsStatus] = useState<"idle" | "loading" | "error" | "ready">("idle");
  const [channelOptionsError, setChannelOptionsError] = useState<string | null>(null);
  const [presetCapcutTemplate, setPresetCapcutTemplate] = useState<string | null>(null);
  const [presetPromptTemplate, setPresetPromptTemplate] = useState<string | null>(null);
  const [promptTemplateOptions, setPromptTemplateOptions] = useState<{ name: string; path: string }[]>([]);
  const [promptPreview, setPromptPreview] = useState<{ status: "idle" | "loading" | "ready" | "error"; content: string; path: string; error?: string }>({
    status: "idle",
    content: "",
    path: "",
    error: undefined,
  });
  const [srtPreview, setSrtPreview] = useState<{ status: "idle" | "loading" | "ready" | "error"; content: string; path: string; error?: string; meta?: string }>({
    status: "idle",
    content: "",
    path: "",
    error: undefined,
    meta: "",
  });
  const [srtFilter, setSrtFilter] = useState("");
  const [showLineNumbers, setShowLineNumbers] = useState(false);
  const [showFullSrt, setShowFullSrt] = useState(false);
  const [srtEdit, setSrtEdit] = useState("");
  const [savingSrt, setSavingSrt] = useState(false);
  const [showCapcutManual, setShowCapcutManual] = useState(false);
  const [showPromptManual, setShowPromptManual] = useState(false);
  const appliedInitialSelectionRef = useRef(false);

  const finalCapcutTemplate = useMemo(() => {
    return form.capcutTemplate.trim() || presetCapcutTemplate || "";
  }, [form.capcutTemplate, presetCapcutTemplate]);

  const filteredSrts = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    if (!keyword) return srts;
    return srts.filter((item) => item.name.toLowerCase().includes(keyword) || item.path.toLowerCase().includes(keyword));
  }, [filter, srts]);

  const templateOptions = useMemo(() => {
    const set = new Set<string>();
    channelOptions.forEach((c) => {
      if (c.capcutTemplate) set.add(c.capcutTemplate);
    });
    if (presetCapcutTemplate) set.add(presetCapcutTemplate);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [channelOptions, presetCapcutTemplate]);

  const promptOptions = useMemo(() => promptTemplateOptions, [promptTemplateOptions]);

  const groupedSrts = useMemo(() => {
    const groups: Record<string, AutoDraftSrtItem[]> = {};
    filteredSrts.forEach((item) => {
      const key = item.name.includes("/") ? item.name.split("/")[0] : "(root)";
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    });
    return Object.entries(groups)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, items]) => ({
        key,
        items: items.sort((a, b) => a.name.localeCompare(b.name)),
      }));
  }, [filteredSrts]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const [srtsResp, channelsResp, promptList] = await Promise.all([
          fetchAutoDraftSrts(),
          fetchVideoProductionChannels(false),
          fetchAutoDraftPromptTemplates(),
        ]);
        setSrts(srtsResp.items);
        setChannelOptions(channelsResp);
        setChannelOptionsStatus("ready");
        setPromptTemplateOptions(promptList.items || []);
        setOpenGroups(new Set(srtsResp.items.map((item) => (item.name.includes("/") ? item.name.split("/")[0] : "(root)"))));
      } catch (err: any) {
        setChannelOptionsStatus("error");
        setChannelOptionsError(err?.message || "取得に失敗しました");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  useEffect(() => {
    if (appliedInitialSelectionRef.current) return;
    if (srts.length === 0) return;

    const querySrtRaw = (searchParams.get("srt") || searchParams.get("srtPath") || "").trim();
    const queryChannel = (searchParams.get("channel") || "").trim().toUpperCase();
    const queryVideoRaw = (searchParams.get("video") || "").trim();
    const queryVideo = /^\d+$/.test(queryVideoRaw) ? queryVideoRaw.padStart(3, "0") : queryVideoRaw;

    const normalizePath = (value: string) => value.replace(/\\/g, "/").toLowerCase();

    let desired: AutoDraftSrtItem | undefined;
    if (querySrtRaw) {
      const needle = normalizePath(querySrtRaw);
      desired = srts.find((item) => {
        const name = normalizePath(item.name || "");
        const path = normalizePath(item.path || "");
        return name === needle || path === needle || name.endsWith(needle) || path.endsWith(needle);
      });
    }
    if (!desired && queryChannel && queryVideo) {
      const expected = normalizePath(`${queryChannel}/${queryVideo}/${queryChannel}-${queryVideo}.srt`);
      desired = srts.find((item) => normalizePath(item.name) === expected);
    }

    const chosen = desired ?? srts[0];
    if (chosen) {
      const defaults = deriveDefaults(chosen);
      setForm((prev) => ({ ...prev, ...defaults, channel: defaults.channel || queryChannel || prev.channel }));
      setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
    }

    appliedInitialSelectionRef.current = true;
  }, [srts, searchParams]);

  // fetch preset info when channel changes
  useEffect(() => {
    const ch = form.channel.trim();
    if (!ch) {
      setPresetCapcutTemplate(null);
      setPresetPromptTemplate(null);
      setForm((prev) => ({ ...prev, capcutTemplate: "", promptTemplate: "" }));
      setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
      setShowCapcutManual(false);
      setShowPromptManual(false);
      return;
    }
    let cancelled = false;
    fetchChannelPreset(ch)
      .then((preset) => {
        if (cancelled) return;
        setPresetCapcutTemplate(preset.capcutTemplate ?? null);
        setPresetPromptTemplate(preset.promptTemplate ?? null);
        setForm((prev) => ({
          ...prev,
          capcutTemplate: preset.capcutTemplate ?? "",
          promptTemplate: preset.promptTemplate ?? "",
        }));
        setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
      })
      .catch(() => {
        if (cancelled) return;
        setPresetCapcutTemplate(null);
        setPresetPromptTemplate(null);
        setForm((prev) => ({ ...prev, capcutTemplate: "", promptTemplate: "" }));
        setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.channel]);

  const handleSelect = (item: AutoDraftSrtItem) => {
    setRunState({ submitting: false, error: null, result: null });
    const defaults = deriveDefaults(item);
    setForm((prev) => ({ ...prev, ...defaults }));
    setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
  };

  const currentPromptPath = useMemo(() => {
    return form.promptTemplate.trim() || presetPromptTemplate || "";
  }, [form.promptTemplate, presetPromptTemplate]);

  useEffect(() => {
    const target = currentPromptPath;
    if (!target) {
      setPromptPreview({ status: "idle", content: "", path: "", error: undefined });
      return;
    }
    let cancelled = false;
    setPromptPreview({ status: "loading", content: "", path: target, error: undefined });
    fetchAutoDraftPromptTemplateContent(target)
      .then((res) => {
        if (cancelled) return;
        setPromptPreview({ status: "ready", content: res.content, path: res.path, error: undefined });
      })
      .catch((err: any) => {
        if (cancelled) return;
        setPromptPreview({
          status: "error",
          content: "",
          path: target,
          error: err?.message || "テンプレ取得に失敗しました",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [currentPromptPath]);

  useEffect(() => {
    const target = form.srtPath;
    if (!target) {
      setSrtPreview({ status: "idle", content: "", path: "", error: undefined, meta: "" });
      setSrtFilter("");
      setSrtEdit("");
      return;
    }
    let cancelled = false;
    setSrtPreview({ status: "loading", content: "", path: target, error: undefined, meta: "" });
    fetchAutoDraftSrtContent(target)
      .then((res: AutoDraftSrtContent) => {
        if (cancelled) return;
        const meta = [
          res.sizeBytes != null ? `${(res.sizeBytes / 1024).toFixed(1)} KB` : null,
          res.modifiedTime ? new Date(res.modifiedTime * 1000).toLocaleString() : null,
        ]
          .filter(Boolean)
          .join(" / ");
        setSrtPreview({ status: "ready", content: res.content, path: res.path, error: undefined, meta });
        setSrtEdit(res.content);
      })
      .catch((err: any) => {
        if (cancelled) return;
        setSrtPreview({
          status: "error",
          content: "",
          path: target,
          error: err?.message || "SRTプレビュー取得に失敗しました",
          meta: "",
        });
        setSrtEdit("");
      });
    return () => {
      cancelled = true;
    };
  }, [form.srtPath]);

  const srtView = useMemo(() => {
    if (srtPreview.status === "ready") {
      const keyword = srtFilter.trim().toLowerCase();
      const lines = srtPreview.content.split(/\r?\n/);
      const filtered = keyword ? lines.filter((line) => line.toLowerCase().includes(keyword)) : lines;
      if (!filtered.length) {
        return { text: keyword ? "フィルタに一致する行がありません" : "空のSRTです", lines: [] };
      }
      const limited = showFullSrt ? filtered : filtered.slice(0, 200);
      const body = limited.map((line, idx) => (showLineNumbers ? `${idx + 1}: ${line}` : line)).join("\n");
      const omitted = !showFullSrt && filtered.length > limited.length ? `\n… (${filtered.length - limited.length} 行省略)` : "";
      return { text: body + omitted, lines: limited };
    }
    if (srtPreview.status === "loading") return { text: "読み込み中...", lines: [] };
    if (srtPreview.status === "error") return { text: srtPreview.error ?? "プレビュー取得に失敗しました", lines: [] };
    return { text: "SRTを選択すると内容を表示します", lines: [] };
  }, [srtFilter, showFullSrt, showLineNumbers, srtPreview]);

  const handleSubmit = async () => {
    if (!form.srtPath) {
      setRunState({ submitting: false, error: "SRTを選択してください", result: null });
      return;
    }
    if (!form.channel) {
      setRunState({ submitting: false, error: "チャンネルを選択してください", result: null });
      return;
    }
    const srtStem = form.srtPath ? (form.srtPath.split(/[\\/]/).pop() || "").replace(/\.srt$/i, "") : "draft";
    const runName = form.runName?.trim() || `${srtStem}_${Date.now()}`;
    const payload: AutoDraftCreatePayload = { srtPath: form.srtPath, channel: form.channel, runName };
    const tmpl = form.capcutTemplate.trim() || presetCapcutTemplate || "";
    if (tmpl) {
      payload.template = tmpl;
    }
    const promptTmpl = form.promptTemplate.trim() || presetPromptTemplate || "";
    if (promptTmpl) {
      payload.promptTemplate = promptTmpl;
    }
    payload.beltMode = "llm";
    if (form.imgDuration) {
      payload.imgDuration = form.imgDuration;
    }
    setRunState({ submitting: true, error: null, result: null });
    setToast("ドラフトを作成中...");
    try {
      const result = await createAutoDraft(payload);
      setRunState({ submitting: false, error: null, result });
      setToast("完了");
    } catch (error: any) {
      setRunState({ submitting: false, error: error?.message || "ドラフト作成に失敗しました", result: null });
      setToast("");
    }
  };
  const handleCopySrt = async () => {
    if (srtPreview.status !== "ready" || !srtPreview.content) return;
    try {
      await navigator.clipboard.writeText(srtPreview.content);
      setToast("SRTをコピーしました");
    } catch (err: any) {
      setToast(err?.message || "コピーに失敗しました");
    }
  };

  const handleSaveSrt = async () => {
    if (!form.srtPath) {
      setToast("SRTが未選択です");
      return;
    }
    setSavingSrt(true);
    setToast("SRTを保存中...");
    try {
      const res = await updateAutoDraftSrtContent(form.srtPath, srtEdit);
      setSrtPreview({
        status: "ready",
        content: res.content,
        path: res.path,
        error: undefined,
        meta:
          [
            res.sizeBytes != null ? `${(res.sizeBytes / 1024).toFixed(1)} KB` : null,
            res.modifiedTime ? new Date(res.modifiedTime * 1000).toLocaleString() : null,
          ]
            .filter(Boolean)
            .join(" / ") || "",
      });
      setToast("SRTを保存しました");
    } catch (err: any) {
      setToast(err?.message || "SRT保存に失敗しました");
    } finally {
      setSavingSrt(false);
    }
  };

  const renderSrtLine = (line: string, idx: number) => {
    const arrow = " --> ";
    const tsMatch = line.match(
      /^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})(.*)$/i
    );
    if (tsMatch) {
      return (
        <span>
          <span style={{ color: "#0f172a", fontWeight: 700 }}>{tsMatch[1]}</span>
          <span style={{ color: "#94a3b8" }}>{arrow}</span>
          <span style={{ color: "#0f172a", fontWeight: 700 }}>{tsMatch[2]}</span>
          <span style={{ color: "#475569" }}>{tsMatch[3]}</span>
        </span>
      );
    }
    if (/^\d+$/.test(line.trim())) {
      return <span style={{ color: "#475569" }}>{line}</span>;
    }
    return <span style={{ color: "#0f172a" }}>{line || "\u00A0"}</span>;
  };

  const handleRefresh = async () => {
    setToast("更新中...");
    try {
      const data = await fetchAutoDraftSrts();
      setSrts(data.items);
      setOpenGroups(new Set(data.items.map((item) => (item.name.includes("/") ? item.name.split("/")[0] : "(root)"))));
      setToast("更新しました");
    } catch (error: any) {
      setToast(error?.message || "更新に失敗しました");
    }
  };

  const logText = useMemo(() => {
    const parts: string[] = [];
    if (runState.error) {
      parts.push(`[error]\n${runState.error}`);
    }
    if (runState.result?.stdout) {
      parts.push(`[stdout]\n${runState.result.stdout}`);
    }
    if (runState.result?.stderr) {
      parts.push(`[stderr]\n${runState.result.stderr}`);
    }
    return parts.join("\n\n");
  }, [runState]);

  const liveLogText = useMemo(() => {
    if (runState.submitting) {
      return "実行中...\nサーバーからのログ待ちです。完了後に詳細ログが表示されます。";
    }
    return logText || "ログなし";
  }, [logText, runState.submitting]);

  return (
    <div style={{ padding: 20, display: "grid", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>CapCutドラフト作成</h2>
        {toast && (
          <span style={{ fontSize: 12, background: "#eef2ff", color: "#1e1b4b", padding: "4px 8px", borderRadius: 8 }}>
            {toast}
          </span>
        )}
      </div>
      <div
        style={{
          border: "1px solid #e5e7eb",
          background: "#f8fafc",
          color: "#0f172a",
          borderRadius: 10,
          padding: "10px 12px",
          fontSize: 13,
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontWeight: 700 }}>手順:</span>
        <span>1) SRT選択</span>
        <span>2) チャンネル選択</span>
        <span>3) CapCutテンプレ</span>
        <span>4) 画風テンプレ</span>
        <span>→ 実行</span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(320px, 1fr) 2fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <div>
              <strong>SRTを選択</strong>
              <div style={{ fontSize: 12, color: "#64748b" }}>
                SoT: workspaces/audio/final（互換: audio_tts_v2/artifacts/final）配下の .srt を列挙します
              </div>
            </div>
            <button
              onClick={handleRefresh}
              style={{
                border: "none",
                borderRadius: 8,
                padding: "8px 12px",
                background: "#0f172a",
                color: "#fff",
                fontWeight: 700,
                cursor: "pointer",
                boxShadow: "0 4px 10px rgba(15,23,42,0.15)",
              }}
            >
              再読み込み
            </button>
          </div>
          <input
            type="text"
            placeholder="ファイル名で絞り込み"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1" }}
          />
          {loading ? (
            <div style={{ color: "#94a3b8" }}>読み込み中...</div>
          ) : filteredSrts.length === 0 ? (
            <div style={{ color: "#ef4444" }}>workspaces/audio/final（互換: audio_tts_v2/artifacts/final）に SRT が見つかりません</div>
          ) : (
            <div style={{ display: "grid", gap: 8, maxHeight: 520, overflow: "auto" }}>
              {groupedSrts.map((group) => {
                const isOpen = openGroups.has(group.key);
                return (
                  <div key={group.key} style={{ border: "1px solid #e5e7eb", borderRadius: 10, background: "#fff" }}>
                    <button
                      onClick={() =>
                        setOpenGroups((prev) => {
                          const next = new Set(prev);
                          if (next.has(group.key)) next.delete(group.key);
                          else next.add(group.key);
                          return next;
                        })
                      }
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "10px 12px",
                        border: "none",
                        background: "#f8fafc",
                        borderRadius: "10px 10px 0 0",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontWeight: 800, color: "#0f172a" }}>{group.key}</span>
                        <span style={{ fontSize: 12, color: "#475569" }}>{group.items.length}件</span>
                      </div>
                      <span style={{ fontSize: 12, color: "#475569" }}>{isOpen ? "▲" : "▼"}</span>
                    </button>
                    {isOpen && (
                      <div style={{ padding: 10, display: "grid", gap: 8 }}>
                        {group.items.map((item) => {
                          const active = item.path === form.srtPath;
                          return (
                            <button
                              key={item.path}
                              onClick={() => handleSelect(item)}
                              style={{
                                textAlign: "left",
                                border: "1px solid " + (active ? "#312e81" : "#e5e7eb"),
                                background: active ? "#eef2ff" : "#ffffff",
                                borderRadius: 10,
                                padding: "10px 12px",
                                cursor: "pointer",
                                boxShadow: active ? "0 4px 10px rgba(49,46,129,0.12)" : "none",
                              }}
                            >
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                                <div style={{ fontWeight: 800, color: "#0f172a" }}>{item.name}</div>
                                {active && (
                                  <span
                                    style={{
                                      fontSize: 11,
                                      padding: "2px 8px",
                                      borderRadius: 8,
                                      background: "#c7d2fe",
                                      color: "#1e1b4b",
                                      fontWeight: 700,
                                    }}
                                  >
                                    選択中
                                  </span>
                                )}
                              </div>
                              <div style={{ fontSize: 12, color: "#475569", wordBreak: "break-all" }}>{item.path}</div>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <div>
              <strong>SRTプレビュー</strong>
              <div style={{ fontSize: 12, color: "#64748b" }}>{form.srtPath || "未選択"}</div>
              {srtPreview.meta && <div style={{ fontSize: 11, color: "#94a3b8" }}>{srtPreview.meta}</div>}
            </div>
            {srtPreview.status === "loading" && <span style={{ fontSize: 12, color: "#475569" }}>読み込み中...</span>}
            {srtPreview.status === "error" && <span style={{ fontSize: 12, color: "#b91c1c" }}>{srtPreview.error}</span>}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="text"
              placeholder="キーワードで絞り込み"
              value={srtFilter}
              onChange={(e) => setSrtFilter(e.target.value)}
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1", minWidth: 220 }}
            />
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#334155" }}>
              <input type="checkbox" checked={showLineNumbers} onChange={(e) => setShowLineNumbers(e.target.checked)} />
              行番号を表示
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#334155" }}>
              <input type="checkbox" checked={showFullSrt} onChange={(e) => setShowFullSrt(e.target.checked)} />
              全文表示（デフォルト200行）
            </label>
            <button
              type="button"
              onClick={handleCopySrt}
              disabled={srtPreview.status !== "ready"}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid #cbd5e1",
                background: srtPreview.status === "ready" ? "#f8fafc" : "#e5e7eb",
                color: "#0f172a",
                cursor: srtPreview.status === "ready" ? "pointer" : "not-allowed",
              }}
            >
              コピー
            </button>
          </div>
          <div
            style={{
              border: "1px solid #e5e7eb",
              borderRadius: 10,
              background: "#f8fafc",
              padding: 10,
              minHeight: 160,
              maxHeight: 320,
              overflow: "auto",
              fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
            }}
          >
            {srtView.lines.length
              ? srtView.lines.map((line, idx) => (
                  <div key={`${line}-${idx}`} style={{ lineHeight: 1.4 }}>
                    {renderSrtLine(line, idx)}
                  </div>
                ))
              : srtView.text}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 12, color: "#334155" }}>SRT編集（保存すると上書き）</div>
            <textarea
              value={srtEdit}
              onChange={(e) => setSrtEdit(e.target.value)}
              style={{
                width: "100%",
                minHeight: 200,
                borderRadius: 10,
                border: "1px solid #cbd5e1",
                padding: 10,
                fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                fontSize: 12,
                background: "#fff",
              }}
            />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={handleSaveSrt}
                disabled={savingSrt || srtPreview.status !== "ready"}
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  border: "none",
                  background: savingSrt ? "#e5e7eb" : "#0f172a",
                  color: "#fff",
                  cursor: savingSrt || srtPreview.status !== "ready" ? "not-allowed" : "pointer",
                  fontWeight: 700,
                }}
              >
                {savingSrt ? "保存中..." : "この内容で保存"}
              </button>
              <button
                type="button"
                onClick={() => setSrtEdit(srtPreview.content)}
                disabled={srtPreview.status !== "ready"}
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  border: "1px solid #cbd5e1",
                  background: "#f8fafc",
                  color: "#0f172a",
                  cursor: srtPreview.status === "ready" ? "pointer" : "not-allowed",
                  fontWeight: 600,
                }}
              >
                プレビュー内容でリセット
              </button>
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div>
                <strong>最小入力</strong>
                <div style={{ fontSize: 12, color: "#64748b" }}>SRT → チャンネル → 実行（run_name/テンプレは自動）</div>
              </div>
            </div>

            <div style={{ display: "grid", gap: 10, border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "#fff" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 12 }}>チャンネルを選択（プリセット反映）</div>
                {channelOptionsStatus === "loading" && <span style={{ fontSize: 12, color: "#64748b" }}>取得中...</span>}
                {channelOptionsStatus === "error" && <span style={{ fontSize: 12, color: "#b91c1c" }}>{channelOptionsError}</span>}
              </div>
              <select
                value={form.channel}
                onChange={(e) => setForm((prev) => ({ ...prev, channel: e.target.value }))}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
              >
                <option value="">選択してください</option>
                {channelOptions.map((ch) => (
                  <option key={ch.channelId} value={ch.channelId}>
                    {ch.channelId} {ch.name ? `- ${ch.name}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: "grid", gap: 10, border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "#fff" }}>
              <div style={{ fontWeight: 700, fontSize: 12 }}>CapCutテンプレート</div>
              <div style={{ fontSize: 11, color: "#475569" }}>
                プリセット: {shortName(presetCapcutTemplate || "") || "なし"} / 現在: {shortName(finalCapcutTemplate) || "(未設定)"}
              </div>
              <select
                value={showCapcutManual ? "__custom__" : form.capcutTemplate || "__preset__"}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__preset__") {
                    setShowCapcutManual(false);
                    setForm((prev) => ({ ...prev, capcutTemplate: "" }));
                  } else if (v === "__custom__") {
                    setShowCapcutManual(true);
                    setForm((prev) => ({ ...prev, capcutTemplate: prev.capcutTemplate || "" }));
                  } else {
                    setShowCapcutManual(false);
                    setForm((prev) => ({ ...prev, capcutTemplate: v }));
                  }
                }}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
              >
                <option value="__preset__">プリセットを使う {presetCapcutTemplate ? `(${shortName(presetCapcutTemplate)})` : ""}</option>
                {templateOptions.map((t) => (
                  <option key={t} value={t}>
                    {shortName(t)}
                  </option>
                ))}
                <option value="__custom__">カスタム入力</option>
              </select>
              {showCapcutManual && (
                <input
                  type="text"
                  placeholder="直接指定（空欄ならプリセット/セレクトを使用）"
                  value={form.capcutTemplate}
                  onChange={(e) => setForm((prev) => ({ ...prev, capcutTemplate: e.target.value }))}
                  style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e5e7eb" }}
                />
              )}
            </div>

            <div style={{ display: "grid", gap: 10, border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "#fff" }}>
              <div style={{ fontWeight: 700, fontSize: 12 }}>画像の細かさ（1枚あたり秒数）</div>
              <div style={{ fontSize: 11, color: "#475569" }}>数値を小さくすると画像枚数が増えます（目標の平均秒数。帯の分割とは無関係）。</div>
              <input
                type="number"
                min={5}
                max={60}
                step={1}
                value={form.imgDuration}
                onChange={(e) => setForm((prev) => ({ ...prev, imgDuration: Number(e.target.value) || 20 }))}
                style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1", width: 140 }}
              />
            </div>

            <div style={{ display: "grid", gap: 10, border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "#fff" }}>
              <div style={{ fontWeight: 700, fontSize: 12 }}>画風テンプレート（prompt_template）</div>
              <div style={{ fontSize: 11, color: "#475569" }}>
                プリセット: {shortName(presetPromptTemplate || "") || "なし"} / 現在: {shortName(currentPromptPath) || "(未設定)"}
              </div>
              <select
                value={showPromptManual ? "__custom__" : form.promptTemplate || "__preset__"}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__preset__") {
                    setShowPromptManual(false);
                    setForm((prev) => ({ ...prev, promptTemplate: "" }));
                  } else if (v === "__custom__") {
                    setShowPromptManual(true);
                    setForm((prev) => ({ ...prev, promptTemplate: prev.promptTemplate || "" }));
                  } else {
                    setShowPromptManual(false);
                    setForm((prev) => ({ ...prev, promptTemplate: v }));
                  }
                }}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
              >
                <option value="__preset__">プリセットを使う {presetPromptTemplate ? `(${shortName(presetPromptTemplate)})` : ""}</option>
                {promptOptions.map((t) => (
                  <option key={t.path} value={t.path}>
                    {shortName(t.name || t.path)}
                  </option>
                ))}
                <option value="__custom__">カスタム入力</option>
              </select>
              {showPromptManual && (
                <input
                  type="text"
                  placeholder="直接指定（空欄ならプリセット/セレクトを使用）"
                  value={form.promptTemplate}
                  onChange={(e) => setForm((prev) => ({ ...prev, promptTemplate: e.target.value }))}
                  style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e5e7eb" }}
                />
              )}
              <div style={{ display: "grid", gap: 6, fontSize: 12, color: "#475569" }}>
                <span>使用中: {shortName(currentPromptPath) || "未選択"}</span>
                <div
                  style={{
                    border: "1px solid #cbd5e1",
                    background: "#f8fafc",
                    borderRadius: 10,
                    padding: 10,
                    maxHeight: 220,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {promptPreview.status === "idle" && <span style={{ color: "#94a3b8" }}>画風テンプレを選択すると内容が表示されます</span>}
                  {promptPreview.status === "loading" && <span style={{ color: "#475569" }}>読み込み中...</span>}
                  {promptPreview.status === "error" && <span style={{ color: "#b91c1c" }}>{promptPreview.error}</span>}
                  {promptPreview.status === "ready" && (
                    <div>
                      <div style={{ fontWeight: 700, marginBottom: 6 }}>{shortName(promptPreview.path)}</div>
                      <div style={{ fontSize: 11, color: "#94a3b8" }}>{promptPreview.path}</div>
                      <div style={{ color: "#334155" }}>{promptPreview.content}</div>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 帯はLLM自動のみを使用。grouped UIは非表示 */}

            <div style={{ display: "grid", gap: 8, border: "1px dashed #cbd5e1", borderRadius: 12, padding: 12, background: "#f8fafc" }}>
              <div style={{ fontWeight: 700, fontSize: 12, color: "#0f172a" }}>今回送る設定</div>
              <div style={{ fontSize: 13, color: "#0f172a" }}>run_name: {form.runName?.trim() || `${(form.srtPath.split(/[\\/]/).pop() || "draft").replace(/\\.srt$/i, "")}_${Date.now()}`}</div>
              <div style={{ fontSize: 13, color: "#0f172a" }}>CapCutテンプレ: {shortName(finalCapcutTemplate) || "(未設定)"}</div>
              <div style={{ fontSize: 13, color: "#0f172a" }}>画風テンプレ: {shortName(currentPromptPath) || "(未設定)"}</div>
              <div style={{ fontSize: 13, color: "#0f172a" }}>帯モード: {form.beltMode === "llm" ? "LLM自動" : "grouped（章JSON使用）"}</div>
            </div>

            <button
              onClick={handleSubmit}
              disabled={runState.submitting || !form.srtPath || !form.channel}
              style={{
                padding: "12px 14px",
                borderRadius: 10,
                border: "none",
                background: runState.submitting ? "#cbd5e1" : "#0f172a",
                color: "#fff",
                fontWeight: 800,
                cursor: runState.submitting || !form.srtPath || !form.channel ? "not-allowed" : "pointer",
                marginTop: 4,
              }}
            >
              {runState.submitting ? "実行中..." : "CapCutドラフトを作成"}
            </button>

            {runState.error && (
              <div style={{ color: "#b91c1c", background: "#fef2f2", border: "1px solid #fecdd3", borderRadius: 10, padding: 10 }}>
                {runState.error}
              </div>
            )}

            <div
              style={{
                border: "1px solid #e5e7eb",
                background: "#0f172a",
                color: "#e2e8f0",
                borderRadius: 12,
                padding: 12,
                display: "grid",
                gap: 6,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontWeight: 700, fontSize: 12, color: "#bae6fd" }}>ログ</div>
                {runState.submitting && (
                  <span style={{ fontSize: 11, color: "#cbd5e1" }}>ドラフト作成中...完了後に詳細が反映されます</span>
                )}
              </div>
              <pre
                style={{
                  margin: 0,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  lineHeight: 1.4,
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace",
                }}
              >
                {liveLogText}
              </pre>
            </div>
          </div>

          {runState.result && (
            <div
              style={{
                border: "1px solid #bbf7d0",
                background: "#f0fdf4",
                color: "#166534",
                borderRadius: 12,
                padding: 12,
                display: "grid",
                gap: 8,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontWeight: 800 }}>作成完了</div>
                <div style={{ fontSize: 12, color: "#16a34a" }}>CapCutでドラフトを確認できます</div>
              </div>
              <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
                <div>run_name: {runState.result.runName}</div>
                <div>チャンネル: {runState.result.channel}</div>
                <div>出力ディレクトリ: {runState.result.runDir}</div>
              </div>
            </div>
          )}
        </div>
      </div>
      {runState.submitting && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            zIndex: 999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#e2e8f0",
            backdropFilter: "blur(2px)",
          }}
        >
          <div
            style={{
              background: "#0f172a",
              border: "1px solid #475569",
              borderRadius: 12,
              padding: "18px 22px",
              minWidth: 260,
              boxShadow: "0 10px 30px rgba(0,0,0,0.3)",
            }}
          >
            <div style={{ fontWeight: 800, marginBottom: 8 }}>ドラフト作成中...</div>
            <div style={{ fontSize: 13, color: "#cbd5e1", lineHeight: 1.5 }}>
              サーバー処理中です。完了すると下部のログに詳細が反映されます。
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default AutoDraftPage;
