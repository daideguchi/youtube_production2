import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchResearchFile, fetchResearchList } from "../api/client";
import type { ResearchFileEntry } from "../api/types";

const BASE = "ssot";

function getExt(path: string) {
  const parts = path.split(".");
  return parts.length > 1 ? parts.pop()!.toLowerCase() : "";
}

function isCsv(path: string) {
  return getExt(path) === "csv";
}

function isTextPreviewable(entry: ResearchFileEntry) {
  if (entry.is_dir) return false;
  const name = entry.name.toLowerCase();
  return [".md", ".txt", ".csv", ".json"].some((ext) => name.endsWith(ext)) || !name.includes(".");
}

function isDocsIndex(entry: ResearchFileEntry): boolean {
  return !entry.is_dir && entry.name.trim().toLowerCase() === "docs_index.md";
}

function isReadmeFile(entry: ResearchFileEntry): boolean {
  return !entry.is_dir && entry.name.trim().toLowerCase() === "readme.md";
}

function resolveParentPath(path: string): string {
  const trimmed = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  if (!trimmed) return "";
  const idx = trimmed.lastIndexOf("/");
  if (idx === -1) return "";
  return trimmed.slice(0, idx);
}

export function SsotWorkspace({ embedded = false }: { embedded?: boolean } = {}) {
  const [currentPath, setCurrentPath] = useState<string>("");
  const [entries, setEntries] = useState<ResearchFileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsedCsv, setParsedCsv] = useState<string[][] | null>(null);
  const [keyword, setKeyword] = useState("");

  const loadList = useCallback(async (path = "") => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchResearchList(BASE, path);
      setCurrentPath(data.path);
      setEntries(data.entries);
      setSelectedFile(null);
      setContent("");
      setKeyword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFile = useCallback(async (targetPath: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchResearchFile(BASE, targetPath);
      setSelectedFile(targetPath);
      setContent(data.content);
      if (isCsv(targetPath)) {
        const rows = data.content
          .split(/\r?\n/)
          .slice(0, 200)
          .map((line) => line.split(","));
        setParsedCsv(rows);
      } else {
        setParsedCsv(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadList("");
  }, [loadList]);

  const handleOpen = (entry: ResearchFileEntry) => {
    if (entry.is_dir) {
      void loadList(entry.path);
      return;
    }
    void loadFile(entry.path);
  };

  const filteredEntries = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((entry) => {
      const haystack = `${entry.name} ${entry.path}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [entries, keyword]);

  useEffect(() => {
    if (!selectedFile && entries.length > 0) {
      const preferred = entries.find((e) => isDocsIndex(e)) ?? entries.find((e) => isReadmeFile(e)) ?? entries.find((e) => isTextPreviewable(e));
      if (preferred) void loadFile(preferred.path);
    }
  }, [entries, loadFile, selectedFile]);

  return (
    <section className="research-workspace">
      {!embedded ? (
        <header className="research-workspace__header">
          <div>
            <p className="eyebrow">/ssot</p>
            <h2>SSOT Docs Browser</h2>
            <p className="research-workspace__note">
              `ssot/` 配下の正本ドキュメントを UI から直接参照します（read-only）。迷ったら `DOCS_INDEX.md` と `OPS_SYSTEM_OVERVIEW.md` から。
            </p>
          </div>
          <div className="research-workspace__actions">
            <span className="badge">ssot（Single Source of Truth）</span>
            <button type="button" className="research-chip" onClick={() => void loadList("")} disabled={loading}>
              ルート
            </button>
            <Link className="research-chip" to="/ssot/map">
              System Map
            </Link>
            <Link className="research-chip" to="/ssot/gaps">
              Gaps
            </Link>
            <Link className="research-chip" to="/ssot/zombies">
              Zombies
            </Link>
            <Link className="research-chip" to="/ssot/entrypoints">
              Entrypoints
            </Link>
            <Link className="research-chip" to="/ssot/trace">
              Trace
            </Link>
            <button type="button" className="research-chip" onClick={() => void loadFile("DECISIONS.md")} disabled={loading}>
              DECISIONS
            </button>
            <button type="button" className="research-chip" onClick={() => void loadFile("DOCS_INDEX.md")} disabled={loading}>
              DOCS_INDEX
            </button>
            <button type="button" className="research-chip" onClick={() => void loadFile("OPS_SYSTEM_OVERVIEW.md")} disabled={loading}>
              OVERVIEW
            </button>
            <button
              type="button"
              className="research-chip"
              onClick={() => void loadFile("ops/OPS_CONFIRMED_PIPELINE_FLOW.md")}
              disabled={loading}
            >
              PIPELINE
            </button>
            <button type="button" className="research-chip" onClick={() => void loadFile("ops/OPS_GAPS_REGISTER.md")} disabled={loading}>
              GAPS
            </button>
            <button type="button" className="research-chip" onClick={() => void loadFile("ops/OPS_OPEN_QUESTIONS.md")} disabled={loading}>
              QUESTIONS
            </button>
            <button type="button" className="research-chip" onClick={() => void loadList(currentPath)} disabled={loading}>
              {loading ? "読み込み中…" : "再読み込み"}
            </button>
          </div>
        </header>
      ) : null}

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">現在の場所</p>
              <div className="research-breadcrumb">
                <strong>{BASE}</strong>
                <span className="crumb-sep">/</span>
                <span className="muted small-text">{currentPath || "—"}</span>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge">{filteredEntries.length} 件</span>
              {currentPath ? (
                <button type="button" className="research-chip" onClick={() => void loadList(resolveParentPath(currentPath))} disabled={loading}>
                  上へ
                </button>
              ) : null}
            </div>
          </div>
          <input
            className="research-workspace__search"
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="このフォルダ内を検索（名前/パス）"
          />
          {loading && <div className="research-loading">読み込み中...</div>}
          {error && <div className="research-error">エラー: {error}</div>}
          {!loading && !error && (
            <ul className="research-list__items">
              {filteredEntries.length === 0 ? (
                <li className="muted">ファイルが見つかりません。</li>
              ) : (
                filteredEntries.map((entry) => (
                  <li key={entry.path}>
                    <button className="research-entry" onClick={() => handleOpen(entry)}>
                      <span className={`badge ${entry.is_dir ? "dir" : "file"}`}>{entry.is_dir ? "DIR" : "FILE"}</span>
                      <div className="research-entry__meta">
                        <span className="name">{entry.name}</span>
                        <span className="meta">{entry.modified ? new Date(entry.modified).toLocaleString("ja-JP") : "-"}</span>
                      </div>
                    </button>
                  </li>
                ))
              )}
            </ul>
          )}
        </div>

        <div className="research-viewer">
          <div className="research-viewer__header">
            <div>
              <strong>プレビュー</strong>
              <p className="research-viewer__path">{selectedFile ?? "ファイルを選択してください"}</p>
            </div>
            {selectedFile ? <span className="badge subtle">テキスト前提の簡易表示（CSVは表形式で最大200行）</span> : null}
          </div>
          {parsedCsv ? (
            <div className="research-csv">
              <table className="research-csv__table">
                <tbody>
                  {parsedCsv.map((row, idx) => (
                    <tr key={idx}>
                      {row.map((cell, cidx) => (
                        <td key={cidx}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <pre className="research-viewer__content">{content || " "}</pre>
          )}
        </div>
      </div>
    </section>
  );
}
