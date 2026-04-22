import type {
  BootstrapStatus,
  ChatResponse,
  LoginResponse,
  MetadataOverview,
  RuntimeStatus,
  SessionCollectionResponse,
  SessionHistoryResponse,
  UserContext,
} from "./types";

const jsonHeaders = {
  "Content-Type": "application/json",
};

async function request<T>(path: string, options: { method?: string; token?: string | null; body?: any } = {}): Promise<T> {
  const headers = new Headers(jsonHeaders);
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || response.statusText);
  }

  return response.status === 204 ? (undefined as any) : response.json();
}

export const api = {
  bootstrapStatus: () => request<BootstrapStatus>("/api/auth/bootstrap-status"),
  login: (body: any) => request<LoginResponse>("/api/auth/login", { method: "POST", body }),
  me: (token: string) => request<UserContext>("/api/auth/me", { token }),
  createSession: (token: string, title?: string) => request<any>("/api/chat/sessions", { method: "POST", token, body: { title } }),
  listSessions: (token: string) => request<SessionCollectionResponse>("/api/chat/sessions", { token }),
  getSessionHistory: (token: string, sessionId: string) => request<SessionHistoryResponse>(`/api/chat/history/${sessionId}`, { token }),
  chatQuery: (token: string, question: string, sessionId?: string) => 
    request<ChatResponse>("/api/chat/query", { method: "POST", token, body: { question, session_id: sessionId } }),
  adminRuntimeStatus: (token: string) => request<RuntimeStatus>("/api/admin/runtime/status", { token }),
  adminMetadataOverview: (token: string) => request<MetadataOverview>("/api/admin/metadata/overview", { token }),
};
