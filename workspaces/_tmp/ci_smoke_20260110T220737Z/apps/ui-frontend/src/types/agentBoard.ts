export type AgentBoardAgentStatus = {
  doing?: string;
  blocked?: string;
  next?: string;
  note?: string;
  tags?: string[];
  updated_at?: string;
  last_note_at?: string;
};

export type AgentBoardAreaStatus = {
  owner?: string | null;
  reviewers?: string[];
  note?: string;
  updated_at?: string;
  updated_by?: string;
};

export type AgentBoardNote = {
  id: string;
  thread_id?: string;
  reply_to?: string;
  ts: string;
  agent: string;
  topic: string;
  message: string;
  tags?: string[];
};

export type AgentBoard = {
  schema_version: number;
  kind: "agent_board";
  updated_at: string;
  agents: Record<string, AgentBoardAgentStatus>;
  areas: Record<string, AgentBoardAreaStatus>;
  log: AgentBoardNote[];
};

