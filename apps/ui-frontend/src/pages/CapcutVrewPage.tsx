import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiUrl } from "../api/baseUrl";

type DraftItem = { name: string; path: string };
type RunDirItem = { name: string; path: string; mtime?: number };

type VrewPromptsState = {
  status: "idle" | "loading" | "ready" | "error";
  runDir: string;
  promptsPath: string;
  lineCount: number;
  textLines: string;
  textKuten: string;
  error?: string;
};

const initialPromptsState: VrewPromptsState = {
  status: "idle",
  runDir: "",
  promptsPath: "",
  lineCount: 0,
  textLines: "",
  textKuten: "",
  error: undefined,
};

export function CapcutVrewPage() {
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [runDirs, setRunDirs] = useState<RunDirItem[]>([]);
  const [draftPath, setDraftPath] = useState("");
  const [runDir, setRunDir] = useState("");
  const [runDirFilter, setRunDirFilter] = useState("");
  const [toast, setToast] = useState("");
  const [prompts, setPrompts] = useState<VrewPromptsState>(initialPromptsState);

  const filteredRunDirs = useMemo(() => {
    const keyword = runDirFilter.trim().toLowerCase();
    if (!keyword) return runDirs;
    return runDirs.filter((it) => it.name.toLowerCase().includes(keyword) || it.path.toLowerCase().includes(keyword));
  }, [runDirFilter, runDirs]);

  const loadRunDirForDraftName = async (draftName: string) => {
    if (!draftName) return;
    try {
      const rd = await fetch(apiUrl(`/api/swap/auto-run-dir?draft_name=${encodeURIComponent(draftName)}`)).then((r) => r.json());
      if (rd?.run_dir) {
        setRunDir(String(rd.run_dir));
        setPrompts((prev) => ({ ...initialPromptsState, status: "idle", runDir: String(rd.run_dir) }));
        setToast("run_dir を推定しました。必要なら手動で変更できます。");
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    const load = async () => {
      setToast("読み込み中...");
      try {
        const [draftsResp, runDirsResp] = await Promise.all([
          fetch(apiUrl("/api/swap/drafts")).then((r) => r.json()),
          fetch(apiUrl("/api/swap/run-dirs")).then((r) => r.json()),
        ]);
        const d: DraftItem[] = draftsResp?.items || [];
        const r: RunDirItem[] = runDirsResp?.items || [];
        setDrafts(d);
        setRunDirs(r);

        const firstDraft = d[0];
        const firstRun = r[0];
        if (firstDraft) {
          setDraftPath(firstDraft.path);
          await loadRunDirForDraftName(firstDraft.name);
        } else if (firstRun) {
          setRunDir(firstRun.path);
          setPrompts((prev) => ({ ...prev, runDir: firstRun.path }));
        }
        setToast("");
      } catch (e: any) {
        setToast(e?.message || "初期ロードに失敗しました");
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSelectDraft = async (value: string) => {
    setDraftPath(value);
    const chosen = drafts.find((d) => d.path === value);
    if (chosen) {
      await loadRunDirForDraftName(chosen.name);
    }
  };

  const handleSelectRunDir = (value: string) => {
    setRunDir(value);
    setPrompts((prev) => ({ ...initialPromptsState, status: "idle", runDir: value }));
  };

  const handleLoadVrewPrompts = async () => {
    if (!runDir) {
      setToast("run_dir を選択してください");
      return;
    }
    setToast("Vrewプロンプト読み込み中...");
    setPrompts((prev) => ({ ...initialPromptsState, status: "loading", runDir }));
    try {
      const params = new URLSearchParams({ run_dir: runDir });
      const res = await fetch(apiUrl(`/api/swap/vrew-prompts?${params.toString()}`));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPrompts({
          ...initialPromptsState,
          status: "error",
          runDir,
          error: data?.detail || "Vrewプロンプトの取得に失敗しました",
        });
        setToast("");
        return;
      }
      setPrompts({
        status: "ready",
        runDir: data?.run_dir || runDir,
        promptsPath: data?.prompts_path || "",
        lineCount: Number(data?.line_count || 0),
        textLines: String(data?.prompts_text || ""),
        textKuten: String(data?.prompts_text_kuten || ""),
        error: undefined,
      });
      setToast("読み込み完了");
    } catch (e: any) {
      setPrompts({
        ...initialPromptsState,
        status: "error",
        runDir,
        error: e?.message || "Vrewプロンプトの取得に失敗しました",
      });
      setToast("");
    }
  };

  const handleCopy = async (text: string, label: string) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setToast(`${label} をコピーしました`);
    } catch (e: any) {
      setToast(e?.message || "コピーに失敗しました");
    }
  };

  return (
    <div className="page capcut-edit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">CapCutライン</p>
          <h1>Vrew用プロンプト</h1>
          <p className="page-lead">CapCut run_dir の vrew_import_prompts.txt を読み込み、Vrewにそのまま貼れる本文を出します（Vrewは「。」で分割）。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/capcut-edit">
            ← CapCut編集メニューへ戻る
          </Link>
          <Link className="button" to="/capcut-edit/draft">
            新規ドラフト作成
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section" style={{ display: "grid", gap: 14 }}>
        {toast && (
          <div style={{ padding: "10px 12px", borderRadius: 10, background: "#0f172a", color: "#fff", fontSize: 12 }}>
            {toast}
          </div>
        )}

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ fontWeight: 800 }}>1) 対象を選ぶ</div>
            <div style={{ fontSize: 12, color: "#64748b" }}>ドラフト名から run_dir を自動推定できます（必要なら run_dir を手動で選び直し）。</div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>ドラフト（任意）</div>
              <select
                value={draftPath}
                onChange={(e) => handleSelectDraft(e.target.value)}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                <option value="">（未選択）</option>
                {drafts.map((d) => (
                  <option key={d.path} value={d.path}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>run_dir（必須）</div>
              <input
                value={runDirFilter}
                onChange={(e) => setRunDirFilter(e.target.value)}
                placeholder="フィルタ（例: CH23-001）"
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1" }}
              />
              <select
                value={runDir}
                onChange={(e) => handleSelectRunDir(e.target.value)}
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                <option value="">（未選択）</option>
                {filteredRunDirs.map((r) => (
                  <option key={r.path} value={r.path}>
                    {r.name}
                  </option>
                ))}
              </select>
              {runDir && <div style={{ fontSize: 11, color: "#94a3b8" }}>{runDir}</div>}
            </div>

            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={handleLoadVrewPrompts}
                disabled={!runDir || prompts.status === "loading"}
                style={{
                  padding: "10px 14px",
                  borderRadius: 10,
                  border: "none",
                  background: !runDir || prompts.status === "loading" ? "#e5e7eb" : "#0f172a",
                  color: "#fff",
                  cursor: !runDir || prompts.status === "loading" ? "not-allowed" : "pointer",
                  fontWeight: 800,
                }}
              >
                {prompts.status === "loading" ? "読み込み中..." : "Vrewプロンプトを読み込む"}
              </button>
              {prompts.status === "ready" && (
                <div style={{ fontSize: 12, color: "#64748b" }}>
                  {prompts.lineCount} 件 / {prompts.promptsPath ? prompts.promptsPath : "vrew_import_prompts.txt"}
                </div>
              )}
              {prompts.status === "error" && <div style={{ fontSize: 12, color: "#b91c1c" }}>{prompts.error}</div>}
            </div>
          </div>
        </div>

        <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14, display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div>
              <strong>2) Vrew貼り付け用（句点区切り）</strong>
              <div style={{ fontSize: 12, color: "#64748b" }}>Vrewは「。」でセクション分割します。ここは改行なしで出します。</div>
            </div>
            <button
              type="button"
              onClick={() => handleCopy(prompts.textKuten, "Vrew貼り付け本文")}
              disabled={prompts.status !== "ready" || !prompts.textKuten}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid #cbd5e1",
                background: prompts.status === "ready" ? "#f8fafc" : "#e5e7eb",
                color: "#0f172a",
                cursor: prompts.status === "ready" ? "pointer" : "not-allowed",
                fontWeight: 700,
              }}
            >
              コピー
            </button>
          </div>
          <textarea
            readOnly
            value={
              prompts.status === "ready"
                ? prompts.textKuten
                : prompts.status === "loading"
                  ? "読み込み中..."
                  : "run_dir を選んで「Vrewプロンプトを読み込む」を押してください。"
            }
            style={{
              width: "100%",
              minHeight: 200,
              borderRadius: 10,
              border: "1px solid #cbd5e1",
              padding: 10,
              fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              background: "#fff",
              whiteSpace: "pre-wrap",
            }}
          />
        </div>

        <details style={{ border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 14 }}>
          <summary style={{ cursor: "pointer", fontWeight: 800 }}>確認用（1行=1プロンプト）</summary>
          <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: "#64748b" }}>内容確認/編集用。Vrew貼り付けは上の「句点区切り」を推奨。</div>
              <button
                type="button"
                onClick={() => handleCopy(prompts.textLines, "改行区切り本文")}
                disabled={prompts.status !== "ready" || !prompts.textLines}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  background: prompts.status === "ready" ? "#f8fafc" : "#e5e7eb",
                  color: "#0f172a",
                  cursor: prompts.status === "ready" ? "pointer" : "not-allowed",
                  fontWeight: 700,
                }}
              >
                コピー
              </button>
            </div>
            <textarea
              readOnly
              value={prompts.status === "ready" ? prompts.textLines : ""}
              style={{
                width: "100%",
                minHeight: 160,
                borderRadius: 10,
                border: "1px solid #cbd5e1",
                padding: 10,
                fontFamily: "SFMono-Regular, Menlo, Consolas, monospace",
                fontSize: 12,
                background: "#fff",
                whiteSpace: "pre",
              }}
            />
          </div>
        </details>
      </section>
    </div>
  );
}

