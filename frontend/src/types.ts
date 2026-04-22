export type ViewMode = "workspace" | "admin";

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

export interface TimeRange {
  start?: string | null;
  end?: string | null;
}

export interface TimeContext {
  grain: string;
  range?: TimeRange | null;
}

export interface VersionContext {
  field?: string | null;
  value?: string | null;
}

export interface ContextDelta {
  add_filters: FilterItem[];
  remove_filters: string[];
  replace_metrics: string[];
  replace_dimensions: string[];
  replace_time_context: TimeContext;
}

export interface QuestionClassification {
  question_type: string;
  subject_domain: string;
  inherit_context: boolean;
  confidence: number;
  reason?: string | null;
  reason_code?: string | null;
  context_delta: ContextDelta;
  need_clarification: boolean;
  clarification_question?: string | null;
}

export interface SemanticParse {
  normalized_question: string;
  matched_metrics: string[];
  matched_entities: string[];
  filters: FilterItem[];
  time_context: TimeContext;
  version_context?: VersionContext | null;
  subject_domain: string;
  has_follow_up_cue: boolean;
  has_explicit_slots: boolean;
}

export interface SortItem {
  field: string;
  order: string;
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
  version_context?: VersionContext | null;
  inherit_context: boolean;
  need_clarification: boolean;
  clarification_question?: string | null;
  reason_code?: string | null;
  sort: SortItem[];
  limit: number;
  reason?: string | null;
}

export interface RetrievalHit {
  source_type: string;
  source_id: string;
  score: number;
  summary: string;
  matched_features: string[];
  metadata: Record<string, unknown>;
}

export interface RetrievalContext {
  domains: string[];
  semantic_views: string[];
  metrics: string[];
  retrieval_terms: string[];
  retrieval_channels: string[];
  hits: RetrievalHit[];
  hit_count_by_source: Record<string, number>;
}

export interface ValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface TraceStep {
  name: string;
  status: string;
  detail?: string | null;
  metadata: Record<string, unknown>;
}

export interface TraceRecord {
  trace_id: string;
  created_at: string;
  steps: TraceStep[];
  warnings: string[];
}

export interface AnswerPayload {
  status: string;
  summary: string;
  detail?: string | null;
  follow_up_hint?: string | null;
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
  error_category?: string | null;
  truncated: boolean;
}

export interface SessionState {
  session_id: string;
  topic?: string | null;
  subject_domain: string;
  entities: string[];
  tables: string[];
  semantic_views: string[];
  metrics: string[];
  dimensions: string[];
  filters: FilterItem[];
  time_context?: TimeContext | null;
  version_context?: VersionContext | null;
  last_question_type?: string | null;
  last_query_plan?: QueryPlan | null;
  last_sql?: string | null;
  last_result_shape?: string | null;
}

export interface ChatResponse {
  classification: QuestionClassification;
  semantic_parse: SemanticParse;
  retrieval?: RetrievalContext | null;
  trace?: TraceRecord | null;
  answer?: AnswerPayload | null;
  query_plan: QueryPlan;
  sql?: string | null;
  plan_validation: ValidationResponse;
  sql_validation: ValidationResponse;
  execution?: ExecutionResponse | null;
  next_session_state: SessionState;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  trace_id?: string | null;
}

export interface ChatSession {
  id: string;
  user_id?: string | null;
  title?: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  last_state?: SessionState | null;
}

export interface SessionCreateResponse {
  session: ChatSession;
}

export interface SessionCollectionResponse {
  sessions: ChatSession[];
  count: number;
}

export interface SessionHistoryResponse {
  session: ChatSession;
  messages: ChatMessage[];
}

export interface SessionStateResponse {
  session: ChatSession;
  state?: SessionState | null;
}

export interface SessionSnapshotRecord {
  snapshot_id: string;
  session_id: string;
  trace_id?: string | null;
  state: SessionState;
  created_at: string;
}

export interface RuntimeQueryLogRecord {
  trace_id: string;
  session_id?: string | null;
  user_id?: string | null;
  question?: string | null;
  question_type?: string | null;
  subject_domain?: string | null;
  answer_status?: string | null;
  plan_valid?: boolean | null;
  sql_valid?: boolean | null;
  executed?: boolean | null;
  row_count?: number | null;
  warnings: string[];
  created_at: string;
}

export interface RuntimeQueryLogCollectionResponse {
  query_logs: RuntimeQueryLogRecord[];
  count: number;
}

export interface RuntimeRetrievalLogRecord {
  retrieval_log_id: string;
  trace_id: string;
  rank_position: number;
  source_type: string;
  source_id: string;
  score: number;
  matched_features: string[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface RuntimeSqlAuditRecord {
  sql_audit_id: string;
  trace_id: string;
  sql_text?: string | null;
  plan_valid: boolean;
  sql_valid: boolean;
  executed: boolean;
  row_count?: number | null;
  warnings: string[];
  errors: string[];
  created_at: string;
}

export interface FeedbackRecord {
  id: string;
  session_id?: string | null;
  trace_id?: string | null;
  user_id?: string | null;
  feedback_type: "correct" | "incorrect" | "clarification" | "other";
  comment?: string | null;
  created_at: string;
}

export interface FeedbackCollectionResponse {
  feedbacks: FeedbackRecord[];
  count: number;
}

export interface FeedbackTypeSummary {
  feedback_type: string;
  count: number;
}

export interface FeedbackSummary {
  total: number;
  by_type: FeedbackTypeSummary[];
}

export interface MetadataOverview {
  semantic_version?: string | null;
  semantic_domains: string[];
  semantic_views: string[];
  example_count: number;
  trace_count: number;
}

export interface RoleRecord {
  role_name: string;
  description?: string | null;
  created_at: string;
}

export interface RuntimeSessionCollectionResponse {
  sessions: ChatSession[];
  count: number;
}

export interface EvaluationDimensionSummary {
  key: string;
  total: number;
  passed: number;
  failed: number;
}

export interface EvaluationSummary {
  run_count: number;
  case_count: number;
  passed_count: number;
  failed_count: number;
  by_domain: EvaluationDimensionSummary[];
  by_question_type: EvaluationDimensionSummary[];
  by_answer_status: EvaluationDimensionSummary[];
}

export interface RuntimeStatus {
  business_database: Record<string, unknown>;
  runtime_database: Record<string, unknown>;
  llm: Record<string, unknown>;
  vector_retrieval: Record<string, unknown>;
  retrieval_corpus: Record<string, unknown>;
  classification: Record<string, unknown>;
  sql_ast: Record<string, unknown>;
}

export interface UserUpsertPayload {
  username: string;
  password?: string;
  roles: string[];
  can_view_sql: boolean;
  can_execute_sql: boolean;
  is_active: boolean;
}

export interface QueryLogListParams {
  sessionId?: string;
  limit?: number;
}
