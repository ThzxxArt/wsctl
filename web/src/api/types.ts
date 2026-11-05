export interface User {
  id: number;
  username: string;
  role: "admin" | "user";
  disabled?: boolean;
  created_at?: number;
  totp?: boolean;
}

export interface SessionInfo {
  id: string;
  name: string;
  pid: number;
  owner_id: number | null;
  owner: string | null;
  backend: string;
  shared: boolean;
  share_writable: boolean;
  recording: boolean;
  clients: number;
  alive: boolean;
  created_at: number;
  last_active: number;
  bytes: number;
  duration: number;
  command: string | null;
  argv: string[];
  cwd: string | null;
  status: string;
  ended_reason: string;
  exit_code?: number | null;
}

export interface HistoryRow extends SessionInfo {
  ended_at?: number | null;
}

export interface FileEntry {
  name: string;
  type: "dir" | "file";
  size: number;
  mtime: number;
}

export interface ShareRecord {
  id: number;
  session: string;
  scope: "read" | "write" | "control";
  label: string;
  created_at: number;
  expires_at: number | null;
  last_used: number | null;
}

export interface ConfigField {
  key: string;
  section: string;
  section_title?: string;
  title: string;
  description: string;
  apply: "hot" | "new" | "restart";
  type: "bool" | "int" | "float" | "string" | "list" | "path";
  default: string;
  value: unknown;
  secret?: boolean;
}

export interface ConfigSchema {
  version: number;
  config_path: string;
  section_titles?: Record<string, string>;
  sections: Record<string, ConfigField[]>;
}

export interface CommandBlock {
  id: string;
  session: string;
  command: string;
  cwd: string | null;
  started_at: number | null;
  ended_at: number | null;
  duration_ms: number | null;
  exit_code: number | null;
  status: "running" | "ok" | "failed";
}

export interface ApiKeyRecord {
  id: number;
  user_id: number;
  name: string;
  scopes: string[];
  created_at: number;
  expires_at: number | null;
  last_used: number | null;
}

export interface AuditRow {
  id: number;
  user_id: number | null;
  term_session_id: string | null;
  event: string;
  payload: string | null;
  ip: string | null;
  ts: number;
  hash?: string;
  prev_hash?: string;
}

export interface Overview {
  sessions: number;
  users: number;
  clients: number;
  recordings?: number;
  memory_bytes?: number;
  uptime?: number;
  version?: string;
  metrics?: Record<string, number>;
}

export interface RecordingInfo {
  name: string;
  size: number;
  mtime: number;
}
