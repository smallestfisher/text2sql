import type {
  BootstrapStatus,
  ChatResponse,
  ProgressEvent,
  AdminDashboardResponse,
  AdminMetricsSummary,
  EvaluationReplayRequest,
  EvaluationReplayResult,
  EvaluationSummary,
  LoginResponse,
  MetadataOverview,
  AdminMetadataReloadResponse,
  AdminVectorPrewarmResponse,
  RoleRecord,
  RuntimeQueryLogCollectionResponse,
  RuntimeSessionCollectionResponse,
  RuntimeSqlAuditRecord,
  RuntimeStatus,
  DomainSummary,
  SessionCollectionResponse,
  SessionCreateResponse,
  SessionHistoryResponse,
  SessionStateResponse,
  SessionWorkspaceResponse,
  TraceRecord,
  UserContext,
  UserCollectionResponse,
  UserUpsertPayload,
  FeedbackSummary,
  MetadataDocumentRecord,
  MetadataDocumentListResponse,
  ConfigCollectionResponse,
  ConfigUpdateResponse,
  ExampleCollectionResponse,
  ExampleMutationResponse,
  ExampleTemplateRecord,
} from "./types";

type RequestOptions = {
  method?: string;
  token?: string | null;
  body?: unknown;
  signal?: AbortSignal;
};

type PageOptions = {
  limit?: number;
  offset?: number;
};

type AdminDashboardOptions = {
  userLimit?: number;
  userOffset?: number;
  logLimit?: number;
  logOffset?: number;
  signal?: AbortSignal;
};

function pageQuery(options: PageOptions = {}) {
  const params = new URLSearchParams();
  if (typeof options.limit === "number") {
    params.set("limit", String(options.limit));
  }
  if (typeof options.offset === "number") {
    params.set("offset", String(options.offset));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function adminDashboardQuery(options: AdminDashboardOptions = {}) {
  const params = new URLSearchParams();
  if (typeof options.userLimit === "number") {
    params.set("user_limit", String(options.userLimit));
  }
  if (typeof options.userOffset === "number") {
    params.set("user_offset", String(options.userOffset));
  }
  if (typeof options.logLimit === "number") {
    params.set("log_limit", String(options.logLimit));
  }
  if (typeof options.logOffset === "number") {
    params.set("log_offset", String(options.logOffset));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export class ApiRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

export function isAuthFailure(error: unknown) {
  return error instanceof ApiRequestError && (error.status === 401 || error.status === 403);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      detail = response.statusText;
    }
    throw new ApiRequestError(response.status, detail);
  }

  return (await response.json()) as T;
}


async function requestEventStream(
  path: string,
  options: RequestOptions,
  onEvent: (event: ProgressEvent) => void,
): Promise<void> {
  const emitEvent = (rawBlock: string) => {
    const block = rawBlock.trim();
    if (!block) {
      return;
    }
    const dataLines = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.startsWith("data: ") ? line.slice(6) : line.slice(5));
    if (!dataLines.length) {
      return;
    }
    const payload = JSON.parse(dataLines.join("\n")) as ProgressEvent;
    onEvent(payload);
  };

  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("Accept", "text/event-stream");
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      detail = response.statusText;
    }
    throw new ApiRequestError(response.status, detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      emitEvent(part);
    }
  }
  buffer += decoder.decode().replace(/\r\n/g, "\n");
  emitEvent(buffer);
}

async function requestText(path: string, options: RequestOptions = {}): Promise<string> {
  const headers = new Headers();
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      detail = response.statusText;
    }
    throw new ApiRequestError(response.status, detail);
  }

  return await response.text();
}

export const api = {
  domainSummary(): Promise<DomainSummary> {
    return request("/api/semantic/summary");
  },
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
  createSession(token: string, title?: string): Promise<SessionCreateResponse> {
    return request("/api/chat/sessions", {
      method: "POST",
      token,
      body: title === undefined ? {} : { title },
    });
  },
  listSessions(token: string): Promise<SessionCollectionResponse> {
    return request("/api/chat/sessions", { token });
  },
  deleteSession(token: string, sessionId: string): Promise<{ deleted: boolean }> {
    return request(`/api/chat/sessions/${sessionId}`, {
      method: "DELETE",
      token,
    });
  },
  getSessionHistory(token: string, sessionId: string): Promise<SessionHistoryResponse> {
    return request(`/api/chat/history/${sessionId}`, { token });
  },
  getSessionState(token: string, sessionId: string): Promise<SessionStateResponse> {
    return request(`/api/chat/state/${sessionId}`, { token });
  },
  getSessionWorkspace(token: string, sessionId: string): Promise<SessionWorkspaceResponse> {
    return request(`/api/chat/sessions/${sessionId}/workspace`, { token });
  },
  listQueryLogs(token: string, sessionId: string): Promise<RuntimeQueryLogCollectionResponse> {
    return request(`/api/chat/query-logs?session_id=${encodeURIComponent(sessionId)}&limit=5`, { token });
  },
  getTrace(token: string, traceId: string): Promise<TraceRecord> {
    return request(`/api/chat/traces/${traceId}`, { token });
  },
  getTraceSqlAudit(token: string, traceId: string): Promise<RuntimeSqlAuditRecord> {
    return request(`/api/chat/traces/${traceId}/sql-audit`, { token });
  },
  downloadTraceResult(token: string, traceId: string): Promise<string> {
    return requestText(`/api/chat/traces/${traceId}/export`, { token });
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
  chatQueryStream(
    token: string,
    question: string,
    sessionId: string | undefined,
    onEvent: (event: ProgressEvent) => void,
  ): Promise<void> {
    return requestEventStream("/api/chat/query/stream", {
      method: "POST",
      token,
      body: {
        question,
        session_id: sessionId,
      },
    }, onEvent);
  },
  adminDashboard(token: string, options: AdminDashboardOptions = {}): Promise<AdminDashboardResponse> {
    return request(`/api/admin/dashboard${adminDashboardQuery(options)}`, {
      token,
      signal: options.signal,
    });
  },
  adminRuntimeStatus(token: string): Promise<RuntimeStatus> {
    return request("/api/admin/runtime/status", { token });
  },
  adminMetricsSummary(token: string): Promise<AdminMetricsSummary> {
    return request("/api/admin/metrics/summary", { token });
  },
  adminRuntimeSessions(token: string, options: PageOptions = { limit: 20 }): Promise<RuntimeSessionCollectionResponse> {
    return request(`/api/admin/runtime/sessions${pageQuery(options)}`, { token });
  },
  adminMetadataOverview(token: string): Promise<MetadataOverview> {
    return request("/api/admin/metadata/overview", { token });
  },
  adminReloadMetadata(token: string): Promise<AdminMetadataReloadResponse> {
    return request("/api/admin/metadata/reload", {
      method: "POST",
      token,
    });
  },
  adminPrewarmVectorIndex(token: string): Promise<AdminVectorPrewarmResponse> {
    return request("/api/admin/runtime/vector/prewarm", {
      method: "POST",
      token,
    });
  },
  adminUsers(token: string, options: PageOptions = { limit: 20 }): Promise<UserCollectionResponse> {
    return request(`/api/admin/users${pageQuery(options)}`, { token });
  },
  adminUpsertUser(token: string, userId: string, payload: UserUpsertPayload): Promise<UserContext> {
    return request(`/api/admin/users/${userId}`, {
      method: "PUT",
      token,
      body: {
        username: payload.username,
        password: payload.password || null,
        roles: payload.roles,
        is_active: payload.is_active,
      },
    });
  },
  adminResetUserPassword(token: string, userId: string, newPassword: string): Promise<{ updated: boolean }> {
    return request(`/api/admin/users/${userId}/reset-password`, {
      method: "POST",
      token,
      body: { new_password: newPassword },
    });
  },
  adminDeleteUser(token: string, userId: string): Promise<{ deleted: boolean }> {
    return request(`/api/admin/users/${userId}`, {
      method: "DELETE",
      token,
    });
  },
  adminRoles(token: string): Promise<RoleRecord[]> {
    return request("/api/admin/roles", { token });
  },
  adminQueryLogs(token: string, options: PageOptions = { limit: 20 }): Promise<RuntimeQueryLogCollectionResponse> {
    return request(`/api/admin/runtime/query-logs${pageQuery(options)}`, { token });
  },
  adminFeedbackSummary(token: string): Promise<FeedbackSummary> {
    return request("/api/admin/feedbacks/summary", { token });
  },
  adminEvaluationSummary(token: string): Promise<EvaluationSummary> {
    return request("/api/admin/eval/summary", { token });
  },
  adminReplayQueryLog(
    token: string,
    traceId: string,
    payload: EvaluationReplayRequest,
  ): Promise<EvaluationReplayResult> {
    return request(`/api/admin/runtime/query-logs/${traceId}/replay`, {
      method: "POST",
      token,
      body: payload,
    });
  },
  adminListMetadataDocuments(token: string): Promise<MetadataDocumentListResponse> {
    return request("/api/admin/metadata/documents", { token });
  },
  adminGetMetadataDocument(token: string, name: string): Promise<MetadataDocumentRecord> {
    return request(`/api/admin/metadata/documents/${encodeURIComponent(name)}`, { token });
  },
  adminUpdateMetadataDocument(
    token: string,
    name: string,
    content: unknown,
  ): Promise<MetadataDocumentRecord> {
    return request(`/api/admin/metadata/documents/${encodeURIComponent(name)}`, {
      method: "PUT",
      token,
      body: { content },
    });
  },
  adminGetConfig(token: string): Promise<ConfigCollectionResponse> {
    return request("/api/admin/config", { token });
  },
  adminUpdateConfig(
    token: string,
    values: Record<string, string | null>,
  ): Promise<ConfigUpdateResponse> {
    return request("/api/admin/config", {
      method: "PUT",
      token,
      body: { values },
    });
  },
  adminListExamples(token: string): Promise<ExampleCollectionResponse> {
    return request("/api/admin/examples", { token });
  },
  adminCreateExample(token: string, example: ExampleTemplateRecord): Promise<ExampleMutationResponse> {
    return request("/api/admin/examples", {
      method: "POST",
      token,
      body: { example },
    });
  },
  adminUpdateExample(
    token: string,
    exampleId: string,
    example: ExampleTemplateRecord,
  ): Promise<ExampleMutationResponse> {
    return request(`/api/admin/examples/${encodeURIComponent(exampleId)}`, {
      method: "PUT",
      token,
      body: { example },
    });
  },
};
