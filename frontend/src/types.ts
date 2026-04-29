export interface UserContext {
  user_id: string;
  username?: string | null;
  roles: string[];
  is_active: boolean;
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

export interface DomainSummary {
  version?: string;
  domains: string[];
  entities: string[];
  metrics: string[];
  tables: string[];
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

export interface SortItem {
  field: string;
  order: string;
}

export interface QueryPlan {
  question_type: string;
  subject_domain: string;
  tables: string[];
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
  analysis_mode?: string | null;
  sort: SortItem[];
  limit: number;
  reason?: string | null;
}

export interface QuestionClassification {
  question_type: string;
  subject_domain: string;
  inherit_context: boolean;
  confidence: number;
  reason?: string | null;
  reason_code?: string | null;
  suggested_reply?: string | null;
  need_clarification: boolean;
  clarification_question?: string | null;
  context_delta?: Record<string, unknown>;
}

export interface QueryIntent {
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
  metrics: string[];
  retrieval_terms: string[];
  retrieval_channels: string[];
  hits: RetrievalHit[];
  hit_count_by_source: Record<string, number>;
  hit_count_by_channel: Record<string, number>;
}

export interface ValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  risk_level?: string;
  risk_flags?: string[];
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

export interface ProgressEvent {
  trace_id: string;
  type: "accepted" | "stage" | "completed" | "failed";
  stage: string;
  status: string;
  detail?: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
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
  metrics: string[];
  dimensions: string[];
  filters: FilterItem[];
  sort: SortItem[];
  limit?: number | null;
  time_context?: TimeContext | null;
  version_context?: VersionContext | null;
  analysis_mode?: string | null;
  last_question_type?: string | null;
  last_query_plan?: QueryPlan | null;
  last_sql?: string | null;
  last_result_shape?: string | null;
}

export interface ChatResponse {
  classification: QuestionClassification;
  query_intent: QueryIntent;
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

export interface SessionWorkspaceResponse {
  session: ChatSession;
  messages: ChatMessage[];
  state?: SessionState | null;
  latest_response?: ChatResponse | null;
  latest_trace?: TraceRecord | null;
  latest_sql_audit?: RuntimeSqlAuditRecord | null;
  latest_query_logs: RuntimeQueryLogRecord[];
  trace_artifacts: SessionTraceWorkspaceRecord[];
}

export interface SessionTraceWorkspaceRecord {
  trace_id: string;
  response?: ChatResponse | null;
  trace?: TraceRecord | null;
  sql_audit?: RuntimeSqlAuditRecord | null;
  query_log?: RuntimeQueryLogRecord | null;
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
  plan_risk_level?: string | null;
  plan_risk_flags?: string[];
  sql_valid?: boolean | null;
  sql_risk_level?: string | null;
  sql_risk_flags?: string[];
  executed?: boolean | null;
  row_count?: number | null;
  warnings: string[];
  prompt_context_summary?: Record<string, unknown>;
  created_at: string;
}

export interface RuntimeQueryLogCollectionResponse {
  query_logs: RuntimeQueryLogRecord[];
  count: number;
}

export interface RuntimeSqlAuditRecord {
  sql_audit_id: string;
  trace_id: string;
  sql_text?: string | null;
  plan_valid: boolean;
  plan_risk_level?: string | null;
  plan_risk_flags?: string[];
  sql_valid: boolean;
  sql_risk_level?: string | null;
  sql_risk_flags?: string[];
  executed: boolean;
  row_count?: number | null;
  warnings: string[];
  errors: string[];
  created_at: string;
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
  table_count: number;
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

export interface EvaluationReplayRequest {
  user_id?: string | null;
  reuse_original_user: boolean;
  include_prior_context: boolean;
}

export interface EvaluationReplayDiff {
  classification_changed: boolean;
  question_type_changed: boolean;
  subject_domain_changed: boolean;
  answer_status_changed: boolean;
  plan_valid_changed: boolean;
  sql_valid_changed: boolean;
  execution_status_changed: boolean;
  sql_changed: boolean;
  prompt_context_changed?: boolean;
  original_prompt_context_summary?: Record<string, unknown>;
  replay_prompt_context_summary?: Record<string, unknown>;
}

export interface EvaluationReplayResult {
  source_type: "evaluation_case" | "runtime_query_log";
  source_id: string;
  question: string;
  session_questions: string[];
  replay_user?: UserContext | null;
  original_trace_id?: string | null;
  original_session_id?: string | null;
  original_user_id?: string | null;
  diff?: EvaluationReplayDiff | null;
  response: ChatResponse;
}

export interface RuntimeStatus {
  business_database: Record<string, unknown>;
  runtime_database: Record<string, unknown>;
  llm: Record<string, unknown>;
  vector_retrieval: Record<string, unknown>;
  retrieval_corpus: Record<string, unknown>;
  sql_ast: Record<string, unknown>;
}

export interface UserUpsertPayload {
  username: string;
  password?: string;
  roles: string[];
  is_active: boolean;
}
