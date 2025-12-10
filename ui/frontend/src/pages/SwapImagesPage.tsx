import React, { useEffect, useState } from "react";

type SwapRequest = {
  draft_path: string;
  run_dir: string;
  indices: string;
  custom_prompt?: string | null;
  style_mode: string;
  only_allow_draft_substring: string;
  apply: boolean;
  validate_after: boolean;
  rollback_on_validate_fail: boolean;
};

type SwapResponse = { ok: boolean; log_path: string; stdout: string; stderr: string };
type ImageItem = {
  index: number;
  material_id: string;
  material_name: string;
  asset_path: string | null;
  start_ms?: number;
  duration_ms?: number;
  prompt?: string;
  prompt_source?: "snapshot" | "cues_only" | "missing";
  prompt_timestamp?: string;
};

const formatHM = (sec: number | null | undefined) => {
  if (sec == null || Number.isNaN(sec)) return "??:??";
  const total = Math.max(0, Math.floor(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
};

const formatDurationHM = (start: number | null, end: number | null) => {
  if (start == null || end == null) return "??:??";
  const d = Math.max(0, Math.floor(end - start));
  const h = Math.floor(d / 3600);
  const m = Math.floor((d % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
};
type DraftItem = { name: string; path: string };

const defaultReq: SwapRequest = {
  draft_path: "",
  run_dir: "",
  indices: "",
  custom_prompt: null,
  style_mode: "illustration",
  only_allow_draft_substring: "",
  apply: true,
  validate_after: true,
  rollback_on_validate_fail: true,
};

export const SwapImagesPage: React.FC = () => {
  const [form, setForm] = useState<SwapRequest>(defaultReq);
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [toast, setToast] = useState("");
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // 初期ロード: ドラフト一覧取得＋最初のドラフトを自動選択→run_dir推定→画像読み込み
  useEffect(() => {
    fetch(`${API_BASE}/api/swap/drafts`)
      .then((r) => r.json())
      .then((data) => {
        const list = data.items || [];
        setDrafts(list);
        const first = list[0];
        if (first) {
          setForm((f) => ({
            ...f,
            draft_path: first.path,
            only_allow_draft_substring: first.name,
          }));
          fetch(`${API_BASE}/api/swap/auto-run-dir?draft_name=${encodeURIComponent(first.name)}`)
            .then((res) => res.json())
            .then((rd) => setForm((f) => ({ ...f, run_dir: rd.run_dir || f.run_dir })))
            .finally(() => loadImages(first.path));
        }
      })
      .catch(() => setDrafts([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadImages = async (draftPath?: string) => {
    const draft = draftPath || form.draft_path;
    if (!draft) {
      setToast("ドラフトを選択してください");
      return;
    }
    setToast("読み込み中...");
    setImages([]);
    try {
      let runDir = form.run_dir;
      if (!runDir) {
        const nm = draft.split("/").pop() || "";
        const rd = await fetch(`${API_BASE}/api/swap/auto-run-dir?draft_name=${encodeURIComponent(nm)}`).then((r) => r.json());
        if (rd.run_dir) {
          runDir = rd.run_dir;
          setForm((f) => ({ ...f, run_dir: rd.run_dir }));
        }
      }
      const params = new URLSearchParams({ draft_path: draft });
      const res = await fetch(`${API_BASE}/api/swap/images/list?${params.toString()}`);
      if (!res.ok) {
        setToast("読み込み失敗");
        return;
      }
      let items: ImageItem[] = (await res.json()).items || [];
      const cuesByIdx: Record<number, string> = {};
      const snapsByIdx: Record<number, { prompt: string; timestamp: string }> = {};
      if (runDir) {
        try {
          const cues = await fetch(`${API_BASE}/api/swap/image-cues?run_dir=${encodeURIComponent(runDir)}`).then((r) => r.json());
          (cues.items || []).forEach((c: any) => {
            if (typeof c.index === "number") cuesByIdx[c.index] = c.prompt || c.raw_prompt || c.positive || "";
          });
        } catch {
          // ignore
        }
        try {
          const snaps = await fetch(`${API_BASE}/api/swap/prompt-snapshots?run_dir=${encodeURIComponent(runDir)}`).then((r) => r.json());
          (snaps.items || []).forEach((s: any) => {
            if (typeof s.index === "number") snapsByIdx[s.index] = { prompt: s.prompt || "", timestamp: s.timestamp || "" };
          });
        } catch {
          // ignore
        }
      }
      items = items.map((it) => {
        const snap = snapsByIdx[it.index];
        if (snap && snap.prompt) return { ...it, prompt: snap.prompt, prompt_source: "snapshot", prompt_timestamp: snap.timestamp };
        const cuePrompt = cuesByIdx[it.index];
        if (cuePrompt) return { ...it, prompt: cuePrompt, prompt_source: "cues_only" };
        return { ...it, prompt: "プロンプトは保存されていません（再生成すると保存されます）", prompt_source: "missing" };
      });
      setImages(items);
      setToast("読み込み完了");
    } catch (e: any) {
      setToast("error: " + e?.message);
    }
  };

  const runSwap = async (index: number) => {
    if (running) return;
    if (!form.draft_path || !form.run_dir) {
      setToast("ドラフトまたはrun_dirが未設定です");
      return;
    }
    setRunning(true);
    setToast(`カット#${index} 差し替え中...`);
    try {
      const payload: SwapRequest = {
        ...form,
        indices: String(index),
        custom_prompt: null,
        only_allow_draft_substring: form.only_allow_draft_substring || form.draft_path.split("/").pop() || "",
        apply: true,
      };
      const res = await fetch(`${API_BASE}/api/swap/images`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const text = await res.text();
      if (!res.ok) {
        setToast("失敗: " + text);
      } else {
        const data: SwapResponse = JSON.parse(text);
        setToast(`完了: log ${data.log_path}`);
        await loadImages();
      }
    } catch (e: any) {
      setToast("error: " + e?.message);
    } finally {
      setRunning(false);
    }
  };

  const rollbackLatest = async (index: number) => {
    if (!form.draft_path) return;
    setRunning(true);
    setToast(`カット#${index} を直前に戻しています...`);
    try {
      const hist = await fetch(`${API_BASE}/api/swap/images/history?${new URLSearchParams({ draft_path: form.draft_path, index: String(index), limit: "1" }).toString()}`).then((r) =>
        r.json()
      );
      const item = (hist.items || [])[0];
      if (!item) {
        setToast("履歴がありません");
      } else {
        const res = await fetch(`${API_BASE}/api/swap/images/rollback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ draft_path: form.draft_path, index, history_path: item.path }),
        });
        if (!res.ok) {
          const t = await res.text();
          setToast("戻し失敗: " + t);
        } else {
          setToast("直前に戻しました");
          await loadImages();
        }
      }
    } catch (e: any) {
      setToast("error: " + e?.message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div style={{ padding: 16, background: "#f8fafc", minHeight: "100vh" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>画像差し替え</h2>
        {toast && <span style={{ fontSize: 12, color: "#0f172a", background: "#eef2ff", padding: "4px 8px", borderRadius: 8 }}>{toast}</span>}
      </div>

      <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "#fff", marginBottom: 14, display: "grid", gap: 8 }}>
        <label>
          ドラフト
          <select
            value={form.draft_path}
            onChange={(e) => {
              const nm = e.target.value.split("/").pop() || "";
              setForm({ ...form, draft_path: e.target.value, only_allow_draft_substring: nm });
            }}
            style={{ width: "100%" }}
          >
            <option value="">選択してください</option>
            {drafts.map((d) => (
              <option key={d.path} value={d.path}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => loadImages()} disabled={running} style={{ width: 160, padding: "8px 10px", borderRadius: 8, border: "1px solid #e5e7eb", background: "#0f172a", color: "#fff" }}>
          画像を読み込む
        </button>
      </div>

      {images.length === 0 ? (
        <div style={{ padding: 20, border: "1px dashed #d1d5db", borderRadius: 12, background: "#fff", color: "#6b7280" }}>画像がありません。ドラフトを選んで「画像を読み込む」を押してください。</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
          {images.map((item) => {
            const imgSrc = item.material_name
              ? `${API_BASE}/api/swap/images/file?draft_path=${encodeURIComponent(form.draft_path)}&material_name=${encodeURIComponent(item.material_name)}`
              : undefined;
            const startSecNum = item.start_ms != null ? item.start_ms / 1000 : null;
            const endSecNum = item.start_ms != null && item.duration_ms != null ? (item.start_ms + item.duration_ms) / 1000 : null;
            return (
              <div
                key={item.index}
                style={{
                  border: "1px solid #e5e7eb",
                  borderRadius: 12,
                  padding: 12,
                  background: "#ffffff",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
                  display: "grid",
                  gridTemplateRows: "auto 1fr auto",
                  gap: 6,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ fontWeight: 800, fontSize: 14 }}>#{item.index.toString().padStart(2, "0")}</div>
                  <div style={{ fontSize: 12, color: "#1f2937" }}>
                    {startSecNum != null && endSecNum != null
                      ? `開始 ${formatHM(startSecNum)} / 終了 ${formatHM(endSecNum)} / 長さ ${formatDurationHM(startSecNum, endSecNum)}`
                      : "時間不明"}
                  </div>
                </div>
                {imgSrc ? (
                  <img src={imgSrc} alt={item.material_name} style={{ width: "100%", height: 160, objectFit: "cover", borderRadius: 10 }} />
                ) : (
                  <div style={{ height: 160, background: "#f3f4f6", borderRadius: 10, display: "grid", placeItems: "center", color: "#9ca3af" }}>画像なし</div>
                )}
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ fontSize: 13, color: "#0f172a", fontWeight: 700 }}>{item.material_name || "名前なし"}</div>
                  {(() => {
                    const raw = item.prompt || "";
                    const full = raw || "プロンプトは保存されていません（再生成すると保存されます）";
                    const firstLine = full
                      .split("\n")
                      .map((s) => s.trim())
                      .filter((s) => s.length > 0)[0] || full;
                    const isOpen = expanded.has(item.index);
                    const summary = firstLine.length > 140 ? firstLine.slice(0, 140) + " ..." : firstLine;
                    const source =
                      item.prompt_source === "snapshot"
                        ? { text: "記録済み", bg: "#dcfce7", border: "#86efac", color: "#166534" }
                        : item.prompt_source === "cues_only"
                        ? { text: "未保存（テンプレ表示）", bg: "#fff7ed", border: "#fed7aa", color: "#9a3412" }
                        : { text: "未保存", bg: "#fee2e2", border: "#fecaca", color: "#991b1b" };
                    return (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div style={{ display: "flex", gap: 8, fontSize: 11, color: "#4b5563", alignItems: "center" }}>
                          <span
                            style={{
                              padding: "2px 8px",
                              borderRadius: 8,
                              background: source.bg,
                              border: `1px solid ${source.border}`,
                              color: source.color,
                              fontWeight: 700,
                            }}
                          >
                            {source.text}
                          </span>
                          {item.prompt_timestamp && <span style={{ color: "#6b7280" }}>記録: {item.prompt_timestamp}</span>}
                        </div>
                        <div
                          style={{ fontSize: 12, color: "#0f172a", background: "#f8fafc", padding: 10, borderRadius: 10, border: "1px solid #e5e7eb", whiteSpace: "pre-wrap" }}
                          title={full}
                        >
                          {isOpen ? full : summary}
                        </div>
                        {full !== summary && (
                          <button
                            onClick={() =>
                              setExpanded((prev) => {
                                const next = new Set(prev);
                                if (next.has(item.index)) next.delete(item.index);
                                else next.add(item.index);
                                return next;
                              })
                            }
                            style={{
                              alignSelf: "flex-start",
                              fontSize: 12,
                              padding: "4px 10px",
                              borderRadius: 8,
                              border: "1px solid #0f172a",
                              background: "#0f172a",
                              color: "#fff",
                              fontWeight: 700,
                            }}
                          >
                            {isOpen ? "閉じる" : "全文表示"}
                          </button>
                        )}
                      </div>
                    );
                  })()}
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button
                    onClick={() => runSwap(item.index)}
                    disabled={running}
                    style={{
                      flex: 1,
                      padding: "10px 12px",
                      borderRadius: 10,
                      border: "none",
                      background: "#2563eb",
                      color: "#fff",
                      fontWeight: 800,
                      boxShadow: "0 2px 8px rgba(37,99,235,0.3)",
                    }}
                  >
                    再生成して適用
                  </button>
                  <button
                    onClick={() => rollbackLatest(item.index)}
                    disabled={running}
                    style={{
                      padding: "10px 12px",
                      borderRadius: 10,
                      border: "1px solid #2563eb",
                      background: "#ffffff",
                      color: "#2563eb",
                      fontWeight: 700,
                    }}
                  >
                    直前に戻す
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default SwapImagesPage;
const API_BASE = process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000";
