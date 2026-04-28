import type {
  BootstrapStatus,
  ChatResponse,
  ProgressEvent,
  EvaluationReplayRequest,
  EvaluationReplayResult,
  EvaluationSummary,
  LoginResponse,
  MetadataOverview,
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
  UserUpsertPayload,
  FeedbackSummary,
} from "./types";

type RequestOptions = {
  method?: string;
  token?: string | null;
  body?: unknown;
};

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
    throw new Error(detail);
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
    throw new Error(detail);
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
    throw new Error(detail);
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
      body: { title },
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
  adminRuntimeStatus(token: string): Promise<RuntimeStatus> {
    return request("/api/admin/runtime/status", { token });
  },
  adminRuntimeSessions(token: string): Promise<RuntimeSessionCollectionResponse> {
    return request("/api/admin/runtime/sessions?limit=20", { token });
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
  adminQueryLogs(token: string): Promise<RuntimeQueryLogCollectionResponse> {
    return request("/api/admin/runtime/query-logs?limit=20", { token });
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
};
