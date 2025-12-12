import { useEffect, useState, useCallback } from "react";
import { fetchResearchFile, fetchResearchList } from "../api/client";
import type { ResearchFileEntry } from "../api/types";

type BaseKey = "research" | "scripts";

const PRESETS: Array<{ label: string; base: BaseKey; path: string; hint?: string }> = [
  { label: "心理・スピ系ベンチ", base: "research", path: "心理学スピリチュアル系" },
  { label: "シニア恋愛ベンチ", base: "research", path: "シニアのストーリー/シニアの恋愛1" },
  { label: "偉人・名言ベンチ", base: "research", path: "偉人名言系のベンチマーク台本（参考）" },
  { label: "CH05 台本アーカイブ", base: "scripts", path: "CH05" },
  { label: "CH10 台本アーカイブ", base: "scripts", path: "CH10" },
];

const BASE_LABEL: Record<BaseKey, string> = {
  research: "00_research（ベンチマーク・調査）",
  scripts: "script_pipeline/data（台本アーカイブ）",
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

export function ResearchWorkspace() {
  const [base, setBase] = useState<BaseKey>("research");
  const [currentPath, setCurrentPath] = useState<string>("");
  const [activePreset, setActivePreset] = useState<typeof PRESETS[number] | null>(null);
  const [entries, setEntries] = useState<ResearchFileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsedCsv, setParsedCsv] = useState<string[][] | null>(null);

  const loadList = async (nextBase: BaseKey, path = "", preset?: typeof PRESETS[number]) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchResearchList(nextBase, path);
      setBase(nextBase);
      setCurrentPath(data.path);
      setEntries(data.entries);
      if (preset) {
        setActivePreset(preset);
      }
      setSelectedFile(null);
      setContent("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const loadFile = async (targetPath: string) => {
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
  };

  const loadPreset = useCallback((preset: typeof PRESETS[number]) => {
    void loadList(preset.base as BaseKey, preset.path, preset);
  }, []);

  useEffect(() => {
    // 初回は一番上のプリセット
    loadPreset(PRESETS[0]);
  }, [loadPreset]);

  const handleOpen = (entry: ResearchFileEntry) => {
    if (entry.is_dir) {
      void loadList(base, entry.path, activePreset ?? undefined);
      return;
    }
    // 可能なものは全て読み込む（テキスト前提）
    void loadFile(entry.path);
  };

  // 初回とリスト切り替え時にテキスト系があれば自動プレビュー
  useEffect(() => {
    if (!selectedFile && entries.length > 0) {
      const firstText = entries.find((e) => isTextPreviewable(e));
      if (firstText) {
        void loadFile(firstText.path);
      }
    }
    // loadFile intentionally excluded to avoid ref churn; relies on stable fetchResearchFile
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, selectedFile]);

  return (
    <section className="research-workspace">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/research</p>
          <h2>リサーチ＆ベンチマーク</h2>
          <p className="research-workspace__note">
            00_research（ベンチマーク）と script_pipeline/data（台本アーカイブ）をブラウズ。ベンチ情報を見ながら台本を確認する最小限ビューです。
          </p>
        </div>
        <div className="research-workspace__actions">
          <span className="badge subtle">{BASE_LABEL[base]}</span>
          {activePreset ? <span className="badge subtle">{activePreset.label}</span> : null}
          <button type="button" className="research-chip" onClick={() => loadPreset(activePreset ?? PRESETS[0])}>
            再読み込み
          </button>
        </div>
      </header>

      <section className="research-quick">
        <div className="research-quick__title">ショートカット</div>
        <div className="research-quick__grid">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              className="research-quick__item"
              onClick={() => loadPreset(preset)}
              title={preset.hint ?? preset.path}
            >
              <span className="research-quick__label">{preset.label}</span>
              <span className="research-quick__path">
                {BASE_LABEL[preset.base]} / {preset.path}
              </span>
            </button>
          ))}
        </div>
      </section>

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">現在のプリセット</p>
              <div className="research-breadcrumb">
                <strong>{activePreset?.label ?? "未選択"}</strong>
                <span className="crumb-sep">/</span>
                <span className="muted small-text">{currentPath || "—"}</span>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge subtle">{entries.length} 件</span>
              <span className="badge subtle">{BASE_LABEL[base]}</span>
            </div>
          </div>
          {loading && <div className="research-loading">読み込み中...</div>}
          {error && <div className="research-error">エラー: {error}</div>}
          {!loading && !error && (
            <ul className="research-list__items">
              {entries.length === 0 ? (
                <li className="muted">ファイルが見つかりません。</li>
              ) : (
                entries.map((entry) => (
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
