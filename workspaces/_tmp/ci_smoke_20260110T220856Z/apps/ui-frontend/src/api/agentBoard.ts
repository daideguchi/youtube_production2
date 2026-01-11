import type { AgentBoard } from "../types/agentBoard";
import { apiUrl } from "./baseUrl";

export type AgentBoardGetResponse = {
  queue_dir: string;
  board_path: string;
  board: AgentBoard;
};

export type AgentBoardStatusUpdateRequest = {
  from: string;
  doing?: string | null;
  blocked?: string | null;
  next?: string | null;
  note?: string | null;
  tags?: string | null;
  clear?: boolean;
};

export type AgentBoardNoteCreateRequest = {
  from: string;
  topic: string;
  message: string;
  reply_to?: string | null;
  tags?: string | null;
};

export type AgentBoardAreaSetRequest = {
  from: string;
  area: string;
  owner?: string | null;
  reviewers?: string | null;
  note?: string | null;
  clear?: boolean;
};

function buildUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return apiUrl(path);
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(buildUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export async function getAgentBoard(): Promise<AgentBoardGetResponse> {
  return fetchJson<AgentBoardGetResponse>("/api/agent-org/board");
}

export async function postAgentBoardStatus(body: AgentBoardStatusUpdateRequest): Promise<AgentBoardGetResponse> {
  return fetchJson<AgentBoardGetResponse>("/api/agent-org/board/status", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function postAgentBoardNote(body: AgentBoardNoteCreateRequest): Promise<{ note_id: string; thread_id: string }> {
  return fetchJson<{ note_id: string; thread_id: string }>("/api/agent-org/board/note", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function postAgentBoardArea(body: AgentBoardAreaSetRequest): Promise<AgentBoardGetResponse> {
  return fetchJson<AgentBoardGetResponse>("/api/agent-org/board/area", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
