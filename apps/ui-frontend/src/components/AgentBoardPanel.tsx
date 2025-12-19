import { useCallback, useEffect, useMemo, useState } from "react";
import "./AgentBoardPanel.css";

import { getAgentBoard, postAgentBoardNote, postAgentBoardStatus } from "../api/agentBoard";
import type { AgentBoard, AgentBoardNote } from "../types/agentBoard";

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

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", { hour12: false });
}

function safeString(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

function normalizeSearch(value: string): string {
  return value.trim().toLowerCase();
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
  for (const [threadId, notes] of threads.entries()) {
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
  }
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

  const [noteTopic, setNoteTopic] = useState("[FYI][coordination] ");
  const [noteMessage, setNoteMessage] = useState("");
  const [noteTags, setNoteTags] = useState("ui,coordination");
  const [noteReplyTo, setNoteReplyTo] = useState<string>("");

  const threads = useMemo(() => buildThreads(board), [board]);

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

        <div className="agent-board__panel">
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

