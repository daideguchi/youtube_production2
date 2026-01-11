import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchResearchFileChunk, fetchResearchList } from "../api/client";
import type { ResearchFileEntry } from "../api/types";

type TraceKind = "llm" | "image";

function stripJsonl(name: string): string {
  return name.endsWith(".jsonl") ? name.slice(0, -".jsonl".length) : name;
}

function formatJson(val: unknown) {
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

function parseJsonl(content: string): Array<Record<string, unknown>> {
  const out: Array<Record<string, unknown>> = [];
  for (const line of (content || "").split("\n")) {
    const s = line.trim();
    if (!s) continue;
    try {
      const obj = JSON.parse(s);
      if (obj && typeof obj === "object") out.push(obj as Record<string, unknown>);
    } catch {
      // ignore
    }
  }
  return out;
}

function normalizeContent(raw: unknown): string {
  if (raw == null) return "";
  if (typeof raw === "string") return raw;
  if (typeof raw === "number" || typeof raw === "boolean") return String(raw);
  return formatJson(raw);
}

export function SsotTracePage() {
  const params = useParams();
  const navigate = useNavigate();

  const [kind, setKind] = useState<TraceKind>("llm");
  const [traceKey, setTraceKey] = useState<string>((params as any).key ? String((params as any).key) : "");
  const [available, setAvailable] = useState<ResearchFileEntry[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [raw, setRaw] = useState("");

  const dirPath = useMemo(() => `traces/${kind}`, [kind]);
  const filePath = useMemo(() => (traceKey ? `${dirPath}/${traceKey}.jsonl` : ""), [dirPath, traceKey]);

  const loadList = useCallback(async () => {
    setLoadingList(true);
    setError(null);
    try {
      const data = await fetchResearchList("logs", dirPath);
      setAvailable(data.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setAvailable([]);
    } finally {
      setLoadingList(false);
    }
  }, [dirPath]);

  const loadFile = useCallback(async () => {
    if (!filePath) {
      setRaw("");
      return;
    }
    setLoadingFile(true);
    setError(null);
    try {
      const data = await fetchResearchFileChunk("logs", filePath, { offset: 0, length: 5000 });
      setRaw(data.content || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRaw("");
    } finally {
      setLoadingFile(false);
    }
  }, [filePath]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void loadFile();
  }, [loadFile]);

  useEffect(() => {
    const k = (params as any).key ? String((params as any).key) : "";
    if (k && k !== traceKey) setTraceKey(k);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [(params as any).key]);

  const keys = useMemo(() => {
    const files = available.filter((e) => !e.is_dir && e.name.endsWith(".jsonl"));
    const q = keyword.trim().toLowerCase();
    const mapped = files.map((e) => ({ entry: e, key: stripJsonl(e.name) }));
    return q ? mapped.filter((m) => m.key.toLowerCase().includes(q)) : mapped;
  }, [available, keyword]);

  const events = useMemo(() => parseJsonl(raw), [raw]);

  const openKey = (k: string) => {
    setTraceKey(k);
    navigate(`/ssot/trace/${encodeURIComponent(k)}`);
  };

  return (
    <section className="research-workspace">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot/trace</p>
          <h2>Trace Viewer</h2>
          <p className="research-workspace__note">
            実行ログ（JSONL）を閲覧します。`LLM_ROUTING_KEY` または `YTM_TRACE_KEY` をセットして実行すると、episode別に追跡できます。
          </p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <Link className="research-chip" to="/ssot">
              SSOT Portal
            </Link>
            <Link className="research-chip" to="/ssot/map">
              System Map
            </Link>
            <Link className="research-chip" to="/ssot/gaps">
              Gaps
            </Link>
            <Link className="research-chip" to="/ssot/entrypoints">
              Entrypoints
            </Link>
            <button type="button" className={`research-chip ${kind === "llm" ? "is-active" : ""}`} onClick={() => setKind("llm")}>
              LLM
            </button>
            <button type="button" className={`research-chip ${kind === "image" ? "is-active" : ""}`} onClick={() => setKind("image")}>
              Image
            </button>
            <button type="button" className="research-chip" onClick={() => void loadList()} disabled={loadingList}>
              {loadingList ? "更新中…" : "キー再読み込み"}
            </button>
            <button type="button" className="research-chip" onClick={() => void loadFile()} disabled={loadingFile || !filePath}>
              {loadingFile ? "読み込み中…" : "ログ再読み込み"}
            </button>
          </div>
        </div>
      </header>

      <div className="research-body">
        <div className="research-list">
          <div className="research-list__header">
            <div>
              <p className="muted">Trace Keys</p>
              <div className="research-breadcrumb">
                <strong className="mono">workspaces/logs/{dirPath}</strong>
              </div>
            </div>
            <div className="research-list__status">
              <span className="badge">{keys.length} 件</span>
            </div>
          </div>
          <input className="research-workspace__search" type="search" value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="キー検索" />
          <div style={{ display: "flex", gap: 8, padding: "0 0 10px 0" }}>
            <input
              className="research-workspace__search"
              type="search"
              value={traceKey}
              onChange={(e) => setTraceKey(e.target.value)}
              placeholder="trace key（例: CH01-251）"
            />
            <button type="button" className="research-chip" onClick={() => openKey(traceKey)} disabled={!traceKey.trim()}>
              開く
            </button>
          </div>
          {error ? <div className="main-alert main-alert--error">エラー: {error}</div> : null}
          <ul className="research-list__items">
            {keys.map(({ key }) => (
              <li key={key}>
                <button
                  className="research-entry"
                  onClick={() => openKey(key)}
                  style={{ borderColor: traceKey === key ? "var(--color-primary)" : undefined }}
                >
                  <span className="badge dir">{kind.toUpperCase()}</span>
                  <div className="research-entry__meta">
                    <span className="name mono">{key}</span>
                    <span className="meta">{traceKey === key ? "selected" : "—"}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="research-viewer">
          <div className="research-viewer__header">
            <div>
              <strong>Events</strong>
              <p className="research-viewer__path">{filePath || "—"}</p>
            </div>
            <span className="badge subtle">read-only</span>
          </div>

          {!filePath ? <div className="main-alert">左からキーを選ぶか、trace key を入力してください。</div> : null}
          {filePath && loadingFile ? <div className="main-alert">読み込み中…</div> : null}
          {filePath && !loadingFile ? (
            <div style={{ display: "grid", gap: 12 }}>
              <section className="shell-panel shell-panel--placeholder">
                <h3 style={{ marginTop: 0 }}>Summary</h3>
                <div className="mono">
                  kind={kind} / events={events.length}
                </div>
                <p className="muted small-text" style={{ margin: "8px 0 0 0" }}>
                  NOTE: trace は「呼び出し内容（request/messages/input）」中心で、provider により response/output が含まれない場合があります。
                </p>
              </section>

              {events.length === 0 ? (
                <div className="main-alert main-alert--warning">イベントが見つかりません（またはJSONLのパースに失敗しました）。</div>
              ) : null}

              {events.map((ev, idx) => {
                const schema = String(ev.schema || "");
                const t = String((ev as any).task || "");
                const at = String((ev as any).generated_at || "");
                const provider = String((ev as any).provider || "");
                const model = String((ev as any).model || (ev as any).model_key || "");
                const callsite = (ev as any).callsite as any;
                const callsiteLabel = callsite?.path ? `${callsite.path}:${callsite.line}` : "";
                const request = (ev as any).request as any;
                const tier = String((ev as any).tier || "");
                const durationMs = Number((ev as any).latency_ms ?? (ev as any).duration_ms ?? 0) || 0;
                const requestId = String((ev as any).request_id || "");
                const traceKeyValue = String((ev as any).trace_key || "");

                const messagesRaw = (ev as any).messages;
                const messages = Array.isArray(messagesRaw) ? (messagesRaw as any[]) : [];
                const imageInput = (ev as any).input as any;

                return (
                  <details key={idx} style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, padding: 12 }}>
                    <summary style={{ cursor: "pointer" }}>
                      <span className="mono">
                        [{idx + 1}] {t} {provider ? `· ${provider}` : ""} {model ? `· ${model}` : ""} {at ? `· ${at}` : ""}{" "}
                        {callsiteLabel ? `· ${callsiteLabel}` : ""} {schema ? `· ${schema}` : ""}
                      </span>
                    </summary>
                    <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                      <div className="mono muted small-text" style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                        {traceKeyValue ? <span>trace_key={traceKeyValue}</span> : null}
                        {requestId ? <span>request_id={requestId}</span> : null}
                        {tier ? <span>tier={tier}</span> : null}
                        {durationMs ? <span>duration_ms={durationMs}</span> : null}
                      </div>

                      {request ? (
                        <details>
                          <summary className="muted small-text" style={{ cursor: "pointer" }}>
                            request（max_tokens/temperature/format など）
                          </summary>
                          <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: "10px 0 0 0", maxHeight: 260, overflow: "auto" }}>
                            {formatJson(request)}
                          </pre>
                        </details>
                      ) : null}

                      {kind === "llm" && messages.length > 0 ? (
                        <div>
                          <div className="muted small-text" style={{ marginBottom: 6 }}>
                            messages（実際に投げたプロンプト）
                          </div>
                          <div style={{ display: "grid", gap: 10 }}>
                            {messages.map((m, mi) => {
                              const role = m?.role ? String(m.role) : "unknown";
                              const content = normalizeContent(m?.content);
                              return (
                                <section
                                  key={`${idx}-msg-${mi}`}
                                  style={{
                                    border: "1px solid var(--color-border-muted)",
                                    borderRadius: 12,
                                    background: "var(--color-surface)",
                                    padding: 10,
                                  }}
                                >
                                  <div className="mono muted small-text">[{mi + 1}] role={role}</div>
                                  <pre
                                    className="mono"
                                    style={{
                                      whiteSpace: "pre-wrap",
                                      margin: "8px 0 0 0",
                                      maxHeight: 360,
                                      overflow: "auto",
                                      background: "var(--color-surface-subtle)",
                                      border: "1px solid var(--color-border-muted)",
                                      borderRadius: 10,
                                      padding: 10,
                                    }}
                                  >
                                    {content || "(empty)"}
                                  </pre>
                                </section>
                              );
                            })}
                          </div>
                        </div>
                      ) : null}

                      {kind === "image" && imageInput ? (
                        <div>
                          <div className="muted small-text" style={{ marginBottom: 6 }}>
                            input（image generation）
                          </div>
                          <pre
                            className="mono"
                            style={{
                              whiteSpace: "pre-wrap",
                              margin: 0,
                              maxHeight: 420,
                              overflow: "auto",
                              background: "var(--color-surface-subtle)",
                              border: "1px solid var(--color-border-muted)",
                              borderRadius: 10,
                              padding: 10,
                            }}
                          >
                            {formatJson(imageInput)}
                          </pre>
                        </div>
                      ) : null}

                      <details>
                        <summary className="muted small-text" style={{ cursor: "pointer" }}>
                          raw JSON
                        </summary>
                        <pre className="mono" style={{ whiteSpace: "pre-wrap", marginTop: 10 }}>
                          {formatJson(ev)}
                        </pre>
                      </details>
                    </div>
                  </details>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
