import { useCallback, useEffect, useMemo, useState } from "react";

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

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [actorName, setActorName] = useState(() => {
    try {
      return localStorage.getItem("agent_org_actor") || "dd";
    } catch {
      return "dd";
    }
  });

  const [orch, setOrch] = useState<OrchestratorStatus | null>(null);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [memos, setMemos] = useState<MemoRow[]>([]);
  const [notes, setNotes] = useState<NoteRow[]>([]);
  const [locks, setLocks] = useState<LockRow[]>([]);

  const [noteTo, setNoteTo] = useState("");
  const [noteSubject, setNoteSubject] = useState("no-touch");
  const [noteBody, setNoteBody] = useState("");
  const [noteTtlMin, setNoteTtlMin] = useState("60");

  const [roleAgent, setRoleAgent] = useState("");
  const [roleValue, setRoleValue] = useState("worker");

  const [assignTaskId, setAssignTaskId] = useState("");
  const [assignAgent, setAssignAgent] = useState("");
  const [assignNote, setAssignNote] = useState("");

  const [lockScopes, setLockScopes] = useState("ui/**");
  const [lockMode, setLockMode] = useState("no_touch");
  const [lockTtlMin, setLockTtlMin] = useState("60");
  const [lockNote, setLockNote] = useState("");

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
    const first = agents[0]?.name || "";
    if (!noteTo && first) setNoteTo(first);
    if (!roleAgent && first) setRoleAgent(first);
    if (!assignAgent && first) setAssignAgent(first);
  }, [agents, noteTo, roleAgent, assignAgent]);

  const handleSendNote = useCallback(async () => {
    setActionMessage(null);
    setActionError(null);
    try {
      if (!noteTo.trim()) throw new Error("to is required");
      if (!noteSubject.trim()) throw new Error("subject is required");
      await postJson("/api/agent-org/notes", {
        to: noteTo.trim(),
        subject: noteSubject.trim(),
        body: noteBody,
        ttl_min: noteTtlMin.trim() ? Number(noteTtlMin.trim()) : undefined,
        from: actorName.trim() || "dd",
      });
      setActionMessage("note を送信しました");
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
      try {
        await postJson("/api/agent-org/orchestrator/request", {
          action,
          payload,
          from: actorName.trim() || "dd",
          wait_sec: 3,
        });
        setActionMessage(`orchestrator: ${action} を送信しました`);
        await loadAll();
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    },
    [actorName, loadAll]
  );

  const handleSetRole = useCallback(async () => {
    if (!roleAgent.trim()) {
      setActionError("agent is required");
      return;
    }
    if (!roleValue.trim()) {
      setActionError("role is required");
      return;
    }
    await sendOrchestratorRequest("set_role", { agent_name: roleAgent.trim(), role: roleValue.trim() });
  }, [roleAgent, roleValue, sendOrchestratorRequest]);

  const handleAssignTask = useCallback(async () => {
    if (!assignTaskId.trim()) {
      setActionError("task_id is required");
      return;
    }
    if (!assignAgent.trim()) {
      setActionError("agent is required");
      return;
    }
    await sendOrchestratorRequest("assign_task", {
      task_id: assignTaskId.trim(),
      agent_name: assignAgent.trim(),
      note: assignNote.trim() || undefined,
    });
    setAssignTaskId("");
    setAssignNote("");
  }, [assignAgent, assignNote, assignTaskId, sendOrchestratorRequest]);

  const handleLock = useCallback(async () => {
    const scopes = lockScopes
      .split(/[\n,]+/g)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!scopes.length) {
      setActionError("scopes is required");
      return;
    }
    await sendOrchestratorRequest("lock", {
      scopes,
      mode: lockMode,
      ttl_min: lockTtlMin.trim() ? Number(lockTtlMin.trim()) : undefined,
      note: lockNote.trim() || undefined,
    });
    setLockNote("");
  }, [lockMode, lockNote, lockScopes, lockTtlMin, sendOrchestratorRequest]);

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

  return (
    <div className="page agent-org-page" style={{ padding: 16, display: "grid", gap: 12 }}>
      <h1>AI Org（協調）</h1>
      {error && (
        <div className="error" style={{ color: "red" }}>
          {error}
        </div>
      )}

      <div className="card" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={loadAll} disabled={loading}>
          再読み込み
        </button>
        <div style={{ opacity: loading ? 0.6 : 1 }}>{orchSummary}</div>
      </div>

      <div className="card" style={{ padding: 12, display: "grid", gap: 12 }}>
        <h3>Actions</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ opacity: 0.7 }}>from</span>
            <input
              value={actorName}
              onChange={(e) => setActorName(e.target.value)}
              style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc", minWidth: 140 }}
            />
          </label>
          {actionMessage && <span style={{ color: "#0a7f33" }}>{actionMessage}</span>}
          {actionError && <span style={{ color: "red" }}>{actionError}</span>}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 12 }}>
            <h4 style={{ marginTop: 0 }}>Send note</h4>
            <div style={{ display: "grid", gap: 8 }}>
              <label style={{ display: "grid", gap: 4 }}>
                <span style={{ opacity: 0.7 }}>to</span>
                <select
                  value={noteTo}
                  onChange={(e) => setNoteTo(e.target.value)}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                >
                  <option value="">(select)</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.name}>
                      {a.name} ({a.role})
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: "grid", gap: 4 }}>
                <span style={{ opacity: 0.7 }}>subject</span>
                <input
                  value={noteSubject}
                  onChange={(e) => setNoteSubject(e.target.value)}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
              </label>
              <label style={{ display: "grid", gap: 4 }}>
                <span style={{ opacity: 0.7 }}>ttl (min)</span>
                <input
                  value={noteTtlMin}
                  onChange={(e) => setNoteTtlMin(e.target.value)}
                  inputMode="numeric"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc", maxWidth: 140 }}
                />
              </label>
              <label style={{ display: "grid", gap: 4 }}>
                <span style={{ opacity: 0.7 }}>body</span>
                <textarea
                  value={noteBody}
                  onChange={(e) => setNoteBody(e.target.value)}
                  rows={4}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
              </label>
              <button onClick={() => void handleSendNote()} disabled={loading}>
                送信
              </button>
            </div>
          </div>

          <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 12 }}>
            <h4 style={{ marginTop: 0 }}>Orchestrator</h4>
            <div style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "grid", gap: 8 }}>
                <strong>Set role</strong>
                <select
                  value={roleAgent}
                  onChange={(e) => setRoleAgent(e.target.value)}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                >
                  <option value="">(select)</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <input
                  value={roleValue}
                  onChange={(e) => setRoleValue(e.target.value)}
                  placeholder="role"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
                <button onClick={() => void handleSetRole()} disabled={loading}>
                  apply
                </button>
              </div>

              <div style={{ display: "grid", gap: 8 }}>
                <strong>Assign task</strong>
                <input
                  value={assignTaskId}
                  onChange={(e) => setAssignTaskId(e.target.value)}
                  placeholder="task_id (e.g. CH06-002)"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
                <select
                  value={assignAgent}
                  onChange={(e) => setAssignAgent(e.target.value)}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                >
                  <option value="">(select)</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <input
                  value={assignNote}
                  onChange={(e) => setAssignNote(e.target.value)}
                  placeholder="note (optional)"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
                <button onClick={() => void handleAssignTask()} disabled={loading}>
                  assign
                </button>
              </div>

              <div style={{ display: "grid", gap: 8 }}>
                <strong>Lock</strong>
                <textarea
                  value={lockScopes}
                  onChange={(e) => setLockScopes(e.target.value)}
                  rows={3}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc", fontFamily: "monospace" }}
                />
                <input
                  value={lockMode}
                  onChange={(e) => setLockMode(e.target.value)}
                  placeholder="mode"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
                <input
                  value={lockTtlMin}
                  onChange={(e) => setLockTtlMin(e.target.value)}
                  inputMode="numeric"
                  placeholder="ttl_min"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc", maxWidth: 140 }}
                />
                <input
                  value={lockNote}
                  onChange={(e) => setLockNote(e.target.value)}
                  placeholder="note (optional)"
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #ccc" }}
                />
                <button onClick={() => void handleLock()} disabled={loading}>
                  lock
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 12 }}>
        <h3>Agents</h3>
        {agents.length ? (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th>Status</th>
                <th>Name</th>
                <th>Role</th>
                <th>PID</th>
                <th>Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id}>
                  <td>{a.status}</td>
                  <td>{a.name}</td>
                  <td>{a.role}</td>
                  <td>{a.pid ?? "-"}</td>
                  <td>{a.last_seen_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div>(no agents)</div>
        )}
      </div>

      <div className="card" style={{ padding: 12 }}>
        <h3>Locks</h3>
        {locks.length ? (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th>Status</th>
                <th>Mode</th>
                <th>By</th>
                <th>Scopes</th>
                <th>Expires</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {locks.map((l) => (
                <tr key={l.id}>
                  <td>{l.status}</td>
                  <td>{l.mode ?? "-"}</td>
                  <td>{l.created_by ?? "-"}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>{(l.scopes ?? []).join(", ")}</td>
                  <td>{l.expires_at ?? "-"}</td>
                  <td>{l.note ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div>(no locks)</div>
        )}
      </div>

      <div className="card" style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <h3>Memos</h3>
          {memos.length ? (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th>At</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Subject</th>
                </tr>
              </thead>
              <tbody>
                {memos.map((m) => (
                  <tr
                    key={m.id}
                    style={{ cursor: "pointer", background: selectedMemoId === m.id ? "#fff7d6" : "transparent" }}
                    onClick={() => void handleSelectMemo(m.id)}
                  >
                    <td>{m.created_at ?? "-"}</td>
                    <td>{m.from ?? "-"}</td>
                    <td>{(m.to ?? []).join(", ")}</td>
                    <td>{m.subject ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div>(no memos)</div>
          )}
        </div>

        <div>
          <h3>Notes</h3>
          {notes.length ? (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Subject</th>
                </tr>
              </thead>
              <tbody>
                {notes.map((n) => (
                  <tr
                    key={n.id}
                    style={{ cursor: "pointer", background: selectedNoteId === n.id ? "#fff7d6" : "transparent" }}
                    onClick={() => void handleSelectNote(n.id)}
                  >
                    <td>{n.status}</td>
                    <td>{n.from ?? "-"}</td>
                    <td>{n.to ?? "-"}</td>
                    <td>{n.subject ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div>(no notes)</div>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <h3>Memo Detail</h3>
          <pre style={{ whiteSpace: "pre-wrap", background: "#f7f7f7", padding: 12, borderRadius: 8 }}>
            {memoDetail ? JSON.stringify(memoDetail, null, 2) : "(select a memo)"}
          </pre>
        </div>
        <div>
          <h3>Note Detail</h3>
          <pre style={{ whiteSpace: "pre-wrap", background: "#f7f7f7", padding: 12, borderRadius: 8 }}>
            {noteDetail ? JSON.stringify(noteDetail, null, 2) : "(select a note)"}
          </pre>
        </div>
      </div>
    </div>
  );
}
