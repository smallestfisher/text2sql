import type {
  BootstrapStatus,
  ChatResponse,
  FeedbackCollectionResponse,
  FeedbackRecord,
  FeedbackSummary,
  LoginResponse,
  MetadataOverview,
  QueryLogListParams,
  RoleRecord,
  RuntimeQueryLogCollectionResponse,
  RuntimeRetrievalLogRecord,
  RuntimeSqlAuditRecord,
  RuntimeStatus,
  SessionCollectionResponse,
  SessionCreateResponse,
  SessionHistoryResponse,
  SessionSnapshotRecord,
  SessionStateResponse,
  TraceRecord,
  UserContext,
  UserUpsertPayload,
  EvaluationSummary,
} from "./types";

type RequestOptions = {
  method?: string;
  token?: string | null;
  body?: unknown;
};

const jsonHeaders = {
  "Content-Type": "application/json",
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(jsonHeaders);
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        detail = payload.detail.map((item) => JSON.stringify(item)).join("; ");
      }
    } catch {
      detail = response.statusText;
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  bootstrapStatus(): Promise<BootstrapStatus> {
    return request("/api/auth/bootstrap-status");
  },
  bootstrapAdmin(username: string, password: string): Promise<UserContext> {
    return request("/api/auth/bootstrap-admin", {
      method: "POST",
      body: { username, password },
    });
  },
  login(username: string, password: string): Promise<LoginResponse> {
    return request("/api/auth/login", {
      method: "POST",
      body: { username, password },
    });
  },
  me(token: string): Promise<UserContext> {
    return request("/api/auth/me", { token });
  },
  changePassword(token: string, currentPassword: string, newPassword: string): Promise<{ updated: boolean }> {
    return request("/api/auth/change-password", {
      method: "POST",
      token,
      body: {
        current_password: currentPassword,
        new_password: newPassword,
      },
    });
  },
  createSession(token: string, title?: string): Promise<SessionCreateResponse> {
    return request("/api/chat/sessions", {
      method: "POST",
      token,
      body: { title },
    });
  },
  listSessions(token: string): Promise<SessionCollectionResponse> {
    return request("/api/chat/sessions", { token });
  },
  updateSessionStatus(token: string, sessionId: string, status: "active" | "archived"): Promise<SessionCreateResponse> {
    return request(`/api/chat/sessions/${sessionId}/status`, {
      method: "PUT",
      token,
      body: { status },
    });
  },
  getSessionHistory(token: string, sessionId: string): Promise<SessionHistoryResponse> {
    return request(`/api/chat/history/${sessionId}`, { token });
  },
  getSessionState(token: string, sessionId: string): Promise<SessionStateResponse> {
    return request(`/api/chat/state/${sessionId}`, { token });
  },
  getSessionSnapshots(token: string, sessionId: string): Promise<SessionSnapshotRecord[]> {
    return request(`/api/chat/snapshots/${sessionId}`, { token });
  },
  chatQuery(token: string, question: string, sessionId?: string): Promise<ChatResponse> {
    return request("/api/chat/query", {
      method: "POST",
      token,
      body: {
        question,
        session_id: sessionId,
      },
    });
  },
  submitFeedback(
    token: string,
    payload: {
      sessionId?: string;
      traceId?: string;
      feedbackType: "correct" | "incorrect" | "clarification" | "other";
      comment?: string;
    },
  ): Promise<FeedbackRecord> {
    return request("/api/chat/feedback", {
      method: "POST",
      token,
      body: {
        session_id: payload.sessionId,
        trace_id: payload.traceId,
        feedback_type: payload.feedbackType,
        comment: payload.comment,
      },
    });
  },
  listMyFeedbacks(token: string): Promise<FeedbackCollectionResponse> {
    return request("/api/chat/feedbacks", { token });
  },
  summarizeMyFeedbacks(token: string): Promise<FeedbackSummary> {
    return request("/api/chat/feedbacks/summary", { token });
  },
  listMyQueryLogs(token: string, params: QueryLogListParams = {}): Promise<RuntimeQueryLogCollectionResponse> {
    const search = new URLSearchParams();
    if (params.sessionId) {
      search.set("session_id", params.sessionId);
    }
    if (params.limit) {
      search.set("limit", String(params.limit));
    }
    return request(`/api/chat/query-logs${search.size ? `?${search.toString()}` : ""}`, { token });
  },
  getTrace(token: string, traceId: string): Promise<TraceRecord> {
    return request(`/api/chat/traces/${traceId}`, { token });
  },
  getTraceRetrieval(token: string, traceId: string): Promise<RuntimeRetrievalLogRecord[]> {
    return request(`/api/chat/traces/${traceId}/retrieval`, { token });
  },
  getTraceSqlAudit(token: string, traceId: string): Promise<RuntimeSqlAuditRecord> {
    return request(`/api/chat/traces/${traceId}/sql-audit`, { token });
  },
  adminRuntimeStatus(token: string): Promise<RuntimeStatus> {
    return request("/api/admin/runtime/status", { token });
  },
  adminMetadataOverview(token: string): Promise<MetadataOverview> {
    return request("/api/admin/metadata/overview", { token });
  },
  adminUsers(token: string): Promise<UserContext[]> {
    return request("/api/admin/users", { token });
  },
  adminUpsertUser(token: string, userId: string, payload: UserUpsertPayload): Promise<UserContext> {
    return request(`/api/admin/users/${userId}`, {
      method: "PUT",
      token,
      body: {
        username: payload.username,
        password: payload.password || null,
        roles: payload.roles,
        can_view_sql: payload.can_view_sql,
        can_execute_sql: payload.can_execute_sql,
        is_active: payload.is_active,
      },
    });
  },
  adminRoles(token: string): Promise<RoleRecord[]> {
    return request("/api/admin/roles", { token });
  },
  adminQueryLogs(token: string): Promise<RuntimeQueryLogCollectionResponse> {
    return request("/api/admin/runtime/query-logs", { token });
  },
  adminFeedbackSummary(token: string): Promise<FeedbackSummary> {
    return request("/api/admin/feedbacks/summary", { token });
  },
  adminEvaluationSummary(token: string): Promise<EvaluationSummary> {
    return request("/api/admin/eval/summary", { token });
  },
};
