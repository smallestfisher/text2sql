export type ViewMode = "workspace" | "admin" | "semantic";

export interface UserContext {
  user_id: string;
  username?: string | null;
  roles: string[];
  can_view_sql: boolean;
  can_execute_sql: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserContext;
}

export interface BootstrapStatus {
  has_users: boolean;
}

export interface FilterItem {
  field: string;
  op: string;
  value: unknown;
}

export interface SortItem {
  field: string;
  order: string;
}

export interface TimeRange {
  start?: string | null;
  end?: string | null;
}

export interface TimeContext {
  grain: string;
  range?: TimeRange | null;
}

export interface QueryPlan {
  question_type: string;
  subject_domain: string;
  tables: string[];
  semantic_views: string[];
  entities: string[];
  metrics: string[];
  dimensions: string[];
  filters: FilterItem[];
  join_path: string[];
  time_context: TimeContext;
  sort: SortItem[];
  limit: number;
  reason?: string | null;
}

export interface ExecutionResponse {
  executed: boolean;
  status: string;
  sql?: string | null;
  row_count: number;
  columns: string[];
  rows: Record<string, unknown>[];
  errors: string[];
  warnings: string[];
  elapsed_ms?: number | null;
  truncated: boolean;
}

export interface AnswerPayload {
  status: string;
  summary: string;
  detail?: string | null;
  follow_up_hint?: string | null;
}

export interface ChatResponse {
  answer?: AnswerPayload | null;
  query_plan: QueryPlan;
  sql?: string | null;
  execution?: ExecutionResponse | null;
  next_session_state: any;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  trace_id?: string | null;
  answer_payload?: AnswerPayload | null;
  query_plan?: QueryPlan | null;
  sql?: string | null;
  execution?: ExecutionResponse | null;
}

export interface ChatSession {
  id: string;
  title?: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface SessionCollectionResponse {
  sessions: ChatSession[];
  count: number;
}

export interface SessionHistoryResponse {
  session: ChatSession;
  messages: ChatMessage[];
}

export interface RuntimeStatus {
  business_database: Record<string, any>;
  llm: Record<string, any>;
}

export interface MetadataOverview {
  semantic_domains: string[];
  semantic_views: string[];
  example_count: number;
}
