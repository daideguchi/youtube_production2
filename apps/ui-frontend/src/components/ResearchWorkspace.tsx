import { useEffect, useMemo, useState, useCallback } from "react";
import { fetchResearchFile, fetchResearchList } from "../api/client";
import type { ResearchFileEntry } from "../api/types";

type BaseKey = "research" | "scripts";

const BASE_LABEL: Record<BaseKey, string> = {
  research: "workspaces/research（ベンチマーク・調査）",
  scripts: "workspaces/scripts（台本SoT）",
};

function getExt(path: string) {
  const parts = path.split(".");
  return parts.length > 1 ? parts.pop()!.toLowerCase() : "";
}

function isCsv(path: string) {
  return getExt(path) === "csv";
}

// 可能な限りテキストとして扱う（拡張子なしもプレビュー対象）
function isTextPreviewable(entry: ResearchFileEntry) {
  if (entry.is_dir) return false;
  const name = entry.name.toLowerCase();
  return [".md", ".txt", ".csv", ".json"].some((ext) => name.endsWith(ext)) || !name.includes(".");
}

function isIndexFile(entry: ResearchFileEntry): boolean {
  return !entry.is_dir && entry.name.trim().toLowerCase() === "index.md";
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

export function ResearchWorkspace() {
  const [base, setBase] = useState<BaseKey>("research");
  const [currentPath, setCurrentPath] = useState<string>("");
  const [genres, setGenres] = useState<ResearchFileEntry[]>([]);
  const [hasInbox, setHasInbox] = useState(false);
  const [entries, setEntries] = useState<ResearchFileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsedCsv, setParsedCsv] = useState<string[][] | null>(null);
  const [keyword, setKeyword] = useState("");

  const loadList = useCallback(async (nextBase: BaseKey, path = "") => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchResearchList(nextBase, path);
      setBase(nextBase);
      setCurrentPath(data.path);
      setEntries(data.entries);
      if (nextBase === "research" && data.path === "") {
        const topDirs = data.entries.filter(
          (entry) => entry.is_dir && !entry.name.startsWith("_") && !entry.name.startsWith(".")
        );
        setHasInbox(topDirs.some((entry) => entry.name === "INBOX"));
        setGenres(topDirs.filter((entry) => entry.name !== "INBOX"));
      }
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
      const data = await fetchResearchFile(base, targetPath);
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
  }, [base]);

  useEffect(() => {
    // 初回は research ルート（INDEX.md があれば自動プレビュー）
    void loadList("research", "");
  }, [loadList]);

  const handleOpen = (entry: ResearchFileEntry) => {
    if (entry.is_dir) {
      void loadList(base, entry.path);
      return;
    }
    // 可能なものは全て読み込む（テキスト前提）
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

  // 初回とリスト切り替え時にテキスト系があれば自動プレビュー
  useEffect(() => {
    if (!selectedFile && entries.length > 0) {
      const preferred =
        entries.find((e) => isIndexFile(e)) ??
        entries.find((e) => isReadmeFile(e)) ??
        entries.find((e) => isTextPreviewable(e));
      if (preferred) void loadFile(preferred.path);
    }
  }, [entries, loadFile, selectedFile]);

  return (
    <section className="research-workspace">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/research</p>
          <h2>リサーチ＆ベンチマーク</h2>
          <p className="research-workspace__note">
            ジャンル軸でベンチ/参考台本/分析メモを参照します。各ジャンルの INDEX.md を入口にすると迷いません。
          </p>
        </div>
        <div className="research-workspace__actions">
          <span className="badge">{BASE_LABEL[base]}</span>
          <button
            type="button"
            className={base === "research" ? "research-chip is-active" : "research-chip"}
            onClick={() => void loadList("research", "")}
            disabled={loading}
          >
            research
          </button>
          <button
            type="button"
            className={base === "scripts" ? "research-chip is-active" : "research-chip"}
            onClick={() => void loadList("scripts", "")}
            disabled={loading}
          >
            scripts
          </button>
          <button type="button" className="research-chip" onClick={() => void loadList(base, currentPath)} disabled={loading}>
            {loading ? "読み込み中…" : "再読み込み"}
          </button>
        </div>
      </header>

      <section className="research-quick">
        <div className="research-quick__title">ジャンル（research）</div>
        <div className="research-quick__grid">
          <button className="research-quick__item" onClick={() => void loadList("research", "")} title="workspaces/research/INDEX.md">
            <span className="research-quick__label">全体INDEX</span>
            <span className="research-quick__path">{BASE_LABEL.research} / INDEX.md</span>
          </button>
          {hasInbox ? (
            <button
              className="research-quick__item"
              onClick={() => void loadList("research", "INBOX")}
              title="workspaces/research/INBOX/INDEX.md"
            >
              <span className="research-quick__label">INBOX（未整理）</span>
              <span className="research-quick__path">{BASE_LABEL.research} / INBOX</span>
            </button>
          ) : null}
          {genres.map((genre) => (
            <button
              key={genre.path}
              className="research-quick__item"
              onClick={() => void loadList("research", genre.path)}
              title={`workspaces/research/${genre.path}/INDEX.md`}
            >
              <span className="research-quick__label">{genre.name}</span>
              <span className="research-quick__path">
                {BASE_LABEL.research} / {genre.path}
              </span>
            </button>
          ))}
        </div>
      </section>

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">現在の場所</p>
              <div className="research-breadcrumb">
                <strong>{base}</strong>
                <span className="crumb-sep">/</span>
                <span className="muted small-text">{currentPath || "—"}</span>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge">{filteredEntries.length} 件</span>
              {currentPath ? (
                <button type="button" className="research-chip" onClick={() => void loadList(base, resolveParentPath(currentPath))} disabled={loading}>
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
                        <span className="meta">
                          {entry.modified ? new Date(entry.modified).toLocaleString("ja-JP") : "-"}
                        </span>
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
