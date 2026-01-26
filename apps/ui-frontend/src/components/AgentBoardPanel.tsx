import { useCallback, useEffect, useMemo, useState } from "react";
import "./AgentBoardPanel.css";

import { getAgentBoard, postAgentBoardNote, postAgentBoardStatus } from "../api/agentBoard";
import type { AgentBoard, AgentBoardAgentStatus, AgentBoardNote } from "../types/agentBoard";

type AgentBoardPanelProps = {
  actorName: string;
};

type ThreadSummary = {
  threadId: string;
  topic: string;
  lastTs: string;
  count: number;
  participants: string[];
  lastNote: AgentBoardNote;
};

type AgentStatusRow = {
  agent: string;
  status: AgentBoardAgentStatus;
  updatedAt: string;
  ageMinutes: number | null;
  isBlocked: boolean;
  tags: string[];
};

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", { hour12: false });
}

function parseIsoMillis(value?: string | null): number | null {
  if (!value) return null;
  const date = new Date(value);
  const t = date.getTime();
  return Number.isNaN(t) ? null : t;
}

function formatAge(ageMinutes: number | null): string {
  if (ageMinutes == null) return "—";
  if (ageMinutes < 1) return "<1m";
  if (ageMinutes < 60) return `${Math.floor(ageMinutes)}m`;
  const h = Math.floor(ageMinutes / 60);
  const m = Math.floor(ageMinutes % 60);
  if (h < 24) return `${h}h${m ? `${m}m` : ""}`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return `${d}d${rh ? `${rh}h` : ""}`;
}

function safeString(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

function normalizeSearch(value: string): string {
  return value.trim().toLowerCase();
}

function isBlockedStatus(status: AgentBoardAgentStatus | null | undefined): boolean {
  const raw = safeString(status?.blocked).trim();
  if (!raw) return false;
  if (raw === "-") return false;
  return true;
}

function buildAgentRows(board: AgentBoard | null): AgentStatusRow[] {
  if (!board) return [];
  const now = Date.now();
  return Object.entries(board.agents ?? {})
    .map(([agent, status]) => {
      const updatedAt = safeString(status?.updated_at).trim();
      const updatedMillis = parseIsoMillis(updatedAt);
      const ageMinutes = updatedMillis == null ? null : (now - updatedMillis) / 60000;
      const tags = Array.isArray(status?.tags)
        ? status.tags.map((t) => safeString(t).trim()).filter(Boolean)
        : [];
      return {
        agent,
        status: status ?? {},
        updatedAt,
        ageMinutes,
        isBlocked: isBlockedStatus(status),
        tags,
      };
    })
    .sort((a, b) => safeString(b.updatedAt).localeCompare(safeString(a.updatedAt)));
}

function buildThreads(board: AgentBoard | null): ThreadSummary[] {
  if (!board) return [];
  const log = Array.isArray(board.log) ? board.log : [];
  const threads = new Map<string, AgentBoardNote[]>();
  for (const entry of log) {
    if (!entry || typeof entry !== "object") continue;
    const note = entry as AgentBoardNote;
    const threadId = note.thread_id || note.id;
    if (!threadId) continue;
    const bucket = threads.get(threadId) ?? [];
    bucket.push(note);
    threads.set(threadId, bucket);
  }

  const out: ThreadSummary[] = [];
  threads.forEach((notes, threadId) => {
    notes.sort((a, b) => safeString(a.ts).localeCompare(safeString(b.ts)));
    const lastNote = notes[notes.length - 1];
    const firstNote = notes[0];
    const participants = Array.from(
      new Set(notes.map((n) => safeString(n.agent).trim()).filter(Boolean))
    ).sort((a, b) => a.localeCompare(b));
    out.push({
      threadId,
      topic: safeString(firstNote.topic) || safeString(lastNote.topic) || "(no topic)",
      lastTs: safeString(lastNote.ts) || "",
      count: notes.length,
      participants,
      lastNote,
    });
  });
  out.sort((a, b) => safeString(b.lastTs).localeCompare(safeString(a.lastTs)));
  return out;
}

export function AgentBoardPanel(props: AgentBoardPanelProps) {
  const actorName = (props.actorName || "ui").trim() || "ui";

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [board, setBoard] = useState<AgentBoard | null>(null);
  const [queueDir, setQueueDir] = useState<string>("");
  const [boardPath, setBoardPath] = useState<string>("");

  const [threadQuery, setThreadQuery] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);

  const [statusDoing, setStatusDoing] = useState("");
  const [statusBlocked, setStatusBlocked] = useState("");
  const [statusNext, setStatusNext] = useState("");
  const [statusNote, setStatusNote] = useState("");
  const [statusTags, setStatusTags] = useState("ui");

  const [agentQuery, setAgentQuery] = useState("");
  const [showBlockedOnly, setShowBlockedOnly] = useState(false);

  const [noteTopic, setNoteTopic] = useState("[FYI][coordination] ");
  const [noteMessage, setNoteMessage] = useState("");
  const [noteTags, setNoteTags] = useState("ui,coordination");
  const [noteReplyTo, setNoteReplyTo] = useState<string>("");

  const agentRows = useMemo(() => buildAgentRows(board), [board]);
  const threads = useMemo(() => buildThreads(board), [board]);

  const filteredAgentRows = useMemo(() => {
    const q = normalizeSearch(agentQuery);
    return agentRows.filter((row) => {
      if (showBlockedOnly && !row.isBlocked) return false;
      if (!q) return true;
      const st = row.status ?? {};
      const hay = normalizeSearch(
        [
          row.agent,
          safeString(st.doing),
          safeString(st.blocked),
          safeString(st.next),
          safeString(st.note),
          row.tags.join(" "),
        ].join(" ")
      );
      return hay.includes(q);
    });
  }, [agentQuery, agentRows, showBlockedOnly]);

  const blockedCount = useMemo(() => agentRows.filter((r) => r.isBlocked).length, [agentRows]);

  const selectedNotes = useMemo(() => {
    if (!board || !selectedThreadId) return [];
    const log = Array.isArray(board.log) ? board.log : [];
    return log
      .filter((n) => n && typeof n === "object")
      .map((n) => n as AgentBoardNote)
      .filter((n) => (n.thread_id || n.id) === selectedThreadId)
      .sort((a, b) => safeString(a.ts).localeCompare(safeString(b.ts)));
  }, [board, selectedThreadId]);

  useEffect(() => {
    if (!selectedThreadId) return;
    const last = selectedNotes[selectedNotes.length - 1];
    if (!last) return;
    setNoteReplyTo(last.id);
  }, [selectedThreadId, selectedNotes]);

  const filteredThreads = useMemo(() => {
    const q = normalizeSearch(threadQuery);
    if (!q) return threads;
    return threads.filter((t) => {
      const hay = normalizeSearch(`${t.topic} ${t.participants.join(" ")} ${t.lastNote.message || ""}`);
      return hay.includes(q);
    });
  }, [threadQuery, threads]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getAgentBoard();
      setBoard(resp.board);
      setQueueDir(resp.queue_dir);
      setBoardPath(resp.board_path);
      if (!selectedThreadId && resp.board?.log?.length) {
        const nextThreads = buildThreads(resp.board);
        if (nextThreads.length) setSelectedThreadId(nextThreads[0].threadId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [selectedThreadId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const cur = board?.agents?.[actorName];
    if (!cur) return;
    const anyFilled = Boolean(statusDoing.trim() || statusBlocked.trim() || statusNext.trim() || statusNote.trim());
    if (anyFilled) return;
    if (cur.doing) setStatusDoing(String(cur.doing));
    if (cur.blocked) setStatusBlocked(String(cur.blocked));
    if (cur.next) setStatusNext(String(cur.next));
    if (cur.note) setStatusNote(String(cur.note));
    if (Array.isArray(cur.tags) && cur.tags.length) setStatusTags(cur.tags.join(","));
  }, [actorName, board, statusBlocked, statusDoing, statusNext, statusNote]);

  const submitStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await postAgentBoardStatus({
        from: actorName,
        doing: statusDoing || null,
        blocked: statusBlocked || null,
        next: statusNext || null,
        note: statusNote || null,
        tags: statusTags || null,
        clear: false,
      });
      setBoard(resp.board);
      setQueueDir(resp.queue_dir);
      setBoardPath(resp.board_path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [actorName, statusDoing, statusBlocked, statusNext, statusNote, statusTags]);

  const clearStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await postAgentBoardStatus({ from: actorName, clear: true });
      setBoard(resp.board);
      setQueueDir(resp.queue_dir);
      setBoardPath(resp.board_path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [actorName]);

  const submitNote = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const topic = noteTopic.trim();
      const message = noteMessage.trim();
      if (!topic) throw new Error("topic is required");
      if (!message) throw new Error("message is required");
      const resp = await postAgentBoardNote({
        from: actorName,
        topic,
        message,
        reply_to: noteReplyTo.trim() || null,
        tags: noteTags || null,
      });
      await refresh();
      if (resp.thread_id) setSelectedThreadId(resp.thread_id);
      setNoteMessage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [actorName, noteTopic, noteMessage, noteReplyTo, noteTags, refresh]);

  return (
    <div className="agent-board">
      <div className="agent-board__header">
        <div>
          <div className="agent-board__title">Shared Board</div>
          <div className="agent-board__meta">
            <span>queue: {queueDir || "—"}</span>
            <span>path: {boardPath || "—"}</span>
            <span>updated: {formatDateTime(board?.updated_at)}</span>
          </div>
        </div>
        <div className="agent-board__actions">
          <button className="agent-board__btn" onClick={refresh} disabled={loading}>
            Refresh
          </button>
        </div>
      </div>

      {error ? <div className="agent-board__error">Error: {error}</div> : null}

      <div className="agent-board__grid">
        <div className="agent-board__panel agent-board__panel--wide">
          <div className="agent-board__panel-title">Now</div>
          <div className="agent-board__now-meta">
            <span>
              agents: <span className="agent-board__mono">{agentRows.length}</span>
            </span>
            <span>
              blocked:{" "}
              <span className={`agent-board__mono ${blockedCount ? "agent-board__danger" : ""}`}>{blockedCount}</span>
            </span>
            <span className="agent-board__muted">（このMacの board.json をそのまま表示）</span>
          </div>
          <div className="agent-board__now-controls">
            <input
              className="agent-board__input agent-board__input--compact"
              placeholder="search agent/doing/blocked/next/tags…"
              value={agentQuery}
              onChange={(e) => setAgentQuery(e.target.value)}
            />
            <label className="agent-board__checkbox">
              <input type="checkbox" checked={showBlockedOnly} onChange={(e) => setShowBlockedOnly(e.target.checked)} />
              blocked only
            </label>
          </div>
          <div className="agent-board__table-wrap">
            <table className="agent-board__table">
              <thead>
                <tr>
                  <th>agent</th>
                  <th>doing</th>
                  <th>blocked</th>
                  <th>next</th>
                  <th>tags</th>
                  <th>age</th>
                  <th>updated</th>
                </tr>
              </thead>
              <tbody>
                {filteredAgentRows.map((row) => {
                  const st = row.status ?? {};
                  const blocked = safeString(st.blocked).trim();
                  return (
                    <tr key={row.agent} className={row.isBlocked ? "agent-board__row--blocked" : ""}>
                      <td className="agent-board__mono">{row.agent}</td>
                      <td className="agent-board__note">{safeString(st.doing) || "—"}</td>
                      <td className={`agent-board__note ${blocked ? "agent-board__danger" : ""}`}>{blocked || "—"}</td>
                      <td className="agent-board__note">{safeString(st.next) || "—"}</td>
                      <td className="agent-board__mono">{row.tags.length ? row.tags.join(",") : "—"}</td>
                      <td className="agent-board__mono">{formatAge(row.ageMinutes)}</td>
                      <td>{formatDateTime(row.updatedAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="agent-board__panel">
          <div className="agent-board__panel-title">Ownership</div>
          <div className="agent-board__table-wrap">
            <table className="agent-board__table">
              <thead>
                <tr>
                  <th>area</th>
                  <th>owner</th>
                  <th>reviewers</th>
                  <th>updated</th>
                  <th>note</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(board?.areas ?? {})
                  .sort((a, b) => safeString(b[1]?.updated_at).localeCompare(safeString(a[1]?.updated_at)))
                  .map(([area, st]) => (
                    <tr key={area}>
                      <td className="agent-board__mono">{area}</td>
                      <td className="agent-board__mono">{safeString(st?.owner) || "—"}</td>
                      <td className="agent-board__mono">{Array.isArray(st?.reviewers) ? st.reviewers.join(",") : "—"}</td>
                      <td>{formatDateTime(st?.updated_at)}</td>
                      <td className="agent-board__note">{safeString(st?.note) || "—"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="agent-board__panel">
          <div className="agent-board__panel-title">Threads</div>
          <input
            className="agent-board__input"
            placeholder="search topic/agent/message…"
            value={threadQuery}
            onChange={(e) => setThreadQuery(e.target.value)}
          />
          <div className="agent-board__threads">
            {filteredThreads.map((t) => (
              <button
                key={t.threadId}
                type="button"
                className={`agent-board__thread ${t.threadId === selectedThreadId ? "agent-board__thread--active" : ""}`}
                onClick={() => setSelectedThreadId(t.threadId)}
              >
                <div className="agent-board__thread-top">
                  <div className="agent-board__thread-topic">{t.topic}</div>
                  <div className="agent-board__thread-meta">
                    <span>{t.count} notes</span>
                    <span>{formatDateTime(t.lastTs)}</span>
                  </div>
                </div>
                <div className="agent-board__thread-sub">{t.participants.join(", ")}</div>
                <div className="agent-board__thread-preview">{safeString(t.lastNote.message).slice(0, 160)}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="agent-board__panel agent-board__panel--wide">
          <div className="agent-board__panel-title">Thread</div>
          {selectedThreadId ? (
            <div className="agent-board__thread-detail">
              <div className="agent-board__mono agent-board__thread-id">thread_id: {selectedThreadId}</div>
              {selectedNotes.map((n) => (
                <div key={n.id} className="agent-board__note-card">
                  <div className="agent-board__note-head">
                    <span className="agent-board__mono">{n.id}</span>
                    <span>{formatDateTime(n.ts)}</span>
                    <span className="agent-board__mono">{n.agent}</span>
                    {n.reply_to ? <span className="agent-board__mono">reply_to={n.reply_to}</span> : null}
                  </div>
                  <div className="agent-board__note-topic">{n.topic}</div>
                  <pre className="agent-board__note-body">{n.message}</pre>
                </div>
              ))}
            </div>
          ) : (
            <div className="agent-board__muted">No thread selected.</div>
          )}
        </div>

        <div className="agent-board__panel">
          <div className="agent-board__panel-title">My Status ({actorName})</div>
          <div className="agent-board__form">
            <label>
              doing
              <input className="agent-board__input" value={statusDoing} onChange={(e) => setStatusDoing(e.target.value)} />
            </label>
            <label>
              blocked
              <input
                className="agent-board__input"
                value={statusBlocked}
                onChange={(e) => setStatusBlocked(e.target.value)}
              />
            </label>
            <label>
              next
              <input className="agent-board__input" value={statusNext} onChange={(e) => setStatusNext(e.target.value)} />
            </label>
            <label>
              note
              <input className="agent-board__input" value={statusNote} onChange={(e) => setStatusNote(e.target.value)} />
            </label>
            <label>
              tags (csv)
              <input className="agent-board__input" value={statusTags} onChange={(e) => setStatusTags(e.target.value)} />
            </label>
            <div className="agent-board__row">
              <button className="agent-board__btn" onClick={submitStatus} disabled={loading}>
                Update
              </button>
              <button className="agent-board__btn agent-board__btn--ghost" onClick={clearStatus} disabled={loading}>
                Clear
              </button>
            </div>
          </div>
        </div>

        <div className="agent-board__panel">
          <div className="agent-board__panel-title">Post Note</div>
          <div className="agent-board__form">
            <label>
              topic
              <input className="agent-board__input" value={noteTopic} onChange={(e) => setNoteTopic(e.target.value)} />
            </label>
            <label>
              reply_to (note_id)
              <input className="agent-board__input" value={noteReplyTo} onChange={(e) => setNoteReplyTo(e.target.value)} />
            </label>
            <label>
              tags (csv)
              <input className="agent-board__input" value={noteTags} onChange={(e) => setNoteTags(e.target.value)} />
            </label>
            <label>
              message
              <textarea
                className="agent-board__textarea"
                value={noteMessage}
                onChange={(e) => setNoteMessage(e.target.value)}
                rows={10}
              />
            </label>
            <div className="agent-board__row">
              <button className="agent-board__btn" onClick={submitNote} disabled={loading}>
                Post
              </button>
            </div>
            <div className="agent-board__muted">
              BEP-1推奨: topicは <span className="agent-board__mono">[Q]</span>/<span className="agent-board__mono">[REVIEW]</span> 等で
              始め、本文に scope/locks/now/ask/commands を含める。
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
