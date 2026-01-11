import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiUrl } from "../api/baseUrl";
import "./AgentOrgPage.css";

type OrchestratorStatus = {
  queue_dir: string;
  lock_held: boolean;
  pid_alive: boolean;
  heartbeat_age_sec: number | null;
  state: Record<string, unknown>;
};

type AgentRow = {
  status: "active" | "stale" | "dead";
  id: string;
  name: string;
  role: string;
  pid: number | null;
  host_pid?: number | null;
  last_seen_at?: string | null;
};

type MemoRow = {
  id: string;
  created_at?: string | null;
  from?: string | null;
  to: string[];
  subject?: string | null;
  related_task_id?: string | null;
};

type NoteRow = {
  status: "active" | "expired";
  id: string;
  created_at?: string | null;
  from?: string | null;
  to?: string | null;
  subject?: string | null;
};

type LockRow = {
  status: "active" | "expired";
  id: string;
  mode?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  scopes: string[];
  note?: string | null;
};

type TabKey = "overview" | "agents" | "locks" | "memos" | "notes" | "actions";

const TAB_KEYS: TabKey[] = ["overview", "agents", "locks", "memos", "notes", "actions"];

function isTabKey(value: string | null): value is TabKey {
  return value != null && (TAB_KEYS as readonly string[]).includes(value);
}

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", { hour12: false });
}

function formatLastUpdated(value: Date | null): string {
  if (!value) return "—";
  return value.toLocaleString("ja-JP", { hour12: false });
}

function normalizeSearch(value: string): string {
  return value.trim().toLowerCase();
}

function joinNonEmpty(values: Array<string | number | null | undefined>): string {
  return values
    .map((v) => (v == null ? "" : String(v)))
    .map((v) => v.trim())
    .filter(Boolean)
    .join(" ");
}

function agentBadgeClass(status: AgentRow["status"]): string {
  if (status === "active") return "agent-org-page__badge agent-org-page__badge--ok";
  if (status === "stale") return "agent-org-page__badge agent-org-page__badge--warn";
  return "agent-org-page__badge agent-org-page__badge--err";
}

function lockBadgeClass(status: LockRow["status"]): string {
  if (status === "active") return "agent-org-page__badge agent-org-page__badge--warn";
  return "agent-org-page__badge";
}

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return apiUrl(path);
}

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(buildUrl(path), { headers: { "Content-Type": "application/json" } });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(buildUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function AgentOrgPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchParamsString = searchParams.toString();
  const urlTab = searchParams.get("tab");
  const urlFrom = searchParams.get("from");
  const urlQuery = searchParams.get("q");
  const urlAuto = searchParams.get("auto");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionResponse, setActionResponse] = useState<Record<string, unknown> | null>(null);

  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const copyNoticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [actorName, setActorName] = useState(() => {
    const fromParam = (urlFrom ?? "").trim();
    if (fromParam) {
      return fromParam;
    }
    try {
      const stored = (localStorage.getItem("agent_org_actor") || "").trim();
      if (stored) {
        return stored;
      }
      const suffixKey = "agent_org_actor_suffix";
      let suffix = (localStorage.getItem(suffixKey) || "").trim();
      if (!suffix) {
        suffix = Math.random().toString(16).slice(2, 6);
        localStorage.setItem(suffixKey, suffix);
      }
      const generated = `dd-ui-${suffix}`;
      localStorage.setItem("agent_org_actor", generated);
      return generated;
    } catch {
      return "dd-ui";
    }
  });

  const [tab, setTab] = useState<TabKey>(() => {
    if (isTabKey(urlTab)) {
      return urlTab;
    }
    try {
      const stored = localStorage.getItem("agent_org_tab");
      if (isTabKey(stored)) {
        return stored;
      }
    } catch {
      /* ignore */
    }
    return "overview";
  });

  const [query, setQuery] = useState(() => urlQuery ?? "");
  const [autoRefresh, setAutoRefresh] = useState(() => {
    if (urlAuto === "1") {
      return true;
    }
    if (urlAuto === "0") {
      return false;
    }
    try {
      return localStorage.getItem("agent_org_auto_refresh") === "1";
    } catch {
      return false;
    }
  });
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

  const [orch, setOrch] = useState<OrchestratorStatus | null>(null);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [memos, setMemos] = useState<MemoRow[]>([]);
  const [notes, setNotes] = useState<NoteRow[]>([]);
  const [locks, setLocks] = useState<LockRow[]>([]);

  const [noteTo, setNoteTo] = useState("");
  const [noteSubject, setNoteSubject] = useState("no-touch");
  const [noteBody, setNoteBody] = useState("");
  const [noteTtlMin, setNoteTtlMin] = useState("60");

  const [roleAgentId, setRoleAgentId] = useState("");
  const [roleValue, setRoleValue] = useState("worker");

  const [assignTaskId, setAssignTaskId] = useState("");
  const [assignAgentId, setAssignAgentId] = useState("");
  const [assignNote, setAssignNote] = useState("");

  const [lockScopes, setLockScopes] = useState("apps/ui-frontend/**");
  const [lockMode, setLockMode] = useState("no_touch");
  const [lockTtlMin, setLockTtlMin] = useState("60");
  const [lockNote, setLockNote] = useState("");
  const [unlockLockId, setUnlockLockId] = useState("");

  const [selectedMemoId, setSelectedMemoId] = useState<string | null>(null);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [memoDetail, setMemoDetail] = useState<Record<string, unknown> | null>(null);
  const [noteDetail, setNoteDetail] = useState<Record<string, unknown> | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [orchResp, agentsResp, memosResp, notesResp, locksResp] = await Promise.all([
        fetchJson<OrchestratorStatus>("/api/agent-org/orchestrator"),
        fetchJson<{ agents: AgentRow[] }>("/api/agent-org/agents?stale_sec=30"),
        fetchJson<{ memos: MemoRow[] }>("/api/agent-org/memos?limit=100"),
        fetchJson<{ notes: NoteRow[] }>("/api/agent-org/notes?limit=100"),
        fetchJson<{ locks: LockRow[] }>("/api/agent-org/locks"),
      ]);
      setOrch(orchResp);
      setAgents(Array.isArray(agentsResp.agents) ? agentsResp.agents : []);
      setMemos(Array.isArray(memosResp.memos) ? memosResp.memos : []);
      setNotes(Array.isArray(notesResp.notes) ? notesResp.notes : []);
      setLocks(Array.isArray(locksResp.locks) ? locksResp.locks : []);
      setLastUpdatedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    try {
      localStorage.setItem("agent_org_actor", actorName);
    } catch {
      /* ignore */
    }
  }, [actorName]);

  useEffect(() => {
    try {
      localStorage.setItem("agent_org_tab", tab);
    } catch {
      /* ignore */
    }
  }, [tab]);

  useEffect(() => {
    try {
      localStorage.setItem("agent_org_auto_refresh", autoRefresh ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [autoRefresh]);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const tabParam = params.get("tab");
    const desiredTab: TabKey = isTabKey(tabParam) ? tabParam : "overview";
    const desiredFromRaw = params.get("from");
    const desiredFrom = (desiredFromRaw ?? "").trim();
    const hasFrom = desiredFromRaw != null;
    const desiredQuery = params.get("q") ?? "";
    const desiredAuto = params.get("auto") === "1";

    setTab((current) => (current === desiredTab ? current : desiredTab));
    setActorName((current) => {
      if (!hasFrom) return current;
      const next = desiredFrom || current;
      return current === next ? current : next;
    });
    setQuery((current) => (current === desiredQuery ? current : desiredQuery));
    setAutoRefresh((current) => (current === desiredAuto ? current : desiredAuto));
  }, [searchParamsString]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const next = new URLSearchParams(window.location.search);
    const normalizedActor = actorName.trim();
    const normalizedQuery = query.trim();

    if (tab && tab !== "overview") {
      next.set("tab", tab);
    } else {
      next.delete("tab");
    }

    if (normalizedActor && normalizedActor !== "dd") {
      next.set("from", normalizedActor);
    } else {
      next.delete("from");
    }

    if (normalizedQuery) {
      next.set("q", normalizedQuery);
    } else {
      next.delete("q");
    }

    if (autoRefresh) {
      next.set("auto", "1");
    } else {
      next.delete("auto");
    }

    if (next.toString() === window.location.search.replace(/^\?/, "")) {
      return;
    }
    setSearchParams(next, { replace: true });
  }, [actorName, autoRefresh, query, setSearchParams, tab]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => {
      if (!loading) {
        void loadAll();
      }
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadAll, loading]);

  useEffect(() => {
    const first = agents[0];
    if (!noteTo && first?.name) setNoteTo(first.name);
    if (!roleAgentId && first?.id) setRoleAgentId(first.id);
    if (!assignAgentId && first?.id) setAssignAgentId(first.id);
  }, [agents, noteTo, roleAgentId, assignAgentId]);

  useEffect(() => {
    return () => {
      if (copyNoticeTimerRef.current) {
        clearTimeout(copyNoticeTimerRef.current);
        copyNoticeTimerRef.current = null;
      }
    };
  }, []);

  const handleCopy = useCallback((value: string, label: string) => {
    if (!value) return;
    navigator.clipboard
      .writeText(value)
      .then(() => {
        if (copyNoticeTimerRef.current) {
          clearTimeout(copyNoticeTimerRef.current);
        }
        setCopyNotice(`${label} をコピーしました`);
        copyNoticeTimerRef.current = setTimeout(() => {
          setCopyNotice(null);
          copyNoticeTimerRef.current = null;
        }, 2500);
      })
      .catch(() => {
        if (copyNoticeTimerRef.current) {
          clearTimeout(copyNoticeTimerRef.current);
        }
        setCopyNotice("コピーに失敗しました");
        copyNoticeTimerRef.current = setTimeout(() => {
          setCopyNotice(null);
          copyNoticeTimerRef.current = null;
        }, 2500);
      });
  }, []);

  const handleSendNote = useCallback(async () => {
    setActionMessage(null);
    setActionError(null);
    setActionResponse(null);
    try {
      if (!noteTo.trim()) throw new Error("to is required");
      if (!noteSubject.trim()) throw new Error("subject is required");
      const resp = await postJson<Record<string, unknown>>("/api/agent-org/notes", {
        to: noteTo.trim(),
        subject: noteSubject.trim(),
        body: noteBody,
        ttl_min: noteTtlMin.trim() ? Number(noteTtlMin.trim()) : undefined,
        from: actorName.trim() || "dd",
      });
      setActionMessage("note を送信しました");
      setActionResponse(resp);
      setNoteBody("");
      await loadAll();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }, [actorName, loadAll, noteBody, noteSubject, noteTo, noteTtlMin]);

  const sendOrchestratorRequest = useCallback(
    async (action: string, payload: Record<string, unknown>) => {
      setActionMessage(null);
      setActionError(null);
      setActionResponse(null);
      try {
        const canWait = Boolean(orch?.lock_held && orch?.pid_alive);
        const resp = await postJson<Record<string, unknown>>("/api/agent-org/orchestrator/request", {
          action,
          payload,
          from: actorName.trim() || "dd",
          wait_sec: canWait ? 3 : 0,
        });
        const hasResponse = "response" in resp;
        setActionMessage(`orchestrator: ${action} を${hasResponse ? "実行" : "キュー投入"}しました`);
        setActionResponse(resp);
        await loadAll();
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    },
    [actorName, loadAll, orch]
  );

  const handleSetRole = useCallback(async () => {
    if (!roleAgentId.trim()) {
      setActionError("agent is required");
      return;
    }
    if (!roleValue.trim()) {
      setActionError("role is required");
      return;
    }
    await sendOrchestratorRequest("set_role", { agent_id: roleAgentId.trim(), role: roleValue.trim() });
  }, [roleAgentId, roleValue, sendOrchestratorRequest]);

  const handleAssignTask = useCallback(async () => {
    if (!assignTaskId.trim()) {
      setActionError("task_id is required");
      return;
    }
    if (!assignAgentId.trim()) {
      setActionError("agent is required");
      return;
    }
    await sendOrchestratorRequest("assign_task", {
      task_id: assignTaskId.trim(),
      agent_id: assignAgentId.trim(),
      note: assignNote.trim() || undefined,
    });
    setAssignTaskId("");
    setAssignNote("");
  }, [assignAgentId, assignNote, assignTaskId, sendOrchestratorRequest]);

  const handleLock = useCallback(async () => {
    setActionMessage(null);
    setActionError(null);
    setActionResponse(null);
    const scopes = lockScopes
      .split(/[\n,]+/g)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      if (!scopes.length) {
        throw new Error("scopes is required");
      }
      const resp = await postJson<Record<string, unknown>>("/api/agent-org/locks", {
        scopes,
        mode: lockMode,
        ttl_min: lockTtlMin.trim() ? Number(lockTtlMin.trim()) : undefined,
        note: lockNote.trim() || undefined,
        from: actorName.trim() || "dd",
      });
      setActionMessage("lock を作成しました");
      setActionResponse(resp);
      setLockNote("");
      await loadAll();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }, [actorName, loadAll, lockMode, lockNote, lockScopes, lockTtlMin]);

  const handleUnlock = useCallback(
    async (lockId?: string) => {
      setActionMessage(null);
      setActionError(null);
      setActionResponse(null);
      const target = (lockId ?? unlockLockId).trim();
      try {
        if (!target) {
          throw new Error("lock_id is required");
        }
        const resp = await postJson<Record<string, unknown>>("/api/agent-org/locks/unlock", {
          lock_id: target,
          from: actorName.trim() || "dd",
        });
        setActionMessage("lock を解除しました");
        setActionResponse(resp);
        setUnlockLockId("");
        await loadAll();
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    },
    [actorName, loadAll, unlockLockId]
  );

  const handleSelectMemo = useCallback(async (id: string) => {
    setSelectedMemoId(id);
    setMemoDetail(null);
    try {
      const data = await fetchJson<Record<string, unknown>>(`/api/agent-org/memos/${encodeURIComponent(id)}`);
      setMemoDetail(data);
    } catch (e) {
      setMemoDetail({ error: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  const handleSelectNote = useCallback(async (id: string) => {
    setSelectedNoteId(id);
    setNoteDetail(null);
    try {
      const data = await fetchJson<Record<string, unknown>>(`/api/agent-org/notes/${encodeURIComponent(id)}`);
      setNoteDetail(data);
    } catch (e) {
      setNoteDetail({ error: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  const orchSummary = useMemo(() => {
    if (!orch) {
      return "(no orchestrator info)";
    }
    const name = String(orch.state?.name ?? "-");
    const pid = String(orch.state?.pid ?? "-");
    const hb = orch.heartbeat_age_sec == null ? "-" : `${orch.heartbeat_age_sec}s`;
    const status = orch.lock_held && orch.pid_alive ? "running" : "stopped";
    return `${status} / name=${name} / pid=${pid} / heartbeat=${hb}`;
  }, [orch]);

  const queryKey = useMemo(() => normalizeSearch(query), [query]);
  const searchPlaceholder = useMemo(() => {
    switch (tab) {
      case "agents":
        return "Search: name / role / id / pid";
      case "locks":
        return "Search: lock_id / scope / note";
      case "memos":
        return "Search: subject / from / to / id";
      case "notes":
        return "Search: subject / from / to / id";
      case "overview":
      default:
        return "Search: agents / locks / memos / notes";
    }
  }, [tab]);

  const agentCounts = useMemo(() => {
    return agents.reduce(
      (acc, row) => {
        acc[row.status] += 1;
        return acc;
      },
      { active: 0, stale: 0, dead: 0 }
    );
  }, [agents]);

  const activeLocks = useMemo(() => locks.filter((l) => l.status === "active"), [locks]);

  const agentsView = useMemo(() => {
    const filtered = queryKey
      ? agents.filter((a) =>
          joinNonEmpty([a.status, a.name, a.role, a.id, a.pid, a.host_pid, a.last_seen_at]).toLowerCase().includes(queryKey)
        )
      : agents;
    return [...filtered].sort((a, b) => {
      const rank: Record<AgentRow["status"], number> = { active: 0, stale: 1, dead: 2 };
      const ra = rank[a.status] ?? 9;
      const rb = rank[b.status] ?? 9;
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name, "ja-JP");
    });
  }, [agents, queryKey]);

  const locksView = useMemo(() => {
    const filtered = queryKey
      ? locks.filter((l) =>
          joinNonEmpty([l.status, l.id, l.mode, l.created_by, l.expires_at, l.note, (l.scopes ?? []).join(",")])
            .toLowerCase()
            .includes(queryKey)
        )
      : locks;
    return [...filtered].sort((a, b) => {
      const rank: Record<LockRow["status"], number> = { active: 0, expired: 1 };
      const ra = rank[a.status] ?? 9;
      const rb = rank[b.status] ?? 9;
      if (ra !== rb) return ra - rb;
      return String(a.expires_at ?? "").localeCompare(String(b.expires_at ?? ""));
    });
  }, [locks, queryKey]);

  const memosView = useMemo(() => {
    const filtered = queryKey
      ? memos.filter((m) =>
          joinNonEmpty([m.id, m.created_at, m.from, (m.to ?? []).join(","), m.subject, m.related_task_id])
            .toLowerCase()
            .includes(queryKey)
        )
      : memos;
    return [...filtered].sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  }, [memos, queryKey]);

  const notesView = useMemo(() => {
    const filtered = queryKey
      ? notes.filter((n) =>
          joinNonEmpty([n.id, n.status, n.created_at, n.from, n.to, n.subject]).toLowerCase().includes(queryKey)
        )
      : notes;
    return [...filtered].sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  }, [notes, queryKey]);

  const tabs = useMemo(
    (): Array<{ key: TabKey; label: string; count?: number }> => [
      { key: "overview", label: "概要" },
      { key: "agents", label: "Agents", count: agents.length },
      { key: "locks", label: "Locks", count: activeLocks.length },
      { key: "memos", label: "Memos", count: memos.length },
      { key: "notes", label: "Notes", count: notes.length },
      { key: "actions", label: "Actions" },
    ],
    [activeLocks.length, agents.length, memos.length, notes.length]
  );

  return (
    <section className="agent-org-page">
      <header className="agent-org-page__header">
        <div>
          <h1 className="agent-org-page__title">AI Org（協調）</h1>
          <p className="agent-org-page__subtitle">エージェント / ロック / メモ / ノートの状態確認と、オーケストレーター操作。</p>
        </div>
        <div className="agent-org-page__meta">
          最終更新: {formatLastUpdated(lastUpdatedAt)} / {orchSummary}
        </div>
      </header>

      <div className="agent-org-page__toolbar">
        <button
          type="button"
          className="action-button action-button--primary"
          onClick={() => void loadAll()}
          disabled={loading}
        >
          {loading ? "更新中…" : "再読み込み"}
        </button>

        <label className="action-toggle">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> 自動更新(10s)
        </label>

        <button
          type="button"
          className="action-button"
          onClick={() => {
            if (typeof window === "undefined") return;
            handleCopy(window.location.href, "link");
          }}
        >
          リンクコピー
        </button>

        {tab !== "actions" ? (
          <div className="agent-org-page__search" aria-label="Search">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={searchPlaceholder} />
            {query ? (
              <button type="button" className="agent-org-page__clear" onClick={() => setQuery("")}>
                クリア
              </button>
            ) : null}
          </div>
        ) : (
          <div className="agent-org-page__spacer" />
        )}

        <div className="agent-org-page__spacer" />

        <label className="agent-org-page__actor">
          <span>from</span>
          <input value={actorName} onChange={(e) => setActorName(e.target.value)} />
        </label>

        {copyNotice ? <span className="agent-org-page__notice agent-org-page__notice--ok">{copyNotice}</span> : null}
        {actionMessage ? <span className="agent-org-page__notice agent-org-page__notice--ok">{actionMessage}</span> : null}
        {actionError ? <span className="agent-org-page__notice agent-org-page__notice--err">{actionError}</span> : null}
      </div>

      {error ? <div className="agent-org-page__notice agent-org-page__notice--err">取得に失敗しました: {error}</div> : null}

      <div className="agent-org-page__tabs" role="tablist" aria-label="AI Org Tabs">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            className={tab === item.key ? "agent-org-page__tab is-active" : "agent-org-page__tab"}
            onClick={() => setTab(item.key)}
          >
            {item.label}
            {typeof item.count === "number" ? <span className="agent-org-page__tab-count">{item.count}</span> : null}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <div className="agent-org-page__card">
          <h2>概要</h2>
          <div className="agent-org-page__kpis">
            <div className="agent-org-page__kpi">
              <span>Agents</span>
              <strong>
                active {agentCounts.active} / stale {agentCounts.stale} / dead {agentCounts.dead}
              </strong>
            </div>
            <div className="agent-org-page__kpi">
              <span>Locks</span>
              <strong>
                active {activeLocks.length} / total {locks.length}
              </strong>
            </div>
            <div className="agent-org-page__kpi">
              <span>Memos</span>
              <strong>{memos.length}</strong>
            </div>
            <div className="agent-org-page__kpi">
              <span>Notes</span>
              <strong>{notes.length}</strong>
            </div>
          </div>
          <details>
            <summary className="agent-org-page__meta" style={{ cursor: "pointer" }}>
              orchestrator state JSON
            </summary>
            <pre className="agent-org-page__json">{JSON.stringify(orch?.state ?? {}, null, 2)}</pre>
          </details>
          {actionResponse ? (
            <details>
              <summary className="agent-org-page__meta" style={{ cursor: "pointer" }}>
                last response
              </summary>
              <pre className="agent-org-page__json">{JSON.stringify(actionResponse, null, 2)}</pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {tab === "agents" ? (
        <div className="agent-org-page__card">
          <h2>
            Agents <span className="agent-org-page__meta">({agentsView.length}/{agents.length})</span>
          </h2>
          {agentsView.length ? (
            <div className="agent-org-page__table-wrapper">
              <table className="agent-org-page__table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Name</th>
                    <th>Role</th>
                    <th>PID</th>
                    <th>Last Seen</th>
                    <th>ID</th>
                  </tr>
                </thead>
                <tbody>
                  {agentsView.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <span className={agentBadgeClass(a.status)}>{a.status}</span>
                      </td>
                      <td>{a.name}</td>
                      <td>{a.role}</td>
                      <td>{a.pid ?? "-"}</td>
                      <td>{formatDateTime(a.last_seen_at)}</td>
                      <td>
                        <span className="agent-org-page__mono">{a.id}</span>{" "}
                        <button type="button" className="action-chip" onClick={() => handleCopy(a.id, "agent_id")}>
                          copy
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div>(no agents)</div>
          )}
        </div>
      ) : null}

      {tab === "locks" ? (
        <div className="agent-org-page__card">
          <h2>
            Locks <span className="agent-org-page__meta">({locksView.length}/{locks.length})</span>
          </h2>
          {locksView.length ? (
            <div className="agent-org-page__table-wrapper">
              <table className="agent-org-page__table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Mode</th>
                    <th>By</th>
                    <th>Scopes</th>
                    <th>Expires</th>
                    <th>Note</th>
                    <th>ID</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {locksView.map((l) => (
                    <tr key={l.id}>
                      <td>
                        <span className={lockBadgeClass(l.status)}>{l.status}</span>
                      </td>
                      <td>{l.mode ?? "-"}</td>
                      <td className="agent-org-page__mono">{l.created_by ?? "-"}</td>
                      <td className="agent-org-page__mono">{(l.scopes ?? []).join(", ")}</td>
                      <td>{formatDateTime(l.expires_at)}</td>
                      <td>{l.note ?? "-"}</td>
                      <td>
                        <span className="agent-org-page__mono">{l.id}</span>{" "}
                        <button type="button" className="action-chip" onClick={() => handleCopy(l.id, "lock_id")}>
                          copy
                        </button>
                      </td>
                      <td>
                        {l.status === "active" ? (
                          <button
                            type="button"
                            className="action-chip action-chip--warn"
                            onClick={() => void handleUnlock(l.id)}
                            disabled={loading}
                          >
                            unlock
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div>(no locks)</div>
          )}
        </div>
      ) : null}

      {tab === "memos" ? (
        <div className="agent-org-page__card">
          <h2>
            Memos <span className="agent-org-page__meta">({memosView.length}/{memos.length})</span>
          </h2>
          {memosView.length ? (
            <div className="agent-org-page__split">
              <div className="agent-org-page__table-wrapper">
                <table className="agent-org-page__table">
                  <thead>
                    <tr>
                      <th>At</th>
                      <th>From</th>
                      <th>To</th>
                      <th>Subject</th>
                    </tr>
                  </thead>
                  <tbody>
                    {memosView.map((m) => (
                      <tr
                        key={m.id}
                        className={selectedMemoId === m.id ? "agent-org-page__row is-selected" : "agent-org-page__row"}
                        onClick={() => void handleSelectMemo(m.id)}
                      >
                        <td>{formatDateTime(m.created_at)}</td>
                        <td>{m.from ?? "-"}</td>
                        <td>{(m.to ?? []).join(", ")}</td>
                        <td>{m.subject ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <h3 style={{ margin: 0 }}>Detail</h3>
                  {selectedMemoId ? (
                    <button type="button" className="action-chip" onClick={() => handleCopy(selectedMemoId, "memo_id")}>
                      copy memo_id
                    </button>
                  ) : null}
                </div>
                <pre className="agent-org-page__json">
                  {memoDetail ? JSON.stringify(memoDetail, null, 2) : "(select a memo)"}
                </pre>
              </div>
            </div>
          ) : (
            <div>(no memos)</div>
          )}
        </div>
      ) : null}

      {tab === "notes" ? (
        <div className="agent-org-page__card">
          <h2>
            Notes <span className="agent-org-page__meta">({notesView.length}/{notes.length})</span>
          </h2>
          {notesView.length ? (
            <div className="agent-org-page__split">
              <div className="agent-org-page__table-wrapper">
                <table className="agent-org-page__table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>From</th>
                      <th>To</th>
                      <th>Subject</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notesView.map((n) => (
                      <tr
                        key={n.id}
                        className={selectedNoteId === n.id ? "agent-org-page__row is-selected" : "agent-org-page__row"}
                        onClick={() => void handleSelectNote(n.id)}
                      >
                        <td>
                          <span
                            className={
                              n.status === "active"
                                ? "agent-org-page__badge agent-org-page__badge--ok"
                                : "agent-org-page__badge"
                            }
                          >
                            {n.status}
                          </span>
                        </td>
                        <td>{n.from ?? "-"}</td>
                        <td>{n.to ?? "-"}</td>
                        <td>{n.subject ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <h3 style={{ margin: 0 }}>Detail</h3>
                  {selectedNoteId ? (
                    <button type="button" className="action-chip" onClick={() => handleCopy(selectedNoteId, "note_id")}>
                      copy note_id
                    </button>
                  ) : null}
                </div>
                <pre className="agent-org-page__json">
                  {noteDetail ? JSON.stringify(noteDetail, null, 2) : "(select a note)"}
                </pre>
              </div>
            </div>
          ) : (
            <div>(no notes)</div>
          )}
        </div>
      ) : null}

      {tab === "actions" ? (
        <div className="agent-org-page__card">
          <h2>Actions</h2>
          <div className="agent-org-page__actions">
            <div className="agent-org-page__panel">
              <h4>Send note</h4>
              <div className="agent-org-page__fields">
                <label className="agent-org-page__field">
                  <span>to</span>
                  <select value={noteTo} onChange={(e) => setNoteTo(e.target.value)}>
                    <option value="">(select)</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.name}>
                        {a.name} ({a.role})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="agent-org-page__field">
                  <span>subject</span>
                  <input value={noteSubject} onChange={(e) => setNoteSubject(e.target.value)} />
                </label>
                <label className="agent-org-page__field">
                  <span>ttl_min</span>
                  <input value={noteTtlMin} onChange={(e) => setNoteTtlMin(e.target.value)} inputMode="numeric" />
                </label>
                <label className="agent-org-page__field">
                  <span>body</span>
                  <textarea value={noteBody} onChange={(e) => setNoteBody(e.target.value)} rows={6} />
                </label>
                <button
                  type="button"
                  className="action-button action-button--primary"
                  onClick={() => void handleSendNote()}
                  disabled={loading}
                >
                  send
                </button>
              </div>
            </div>

            <div className="agent-org-page__panel">
              <h4>Set role</h4>
              <div className="agent-org-page__fields">
                <label className="agent-org-page__field">
                  <span>agent</span>
                  <select value={roleAgentId} onChange={(e) => setRoleAgentId(e.target.value)}>
                    <option value="">(select)</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} (id={a.id}, pid={a.pid ?? "-"})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="agent-org-page__field">
                  <span>role</span>
                  <input value={roleValue} onChange={(e) => setRoleValue(e.target.value)} placeholder="worker" />
                </label>
                <button
                  type="button"
                  className="action-button action-button--primary"
                  onClick={() => void handleSetRole()}
                  disabled={loading}
                >
                  apply
                </button>
              </div>
            </div>

            <div className="agent-org-page__panel">
              <h4>Assign task</h4>
              <div className="agent-org-page__fields">
                <label className="agent-org-page__field">
                  <span>task_id</span>
                  <input value={assignTaskId} onChange={(e) => setAssignTaskId(e.target.value)} placeholder="CH12-001" />
                </label>
                <label className="agent-org-page__field">
                  <span>agent</span>
                  <select value={assignAgentId} onChange={(e) => setAssignAgentId(e.target.value)}>
                    <option value="">(select)</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} (id={a.id}, pid={a.pid ?? "-"})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="agent-org-page__field">
                  <span>note (optional)</span>
                  <input value={assignNote} onChange={(e) => setAssignNote(e.target.value)} />
                </label>
                <button
                  type="button"
                  className="action-button action-button--primary"
                  onClick={() => void handleAssignTask()}
                  disabled={loading}
                >
                  assign
                </button>
              </div>
            </div>

            <div className="agent-org-page__panel">
              <h4>Lock / Unlock</h4>
              <div className="agent-org-page__fields">
                <label className="agent-org-page__field">
                  <span>scopes (newline / comma)</span>
                  <textarea value={lockScopes} onChange={(e) => setLockScopes(e.target.value)} rows={3} />
                </label>
                <label className="agent-org-page__field">
                  <span>mode</span>
                  <select value={lockMode} onChange={(e) => setLockMode(e.target.value)}>
                    <option value="no_touch">no_touch</option>
                    <option value="no_write">no_write</option>
                    <option value="read_only">read_only</option>
                  </select>
                </label>
                <label className="agent-org-page__field">
                  <span>ttl_min</span>
                  <input value={lockTtlMin} onChange={(e) => setLockTtlMin(e.target.value)} inputMode="numeric" placeholder="60" />
                </label>
                <label className="agent-org-page__field">
                  <span>note (optional)</span>
                  <input value={lockNote} onChange={(e) => setLockNote(e.target.value)} placeholder="reason" />
                </label>
                <button
                  type="button"
                  className="action-button action-button--primary"
                  onClick={() => void handleLock()}
                  disabled={loading}
                >
                  lock
                </button>
                <hr style={{ border: "none", borderTop: "1px solid rgba(148, 163, 184, 0.22)" }} />
                <label className="agent-org-page__field">
                  <span>unlock lock_id</span>
                  <input value={unlockLockId} onChange={(e) => setUnlockLockId(e.target.value)} placeholder="lock__..." />
                </label>
                <button type="button" className="action-button" onClick={() => void handleUnlock()} disabled={loading}>
                  unlock
                </button>
              </div>
            </div>
          </div>

          {actionResponse ? (
            <details>
              <summary className="agent-org-page__meta" style={{ cursor: "pointer" }}>
                last response
              </summary>
              <pre className="agent-org-page__json">{JSON.stringify(actionResponse, null, 2)}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
